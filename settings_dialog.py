# settings_dialog.py
from __future__ import annotations

import json
import os
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QSize
from PyQt6.QtGui import QIcon, QDesktopServices, QColor, QPainter, QPixmap, QPalette
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSlider, QSpinBox, QStackedWidget, QTextBrowser, QToolButton, QVBoxLayout,
    QWidget, QListWidget, QListWidgetItem, QStyle
)

from storage import get_appdata_dir, load_targets_from_file


# ---------- assets / icons ----------

def _icon_path(name: str) -> Optional[str]:
    """Look up an icon in assets/ic-*.svg|png, then fall back by name."""
    for p in (
        os.path.join("assets", f"ic-{name}.svg"),
        os.path.join("assets", f"ic-{name}.png"),
        f"ic-{name}.svg",
        f"ic-{name}.png",
    ):
        if os.path.exists(p):
            return p
    return None


def _qt_fallback(name: str) -> QIcon:
    st = QApplication.style() if QApplication.instance() else None
    if not st:
        return QIcon()
    # Reasonable fallbacks mapped to Qt standard pixmaps
    mp = {
        "general": QStyle.StandardPixmap.SP_FileDialogInfoView,
        "data": QStyle.StandardPixmap.SP_DirIcon,
        "performance": QStyle.StandardPixmap.SP_ComputerIcon,
        "retries": QStyle.StandardPixmap.SP_BrowserReload,
        "help": QStyle.StandardPixmap.SP_MessageBoxQuestion,

        "eye": QStyle.StandardPixmap.SP_DialogYesButton,
        "paste": QStyle.StandardPixmap.SP_DialogOpenButton,
        "ext": QStyle.StandardPixmap.SP_DirLinkIcon,
        "check": QStyle.StandardPixmap.SP_DialogApplyButton,
        "folder-open": QStyle.StandardPixmap.SP_DirOpenIcon,
        "file-plus": QStyle.StandardPixmap.SP_FileIcon,
        "broom": QStyle.StandardPixmap.SP_TrashIcon,
        "save": QStyle.StandardPixmap.SP_DialogSaveButton,
        "cancel": QStyle.StandardPixmap.SP_DialogCancelButton,
        "apply": QStyle.StandardPixmap.SP_DialogApplyButton,
        "reset": QStyle.StandardPixmap.SP_BrowserStop,
        "shield-check": QStyle.StandardPixmap.SP_DialogApplyButton,
    }
    return st.standardIcon(mp.get(name, QStyle.StandardPixmap.SP_FileDialogInfoView))


def _base_icon(name: str) -> QIcon:
    p = _icon_path(name)
    return QIcon(p) if p else _qt_fallback(name)


def icon(name: str, tint: Optional[str] = None) -> QIcon:
    """
    Return an icon tinted to the given hex color (if provided) to suit
    dark UI surfaces without needing multiple colored assets.
    """
    ico = _base_icon(name)
    if tint is None:
        return ico
    pm = ico.pixmap(18, 18)
    if pm.isNull():
        return ico
    tinted = QPixmap(pm.size())
    tinted.fill(Qt.GlobalColor.transparent)
    p = QPainter(tinted)
    p.drawPixmap(0, 0, pm)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(tinted.rect(), QColor(tint))
    p.end()
    out = QIcon()
    out.addPixmap(tinted)
    return out


# ---------- constants & helpers ----------

_API_RE_UPPER_LEN = 64
_API_RE_LOWER_LEN = 16

def _looks_like_api_key(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return False
    if not s.isalnum():
        return False
    return _API_RE_LOWER_LEN <= len(s) <= _API_RE_UPPER_LEN


# ---------- dialog ----------

class SettingsDialog(QDialog):
    saved = pyqtSignal(dict)
    API_KEY_HELP_URL = "https://www.torn.com/preferences.php#tab=api"

    def __init__(self, settings: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        # Tighter, saner default sizing (no horizontal scrollbars)
        self.resize(860, 560)
        self.setMinimumSize(760, 520)

        self._settings = dict(settings or {})
        self._dirty = False

        self._apply_theme_stylesheet()

        # ====== layout skeleton ======
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        # Header
        header = QWidget(self)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)

        h_ico = QLabel()
        h_ico.setPixmap(icon("general", "#CFE3FF").pixmap(18, 18))
        h_title = QLabel("<span style='font-size:16px;font-weight:600'>Settings</span>")
        h_sub = QLabel("Tweak your API, cache, and performance preferences")
        h_sub.setProperty("class", "muted")

        h_text = QVBoxLayout()
        h_text.setContentsMargins(0, 0, 0, 0)
        h_text.setSpacing(2)
        h_text.addWidget(h_title)
        h_text.addWidget(h_sub)

        hl.addWidget(h_ico, 0, Qt.AlignmentFlag.AlignTop)
        hl.addLayout(h_text, 1)
        outer.addWidget(header)

        # Center area
        center = QWidget(self)
        cl = QHBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)

        # Sidebar
        self.sidebar = QListWidget(objectName="sidebar")
        self.sidebar.setFixedWidth(230)
        self.sidebar.setIconSize(QSize(18, 18))
        cl.addWidget(self.sidebar)

        # Stack
        self.stack = QStackedWidget(self)
        cl.addWidget(self.stack, 1)

        outer.addWidget(center, 1)

        # Footer
        footer = QWidget(self)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(8)

        self.btn_restore = QPushButton("Restore Defaults")
        self.btn_restore.setObjectName("btnTonal")
        self.btn_restore.setIcon(icon("reset", "#CFE3FF"))

        fl.addWidget(self.btn_restore)
        fl.addStretch(1)

        self.btn_save = QPushButton("Save")
        self.btn_save.setObjectName("btnPrimary")
        self.btn_save.setIcon(icon("save"))

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("btnGhost")
        self.btn_cancel.setIcon(icon("cancel"))

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setObjectName("btnPrimary")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setIcon(icon("apply"))

        for b in (self.btn_save, self.btn_cancel, self.btn_apply):
            b.setMinimumWidth(96)

        fl.addWidget(self.btn_save)
        fl.addWidget(self.btn_cancel)
        fl.addWidget(self.btn_apply)

        outer.addWidget(footer)

        # Build pages
        self._build_pages()

        # Wire footer
        self.btn_restore.clicked.connect(self._reset_values)
        self.btn_cancel.clicked.connect(self._maybe_discard_and_close)
        self.btn_save.clicked.connect(self._save_and_close)
        self.btn_apply.clicked.connect(self._apply)

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

    # ---------- stylesheet tuned for dark UI ----------

    def _apply_theme_stylesheet(self):
        pal: QPalette = QApplication.palette()
        base_win = pal.color(QPalette.ColorRole.Window).name()            # window background
        text_col = pal.color(QPalette.ColorRole.WindowText).name()
        accent = pal.color(QPalette.ColorRole.Highlight).name()

        # Slightly darker card surface than the app window (visible grouping)
        # and tasteful borders that won’t glow.
        BORDER = "rgba(255,255,255,0.10)"
        BORDER_FOCUS = accent
        CARD_BG = "rgba(0,0,0,0.18)"   # <- darker than window (subtle but clear)
        INPUT_BG = "rgba(255,255,255,0.04)"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {base_win};
                color: {text_col};
            }}

            .muted {{ color: rgba(207,227,255,0.75); }}
            .note  {{ color: #CFE3FF; }}

            /* Sidebar */
            QListWidget#sidebar {{
                border: 1px solid {BORDER};
                border-radius: 12px;
                background: rgba(0,0,0,0.12);
                padding: 6px;
                outline: 0;
            }}
            QListWidget#sidebar::item {{
                padding: 9px 12px;
                border-radius: 8px;
                margin: 1px 0;
            }}
            QListWidget#sidebar::item:selected {{
                background: rgba(77,163,255,0.15);
                border: 1px solid {BORDER_FOCUS};
            }}
            QListWidget#sidebar::item:hover {{
                background: rgba(255,255,255,0.06);
            }}

            /* Card group */
            QFrame#card {{
                background: {CARD_BG};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            QLabel.cardTitle {{
                font-size: 14px;
                font-weight: 600;
                padding: 2px 0 4px 0;
            }}

            /* Inputs */
            QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
                background: {INPUT_BG};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 6px 8px;
                min-height: 28px;
            }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
                border: 1px solid {BORDER_FOCUS};
                outline: none;
            }}

            /* Sliders align with inputs visually */
            QSlider::groove:horizontal {{
                height: 6px;
                background: rgba(255,255,255,0.10);
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
                background: {BORDER_FOCUS};
            }}

            /* Buttons (base) */
            QPushButton, QToolButton {{
                border: 1px solid {BORDER};
                border-radius: 9px;
                background: rgba(255,255,255,0.03);
                padding: 6px 10px;
                min-height: 28px;
            }}
            QPushButton:hover, QToolButton:hover {{ background: rgba(255,255,255,0.08); }}
            QPushButton:pressed, QToolButton:pressed {{ background: rgba(255,255,255,0.12); }}
            QPushButton:disabled, QToolButton:disabled {{
                color: rgba(255,255,255,0.45);
                border-color: rgba(255,255,255,0.08);
                background: rgba(255,255,255,0.02);
            }}

            /* Button variants */
            QPushButton#btnPrimary {{
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 rgba(77,163,255,0.28),
                    stop:1 rgba(77,163,255,0.18));
                border: 1px solid {BORDER_FOCUS};
            }}
            QPushButton#btnPrimary:hover {{
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 rgba(77,163,255,0.36),
                    stop:1 rgba(77,163,255,0.26));
            }}
            QPushButton#btnPrimary:pressed {{
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 rgba(77,163,255,0.28),
                    stop:1 rgba(77,163,255,0.18));
            }}

            QPushButton#btnTonal {{
                background: rgba(255,255,255,0.06);
            }}
            QPushButton#btnGhost {{
                background: transparent;
            }}

            /* Utility rows that should never show a box */
            QWidget#rowRight, QWidget#rowClear {{
                background: transparent;
                border: none;
            }}
        """)

    # ---------- page / card helpers ----------

    def _card(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        wrapper = QWidget(objectName="card")
        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)
        ttl = QLabel(title)
        ttl.setProperty("class", "cardTitle")
        outer.addWidget(ttl)
        body = QWidget()
        vb = QVBoxLayout(body)
        vb.setContentsMargins(2, 0, 2, 0)
        vb.setSpacing(8)
        outer.addWidget(body)
        return wrapper, vb

    def _row_right(self, *widgets: QWidget) -> QWidget:
        w = QWidget(objectName="rowRight")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch(1)
        for wid in widgets:
            h.addWidget(wid)
        return w

    # ---------- pages ----------

    def _build_pages(self):
        # Sidebar items (with consistent, clean icons)
        self.sidebar.addItem(QListWidgetItem(icon("general", "#CFE3FF"), "General"))
        self.sidebar.addItem(QListWidgetItem(icon("data", "#CFE3FF"), "Data & Cache"))
        self.sidebar.addItem(QListWidgetItem(icon("performance", "#CFE3FF"), "Performance"))
        self.sidebar.addItem(QListWidgetItem(icon("retries", "#CFE3FF"), "Retries & Backoff"))
        self.sidebar.addItem(QListWidgetItem(icon("help", "#CFE3FF"), "Help"))

        # ---- GENERAL (API + Targets/Window)
        general = QWidget()
        gl = QVBoxLayout(general)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(10)

        # Torn API
        api_card, api_body = self._card("Torn API")

        self.ed_api = QLineEdit(self._settings.get("api_key", ""))
        self.ed_api.setEchoMode(QLineEdit.EchoMode.Password)
        self.lbl_api_status = QLabel("")
        self.lbl_api_status.setProperty("class", "muted")

        btn_show = QToolButton(text="Show")
        btn_show.setIcon(icon("eye"))
        btn_show.setCheckable(True)
        btn_show.toggled.connect(lambda ch: self.ed_api.setEchoMode(
            QLineEdit.EchoMode.Normal if ch else QLineEdit.EchoMode.Password))

        btn_paste = QToolButton(text="Paste")
        btn_paste.setIcon(icon("paste"))
        btn_paste.clicked.connect(self._paste_api)

        btn_open = QToolButton(text="")
        btn_open.setToolTip("Open API page")
        btn_open.setIcon(icon("ext"))
        btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.API_KEY_HELP_URL)))

        btn_test = QToolButton(text="")
        btn_test.setToolTip("Test key")
        btn_test.setIcon(icon("check"))
        btn_test.clicked.connect(self._test_key)

        api_row = QWidget()
        ar = QHBoxLayout(api_row)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.setSpacing(6)
        ar.addWidget(self.ed_api, 1)
        for b in (btn_show, btn_paste, btn_open, btn_test):
            ar.addWidget(b)

        api_form = QFormLayout()
        api_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        api_form.setHorizontalSpacing(10)
        api_form.setVerticalSpacing(8)
        api_form.addRow("API Key:", api_row)
        api_form.addRow("", self.lbl_api_status)
        api_body.addLayout(api_form)

        # Targets + Window
        tgt_card, tgt_body = self._card("Targets  Window")

        self.ed_targets = QLineEdit(self._settings.get("targets_file", "target.json"))
        self.lbl_targets_hint = QLabel("")
        self.lbl_targets_hint.setProperty("class", "muted")

        # icon-only for compactness
        btn_browse = QToolButton()
        btn_browse.setIcon(icon("folder-open"))
        btn_browse.setToolTip("Browse…")
        btn_browse.clicked.connect(self._pick_targets)

        btn_create = QToolButton()
        btn_create.setIcon(icon("file-plus"))
        btn_create.setToolTip("Create file")
        btn_create.clicked.connect(self._create_targets_file)

        row_tgt = QWidget()
        rt = QHBoxLayout(row_tgt)
        rt.setContentsMargins(0, 0, 0, 0)
        rt.setSpacing(6)
        rt.addWidget(self.ed_targets, 1)
        rt.addWidget(btn_browse)
        rt.addWidget(btn_create)

        self.chk_start_max = QCheckBox("Start maximized")
        self.chk_start_max.setChecked(bool(self._settings.get("start_maximized", True)))

        tgt_form = QFormLayout()
        tgt_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        tgt_form.setHorizontalSpacing(10)
        tgt_form.setVerticalSpacing(8)
        tgt_form.addRow("Targets file:", row_tgt)
        tgt_form.addRow("", self.lbl_targets_hint)
        tgt_form.addRow("", self.chk_start_max)

        tgt_body.addLayout(tgt_form)

        gl.addWidget(api_card)
        gl.addWidget(tgt_card)
        gl.addStretch(1)

        # ---- DATA & CACHE
        data = QWidget()
        dl = QVBoxLayout(data)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(10)

        app_card, app_body = self._card("AppData")

        self.ed_appdata = QLineEdit(get_appdata_dir())
        self.ed_appdata.setReadOnly(True)

        btn_open_folder = QToolButton()
        btn_open_folder.setIcon(icon("folder-open"))
        btn_open_folder.setToolTip("Open folder")
        btn_open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(get_appdata_dir())))

        row_ad = QWidget()
        adl = QHBoxLayout(row_ad)
        adl.setContentsMargins(0, 0, 0, 0)
        adl.setSpacing(6)
        adl.addWidget(self.ed_appdata, 1)
        adl.addWidget(btn_open_folder)

        app_form = QFormLayout()
        app_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        app_form.setHorizontalSpacing(10)
        app_form.setVerticalSpacing(8)
        app_form.addRow("", row_ad)

        app_body.addLayout(app_form)

        cache_card, cache_body = self._card("Cache")
        self.chk_load_cache = QCheckBox("Load cache at startup")
        self.chk_load_cache.setChecked(bool(self._settings.get("load_cache_at_start", True)))

        self.sb_save_every = QSpinBox()
        self.sb_save_every.setRange(5, 200)
        self.sb_save_every.setValue(int(self._settings.get("save_cache_every", 20)))
        self.sb_save_every.setSuffix(" updates")

        self.lbl_cache_info = QLabel("")
        self.lbl_cache_info.setProperty("class", "muted")

        btn_clear_cache = QPushButton(" Clear cache…")
        btn_clear_cache.setIcon(icon("broom"))
        btn_clear_cache.clicked.connect(self._clear_cache)

        cache_form = QFormLayout()
        cache_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        cache_form.setHorizontalSpacing(10)
        cache_form.setVerticalSpacing(8)
        cache_form.addRow("", self.chk_load_cache)
        cache_form.addRow("Save cache every:", self.sb_save_every)
        cache_form.addRow("", self.lbl_cache_info)
        cache_body.addLayout(cache_form)
        cache_body.addWidget(self._row_right(btn_clear_cache))

        dl.addWidget(app_card)
        dl.addWidget(cache_card)
        dl.addStretch(1)

        # ---- PERFORMANCE
        perf = QWidget()
        pl = QVBoxLayout(perf)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(10)

        perf_card, perf_body = self._card("Performance")

        gridw = QWidget()
        grid = QGridLayout(gridw)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.sb_conc = QSpinBox()
        self.sb_conc.setRange(1, 16)
        self.sb_conc.setValue(int(self._settings.get("concurrency", 4)))
        self.sl_conc = QSlider(Qt.Orientation.Horizontal)
        self.sl_conc.setRange(1, 16)
        self.sl_conc.setValue(self.sb_conc.value())

        self.sb_auto = QSpinBox()
        self.sb_auto.setRange(0, 3600)
        self.sb_auto.setSuffix(" sec")
        self.sb_auto.setValue(int(self._settings.get("auto_refresh_sec", 0)))
        self.sl_auto = QSlider(Qt.Orientation.Horizontal)
        self.sl_auto.setRange(0, 3600)
        self.sl_auto.setValue(self.sb_auto.value())

        self.sb_rate_cap = QSpinBox()
        self.sb_rate_cap.setRange(10, 200)
        self.sb_rate_cap.setValue(int(self._settings.get("rate_max_per_min", 100)))
        self.sl_rate_cap = QSlider(Qt.Orientation.Horizontal)
        self.sl_rate_cap.setRange(10, 200)
        self.sl_rate_cap.setValue(self.sb_rate_cap.value())

        self.sb_min_interval = QSpinBox()
        self.sb_min_interval.setRange(0, 5000)
        self.sb_min_interval.setSuffix(" ms")
        self.sb_min_interval.setValue(int(self._settings.get("min_interval_ms", self._settings.get("req_delay_ms", 620))))
        self.sl_min_interval = QSlider(Qt.Orientation.Horizontal)
        self.sl_min_interval.setRange(0, 5000)
        self.sl_min_interval.setValue(self.sb_min_interval.value())

        self._bind_pair(self.sb_conc, self.sl_conc)
        self._bind_pair(self.sb_auto, self.sl_auto)
        self._bind_pair(self.sb_rate_cap, self.sl_rate_cap)
        self._bind_pair(self.sb_min_interval, self.sl_min_interval)

        r = 0
        grid.addWidget(QLabel("Concurrency:"), r, 0)
        grid.addWidget(self.sb_conc, r, 1)
        grid.addWidget(self.sl_conc, r, 2)
        r += 1

        grid.addWidget(QLabel("Auto refresh:"), r, 0)
        grid.addWidget(self.sb_auto, r, 1)
        grid.addWidget(self.sl_auto, r, 2)
        r += 1

        grid.addWidget(QLabel("Rate cap (per minute):"), r, 0)
        grid.addWidget(self.sb_rate_cap, r, 1)
        grid.addWidget(self.sl_rate_cap, r, 2)
        r += 1

        grid.addWidget(QLabel("Min interval between calls:"), r, 0)
        grid.addWidget(self.sb_min_interval, r, 1)
        grid.addWidget(self.sl_min_interval, r, 2)

        self.lbl_effective = QLabel("")
        self.lbl_effective.setProperty("class", "muted")
        self.lbl_estimate = QLabel("")
        self.lbl_estimate.setProperty("class", "muted")

        perf_body.addWidget(gridw)
        perf_body.addSpacing(4)
        perf_body.addWidget(self.lbl_effective)
        perf_body.addWidget(self.lbl_estimate)

        btn_recommended = QPushButton(" Recommended (Torn safe)")
        btn_recommended.setIcon(icon("shield-check"))
        btn_recommended.setObjectName("btnTonal")
        btn_recommended.clicked.connect(self._apply_recommended)
        perf_body.addWidget(self._row_right(btn_recommended))

        pl.addWidget(perf_card)
        pl.addStretch(1)

        # ---- RETRIES / BACKOFF
        back = QWidget()
        bl = QVBoxLayout(back)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)

        back_card, back_body = self._card("Retries  Backoff")

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.sb_max_retries = QSpinBox()
        self.sb_max_retries.setRange(1, 12)
        self.sb_max_retries.setValue(int(self._settings.get("max_retries", 8)))
        self.sb_backoff_base = QSpinBox()
        self.sb_backoff_base.setRange(0, 3000)
        self.sb_backoff_base.setSuffix(" ms")
        self.sb_backoff_base.setValue(int(self._settings.get("backoff_base_ms", 600)))
        self.sb_backoff_cap = QSpinBox()
        self.sb_backoff_cap.setRange(1, 60)
        self.sb_backoff_cap.setSuffix(" s")
        self.sb_backoff_cap.setValue(int(self._settings.get("backoff_cap_s", 8)))
        self.chk_retry_after = QCheckBox("Honor Retry-After header")
        self.chk_retry_after.setChecked(bool(self._settings.get("respect_retry_after", True)))

        form.addRow("Max retries:", self.sb_max_retries)
        form.addRow("Backoff base:", self.sb_backoff_base)
        form.addRow("Backoff cap:", self.sb_backoff_cap)
        form.addRow("", self.chk_retry_after)

        self.lbl_backoff_hint = QLabel("")
        self.lbl_backoff_hint.setProperty("class", "muted")
        self.lbl_backoff_table = QLabel("")
        self.lbl_backoff_table.setProperty("class", "note")
        self.lbl_backoff_table.setTextFormat(Qt.TextFormat.RichText)

        back_body.addLayout(form)
        back_body.addWidget(self.lbl_backoff_hint)
        back_body.addWidget(self.lbl_backoff_table)

        bl.addWidget(back_card)
        bl.addStretch(1)

        # ---- HELP
        helpw = QWidget()
        hl = QVBoxLayout(helpw)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)

        help_card, help_body = self._card("Help")
        help_box = QTextBrowser()
        help_box.setOpenExternalLinks(True)
        help_box.setHtml(self._help_html())
        help_body.addWidget(help_box)
        hl.addWidget(help_card, 1)

        # Stack
        self.stack.addWidget(general)
        self.stack.addWidget(data)
        self.stack.addWidget(perf)
        self.stack.addWidget(back)
        self.stack.addWidget(helpw)

        # Init dynamic bits
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

    # ---------- bindings / dirty state ----------
    def _bind_pair(self, spin: QSpinBox, slider: QSlider):
        spin.valueChanged.connect(slider.setValue)
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(self._mark_dirty)
        slider.valueChanged.connect(self._mark_dirty)
        if spin in (self.sb_rate_cap, self.sb_min_interval, self.sb_auto, self.sb_conc):
            spin.valueChanged.connect(self._update_effective_pacing)

    def _mark_dirty(self):
        self._dirty = True
        self.btn_apply.setEnabled(True)

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

    # ---------- actions ----------
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
            self.lbl_api_status.setText("<span class='muted'>Paste a <b>Limited Access</b> key from Torn.</span>")
            return
        self.lbl_api_status.setText(
            "<span style='color:#8ee6b3'>Looks good. (Format check passed)</span>"
            if _looks_like_api_key(key) else
            "<span style='color:#ff9b8e'>This doesn't look like a Torn API key.</span>"
        )

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
            self.lbl_targets_hint.setText(
                f"Path: <code>{abspath}</code> • <b>{cnt}</b> target(s)."
            )

    def _cache_info(self) -> Optional[str]:
        p = os.path.join(get_appdata_dir(), "cache_targets.json")
        if not os.path.exists(p):
            return None
        try:
            sz = os.path.getsize(p)
            kb = sz / 1024.0
            mtime = os.path.getmtime(p)
            from time import localtime, strftime
            ts = strftime("%Y-%m-%d %H:%M", localtime(mtime))
            return f"Cache file: <code>{p}</code> • {kb:.1f} KB • modified {ts}"
        except Exception:
            return f"Cache file: <code>{p}</code>"

    def _update_cache_info(self):
        info = self._cache_info()
        self.lbl_cache_info.setText(info or "<span class='muted'>No cache yet.</span>")

    def _clear_cache(self):
        p = os.path.join(get_appdata_dir(), "cache_targets.json")
        if not os.path.exists(p):
            QMessageBox.information(self, "Clear cache", "No cache file found.")
            return
        m = QMessageBox(self)
        m.setIcon(QMessageBox.Icon.Warning)
        m.setWindowTitle("Clear cache?")
        m.setText("Delete the local cache file? It will be rebuilt on next refresh.")
        m.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if m.exec() == QMessageBox.StandardButton.Yes:
            try:
                os.remove(p)
                self._update_cache_info()
            except Exception as e:
                QMessageBox.warning(self, "Clear cache", f"Failed to delete:\n{e}")

    # pacing
    def _effective_per_min(self) -> int:
        cap = max(1, int(self.sb_rate_cap.value()))
        min_ms = max(0, int(self.sb_min_interval.value()))
        by_interval = (60000 // min_ms) if min_ms > 0 else cap
        return max(1, min(cap, by_interval))

    def _update_effective_pacing(self):
        eff = self._effective_per_min()
        rps = eff / 60.0
        self.lbl_effective.setText(f"Effective pacing: <b>~{eff}/min</b>  <span class='muted'>(~{rps:.2f}/s)</span>")
        cnt, _ = self._targets_stats()
        if cnt <= 0 or rps <= 0:
            self.lbl_estimate.setText("<span class='muted'>No targets detected.</span>")
            return
        secs = int(round(cnt / rps))
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        eta = f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")
        self.lbl_estimate.setText(f"Estimated time for <b>{cnt}</b> target(s): <b>{eta}</b> <span class='muted'>(ignores retries)</span>")

    def _apply_recommended(self):
        self.sb_rate_cap.setValue(100)
        self.sb_min_interval.setValue(620)
        self._update_effective_pacing()

    # backoff preview
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
            f"<span style='padding:2px 8px;border:1px solid rgba(255,255,255,0.12);border-radius:6px;margin-right:6px'>#{i+1}: {s}s</span>"
            for i, s in enumerate(series)
        )
        self.lbl_backoff_table.setText(f"<div style='margin-top:6px'>{cells}</div>")

    # ---------- apply/save ----------
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

    def _save_and_close(self):
        self._apply()
        self.accept()

    def _maybe_discard_and_close(self):
        if not self._dirty:
            self.reject()
            return
        m = QMessageBox(self)
        m.setWindowTitle("Discard changes?")
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

    # ---------- tests ----------
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

    # Guard against accidental close with unsaved changes
    def closeEvent(self, e):
        if self._dirty:
            m = QMessageBox(self)
            m.setWindowTitle("Discard changes?")
            m.setText("You have unsaved changes. Discard them?")
            m.setIcon(QMessageBox.Icon.Warning)
            m.setStandardButtons(QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if m.exec() != QMessageBox.StandardButton.Discard:
                e.ignore()
                return
        super().closeEvent(e)
