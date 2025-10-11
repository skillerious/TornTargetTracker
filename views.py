from __future__ import annotations
import os
import re
import sys
import json
import threading
import logging
from typing import List, Set, Dict, Optional, Tuple

import requests

from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QVariant, QSortFilterProxyModel,
    pyqtSignal, QUrl, QSize, QTimer, QItemSelectionModel, QDateTime
)
from PyQt6.QtGui import QAction, QDesktopServices, QIcon, QFont, QPixmap, QPainter, QPen, QBrush, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableView, QLineEdit, QToolBar, QCheckBox,
    QPushButton, QMenu, QFileDialog, QDialog, QFormLayout, QSpinBox, QLabel,
    QGroupBox, QTextBrowser, QListWidgetItem, QListWidget, QTableWidget,
    QTableWidgetItem, QFrame, QToolButton, QComboBox, QTabWidget, QStyle, QHeaderView, QApplication, QSizePolicy
)

from models import TargetInfo
from storage import get_appdata_dir

# -----------------------------------------------------------------------------
# Logging (verbose for update checks)
# -----------------------------------------------------------------------------
log = logging.getLogger("TargetTracker.Views")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

about_log = logging.getLogger("TargetTracker.About")
if not about_log.handlers:
    _h2 = logging.StreamHandler()
    _h2.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    about_log.addHandler(_h2)
    about_log.setLevel(logging.INFO)

# Optional GitHub token for higher rate limits (Fine-grained PAT → Contents: Read-only)
# NOTE: You asked to hardcode this token. It’s embedded below exactly as provided.
GITHUB_TOKEN = "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"

COLUMNS = ["Name", "ID", "Level", "Status", "Details", "Until", "Faction", "Last Action", "Error"]

# ---------- URL helpers ----------
def profile_url(uid: int) -> str:
    return f"https://www.torn.com/profiles.php?XID={int(uid)}"

def attack_url(uid: int) -> str:
    return f"https://www.torn.com/loader.php?sid=attack&user2ID={int(uid)}"

# ---------- PyInstaller-safe assets ----------
def asset_path(rel: str) -> str:
    """
    Resolve a resource path for dev and PyInstaller (onefile) builds.
    """
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    cand = os.path.join(base, rel)
    return cand if os.path.exists(cand) else rel

def _icon_path(name: str) -> Optional[str]:
    for p in (
        asset_path(os.path.join("assets", f"ic-{name}.svg")),
        asset_path(os.path.join("assets", f"ic-{name}.png")),
        asset_path(f"ic-{name}.svg"),
        asset_path(f"ic-{name}.png"),
    ):
        if os.path.exists(p):
            return p
    return None

def icon(name: str) -> QIcon:
    p = _icon_path(name)
    return QIcon(p) if p else QIcon()

# ======================================================================
#                               MODEL
# ======================================================================

class TargetsModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._rows: List[TargetInfo] = []
        self._ignored: Set[int] = set()
        self._idx_by_uid: Dict[int, int] = {}   # fast lookup for upserts

    # ---------- helpers ----------
    @staticmethod
    def _relative_to_seconds(text: str) -> int:
        """
        Convert Torn-style 'last action' strings to seconds for stable sorting.
        Examples handled:
          'online', 'idle', 'active', 'unknown', '—'
          '45s', '3m', '2h 10m', '1 day 3h', '5d', '2w 1d'
          '3 minutes ago', '1 hour ago', '3 days ago', etc.
        Lower is more recent (0 = online). Unknown -> very large number.
        """
        if not text:
            return 10**9

        s = text.strip().lower()

        if "online" in s:
            return 0
        if "idle" in s:
            return 60
        if "active" in s and "ago" not in s:
            return 120
        if s in {"—", "-", "unknown", "n/a"}:
            return 10**9

        s = s.replace("ago", " ").replace(",", " ")
        repl = {
            "minutes": "m", "minute": "m", "mins": "m", "min": "m", "m ": "m ",
            "hours": "h", "hour": "h",
            "days": "d", "day": "d",
            "weeks": "w", "week": "w",
            "seconds": "s", "second": "s",
        }
        for k, v in repl.items():
            s = s.replace(k, v)

        total = 0
        for val, unit in re.findall(r"(\d+)\s*([wdhms])", s):
            n = int(val)
            if unit == "w": total += n * 7 * 24 * 3600
            elif unit == "d": total += n * 24 * 3600
            elif unit == "h": total += n * 3600
            elif unit == "m": total += n * 60
            elif unit == "s": total += n
        if total > 0:
            return total

        try:
            return int(s) * 60
        except Exception:
            return 10**9

    @staticmethod
    def _status_rank(chip: str) -> int:
        c = (chip or "").lower()
        if "okay" in c: return 0
        if "hospital" in c: return 1
        if "jail" in c and "federal" in c: return 2
        if "jail" in c: return 3
        if "travel" in c: return 4
        return 5

    # ---------- model core ----------
    def set_rows(self, rows: List[TargetInfo]):
        self.beginResetModel()
        self._rows = list(rows or [])
        self._idx_by_uid = {r.user_id: i for i, r in enumerate(self._rows)}
        self.endResetModel()

    def upsert(self, info: TargetInfo):
        """Insert or replace a single row and emit fine-grained signals."""
        uid = int(info.user_id)
        idx = self._idx_by_uid.get(uid)
        if idx is None:
            row = len(self._rows)
            self.beginInsertRows(QModelIndex(), row, row)
            self._rows.append(info)
            self._idx_by_uid[uid] = row
            self.endInsertRows()
        else:
            self._rows[idx] = info
            tl = self.index(idx, 0)
            br = self.index(idx, self.columnCount() - 1)
            self.dataChanged.emit(tl, br, [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.DecorationRole,
                Qt.ItemDataRole.ForegroundRole,
                Qt.ItemDataRole.FontRole,
                Qt.ItemDataRole.UserRole,
                Qt.ItemDataRole.ToolTipRole,
            ])

    def set_ignored(self, ids: Set[int]):
        self._ignored = set(ids)
        if self._rows:
            tl = self.index(0, 0)
            br = self.index(max(0, len(self._rows)-1), self.columnCount()-1)
            self.dataChanged.emit(tl, br, [
                Qt.ItemDataRole.DecorationRole,
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.ForegroundRole,
                Qt.ItemDataRole.FontRole,
                Qt.ItemDataRole.UserRole
            ])

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return QVariant()

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return QVariant()
        r = self._rows[index.row()]
        c = index.column()

        # Sort role
        if role == Qt.ItemDataRole.UserRole:
            if c == 0: return (r.name or "").lower()
            if c == 1: return int(r.user_id)
            if c == 2: return int(r.level) if r.level is not None else -1
            if c == 3: return self._status_rank(r.status_chip())
            if c == 4: return (r.status_desc or "").lower()
            if c == 5: return int(r.status_until) if r.status_until else 0
            if c == 6: return (r.faction or "").lower()
            if c == 7:
                rel = r.last_action_relative or r.last_action_status or ""
                return self._relative_to_seconds(rel)
            if c == 8: return (r.error or "")
            return ""

        # icon for ignored
        if role == Qt.ItemDataRole.DecorationRole and c == 0 and r.user_id in self._ignored:
            return icon("block-red")

        # disabled look for ignored (dim + italic)
        if role == Qt.ItemDataRole.ForegroundRole and r.user_id in self._ignored:
            from PyQt6.QtGui import QBrush, QColor
            return QBrush(QColor(150, 150, 150))
        if role == Qt.ItemDataRole.FontRole and r.user_id in self._ignored:
            f = QFont(); f.setItalic(True); return f

        # display
        if role == Qt.ItemDataRole.DisplayRole:
            dash = "—"
            if c == 0: return r.name or dash
            if c == 1: return str(r.user_id)
            if c == 2: return dash if r.level is None else str(r.level)
            if c == 3: return r.status_chip() or dash
            if c == 4: return r.status_desc or dash
            if c == 5: return r.until_human() or dash
            if c == 6: return r.faction or dash
            if c == 7: return (r.last_action_relative or r.last_action_status) or dash
            if c == 8: return r.error or ""

        # status color when not ignored
        if role == Qt.ItemDataRole.ForegroundRole and r.user_id not in self._ignored:
            s = (r.status_chip()).lower()
            from PyQt6.QtGui import QBrush, QColor
            if "okay" in s:
                return QBrush(QColor(120, 200, 120))
            if "hospital" in s or "jail" in s or "federal" in s:
                return QBrush(QColor(220, 140, 140))

        if role == Qt.ItemDataRole.TextAlignmentRole and c in (1, 2, 5):
            return Qt.AlignmentFlag.AlignCenter

        if role == Qt.ItemDataRole.ToolTipRole:
            tip = [
                f"<b>{r.name or '—'}</b> [{r.user_id}]",
                f"Level: {r.level if r.level is not None else '—'}",
                f"Status: {r.status_chip() or '—'} — {r.status_desc or '—'}",
                f"Until: {r.until_human() or '—'}",
                f"Faction: {r.faction or '—'}",
                f"Last Action: {(r.last_action_relative or r.last_action_status) or '—'}",
            ]
            if r.error: tip.append(f"<b>Error:</b> {r.error}")
            if r.user_id in self._ignored: tip.append("<b>Ignored:</b> Yes")
            return "<br>".join(tip)
        return QVariant()

    def row(self, idx: int) -> TargetInfo:
        return self._rows[idx]

# ======================================================================
#                           SEARCH BAR
# ======================================================================

class SearchBar(QWidget):
    """
    Debounced search with scope, regex and case toggles.
    Emits queryChanged(dict) => {'text','mode','regex','case_sensitive'}
    """
    queryChanged = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)

        self.scope = QComboBox()
        self.scope.addItems(["All", "Name", "ID", "Faction"])
        self.scope.setToolTip("Search scope")
        self.scope.currentIndexChanged.connect(self._emit)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Search…  (text, /regex/, id, faction)")
        self.edit.textChanged.connect(self._debounced_emit)
        # leading search icon
        act_left = self.edit.addAction(icon("search") if not icon("search").isNull() else
                                       self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogStart),
                                       QLineEdit.ActionPosition.LeadingPosition)
        act_left.setToolTip("Search")
        # trailing clear action
        clear_ic = icon("clear")
        if clear_ic.isNull():
            clear_ic = self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton)
        act_clear = self.edit.addAction(clear_ic, QLineEdit.ActionPosition.TrailingPosition)
        act_clear.setToolTip("Clear")
        act_clear.triggered.connect(self.edit.clear)

        self.btn_regex = QToolButton()
        self.btn_regex.setText(".*")
        self.btn_regex.setCheckable(True)
        self.btn_regex.setToolTip("Regex match")
        self.btn_regex.toggled.connect(self._emit)

        self.btn_case = QToolButton()
        self.btn_case.setText("Aa")
        self.btn_case.setCheckable(True)
        self.btn_case.setToolTip("Case sensitive")
        self.btn_case.toggled.connect(self._emit)

        lay.addWidget(self.scope)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.btn_regex)
        lay.addWidget(self.btn_case)

        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(200)
        self._timer.timeout.connect(self._emit)

    def focus(self): self.edit.setFocus()

    def _debounced_emit(self, _): self._timer.start()

    def _emit(self):
        text = self.edit.text()
        # smart detect /regex/ input
        if text.startswith("/") and text.endswith("/") and len(text) >= 2:
            self.btn_regex.setChecked(True)
            text = text[1:-1]
        q = {
            "text": text,
            "mode": self.scope.currentText().lower(),  # 'all' | 'name' | 'id' | 'faction'
            "regex": self.btn_regex.isChecked(),
            "case_sensitive": self.btn_case.isChecked(),
        }
        self.queryChanged.emit(q)

# ======================================================================
#                           FILTER PROXY
# ======================================================================

class FilterProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.setDynamicSortFilter(False)
        self.search_query = {"text": "", "mode": "all", "regex": False, "case_sensitive": False}
        self.show_okay = True
        self.show_hospital = True
        self.show_jail = True
        self.show_federal = True
        self.show_traveling = True
        self.hide_ignored = False
        self._ignored: Set[int] = set()
        self.level_min = 0
        self.level_max = 100

    def set_search_query(self, q: dict):
        self.search_query = q or self.search_query
        self.invalidateFilter()

    def set_flags(self, **flags):
        for k, v in flags.items():
            setattr(self, k, v)
        self.invalidateFilter()

    def set_ignored(self, ids: Set[int]):
        self._ignored = set(ids)
        self.invalidateFilter()

    def set_level_bounds(self, lo: int, hi: int):
        self.level_min = int(lo); self.level_max = int(hi)
        self.invalidateFilter()

    # util
    def _match_text(self, text: str, hay: str, regex: bool, case: bool) -> bool:
        if hay is None: return False
        if not text: return True
        if regex:
            try:
                flags = 0 if case else re.IGNORECASE
                return re.search(text, hay, flags) is not None
            except re.error:
                return text.lower() in hay.lower()
        return (text in hay) if case else (text.lower() in hay.lower())

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        m: TargetsModel = self.sourceModel()  # type: ignore
        r = m.row(source_row)

        # Search handling
        q = self.search_query
        txt = q.get("text", "") or ""
        mode = q.get("mode", "all")
        regex = bool(q.get("regex", False))
        case = bool(q.get("case_sensitive", False))

        if txt:
            if mode == "id":
                if not self._match_text(txt, str(r.user_id), regex, case): return False
            elif mode == "name":
                if not self._match_text(txt, r.name or "", regex, case): return False
            elif mode == "faction":
                if not self._match_text(txt, r.faction or "", regex, case): return False
            else:  # all
                hit = (
                    self._match_text(txt, r.name or "", regex, case) or
                    self._match_text(txt, str(r.user_id), regex, case) or
                    self._match_text(txt, r.faction or "", regex, case)
                )
                if not hit: return False

        if self.hide_ignored and r.user_id in self._ignored:
            return False

        lvl = r.level if r.level is not None else 0
        if not (self.level_min <= lvl <= self.level_max):
            return False

        chip = (r.status_chip() or "").lower()
        if ("okay" in chip and not self.show_okay): return False
        if ("hospital" in chip and not self.show_hospital): return False
        if ("jail" in chip and not self.show_jail): return False
        if ("federal" in chip and not self.show_federal): return False
        if ("travel" in chip and not self.show_traveling): return False
        return True

# ======================================================================
#                           SETTINGS DIALOG
# ======================================================================

class SettingsDialog(QDialog):
    saved = pyqtSignal(dict)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(640)

        tabs = QTabWidget(self)

        # ---- API tab
        api_tab = QWidget(); api_form = QFormLayout(api_tab)

        self.ed_api = QLineEdit(settings.get("api_key",""))
        self.ed_api.setEchoMode(QLineEdit.EchoMode.Password)
        btn_show = QPushButton("Show"); btn_show.setCheckable(True)
        btn_show.toggled.connect(lambda ch: self.ed_api.setEchoMode(QLineEdit.EchoMode.Normal if ch else QLineEdit.EchoMode.Password))
        row_api_key = QWidget(); hb = QHBoxLayout(row_api_key); hb.setContentsMargins(0,0,0,0)
        hb.addWidget(self.ed_api, 1); hb.addWidget(btn_show)

        self.ed_targets = QLineEdit(settings.get("targets_file","target.json"))
        btn_pick = QPushButton("Browse…"); btn_pick.clicked.connect(self._pick_targets)
        row_t = QWidget(); hb2 = QHBoxLayout(row_t); hb2.setContentsMargins(0,0,0,0)
        hb2.addWidget(self.ed_targets, 1); hb2.addWidget(btn_pick)

        api_form.addRow("API key:", row_api_key)
        api_form.addRow("Targets file:", row_t)

        # ---- Data & Cache tab
        data_tab = QWidget(); data = QFormLayout(data_tab)
        self.lbl_appdata = QLabel(get_appdata_dir())
        self.lbl_appdata.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        btn_open_appdata = QPushButton("Open Folder"); btn_open_appdata.clicked.connect(self._open_appdata)
        row_app = QWidget(); hb3 = QHBoxLayout(row_app); hb3.setContentsMargins(0, 0, 0, 0)
        hb3.addWidget(self.lbl_appdata, 1); hb3.addWidget(btn_open_appdata)

        self.chk_load_cache = QCheckBox("Load cache at startup")
        self.chk_load_cache.setChecked(bool(settings.get("load_cache_at_start", True)))

        self.sb_save_every = QSpinBox(); self.sb_save_every.setRange(5, 200); self.sb_save_every.setValue(int(settings.get("save_cache_every", 20)))
        self.sb_save_every.setSuffix(" updates")

        data.addRow("AppData folder:", row_app)
        data.addRow("", self.chk_load_cache)
        data.addRow("Save cache every:", self.sb_save_every)

        # ---- Performance tab
        perf_tab = QWidget(); perf = QFormLayout(perf_tab)
        self.sb_conc = QSpinBox(); self.sb_conc.setRange(1, 16); self.sb_conc.setValue(int(settings.get("concurrency", 4)))
        self.sb_auto = QSpinBox(); self.sb_auto.setRange(0, 3600); self.sb_auto.setSuffix(" sec"); self.sb_auto.setValue(int(settings.get("auto_refresh_sec", 0)))
        self.sb_rate_cap = QSpinBox(); self.sb_rate_cap.setRange(10, 200); self.sb_rate_cap.setValue(int(settings.get("rate_max_per_min", 100)))
        self.sb_min_interval = QSpinBox(); self.sb_min_interval.setRange(0, 2000); self.sb_min_interval.setSuffix(" ms"); self.sb_min_interval.setValue(int(settings.get("min_interval_ms", settings.get("req_delay_ms", 620))))
        perf.addRow("Concurrency:", self.sb_conc)
        perf.addRow("Auto refresh:", self.sb_auto)
        perf.addRow("Rate cap (per minute):", self.sb_rate_cap)
        perf.addRow("Min interval between calls:", self.sb_min_interval)

        # ---- Retry/Backoff tab
        back_tab = QWidget(); back = QFormLayout(back_tab)
        self.sb_max_retries = QSpinBox(); self.sb_max_retries.setRange(1, 12); self.sb_max_retries.setValue(int(settings.get("max_retries", 8)))
        self.sb_backoff_base = QSpinBox(); self.sb_backoff_base.setRange(0, 2000); self.sb_backoff_base.setSuffix(" ms"); self.sb_backoff_base.setValue(int(settings.get("backoff_base_ms", 600)))
        self.sb_backoff_cap = QSpinBox(); self.sb_backoff_cap.setRange(1, 30); self.sb_backoff_cap.setSuffix(" s"); self.sb_backoff_cap.setValue(int(settings.get("backoff_cap_s", 8)))
        self.chk_retry_after = QCheckBox("Honor Retry-After header"); self.chk_retry_after.setChecked(bool(settings.get("respect_retry_after", True)))
        back.addRow("Max retries:", self.sb_max_retries)
        back.addRow("Backoff base:", self.sb_backoff_base)
        back.addRow("Backoff cap:", self.sb_backoff_cap)
        back.addRow("", self.chk_retry_after)

        # ---- Help tab
        help_tab = QWidget(); v = QVBoxLayout(help_tab); v.setContentsMargins(0,0,0,0)
        help_box = QTextBrowser()
        help_box.setOpenExternalLinks(True)
        help_box.setHtml(
            "<style>a { color: #4da3ff; }</style>"
            "<p><b>Tips</b></p>"
            "<ul>"
            "<li><i>Rate cap</i> + <i>Min interval</i> keep you under Torn's 100/min global limit.</li>"
            "<li>Retries/backoff apply when the API returns <i>429 Too Many Requests</i> or transient errors.</li>"
            "<li>Cache is updated periodically to speed up the next launch.</li>"
            "</ul>"
        )
        v.addWidget(help_box)

        for (name, w) in (("API", api_tab), ("Data & Cache", data_tab), ("Performance", perf_tab), ("Retry & Backoff", back_tab), ("Help", help_tab)):
            tabs.addTab(w, name)

        # ---- footer buttons
        btns = QWidget(); hb = QHBoxLayout(btns); hb.setContentsMargins(0,0,0,0)
        hb.addStretch(1)
        btn_reset = QPushButton("Reset values")
        btn_save = QPushButton("Save")
        btn_cancel = QPushButton("Cancel")
        hb.addWidget(btn_reset); hb.addWidget(btn_save); hb.addWidget(btn_cancel)

        root = QVBoxLayout(self)
        root.addWidget(tabs)
        root.addWidget(btns)

        # wire
        btn_reset.clicked.connect(self._reset_numbers)
        btn_save.clicked.connect(self._save)
        btn_cancel.clicked.connect(self.reject)

    def _pick_targets(self):
        p, _ = QFileDialog.getOpenFileName(self, "Pick targets JSON", "", "JSON (*.json)")
        if p: self.ed_targets.setText(p)

    def _open_appdata(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(get_appdata_dir()))

    def _reset_numbers(self):
        self.sb_conc.setValue(4)
        self.sb_auto.setValue(0)
        self.sb_rate_cap.setValue(100)
        self.sb_min_interval.setValue(620)
        self.sb_save_every.setValue(20)
        self.sb_max_retries.setValue(8)
        self.sb_backoff_base.setValue(600)
        self.sb_backoff_cap.setValue(8)
        self.chk_retry_after.setChecked(True)

    def _save(self):
        self.saved.emit({
            "api_key": self.ed_api.text().strip(),
            "targets_file": self.ed_targets.text().strip() or "target.json",
            "concurrency": int(self.sb_conc.value()),
            "auto_refresh_sec": int(self.sb_auto.value()),
            "rate_max_per_min": int(self.sb_rate_cap.value()),
            "min_interval_ms": int(self.sb_min_interval.value()),
            "load_cache_at_start": True if self._get_chk(self.chk_load_cache) else False,
            "save_cache_every": int(self.sb_save_every.value()),
            "max_retries": int(self.sb_max_retries.value()),
            "backoff_base_ms": int(self.sb_backoff_base.value()),
            "backoff_cap_s": int(self.sb_backoff_cap.value()),
            "respect_retry_after": True if self._get_chk(self.chk_retry_after) else False,
            "req_delay_ms": int(self.sb_min_interval.value()),
        })
        self.accept()

    def _get_chk(self, cb: QCheckBox) -> bool:
        try:
            return bool(cb.isChecked())
        except Exception:
            return False

# ======================================================================
#                           IGNORE DIALOGS
# ======================================================================

class IgnoreDialog(QDialog):
    def __init__(self, ignored: Set[int], infos: List[TargetInfo], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ignored Targets")
        self.setModal(True)
        self.ignored = set(ignored)
        self.info_map: Dict[int, TargetInfo] = {i.user_id: i for i in infos}

        outer = QVBoxLayout(self)

        # Top bar with search
        top = QWidget(); th = QHBoxLayout(top); th.setContentsMargins(0,0,0,0)
        self.search = QLineEdit(placeholderText="Search name or ID…")
        self.search.textChanged.connect(self._filter_table)
        th.addWidget(self.search)
        outer.addWidget(top)

        # Table
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Name", "ID", "Level", "Last Seen"])
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        outer.addWidget(self.table)

        # Footer
        btns = QWidget(); hb = QHBoxLayout(btns); hb.setContentsMargins(0,0,0,0)
        self.btn_open = QPushButton("Open Profile")
        self.btn_un = QPushButton("Unignore Selected")
        self.btn_un_all = QPushButton("Unignore All")
        hb.addWidget(self.btn_open); hb.addWidget(self.btn_un); hb.addWidget(self.btn_un_all); hb.addStretch(1)
        self.btn_import = QPushButton("Import…")
        self.btn_export = QPushButton("Export…")
        self.btn_close = QPushButton("Close")
        hb.addWidget(self.btn_import); hb.addWidget(self.btn_export); hb.addWidget(self.btn_close)
        outer.addWidget(btns)

        self.lbl_count = QLabel()
        outer.addWidget(self.lbl_count)

        # Wire
        self.btn_open.clicked.connect(self._open_profile_selected)
        self.btn_un.clicked.connect(self._unignore_selected)
        self.btn_un_all.clicked.connect(self._unignore_all)
        self.btn_export.clicked.connect(self._export)
        self.btn_import.clicked.connect(self._import)
        self.btn_close.clicked.connect(self.accept)

        self._reload_table()
        self._update_count()

    def _rows_source(self) -> List[TargetInfo]:
        return [self.info_map.get(uid, TargetInfo(user_id=uid)) for uid in sorted(self.ignored)]

    def _reload_table(self):
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)

        data = self._rows_source()
        self.table.setRowCount(0)
        for inf in data:
            r = self.table.rowCount()
            self.table.insertRow(r)

            name = inf.name or "—"
            last = inf.last_action_relative or inf.last_action_status or "—"
            lvl = "" if inf.level is None else str(inf.level)

            it_name = QTableWidgetItem(name)
            it_id = QTableWidgetItem(str(inf.user_id))
            it_level = QTableWidgetItem(lvl)
            it_last = QTableWidgetItem(last)

            it_id.setData(Qt.ItemDataRole.UserRole, int(inf.user_id))
            it_level.setData(Qt.ItemDataRole.UserRole, int(inf.level) if inf.level is not None else -1)

            f = it_name.font(); f.setItalic(True); it_name.setFont(f)

            self.table.setItem(r, 0, it_name)
            self.table.setItem(r, 1, it_id)
            self.table.setItem(r, 2, it_level)
            self.table.setItem(r, 3, it_last)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(was_sorting)
        self._filter_table(self.search.text())

    def _safe_cell_lower(self, row: int, col: int) -> str:
        it = self.table.item(row, col)
        return ((it.text() if it else "") or "").lower()

    def _filter_table(self, text: str):
        t = (text or "").strip().lower()
        for r in range(self.table.rowCount()):
            nm = self._safe_cell_lower(r, 0)
            idt = self._safe_cell_lower(r, 1)
            self.table.setRowHidden(r, not (t in nm or t in idt))

    def _selected_ids(self) -> List[int]:
        ids: List[int] = []
        for idx in self.table.selectionModel().selectedRows():
            it = self.table.item(idx.row(), 1)
            if it and it.text().isdigit():
                ids.append(int(it.text()))
        return ids

    def _open_profile_selected(self):
        ids = self._selected_ids()
        if not ids: return
        QDesktopServices.openUrl(QUrl.fromUserInput(profile_url(ids[0])))

    def _unignore_selected(self):
        for uid in self._selected_ids():
            self.ignored.discard(uid)
        self._reload_table(); self._update_count()

    def _unignore_all(self):
        self.ignored.clear()
        self._reload_table(); self._update_count()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export ignored", "ignored.txt", "Text (*.txt)")
        if not path: return
        with open(path, "w", encoding="utf-8") as f:
            for uid in sorted(self.ignored):
                f.write(f"{uid}\n")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import ignored", "", "Text (*.txt);;All Files (*)")
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.isdigit():
                        uid = int(line)
                        self.ignored.add(uid)
                        if uid not in self.info_map:
                            self.info_map[uid] = TargetInfo(user_id=uid)
            self._reload_table(); self._update_count()
        except Exception:
            pass

    def _update_count(self):
        self.lbl_count.setText(f"Ignored: {len(self.ignored)} target(s)")

class IgnoredPromptDialog(QDialog):
    def __init__(self, name: str, parent=None, open_what: str = "profile"):
        super().__init__(parent)
        self.setWindowTitle("Ignored Target")
        lay = QVBoxLayout(self)

        if open_what == "attack":
            action_text = "open the attack window"
        else:
            action_text = "open the profile"

        lbl = QLabel(
            f"<b>{name}</b> is currently <span style='color:#ff8080'>ignored</span>.<br/>"
            f"Do you want to unignore and {action_text}?"
        )
        lay.addWidget(lbl)
        btns = QHBoxLayout()
        self.btn_unignore_open = QPushButton("Unignore & Open")
        self.btn_open_once = QPushButton("Open Once")
        self.btn_cancel = QPushButton("Cancel")
        btns.addStretch(1)
        btns.addWidget(self.btn_unignore_open)
        btns.addWidget(self.btn_open_once)
        btns.addWidget(self.btn_cancel)
        lay.addLayout(btns)
        self.result_choice = "cancel"
        self.btn_unignore_open.clicked.connect(lambda: (setattr(self, "result_choice", "unignore_open"), self.accept()))
        self.btn_open_once.clicked.connect(lambda: (setattr(self, "result_choice", "open_once"), self.accept()))
        self.btn_cancel.clicked.connect(self.reject)

# ======================================================================
#                            ABOUT DIALOG
# ======================================================================

class AboutDialog(QDialog):
    """
    Transparent header, crisp logo (ICO preferred), robust GitHub version check.
    When a newer version is found, shows a green outline pill (transparent inside)
    and enables 'Update'. Uses a queued signal to update the UI reliably.
    """

    updateFound = pyqtSignal(str)  # emitted from worker thread → handled in UI thread

    GITHUB_RAW = "https://raw.githubusercontent.com/skillerious/TornTargetTracker/main/assets/version.json"
    GITHUB_API = "https://api.github.com/repos/skillerious/TornTargetTracker/contents/assets/version.json"
    GITHUB_RAW_FALLBACK = "https://github.com/skillerious/TornTargetTracker/raw/main/assets/version.json"
    GITHUB_BLOB_PAGE = "https://github.com/skillerious/TornTargetTracker/blob/main/assets/version.json"
    RELEASES_URL = "https://github.com/skillerious/TornTargetTracker/releases"

    # Optional: env var (or hardcode if you insist). Keep read-only!
    GITHUB_TOKEN = os.environ.get("TTT_GITHUB_TOKEN", "").strip()

    def __init__(self, parent=None, app_name: str = "Target Tracker", app_version: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle(f"About {app_name}")
        self.setModal(True)
        self.setMinimumWidth(580)

        # ---------- logging ----------
        self._log = logging.getLogger("TargetTracker.About")
        if not self._log.handlers:
            h = logging.StreamHandler()
            h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
            self._log.addHandler(h)
            self._log.setLevel(logging.INFO)

        # ---------- runtime info ----------
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

        self.app_name = app_name
        self.py_ver = sys.version.split(" ")[0]
        self.pyqt_ver = PYQT_VERSION_STR
        self.qt_ver = QT_VERSION_STR

        # ---------- helpers ----------
        def _json_version_from(path: str) -> Optional[str]:
            try:
                if not os.path.exists(path):
                    return None
                with open(path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                v = obj.get("version")
                return str(v).strip() if v else None
            except Exception:
                return None

        def _detect_local_version() -> str:
            here = os.path.abspath(os.path.dirname(__file__))
            for p in (
                os.path.join(here, "assets", "version.json"),
                os.path.join(here, "version.json"),
                os.path.join(get_appdata_dir(), "version.json"),
            ):
                v = _json_version_from(p)
                if v:
                    return v
            try:
                from storage import load_settings
                st = load_settings()
                tf = st.get("targets_file")
                if tf and os.path.exists(tf):
                    v = _json_version_from(tf)
                    if v:
                        return v
            except Exception:
                pass
            return app_version or "—"

        def _parse_version(s: str) -> tuple:
            if not s:
                return ()
            s = s.strip().lower()
            if s.startswith("v"):
                s = s[1:]
            cleaned = "".join(ch for ch in s if ch.isdigit() or ch == ".")
            parts = [p for p in cleaned.split(".") if p]
            nums = []
            for p in parts[:4]:
                try:
                    nums.append(int(p))
                except Exception:
                    nums.append(0)
            while len(nums) < 3:
                nums.append(0)
            return tuple(nums)

        def _is_newer(remote: str, local: str) -> bool:
            r, l = _parse_version(remote), _parse_version(local)
            newer = r > l
            self._log.info("Compare versions: remote=%s parsed=%s  local=%s parsed=%s  -> newer=%s",
                           remote, r, local, l, newer)
            return newer

        self.local_version = _detect_local_version()
        self._log.info("About opened: local_version=%s Python=%s PyQt=%s Qt=%s",
                       self.local_version, self.py_ver, self.pyqt_ver, self.qt_ver)

        # ---------- header (transparent) ----------
        self.header = QWidget(self)
        self.header.setObjectName("aboutHeader")
        self.header.setStyleSheet("""
            #aboutHeader { background: transparent; }

            /* Blue version pill — same geometry as update pill */
            QWidget#pillBlue {
                background: transparent;
                border: 1px solid #3d5371;
                border-radius: 11px;
            }
            QLabel#pillBlueText { color:#bcd6ff; font-size:12px; background: transparent; }

            /* Green update pill */
            QWidget#pill {
                background: transparent;               /* fully transparent interior */
                border: 1px solid rgba(46,125,50,0.85);/* green outline */
                border-radius: 11px;
            }
            QLabel#pillText { color:#d7ffe3; font-size:12px; background: transparent; }
        """)
        h = QHBoxLayout(self.header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)

        # crisp logo: prefer ICO, then PNG
        pm_logo = QPixmap()
        ico_path = asset_path(os.path.join("assets", "logo.ico"))
        png_path = asset_path(os.path.join("assets", "logo.png"))
        logo_used = None
        if os.path.exists(ico_path):
            ic = QIcon(ico_path)
            pm_logo = ic.pixmap(QSize(64, 64))
            if pm_logo.isNull():
                pm_logo = QPixmap(ico_path)
            logo_used = ico_path
        elif os.path.exists(png_path):
            pm_logo = QPixmap(png_path)
            logo_used = png_path

        logo_label = QLabel()
        if not pm_logo.isNull():
            pm_logo = pm_logo.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            logo_label.setPixmap(pm_logo)
            h.addWidget(logo_label, 0, Qt.AlignmentFlag.AlignTop)
        self._log.info("About logo: using %s", logo_used or "none")

        # title
        title_box = QVBoxLayout(); title_box.setSpacing(2)
        self.lbl_title = QLabel(f"<span style='font-size:20px; font-weight:600;'>{app_name}</span>")
        self.lbl_sub = QLabel("A Torn.com target list viewer")
        self.lbl_sub.setStyleSheet("color:#b8c0cc;")
        title_box.addWidget(self.lbl_title); title_box.addWidget(self.lbl_sub)
        h.addLayout(title_box, 1); h.addStretch(1)

        # Version pill (match height/padding of update pill)
        self.version_pill = QWidget()
        self.version_pill.setObjectName("pillBlue")
        self.version_pill.setAutoFillBackground(False)
        vp_l = QHBoxLayout(self.version_pill)
        vp_l.setContentsMargins(10, 3, 10, 3)   # same padding as update pill
        vp_l.setSpacing(6)
        self._version_text = QLabel(f"Version {self.local_version}")
        self._version_text.setObjectName("pillBlueText")
        vp_l.addWidget(self._version_text)
        h.addWidget(self.version_pill, 0, Qt.AlignmentFlag.AlignTop)

        # green pill (transparent interior) for updates
        def _green_dot_pm(sz=12):
            pm = QPixmap(sz, sz); pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            color = QColor(72, 187, 120)
            p.setBrush(QBrush(color)); p.setPen(QPen(color, 1))
            r = sz - 2; p.drawEllipse(1, 1, r, r); p.end()
            return pm

        self.update_pill = QWidget()
        self.update_pill.setObjectName("pill")
        self.update_pill.setAutoFillBackground(False)
        self.update_pill.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        pill_l = QHBoxLayout(self.update_pill)
        pill_l.setContentsMargins(10, 3, 10, 3)
        pill_l.setSpacing(6)

        pill_icon = QLabel(); pill_icon.setPixmap(_green_dot_pm(14))
        self._pill_text = QLabel("Update available")
        self._pill_text.setObjectName("pillText")
        self._pill_text.setWordWrap(False)

        pill_l.addWidget(pill_icon); pill_l.addWidget(self._pill_text)
        self.update_pill.setVisible(False)
        h.addWidget(self.update_pill, 0, Qt.AlignmentFlag.AlignTop)

        # ---------- body ----------
        info = QTextBrowser(self)
        info.setOpenExternalLinks(True)
        info.setStyleSheet("QTextBrowser { border: none; }")
        info.setHtml(f"""
            <style>
            a {{ color: #4da3ff; text-decoration:none; }}
            a:hover {{ text-decoration: underline; }}
            .muted {{ color:#b8c0cc; }}
            table {{ border-collapse: collapse; width:100%; }}
            td {{ padding: 4px 8px; vertical-align: top; }}
            .k {{ color:#9bb3c7; width:170px; padding-right:14px; white-space:nowrap; }}
            </style>

            <p>{app_name} displays Torn user info (name, level, status, faction, last action) for a list of targets,
            with cached startup, filters, and an ignore list.</p>

            <table>
              <tr><td class="k">Developer</td>
                  <td>Skillerious — <a href="https://github.com/skillerious">GitHub</a></td></tr>
              <tr><td class="k">Torn Profile</td>
                  <td><a href="https://www.torn.com/profiles.php?XID=3212954">Open</a></td></tr>
              <tr><td class="k">Environment</td>
                  <td>Python {self.py_ver} • PyQt {self.pyqt_ver} • Qt {self.qt_ver}</td></tr>
              <tr><td class="k">Libraries</td>
                  <td><a href="https://qdarkstylesheet.readthedocs.io">QDarkStyle</a>, PyQt6</td></tr>
            </table>

            <p class="muted" style="margin-top:10px;">
            Torn© is a trademark of Torn EQ Ltd. This tool is an independent, fan-made viewer and is not affiliated
            with Torn or its developers.
            </p>
        """)

        sep = QFrame(self); sep.setFrameShape(QFrame.Shape.HLine); sep.setFrameShadow(QFrame.Shadow.Sunken)

        # ---------- footer ----------
        def _btn(text: str, icon_name: Optional[str], url: Optional[str]):
            b = QPushButton(text)
            if icon_name: b.setIcon(icon(icon_name))
            if url: b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
            return b

        btn_github = _btn("GitHub", "github", "https://github.com/skillerious")
        btn_torn   = _btn("Torn Profile", "link", "https://www.torn.com/profiles.php?XID=3212954")

        # Renamed to "Update" (was "Release Notes")
        self.btn_release = _btn("Update", "update", self.RELEASES_URL)
        self.btn_release.setVisible(False)

        copy_btn = QPushButton("Copy Diagnostics")
        def _copy_diag():
            try:
                QApplication.clipboard().setText(
                    f"{app_name} {self.local_version}  |  Python {self.py_ver}  •  PyQt {self.pyqt_ver}  •  Qt {self.qt_ver}"
                )
            except Exception:
                pass
        copy_btn.clicked.connect(_copy_diag)

        close_btn = QPushButton("Close"); close_btn.clicked.connect(self.accept)

        btns = QHBoxLayout(); btns.setContentsMargins(0, 0, 0, 0); btns.setSpacing(8)
        btns.addStretch(1)
        for b in (btn_github, btn_torn, self.btn_release, copy_btn, close_btn): btns.addWidget(b)

        # ---------- root layout ----------
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14); root.setSpacing(12)
        root.addWidget(self.header); root.addWidget(info); root.addWidget(sep); root.addLayout(btns)

        # Connect the queued signal for reliable UI updates
        self.updateFound.connect(self._apply_update_visuals)

        # start async update check
        QTimer.singleShot(0, lambda: self._check_for_update(_is_newer))

    # ---------- update logic ----------
    def _check_for_update(self, is_newer_fn):
        import threading, base64, re, time
        import requests

        headers = {
            "User-Agent": f"{self.app_name}-AboutDialog",
            "Accept": "application/vnd.github+json",
        }
        if self.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {self.GITHUB_TOKEN}"
            self._log.info("GitHub token enabled (len=%d)", len(self.GITHUB_TOKEN))

        def _fetch_remote_version() -> Optional[str]:
            # 1) RAW (cache-busted)
            try:
                url = f"{self.GITHUB_RAW}?_ts={int(time.time())}"
                self._log.info("Fetch RAW: %s", url)
                r = requests.get(url, headers=headers, timeout=5)
                self._log.info("RAW status=%s len=%s", r.status_code, len(r.text or ""))
                if r.ok and r.text:
                    obj = r.json()
                    v = obj.get("version")
                    if v: return str(v).strip()
            except Exception as e:
                self._log.info("RAW fetch err: %s", e)

            # 2) Contents API
            try:
                self._log.info("Fetch API: %s", self.GITHUB_API)
                r = requests.get(self.GITHUB_API, headers=headers, timeout=6)
                self._log.info("API status=%s", r.status_code)
                if r.ok:
                    data = r.json()
                    if isinstance(data, dict) and "content" in data:
                        raw = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
                        obj = json.loads(raw)
                        v = obj.get("version")
                        if v: return str(v).strip()
            except Exception as e:
                self._log.info("API fetch err: %s", e)

            # 3) RAW fallback
            try:
                self._log.info("Fetch RAW Fallback: %s", self.GITHUB_RAW_FALLBACK)
                r = requests.get(self.GITHUB_RAW_FALLBACK, headers=headers, timeout=6, allow_redirects=True)
                self._log.info("RAW Fallback status=%s", r.status_code)
                if r.ok and r.text:
                    obj = r.json()
                    v = obj.get("version")
                    if v: return str(v).strip()
            except Exception as e:
                self._log.info("RAW fallback err: %s", e)

            # 4) Parse blob page
            try:
                self._log.info("Fetch BLOB page: %s", self.GITHUB_BLOB_PAGE)
                r = requests.get(self.GITHUB_BLOB_PAGE, headers=headers, timeout=7)
                self._log.info("BLOB status=%s len=%s", r.status_code, len(r.text or ""))
                if r.ok and r.text:
                    m = re.search(r'\"version\"\s*:\s*\"([0-9][^\"]+)\"', r.text)
                    if m: return m.group(1).strip()
            except Exception as e:
                self._log.info("BLOB parse err: %s", e)

            return None

        def _worker():
            remote = _fetch_remote_version()
            self._log.info("Remote version fetched: %r (local=%r)", remote, self.local_version)
            if not remote:
                return
            try:
                if is_newer_fn(remote, self.local_version):
                    self._log.info("Newer version detected -> emitting signal")
                    self.updateFound.emit(remote)  # queued across threads
                else:
                    self._log.info("No update available")
            except Exception as e:
                self._log.info("Version compare failed: %s", e)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_update_visuals(self, remote_version: str):
        """UI-thread: show transparent green pill and enable Update button."""
        try:
            text = f"Update {remote_version} available"
            self._pill_text.setText(text)

            # ensure text never clips
            fm = self._pill_text.fontMetrics()
            min_w = fm.horizontalAdvance(text) + 28  # text + icon + paddings
            self.update_pill.setMinimumWidth(min_w)
            self.update_pill.adjustSize()
            self.update_pill.setVisible(True)

            self.btn_release.setVisible(True)

            # refresh layout immediately
            self.header.layout().invalidate()
            self.header.adjustSize()
            self.update_pill.repaint()
            self.repaint()

            self._log.info("Update UI applied (pill shown, min_w=%d).", min_w)
        except Exception as e:
            self._log.info("Apply update visuals failed: %s", e)



# ======================================================================
#                           TOOLBAR + MAIN VIEW
# ======================================================================

class StyledToolBar(QToolBar):
    def __init__(self):
        super().__init__()
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(20, 20))
        self.setStyleSheet("""
            QToolBar { background: transparent; border: 0; padding: 2px; spacing: 6px; }
            QToolButton { background: transparent; border: 0; padding: 6px; border-radius: 8px; }
            QToolButton:hover { background: rgba(255,255,255,0.06); }
            QToolButton:pressed { background: rgba(255,255,255,0.10); }
            QToolBar::separator { background: rgba(255,255,255,0.10); width: 1px; margin: 6px 4px; }
        """)

class MainView(QWidget):
    request_refresh = pyqtSignal()
    request_export = pyqtSignal()
    request_open_settings = pyqtSignal()
    request_manage_ignore = pyqtSignal()
    request_load_targets = pyqtSignal()
    request_show_about = pyqtSignal()
    request_add_targets = pyqtSignal(list)
    request_remove_targets = pyqtSignal(list)

    ignore_ids = pyqtSignal(list)
    unignore_ids = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.model = TargetsModel()
        self.proxy = FilterProxy(); self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self._ignored_ids: Set[int] = set()
        self._did_size_cols = False  # avoid resizing columns on each refresh

        # fetching + coalesced sort
        self._fetching = False
        self._resort_timer = QTimer(self)
        self._resort_timer.setSingleShot(True)
        self._resort_timer.setInterval(300)  # coalesce updates within 300ms
        self._resort_timer.timeout.connect(self._flush_resort)

        # improved search bar
        self.search_bar = SearchBar()
        self.search_bar.queryChanged.connect(self.proxy.set_search_query)

        self.chk_ok = QCheckBox("Okay"); self.chk_ok.setChecked(True)
        self.chk_hosp = QCheckBox("Hospital"); self.chk_hosp.setChecked(True)
        self.chk_jail = QCheckBox("Jail"); self.chk_jail.setChecked(True)
        self.chk_fed = QCheckBox("Federal"); self.chk_fed.setChecked(True)
        self.chk_trav = QCheckBox("Traveling"); self.chk_trav.setChecked(True)
        self.chk_hide_ignored = QCheckBox("Hide Ignored"); self.chk_hide_ignored.setChecked(False)

        for chk in (self.chk_ok, self.chk_hosp, self.chk_jail, self.chk_fed, self.chk_trav, self.chk_hide_ignored):
            chk.stateChanged.connect(self._on_filter_flags)

        self.sb_lvl_min = QSpinBox(); self.sb_lvl_min.setRange(0, 100); self.sb_lvl_min.setValue(0)
        self.sb_lvl_max = QSpinBox(); self.sb_lvl_max.setRange(0, 100); self.sb_lvl_max.setValue(100)
        self.sb_lvl_min.valueChanged.connect(self._on_level_bounds)
        self.sb_lvl_max.valueChanged.connect(self._on_level_bounds)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(2, Qt.SortOrder.DescendingOrder)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)

        # smoother UX when columns resized: stretch last column
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionsClickable(True)

        # DOUBLE-CLICK => OPEN ATTACK WINDOW
        self.table.doubleClicked.connect(self._open_attack)

        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)

        # actions
        self.act_ignore = QAction(icon("block"), "Ignore Selected", self)
        _unblock_ic = icon("unblock")
        self.act_unignore = QAction(_unblock_ic if not _unblock_ic.isNull() else icon("block"), "Unignore Selected", self)
        self.act_remove = QAction(icon("delete"), "Remove Selected", self); self.act_remove.setShortcut("Del")
        self.act_ignore.triggered.connect(self._emit_ignore_selected)
        self.act_unignore.triggered.connect(self._emit_unignore_selected)
        self.act_remove.triggered.connect(self._emit_remove_selected)

        # toolbar
        tb = StyledToolBar()
        act_refresh = tb.addAction(icon("refresh"), "Refresh"); act_refresh.setShortcut("Ctrl+R")
        act_refresh.triggered.connect(self.request_refresh.emit)
        act_export = tb.addAction(icon("export"), "Export CSV"); act_export.setShortcut("Ctrl+E")
        act_export.triggered.connect(self.request_export.emit)
        tb.addSeparator()
        act_load = tb.addAction(icon("folder"), "Load Targets JSON"); act_load.setShortcut("Ctrl+O")
        act_load.triggered.connect(self.request_load_targets.emit)
        act_add = tb.addAction(icon("add"), "Add Targets…"); act_add.setShortcut("Ctrl+N")
        act_add.triggered.connect(self._show_add_dialog)
        act_del = tb.addAction(icon("delete"), "Remove Selected"); act_del.setShortcut("Del")
        act_del.triggered.connect(self._emit_remove_selected)
        tb.addSeparator()
        act_ignore_mgr = tb.addAction(icon("block"), "Ignored…"); act_ignore_mgr.triggered.connect(self.request_manage_ignore.emit)
        tb.addSeparator()
        act_settings = tb.addAction(icon("settings"), "Settings…"); act_settings.setShortcut("Ctrl+,")
        act_settings.triggered.connect(self.request_open_settings.emit)
        act_about = tb.addAction(icon("info"), "About"); act_about.triggered.connect(self.request_show_about.emit)

        # filters row
        frow = QWidget(); fh = QHBoxLayout(frow); fh.setContentsMargins(0,0,0,0)
        fh.addWidget(QLabel("Filters:"))
        for w in (self.chk_ok, self.chk_hosp, self.chk_jail, self.chk_fed, self.chk_trav, self.chk_hide_ignored):
            fh.addWidget(w)
        fh.addSpacing(12)
        fh.addWidget(QLabel("Level"))
        fh.addWidget(self.sb_lvl_min); fh.addWidget(QLabel("to")); fh.addWidget(self.sb_lvl_max)
        fh.addStretch(1); fh.addWidget(self.search_bar)

        lay = QVBoxLayout(self)
        lay.addWidget(tb); lay.addWidget(frow); lay.addWidget(self.table)

    # ---- fetching state + coalesced sorting ----
    def set_fetching(self, fetching: bool):
        """Called by controller at start/end of a batch fetch."""
        if fetching == self._fetching:
            return
        self._fetching = fetching
        if fetching:
            # coalesce sorts while streaming updates
            self.proxy.setDynamicSortFilter(False)
        else:
            # final resort + re-enable dynamics
            self._flush_resort()
            self.proxy.setDynamicSortFilter(True)

    def _schedule_resort(self):
        if not self._resort_timer.isActive():
            self._resort_timer.start()

    def _flush_resort(self):
        """Re-sort once, restoring selection and scroll position."""
        header: QHeaderView = self.table.horizontalHeader()
        col = header.sortIndicatorSection()
        order = header.sortIndicatorOrder()
        prev_sel = self._selected_ids()
        vbar = self.table.verticalScrollBar()
        prev_scroll = vbar.value()

        self.table.setUpdatesEnabled(False)
        self.proxy.invalidate()
        self.proxy.sort(col if col >= 0 else 2, order if order is not None else Qt.SortOrder.DescendingOrder)
        self.table.setUpdatesEnabled(True)

        self._restore_selection(prev_sel)
        vbar.setValue(prev_scroll)

    # ---- selection helpers to persist highlights while refreshing ----
    def _selected_ids(self) -> List[int]:
        ids: List[int] = []
        sel = self.table.selectionModel()
        if not sel:
            return ids
        for proxy_idx in sel.selectedRows():
            src = self.proxy.mapToSource(proxy_idx)
            row = self.model.row(src.row())
            ids.append(int(row.user_id))
        return ids

    def _restore_selection(self, ids: List[int]):
        sm = self.table.selectionModel()
        if not sm or not ids:
            return
        sm.clearSelection()
        id_set = set(ids)
        uid_to_row: Dict[int, int] = {}
        for i, r in enumerate(getattr(self.model, "_rows", [])):
            uid_to_row[int(r.user_id)] = i
        first_idx: Optional[QModelIndex] = None
        for uid in id_set:
            srow = uid_to_row.get(uid)
            if srow is None:
                continue
            src_idx = self.model.index(srow, 0)
            proxy_idx = self.proxy.mapFromSource(src_idx)
            if proxy_idx.isValid():
                if first_idx is None:
                    first_idx = proxy_idx
                sm.select(proxy_idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        if first_idx is not None:
            self.table.setCurrentIndex(first_idx)

    # ---- batch-friendly API ----
    def update_or_insert(self, info: TargetInfo):
        """Used by controller per completed fetch item—no full reset."""
        self.model.upsert(info)
        if self._fetching:
            self._schedule_resort()

    def set_rows(self, rows: List[TargetInfo]):
        prev_sel = self._selected_ids()
        sb = self.table.verticalScrollBar()
        scroll_val = sb.value()

        self.model.set_rows(rows)

        if not getattr(self, "_did_size_cols", False):
            self.table.resizeColumnsToContents()
            self.table.horizontalHeader().setStretchLastSection(True)
            self._did_size_cols = True

        self._restore_selection(prev_sel)
        sb.setValue(scroll_val)

        hdr = self.table.horizontalHeader()
        self.proxy.sort(hdr.sortIndicatorSection(), hdr.sortIndicatorOrder())

    def set_ignored(self, ids: Set[int]):
        self._ignored_ids = set(ids)
        self.model.set_ignored(ids)
        self.proxy.set_ignored(ids)

    def _on_filter_flags(self, _=None):
        self.proxy.set_flags(
            show_okay=self.chk_ok.isChecked(),
            show_hospital=self.chk_hosp.isChecked(),
            show_jail=self.chk_jail.isChecked(),
            show_federal=self.chk_fed.isChecked(),
            show_traveling=self.chk_trav.isChecked(),
            hide_ignored=self.chk_hide_ignored.isChecked(),
        )

    def _on_level_bounds(self, _=None):
        lo = min(self.sb_lvl_min.value(), self.sb_lvl_max.value())
        hi = max(self.sb_lvl_min.value(), self.sb_lvl_max.value())
        self.sb_lvl_min.blockSignals(True); self.sb_lvl_max.blockSignals(True)
        self.sb_lvl_min.setValue(lo); self.sb_lvl_max.setValue(hi)
        self.sb_lvl_min.blockSignals(False); self.sb_lvl_max.blockSignals(False)
        self.proxy.set_level_bounds(lo, hi)

    def _index_to_row(self, idx: QModelIndex) -> TargetInfo:
        src = self.proxy.mapToSource(idx); return self.model.row(src.row())

    # ---- openers ----
    def _open_profile(self, idx: QModelIndex):
        r = self._index_to_row(idx)
        url = QUrl.fromUserInput(profile_url(r.user_id))
        if r.user_id in self._ignored_ids:
            dlg = IgnoredPromptDialog(r.name or str(r.user_id), self, open_what="profile")
            if dlg.exec():
                if dlg.result_choice == "unignore_open":
                    self.unignore_ids.emit([r.user_id])
                    QDesktopServices.openUrl(url)
                elif dlg.result_choice == "open_once":
                    QDesktopServices.openUrl(url)
            return
        QDesktopServices.openUrl(url)

    def _open_attack(self, idx: QModelIndex):
        r = self._index_to_row(idx)
        url = QUrl.fromUserInput(attack_url(r.user_id))
        if r.user_id in self._ignored_ids:
            dlg = IgnoredPromptDialog(r.name or str(r.user_id), self, open_what="attack")
            if dlg.exec():
                if dlg.result_choice == "unignore_open":
                    self.unignore_ids.emit([r.user_id])
                    QDesktopServices.openUrl(url)
                elif dlg.result_choice == "open_once":
                    QDesktopServices.openUrl(url)
            return
        QDesktopServices.openUrl(url)

    # ---- context menu ----
    def _menu(self, pos):
        idx = self.table.indexAt(pos)
        if idx.isValid():
            self.table.setFocus()
            if not self.table.selectionModel().isSelected(idx):
                self.table.clearSelection()
                self.table.selectRow(idx.row())

        menu = QMenu(self)
        if idx.isValid():
            r = self._index_to_row(idx)

            act_open_profile = QAction(icon("profile"), "Open Profile", self)
            act_open_profile.triggered.connect(lambda: self._open_profile(idx))

            act_open_attack = QAction(icon("attack"), "Open attack window", self)
            act_open_attack.triggered.connect(lambda: self._open_attack(idx))

            act_copy_profile = QAction(icon("copy"), "Copy Profile URL", self)
            act_copy_profile.triggered.connect(lambda: self._copy(profile_url(r.user_id)))

            act_copy_attack = QAction(icon("copy"), "Copy Attack URL", self)
            act_copy_attack.triggered.connect(lambda: self._copy(attack_url(r.user_id)))

            menu.addAction(act_open_profile)
            menu.addAction(act_open_attack)
            menu.addSeparator()
            menu.addAction(act_copy_profile)
            menu.addAction(act_copy_attack)
            menu.addSeparator()

        menu.addAction(self.act_ignore)
        menu.addAction(self.act_unignore)
        menu.addSeparator()
        menu.addAction(self.act_remove)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _copy(self, text: str):
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
        except Exception:
            pass

    def _emit_ignore_selected(self):
        self.ignore_ids.emit(self._selected_ids())

    def _emit_unignore_selected(self):
        self.unignore_ids.emit(self._selected_ids())

    def _emit_remove_selected(self):
        self.request_remove_targets.emit(self._selected_ids())

    def _show_add_dialog(self):
        dlg = AddTargetsDialog(self)
        dlg.accepted_ids.connect(lambda ids: self.request_add_targets.emit(ids) if ids else None)
        dlg.exec()

# ======================================================================
#                       ADD TARGETS DIALOG
# ======================================================================

class AddTargetsDialog(QDialog):
    accepted_ids = pyqtSignal(list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Targets")
        lay = QVBoxLayout(self)

        help_lbl = QLabel(
            "Paste Torn profile URL(s) or ID(s). Examples:<br>"
            "<code>https://www.torn.com/profiles.php?XID=3212954</code><br>"
            "<code>3212954, 123456</code>"
        )
        help_lbl.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(help_lbl)

        self.ed = QLineEdit()
        self.ed.setPlaceholderText("Paste here… e.g. https://www.torn.com/profiles.php?XID=3212954, 123456")
        lay.addWidget(self.ed)

        self.list_preview = QListWidget(); self.list_preview.setMinimumHeight(140)
        lay.addWidget(self.list_preview)

        self.lbl_count = QLabel("0 ID(s) found"); lay.addWidget(self.lbl_count)

        btns = QHBoxLayout(); btns.addStretch(1)
        self.btn_ok = QPushButton("Add"); self.btn_ok.setEnabled(False)
        btn_cancel = QPushButton("Cancel")
        btns.addWidget(self.btn_ok); btns.addWidget(btn_cancel)
        lay.addLayout(btns)

        self.ed.textChanged.connect(self._reparse)
        self.btn_ok.clicked.connect(self._emit)
        btn_cancel.clicked.connect(self.reject)
        self._ids: List[int] = []

    def _parse_text_ids(self, text: str) -> List[int]:
        text = text.strip(); ids: List[int] = []
        if not text: return ids
        for m in re.findall(r"XID=(\d+)", text, flags=re.IGNORECASE):
            try: ids.append(int(m))
            except ValueError: pass
        for m in re.findall(r"\b(\d{1,10})\b", text):
            try:
                val = int(m)
                if val not in ids: ids.append(val)
            except ValueError: pass
        seen=set(); out=[]
        for i in ids:
            if i not in seen: seen.add(i); out.append(i)
        return out

    def _reparse(self):
        ids = self._parse_text_ids(self.ed.text())
        self._ids = ids
        self.list_preview.clear()
        for i in ids: self.list_preview.addItem(QListWidgetItem(str(i)))
        self.lbl_count.setText(f"{len(ids)} ID(s) found")
        self.btn_ok.setEnabled(len(ids) > 0)

    def _emit(self):
        self.accepted_ids.emit(self._ids); self.accept()
