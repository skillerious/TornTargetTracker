# settings_dialog.py
from __future__ import annotations

import json
import os
import threading
import socket
from typing import Dict, Optional, Tuple

import urllib.request
import urllib.error

from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QTimer
from PyQt6.QtGui import (
    QDesktopServices, QColor, QPalette, QIcon, QKeySequence, QShortcut, QPixmap, QPainter
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSlider, QSpinBox, QTextBrowser, QToolButton, QVBoxLayout,
    QWidget, QStyleFactory, QSizePolicy, QFrame, QScrollArea, QStyle, QTabWidget,
    QGroupBox
)

from storage import get_appdata_dir, load_targets_from_file


__all__ = ["SettingsDialog"]

API_HELP_URL = "https://www.torn.com/preferences.php#tab=api"


# ------------------------- helpers -------------------------

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


def icon(name: str) -> QIcon:
    p = _icon_path(name)
    return QIcon(p) if p else QIcon()  # silent fallback


def _looks_like_api_key(s: str) -> bool:
    s = (s or "").strip()
    return s.isalnum() and 16 <= len(s) <= 64


class SettingsSection(QGroupBox):
    """Compact group-box container with a form body."""
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(title, parent)
        self.setObjectName("settingsGroup")
        self.setFlat(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 14, 10, 10)  # compact
        outer.setSpacing(6)

        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("groupSubtitle")
            sub.setWordWrap(True)
            outer.addWidget(sub)

        body = QWidget(objectName="groupBody")
        form = QFormLayout(body)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)              # compact rows
        form.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)

        self._form = form

    @property
    def form(self) -> QFormLayout:
        return self._form


# ------------------------- dialog -------------------------

class SettingsDialog(QDialog):
    """
    Compact dark-grey, tabbed settings dialog using QGroupBox sections.
    Preserves all logic (signals, payload, retries/backoff, etc.).
    """
    saved = pyqtSignal(dict)
    apiTestFinished = pyqtSignal(str, str, str)

    def __init__(self, settings: Dict, parent=None):
        super().__init__(parent)

        # Fusion + tuned dark palette
        self._apply_fusion_dark()
        self.setObjectName("TTSettingsRoot")
        self.setWindowTitle("Settings")
        self.resize(820, 540)
        self.setMinimumSize(720, 480)

        self._settings = dict(settings or {})
        self._dirty = False
        self._api_test_thread: Optional[threading.Thread] = None
        self._api_test_stop = threading.Event()

        # Isolate from any global QSS, then apply scoped dark style
        self.setStyleSheet("")
        self._apply_scoped_qss()

        # Root layout
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Header (compact)
        header = QWidget(objectName="header")
        hl = QHBoxLayout(header); hl.setContentsMargins(10, 8, 10, 8); hl.setSpacing(8)

        hico = QLabel()
        pm = QPixmap(16, 16); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); p.setPen(QColor("#9cc7ff")); p.drawEllipse(3, 3, 10, 10); p.end()
        hico.setPixmap(icon("settings").pixmap(16, 16) if not icon("settings").isNull() else pm)

        title_wrap = QVBoxLayout(); title_wrap.setContentsMargins(0, 0, 0, 0); title_wrap.setSpacing(1)
        hTitle = QLabel("<span class='h1'>Settings</span>")
        hSub = QLabel("Configure API access, cache, performance, and backoff")
        hSub.setProperty("muted", True)
        title_wrap.addWidget(hTitle); title_wrap.addWidget(hSub)

        self.lblSummary = QLabel("")
        self.lblSummary.setObjectName("headerSummary")

        hl.addWidget(hico, 0, Qt.AlignmentFlag.AlignTop)
        hl.addLayout(title_wrap, 1)
        hl.addWidget(self.lblSummary, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(header)

        # Tabs
        self.tabs = QTabWidget(objectName="tabs")
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)

        # Footer (compact)
        footer = QWidget(objectName="footer")
        fl = QHBoxLayout(footer); fl.setContentsMargins(6, 6, 6, 6); fl.setSpacing(6)

        self.btn_defaults = QPushButton("Restore Defaults"); self.btn_defaults.setProperty("role", "ghost")
        fl.addWidget(self.btn_defaults); fl.addStretch(1)

        self.lbl_footer_status = QLabel("")
        self.lbl_footer_status.setObjectName("footerStatus")
        fl.addWidget(self.lbl_footer_status)

        self.btn_ok = QPushButton("OK"); self.btn_cancel = QPushButton("Cancel"); self.btn_apply = QPushButton("Apply")
        self.btn_ok.setProperty("role", "primary"); self.btn_apply.setProperty("role", "primary"); self.btn_apply.setEnabled(False)
        for b in (self.btn_ok, self.btn_cancel, self.btn_apply):
            b.setMinimumWidth(88)
        fl.addWidget(self.btn_ok); fl.addWidget(self.btn_cancel); fl.addWidget(self.btn_apply)
        root.addWidget(footer)

        # Build tabs & wire actions
        self._build_tabs()
        self.btn_defaults.clicked.connect(self._reset_values)
        self.btn_cancel.clicked.connect(self._maybe_discard_and_close)
        self.btn_ok.clicked.connect(self._save_and_close)
        self.btn_apply.clicked.connect(self._apply)

        self._footer_timer = QTimer(self)
        self._footer_timer.setSingleShot(True)
        self._footer_timer.timeout.connect(self._clear_footer_status)
        self.apiTestFinished.connect(self._handle_api_test_result)

        # Shortcuts
        QShortcut(QKeySequence.StandardKey.Save, self, self._apply)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self._apply)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._maybe_discard_and_close)

    # --------------------- look & feel ---------------------

    def _apply_fusion_dark(self):
        self.setStyle(QStyleFactory.create("Fusion"))
        pal = QPalette()

        window = QColor("#2b2b2b")
        panel  = QColor("#303030")
        base   = QColor("#353535")
        text   = QColor("#f1f1f1")
        muted  = QColor("#c7c7c7")
        blue   = QColor("#2a82da")
        hltext = QColor("#0d1115")

        pal.setColor(QPalette.ColorRole.Window, window)
        pal.setColor(QPalette.ColorRole.Base, base)
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#3a3a3a"))
        pal.setColor(QPalette.ColorRole.Button, panel)
        pal.setColor(QPalette.ColorRole.ButtonText, text)
        pal.setColor(QPalette.ColorRole.Text, text)
        pal.setColor(QPalette.ColorRole.WindowText, text)
        pal.setColor(QPalette.ColorRole.BrightText, text)
        pal.setColor(QPalette.ColorRole.ToolTipBase, panel)
        pal.setColor(QPalette.ColorRole.ToolTipText, text)
        pal.setColor(QPalette.ColorRole.Highlight, blue)
        pal.setColor(QPalette.ColorRole.HighlightedText, hltext)

        pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, muted)
        pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, muted)

        self.setPalette(pal)

    def _apply_scoped_qss(self):
        self.setStyleSheet(
            """
#TTSettingsRoot { background: #2b2b2b; color: #f1f1f1; font-size: 12px; }
#TTSettingsRoot * { background: transparent; }

/* Header */
#TTSettingsRoot QWidget#header {
    background: #363636;
    border: 1px solid #4a4a4a;
    border-radius: 6px;
}
#TTSettingsRoot .h1 { font-size: 14px; font-weight: 600; }
#TTSettingsRoot [muted="true"] { color: #c7c7c7; }
#TTSettingsRoot QLabel#headerSummary {
    padding: 4px 8px;
    border-radius: 10px;
    background: #2a2f38;
    border: 1px solid #3e536e;
    color: #cfe3ff; font-weight: 600; }

/* Tabs */
#TTSettingsRoot QTabWidget#tabs::pane {
    background: #2b2b2b;
    border: 1px solid #4a4a4a;
    border-radius: 6px; top: -1px; }
#TTSettingsRoot QTabBar::tab {
    background: #3a3a3a;
    border: 1px solid #4a4a4a;
    border-bottom: none;
    padding: 4px 10px; margin-right: 2px;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
    color: #e0e0e0; }
#TTSettingsRoot QTabBar::tab:selected { background: #2b2b2b; color: #ffffff; }
#TTSettingsRoot QTabBar::tab:hover { background: #414141; }

/* Group boxes (compact) */
#TTSettingsRoot QGroupBox#settingsGroup {
    background: #303030;
    border: 1px solid #4a4a4a;
    border-radius: 6px;
    margin-top: 14px;               /* space for the title */
    padding-top: 6px;               /* inner padding below title */
}
#TTSettingsRoot QGroupBox#settingsGroup::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    padding: 0 4px;
    color: #f1f1f1;
    background: #2b2b2b;
    border-radius: 3px;
    margin-left: 6px;               /* breathing room from border */
}
#TTSettingsRoot QLabel#groupSubtitle {
    color: #c7c7c7; font-size: 11px; }

/* Inputs (compact) */
#TTSettingsRoot QLineEdit,
#TTSettingsRoot QSpinBox,
#TTSettingsRoot QComboBox,
#TTSettingsRoot QTextBrowser,
#TTSettingsRoot QTextEdit,
#TTSettingsRoot QPlainTextEdit {
    background: #353535;
    border: 1px solid #4a4a4a;
    border-radius: 4px;
    padding: 4px 7px;
    min-height: 22px;
}
#TTSettingsRoot QLineEdit:focus,
#TTSettingsRoot QSpinBox:focus,
#TTSettingsRoot QComboBox:focus,
#TTSettingsRoot QTextBrowser:focus,
#TTSettingsRoot QTextEdit:focus,
#TTSettingsRoot QPlainTextEdit:focus {
    border-color: #2a82da;
}

/* Buttons (compact) */
#TTSettingsRoot QToolButton,
#TTSettingsRoot QPushButton {
    background: #353535; border: 1px solid #4a4a4a; border-radius: 4px;
    padding: 5px 9px; color: #f1f1f1; }
#TTSettingsRoot QToolButton:hover,
#TTSettingsRoot QPushButton:hover { background: #3d3d3d; }
#TTSettingsRoot QToolButton:pressed,
#TTSettingsRoot QPushButton:pressed { background: #444444; }

#TTSettingsRoot QPushButton[role="primary"] {
    background: #2a82da; border-color: #2a82da; color: #ffffff; font-weight: 600; }
#TTSettingsRoot QPushButton[role="primary"]:hover { background: #3a8fe2; }
#TTSettingsRoot QPushButton[role="danger"] {
    background: #5c2a2a; border: 1px solid #8b3a3a; }
#TTSettingsRoot QPushButton[role="ghost"] {
    background: transparent; border: 1px solid #4a4a4a; }

/* Sliders */
#TTSettingsRoot QSlider::groove:horizontal { height: 5px; border-radius: 3px; background: #4a4a4a; }
#TTSettingsRoot QSlider::handle:horizontal {
    width: 12px; height: 12px; margin: -4px 0;
    border-radius: 6px; background: #2a82da; border: 1px solid #1b60a1; }

/* Checkboxes */
#TTSettingsRoot QCheckBox::indicator {
    width: 14px; height: 14px; border-radius: 3px;
    border: 1px solid #4a4a4a; background: #353535; }
#TTSettingsRoot QCheckBox::indicator:checked { background: #2a82da; border-color: #2a82da; }

/* Footer */
#TTSettingsRoot QWidget#footer { border-top: 1px solid #4a4a4a; }
#TTSettingsRoot QLabel#footerStatus {
    color: #c7d6ec;
    padding-right: 12px;
    font-size: 11px;
}

/* Help text + mono chip */
#TTSettingsRoot QLabel.helpSmall { color: #d0d0d0; }
#TTSettingsRoot .mono {
    background: #353535; padding: 1px 3px; border-radius: 3px;
    border: 1px solid #4a4a4a; font-family: Consolas, 'Courier New', monospace; }

/* Scroll area */
#TTSettingsRoot QScrollArea { background: transparent; border: none; }
#TTSettingsRoot QScrollArea QWidget#qt_scrollarea_viewport { background: transparent; }

/* Scrollbars */
#TTSettingsRoot QScrollBar:vertical { background: transparent; width: 11px; margin: 3px 2px; }
#TTSettingsRoot QScrollBar::handle:vertical { background: #4a4a4a; border-radius: 6px; }
#TTSettingsRoot QScrollBar::handle:vertical:hover { background: #5a7aa5; }
            """
        )

    # --------------------- helpers ---------------------

    def _row(self, *widgets: QWidget) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        for wd in widgets: h.addWidget(wd)
        return w

    def _inline_button(
        self,
        text: str,
        *,
        icon: Optional[QIcon] = None,
        checkable: bool = False,
        tooltip: str = "",
        role: Optional[str] = None,
    ) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setCheckable(checkable)
        if icon and not icon.isNull():
            btn.setIcon(icon)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setAutoRaise(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            btn.setToolTip(tooltip)
        if role:
            btn.setProperty("role", role)
        return btn

    def _add_section(self, parent_layout: QVBoxLayout, title: str, subtitle: str = "") -> QFormLayout:
        section = SettingsSection(title, subtitle, self)
        parent_layout.addWidget(section)
        return section.form

    def _wrap_scroll(self, widget: QWidget) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidget(widget)
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return sc

    def _spacer(self) -> QWidget:
        s = QWidget(); s.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); return s

    def _bind_pair(self, spin: QSpinBox, slider: QSlider):
        spin.valueChanged.connect(slider.setValue)
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(self._mark_dirty)
        slider.valueChanged.connect(self._mark_dirty)

        def maybe_update_pacing():
            if hasattr(self, "sb_rate_cap") and spin in (
                self.sb_rate_cap, self.sb_min_interval, self.sb_auto, self.sb_conc
            ):
                self._update_effective_pacing()

        spin.valueChanged.connect(maybe_update_pacing)
        slider.valueChanged.connect(maybe_update_pacing)

    # --------------------- tabs & pages ---------------------

    def _build_tabs(self):
        style = self.style() or QApplication.style()

        # -------- General
        pg_general = QWidget()
        gl = QVBoxLayout(pg_general); gl.setContentsMargins(8, 8, 8, 8); gl.setSpacing(8)

        api_form = self._add_section(
            gl, "Torn API",
            "Paste your Limited Access key so Target Tracker can talk to Torn on your behalf."
        )
        self.ed_api = QLineEdit(self._settings.get("api_key", ""))
        self.ed_api.setEchoMode(QLineEdit.EchoMode.Password)
        self.lbl_api_status = QLabel(""); self.lbl_api_status.setProperty("class", "helpSmall")

        show_icon  = style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView) if style else QIcon()
        paste_icon = style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)     if style else QIcon()
        open_icon  = style.standardIcon(QStyle.StandardPixmap.SP_DirLinkIcon)         if style else QIcon()
        test_icon  = style.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)   if style else QIcon()

        btn_show  = self._inline_button("Show", icon=show_icon, checkable=True, tooltip="Show or hide the API key")
        btn_show.toggled.connect(lambda ch: self.ed_api.setEchoMode(QLineEdit.EchoMode.Normal if ch else QLineEdit.EchoMode.Password))
        btn_paste = self._inline_button("Paste", icon=paste_icon, tooltip="Paste from clipboard"); btn_paste.clicked.connect(self._paste_api)
        btn_open  = self._inline_button("Open site", icon=open_icon,  tooltip="Open Torn API preferences"); btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(API_HELP_URL)))
        self.btn_test = self._inline_button("Test key", icon=test_icon, tooltip="Ping Torn once to confirm the key", role="primary")
        self.btn_test.clicked.connect(self._test_key)

        api_form.addRow("API Key:", self._row(self.ed_api, btn_show, btn_paste, btn_open, self.btn_test))
        api_form.addRow("", self.lbl_api_status)

        tgt_form = self._add_section(gl, "Targets & Window", "Choose where target.json lives and how the main window opens by default.")
        self.ed_targets = QLineEdit(self._settings.get("targets_file", "target.json"))
        self.lbl_targets_hint = QLabel(""); self.lbl_targets_hint.setProperty("class", "helpSmall")
        browse_icon = style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton) if style else QIcon()
        create_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder) if style else QIcon()
        btn_browse = self._inline_button("Browse…", icon=browse_icon, tooltip="Pick a targets JSON file"); btn_browse.clicked.connect(self._pick_targets)
        btn_create = self._inline_button("Create", icon=create_icon, tooltip="Create a fresh targets file"); btn_create.clicked.connect(self._create_targets_file)
        self.chk_start_max = QCheckBox("Start maximized"); self.chk_start_max.setChecked(bool(self._settings.get("start_maximized", True)))

        tgt_form.addRow("Targets file:", self._row(self.ed_targets, btn_browse, btn_create))
        tgt_form.addRow("", self.lbl_targets_hint)
        tgt_form.addRow("", self.chk_start_max)

        gl.addStretch(1)
        self.tabs.addTab(self._wrap_scroll(pg_general), "General")

        # -------- Data & Cache
        pg_data = QWidget()
        dl = QVBoxLayout(pg_data); dl.setContentsMargins(8, 8, 8, 8); dl.setSpacing(8)

        app_form = self._add_section(dl, "AppData", "Quick access to the folder where settings, cache and ignore lists live.")
        self.ed_appdata = QLineEdit(get_appdata_dir()); self.ed_appdata.setReadOnly(True)
        open_icon2 = style.standardIcon(QStyle.StandardPixmap.SP_DirLinkIcon) if style else QIcon()
        btn_open_folder = self._inline_button("Open folder", icon=open_icon2, tooltip="Open AppData folder")
        btn_open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(get_appdata_dir())))
        app_form.addRow("", self._row(self.ed_appdata, btn_open_folder))

        cache_form = self._add_section(dl, "Cache", "Control how often we persist API responses locally for faster restarts.")
        self.chk_load_cache = QCheckBox("Load cache at startup")
        self.chk_load_cache.setChecked(bool(self._settings.get("load_cache_at_start", True)))
        self.sb_save_every = QSpinBox(); self.sb_save_every.setRange(5, 200); self.sb_save_every.setValue(int(self._settings.get("save_cache_every", 20))); self.sb_save_every.setSuffix(" updates")
        self.lbl_cache_info = QLabel(""); self.lbl_cache_info.setProperty("class", "helpSmall")
        btn_clear_cache = QPushButton("Clear cache…"); btn_clear_cache.setProperty("role", "danger"); btn_clear_cache.clicked.connect(self._clear_cache)

        cache_form.addRow("", self.chk_load_cache)
        cache_form.addRow("Save cache every:", self.sb_save_every)
        cache_form.addRow("", self.lbl_cache_info)
        cache_form.addRow("", self._row(self._spacer(), btn_clear_cache))
        dl.addStretch(1)
        self.tabs.addTab(self._wrap_scroll(pg_data), "Data & Cache")

        # -------- Performance
        pg_perf = QWidget()
        pl = QVBoxLayout(pg_perf); pl.setContentsMargins(8, 8, 8, 8); pl.setSpacing(8)

        perf_grid = QWidget(); grid = QGridLayout(perf_grid)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10); grid.setVerticalSpacing(8)

        self.sb_conc = QSpinBox(); self.sb_conc.setRange(1, 16); self.sb_conc.setValue(int(self._settings.get("concurrency", 4)))
        self.sl_conc = QSlider(Qt.Orientation.Horizontal); self.sl_conc.setRange(1, 16); self.sl_conc.setValue(self.sb_conc.value())

        self.sb_auto = QSpinBox(); self.sb_auto.setRange(0, 3600); self.sb_auto.setSuffix(" sec"); self.sb_auto.setValue(int(self._settings.get("auto_refresh_sec", 0)))
        self.sl_auto = QSlider(Qt.Orientation.Horizontal); self.sl_auto.setRange(0, 3600); self.sl_auto.setValue(self.sb_auto.value())

        self.sb_rate_cap = QSpinBox(); self.sb_rate_cap.setRange(10, 200); self.sb_rate_cap.setValue(int(self._settings.get("rate_max_per_min", 100)))
        self.sl_rate_cap = QSlider(Qt.Orientation.Horizontal); self.sl_rate_cap.setRange(10, 200); self.sl_rate_cap.setValue(self.sb_rate_cap.value())

        self.sb_min_interval = QSpinBox(); self.sb_min_interval.setRange(0, 5000); self.sb_min_interval.setSuffix(" ms")
        self.sb_min_interval.setValue(int(self._settings.get("min_interval_ms", self._settings.get("req_delay_ms", 620))))
        self.sl_min_interval = QSlider(Qt.Orientation.Horizontal); self.sl_min_interval.setRange(0, 5000); self.sl_min_interval.setValue(self.sb_min_interval.value())

        self._bind_pair(self.sb_conc, self.sl_conc)
        self._bind_pair(self.sb_auto, self.sl_auto)
        self._bind_pair(self.sb_rate_cap, self.sl_rate_cap)
        self._bind_pair(self.sb_min_interval, self.sl_min_interval)

        r = 0
        grid.addWidget(QLabel("Concurrency:"), r, 0); grid.addWidget(self.sb_conc, r, 1); grid.addWidget(self.sl_conc, r, 2); r += 1
        grid.addWidget(QLabel("Auto refresh:"), r, 0); grid.addWidget(self.sb_auto, r, 1); grid.addWidget(self.sl_auto, r, 2); r += 1
        grid.addWidget(QLabel("Rate cap (per minute):"), r, 0); grid.addWidget(self.sb_rate_cap, r, 1); grid.addWidget(self.sl_rate_cap, r, 2); r += 1
        grid.addWidget(QLabel("Min interval between calls:"), r, 0); grid.addWidget(self.sb_min_interval, r, 1); grid.addWidget(self.sl_min_interval, r, 2)

        self.lbl_effective = QLabel(""); self.lbl_effective.setProperty("class", "helpSmall")
        self.lbl_estimate = QLabel(""); self.lbl_estimate.setProperty("class", "helpSmall")
        btn_recommended = QPushButton("Use Torn-safe preset"); btn_recommended.setProperty("role", "primary"); btn_recommended.clicked.connect(self._apply_recommended)

        perf_form = self._add_section(pl, "Performance", "Tune concurrency and pacing to respect Torn limits.")
        perf_form.addRow("", perf_grid)
        perf_form.addRow("", self.lbl_effective)
        perf_form.addRow("", self.lbl_estimate)
        perf_form.addRow("", self._row(self._spacer(), btn_recommended))
        pl.addStretch(1)
        self.tabs.addTab(self._wrap_scroll(pg_perf), "Performance")

        # -------- Retries & Backoff
        pg_back = QWidget()
        bl = QVBoxLayout(pg_back); bl.setContentsMargins(8, 8, 8, 8); bl.setSpacing(8)

        form = self._add_section(bl, "Retries & Backoff", "Control how we recover from 429s and transient Torn errors.")
        self.sb_max_retries = QSpinBox(); self.sb_max_retries.setRange(1, 12); self.sb_max_retries.setValue(int(self._settings.get("max_retries", 8)))
        self.sb_backoff_base = QSpinBox(); self.sb_backoff_base.setRange(0, 3000); self.sb_backoff_base.setSuffix(" ms"); self.sb_backoff_base.setValue(int(self._settings.get("backoff_base_ms", 600)))
        self.sb_backoff_cap = QSpinBox(); self.sb_backoff_cap.setRange(1, 60); self.sb_backoff_cap.setSuffix(" s"); self.sb_backoff_cap.setValue(int(self._settings.get("backoff_cap_s", 8)))
        self.chk_retry_after = QCheckBox("Honor Retry-After header"); self.chk_retry_after.setChecked(bool(self._settings.get("respect_retry_after", True)))

        form.addRow("Max retries:", self.sb_max_retries)
        form.addRow("Backoff base:", self.sb_backoff_base)
        form.addRow("Backoff cap:", self.sb_backoff_cap)
        form.addRow("", self.chk_retry_after)

        self.lbl_backoff_hint = QLabel(""); self.lbl_backoff_hint.setProperty("class", "helpSmall")
        self.lbl_backoff_table = QLabel(""); self.lbl_backoff_table.setTextFormat(Qt.TextFormat.RichText)
        form.addRow("", self.lbl_backoff_hint)
        form.addRow("", self.lbl_backoff_table)
        bl.addStretch(1)
        self.tabs.addTab(self._wrap_scroll(pg_back), "Retries & Backoff")

        # -------- Help
        pg_help = QWidget()
        hl = QVBoxLayout(pg_help); hl.setContentsMargins(8, 8, 8, 8); hl.setSpacing(8)
        help_form = self._add_section(hl, "Help", "Reference links, release notes and support.")
        help_box = QTextBrowser(); help_box.setOpenExternalLinks(True); help_box.setHtml(self._help_html())
        help_form.addRow("", help_box)
        hl.addStretch(1)
        self.tabs.addTab(self._wrap_scroll(pg_help), "Help")

        # Init dynamic bits
        self._validate_api_key()
        self._update_targets_hint()
        self._update_cache_info()
        self._update_effective_pacing()
        self._update_backoff_preview()
        self._connect_dirty_signals()
        self._refresh_header_summary()

    # --------------------- logic (unchanged behavior) ---------------------

    def _connect_dirty_signals(self):
        for w in (
            self.ed_api, self.ed_targets, self.chk_start_max,
            self.chk_load_cache, self.sb_save_every,
            self.sb_conc, self.sb_auto, self.sb_rate_cap, self.sb_min_interval,
            self.sb_max_retries, self.sb_backoff_base, self.sb_backoff_cap, self.chk_retry_after,
        ):
            if isinstance(w, QLineEdit):
                w.textChanged.connect(self._mark_dirty)
            elif isinstance(w, QSpinBox):
                w.valueChanged.connect(self._mark_dirty)
            elif isinstance(w, QCheckBox):
                w.toggled.connect(self._mark_dirty)

        self.ed_api.textChanged.connect(self._validate_api_key)
        self.ed_targets.textChanged.connect(self._update_targets_hint)
        for sb in (self.sb_backoff_base, self.sb_backoff_cap, self.sb_max_retries):
            sb.valueChanged.connect(self._update_backoff_preview)

    def _mark_dirty(self):
        self._dirty = True
        self.btn_apply.setEnabled(True)
        self._refresh_header_summary()
        self._set_footer_status("")

    def _refresh_header_summary(self):
        target_path = (self.ed_targets.text().strip() if hasattr(self, "ed_targets") else "") or self._settings.get("targets_file", "target.json")
        target_name = os.path.basename(target_path) or target_path or "target.json"
        api_ok = _looks_like_api_key(self.ed_api.text().strip()) if hasattr(self, "ed_api") else False
        api_state = "API key ready" if api_ok else "API key missing"
        auto_val = int(self.sb_auto.value()) if hasattr(self, "sb_auto") else int(self._settings.get("auto_refresh_sec", 0))
        auto_state = f"Auto refresh {auto_val}s" if auto_val else "Auto refresh off"
        self.lblSummary.setText(f"{api_state}  •  Targets: {target_name}  •  {auto_state}")

    def _set_footer_status(self, text: str, kind: str = "info", timeout_ms: int = 3000):
        if not hasattr(self, "lbl_footer_status"):
            return
        if not text:
            self._footer_timer.stop()
            self._clear_footer_status()
            return
        palette = {
            "info": "#c7d6ec",
            "success": "#8ee6b3",
            "warn": "#ffd27d",
            "error": "#ff9b8e",
        }
        color = palette.get(kind, palette["info"])
        self.lbl_footer_status.setStyleSheet(f"color: {color};")
        self.lbl_footer_status.setText(text)
        self._footer_timer.stop()
        if text and timeout_ms:
            self._footer_timer.start(timeout_ms)

    def _clear_footer_status(self):
        if hasattr(self, "lbl_footer_status"):
            self._footer_timer.stop()
            self.lbl_footer_status.setText("")
            self.lbl_footer_status.setStyleSheet("")

    def _paste_api(self):
        try:
            clip = QApplication.clipboard().text().strip()
            if clip:
                self.ed_api.setText(clip)
        except Exception:
            pass

    def _validate_api_key(self):
        key = self.ed_api.text().strip()
        if not key:
            self.lbl_api_status.setText("<span class='helpSmall'>Paste a <b>Limited Access</b> key from Torn.</span>")
            return
        self.lbl_api_status.setText(
            "<span style='color:#8ee6b3'>Looks good. (Format check passed)</span>"
            if _looks_like_api_key(key) else
            "<span style='color:#ff9b8e'>This doesn't look like a Torn API key.</span>"
        )

    def _test_key(self):
        """Robust key test mirroring the onboarding dialog behaviour."""
        key = self.ed_api.text().strip()
        if not key:
            QMessageBox.information(self, "Test Key", "Please paste your API key first.")
            self.lbl_api_status.setText("<span style='color:#ff9b8e'>No key entered.</span>")
            self._set_footer_status("Paste an API key to test.", "warn", timeout_ms=4000)
            return

        honor_retry_after = bool(self.chk_retry_after.isChecked()) if hasattr(self, "chk_retry_after") else True

        if hasattr(self, "btn_test"):
            self.btn_test.setEnabled(False)
            self.btn_test.setText("Testing…")

        self.lbl_api_status.setText("<span class='helpSmall'>Testing against Torn…</span>")

        # cancel any in-flight test
        prev_thread = getattr(self, "_api_test_thread", None)
        if prev_thread and prev_thread.is_alive():
            self._api_test_stop.set()
            try:
                prev_thread.join(timeout=0.2)
            except Exception:
                pass
        self._api_test_stop = threading.Event()

        result = {"status": "", "kind": "", "text": ""}

        def worker(stop_event: threading.Event):
            import time

            url = f"https://api.torn.com/user/?selections=basic&key={key}"
            headers = {"User-Agent": "TargetTracker/1.0 (+pyqt)", "Accept": "application/json"}
            timeout = 8.0
            max_attempts = 3

            try:
                for attempt in range(1, max_attempts + 1):
                    if stop_event.is_set():
                        return
                    req = urllib.request.Request(url, headers=headers)
                    status = 0
                    body = ""
                    headers_obj = None
                    try:
                        with urllib.request.urlopen(req, timeout=timeout) as resp:
                            status = resp.getcode() or 0
                            raw = resp.read() or b""
                            headers_obj = resp.headers
                    except urllib.error.HTTPError as err:
                        status = err.code or 0
                        raw = err.read() or b""
                        headers_obj = err.headers
                    except socket.timeout:
                        result.update(
                            status="<span style='color:#ff9b8e'>Timed out. Network slow?</span>",
                            kind="warn",
                            text="Request timed out. Network slow?",
                        )
                        if attempt < max_attempts:
                            time.sleep(0.6 * attempt)
                            continue
                        break
                    except Exception as exc:
                        result.update(
                            status=f"<span style='color:#ff9b8e'>Request failed: {exc}</span>",
                            kind="error",
                            text=f"Request failed: {exc}",
                        )
                        break
                    else:
                        charset = None
                        if headers_obj is not None:
                            try:
                                charset = headers_obj.get_content_charset()
                            except Exception:
                                charset = None
                        charset = charset or "utf-8"
                        body = raw.decode(charset, errors="replace") if raw else ""

                    if status == 429:
                        wait_s = 0
                        try:
                            if headers_obj is not None:
                                ra = headers_obj.get("Retry-After")
                                wait_s = int(float(ra)) if ra else 0
                        except Exception:
                            wait_s = 0
                        if honor_retry_after and wait_s and attempt < max_attempts:
                            time.sleep(min(wait_s, 5))
                            continue
                        result.update(
                            status="<span style='color:#ff9b8e'>Rate limited (429). Try again shortly.</span>",
                            kind="warn",
                            text="Rate limited (429). Try again shortly.",
                        )
                        break

                    if status >= 400:
                        result.update(
                            status=f"<span style='color:#ff9b8e'>HTTP {status} while checking key.</span>",
                            kind="error",
                            text=f"HTTP {status} while checking key.",
                        )
                        if status >= 500 and attempt < max_attempts:
                            time.sleep(0.6 * attempt)
                            continue
                        break

                    try:
                        data = json.loads(body or "{}")
                    except Exception:
                        result.update(
                            status="<span style='color:#ff9b8e'>Non-JSON response from Torn.</span>",
                            kind="error",
                            text="Non-JSON response from Torn.",
                        )
                        break

                    if isinstance(data, dict) and "error" in data:
                        err_info = data.get("error") or {}
                        code = err_info.get("code")
                        desc = err_info.get("error") or "Unknown error"
                        hint = " (Incorrect/expired key?)" if code in (1, 2) else (" (Temporarily rate-limited.)" if code in (5, 9) else "")
                        result.update(
                            status=f"<span style='color:#ff9b8e'>API error {code}: {desc}{hint}</span>",
                            kind="error",
                            text=f"API error {code}: {desc}{hint}",
                        )
                        break

                    name = data.get("name") or "OK"
                    pid = data.get("player_id")
                    who = f"{name} (ID: {pid})" if pid else name
                    result.update(
                        status=f"<span style='color:#8ee6b3'>Valid. Hello, {who}.</span>",
                        kind="info",
                        text=f"Success! Authenticated as: {who}.",
                    )
                    break
            finally:
                if not stop_event.is_set():
                    status_html = result["status"] or "<span style='color:#ff9b8e'>Test ended unexpectedly.</span>"
                    kind = result["kind"] or ""
                    text_msg = result["text"] or ""
                    self.apiTestFinished.emit(status_html, kind, text_msg)
                self._api_test_thread = None

        thread = threading.Thread(target=worker, args=(self._api_test_stop,), daemon=True)
        self._api_test_thread = thread
        thread.start()

    def _handle_api_test_result(self, status_html: str, kind: str, message: str):
        self.lbl_api_status.setText(status_html)
        if hasattr(self, "btn_test"):
            self.btn_test.setEnabled(True)
            self.btn_test.setText("Test key")
        if kind == "info" and message:
            QMessageBox.information(self, "Test Key", message)
            self._set_footer_status("API key validated.", "success")
        elif kind in ("warn", "error") and message:
            QMessageBox.warning(self, "Test Key", message)
            self._set_footer_status(message, "warn" if kind == "warn" else "error", timeout_ms=5000)
        else:
            self._set_footer_status("", timeout_ms=0)

    def closeEvent(self, event):
        try:
            if self._api_test_thread and self._api_test_thread.is_alive():
                self._api_test_stop.set()
                self._api_test_thread.join(timeout=0.2)
        except Exception:
            pass
        super().closeEvent(event)

    def _pick_targets(self):
        p, _ = QFileDialog.getOpenFileName(self, "Pick targets JSON", "", "JSON (*.json)")
        if p:
            self.ed_targets.setText(p)
            self._update_targets_hint()

    def _create_targets_file(self):
        path = self.ed_targets.text().strip() or "target.json"
        try:
            if not os.path.isabs(path):
                path = os.path.join(get_appdata_dir(), os.path.basename(path))
                self.ed_targets.setText(path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                payload = {"app": "Target Tracker", "version": "1.0.0", "targets": []}
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
            self._update_targets_hint()
        except Exception as e:
            QMessageBox.warning(self, "Create file", f"Could not create file:\n{e}")

    def _targets_stats(self) -> Tuple[int, str]:
        p = self.ed_targets.text().strip()
        if not p:
            return 0, "No path"
        ids = load_targets_from_file(p)
        return len(ids), os.path.abspath(p)

    def _update_targets_hint(self):
        cnt, abspath = self._targets_stats()
        if abspath == "No path" or not os.path.exists(abspath):
            self.lbl_targets_hint.setText("<span style='color:#ff9b8e'>File not found. Click <b>Create</b> or choose an existing JSON.</span>")
        else:
            self.lbl_targets_hint.setText(f"Path: <span class='mono'>{abspath}</span> | <b>{cnt}</b> target(s).")

    def _cache_info(self) -> Optional[str]:
        p = os.path.join(get_appdata_dir(), "cache_targets.json")
        if not os.path.exists(p):
            return None
        try:
            sz = os.path.getsize(p) / 1024.0
            mtime = os.path.getmtime(p)
            from time import localtime, strftime
            ts = strftime("%Y-%m-%d %H:%M", localtime(mtime))
            return f"Cache file: <span class='mono'>{p}</span> - {sz:.1f} KB - modified {ts}"
        except Exception:
            return f"Cache file: <span class='mono'>{p}</span>"

    def _update_cache_info(self):
        info = self._cache_info()
        self.lbl_cache_info.setText(info or "<span class='helpSmall'>No cache yet.</span>")

    def _clear_cache(self):
        p = os.path.join(get_appdata_dir(), "cache_targets.json")
        if not os.path.exists(p):
            QMessageBox.information(self, "Clear cache", "No cache file found.")
            return
        m = QMessageBox(self); m.setIcon(QMessageBox.Icon.Warning)
        m.setWindowTitle("Clear cache?"); m.setText("Delete the local cache file? It will be rebuilt on next refresh.")
        m.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if m.exec() == QMessageBox.StandardButton.Yes:
            try:
                os.remove(p); self._update_cache_info()
            except Exception as e:
                QMessageBox.warning(self, "Clear cache", f"Failed to delete:\n{e}")

    def _effective_per_min(self) -> int:
        cap = max(1, int(self.sb_rate_cap.value()))
        min_ms = max(0, int(self.sb_min_interval.value()))
        by_interval = (60000 // min_ms) if min_ms > 0 else cap
        return max(1, min(cap, by_interval))

    def _update_effective_pacing(self):
        eff = self._effective_per_min()
        rps = eff / 60.0
        self.lbl_effective.setText(f"Effective pacing: <b>~{eff}/min</b>  <span class='helpSmall'>(~{rps:.2f}/s)</span>")
        cnt, _ = self._targets_stats()
        if cnt <= 0 or rps <= 0:
            self.lbl_estimate.setText("<span class='helpSmall'>No targets detected.</span>")
            return
        secs = int(round(cnt / rps))
        m, s = divmod(secs, 60); h, m = divmod(m, 60)
        eta = f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")
        self.lbl_estimate.setText(f"Estimated time for <b>{cnt}</b> target(s): <b>{eta}</b> <span class='helpSmall'>(ignores retries)</span>")

    def _apply_recommended(self):
        self.sb_rate_cap.setValue(100)
        self.sb_min_interval.setValue(620)
        self._update_effective_pacing()

    def _backoff_series(self, base_ms: int, cap_s: int, retries: int) -> list[int]:
        out = []
        for attempt in range(1, retries + 1):
            v = base_ms * (2 ** (attempt - 1)) / 1000.0
            out.append(int(round(min(v, cap_s))))
        return out

    def _update_backoff_preview(self):
        base = int(self.sb_backoff_base.value())
        cap = int(self.sb_backoff_cap.value())
        retries = int(self.sb_max_retries.value())
        self.lbl_backoff_hint.setText(
            f"Exponential backoff with jitter. Base: <b>{base}ms</b> - Cap: <b>{cap}s</b> - Retries: <b>{retries}</b>"
        )
        series = self._backoff_series(base, cap, retries)
        cells = "".join(
            f"<span style='padding:2px 6px;border:1px solid #4a4a4a;border-radius:4px;margin-right:4px'>#{i+1}: {s}s</span>"
            for i, s in enumerate(series)
        )
        self.lbl_backoff_table.setText(f"<div style='margin-top:4px'>{cells}</div>")

    def _help_html(self) -> str:
        return (
            "<style>a{color:#2a82da;text-decoration:none}a:hover{text-decoration:underline}"
            "ul{margin-left:1.1em}code{background:#353535;padding:1px 3px;border-radius:3px;border:1px solid #4a4a4a}</style>"
            "<h3>Tips</h3>"
            "<ul>"
            "<li><b>API Key:</b> Use a <i>Limited Access</i> key. Stored locally only.</li>"
            "<li><b>Rate Limits:</b> Keep &le; 100/min or add a minimum interval to pace.</li>"
            "<li><b>Recommended:</b> 100/min with 620ms interval is safe for Torn.</li>"
            "<li><b>Backoff:</b> Exponential with jitter; enable <i>Retry-After</i>.</li>"
            "</ul>"
            f"<p>Manage your key: <a href='{API_HELP_URL}'>{API_HELP_URL}</a></p>"
        )

    # --------------------- save/apply ---------------------

    def _payload(self) -> Dict:
        return {
            "api_key": self.ed_api.text().strip(),
            "targets_file": self.ed_targets.text().strip() or "target.json",
            "concurrency": int(self.sb_conc.value()),
            "auto_refresh_sec": int(self.sb_auto.value()),
            "rate_max_per_min": int(self.sb_rate_cap.value()),
            "min_interval_ms": int(self.sb_min_interval.value()),
            "load_cache_at_start": bool(self.chk_load_cache.isChecked()),
            "save_cache_every": int(self.sb_save_every.value()),
            "max_retries": int(self.sb_max_retries.value()),
            "backoff_base_ms": int(self.sb_backoff_base.value()),
            "backoff_cap_s": int(self.sb_backoff_cap.value()),
            "respect_retry_after": bool(self.chk_retry_after.isChecked()),
            "req_delay_ms": int(self.sb_min_interval.value()),       # compat
            "start_maximized": bool(self.chk_start_max.isChecked()), # compat
        }

    def _apply(self):
        payload = self._payload()
        self.saved.emit(payload)
        self._settings.update(payload)
        self._dirty = False
        self.btn_apply.setEnabled(False)
        self._refresh_header_summary()
        self._set_footer_status("Settings applied.", "success")

    def _save_and_close(self):
        self._apply()
        self.accept()

    def _maybe_discard_and_close(self):
        if not self._dirty:
            self.reject(); return
        m = QMessageBox(self); m.setWindowTitle("Discard changes?")
        m.setText("You have unsaved changes. Discard them?")
        m.setIcon(QMessageBox.Icon.Warning)
        m.setStandardButtons(QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if m.exec() == QMessageBox.StandardButton.Discard:
            self.reject()

    def _reset_values(self):
        self.ed_api.setText(self._settings.get("api_key", ""))
        self.ed_targets.setText(self._settings.get("targets_file", "target.json"))
        self.chk_start_max.setChecked(bool(self._settings.get("start_maximized", True)))

        self.chk_load_cache.setChecked(True)
        self.sb_save_every.setValue(20)

        self.sb_conc.setValue(4)
        self.sb_auto.setValue(0)
        self.sb_rate_cap.setValue(100)
        self.sb_min_interval.setValue(620)

        self.sb_max_retries.setValue(8)
        self.sb_backoff_base.setValue(600)
        self.sb_backoff_cap.setValue(8)
        self.chk_retry_after.setChecked(True)

        self._validate_api_key()
        self._update_targets_hint()
        self._update_cache_info()
        self._update_effective_pacing()
        self._update_backoff_preview()
        self._refresh_header_summary()
        self._dirty = True
        self.btn_apply.setEnabled(True)
        self._set_footer_status("Defaults restored. Click Apply to keep them.", "info")
