from __future__ import annotations

import os
import sys
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from PyQt6.QtCore import Qt, QSize, QPoint, QTimer, QUrl
from PyQt6.QtGui import QIcon, QFontMetrics, QCursor, QColor, QAction, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStatusBar,
    QLabel,
    QProgressBar,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QStyle,
    QMenu,
    QSizePolicy,
)

# App modules
from views import MainView
from storage import load_settings, save_settings, get_appdata_dir
from documentation import DocumentationDialog

# (Onboarding is triggered from main.py; we don't open it here.)

# ---------------- logging ----------------
logger = logging.getLogger("TargetTracker.Main")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)


# ---------------- PyInstaller-safe asset helpers ----------------
def asset_path(rel: str) -> str:
    """
    Resolve a path inside the bundled app. Works both in source and in
    PyInstaller one-file/one-dir builds.
    """
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base, rel)


def _first_existing(candidates) -> Optional[str]:
    for p in candidates:
        # If a candidate is relative, look inside the bundle first
        full = asset_path(p) if not os.path.isabs(p) else p
        if os.path.exists(full):
            return full
        # Also try plain relative (developer runs from repo)
        if not os.path.isabs(p) and os.path.exists(p):
            return p
    return None


def _icon_path(name: str) -> Optional[str]:
    # Preferred locations / names
    return _first_existing([
        os.path.join("assets", f"ic-{name}.svg"),
        os.path.join("assets", f"ic-{name}.png"),
        f"ic-{name}.svg",
        f"ic-{name}.png",
    ])


def _fallback_qt_icon(name: str) -> QIcon:
    """Map our semantic names to Qt standard icons (last-resort)."""
    style = QApplication.instance().style() if QApplication.instance() else None
    if not style:
        return QIcon()
    mapping = {
        "check": QStyle.StandardPixmap.SP_DialogApplyButton,
        "warning": QStyle.StandardPixmap.SP_MessageBoxWarning,
        "error": QStyle.StandardPixmap.SP_MessageBoxCritical,
        "refresh": QStyle.StandardPixmap.SP_BrowserReload,
        "info": QStyle.StandardPixmap.SP_MessageBoxInformation,
    }
    sp = mapping.get(name)
    return style.standardIcon(sp) if sp is not None else QIcon()


def app_icon() -> QIcon:
    """
    Prefer the app's ICO (for Windows taskbar/window), then fall back to ic-app.*
    and finally a standard info icon.
    """
    p = _first_existing([
        os.path.join("assets", "logo.ico"),
        os.path.join("assets", "ic-app.png"),
        os.path.join("assets", "ic-app.svg"),
    ])
    if p:
        return QIcon(p)
    return _fallback_qt_icon("info")


def themed_icon(name: str) -> QIcon:
    """
    Themed icons used across the UI. Tries packaged assets first; if missing,
    falls back to a Qt standard icon so the build still shows something.
    """
    p = _icon_path(name)
    if p:
        return QIcon(p)
    return _fallback_qt_icon(name)


# ---------------- dark style ----------------
def apply_darkstyle(app: QApplication) -> str:
    """
    Return the QDarkStyle stylesheet (plus app-specific tweaks) without
    installing it globally on the QApplication. Callers can apply the
    returned QSS selectively (e.g., to the main window) so other dialogs can
    opt out or override as needed.
    """
    qss = ""
    try:
        import qdarkstyle  # pip install qdarkstyle
        try:
            qss = qdarkstyle.load_stylesheet(qt_api="pyqt6")
        except Exception:
            qss = qdarkstyle.load_stylesheet_pyqt6()
    except Exception as e:
        logger.warning("QDarkStyle not applied: %s", e)

    extra_css = """
    QToolTip {
        color: #e6eef8;
        background-color: #1f2835;
        border: 1px solid #3b4758;
        padding: 8px 10px;
        border-radius: 8px;
    }
    """
    qss = (qss or "") + "\n" + extra_css
    app.setProperty("_tt_dark_qss", qss)
    return qss


# ---------------- first-run: ensure targets.json in AppData ----------------
def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def ensure_first_run_targets() -> str:
    """
    Ensure AppData exists and a targets JSON exists there.
    Returns the absolute path to the targets file.
    """
    appdir = get_appdata_dir()
    try:
        os.makedirs(appdir, exist_ok=True)
    except Exception as e:
        logger.warning("Failed to ensure AppData folder: %s", e)

    st = load_settings()
    if not isinstance(st, dict):
        st = {}
    target_path = st.get("targets_file")
    if not target_path:
        target_path = os.path.join(appdir, "target.json")

    if not os.path.isabs(target_path):
        target_path = os.path.join(appdir, os.path.basename(target_path))

    if not os.path.exists(target_path):
        payload = {
            "app": "Target Tracker",
            "version": "1.0.0",
            "exportedAt": _iso_now(),
            "targets": [],
        }
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.info("Created new targets file at %r", target_path)
        except Exception as e:
            logger.error("Failed to create targets file %r: %s", target_path, e)

    if st.get("targets_file") != target_path:
        st["targets_file"] = target_path
        save_settings(st)
        logger.info("Settings updated with targets_file=%r", target_path)

    return target_path


# ---------------- Fancy Status Bar ----------------
class PrettyProgress(QProgressBar):
    """Rounded, theme-aware progress bar with error/success accent."""
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(18)
        self.setFixedWidth(360)
        self._qss_ok = """
            QProgressBar {
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                background: rgba(255,255,255,0.06);
                padding: 1px;
                text-align: center;
            }
            QProgressBar::chunk {
                border-radius: 7px;
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #5aa3ff, stop:1 #7fb3ff);
            }
        """
        self._qss_err = """
            QProgressBar {
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                background: rgba(255,255,255,0.06);
                padding: 1px;
                text-align: center;
            }
            QProgressBar::chunk {
                border-radius: 7px;
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff6a6a, stop:1 #ff8a7f);
            }
        """
        self._qss_done = """
            QProgressBar {
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                background: rgba(255,255,255,0.06);
                padding: 1px;
                text-align: center;
            }
            QProgressBar::chunk {
                border-radius: 7px;
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #57d38c, stop:1 #72e3a0);
            }
        """
        self.set_ok()

    def set_ok(self):
        self.setStyleSheet(self._qss_ok)

    def set_error(self):
        self.setStyleSheet(self._qss_err)

    def set_done(self):
        self.setStyleSheet(self._qss_done)


class InfoPopover(QFrame):
    """Lightweight, non-flickery popover with rich HTML."""
    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setObjectName("infoPopover")
        self.setStyleSheet("""
            #infoPopover {
                background-color: #1f2835;
                border: 1px solid #3b4758;
                border-radius: 8px;
            }
            #infoPopover QLabel {
                color: #e6eef8;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        label = QLabel("")
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setOpenExternalLinks(True)
        label.setWordWrap(True)
        layout.addWidget(label)
        self._label = label

        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(24)
        effect.setOffset(0, 8)
        effect.setColor(QColor(0, 0, 0, 180))
        self.setGraphicsEffect(effect)

    def set_html(self, html: str):
        self._label.setText(html or "")
        self.adjustSize()

    def show_at(self, global_pos: QPoint):
        self.move(global_pos)
        self.show()

    def under_cursor(self) -> bool:
        pos = QCursor.pos()
        return self.rect().contains(self.mapFromGlobal(pos))


class HoverIcon(QLabel):
    """QLabel that shows crisp icons and a custom popover."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(QSize(22, 22))
        self.setContentsMargins(0, 0, 0, 0)
        self.setScaledContents(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._enabled = True
        self._html_tip = ""
        self._popover = InfoPopover()

    def set_icon(self, ic: QIcon, size: int = 20):
        self.setPixmap(ic.pixmap(size, size) if ic and not ic.isNull() else QIcon().pixmap(size, size))

    def set_rich_tooltip(self, html: str, enabled: bool = True):
        self._html_tip = html or ""
        self._enabled = bool(enabled)

    def enterEvent(self, ev):
        super().enterEvent(ev)
        if self._enabled and self._html_tip:
            self._popover.set_html(self._html_tip)
            rect = self.rect()
            top_left = self.mapToGlobal(rect.topLeft())
            bottom_left = self.mapToGlobal(rect.bottomLeft())
            screen = QApplication.screenAt(top_left) or QApplication.primaryScreen()
            avail = screen.availableGeometry()
            gap = 8
            x = top_left.x()
            y = top_left.y() - self._popover.height() - gap
            if y < avail.top() + 6:
                y = bottom_left.y() + gap
            x = max(avail.left() + 6, min(x, avail.right() - 6 - self._popover.width()))
            self._popover.show_at(QPoint(x, y))

    def leaveEvent(self, ev):
        super().leaveEvent(ev)
        if not self._popover.under_cursor():
            self._popover.hide()


class Pill(QLabel):
    """Small rounded label for meta stats."""
    def __init__(self, text: str = "—", variant: str = "info"):
        super().__init__(text)
        self.setObjectName("pill")
        self.setStyleSheet("""
            QLabel#pill {
                padding: 4px 8px;
                border-radius: 10px;
                border: 1px solid rgba(255,255,255,0.12);
                background: rgba(255,255,255,0.06);
                color: #cfd7e3;
                font-weight: 500;
            }
            QLabel#pill[variant="ok"] {
                border-color: rgba(64,191,128,0.55);
                background: rgba(64,191,128,0.22);
                color: #dff6e8;
            }
            QLabel#pill[variant="warn"] {
                border-color: rgba(255,196,77,0.55);
                background: rgba(255,196,77,0.20);
                color: #ffe9c4;
            }
            QLabel#pill[variant="error"] {
                border-color: rgba(255,120,120,0.55);
                background: rgba(255,120,120,0.22);
                color: #ffd6d6;
            }
            QLabel#pill[variant="muted"] {
                border-color: rgba(255,255,255,0.08);
                background: rgba(255,255,255,0.04);
                color: #9aa6ba;
            }
        """)
        self.set_variant(variant)

    def set_variant(self, variant: str):
        allowed = {"ok", "warn", "error", "muted"}
        self.setProperty("variant", variant if variant in allowed else "")
        self.style().unpolish(self)
        self.style().polish(self)


class FancyStatusBar(QStatusBar):
    """Displays a polished, dark-friendly status area with a rich error popover."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeGripEnabled(False)
        self.setMinimumHeight(28)
        self.setStyleSheet("QStatusBar { border-top: 1px solid rgba(255,255,255,0.08); }")

        self._left = QWidget(self)
        hl = QHBoxLayout(self._left)
        hl.setContentsMargins(6, 2, 6, 2)
        hl.setSpacing(6)

        self._icon = HoverIcon()
        self._msg = QLabel("Ready")
        self._msg.setMinimumWidth(280)
        self._msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hl.addWidget(self._icon)
        hl.addWidget(self._msg, 1)

        self._prog = PrettyProgress()
        self._prog.setRange(0, 1)
        self._prog.setValue(0)
        self._prog.setTextVisible(True)
        self._prog.setFormat("0/1")
        self._prog.setToolTip("Progress")

        self._meta_container = QWidget(self)
        self._meta_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._meta_container.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._meta_container.setAutoFillBackground(False)
        self._meta_container.setStyleSheet("background: transparent;")
        self._meta_container.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred))
        ml = QHBoxLayout(self._meta_container)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(6)
        self._meta_layout = ml
        self._meta_pills: list[Pill] = []
        self._meta_placeholder = Pill("No stats", "muted")
        ml.addWidget(self._meta_placeholder)

        self._clock = Pill("Updated —", "muted")

        self.addWidget(self._left, 1)
        self.addPermanentWidget(self._prog, 0)
        self.addPermanentWidget(self._meta_container, 0)
        self.addPermanentWidget(self._clock, 0)

        self._current_errors = 0
        self._last_timestamp: Optional[datetime] = None
        self._last_result_ok = True
        self._set_status_icon("info")
        self._set_info_tooltip()

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(60000)
        self._clock_timer.timeout.connect(self._refresh_timestamp_label)
        self._clock_timer.start()
        self._is_fetching = False
        self._refresh_timestamp_label()

    def set_message(self, text: str):
        text = text or ""
        fm = QFontMetrics(self._msg.font())
        w = max(480, self._msg.width())
        self._msg.setText(fm.elidedText(text, Qt.TextElideMode.ElideRight, w))
        self._msg.setToolTip(text)
        self.showMessage(text, 5000)
        self._apply_icon_and_color(text)
        self._record_activity_timestamp(text)

    def set_progress(self, cur: int, total: int):
        total = max(1, int(total))
        cur = max(0, min(int(cur), total))
        self._prog.setRange(0, total)
        self._prog.setValue(cur)
        self._prog.setFormat(f"{cur}/{total}")
        self._prog.setToolTip(f"{cur} of {total} fetched")
        if cur == total:
            self._prog.set_done() if self._current_errors == 0 else self._prog.set_error()

    def set_meta(self, text: str):
        raw = (text or "").strip()
        parts = [p.strip() for p in re.split(r"[•\u2022|]+", raw) if p.strip()]

        if not parts:
            self._meta_placeholder.setText("No stats")
            self._meta_placeholder.show()
            for pill in self._meta_pills:
                pill.hide()
            self._meta_container.setToolTip("")
            return

        self._meta_placeholder.hide()
        while len(self._meta_pills) < len(parts):
            pill = Pill("", "info")
            pill.hide()
            self._meta_pills.append(pill)
            self._meta_layout.addWidget(pill)

        for pill, part in zip(self._meta_pills, parts):
            pill.setText(part)
            pill.set_variant(self._classify_meta(part))
            pill.setToolTip(part)
            pill.show()

        for pill in self._meta_pills[len(parts):]:
            pill.hide()

        self._meta_container.setToolTip(raw)

    def _classify_meta(self, text: str) -> str:
        low = text.lower()
        if "error" in low or "fail" in low:
            return "error"
        if "ignore" in low or "warn" in low:
            return "warn"
        if any(word in low for word in ("done", "total", "visible", "cached")):
            return "ok"
        return "info"

    def _set_fetching(self, active: bool):
        active = bool(active)
        if self._is_fetching == active:
            if active:
                self._clock.setText("Fetching…")
                self._clock.set_variant("ok")
                self._clock.setToolTip("Currently fetching targets…")
            return
        self._is_fetching = active
        if active:
            self._clock.setText("Fetching…")
            self._clock.set_variant("ok")
            self._clock.setToolTip("Currently fetching targets…")
        else:
            self._refresh_timestamp_label()

    def _set_status_icon(self, kind: str):
        # kind: info | progress | ok | warn | error
        name = {
            "ok": "check",
            "warn": "warning",
            "error": "error",
            "progress": "refresh",
            "info": "info",
        }.get(kind, "info")
        ic = themed_icon(name)
        self._icon.set_icon(ic, size=20)

    def _set_info_tooltip(self):
        html = """
        <div style="min-width:240px">
          <div style="font-weight:600;color:#cfe3ff;margin-bottom:4px">Status</div>
          <div>No errors detected.</div>
        </div>
        """
        self._icon.set_rich_tooltip(html, enabled=True)

    def _set_error_tooltip(self, count: int):
        html = f"""
        <div style="min-width:280px">
          <div style="font-weight:700;color:#ffd1d1;margin-bottom:6px">Fetch Errors</div>
          <div style="color:#e6eef8">
             Some requests failed during the last update.
          </div>
          <ul style="margin:6px 0 0 16px;">
            <li><b>{count}</b> error(s) in this run</li>
            <li>Check the <i>Error</i> column for per-row details</li>
          </ul>
        </div>
        """
        self._icon.set_rich_tooltip(html, enabled=True)

    def _apply_icon_and_color(self, text: str):
        self._current_errors = 0
        m = re.search(r"errors:\s*(\d+)", text, flags=re.IGNORECASE) or \
            re.search(r"\b(\d+)\s+errors\b", text, flags=re.IGNORECASE)
        if m:
            try:
                self._current_errors = int(m.group(1))
            except Exception:
                self._current_errors = 0

        low = text.lower()
        self._set_fetching(False)
        if "done" in low:
            self._set_status_icon("ok" if self._current_errors == 0 else "error")
            (self._prog.set_done() if self._current_errors == 0 else self._prog.set_error())
            self._set_fetching(False)
        elif "updating" in low or "fetch" in low or "loading" in low:
            self._set_status_icon("progress" if self._current_errors == 0 else "error")
            (self._prog.set_ok() if self._current_errors == 0 else self._prog.set_error())
            self._set_fetching(True)
        elif self._current_errors > 0:
            self._set_status_icon("error")
            self._prog.set_error()
            self._set_fetching(False)
        else:
            self._set_status_icon("info")
            self._prog.set_ok()
            self._set_fetching(False)

        if self._current_errors > 0:
            self._set_error_tooltip(self._current_errors)
        else:
            self._set_info_tooltip()

    def _record_activity_timestamp(self, text: str):
        low = text.lower()
        triggers = ("done", "export", "refresh", "updated", "loaded", "saved", "copied", "import")
        if any(word in low for word in triggers):
            self._last_timestamp = datetime.now(timezone.utc)
            has_real_error = "error" in low and not re.search(r"errors?\s*:\s*0\b", low)
            self._last_result_ok = self._current_errors == 0 and not has_real_error
            self._refresh_timestamp_label()
            self._set_fetching(False)

    def _refresh_timestamp_label(self):
        if self._is_fetching:
            self._clock.setText("Fetching…")
            self._clock.set_variant("ok")
            self._clock.setToolTip("Currently fetching targets…")
            return
        if not self._clock:
            return
        if not self._last_timestamp:
            self._clock.setText("Updated – never")
            self._clock.set_variant("muted")
            self._clock.setToolTip("No activity yet.")
            return

        now = datetime.now(timezone.utc)
        delta = now - self._last_timestamp
        seconds = max(0, int(delta.total_seconds()))

        if seconds < 5:
            label = "Updated – just now"
        elif seconds < 60:
            label = f"Updated – {seconds}s ago"
        elif seconds < 3600:
            minutes = seconds // 60
            label = f"Updated – {minutes} min ago"
        else:
            hours = seconds // 3600
            label = f"Updated – {hours} hr ago"

        self._clock.setText(label)

        if self._last_result_ok:
            if seconds <= 180:
                variant = "ok"
            elif seconds <= 900:
                variant = ""
            else:
                variant = "warn"
        else:
            variant = "error" if seconds <= 600 else "warn"

        self._clock.set_variant(variant)
        local_dt = self._last_timestamp.astimezone()
        self._clock.setToolTip(local_dt.strftime("%Y-%m-%d %H:%M:%S %Z"))


class MainWindow(QMainWindow):
    """
    The window does not import or construct the Controller directly — call
    win.attach_controller(controller) from main.py to avoid circular imports.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Target Tracker")
        self.setWindowIcon(app_icon())
        self.resize(1200, 720)

        self.view = MainView()
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.setCentralWidget(central)

        self._status = FancyStatusBar(self)
        self.setStatusBar(self._status)

        self._menu_actions: dict[str, QAction] = {}
        self._build_menu()

        self._controller = None  # set via attach_controller()

    # -------- menu + helpers --------

    def _build_menu(self):
        bar = self.menuBar()
        try:
            bar.setNativeMenuBar(False)
            bar.setStyleSheet("")
        except Exception:
            pass

        file_menu = bar.addMenu("&File")
        file_menu.addAction(self._create_action("Refresh Targets", "refresh", "Ctrl+R", self._trigger_refresh))
        file_menu.addSeparator()
        file_menu.addAction(self._create_action("Export CSV...", "csv", "Ctrl+E", self._trigger_export_csv))
        file_menu.addAction(self._create_action("Export JSON...", "json", "Ctrl+Shift+E", self._trigger_export_json))
        file_menu.addSeparator()
        file_menu.addAction(self._create_action("Load Targets JSON...", "folder-open", "Ctrl+O", self._trigger_load_targets))
        file_menu.addAction(self._create_action("Settings...", "settings", "Ctrl+,", self._trigger_open_settings))
        file_menu.addSeparator()
        file_menu.addAction(self._create_action("Exit", "cancel", "Ctrl+Q", self.close))

        targets_menu = bar.addMenu("&Targets")
        targets_menu.addAction(self._create_action("Add Targets...", "add", "Ctrl+N", self._trigger_add_targets))
        targets_menu.addAction(self._create_action("Remove Selected", "delete", "Del", self._trigger_remove_selected))
        targets_menu.addSeparator()
        targets_menu.addAction(self._create_action("Ignore Selected", "block", None, self._trigger_ignore_selected))
        targets_menu.addAction(self._create_action("Unignore Selected", "unblock", None, self._trigger_unignore_selected))
        targets_menu.addAction(self._create_action("Manage Ignored...", "block", None, self._trigger_manage_ignore))
        targets_menu.addSeparator()
        targets_menu.addAction(self._create_action("Copy Selected IDs", "id", "Ctrl+Shift+C", self._trigger_copy_ids))

        view_menu = bar.addMenu("&View")
        self._act_view_toolbar = self._create_action("Show Toolbar", "apply", checkable=True)
        self._act_view_toolbar.setChecked(self.view.toolbar_visible())
        self._act_view_toolbar.toggled.connect(self._toggle_toolbar)
        view_menu.addAction(self._act_view_toolbar)

        self._act_view_filters = self._create_action("Show Filters", "data", checkable=True)
        self._act_view_filters.setChecked(self.view.filters_visible())
        self._act_view_filters.toggled.connect(self._toggle_filters)
        view_menu.addAction(self._act_view_filters)

        view_menu.addSeparator()
        view_menu.addAction(self._create_action("Focus Search", "search", "Ctrl+F", self._trigger_focus_search))
        view_menu.addAction(self._create_action("Reset Sorting", "refresh", None, self._trigger_reset_sort))

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self._create_action("Open Documentation", "help", "F1", self._open_docs))
        help_menu.addAction(self._create_action("Open App Data Folder", "folder-open", None, self._open_appdata_dir))
        help_menu.addSeparator()
        help_menu.addAction(self._create_action("About Target Tracker", "info", None, self._trigger_about))

    def _create_action(self, text: str, icon_name: str, shortcut: Optional[str] = None, slot=None, checkable: bool = False) -> QAction:
        act = QAction(themed_icon(icon_name), text, self)
        if shortcut:
            act.setShortcut(shortcut)
        act.setCheckable(checkable)
        if slot:
            act.triggered.connect(slot)
        self._menu_actions[text] = act
        return act

    def _notify(self, message: str):
        if self._status:
            self._status.set_message(message)

    # -------- action handlers --------

    def _trigger_refresh(self):
        self.view.request_refresh.emit()
        self._notify("Refreshing targets…")

    def _trigger_export_csv(self):
        self.view.request_export.emit()
        self._notify("Exporting CSV…")

    def _trigger_export_json(self):
        self.view.request_export_json.emit()
        self._notify("Exporting JSON…")

    def _trigger_load_targets(self):
        self.view.request_load_targets.emit()
        self._notify("Select a targets JSON file…")

    def _trigger_open_settings(self):
        self.view.request_open_settings.emit()
        self._notify("Opening settings…")

    def _trigger_add_targets(self):
        self.view.show_add_dialog()
        self._notify("Add targets dialog opened.")

    def _trigger_remove_selected(self):
        if self.view.remove_selected():
            self._notify("Removal requested for selected targets.")
        else:
            self._notify("Select targets to remove.")

    def _trigger_ignore_selected(self):
        if self.view.ignore_selected():
            self._notify("Selected targets marked as ignored.")
        else:
            self._notify("Select targets to ignore.")

    def _trigger_unignore_selected(self):
        if self.view.unignore_selected():
            self._notify("Selected targets marked as unignored.")
        else:
            self._notify("Select targets to unignore.")

    def _trigger_manage_ignore(self):
        self.view.request_manage_ignore.emit()
        self._notify("Opening ignore manager…")

    def _trigger_copy_ids(self):
        if self.view.copy_selected_ids():
            self._notify("Copied selected ID(s) to clipboard.")
        else:
            self._notify("Select at least one target to copy IDs.")

    def _trigger_focus_search(self):
        self.view.focus_search_bar()
        self._notify("Search bar focused.")

    def _trigger_reset_sort(self):
        self.view.reset_sorting()
        self._notify("Sorting reset to default.")

    def _toggle_filters(self, checked: bool):
        self.view.set_filters_visible(checked)
        self._notify("Filters shown." if checked else "Filters hidden.")

    def _toggle_toolbar(self, checked: bool):
        self.view.set_toolbar_visible(checked)
        self._notify("Toolbar shown." if checked else "Toolbar hidden.")

    def _open_appdata_dir(self):
        path = get_appdata_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))
        self._notify("Opened app data folder.")

    def _open_docs(self):
        try:
            dlg = DocumentationDialog(self)
            dlg.exec()
            self._notify("Documentation opened.")
        except Exception as exc:
            doc = _first_existing([
                "README.md",
                os.path.join("assets", "Updated_README.rtf"),
                os.path.join("assets", "README.rtf"),
            ])
            if doc and os.path.exists(doc):
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(doc)))
                self._notify("Opened fallback documentation.")
            else:
                QDesktopServices.openUrl(QUrl("https://github.com/"))
                self._notify("Opened online documentation.")
            logger.exception("Failed to open documentation dialog: %s", exc)

    def _trigger_about(self):
        self.view.request_show_about.emit()
        self._notify("About dialog opened.")

    def attach_controller(self, controller):
        """Attach the app Controller and wire status callbacks."""
        self._controller = controller
        controller.set_status_handlers(
            status_cb=self._status.set_message,
            progress_cb=self._status.set_progress,
            meta_cb=self._status.set_meta,
        )

    def closeEvent(self, e):
        try:
            # ensure workers/timers stop before closing
            if self._controller:
                self._controller.shutdown()
            st = load_settings()
            st["last_size"] = [self.width(), self.height()]
            st["last_pos"] = [self.x(), self.y()]
            save_settings(st)
        except Exception:
            pass
        super().closeEvent(e)
