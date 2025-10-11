# controllers.py
from __future__ import annotations

import os
import sys
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from PyQt6.QtCore import Qt, QSize, QPoint
from PyQt6.QtGui import QIcon, QFontMetrics, QCursor, QColor
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
)

# App modules
from views import MainView
from storage import load_settings, save_settings, get_appdata_dir

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
def apply_darkstyle(app: QApplication):
    """Apply QDarkStyle for PyQt6 if available (no crash if missing)."""
    base = ""
    try:
        import qdarkstyle  # pip install qdarkstyle
        try:
            base = qdarkstyle.load_stylesheet(qt_api="pyqt6")
        except Exception:
            base = qdarkstyle.load_stylesheet_pyqt6()
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
    app.setStyleSheet((base or "") + "\n" + extra_css)


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
    def __init__(self, text: str = "—"):
        super().__init__(text)
        self.setObjectName("pill")
        self.setStyleSheet("""
            QLabel#pill {
                padding: 4px 8px;
                border-radius: 10px;
                border: 1px solid rgba(255,255,255,0.12);
                background: rgba(255,255,255,0.06);
                color: #cfd7e3;
            }
        """)


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

        self._meta = Pill("—")

        self.addWidget(self._left, 1)
        self.addPermanentWidget(self._prog, 0)
        self.addPermanentWidget(self._meta, 0)

        self._current_errors = 0
        self._set_status_icon("info")
        self._set_info_tooltip()

    def set_message(self, text: str):
        text = text or ""
        fm = QFontMetrics(self._msg.font())
        w = max(480, self._msg.width())
        self._msg.setText(fm.elidedText(text, Qt.TextElideMode.ElideRight, w))
        self._msg.setToolTip(text)
        self.showMessage(text, 3000)
        self._apply_icon_and_color(text)

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
        self._meta.setText(text or "—")
        self._meta.setToolTip(text or "")

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
        import re
        self._current_errors = 0
        m = re.search(r"errors:\s*(\d+)", text, flags=re.IGNORECASE) or \
            re.search(r"\b(\d+)\s+errors\b", text, flags=re.IGNORECASE)
        if m:
            try:
                self._current_errors = int(m.group(1))
            except Exception:
                self._current_errors = 0

        low = text.lower()
        if "done" in low:
            self._set_status_icon("ok" if self._current_errors == 0 else "error")
            (self._prog.set_done() if self._current_errors == 0 else self._prog.set_error())
        elif "updating" in low or "fetch" in low or "loading" in low:
            self._set_status_icon("progress" if self._current_errors == 0 else "error")
            (self._prog.set_ok() if self._current_errors == 0 else self._prog.set_error())
        elif self._current_errors > 0:
            self._set_status_icon("error")
            self._prog.set_error()
        else:
            self._set_status_icon("info")
            self._prog.set_ok()

        if self._current_errors > 0:
            self._set_error_tooltip(self._current_errors)
        else:
            self._set_info_tooltip()


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

        self._controller = None  # set via attach_controller()

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
            st = load_settings()
            st["last_size"] = [self.width(), self.height()]
            st["last_pos"] = [self.x(), self.y()]
            save_settings(st)
        except Exception:
            pass
        super().closeEvent(e)
