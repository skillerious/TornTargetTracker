# settings_dialog.py
from __future__ import annotations

import json
import os
import threading
from typing import Dict, Optional, Tuple

import requests

from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QTimer
from PyQt6.QtGui import (
    QDesktopServices, QColor, QPalette, QIcon, QKeySequence, QShortcut, QPixmap, QPainter
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSlider, QSpinBox, QStackedWidget, QTextBrowser, QToolButton, QVBoxLayout,
    QWidget, QTreeWidget, QTreeWidgetItem, QStyleFactory, QSizePolicy, QFrame
)

from storage import get_appdata_dir, load_targets_from_file


__all__ = ["SettingsDialog"]


API_HELP_URL = "https://www.torn.com/preferences.php#tab=api"


# ───────────────────────── helpers ─────────────────────────

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


# ───────────────────────── dialog ─────────────────────────

class SettingsDialog(QDialog):
    """
    Fresh, self-styled Settings dialog that doesn’t inherit your app’s QDarkStyle.
    It forces Fusion style + dark palette and scopes all QSS to its own root.
    """
    saved = pyqtSignal(dict)

    def __init__(self, settings: Dict, parent=None):
        super().__init__(parent)

        # — Force a different style/palette so it cannot look like the old window —
        self._apply_fusion_dark()
        self.setObjectName("TTSettingsRoot")
        self.setWindowTitle("Settings — NEW UI")
        self.resize(980, 640)
        self.setMinimumSize(860, 560)

        self._settings = dict(settings or {})
        self._dirty = False

        self._apply_scoped_qss()

        # Root layout
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # Header
        header = QWidget(objectName="header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 10, 14, 10)
        hl.setSpacing(10)

        hico = QLabel()
        # tiny painted gear if no asset present
        pm = QPixmap(18, 18); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); p.setPen(QColor("#A7B7C9")); p.drawEllipse(2, 2, 14, 14); p.end()
        hico.setPixmap(icon("settings").pixmap(18, 18) if not icon("settings").isNull() else pm)

        hTitle = QLabel("<span class='h1'>Settings</span>")
        hSub = QLabel("Manage API, targets, cache and performance")
        hSub.setProperty("muted", True)

        twrap = QVBoxLayout(); twrap.setContentsMargins(0, 0, 0, 0); twrap.setSpacing(2)
        twrap.addWidget(hTitle); twrap.addWidget(hSub)

        hl.addWidget(hico, 0, Qt.AlignmentFlag.AlignTop)
        hl.addLayout(twrap, 1)
        root.addWidget(header)

        # Body
        body = QWidget()
        bl = QHBoxLayout(body); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(10)

        # Left: search + tree
        left = QWidget(objectName="leftPanel")
        lp = QVBoxLayout(left); lp.setContentsMargins(8, 8, 8, 8); lp.setSpacing(8)

        self.search = QLineEdit(placeholderText="Search settings…")
        self.search.setObjectName("leftSearch")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_tree)

        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setIndentation(14)
        self.tree.currentItemChanged.connect(self._on_tree_changed)

        lp.addWidget(self.search); lp.addWidget(self.tree, 1)

        # Right: crumb + stack
        right = QWidget(objectName="rightPanel")
        rr = QVBoxLayout(right); rr.setContentsMargins(0, 0, 0, 0); rr.setSpacing(8)

        crumb = QWidget(objectName="crumb")
        cr = QHBoxLayout(crumb); cr.setContentsMargins(10, 6, 10, 6); cr.setSpacing(8)
        self.lblCrumb = QLabel("Settings"); self.lblCrumb.setProperty("muted", True)
        cr.addWidget(self.lblCrumb); cr.addStretch(1)

        self.stack = QStackedWidget()
        rr.addWidget(crumb); rr.addWidget(self.stack, 1)

        bl.addWidget(left, 0); bl.addWidget(right, 1)
        root.addWidget(body, 1)

        # Footer
        footer = QWidget(objectName="footer")
        fl = QHBoxLayout(footer); fl.setContentsMargins(8, 8, 8, 8); fl.setSpacing(8)

        self.btn_defaults = QPushButton("Restore Defaults")
        fl.addWidget(self.btn_defaults)
        fl.addStretch(1)

        self.btn_ok = QPushButton("OK")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_apply = QPushButton("Apply"); self.btn_apply.setEnabled(False)
        for b in (self.btn_ok, self.btn_cancel, self.btn_apply):
            b.setMinimumWidth(90)
        fl.addWidget(self.btn_ok); fl.addWidget(self.btn_cancel); fl.addWidget(self.btn_apply)
        root.addWidget(footer)

        # Build pages + nav
        self._build_pages_and_tree()

        # Wire actions
        self.btn_defaults.clicked.connect(self._reset_values)
        self.btn_cancel.clicked.connect(self._maybe_discard_and_close)
        self.btn_ok.clicked.connect(self._save_and_close)
        self.btn_apply.clicked.connect(self._apply)

        # Shortcuts
        QShortcut(QKeySequence.StandardKey.Save, self, self._apply)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self._apply)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._maybe_discard_and_close)

        # Initial section
        self._select_tree("General")

    # ───────────────────── look & feel ─────────────────────

    def _apply_fusion_dark(self):
        """Force Fusion style and a dark palette on THIS dialog only."""
        self.setStyle(QStyleFactory.create("Fusion"))
        pal = QPalette()

        # Dark base palette
        bg = QColor(0x23, 0x27, 0x2e)      # main background
        panel = QColor(0x30, 0x34, 0x3a)   # cards/panels
        text = QColor(0xE6, 0xEE, 0xF8)
        muted = QColor(0xA9, 0xB7, 0xC6)
        hl = QColor(0x4D, 0xA3, 0xFF)

        pal.setColor(QPalette.ColorRole.Window, bg)
        pal.setColor(QPalette.ColorRole.Base, panel)
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(0x2B, 0x30, 0x35))
        pal.setColor(QPalette.ColorRole.Button, panel)
        pal.setColor(QPalette.ColorRole.ButtonText, text)
        pal.setColor(QPalette.ColorRole.Text, text)
        pal.setColor(QPalette.ColorRole.WindowText, text)
        pal.setColor(QPalette.ColorRole.BrightText, text)
        pal.setColor(QPalette.ColorRole.ToolTipBase, panel)
        pal.setColor(QPalette.ColorRole.ToolTipText, text)
        pal.setColor(QPalette.ColorRole.Highlight, hl)
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(0x10, 0x14, 0x18))

        self.setPalette(pal)

    def _apply_scoped_qss(self):
        """Stylesheet scoped to #TTSettingsRoot so it overrides app-wide QSS."""
        self.setStyleSheet("""
        #TTSettingsRoot { background: #23272e; color: #e6eef8; font-size: 13px; }

        /* header / crumb */
        #TTSettingsRoot QWidget#header, #TTSettingsRoot QWidget#crumb {
            background: #30343a;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px;
        }
        #TTSettingsRoot .h1 { font-size: 16px; font-weight: 600; }
        #TTSettingsRoot [muted="true"] { color: #a9b7c6; }

        /* left panel */
        #TTSettingsRoot QWidget#leftPanel {
            background: #2b3035;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px;
        }
        #TTSettingsRoot QLineEdit#leftSearch {
            background: #353a40; border: 1px solid rgba(255,255,255,0.1);
            border-radius: 6px; padding: 6px 8px; min-height: 28px;
        }
        #TTSettingsRoot QLineEdit#leftSearch:focus { border-color: #4da3ff; }
        #TTSettingsRoot QTreeWidget { background: transparent; border: none; outline: 0; }
        #TTSettingsRoot QTreeWidget::item {
            padding: 6px 8px; margin: 1px 4px; border-radius: 6px;
        }
        #TTSettingsRoot QTreeWidget::item:selected { background: #0e639c; color: white; }
        #TTSettingsRoot QTreeWidget::item:hover { background: rgba(255,255,255,0.08); }

        /* right panel base (inherits window bg) */
        #TTSettingsRoot QWidget#rightPanel { background: #23272e; }

        /* generic inputs */
        #TTSettingsRoot QLineEdit, #TTSettingsRoot QSpinBox, #TTSettingsRoot QComboBox,
        #TTSettingsRoot QTextEdit, #TTSettingsRoot QPlainTextEdit {
            background: #30343a; border: 1px solid #444a50; border-radius: 6px;
            padding: 6px 8px; min-height: 26px;
        }
        #TTSettingsRoot QLineEdit:focus, #TTSettingsRoot QSpinBox:focus, #TTSettingsRoot QComboBox:focus,
        #TTSettingsRoot QTextEdit:focus, #TTSettingsRoot QPlainTextEdit:focus { border-color: #4da3ff; }
        #TTSettingsRoot QToolButton, #TTSettingsRoot QPushButton {
            background: #30343a; border: 1px solid #444a50; border-radius: 6px;
            padding: 6px 10px; min-height: 26px; color: #e6eef8;
        }
        #TTSettingsRoot QToolButton:hover, #TTSettingsRoot QPushButton:hover { background: #3a3f46; }
        #TTSettingsRoot QToolButton:pressed, #TTSettingsRoot QPushButton:pressed { background: #2d3136; }

        /* footer */
        #TTSettingsRoot QWidget#footer { border-top: 1px solid #444a50; }

        /* small text helpers */
        #TTSettingsRoot QLabel.helpSmall { color: #a9b7c6; }
        #TTSettingsRoot .mono { background: #2e3338; padding: 2px 4px; border-radius: 4px; }
        """)

    # ───────────────────── tree & pages ─────────────────────

    def _select_tree(self, name: str):
        want = name.lower()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it and it.text(0).lower() == want:
                self.tree.setCurrentItem(it)
                break

    def _on_tree_changed(self, cur: QTreeWidgetItem, _prev: QTreeWidgetItem):
        if not cur:
            return
        text = cur.text(0)
        index = {"general": 0, "data & cache": 1, "performance": 2,
                 "retries & backoff": 3, "help": 4}.get(text.lower(), 0)
        self.stack.setCurrentIndex(index)
        self.lblCrumb.setText(f"Settings  >  {text}")

    def _filter_tree(self, q: str):
        q = (q or "").strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.setHidden(bool(q) and q not in it.text(0).lower())

    def _row(self, *widgets: QWidget) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        for wd in widgets: h.addWidget(wd)
        return w

    def _add_section(self, parent_layout: QVBoxLayout, title: str) -> QFormLayout:
        ttl = QLabel(title); ttl.setStyleSheet("font-weight:600; color:#d5e7ff; padding:2px 0 4px 0")
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(255,255,255,0.1)")
        wrap = QWidget(); form = QFormLayout(wrap)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12); form.setVerticalSpacing(10)
        form.setContentsMargins(10, 6, 6, 6)
        parent_layout.addWidget(ttl); parent_layout.addWidget(sep); parent_layout.addWidget(wrap)
        return form

    def _build_pages_and_tree(self):
        # Left nav
        self.tree.clear()
        for text in ("General", "Data & Cache", "Performance", "Retries & Backoff", "Help"):
            self.tree.addTopLevelItem(QTreeWidgetItem([text]))

        # Pages
        # — General
        pg_general = QWidget(); gl = QVBoxLayout(pg_general); gl.setContentsMargins(8, 8, 8, 8); gl.setSpacing(12)

        api_form = self._add_section(gl, "Torn API")
        self.ed_api = QLineEdit(self._settings.get("api_key", ""))
        self.ed_api.setEchoMode(QLineEdit.EchoMode.Password)
        self.lbl_api_status = QLabel(""); self.lbl_api_status.setProperty("class", "helpSmall")

        btn_show = QToolButton(); btn_show.setText("👁"); btn_show.setToolTip("Show / hide")
        btn_show.setCheckable(True)
        btn_show.toggled.connect(lambda ch: self.ed_api.setEchoMode(
            QLineEdit.EchoMode.Normal if ch else QLineEdit.EchoMode.Password))

        btn_paste = QToolButton(); btn_paste.setText("📋"); btn_paste.setToolTip("Paste")
        btn_paste.clicked.connect(self._paste_api)

        btn_open = QToolButton(); btn_open.setText("↗"); btn_open.setToolTip("Open API page")
        btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(API_HELP_URL)))

        btn_test = QToolButton(); btn_test.setText("✓"); btn_test.setToolTip("Test key")
        btn_test.clicked.connect(self._test_key)

        api_form.addRow("API Key:", self._row(self.ed_api, btn_show, btn_paste, btn_open, btn_test))
        api_form.addRow("", self.lbl_api_status)

        tgt_form = self._add_section(gl, "Targets & Window")
        self.ed_targets = QLineEdit(self._settings.get("targets_file", "target.json"))
        self.lbl_targets_hint = QLabel(""); self.lbl_targets_hint.setProperty("class", "helpSmall")
        btn_browse = QToolButton(); btn_browse.setText("📂"); btn_browse.setToolTip("Browse…")
        btn_browse.clicked.connect(self._pick_targets)
        btn_create = QToolButton(); btn_create.setText("＋"); btn_create.setToolTip("Create file")
        btn_create.clicked.connect(self._create_targets_file)
        self.chk_start_max = QCheckBox("Start maximized")
        self.chk_start_max.setChecked(bool(self._settings.get("start_maximized", True)))

        tgt_form.addRow("Targets file:", self._row(self.ed_targets, btn_browse, btn_create))
        tgt_form.addRow("", self.lbl_targets_hint)
        tgt_form.addRow("", self.chk_start_max)

        gl.addStretch(1)

        # — Data & Cache
        pg_data = QWidget(); dl = QVBoxLayout(pg_data); dl.setContentsMargins(8, 8, 8, 8); dl.setSpacing(12)

        app_form = self._add_section(dl, "AppData")
        self.ed_appdata = QLineEdit(get_appdata_dir()); self.ed_appdata.setReadOnly(True)
        btn_open_folder = QToolButton(); btn_open_folder.setText("📂"); btn_open_folder.setToolTip("Open folder")
        btn_open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(get_appdata_dir())))
        app_form.addRow("", self._row(self.ed_appdata, btn_open_folder))

        cache_form = self._add_section(dl, "Cache")
        self.chk_load_cache = QCheckBox("Load cache at startup")
        self.chk_load_cache.setChecked(bool(self._settings.get("load_cache_at_start", True)))
        self.sb_save_every = QSpinBox(); self.sb_save_every.setRange(5, 200)
        self.sb_save_every.setValue(int(self._settings.get("save_cache_every", 20)))
        self.sb_save_every.setSuffix(" updates")
        self.lbl_cache_info = QLabel(""); self.lbl_cache_info.setProperty("class", "helpSmall")
        btn_clear_cache = QPushButton("Clear cache…")
        btn_clear_cache.clicked.connect(self._clear_cache)
        cache_form.addRow("", self.chk_load_cache)
        cache_form.addRow("Save cache every:", self.sb_save_every)
        cache_form.addRow("", self.lbl_cache_info)
        cache_form.addRow("", self._row(self._spacer(), btn_clear_cache))
        dl.addStretch(1)

        # — Performance
        pg_perf = QWidget(); pl = QVBoxLayout(pg_perf); pl.setContentsMargins(8, 8, 8, 8); pl.setSpacing(12)

        perf_grid = QWidget(); grid = QGridLayout(perf_grid)
        grid.setContentsMargins(0, 0, 0, 0); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(10)

        self.sb_conc = QSpinBox(); self.sb_conc.setRange(1, 16)
        self.sb_conc.setValue(int(self._settings.get("concurrency", 4)))
        self.sl_conc = QSlider(Qt.Orientation.Horizontal); self.sl_conc.setRange(1, 16); self.sl_conc.setValue(self.sb_conc.value())

        self.sb_auto = QSpinBox(); self.sb_auto.setRange(0, 3600); self.sb_auto.setSuffix(" sec")
        self.sb_auto.setValue(int(self._settings.get("auto_refresh_sec", 0)))
        self.sl_auto = QSlider(Qt.Orientation.Horizontal); self.sl_auto.setRange(0, 3600); self.sl_auto.setValue(self.sb_auto.value())

        self.sb_rate_cap = QSpinBox(); self.sb_rate_cap.setRange(10, 200)
        self.sb_rate_cap.setValue(int(self._settings.get("rate_max_per_min", 100)))
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
        btn_recommended = QPushButton("Recommended (Torn safe)")
        btn_recommended.clicked.connect(self._apply_recommended)

        perf_form = self._add_section(pl, "Performance")
        perf_form.addRow("", perf_grid)
        perf_form.addRow("", self.lbl_effective)
        perf_form.addRow("", self.lbl_estimate)
        perf_form.addRow("", self._row(self._spacer(), btn_recommended))
        pl.addStretch(1)

        # — Retries & Backoff
        pg_back = QWidget(); bl = QVBoxLayout(pg_back); bl.setContentsMargins(8, 8, 8, 8); bl.setSpacing(12)

        form = self._add_section(bl, "Retries & Backoff")
        self.sb_max_retries = QSpinBox(); self.sb_max_retries.setRange(1, 12)
        self.sb_max_retries.setValue(int(self._settings.get("max_retries", 8)))
        self.sb_backoff_base = QSpinBox(); self.sb_backoff_base.setRange(0, 3000); self.sb_backoff_base.setSuffix(" ms")
        self.sb_backoff_base.setValue(int(self._settings.get("backoff_base_ms", 600)))
        self.sb_backoff_cap = QSpinBox(); self.sb_backoff_cap.setRange(1, 60); self.sb_backoff_cap.setSuffix(" s")
        self.sb_backoff_cap.setValue(int(self._settings.get("backoff_cap_s", 8)))
        self.chk_retry_after = QCheckBox("Honor Retry-After header")
        self.chk_retry_after.setChecked(bool(self._settings.get("respect_retry_after", True)))

        form.addRow("Max retries:", self.sb_max_retries)
        form.addRow("Backoff base:", self.sb_backoff_base)
        form.addRow("Backoff cap:", self.sb_backoff_cap)
        form.addRow("", self.chk_retry_after)

        self.lbl_backoff_hint = QLabel(""); self.lbl_backoff_hint.setProperty("class", "helpSmall")
        self.lbl_backoff_table = QLabel(""); self.lbl_backoff_table.setTextFormat(Qt.TextFormat.RichText)
        form.addRow("", self.lbl_backoff_hint)
        form.addRow("", self.lbl_backoff_table)
        bl.addStretch(1)

        # — Help
        pg_help = QWidget(); hl = QVBoxLayout(pg_help); hl.setContentsMargins(8, 8, 8, 8); hl.setSpacing(12)
        help_form = self._add_section(hl, "Help")
        help_box = QTextBrowser(); help_box.setOpenExternalLinks(True)
        help_box.setHtml(self._help_html())
        help_form.addRow("", help_box)
        hl.addStretch(1)

        # Stack
        self.stack.addWidget(pg_general)   # 0
        self.stack.addWidget(pg_data)      # 1
        self.stack.addWidget(pg_perf)      # 2
        self.stack.addWidget(pg_back)      # 3
        self.stack.addWidget(pg_help)      # 4

        # Init dynamic text + dirty tracking
        self._validate_api_key()
        self._update_targets_hint()
        self._update_cache_info()
        self._update_effective_pacing()
        self._update_backoff_preview()
        self._connect_dirty_signals()

    # ───────────────────── utilities ─────────────────────

    def _spacer(self) -> QWidget:
        s = QWidget(); s.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); return s

    def _bind_pair(self, spin: QSpinBox, slider: QSlider):
        spin.valueChanged.connect(slider.setValue)
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(self._mark_dirty)
        slider.valueChanged.connect(self._mark_dirty)
        if hasattr(self, "sb_rate_cap") and spin in (self.sb_rate_cap, self.sb_min_interval, self.sb_auto, self.sb_conc):
            spin.valueChanged.connect(self._update_effective_pacing)

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
        self._dirty = True; self.btn_apply.setEnabled(True)

    # ───────────────────── actions ─────────────────────

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
        key = self.ed_api.text().strip()
        if not key:
            self.lbl_api_status.setText("<span style='color:#ff9b8e'>No key entered.</span>")
            return

        self.lbl_api_status.setText("<span class='helpSmall'>Testing…</span>")

        def worker():
            url = f"https://api.torn.com/user/?selections=basic&key={key}"
            try:
                r = requests.get(url, timeout=6)
                msg = ""
                if r.ok:
                    try:
                        data = r.json()
                    except Exception:
                        data = {}
                    if "error" in data:
                        code = data["error"].get("code")
                        desc = data["error"].get("error")
                        msg = f"<span style='color:#ff9b8e'>API error {code}: {desc}</span>"
                    else:
                        name = data.get("name") or "OK"
                        msg = f"<span style='color:#8ee6b3'>Valid. Hello, {name}.</span>"
                else:
                    msg = f"<span style='color:#ff9b8e'>HTTP {r.status_code} while checking key.</span>"
            except Exception as e:
                msg = f"<span style='color:#ff9b8e'>Request failed: {e}</span>"

            QTimer.singleShot(0, lambda: self.lbl_api_status.setText(msg))

        threading.Thread(target=worker, daemon=True).start()

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
            self.lbl_targets_hint.setText(f"Path: <span class='mono'>{abspath}</span> • <b>{cnt}</b> target(s).")

    def _cache_info(self) -> Optional[str]:
        p = os.path.join(get_appdata_dir(), "cache_targets.json")
        if not os.path.exists(p):
            return None
        try:
            sz = os.path.getsize(p) / 1024.0
            mtime = os.path.getmtime(p)
            from time import localtime, strftime
            ts = strftime("%Y-%m-%d %H:%M", localtime(mtime))
            return f"Cache file: <span class='mono'>{p}</span> • {sz:.1f} KB • modified {ts}"
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
            f"Exponential backoff with jitter. Base: <b>{base}ms</b> • Cap: <b>{cap}s</b> • Retries: <b>{retries}</b>"
        )
        series = self._backoff_series(base, cap, retries)
        cells = "".join(
            f"<span style='padding:2px 8px;border:1px solid rgba(255,255,255,0.12);border-radius:6px;margin-right:6px'>#{i+1}: {s}s</span>"
            for i, s in enumerate(series)
        )
        self.lbl_backoff_table.setText(f"<div style='margin-top:6px'>{cells}</div>")

    def _help_html(self) -> str:
        return (
            "<style>a{color:#4da3ff;text-decoration:none}a:hover{text-decoration:underline}"
            "ul{margin-left:1.1em}code{background:#2e3338;padding:2px 4px;border-radius:4px}</style>"
            "<h3>Tips</h3>"
            "<ul>"
            "<li><b>API Key:</b> Use a <i>Limited Access</i> key. Stored locally only.</li>"
            "<li><b>Rate Limits:</b> Keep ≤100/min or add a minimum interval to pace.</li>"
            "<li><b>Recommended:</b> 100/min with 620ms interval is safe for Torn.</li>"
            "<li><b>Backoff:</b> Exponential with jitter; enable <i>Retry-After</i>.</li>"
            "</ul>"
            f"<p>Manage your key: <a href='{API_HELP_URL}'>{API_HELP_URL}</a></p>"
        )

    # ───────────────────── save/apply ─────────────────────

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
