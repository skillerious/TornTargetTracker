# controllers.py
from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from PyQt6.QtCore import Qt, QSize, QPoint, QTimer
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
)

# App modules
from views import MainView
from storage import load_settings, save_settings, get_appdata_dir

# Optional onboarding (no-op if not present)
try:
    from onboarding import maybe_show_onboarding
except Exception:  # pragma: no cover
    def maybe_show_onboarding(parent=None):  # type: ignore
        return


# ---------------- logging ----------------
logger = logging.getLogger("TargetTracker.Main")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)


# ---------------- util: icons ----------------
def _icon_path(name: str) -> Optional[str]:
    for p in (
        os.path.join("assets", f"ic-{name}.svg"),
        os.path.join("assets", f"ic-{name}.png"),
        f"ic-{name}.svg",
        f"ic-{name}.png",
    ):
        if os.path.exists(p):
            return p
    return None


def app_icon() -> QIcon:
    p = _icon_path("app")
    return QIcon(p) if p else QIcon()


def themed_icon(name: str) -> QIcon:
    p = _icon_path(name)
    return QIcon(p) if p else QIcon()


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
    """
    Lightweight, non-flickery popover that behaves like a tooltip
    but stays visible while the mouse is over the icon or the popover.
    """
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
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._label = QLabel("")
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setOpenExternalLinks(True)
        self._label.setWordWrap(True)
        self._layout.addWidget(self._label)

        # Soft drop shadow
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
        # Hit-test in local coordinates to avoid flicker
        return self.rect().contains(self.mapFromGlobal(pos))


class HoverIcon(QLabel):
    """
    QLabel that renders crisp icons without clipping and shows a custom popover
    that does not flicker and stays open while hovered.
    """
    def __init__(self):
        super().__init__()
        self.setFixedSize(QSize(22, 22))            # room to avoid SVG clipping
        self.setContentsMargins(0, 0, 0, 0)
        self.setScaledContents(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._enabled = True
        self._html_tip: str = ""
        self._popover = InfoPopover()

        # Timers for smooth show/hide without loops
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.setInterval(120)
        self._show_timer.timeout.connect(self._really_show)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(200)  # slightly longer to allow moving to popover
        self._hide_timer.timeout.connect(self._maybe_hide)

    def set_icon(self, ic: QIcon, size: int = 20):
        if ic and not ic.isNull():
            self.setPixmap(ic.pixmap(size, size))
        else:
            self.setPixmap(QIcon().pixmap(size, size))

    def set_rich_tooltip(self, html: str, enabled: bool = True):
        self._html_tip = html or ""
        self._enabled = bool(enabled)

    # --- events ---
    def enterEvent(self, ev):
        super().enterEvent(ev)
        if self._enabled and self._html_tip:
            self._show_timer.start()
            self._hide_timer.stop()

    def leaveEvent(self, ev):
        super().leaveEvent(ev)
        # Start a delayed hide; cancel if we re-enter or if popover is hovered
        self._hide_timer.start()

    def mouseMoveEvent(self, ev):
        # Keep it visible while we move within the icon
        if self._popover.isVisible():
            self._hide_timer.stop()
        super().mouseMoveEvent(ev)

    # --- internals ---
    def _really_show(self):
        if not (self._enabled and self._html_tip):
            return
        self._popover.set_html(self._html_tip)
        self._popover.adjustSize()

        icon_rect = self.rect()
        global_top_left = self.mapToGlobal(icon_rect.topLeft())
        global_bottom_left = self.mapToGlobal(icon_rect.bottomLeft())

        # Pick the screen *at the icon*, not the primary screen
        screen = QApplication.screenAt(global_top_left) or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()

        gap = 8
        # preferred: above
        x = global_top_left.x()
        y = global_top_left.y() - self._popover.height() - gap

        # flip below if not enough headroom
        if y < avail.top() + 6:
            y = global_bottom_left.y() + gap

        # clamp horizontally
        x = max(avail.left() + 6, min(x, avail.right() - 6 - self._popover.width()))

        self._popover.show_at(QPoint(x, y))

    def _maybe_hide(self):
        # Keep shown if mouse is over icon or popover
        global_pos = QCursor.pos()
        over_icon = self.rect().contains(self.mapFromGlobal(global_pos))
        if over_icon or self._popover.under_cursor():
            return
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
        self.setMinimumHeight(28)  # give icons/badges breathing room
        self.setStyleSheet("QStatusBar { border-top: 1px solid rgba(255,255,255,0.08); }")

        # Left: icon + elided text (added as normal widget)
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

        # Center: progress (permanent)
        self._prog = PrettyProgress()
        self._prog.setRange(0, 1)
        self._prog.setValue(0)
        self._prog.setTextVisible(True)
        self._prog.setFormat("0/1")

        # Right: meta pill (permanent)
        self._meta = Pill("—")

        # add to status bar
        self.addWidget(self._left, 1)
        self.addPermanentWidget(self._prog, 0)
        self.addPermanentWidget(self._meta, 0)

        self._current_errors = 0
        self._set_status_icon("info")
        self._set_info_tooltip()

    # ---------- public API (used from the Controller via MainWindow) ----------
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

    # ---------- internals ----------
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
        # Parse errors either "errors: N" or "… N errors"
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

        # Popover content depending on error state
        if self._current_errors > 0:
            self._set_error_tooltip(self._current_errors)
        else:
            self._set_info_tooltip()


# ---------------- Main Window ----------------
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

        # central view
        self.view = MainView()
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.setCentralWidget(central)

        # fancy status bar
        self._status = FancyStatusBar(self)
        self.setStatusBar(self._status)

        self._controller = None  # set via attach_controller()

        # show onboarding after the window is up (first run / user preference)
        QTimer.singleShot(0, lambda: maybe_show_onboarding(self))

    def attach_controller(self, controller):
        """Attach the app Controller and wire status callbacks."""
        self._controller = controller
        controller.set_status_handlers(
            status_cb=self._status.set_message,
            progress_cb=self._status.set_progress,
            meta_cb=self._status.set_meta,
        )

    # ---- close/save hooks ----
    def closeEvent(self, e):
        try:
            st = load_settings()
            st["last_size"] = [self.width(), self.height()]
            st["last_pos"] = [self.x(), self.y()]
            save_settings(st)
        except Exception:
            pass
        super().closeEvent(e)
