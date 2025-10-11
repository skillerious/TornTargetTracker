# settings_dialog.py
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QSize
from PyQt6.QtGui import (
    QIcon, QDesktopServices, QColor, QPainter, QPixmap, QPalette, QFontMetrics
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSlider, QSpinBox,
    QStackedWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget, QListWidget,
    QListWidgetItem, QStyle, QDialogButtonBox, QGroupBox
)

from storage import get_appdata_dir, load_targets_from_file


# ---------- icon helpers ----------
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


def _base_icon(name: str) -> QIcon:
    p = _icon_path(name)
    if p:
        return QIcon(p)
    st = QApplication.style() if QApplication.instance() else None
    if not st:
        return QIcon()
    fallbacks = {
        "settings": QStyle.StandardPixmap.SP_FileDialogDetailedView,
        "info": QStyle.StandardPixmap.SP_MessageBoxInformation,
        "folder": QStyle.StandardPixmap.SP_DirIcon,
        "check": QStyle.StandardPixmap.SP_DialogApplyButton,
        "refresh": QStyle.StandardPixmap.SP_BrowserReload,
        "link": QStyle.StandardPixmap.SP_DirLinkIcon,
        "clear": QStyle.StandardPixmap.SP_DialogCloseButton,
        "warning": QStyle.StandardPixmap.SP_MessageBoxWarning,
        "open": QStyle.StandardPixmap.SP_DialogOpenButton,
        "save": QStyle.StandardPixmap.SP_DialogSaveButton,
        "apply": QStyle.StandardPixmap.SP_DialogApplyButton,
        "help": QStyle.StandardPixmap.SP_DialogHelpButton,
        "view": QStyle.StandardPixmap.SP_FileDialogInfoView,
        "new": QStyle.StandardPixmap.SP_FileDialogNewFolder
    }
    return st.standardIcon(fallbacks.get(name, QStyle.StandardPixmap.SP_FileDialogInfoView))


def icon(name: str) -> QIcon:
    return _base_icon(name)


# ---------- small helpers ----------
_API_RE = re.compile(r"^[A-Za-z0-9]{16,64}$")
_CACHE_FILE = os.path.join(get_appdata_dir(), "cache_targets.json")


class ElidedLabel(QLabel):
    """Single-line label that elides middle and shows full text as tooltip."""
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setMinimumHeight(18)
        self.setToolTip(text)

    def set_full_text(self, text: str):
        self._full = text or ""
        self.setToolTip(self._full)
        self._refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh()

    def _refresh(self):
        fm = QFontMetrics(self.font())
        full = getattr(self, "_full", self.toolTip() or self.text())
        self.setText(fm.elidedText(full, Qt.TextElideMode.ElideMiddle, max(30, self.width())))


class SettingsDialog(QDialog):
    saved = pyqtSignal(dict)
    API_KEY_HELP_URL = "https://www.torn.com/preferences.php#tab=api"

    def __init__(self, settings: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        # Compact by default; resizable both ways
        self.setMinimumSize(QSize(680, 480))
        self.resize(740, 520)

        self._settings = dict(settings or {})
        self._dirty = False

        self._apply_style()

        # ---------- skeleton (sidebar + stack) ----------
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        outer.addWidget(self._build_header())

        center = QWidget(); cl = QHBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(12)

        self.sidebar = QListWidget(objectName="sidebar")
        self.sidebar.setFixedWidth(208)

        self.stack = QStackedWidget()

        cl.addWidget(self.sidebar)
        cl.addWidget(self.stack, 1)
        outer.addWidget(center, 1)

        # Standard button box
        self.btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.RestoreDefaults |
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.Apply
        )
        self.btn_box.button(QDialogButtonBox.StandardButton.Save).setDefault(True)
        outer.addWidget(self.btn_box)

        # Build pages + wire
        self._build_pages()
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        self.btn_box.clicked.connect(self._on_button)
        self._update_footer_state()

    def _apply_style(self):
        # Keep the dialog consistent with the global palette (QDarkStyle, etc.)
        pal = QApplication.palette()
        border = pal.color(QPalette.ColorRole.Mid)
        focus  = pal.color(QPalette.ColorRole.Highlight)
        text   = pal.color(QPalette.ColorRole.WindowText)
        win    = pal.color(QPalette.ColorRole.Window)

        def rgba(c: QColor, a: float) -> str:
            a = max(0.0, min(1.0, a))
            return f"rgba({c.red()},{c.green()},{c.blue()},{a:.3f})"

        # Detect if we're in a dark theme to choose the right overlay direction
        luminance = (0.2126 * win.redF()) + (0.7152 * win.greenF()) + (0.0722 * win.blueF())
        is_dark = luminance < 0.5

        # Subtle card background for QGroupBox: a *slightly* darker overlay on dark themes
        CARD_ALPHA_DARK = 0.12   # tweak here if you want it a touch lighter/darker (0.10–0.14)
        CARD_ALPHA_LIGHT = 0.06  # for light themes (kept for completeness)

        card_bg = rgba(QColor(0, 0, 0), CARD_ALPHA_DARK) if is_dark else rgba(QColor(255, 255, 255), CARD_ALPHA_LIGHT)

        border_css = border.name()
        hover_css  = rgba(focus, 0.10)
        muted_css  = rgba(text, 0.70)
        note_css   = rgba(focus, 0.85)

        self.setStyleSheet(f"""
            /* Inherit window background from app/theme */
            QLabel, QCheckBox, QTextBrowser {{ background: transparent; }}

            /* Sidebar */
            QListWidget#sidebar {{
                border: 1px solid {border_css};
                border-radius: 10px;
                padding: 6px;
            }}
            QListWidget#sidebar::item {{
                padding: 8px 10px;
                border-radius: 8px;
                margin: 1px 0;
            }}
            QListWidget#sidebar::item:selected {{
                background: {hover_css};
                border: 1px solid {focus.name()};
            }}
            QListWidget#sidebar::item:hover {{ background: {hover_css}; }}

            /* Cards (group boxes) — now slightly darker for contrast */
            QGroupBox {{
                border: 1px solid {border_css};
                border-radius: 10px;
                margin-top: 10px;
                padding: 10px;
                background: {card_bg};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                font-weight: 600;
                color: {note_css};
            }}

            /* Inputs */
            QLineEdit, QSpinBox, QComboBox {{
                border: 1px solid {border_css};
                border-radius: 6px;
                padding: 4px 6px;
                min-height: 26px;
                background: transparent;
            }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
                border: 1px solid {focus.name()};
            }}

            /* Buttons */
            QToolButton, QPushButton {{
                border: 1px solid {border_css};
                border-radius: 8px;
                padding: 5px 10px;
                background: transparent;
                min-height: 26px;
            }}
            QToolButton:hover, QPushButton:hover {{ background: {hover_css}; }}

            /* Utility text classes */
            .muted {{ color: {muted_css}; }}
            .good  {{ color: rgb(142, 230, 179); }}
            .bad   {{ color: rgb(255, 155, 142); }}
            .note  {{ color: {note_css}; }}
        """)



    # ---------- header ----------
    def _build_header(self) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0,0,0,0); h.setSpacing(8)
        ic = QLabel(); ic.setPixmap(icon("settings").pixmap(20, 20)); h.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        box = QVBoxLayout(); box.setSpacing(1)
        title = QLabel("<span style='font-size:16px; font-weight:600;'>Settings</span>")
        sub = QLabel("Tweak your API, cache, and performance preferences"); sub.setProperty("class", "muted")
        box.addWidget(title); box.addWidget(sub)
        h.addLayout(box, 1); h.addStretch(1)
        return w

    # ---------- pages ----------
    def _build_pages(self):
        self.sidebar.addItem(QListWidgetItem(icon("general"), "General"))
        self.sidebar.addItem(QListWidgetItem(icon("folder"), "Data & Cache"))
        self.sidebar.addItem(QListWidgetItem(icon("performance"), "Performance"))
        self.sidebar.addItem(QListWidgetItem(icon("refresh"), "Retries & Backoff"))
        self.sidebar.addItem(QListWidgetItem(icon("help"), "Help"))

        # GENERAL
        general = QWidget(); gl = QVBoxLayout(general); gl.setContentsMargins(2,2,2,2); gl.setSpacing(10)

        # Torn API group
        api_g = QGroupBox("Torn API"); api_l = QGridLayout(api_g); api_l.setHorizontalSpacing(8); api_l.setVerticalSpacing(6)
        self.ed_api = QLineEdit(self._settings.get("api_key", ""))
        self.ed_api.setEchoMode(QLineEdit.EchoMode.Password)
        self.lbl_api_status = QLabel(""); self.lbl_api_status.setProperty("class", "muted")

        btn_show = QToolButton(); btn_show.setText("Show"); btn_show.setCheckable(True)
        btn_show.toggled.connect(lambda ch: self.ed_api.setEchoMode(QLineEdit.EchoMode.Normal if ch else QLineEdit.EchoMode.Password))
        btn_paste = QToolButton(); btn_paste.setText("Paste"); btn_paste.clicked.connect(self._paste_api)

        btn_open = QToolButton(); btn_open.setIcon(icon("link")); btn_open.setToolTip("Open API Page")
        btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.API_KEY_HELP_URL)))
        btn_test = QToolButton(); btn_test.setIcon(icon("check")); btn_test.setToolTip("Test Key")
        btn_test.clicked.connect(self._test_key)

        api_l.addWidget(QLabel("API Key:"), 0, 0)
        api_l.addWidget(self.ed_api, 0, 1)
        row_btns = QWidget(); rb = QHBoxLayout(row_btns); rb.setContentsMargins(0,0,0,0); rb.setSpacing(6)
        for w in (btn_show, btn_paste, btn_open, btn_test):
            rb.addWidget(w)
        api_l.addWidget(row_btns, 0, 2)
        api_l.addWidget(self.lbl_api_status, 1, 1, 1, 2)

        # Targets & Window group
        tgt_g = QGroupBox("Targets & Window"); tgt_f = QFormLayout(tgt_g)
        tgt_f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        tgt_f.setHorizontalSpacing(8); tgt_f.setVerticalSpacing(6)

        self.ed_targets = QLineEdit(self._settings.get("targets_file", "target.json"))
        self.ed_targets.setMinimumWidth(240)
        btn_browse = QToolButton(); btn_browse.setIcon(icon("folder")); btn_browse.setToolTip("Browse…")
        btn_browse.clicked.connect(self._pick_targets)
        btn_create = QToolButton(); btn_create.setIcon(icon("new")); btn_create.setToolTip("Create file")
        btn_create.clicked.connect(self._create_targets_file)

        tgt_row = QWidget(); tr = QHBoxLayout(tgt_row); tr.setContentsMargins(0,0,0,0); tr.setSpacing(6)
        tr.addWidget(self.ed_targets, 1); tr.addWidget(btn_browse); tr.addWidget(btn_create)

        self.lbl_targets_hint = ElidedLabel(); self.lbl_targets_hint.setProperty("class", "muted")
        self.chk_start_max = QCheckBox("Start maximized")
        self.chk_start_max.setChecked(bool(self._settings.get("start_maximized", True)))

        tgt_f.addRow("Targets file:", tgt_row)
        tgt_f.addRow("", self.lbl_targets_hint)
        tgt_f.addRow("", self.chk_start_max)

        gl.addWidget(api_g)
        gl.addWidget(tgt_g)
        gl.addStretch(1)

        # DATA & CACHE
        data = QWidget(); dl = QVBoxLayout(data); dl.setContentsMargins(2,2,2,2); dl.setSpacing(10)

        app_g = QGroupBox("AppData"); app_l = QGridLayout(app_g); app_l.setHorizontalSpacing(8); app_l.setVerticalSpacing(6)
        self.ed_appdata = QLineEdit(get_appdata_dir()); self.ed_appdata.setReadOnly(True)
        btn_open_folder = QPushButton("Open Folder"); btn_open_folder.setIcon(icon("folder"))
        btn_open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(get_appdata_dir())))
        app_l.addWidget(self.ed_appdata, 0, 0, 1, 1)
        app_l.addWidget(btn_open_folder, 0, 1, 1, 1)

        cache_g = QGroupBox("Cache"); cache_f = QFormLayout(cache_g)
        cache_f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        cache_f.setHorizontalSpacing(8); cache_f.setVerticalSpacing(6)

        self.chk_load_cache = QCheckBox("Load cache at startup")
        self.chk_load_cache.setChecked(bool(self._settings.get("load_cache_at_start", True)))
        self.sb_save_every = QSpinBox(); self.sb_save_every.setRange(5, 200)
        self.sb_save_every.setValue(int(self._settings.get("save_cache_every", 20)))
        self.sb_save_every.setSuffix(" updates")
        self.lbl_cache_info = QLabel(""); self.lbl_cache_info.setProperty("class", "muted")
        btn_clear_cache = QPushButton("Clear cache…"); btn_clear_cache.setIcon(icon("clear"))
        btn_clear_cache.clicked.connect(self._clear_cache)

        cache_f.addRow("", self.chk_load_cache)
        cache_f.addRow("Save cache every:", self.sb_save_every)
        cache_f.addRow("", self.lbl_cache_info)
        row_clear = QWidget(); rc = QHBoxLayout(row_clear); rc.setContentsMargins(0,0,0,0); rc.addStretch(1); rc.addWidget(btn_clear_cache)
        cache_f.addRow("", row_clear)

        dl.addWidget(app_g)
        dl.addWidget(cache_g)
        dl.addStretch(1)

        # PERFORMANCE
        perf = QWidget(); pl = QVBoxLayout(perf); pl.setContentsMargins(2,2,2,2); pl.setSpacing(10)

        perf_g = QGroupBox("Performance"); grid = QGridLayout(perf_g)
        grid.setHorizontalSpacing(8); grid.setVerticalSpacing(8)

        self.sb_conc = QSpinBox(); self.sb_conc.setRange(1, 16); self.sb_conc.setValue(int(self._settings.get("concurrency", 4)))
        self.sl_conc = QSlider(Qt.Orientation.Horizontal); self.sl_conc.setRange(1, 16); self.sl_conc.setValue(self.sb_conc.value())
        self.sb_auto = QSpinBox(); self.sb_auto.setRange(0, 3600); self.sb_auto.setSuffix(" sec"); self.sb_auto.setValue(int(self._settings.get("auto_refresh_sec", 0)))
        self.sl_auto = QSlider(Qt.Orientation.Horizontal); self.sl_auto.setRange(0, 3600); self.sl_auto.setValue(self.sb_auto.value())
        self.sb_rate_cap = QSpinBox(); self.sb_rate_cap.setRange(10, 200); self.sb_rate_cap.setValue(int(self._settings.get("rate_max_per_min", 100)))
        self.sl_rate_cap = QSlider(Qt.Orientation.Horizontal); self.sl_rate_cap.setRange(10, 200); self.sl_rate_cap.setValue(self.sb_rate_cap.value())
        self.sb_min_interval = QSpinBox(); self.sb_min_interval.setRange(0, 5000); self.sb_min_interval.setSuffix(" ms")
        self.sb_min_interval.setValue(int(self._settings.get("min_interval_ms", self._settings.get("req_delay_ms", 620))))
        self.sl_min_interval = QSlider(Qt.Orientation.Horizontal); self.sl_min_interval.setRange(0, 5000); self.sl_min_interval.setValue(self.sb_min_interval.value())

        self.lbl_effective = QLabel(""); self.lbl_effective.setProperty("class", "muted")
        self.lbl_estimate = QLabel(""); self.lbl_estimate.setProperty("class", "muted")
        self.btn_recommended = QPushButton("Recommended (Torn safe)"); self.btn_recommended.setIcon(icon("check"))
        self.btn_recommended.clicked.connect(self._apply_recommended)

        # Bindings
        self._bind_pair(self.sb_conc, self.sl_conc)
        self._bind_pair(self.sb_auto, self.sl_auto)
        self._bind_pair(self.sb_rate_cap, self.sl_rate_cap)
        self._bind_pair(self.sb_min_interval, self.sl_min_interval)

        r = 0
        grid.addWidget(QLabel("Concurrency:"), r, 0); grid.addWidget(self.sb_conc, r, 1); grid.addWidget(self.sl_conc, r, 2); r += 1
        grid.addWidget(QLabel("Auto refresh:"), r, 0); grid.addWidget(self.sb_auto, r, 1); grid.addWidget(self.sl_auto, r, 2); r += 1
        grid.addWidget(QLabel("Rate cap (per minute):"), r, 0); grid.addWidget(self.sb_rate_cap, r, 1); grid.addWidget(self.sl_rate_cap, r, 2); r += 1
        grid.addWidget(QLabel("Min interval between calls:"), r, 0); grid.addWidget(self.sb_min_interval, r, 1); grid.addWidget(self.sl_min_interval, r, 2); r += 1
        grid.addWidget(self.lbl_effective, r, 0, 1, 3); r += 1
        grid.addWidget(self.lbl_estimate, r, 0, 1, 3)

        row_btn = QWidget(); rb = QHBoxLayout(row_btn); rb.setContentsMargins(0,0,0,0); rb.addStretch(1); rb.addWidget(self.btn_recommended)

        pl.addWidget(perf_g)
        pl.addWidget(row_btn)
        pl.addStretch(1)

        # RETRIES
        back = QWidget(); bl = QVBoxLayout(back); bl.setContentsMargins(2,2,2,2); bl.setSpacing(10)
        back_g = QGroupBox("Retries & Backoff"); form = QFormLayout(back_g)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(8); form.setVerticalSpacing(6)

        self.sb_max_retries = QSpinBox(); self.sb_max_retries.setRange(1, 12); self.sb_max_retries.setValue(int(self._settings.get("max_retries", 8)))
        self.sb_backoff_base = QSpinBox(); self.sb_backoff_base.setRange(0, 3000); self.sb_backoff_base.setSuffix(" ms"); self.sb_backoff_base.setValue(int(self._settings.get("backoff_base_ms", 600)))
        self.sb_backoff_cap = QSpinBox(); self.sb_backoff_cap.setRange(1, 60); self.sb_backoff_cap.setSuffix(" s"); self.sb_backoff_cap.setValue(int(self._settings.get("backoff_cap_s", 8)))
        self.chk_retry_after = QCheckBox("Honor Retry-After header"); self.chk_retry_after.setChecked(bool(self._settings.get("respect_retry_after", True)))
        self.lbl_backoff_hint = QLabel(""); self.lbl_backoff_hint.setProperty("class", "muted")
        self.lbl_backoff_table = QLabel(""); self.lbl_backoff_table.setProperty("class", "note"); self.lbl_backoff_table.setTextFormat(Qt.TextFormat.RichText)

        form.addRow("Max retries:", self.sb_max_retries)
        form.addRow("Backoff base:", self.sb_backoff_base)
        form.addRow("Backoff cap:", self.sb_backoff_cap)
        form.addRow("", self.chk_retry_after)
        form.addRow("", self.lbl_backoff_hint)
        form.addRow("", self.lbl_backoff_table)

        bl.addWidget(back_g)
        bl.addStretch(1)

        # HELP
        helpw = QWidget(); hl = QVBoxLayout(helpw); hl.setContentsMargins(2,2,2,2); hl.setSpacing(10)
        help_g = QGroupBox("Help"); help_box = QTextBrowser(); help_box.setOpenExternalLinks(True)
        help_box.setHtml(self._help_html())
        help_v = QVBoxLayout(help_g); help_v.setContentsMargins(8,8,8,8); help_v.addWidget(help_box)
        hl.addWidget(help_g, 1)

        # Add to stack
        self.stack.addWidget(general)
        self.stack.addWidget(data)
        self.stack.addWidget(perf)
        self.stack.addWidget(back)
        self.stack.addWidget(helpw)

        # init dynamic text + signals
        self._validate_api_key()
        self._update_targets_hint()
        self._update_cache_info()
        self._update_effective_pacing()
        self._update_backoff_preview()
        self._connect_dirty_signals()

    # ---------- help ----------
    def _help_html(self) -> str:
        return (
            "<style>a{color:#4da3ff;text-decoration:none}a:hover{text-decoration:underline}"
            "ul{margin-left:1.1em}code{background:rgba(255,255,255,0.06);padding:2px 4px;border-radius:4px}</style>"
            "<h3>Tips</h3>"
            "<ul>"
            "<li><b>API Key:</b> Use a <i>Limited Access</i> key. Stored locally only.</li>"
            "<li><b>Rate Limits:</b> Keep ≤100/min or add a minimum interval to pace.</li>"
            "<li><b>Recommended:</b> 100/min with 620ms interval is safe for Torn.</li>"
            "<li><b>Backoff:</b> Exponential with jitter; enable <i>Retry-After</i>.</li>"
            "</ul>"
            f"<p>Manage your key: <a href='{self.API_KEY_HELP_URL}'>{self.API_KEY_HELP_URL}</a></p>"
        )

    # ---------- bindings ----------
    def _bind_pair(self, spin: QSpinBox, slider: QSlider):
        spin.valueChanged.connect(slider.setValue)
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(self._mark_dirty)
        slider.valueChanged.connect(self._mark_dirty)
        if spin in (self.sb_rate_cap, self.sb_min_interval, self.sb_auto, self.sb_conc):
            spin.valueChanged.connect(self._update_effective_pacing)

    # ---------- button box ----------
    def _on_button(self, button):
        std = self.btn_box.standardButton(button)
        if std == QDialogButtonBox.StandardButton.RestoreDefaults:
            self._reset_values()
        elif std == QDialogButtonBox.StandardButton.Apply:
            self._apply()
        elif std == QDialogButtonBox.StandardButton.Save:
            self._apply()
            self.accept()
        elif std == QDialogButtonBox.StandardButton.Cancel:
            self._maybe_discard_and_close()

    # ---------- footer state ----------
    def _update_footer_state(self):
        self.btn_box.button(QDialogButtonBox.StandardButton.Apply).setEnabled(self._dirty)

    def _mark_dirty(self, *_):
        self._dirty = True
        self._update_footer_state()

    def _maybe_discard_and_close(self):
        if not self._dirty:
            self.reject(); return
        m = QMessageBox(self)
        m.setWindowTitle("Discard changes?")
        m.setText("You have unsaved changes. Discard them?")
        m.setIcon(QMessageBox.Icon.Warning)
        m.setStandardButtons(QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if m.exec() == QMessageBox.StandardButton.Discard:
            self.reject()

    # ---------- collect & save ----------
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
        self._update_footer_state()

    # ---------- API & file helpers ----------
    def _validate_api_key(self):
        key = self.ed_api.text().strip()
        if not key:
            self.lbl_api_status.setText("<span class='muted'>Paste a <b>Limited Access</b> key from Torn.</span>")
        elif _API_RE.match(key):
            self.lbl_api_status.setText("<span class='good'>Looks good. (Format check passed)</span>")
        else:
            self.lbl_api_status.setText("<span class='bad'>This doesn't look like a Torn API key.</span>")

    def _paste_api(self):
        try:
            clip = QApplication.clipboard().text().strip()
            if clip:
                self.ed_api.setText(clip)
                self._mark_dirty()
                self._validate_api_key()
        except Exception:
            pass

    def _pick_targets(self):
        p, _ = QFileDialog.getOpenFileName(self, "Pick targets JSON", "", "JSON (*.json)")
        if p:
            self.ed_targets.setText(p)
            self._mark_dirty()
            self._update_targets_hint()
            self._update_effective_pacing()

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
            self._mark_dirty()
            self._update_targets_hint()
            self._update_effective_pacing()
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
            self.lbl_targets_hint.set_full_text("File not found. Click Create or choose an existing JSON.")
            self.lbl_targets_hint.setProperty("class", "bad"); self.lbl_targets_hint.style().unpolish(self.lbl_targets_hint); self.lbl_targets_hint.style().polish(self.lbl_targets_hint)
        else:
            self.lbl_targets_hint.set_full_text(f"{abspath} • {cnt} target(s).")

    # ---------- cache ----------
    def _cache_info(self) -> Optional[str]:
        p = _CACHE_FILE
        if not os.path.exists(p):
            return None
        try:
            sz = os.path.getsize(p)
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))
            kb = sz / 1024.0
            return f"Cache file: {p} • {kb:.1f} KB • modified {mtime}"
        except Exception:
            return f"Cache file: {p}"

    def _update_cache_info(self):
        info = self._cache_info()
        self.lbl_cache_info.setText(info or "<span class='muted'>No cache yet.</span>")

    def _clear_cache(self):
        if not os.path.exists(_CACHE_FILE):
            QMessageBox.information(self, "Clear cache", "No cache file found.")
            return
        m = QMessageBox(self)
        m.setWindowTitle("Clear cache?")
        m.setText("Delete the local cache file? It will be rebuilt on next refresh.")
        m.setIcon(QMessageBox.Icon.Warning)
        m.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if m.exec() == QMessageBox.StandardButton.Yes:
            try:
                os.remove(_CACHE_FILE)
                self._update_cache_info()
            except Exception as e:
                QMessageBox.warning(self, "Clear cache", f"Failed to delete:\n{e}")

    # ---------- pacing ----------
    def _effective_per_min(self) -> int:
        cap = max(1, int(self.sb_rate_cap.value()))
        min_ms = max(0, int(self.sb_min_interval.value()))
        by_interval = (60000 // min_ms) if min_ms > 0 else cap
        return max(1, min(cap, by_interval))

    def _update_effective_pacing(self):
        eff = self._effective_per_min()
        rps = eff / 60.0
        self.lbl_effective.setText(f"Effective pacing: <b>~{eff}/min</b> <span class='note'>(~{rps:.2f}/s)</span>")
        cnt, _ = self._targets_stats()
        if cnt <= 0:
            self.lbl_estimate.setText("<span class='muted'>No targets detected.</span>")
            return
        secs = (cnt / rps) if rps > 0 else 0
        self.lbl_estimate.setText(f"Estimated time for <b>{cnt}</b> target(s): <b>{self._fmt_secs(secs)}</b> (ignores retries)")

    @staticmethod
    def _fmt_secs(secs: float) -> str:
        secs = max(0, int(round(secs)))
        m, s = divmod(secs, 60); h, m = divmod(m, 60)
        return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")

    def _apply_recommended(self):
        self.sb_rate_cap.setValue(100)
        self.sb_min_interval.setValue(620)
        self._mark_dirty()
        self._update_effective_pacing()

    # ---------- backoff ----------
    def _backoff_series(self, base_ms: int, cap_s: int, retries: int) -> list[int]:
        out = []
        for attempt in range(1, retries + 1):
            v = base_ms * (2 ** (attempt - 1)) / 1000.0
            v = min(v, cap_s)
            out.append(int(round(v)))
        return out

    def _update_backoff_preview(self):
        base = int(self.sb_backoff_base.value())
        cap = int(self.sb_backoff_cap.value())
        retries = int(self.sb_max_retries.value())
        self.lbl_backoff_hint.setText(
            f"Exponential backoff with jitter. Base: <b>{base}ms</b> • Cap: <b>{cap}s</b> • Retries: <b>{retries}</b>"
        )
        series = self._backoff_series(base, cap, retries)
        cells = "".join(
            f"<td style='padding:3px 8px;border:1px solid rgba(255,255,255,0.12);'>#{i+1}: {s}s</td>"
            for i, s in enumerate(series)
        )
        self.lbl_backoff_table.setText(f"<div style='margin-top:6px'><table style='border-collapse:collapse;'><tr>{cells}</tr></table></div>")

    # ---------- reset / signals ----------
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

        self._mark_dirty()
        self._validate_api_key()
        self._update_targets_hint()
        self._update_cache_info()
        self._update_effective_pacing()
        self._update_backoff_preview()

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

    # ---------- key test ----------
    def _test_key(self):
        key = self.ed_api.text().strip()
        if not key:
            QMessageBox.information(self, "Test Key", "Please paste your API key first.")
            return
        try:
            import requests
        except Exception:
            QMessageBox.information(self, "Test Key", "The 'requests' package isn't installed.")
            return
        try:
            resp = requests.get("https://api.torn.com/user/", params={"selections": "basic", "key": key}, timeout=6.0)
            data = resp.json()
        except Exception as e:
            QMessageBox.warning(self, "Test Key", f"Network error:\n{e}")
            return
        if isinstance(data, dict) and "error" in data:
            err = data.get("error") or {}
            QMessageBox.warning(self, "Test Key", f"Torn error [{err.get('code','—')}]: {err.get('error','Unknown error')}")
            return
        name = data.get("name") if isinstance(data, dict) else None
        QMessageBox.information(self, "Test Key", f"Success!{' Authenticated as: ' + name if name else ''}")

    # ---------- close guard ----------
    def closeEvent(self, e):
        if self._dirty:
            m = QMessageBox(self)
            m.setWindowTitle("Discard changes?")
            m.setText("You have unsaved changes. Discard them?")
            m.setIcon(QMessageBox.Icon.Warning)
            m.setStandardButtons(QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if m.exec() != QMessageBox.StandardButton.Discard:
                e.ignore(); return
        super().closeEvent(e)
