from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, QSize, QTimer, QPoint
from PyQt6.QtGui import QColor, QCursor, QFontMetrics, QIcon, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QStatusBar,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _hbox(
    parent: QWidget | None,
    l: int = 0, t: int = 0, r: int = 0, b: int = 0,
    spacing: int = 10,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignVCenter,
) -> QHBoxLayout:
    lay = QHBoxLayout(parent)
    lay.setContentsMargins(l, t, r, b)
    lay.setSpacing(spacing)
    lay.setAlignment(align)
    return lay


class _VRule(QWidget):
    """True 1-px vertical rule on a fully transparent background (no dark slabs)."""
    def __init__(self, alpha: float = 0.12, parent: QWidget | None = None):
        super().__init__(parent)
        self._color = QColor(255, 255, 255)
        self._color.setAlphaF(alpha)
        self.setFixedWidth(1)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAutoFillBackground(False)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(QPen(self._color, 1))
        # slightly shorter to suit thinner bar
        p.drawLine(0, 4, 0, self.height() - 4)
        p.end()


# ──────────────────────────────────────────────────────────────────────────────
# Pill
# ──────────────────────────────────────────────────────────────────────────────

class _GlassPill(QFrame):
    """Compact glassy pill. Dynamically sizes to content (no clipping, no jitter)."""

    _PAD_LR = 12
    _PAD_TB = 4
    _HEIGHT = 26  # ↓ from 30 to fit a 36px bar cleanly

    def __init__(self, text: str = "", variant: str = "info", height: int | None = None):
        super().__init__()
        self.setObjectName("glassPill")
        self.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed))
        self.setFixedHeight(height or self._HEIGHT)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(self._PAD_LR, self._PAD_TB, self._PAD_LR, self._PAD_TB)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._label = QLabel("")
        self._label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        lay.addWidget(self._label)

        self._raw_text = ""

        self.setStyleSheet(
            """
            QFrame#glassPill {
                border-radius: 5px;
                border: 1px solid rgba(185, 200, 225, 0.35);
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(28, 38, 52, 0.60),
                    stop:1 rgba(18, 26, 38, 0.60));
            }
            QFrame#glassPill QLabel {
                color: rgba(234, 242, 255, 0.96);
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.2px;
            }

            QFrame#glassPill[variant="info"] {
                border: 1px solid rgba(150, 200, 255, 0.32);
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 rgba(28, 44, 64, 0.70),
                    stop:1 rgba(20, 32, 48, 0.70));
            }
            QFrame#glassPill[variant="muted"] {
                border: 1px solid rgba(160, 175, 190, 0.28);
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 rgba(28, 36, 48, 0.55),
                    stop:1 rgba(20, 26, 36, 0.55));
            }
            QFrame#glassPill[variant="ok"] {
                border: 1px solid rgba(92, 226, 170, 0.70);
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 rgba(70, 190, 150, 0.82),
                    stop:1 rgba(50, 150, 118, 0.82));
            }
            QFrame#glassPill[variant="ok"] QLabel { color: rgba(12, 28, 22, 0.98); }

            QFrame#glassPill[variant="warn"] {
                border: 1px solid rgba(255, 194, 110, 0.66);
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 rgba(218, 166, 90, 0.86),
                    stop:1 rgba(182, 136, 70, 0.86));
            }
            QFrame#glassPill[variant="warn"] QLabel { color: rgba(38, 24, 6, 0.98); }

            QFrame#glassPill[variant="error"] {
                border: 1px solid rgba(255, 132, 140, 0.72);
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 rgba(214, 90, 102, 0.88),
                    stop:1 rgba(180, 64, 78, 0.88));
            }
            QFrame#glassPill[variant="error"] QLabel { color: rgba(250, 240, 245, 0.98); }

            QFrame#glassPill[variant="auto"] {
                border: 1px solid rgba(176, 148, 250, 0.72);
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 rgba(148, 118, 240, 0.88),
                    stop:1 rgba(108, 86, 200, 0.88));
            }
            QFrame#glassPill[variant="auto"] QLabel { color: rgba(246, 240, 255, 0.98); }
            """
        )
        self.set_variant(variant)
        self.setText(text)

    def _sync_width_to_text(self):
        fm = QFontMetrics(self._label.font())
        text_w = fm.horizontalAdvance(self._raw_text or "")
        target = text_w + (self._PAD_LR * 2) + 2  # +2 for borders
        if self.minimumWidth() != target:
            self.setMinimumWidth(target)

    def setText(self, text: str):
        self._raw_text = text or ""
        self._label.setText(self._raw_text)
        self._sync_width_to_text()
        self.setToolTip(self._raw_text)

    def text(self) -> str:
        return self._raw_text

    def set_variant(self, variant: str):
        self.setProperty("variant", variant or "info")
        self.style().unpolish(self)
        self.style().polish(self)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._label.font())
        h = max(self._HEIGHT, fm.height() + self._PAD_TB * 2 + 2)
        w = max(self.minimumWidth(), fm.horizontalAdvance(self._label.text()) + self._PAD_LR * 2 + 2)
        return QSize(w, h)


# ──────────────────────────────────────────────────────────────────────────────
# Progress
# ──────────────────────────────────────────────────────────────────────────────

class _PrettyProgress(QProgressBar):
    def __init__(self):
        super().__init__()
        # ↓ heights tuned for a thin bar
        self.setMinimumHeight(20)
        self.setMaximumHeight(20)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setRange(0, 0)  # busy until first set_progress()
        self._styles = {
            "ok": self._gradient("#57a7ff", "#89c0ff"),
            "error": self._gradient("#ff6a7a", "#ff8f8a"),
            "done": self._gradient("#4fd18e", "#73e3a3"),
        }
        self.set_ok()

    def _gradient(self, start: str, stop: str) -> str:
        return f"""
            QProgressBar {{
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 5px;
                background: rgba(8, 12, 20, 0.55);
                padding: 0px;
                text-align: center;
                color: rgba(235,242,255,0.85);
                font-size: 11px;
                font-weight: 600;
            }}
            QProgressBar::chunk {{
                border-radius: 6px;
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {start}, stop:1 {stop});
            }}
        """

    def set_ok(self):
        self.setStyleSheet(self._styles["ok"])

    def set_error(self):
        self.setStyleSheet(self._styles["error"])

    def set_done(self):
        self.setStyleSheet(self._styles["done"])


# ──────────────────────────────────────────────────────────────────────────────
# Popover + hover icon (unchanged behavior, polished visuals)
# ──────────────────────────────────────────────────────────────────────────────

class _InfoPopover(QFrame):
    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setObjectName("infoPopover")
        self.setStyleSheet(
            """
            #infoPopover {
                background-color: #1b2432;
                border: 1px solid #334055;
                border-radius: 8px;
            }
            #infoPopover QLabel { color: #e6eef8; }
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)
        label = QLabel("")
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setOpenExternalLinks(True)
        label.setWordWrap(True)
        lay.addWidget(label)
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


class _HoverIcon(LABEL := QLabel):
    def __init__(self):
        super().__init__()
        self.setFixedSize(QSize(22, 22))
        self.setScaledContents(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._enabled = True
        self._html_tip = ""
        self._popover = _InfoPopover()

    def set_icon(self, icon: QIcon, size: int = 20):
        self.setPixmap(icon.pixmap(size, size) if icon and not icon.isNull() else QIcon().pixmap(size, size))

    def set_rich_tooltip(self, html: str, enabled: bool = True):
        self._html_tip = html or ""
        self._enabled = bool(enabled)

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._enabled and self._html_tip:
            self._popover.set_html(self._html_tip)
            rect = self.rect()
            top_left = self.mapToGlobal(rect.topLeft())
            bottom_left = self.mapToGlobal(rect.bottomLeft())
            screen = QApplication.screenAt(top_left) or QApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else None
            gap = 8
            x = top_left.x()
            y = top_left.y() - self._popover.height() - gap
            if avail and y < avail.top() + 6:
                y = bottom_left.y() + gap
            if avail:
                x = max(avail.left() + 6, min(x, avail.right() - 6 - self._popover.width()))
            self._popover.show_at(QPoint(x, y))

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if not self._popover.under_cursor():
            self._popover.hide()


# ──────────────────────────────────────────────────────────────────────────────
# Meta bar
# ──────────────────────────────────────────────────────────────────────────────

class _MetaBar(QWidget):
    """Holds the dynamic pills on the right (Total / Ignored / Visible …)."""

    def __init__(self, pill_factory: Callable[[str, str], _GlassPill], parent=None):
        super().__init__(parent)
        self._pill_factory = pill_factory
        self._layout = _hbox(self, l=0, t=0, r=0, b=0, spacing=8)
        self._pills: List[_GlassPill] = []
        self._placeholder = pill_factory("No stats", "muted")
        self._layout.addWidget(self._placeholder)

    def set_items(self, items: List[str]):
        self.setUpdatesEnabled(False)
        try:
            for pill in self._pills:
                pill.hide()

            if not items:
                self._placeholder.show()
                return

            self._placeholder.hide()

            while len(self._pills) < len(items):
                pill = self._pill_factory("", "info")
                pill.hide()
                self._pills.append(pill)
                self._layout.addWidget(pill)

            for pill, text in zip(self._pills, items):
                pill.setText(text)
                pill.set_variant(self._classify(text))
                pill.show()
        finally:
            self.setUpdatesEnabled(True)

    @staticmethod
    def _classify(text: str) -> str:
        low = text.lower()
        if "error" in low or "fail" in low:
            return "error"
        if "warn" in low or "ignore" in low:
            return "warn"
        if any(word in low for word in ("done", "visible", "total", "cached", "ok", "ready")):
            return "ok"
        return "info"


# ──────────────────────────────────────────────────────────────────────────────
# StatusBar
# ──────────────────────────────────────────────────────────────────────────────

class StatusBar(QStatusBar):
    """Refined, thinner, flicker-free status bar."""

    _BAR_H = 38  # ↓ from 48 — enough for 26px pills + a touch of vertical air

    def __init__(self, icon_provider: Callable[[str], QIcon], parent=None):
        super().__init__(parent)
        self._icon_provider = icon_provider

        self.setSizeGripEnabled(False)
        self.setMinimumHeight(self._BAR_H)
        self.setMaximumHeight(self._BAR_H)
        self.setContentsMargins(0, 2, 0, 2)  # tighter vertical padding
        self.setStyleSheet(
            f"""
            QStatusBar {{
                background: #0b1118;
                border-top: 1px solid rgba(255,255,255,0.08);
                min-height: {self._BAR_H}px;
            }}
            QStatusBar::item {{
                border: 0px;
                background: transparent;
                padding: 0px;
                margin: 0px;
            }}
            """
        )

        # ── Left cluster ──────────────────────────────────────────────────────
        self._left = QWidget(self)
        self._left.setAutoFillBackground(False)
        self._left.setStyleSheet("background: transparent;")
        left_layout = _hbox(self._left, l=12, t=2, r=12, b=2, spacing=10)  # ↓ vertical margins

        self._icon = _HoverIcon()

        self._msg = QLabel("Ready")
        self._msg.setMinimumWidth(140)
        self._msg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._msg.setStyleSheet("color: #e9f2ff; font-size: 12px;")

        self._auto_pill = _GlassPill("Auto: Off", "muted", height=26)
        self._auto_pill.hide()

        left_layout.addWidget(self._icon)
        left_layout.addWidget(self._msg, 1)
        left_layout.addWidget(self._auto_pill, 0, Qt.AlignmentFlag.AlignVCenter)

        # ── Right cluster (single container) ─────────────────────────────────
        self._right = QWidget(self)
        self._right.setAutoFillBackground(False)
        self._right.setStyleSheet("background: transparent;")
        right_layout = _hbox(self._right, l=0, t=2, r=12, b=2, spacing=0)  # ↓ vertical margins

        # Progress
        self._progress = _PrettyProgress()
        self._progress.setMinimumHeight(20)
        self._progress.setMaximumHeight(20)

        progress_wrap = QWidget(self._right)
        progress_wrap.setAutoFillBackground(False)
        progress_wrap.setStyleSheet("background: transparent;")
        progress_layout = _hbox(progress_wrap, l=0, t=0, r=0, b=0, spacing=0)
        progress_wrap.setFixedHeight(28)  # ↓ from 34
        progress_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        progress_layout.addWidget(self._progress)

        # Meta pills
        self._meta_bar = _MetaBar(lambda text, variant: _GlassPill(text, variant, height=26))
        meta_wrap = QWidget(self._right)
        meta_wrap.setAutoFillBackground(False)
        meta_wrap.setStyleSheet("background: transparent;")
        meta_layout = _hbox(meta_wrap, l=0, t=0, r=0, b=0, spacing=0)
        meta_wrap.setFixedHeight(28)  # ↓ from 34
        meta_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        meta_layout.addWidget(self._meta_bar)

        # Clock
        self._clock = _GlassPill("Updated -", "muted", height=26)
        clock_wrap = QWidget(self._right)
        clock_wrap.setAutoFillBackground(False)
        clock_wrap.setStyleSheet("background: transparent;")
        clock_layout = _hbox(clock_wrap, l=0, t=0, r=0, b=0, spacing=0)
        clock_wrap.setFixedHeight(28)  # ↓ from 34
        clock_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        clock_layout.addWidget(self._clock)

        # Build: spacing · rule · spacing · progress · spacing · rule · spacing · meta · spacing · rule · spacing · clock
        def add_rule(container_layout: QHBoxLayout, gap=8):  # ↓ gap for tighter bar
            container_layout.addSpacing(gap)
            container_layout.addWidget(_VRule(), 0)
            container_layout.addSpacing(gap)

        add_rule(right_layout)
        right_layout.addWidget(progress_wrap, 0, Qt.AlignmentFlag.AlignVCenter)
        add_rule(right_layout)
        right_layout.addWidget(meta_wrap, 0, Qt.AlignmentFlag.AlignVCenter)
        add_rule(right_layout)
        right_layout.addWidget(clock_wrap, 0, Qt.AlignmentFlag.AlignVCenter)

        # Assemble on the bar
        self.addWidget(self._left, 10)
        self.addPermanentWidget(self._right, 0)

        # ── State ─────────────────────────────────────────────────────────────
        self._current_errors = 0
        self._last_timestamp: Optional[datetime] = None
        self._last_result_ok = True
        self._is_fetching = False
        self._auto_target: Optional[datetime] = None
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(1000)
        self._auto_timer.timeout.connect(self._tick_auto)

        self._set_status_icon("info")
        self._set_info_tooltip()

    # ── Public API ───────────────────────────────────────────────────────────

    def set_message(self, text: str):
        text = text or ""
        fm = QFontMetrics(self._msg.font())
        width = max(140, self._msg.width())
        self._msg.setText(fm.elidedText(text, Qt.TextElideMode.ElideRight, width))
        self._msg.setToolTip(text)

        self._apply_icon_and_color(text)
        self._record_activity_timestamp(text)

    def set_progress(self, cur: int, total: int):
        total = max(1, int(total))
        cur = max(0, min(int(cur), total))
        if self._progress.maximum() == 0:
            self._progress.setRange(0, total)
        else:
            self._progress.setMaximum(total)
        self._progress.setValue(cur)
        self._progress.setFormat(f"{cur}/{total}")
        self._progress.setToolTip(f"{cur} of {total} fetched")
        if cur == total:
            self._progress.set_done() if self._current_errors == 0 else self._progress.set_error()

    def set_meta(self, text: str):
        raw = (text or "").strip()
        parts = [p.strip() for p in re.split(r"[\u2022|]+", raw) if p.strip()]
        self._right.setUpdatesEnabled(False)
        try:
            self._meta_bar.set_items(parts)
        finally:
            self._right.setUpdatesEnabled(True)

    def set_auto_eta(self, seconds: Optional[int]):
        if seconds is None:
            self._auto_timer.stop()
            self._auto_target = None
            self._auto_pill.setText("Auto: Paused")
            self._auto_pill.set_variant("muted")
            self._auto_pill.show()
        elif seconds < 0:
            self._auto_timer.stop()
            self._auto_target = None
            self._auto_pill.setText("Auto: Off")
            self._auto_pill.set_variant("muted")
            self._auto_pill.show()
        else:
            secs = max(0, int(seconds))
            self._auto_target = datetime.now(timezone.utc) + timedelta(seconds=secs)
            self._auto_timer.start()
            self._auto_pill.set_variant("auto")
            self._auto_pill.show()
            self._update_auto_label()

    def set_fetching(self, active: bool):
        self._is_fetching = bool(active)
        self._refresh_timestamp_label()

    # ── Internals ────────────────────────────────────────────────────────────

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.set_message(self._msg.toolTip())
        bar_w = self.width()
        target = max(160, min(340, int(bar_w * 0.18)))
        self._progress.setMinimumWidth(target)

    def _tick_auto(self):
        if not self._auto_target:
            return
        remaining = int((self._auto_target - datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            self._auto_timer.stop()
            self._auto_pill.setText("Auto: Now")
            return
        self._update_auto_label(remaining)

    def _update_auto_label(self, remaining: Optional[int] = None):
        if remaining is None and self._auto_target:
            remaining = int((self._auto_target - datetime.now(timezone.utc)).total_seconds())
        remaining = max(0, remaining or 0)
        if remaining >= 3600:
            label = f"Auto: {remaining // 3600}h {(remaining % 3600) // 60}m"
        elif remaining >= 60:
            label = f"Auto: {remaining // 60:02d}:{remaining % 60:02d}"
        else:
            label = f"Auto: 0:{remaining:02d}"
        self._auto_pill.setText(label)

    def _set_status_icon(self, kind: str):
        name = {
            "ok": "check",
            "warn": "warning",
            "error": "error",
            "progress": "refresh",
            "info": "info",
        }.get(kind, "info")
        icon = self._icon_provider(name) if self._icon_provider else QIcon()
        self._icon.set_icon(icon, size=20)

    def _set_info_tooltip(self):
        html = (
            "<div style='min-width:240px'>"
            "<div style='font-weight:600;color:#cfe3ff;margin-bottom:4px'>Status</div>"
            "<div>No errors detected.</div>"
            "</div>"
        )
        self._icon.set_rich_tooltip(html, enabled=True)

    def _set_error_tooltip(self, count: int):
        html = (
            "<div style='min-width:260px'>"
            "<div style='font-weight:700;color:#ffd1d1;margin-bottom:6px'>Fetch Errors</div>"
            "<div style='color:#e6eef8'>Some requests failed during the last update.</div>"
            "<ul style='margin:6px 0 0 16px;'>"
            f"<li><b>{count}</b> error(s) in this run</li>"
            "<li>Check the <i>Error</i> column for row details.</li>"
            "</ul></div>"
        )
        self._icon.set_rich_tooltip(html, enabled=True)

    def _apply_icon_and_color(self, text: str):
        self._current_errors = 0
        match = re.search(r"errors:\s*(\d+)", text, flags=re.IGNORECASE) or re.search(
            r"\b(\d+)\s+errors\b", text, flags=re.IGNORECASE
        )
        if match:
            try:
                self._current_errors = int(match.group(1))
            except Exception:
                self._current_errors = 0

        low = text.lower()
        self._is_fetching = False

        if "done" in low:
            self._set_status_icon("ok" if self._current_errors == 0 else "error")
            self._progress.set_done() if self._current_errors == 0 else self._progress.set_error()
        elif any(word in low for word in ("updating", "fetch", "loading")):
            self._set_status_icon("progress" if self._current_errors == 0 else "error")
            self._progress.set_ok() if self._current_errors == 0 else self._progress.set_error()
            self._is_fetching = True
        elif self._current_errors > 0:
            self._set_status_icon("error")
            self._progress.set_error()
        else:
            self._set_status_icon("info")
            self._progress.set_ok()

        if self._current_errors > 0:
            self._set_error_tooltip(self._current_errors)
        else:
            self._set_info_tooltip()

    def _record_activity_timestamp(self, text: str):
        triggers = ("done", "export", "refresh", "updated", "loaded", "saved", "copied", "import")
        if any(word in text.lower() for word in triggers):
            self._last_timestamp = datetime.now(timezone.utc)
            has_error = "error" in text.lower() and not re.search(r"errors?\s*:\s*0\b", text.lower())
            self._last_result_ok = self._current_errors == 0 and not has_error
            self._refresh_timestamp_label()

    def _refresh_timestamp_label(self):
        if self._is_fetching:
            self._clock.setText("Fetching…")
            self._clock.set_variant("ok")
            self._clock.setToolTip("Currently fetching targets")
            return

        if not self._last_timestamp:
            self._clock.setText("Updated - never")
            self._clock.set_variant("muted")
            self._clock.setToolTip("No activity yet.")
            return

        seconds = max(0, int((datetime.now(timezone.utc) - self._last_timestamp).total_seconds()))
        if seconds < 5:
            label = "Updated - just now"
        elif seconds < 60:
            label = f"Updated - {seconds}s ago"
        elif seconds < 3600:
            label = f"Updated - {seconds // 60} min ago"
        else:
            label = f"Updated - {seconds // 3600} hr ago"
        self._clock.setText(label)

        if self._last_result_ok:
            variant = "ok" if seconds <= 180 else ("info" if seconds <= 900 else "warn")
        else:
            variant = "error" if seconds <= 600 else "warn"
        self._clock.set_variant(variant)
        tooltip = self._last_timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        self._clock.setToolTip(tooltip)


__all__ = ["StatusBar"]
