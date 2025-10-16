# search_bar.py
from __future__ import annotations

import os
import sys
from typing import Optional, Dict

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal, QPoint
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QToolButton, QStyle,
    QComboBox, QListView, QSpinBox
)

# ---------- asset helpers ----------
def _asset_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    cand = os.path.join(base, rel)
    return cand if os.path.exists(cand) else rel

def _icon_path(name: str) -> Optional[str]:
    for p in (
        _asset_path(os.path.join("assets", f"ic-{name}.svg")),
        _asset_path(os.path.join("assets", f"ic-{name}.png")),
        _asset_path(f"ic-{name}.svg"),
        _asset_path(f"ic-{name}.png"),
    ):
        if os.path.exists(p):
            return p
    return None

def _icon(name: str) -> QIcon:
    p = _icon_path(name)
    return QIcon(p) if p else QIcon()


# ---------- popup-safe combo (prevents clipping behind the table) ----------
class PopupCombo(QComboBox):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.setView(QListView())
        self.setMaxVisibleItems(12)

    def showPopup(self) -> None:
        super().showPopup()
        try:
            w = self.view().window()
            w.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            w.raise_()
            w.activateWindow()
            # align just below the combo
            below = self.mapToGlobal(self.rect().bottomLeft())
            geo = w.geometry()
            if abs(geo.topLeft().y() - below.y()) > 2:
                w.move(QPoint(geo.left(), below.y()))
        except Exception:
            try:
                self.view().raise_()
            except Exception:
                pass


class SearchBar(QWidget):
    """
    Compact, native-looking search bar.
    Emits: queryChanged({'text','mode','regex','case_sensitive'})
    """
    queryChanged = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SearchBar")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # Use the spinbox height as the reference so we match your other controls
        ref = QSpinBox()
        control_h = max(24, ref.sizeHint().height())  # typical 26–30 depending on theme
        icon_h = max(14, control_h - 10)

        # ---- Scope combo (same height as spinboxes)
        self.scope = PopupCombo(self)
        self.scope.setObjectName("sb-scope")
        self.scope.addItems(["All", "Name", "ID", "Faction"])
        self.scope.setFixedHeight(control_h)
        self.scope.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
        self.scope.currentIndexChanged.connect(self._emit)

        # ---- Search edit (native frame; no custom colors)
        self.edit = QLineEdit(self)
        self.edit.setObjectName("sb-edit")
        self.edit.setPlaceholderText("Search…  (text, /regex/, id, faction)")
        self.edit.setFixedHeight(control_h)

        # Leading search icon
        left_ic = _icon("search")
        if left_ic.isNull():
            left_ic = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogStart)
        self.edit.addAction(left_ic, QLineEdit.ActionPosition.LeadingPosition)

        # Trailing clear icon
        clear_ic = _icon("clear")
        if clear_ic.isNull():
            clear_ic = self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton)
        act_clear = self.edit.addAction(clear_ic, QLineEdit.ActionPosition.TrailingPosition)
        act_clear.setToolTip("Clear")
        act_clear.triggered.connect(self.edit.clear)

        # Debounce typing
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self._emit)
        self.edit.textChanged.connect(lambda _=None: self._timer.start())

        # ---- Toggles (same square height as the other controls)
        self.btn_regex = QToolButton(self)
        self.btn_regex.setObjectName("sb-toggle")
        self.btn_regex.setCheckable(True)
        self.btn_regex.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.btn_regex.setAutoRaise(True)
        self.btn_regex.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_regex.setIcon(_icon("regex"))
        if self.btn_regex.icon().isNull():
            self.btn_regex.setText(".*")
        self.btn_regex.setIconSize(QSize(icon_h, icon_h))
        self.btn_regex.setFixedSize(control_h, control_h)
        self.btn_regex.toggled.connect(self._emit)

        self.btn_case = QToolButton(self)
        self.btn_case.setObjectName("sb-toggle")
        self.btn_case.setCheckable(True)
        self.btn_case.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.btn_case.setAutoRaise(True)
        self.btn_case.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_case.setIcon(_icon("case"))
        if self.btn_case.icon().isNull():
            self.btn_case.setText("Aa")
        self.btn_case.setIconSize(QSize(icon_h, icon_h))
        self.btn_case.setFixedSize(control_h, control_h)
        self.btn_case.toggled.connect(self._emit)

        # Layout order
        lay.addWidget(self.scope)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.btn_regex)
        lay.addWidget(self.btn_case)

        # Minimal style adjustments only for spacing/padding.
        # No colors—lets your global QSS/theme handle visuals so it matches the app.
        self.setStyleSheet(f"""
        #SearchBar QComboBox#sb-scope {{
            min-height: {control_h}px; max-height: {control_h}px;
            padding: 0px 24px 0px 8px;   /* text left + arrow right */
        }}
        #SearchBar QLineEdit#sb-edit {{
            min-height: {control_h}px; max-height: {control_h}px;
            padding-left: 26px;          /* make room for search icon */
            padding-right: 26px;         /* make room for clear icon  */
        }}
        #SearchBar QToolButton#sb-toggle {{
            min-height: {control_h}px; max-height: {control_h}px;
            min-width: {control_h}px;  max-width: {control_h}px;
            margin: 0;
        }}
        """)

    # ------------------- API -------------------
    def focus(self) -> None:
        self.edit.setFocus()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

    def text(self) -> str:
        return self.edit.text()

    # ------------------- Internals -------------------
    def _emit(self) -> None:
        txt = self.edit.text()
        # smart /regex/ detection
        if txt.startswith("/") and txt.endswith("/") and len(txt) > 1:
            self.btn_regex.setChecked(True)
            txt = txt[1:-1]
        payload: Dict[str, object] = {
            "text": txt,
            "mode": self.scope.currentText().lower(),   # all | name | id | faction
            "regex": self.btn_regex.isChecked(),
            "case_sensitive": self.btn_case.isChecked(),
        }
        self.queryChanged.emit(payload)
