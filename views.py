from __future__ import annotations

import os

import re

import sys

import json
import html

import threading

import logging

from typing import List, Set, Dict, Optional, Tuple, NamedTuple
try:
    from packaging import version as pkg_version
except Exception:  # packaging optional
    pkg_version = None

from search_bar import SearchBar


import urllib.request
import urllib.error
from urllib.parse import parse_qs, urljoin, urlparse


from PyQt6.QtCore import (
    Qt,
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QVariant,
    QSortFilterProxyModel,
    pyqtSignal,
    QUrl,
    QRectF,
    QSize,
    QTimer,
    QItemSelectionModel,
    QDateTime,
)

from PyQt6.QtGui import (
    QAction,
    QDesktopServices,
    QIcon,
    QFont,
    QPixmap,
    QPainter,
    QPen,
    QBrush,
    QColor,
    QPaintEvent,
)

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableView,
    QLineEdit,
    QToolBar,
    QCheckBox,
    QPushButton,
    QMenu,
    QFileDialog,
    QDialog,
    QFormLayout,
    QSpinBox,
    QLabel,
    QGroupBox,
    QTextBrowser,
    QListWidgetItem,
    QListWidget,
    QTableWidget,
    QTableWidgetItem,
    QFrame,
    QToolButton,
    QComboBox,
    QTabWidget,
    QStyle,
    QHeaderView,
    QApplication,
    QSizePolicy,
    QMessageBox,
)


from models import TargetInfo

from storage import get_appdata_dir


# -----------------------------------------------------------------------------

# Logging (verbose for update checks)

# -----------------------------------------------------------------------------

log = logging.getLogger("TargetTracker.Views")

if not log.handlers:

    _h = logging.StreamHandler()

    _h.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    )

    log.addHandler(_h)

    log.setLevel(logging.INFO)


about_log = logging.getLogger("TargetTracker.About")

if not about_log.handlers:

    _h2 = logging.StreamHandler()

    _h2.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    )

    about_log.addHandler(_h2)

    about_log.setLevel(logging.INFO)


# Optional GitHub token for higher rate limits (Fine-grained PAT â- ' Contents: Read-only)

# NOTE: You asked to hardcode this token. It's embedded below exactly as provided.

VERSION_TRACKER_BASE = "https://skillerious.github.io/Version-Tracker/index.html"
VERSION_APP_ID = "target-tracker"


COLUMNS = [
    "Name",
    "ID",
    "Level",
    "Status",
    "Details",
    "Until",
    "Faction",
    "Last Action",
    "Error",
]


class LastActionPalette(NamedTuple):

    fg: QColor

    bg: QColor

    dot: QColor

    bold: bool

    label: str

    accent_hex: str


# Buckets ordered from freshest to stalest for consistent styling + legend output.

LAST_ACTION_BUCKETS: List[Tuple[str, float, str, int, bool]] = [
    ("Now", 180, "#2dd4bf", 90, True),
    ("<15m", 900, "#22c55e", 80, True),
    ("<1h", 3600, "#fbbf24", 70, False),
    ("<4h", 14400, "#f97316", 60, False),
    ("<12h", 43200, "#38bdf8", 50, False),
    ("<24h", 86400, "#818cf8", 45, False),
    ("1d+", 172800, "#c084fc", 40, False),
    ("3d+", float("inf"), "#94a3b8", 35, False),
]


def _color_with_alpha(hex_code: str, alpha: int) -> QColor:

    color = QColor(hex_code)

    color.setAlpha(max(0, min(255, alpha)))

    return color


def last_action_palette(text: str) -> Optional[LastActionPalette]:

    raw = (text or "").strip()

    if not raw:

        return None

    lowered = raw.lower()

    if lowered in {"-", "n/a", "unknown"}:

        return None

    seconds = TargetsModel._relative_to_seconds(raw)

    if seconds >= 10**8:

        # Treat wildly large values as unknown.

        return None

    # If Torn reports "offline" we bias towards the stalest bucket regardless of seconds.

    if "offline" in lowered:

        seconds = max(seconds, 200000)

    for label, cutoff, accent_hex, bg_alpha, bold in LAST_ACTION_BUCKETS:

        if seconds <= cutoff:

            accent = QColor(accent_hex)

            fg = QColor(accent_hex)

            bg = _color_with_alpha(accent_hex, bg_alpha)

            dot = QColor(accent_hex)

            dot.setAlpha(245)

            return LastActionPalette(
                fg=fg, bg=bg, dot=dot, bold=bold, label=label, accent_hex=accent_hex
            )

    return None


def last_action_legend_html(compact: bool = False) -> str:

    """Return rich text legend markup for last-action buckets."""

    if compact:

        picks = [
            LAST_ACTION_BUCKETS[0],
            LAST_ACTION_BUCKETS[2],
            LAST_ACTION_BUCKETS[-1],
        ]

    else:

        picks = [
            LAST_ACTION_BUCKETS[0],
            LAST_ACTION_BUCKETS[1],
            LAST_ACTION_BUCKETS[2],
            LAST_ACTION_BUCKETS[4],
            LAST_ACTION_BUCKETS[-1],
        ]

    parts = []

    for label, _cutoff, accent, _bg_alpha, _bold in picks:

        parts.append(
            "<span style='display:inline-flex;align-items:center;margin-right:14px;'>"
            f"<span style='width:10px;height:10px;border-radius:5px;background:{accent};"
            "display:inline-block;margin-right:6px;'></span>"
            f"<span style='color:{accent};font-weight:600;'>{label}</span>"
            "</span>"
        )

    return "".join(parts)


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


_ICON_FALLBACKS = {
    "ignore": ("block",),
    "ignore-red": ("block-red",),
    "unignore": ("unblock",),
}


def _icon_path(name: str) -> Optional[str]:

    for key in (name, *_ICON_FALLBACKS.get(name, ())):

        for p in (
            asset_path(os.path.join("assets", f"ic-{key}.svg")),
            asset_path(os.path.join("assets", f"ic-{key}.png")),
            asset_path(f"ic-{key}.svg"),
            asset_path(f"ic-{key}.png"),
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

        self._idx_by_uid: Dict[int, int] = {}  # fast lookup for upserts

    # ---------- helpers ----------

    @staticmethod
    def _relative_to_seconds(text: str) -> int:

        """

        Convert Torn-style 'last action' strings to seconds for stable sorting.

        Examples handled:

          'online', 'idle', 'active', 'unknown', '-'

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

        if s in {"-", "-", "unknown", "n/a"}:

            return 10**9

        s = s.replace("ago", " ").replace(",", " ")

        repl = {
            "minutes": "m",
            "minute": "m",
            "mins": "m",
            "min": "m",
            "m ": "m ",
            "hours": "h",
            "hour": "h",
            "days": "d",
            "day": "d",
            "weeks": "w",
            "week": "w",
            "seconds": "s",
            "second": "s",
        }

        for k, v in repl.items():

            s = s.replace(k, v)

        total = 0

        for val, unit in re.findall(r"(\d+)\s*([wdhms])", s):

            n = int(val)

            if unit == "w":
                total += n * 7 * 24 * 3600

            elif unit == "d":
                total += n * 24 * 3600

            elif unit == "h":
                total += n * 3600

            elif unit == "m":
                total += n * 60

            elif unit == "s":
                total += n

        if total > 0:

            return total

        try:

            return int(s) * 60

        except Exception:

            return 10**9

    @staticmethod
    def _status_rank(chip: str) -> int:

        c = (chip or "").lower()

        if "okay" in c:
            return 0

        if "hospital" in c:
            return 1

        if "jail" in c and "federal" in c:
            return 2

        if "jail" in c:
            return 3

        if "abroad" in c:
            return 4

        if "travel" in c:
            return 5

        return 6

    @staticmethod
    def _status_color(chip: str) -> QColor:
        c = (chip or "").lower()
        if not c:
            return QColor("#78c878")
        if "travel" in c or "abroad" in c:
            return QColor("#5bc6ff")  # cyan-blue for traveling/abroad rows
        if "hospital" in c:
            return QColor("#ff6b6b")  # red for hospital rows
        if "federal" in c and "jail" in c:
            return QColor("#ffe066")  # yellow for federal jail rows
        if "jail" in c:
            return QColor("#ffe066")  # yellow for jail rows
        return QColor("#78c878")  # green for okay/other statuses

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

            self.dataChanged.emit(
                tl,
                br,
                [
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.DecorationRole,
                    Qt.ItemDataRole.ForegroundRole,
                    Qt.ItemDataRole.FontRole,
                    Qt.ItemDataRole.UserRole,
                    Qt.ItemDataRole.ToolTipRole,
                ],
            )

    def set_ignored(self, ids: Set[int]):

        self._ignored = set(ids)

        if self._rows:

            tl = self.index(0, 0)

            br = self.index(max(0, len(self._rows) - 1), self.columnCount() - 1)

            self.dataChanged.emit(
                tl,
                br,
                [
                    Qt.ItemDataRole.DecorationRole,
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.ForegroundRole,
                    Qt.ItemDataRole.FontRole,
                    Qt.ItemDataRole.UserRole,
                ],
            )

    def rowCount(self, parent=QModelIndex()) -> int:

        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:

        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):

        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):

            return COLUMNS[section]

        return QVariant()

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):

        if not index.isValid():

            return QVariant()

        row = index.row()

        if row < 0 or row >= len(self._rows):

            return QVariant()

        r = self._rows[row]

        c = index.column()

        last_rel = r.last_action_relative or ""

        last_status = r.last_action_status or ""

        if last_rel and last_status:

            if last_status.lower() in last_rel.lower():

                last_display = last_rel

            else:

                last_display = f"{last_rel} - {last_status}"

        else:

            last_display = last_rel or last_status

        last_palette = last_action_palette(last_rel or last_status)

        dash = "-"

        # Sort role

        if role == Qt.ItemDataRole.UserRole:

            if c == 0:

                return (r.name or "").lower()

            if c == 1:

                return int(r.user_id)

            if c == 2:

                return int(r.level) if r.level is not None else -1

            if c == 3:

                return self._status_rank(r.status_chip())

            if c == 4:

                return (r.status_desc or "").lower()

            if c == 5:

                return int(r.status_until) if r.status_until else 0

            if c == 6:

                return (r.faction or "").lower()

            if c == 7:

                rel = r.last_action_relative or r.last_action_status or ""

                return self._relative_to_seconds(rel)

            if c == 8:

                return r.error or ""

            return ""

        if role == Qt.ItemDataRole.DecorationRole:

            if c == 0 and r.user_id in self._ignored:

                return icon("block-red")

        if role == Qt.ItemDataRole.ForegroundRole:

            if r.user_id in self._ignored:

                return QBrush(QColor(150, 150, 150))

            status_color = self._status_color(r.status_chip())

            if status_color is not None:

                return QBrush(status_color)

        if role == Qt.ItemDataRole.FontRole:

            if r.user_id in self._ignored:

                f = QFont()

                f.setItalic(True)

                return f

            if c == 7 and last_palette and last_palette.bold:

                f = QFont()

                f.setBold(True)

                return f

        if role == Qt.ItemDataRole.DisplayRole:

            if c == 0:

                return r.name or dash

            if c == 1:

                return str(r.user_id)

            if c == 2:

                return dash if r.level is None else str(r.level)

            if c == 3:

                return r.status_chip() or dash

            if c == 4:

                return r.status_desc or dash

            if c == 5:

                return r.until_human() or dash

            if c == 6:

                return r.faction or dash

            if c == 7:

                return last_display or dash

            if c == 8:

                return r.error or ""

        if role == Qt.ItemDataRole.TextAlignmentRole and c in (1, 2, 5):

            return Qt.AlignmentFlag.AlignCenter

        if role == Qt.ItemDataRole.ToolTipRole:

            status_chip = r.status_chip() or dash

            status_desc = r.status_desc or dash

            tip = [
                f"<b>{r.name or dash}</b> [{r.user_id}]",
                f"Level: {r.level if r.level is not None else dash}",
                f"Status: {status_chip} - {status_desc}",
                f"Until: {r.until_human() or dash}",
                f"Faction: {r.faction or dash}",
                f"Last Action: {last_display or dash}",
            ]

            if last_palette:

                tip.append(
                    f"<b>Recency:</b> <span style='color:{last_palette.dot.name()};'>{last_palette.label}</span>"
                )

            if r.error:

                tip.append(f"<b>Error:</b> {r.error}")

            if r.user_id in self._ignored:

                tip.append("<b>Ignored:</b> Yes")

            return "<br>".join(tip)

        return QVariant()

    def row(self, idx: int) -> TargetInfo:

        return self._rows[idx]


# ======================================================================

#                           FILTER PROXY

# ======================================================================


class FilterProxy(QSortFilterProxyModel):
    def __init__(self):

        super().__init__()

        self.setDynamicSortFilter(False)

        self.search_query = {
            "text": "",
            "mode": "all",
            "regex": False,
            "case_sensitive": False,
        }

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

        self.level_min = int(lo)
        self.level_max = int(hi)

        self.invalidateFilter()

    # util

    def _match_text(self, text: str, hay: str, regex: bool, case: bool) -> bool:

        if hay is None:
            return False

        if not text:
            return True

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

                if not self._match_text(txt, str(r.user_id), regex, case):
                    return False

            elif mode == "name":

                if not self._match_text(txt, r.name or "", regex, case):
                    return False

            elif mode == "faction":

                if not self._match_text(txt, r.faction or "", regex, case):
                    return False

            else:  # all

                hit = (
                    self._match_text(txt, r.name or "", regex, case)
                    or self._match_text(txt, str(r.user_id), regex, case)
                    or self._match_text(txt, r.faction or "", regex, case)
                )

                if not hit:
                    return False

        if self.hide_ignored and r.user_id in self._ignored:

            return False

        lvl = r.level if r.level is not None else 0

        if not (self.level_min <= lvl <= self.level_max):

            return False

        chip = (r.status_chip() or "").lower()

        if "okay" in chip and not self.show_okay:
            return False

        if "hospital" in chip and not self.show_hospital:
            return False

        if "jail" in chip and not self.show_jail:
            return False

        if "federal" in chip and not self.show_federal:
            return False

        if "travel" in chip and not self.show_traveling:
            return False

        return True


from settings_dialog import SettingsDialog


# ======================================================================

#                           IGNORE DIALOGS

# ======================================================================


class IgnoreDialog(QDialog):

    """

    Modern, compact ignore manager:

      • Top search bar with clear + search icon

      • Clean table (striped, compact rows, centered numeric cols)

      • Button bar with icons + hover states

      • Context menu + double-click to open profile

      • Live count pill

    """

    def __init__(self, ignored: Set[int], infos: List[TargetInfo], parent=None):

        super().__init__(parent)

        self.setWindowTitle("Ignored Targets")

        self.setModal(True)

        self.resize(760, 520)

        self.ignored = set(ignored)

        self.info_map: Dict[int, TargetInfo] = {i.user_id: i for i in infos}

        self._apply_styles()

        root = QVBoxLayout(self)

        root.setContentsMargins(10, 10, 10, 10)

        root.setSpacing(8)

        # -- Header (search + count pill)

        header = QHBoxLayout()

        header.setContentsMargins(0, 0, 0, 0)

        self.search = QLineEdit(placeholderText="Search name or ID...")

        self.search.setObjectName("search")  # ensure stylesheet applies

        self.search.setClearButtonEnabled(True)

        act_left = self.search.addAction(
            icon("search"), QLineEdit.ActionPosition.LeadingPosition
        )

        act_left.setToolTip("Search")

        self.search.textChanged.connect(self._filter_table)

        self.lbl_count = QLabel("")

        self.lbl_count.setObjectName("pill")

        self.lbl_count.setMinimumWidth(120)

        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.last_action_legend = QLabel(last_action_legend_html(compact=True))
        self.last_action_legend.setObjectName("lastActionLegend")
        self.last_action_legend.setTextFormat(Qt.TextFormat.RichText)
        self.last_action_legend.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.last_action_legend.setStyleSheet("font-size: 12px;")
        self.last_action_legend.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred
        )

        self.last_action_legend.setWordWrap(False)

        header.addWidget(self.search, 1)

        header.addSpacing(8)

        header.addWidget(self.last_action_legend, 0)

        header.addSpacing(8)

        header.addWidget(self.lbl_count, 0, Qt.AlignmentFlag.AlignRight)

        root.addLayout(header)

        # -- Table

        self.table = QTableWidget(0, 4, self)

        self.table.setObjectName("targetsTable")

        self.table.setHorizontalHeaderLabels(["Name", "ID", "Level", "Last Seen"])

        self.table.setAlternatingRowColors(True)

        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)

        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)

        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self.table.setShowGrid(False)

        self.table.verticalHeader().setVisible(False)

        self.table.setSortingEnabled(True)

        self.table.setWordWrap(False)

        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.table.customContextMenuRequested.connect(self._menu)

        self.table.itemDoubleClicked.connect(
            lambda _=None: self._open_profile_selected()
        )

        hdr = self.table.horizontalHeader()

        from PyQt6.QtWidgets import QHeaderView

        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Name

        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # ID

        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Level

        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)  # Last Seen

        root.addWidget(self.table, 1)

        # -- Footer buttons

        btn_row = QHBoxLayout()

        btn_row.setContentsMargins(0, 0, 0, 0)

        self.btn_open = self._btn(
            "Open Profile", "profile", self._open_profile_selected
        )

        self.btn_un = self._btn(
            "Unignore Selected", "unblock", self._unignore_selected, kind="primary"
        )

        self.btn_un_all = self._btn(
            "Unignore All", "unblock", self._unignore_all, kind="warning"
        )

        self.btn_import = self._btn("Import...", "import", self._import)

        self.btn_export = self._btn("Export...", "export", self._export)

        self.btn_close = self._btn("Close", None, self.accept)

        btn_row.addWidget(self.btn_open)

        btn_row.addWidget(self.btn_un)

        btn_row.addWidget(self.btn_un_all)

        btn_row.addSpacing(12)

        btn_row.addWidget(self.btn_import)

        btn_row.addWidget(self.btn_export)

        btn_row.addStretch(1)

        btn_row.addWidget(self.btn_close)

        root.addLayout(btn_row)

        # data

        self._reload_table()

        self._update_count()

        # keyboard conveniences

        self.search.setFocus()

        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ------------------------- helpers & style -------------------------

    def _btn(
        self, text: str, ic_name: Optional[str], slot, kind: str = "neutral"
    ) -> QPushButton:

        b = QPushButton(text)

        b.setCursor(Qt.CursorShape.PointingHandCursor)

        if ic_name:

            ic = icon(ic_name)

            if not ic.isNull():

                b.setIcon(ic)

        b.setProperty("kind", kind)  # used by stylesheet

        b.clicked.connect(slot)

        return b

    def _apply_styles(self):

        """

        Unified dark, compact styling for the Ignore dialog:

        - rounded search field

        - modern table + headers + scrollbars

        - soft, animated buttons with hover/pressed states

        - colored "Ignored: N" pill (ok/info/warn via dynamic property)

        """

        self.setStyleSheet(
            """

        /* ---------- Base ---------- */

        QDialog {

            background-color: #0f1520;

            color: #e6eef8;

            border: 1px solid rgba(255,255,255,0.06);

            border-radius: 12px;

        }

        QLabel, QToolButton { background: transparent; }



        /* ---------- Search ---------- */

        QLineEdit#search {

            border: 1px solid rgba(255,255,255,0.12);

            border-radius: 9px;

            padding: 6px 10px;

            background: rgba(255,255,255,0.045);

            selection-background-color: rgba(77,163,255,0.35);

        }

        QLineEdit#search:focus {

            border-color: rgba(77,163,255,0.55);

            background: rgba(77,163,255,0.06);

        }



        QLabel#lastActionLegend {

            color: #8fa3c7;

            font-size: 12px;

        }



        /* ---------- Table ---------- */

        QTableView {

            background: #111a28;

            border: 1px solid rgba(255,255,255,0.10);

            border-radius: 10px;

            gridline-color: rgba(255,255,255,0.06);

            selection-background-color: rgba(77,163,255,0.25);

            selection-color: #eaf3ff;

            outline: 0;

        }

        QTableView::item {

            padding: 6px 8px;

        }

        QTableView::item:selected {

            background: rgba(77,163,255,0.22);

        }



        /* Headers */

        QHeaderView {

            background: transparent;

            border: none;

        }

        QHeaderView::section {

            color: #d5e7ff;

            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,

                        stop:0 #162234, stop:1 #131d2c);

            border: none;

            border-bottom: 1px solid rgba(255,255,255,0.10);

            border-right: 1px solid rgba(255,255,255,0.06);

            padding: 6px 8px;

        }

        QHeaderView::section:first {

            border-top-left-radius: 10px;

        }

        QHeaderView::section:last {

            border-top-right-radius: 10px;

            border-right: none;

        }



        /* Scrollbars */

        QScrollBar:vertical, QScrollBar:horizontal {

            background: transparent;

            border: none;

            margin: 0px;

        }

        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {

            background: rgba(255,255,255,0.14);

            border-radius: 6px;

            min-height: 24px;

            min-width: 24px;

        }

        QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {

            background: rgba(77,163,255,0.45);

        }

        QScrollBar::add-line, QScrollBar::sub-line,

        QScrollBar::add-page, QScrollBar::sub-page {

            background: transparent;

            border: none;

        }



        /* ---------- Buttons ---------- */

        QPushButton {

            border: 1px solid rgba(255,255,255,0.12);

            border-radius: 10px;

            background: rgba(255,255,255,0.04);

            padding: 6px 12px;

            min-height: 30px;

        }

        QPushButton:hover {

            background: rgba(77,163,255,0.12);

            border-color: rgba(77,163,255,0.50);

        }

        QPushButton:pressed {

            background: rgba(77,163,255,0.20);

        }

        /* Primary action (give the "Unignore All" or similar a stronger accent by setting objectName to 'primaryBtn') */

        QPushButton#primaryBtn {

            border-color: rgba(77,163,255,0.65);

            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,

                        stop:0 rgba(77,163,255,0.18),

                        stop:1 rgba(77,163,255,0.10));

        }

        QPushButton#primaryBtn:hover {

            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,

                        stop:0 rgba(77,163,255,0.28),

                        stop:1 rgba(77,163,255,0.18));

        }

        /* Destructive (set objectName to 'dangerBtn' on destructive buttons) */

        QPushButton#dangerBtn {

            border-color: rgba(255,95,95,0.55);

            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,

                        stop:0 rgba(255,95,95,0.18),

                        stop:1 rgba(255,95,95,0.10));

        }

        QPushButton#dangerBtn:hover {

            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,

                        stop:0 rgba(255,95,95,0.28),

                        stop:1 rgba(255,95,95,0.18));

            border-color: rgba(255,95,95,0.75);

        }



        /* ---------- Footer hint text ---------- */

        QLabel#footerHint {

            color: rgba(255,255,255,0.70);

            padding-left: 2px;

        }



        /* ---------- Colored pill (Ignored: N) ---------- */

        QLabel#pill {

            padding: 4px 12px;

            border-radius: 999px;

            font-weight: 600;

            letter-spacing: 0.2px;

            min-height: 26px;

        }

        QLabel#pill[variant="ok"] {

            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,

                        stop:0 rgba(72,199,116,0.18),

                        stop:1 rgba(72,199,116,0.10));

            border: 1px solid rgba(72,199,116,0.55);

            color: #d7ffe6;

        }

        QLabel#pill[variant="info"] {

            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,

                        stop:0 rgba(77,163,255,0.22),

                        stop:1 rgba(77,163,255,0.10));

            border: 1px solid rgba(77,163,255,0.60);

            color: #e5f1ff;

        }

        QLabel#pill[variant="warn"] {

            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,

                        stop:0 rgba(255,196,0,0.22),

                        stop:1 rgba(255,196,0,0.10));

            border: 1px solid rgba(255,196,0,0.60);

            color: #fff4d1;

        }

        """
        )

    # ------------------------- data & filtering -------------------------

    def _rows_source(self) -> List[TargetInfo]:

        return [
            self.info_map.get(uid, TargetInfo(user_id=uid))
            for uid in sorted(self.ignored)
        ]

    def _reload_table(self):

        self.table.setSortingEnabled(False)

        self.table.setRowCount(0)

        for inf in self._rows_source():

            r = self.table.rowCount()

            self.table.insertRow(r)

            name = inf.name or "-"
            last_raw = inf.last_action_relative or inf.last_action_status or ""
            last = last_raw or "-"
            lvl = "" if inf.level is None else str(inf.level)

            it_name = QTableWidgetItem(name)
            it_id = QTableWidgetItem(str(inf.user_id))
            it_level = QTableWidgetItem(lvl)
            it_last = QTableWidgetItem(last)

            # For numeric sort

            it_id.setData(Qt.ItemDataRole.UserRole, int(inf.user_id))

            it_level.setData(
                Qt.ItemDataRole.UserRole,
                int(inf.level) if inf.level is not None else -1,
            )

            # Visual tweaks

            f = it_name.font()

            f.setItalic(True)

            it_name.setFont(f)

            it_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            it_level.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            palette = last_action_palette(last_raw)

            if palette:

                it_last.setForeground(QBrush(palette.fg))

                if palette.bold:

                    f_last = it_last.font()

                    f_last.setBold(True)

                    it_last.setFont(f_last)

                tooltip = last if not last_raw else f"{last} - {palette.label}"

            else:

                tooltip = last

            it_last.setToolTip(tooltip)

            self.table.setItem(r, 0, it_name)

            self.table.setItem(r, 1, it_id)

            self.table.setItem(r, 2, it_level)

            self.table.setItem(r, 3, it_last)

        # denser rows

        self.table.verticalHeader().setDefaultSectionSize(28)

        self.table.setSortingEnabled(True)

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

        self._update_count()

    def _dialog_selected_ids(self) -> List[int]:

        ids: List[int] = []

        for idx in self.table.selectionModel().selectedRows():

            it = self.table.item(idx.row(), 1)

            if it and it.text().isdigit():

                ids.append(int(it.text()))

        return ids

    # ------------------------- actions -------------------------

    def _open_profile_selected(self):

        ids = self._dialog_selected_ids()

        if not ids:

            return

        QDesktopServices.openUrl(QUrl.fromUserInput(profile_url(ids[0])))

    def _unignore_selected(self):

        for uid in self._dialog_selected_ids():

            self.ignored.discard(uid)

        self._reload_table()

        self._update_count()

    def _unignore_all(self):

        self.ignored.clear()

        self._reload_table()

        self._update_count()

    def _export(self):

        path, _ = QFileDialog.getSaveFileName(
            self, "Export ignored", "ignored.txt", "Text (*.txt)"
        )

        if not path:

            return

        try:

            with open(path, "w", encoding="utf-8") as f:

                for uid in sorted(self.ignored):

                    f.write(f"{uid}\n")

        except Exception:

            pass

    def _import(self):

        path, _ = QFileDialog.getOpenFileName(
            self, "Import ignored", "", "Text (*.txt);;All Files (*)"
        )

        if not path:

            return

        try:

            with open(path, "r", encoding="utf-8") as f:

                for line in f:

                    line = line.strip()

                    if line.isdigit():

                        uid = int(line)

                        self.ignored.add(uid)

                        if uid not in self.info_map:

                            self.info_map[uid] = TargetInfo(user_id=uid)

            self._reload_table()

            self._update_count()

        except Exception:

            pass

    def _update_count(self):

        n = len(self.ignored)

        # Choose a variant: green when none ignored, blue normally, amber when many.

        variant = "ok" if n == 0 else ("warn" if n >= 100 else "info")

        self.lbl_count.setText(f"Ignored: {n}")

        # Update dynamic style

        self.lbl_count.setProperty("variant", variant)

        self.lbl_count.style().unpolish(self.lbl_count)

        self.lbl_count.style().polish(self.lbl_count)

        self.lbl_count.update()

    # ------------------------- context menu -------------------------

    def _menu(self, pos):

        idx = self.table.indexAt(pos)

        menu = QMenu(self)

        if idx.isValid():

            # ensure row is the selection anchor

            if not self.table.selectionModel().isSelected(idx):

                self.table.clearSelection()

                self.table.selectRow(idx.row())

            r_user = self.table.item(idx.row(), 1)

            user_id = int(r_user.text()) if r_user and r_user.text().isdigit() else None

            if user_id is not None:

                act_open = QAction(icon("profile"), "Open Profile", self)

                act_open.triggered.connect(self._open_profile_selected)

                act_un = QAction(icon("unblock"), "Unignore Selected", self)

                act_un.triggered.connect(self._unignore_selected)

                act_copy = QAction(icon("copy"), "Copy ID", self)

                act_copy.triggered.connect(
                    lambda: QApplication.clipboard().setText(str(user_id))
                )

                menu.addAction(act_open)

                menu.addAction(act_un)

                menu.addSeparator()

                menu.addAction(act_copy)

                menu.addSeparator()

        menu.addAction(
            QAction(icon("import"), "Import...", self, triggered=self._import)
        )

        menu.addAction(
            QAction(icon("export"), "Export...", self, triggered=self._export)
        )

        menu.exec(self.table.viewport().mapToGlobal(pos))


class RemoveTargetsDialog(QDialog):
    def __init__(self, targets: List[TargetInfo], parent=None):
        super().__init__(parent)

        self.setWindowTitle("Remove Targets")
        self.setObjectName("RemoveTargetsDialog")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(430)

        count = len(targets)
        label_text = "target" if count == 1 else "targets"
        pix = icon("delete").pixmap(40, 40)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 24)
        outer.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(16)

        icon_lbl = QLabel()
        if not pix.isNull():
            icon_lbl.setPixmap(pix)
        icon_lbl.setFixedSize(40, 40)
        icon_lbl.setScaledContents(True)
        icon_lbl.setObjectName("RemoveTargetsIcon")

        title_box = QVBoxLayout()
        title_box.setSpacing(4)

        title = QLabel("Remove Selected Targets")
        title.setObjectName("RemoveTargetsTitle")

        subtitle = QLabel(
            f"You are about to remove {count} {label_text} from the tracker."
        )
        subtitle.setObjectName("RemoveTargetsSubtitle")
        subtitle.setWordWrap(True)

        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        header.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)
        header.addLayout(title_box)
        header.addStretch(1)

        outer.addLayout(header)

        card = QFrame()
        card.setObjectName("RemoveTargetsCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        summary = QLabel("TARGETS TO REMOVE")
        summary.setObjectName("RemoveTargetsSummary")
        card_layout.addWidget(summary)

        display_targets = targets[:5]
        list_items: List[str] = []
        for tgt in display_targets:
            base_name = (tgt.name or "").strip()
            if not base_name:
                uid_val = getattr(tgt, "user_id", "")
                base_name = str(uid_val) if uid_val is not None else "Unknown"
            safe_name = html.escape(base_name)
            uid = getattr(tgt, "user_id", None)
            uid_text = f"[{uid}]" if isinstance(uid, int) else ""
            list_items.append(
                f"<li>{safe_name} <span class='uid'>{uid_text}</span></li>"
            )

        extra = count - len(display_targets)
        if extra > 0:
            more_label = "target" if extra == 1 else "targets"
            list_items.append(f"<li>...and {extra} more {more_label}.</li>")

        if not list_items:
            list_items.append("<li>No targets selected.</li>")

        names = QLabel(f"<ul>{''.join(list_items)}</ul>")
        names.setObjectName("RemoveTargetsList")
        names.setTextFormat(Qt.TextFormat.RichText)
        names.setWordWrap(True)
        card_layout.addWidget(names)

        hint = QLabel(
            "Removing a target clears it from the current table. You can add it again later."
        )
        hint.setObjectName("RemoveTargetsHint")
        hint.setWordWrap(True)
        card_layout.addWidget(hint)

        outer.addWidget(card)

        footer = QLabel("This action cannot be undone.")
        footer.setObjectName("RemoveTargetsFooter")
        footer.setWordWrap(True)
        outer.addWidget(footer)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        buttons.addStretch(1)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("RemoveTargetsCancel")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)

        action_caption = "Remove Target" if count == 1 else f"Remove {count} Targets"
        self.btn_remove = QPushButton(action_caption)
        self.btn_remove.setObjectName("RemoveTargetsPrimary")
        self.btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remove.setDefault(True)
        self.btn_remove.setAutoDefault(True)

        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_remove)

        outer.addLayout(buttons)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QDialog#RemoveTargetsDialog {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(26, 32, 48, 0.97),
                    stop:1 rgba(14, 18, 30, 0.97));
                border: 1px solid rgba(74, 96, 140, 0.55);
                border-radius: 14px;
            }
            QLabel#RemoveTargetsTitle,
            QLabel#RemoveTargetsSubtitle,
            QLabel#RemoveTargetsSummary,
            QLabel#RemoveTargetsList,
            QLabel#RemoveTargetsHint,
            QLabel#RemoveTargetsFooter {
                background: transparent;
            }
            QLabel#RemoveTargetsTitle {
                color: #f0f3ff;
                font-size: 18px;
                font-weight: 600;
            }
            QLabel#RemoveTargetsSubtitle {
                color: rgba(210, 220, 244, 0.92);
                font-size: 13px;
            }
            QLabel#RemoveTargetsSummary {
                color: rgba(200, 212, 238, 0.72);
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#RemoveTargetsList {
                color: rgba(236, 240, 255, 0.96);
                font-size: 13px;
            }
            QLabel#RemoveTargetsList ul {
                margin: 0 0 0 18px;
            }
            QLabel#RemoveTargetsList .uid {
                color: rgba(164, 186, 220, 0.75);
            }
            QLabel#RemoveTargetsHint, QLabel#RemoveTargetsFooter {
                color: rgba(190, 202, 228, 0.7);
                font-size: 12px;
            }
            QFrame#RemoveTargetsCard {
                background: transparent;
                border: 1px solid rgba(92, 114, 158, 0.4);
                border-radius: 10px;
            }
            QPushButton#RemoveTargetsPrimary {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(112, 205, 255, 255),
                    stop:1 rgba(74, 156, 255, 255));
                color: #061220;
                border: none;
                padding: 9px 24px;
                border-radius: 10px;
                font-weight: 600;
            }
            QPushButton#RemoveTargetsPrimary:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(132, 216, 255, 255),
                    stop:1 rgba(94, 168, 255, 255));
            }
            QPushButton#RemoveTargetsPrimary:pressed {
                background: rgba(74, 156, 255, 0.82);
            }
            QPushButton#RemoveTargetsCancel {
                background: rgba(255, 255, 255, 0.01);
                border: 1px solid rgba(102, 126, 164, 0.45);
                padding: 8px 20px;
                border-radius: 10px;
                color: rgba(220, 228, 248, 0.88);
                font-weight: 600;
            }
            QPushButton#RemoveTargetsCancel:hover {
                border-color: rgba(128, 150, 188, 0.6);
                background: rgba(255, 255, 255, 0.03);
            }
            QPushButton#RemoveTargetsCancel:pressed {
                background: rgba(102, 126, 164, 0.16);
            }
        """)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_remove.clicked.connect(self.accept)


class IgnoredPromptDialog(QDialog):
    def __init__(self, name: str, parent=None, open_what: str = "profile"):
        super().__init__(parent)

        self.setWindowTitle("Ignored Target")
        self.setObjectName("IgnoredPromptDialog")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(440)

        safe_name = html.escape(name or "Unknown target")
        action_text = (
            "open the attack window" if open_what == "attack" else "open the profile"
        )
        icon_name = "attack" if open_what == "attack" else "profile"
        pix = icon(icon_name).pixmap(42, 42)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 24)
        outer.setSpacing(20)

        header = QHBoxLayout()
        header.setSpacing(16)

        icon_lbl = QLabel()
        if not pix.isNull():
            icon_lbl.setPixmap(pix)
        icon_lbl.setFixedSize(42, 42)
        icon_lbl.setScaledContents(True)
        icon_lbl.setObjectName("IgnoredIcon")

        title_box = QVBoxLayout()
        title_box.setSpacing(4)

        title = QLabel("Target Is Ignored")
        title.setObjectName("IgnoredTitle")

        subtitle = QLabel(
            f"{safe_name} is currently hidden because they are on your ignore list."
        )
        subtitle.setObjectName("IgnoredSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setTextFormat(Qt.TextFormat.RichText)

        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        tag = QLabel("IGNORED")
        tag.setObjectName("IgnoredTag")
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)
        header.addLayout(title_box, stretch=1)
        header.addWidget(tag, 0, Qt.AlignmentFlag.AlignTop)

        outer.addLayout(header)

        card = QFrame()
        card.setObjectName("IgnoredCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        detail = QLabel(
            f"<span class='target-name'>{safe_name}</span> will stay filtered out until you remove them from your ignore list."
        )
        detail.setObjectName("IgnoredDetail")
        detail.setWordWrap(True)
        detail.setTextFormat(Qt.TextFormat.RichText)

        options = QLabel(
            "<ul>"
            f"<li><b>Unignore</b> and {action_text}.</li>"
            "<li>Open one time without changing the ignore list.</li>"
            "</ul>"
        )
        options.setObjectName("IgnoredOptions")
        options.setWordWrap(True)
        options.setTextFormat(Qt.TextFormat.RichText)

        card_layout.addWidget(detail)
        card_layout.addWidget(options)

        outer.addWidget(card)

        hint = QLabel(
            "Unignoring moves the target back into the main tracker. Opening once keeps them ignored."
        )
        hint.setObjectName("IgnoredHint")
        hint.setWordWrap(True)

        outer.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        buttons.addStretch(1)

        self.btn_unignore_open = QPushButton("Unignore & Open")
        self.btn_unignore_open.setObjectName("IgnoredPrimaryButton")
        self.btn_unignore_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_unignore_open.setDefault(True)
        self.btn_unignore_open.setAutoDefault(True)

        self.btn_open_once = QPushButton("Open Once")
        self.btn_open_once.setObjectName("IgnoredSecondaryButton")
        self.btn_open_once.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("IgnoredGhostButton")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)

        buttons.addWidget(self.btn_unignore_open)
        buttons.addWidget(self.btn_open_once)
        buttons.addWidget(self.btn_cancel)

        outer.addLayout(buttons)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QDialog#IgnoredPromptDialog {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(24, 30, 46, 0.97),
                    stop:1 rgba(12, 16, 26, 0.97));
                border: 1px solid rgba(72, 94, 140, 0.55);
                border-radius: 16px;
            }}
            QLabel#IgnoredTitle,
            QLabel#IgnoredSubtitle,
            QLabel#IgnoredDetail,
            QLabel#IgnoredOptions,
            QLabel#IgnoredHint {{
                background: transparent;
            }}
            QLabel#IgnoredTitle {{
                color: #f7f9ff;
                font-size: 18px;
                font-weight: 600;
            }}
            QLabel#IgnoredSubtitle {{
                color: rgba(206, 216, 238, 0.9);
                font-size: 13px;
            }}
            QLabel#IgnoredTag {{
                padding: 4px 14px;
                border-radius: 12px;
                font-size: 11px;
                font-weight: 600;
                color: rgba(220, 232, 255, 0.95);
                border: 1px solid rgba(116, 150, 210, 0.55);
                background: transparent;
            }}
            QFrame#IgnoredCard {{
                background: transparent;
                border: 1px solid rgba(96, 120, 168, 0.42);
                border-radius: 12px;
            }}
            QLabel#IgnoredDetail, QLabel#IgnoredOptions, QLabel#IgnoredHint {{
                color: rgba(232, 238, 255, 0.92);
                font-size: 13px;
            }}
            QLabel#IgnoredDetail .target-name {{
                color: #ffffff;
                font-weight: 600;
            }}
            QLabel#IgnoredOptions ul {{
                margin: 6px 0 0 16px;
            }}
            QPushButton#IgnoredPrimaryButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(118, 231, 228, 255),
                    stop:1 rgba(113, 181, 255, 255));
                color: #03121f;
                border: none;
                padding: 9px 24px;
                border-radius: 10px;
                font-weight: 600;
            }}
            QPushButton#IgnoredPrimaryButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(142, 239, 232, 255),
                    stop:1 rgba(136, 198, 255, 255));
            }}
            QPushButton#IgnoredPrimaryButton:pressed {{
                background: rgba(92, 150, 255, 0.85);
            }}
            QPushButton#IgnoredSecondaryButton {{
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(116, 140, 190, 0.5);
                padding: 8px 20px;
                border-radius: 10px;
                color: rgba(224, 230, 248, 0.9);
                font-weight: 600;
            }}
            QPushButton#IgnoredSecondaryButton:hover {{
                border-color: rgba(144, 172, 216, 0.7);
                background: rgba(255, 255, 255, 0.06);
            }}
            QPushButton#IgnoredSecondaryButton:pressed {{
                background: rgba(140, 170, 255, 0.22);
            }}
            QPushButton#IgnoredGhostButton {{
                background: transparent;
                border: none;
                padding: 8px 12px;
                color: rgba(208, 214, 238, 0.7);
                font-weight: 500;
            }}
            QPushButton#IgnoredGhostButton:hover {{
                color: rgba(238, 243, 255, 0.86);
            }}
            QPushButton#IgnoredGhostButton:pressed {{
                color: rgba(184, 192, 215, 0.86);
            }}
        """)

        self.result_choice = "cancel"

        self.btn_unignore_open.clicked.connect(
            lambda: (setattr(self, "result_choice", "unignore_open"), self.accept())
        )

        self.btn_open_once.clicked.connect(
            lambda: (setattr(self, "result_choice", "open_once"), self.accept())
        )

        self.btn_cancel.clicked.connect(self.reject)


# ======================================================================

#                            ABOUT DIALOG

# ======================================================================


class _MiniSpinner(QWidget):
    """Small, gradient spinner shown during update checks."""

    def __init__(self, diameter: int = 14, color: QColor | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._angle = 0
        self._diameter = diameter
        self._pen_width = max(2, diameter // 6)
        base_color = color or QColor(72, 187, 120)
        self._color = QColor(base_color)
        self._trail_color = QColor(base_color)
        self._trail_color.setAlphaF(0.25)

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._advance)

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(diameter, diameter)
        self.hide()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
        self.hide()

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, _: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect())
        inset = self._pen_width / 2 + 1
        rect.adjust(inset, inset, -inset, -inset)

        trail_pen = QPen(self._trail_color, self._pen_width)
        trail_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(trail_pen)
        painter.drawArc(rect, 0, 360 * 16)

        arc_pen = QPen(self._color, self._pen_width)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        painter.drawArc(rect, -self._angle * 16, -120 * 16)

        painter.end()


class AboutDialog(QDialog):

    """

    Transparent header, crisp logo (ICO preferred), robust GitHub version check.

    When a newer version is found, shows a green outline pill (transparent inside)

    and enables 'Update'. Uses a queued signal to update the UI reliably.

    """

    updateFound = pyqtSignal(
        str
    )  # emitted from worker thread â- ' handled in UI thread

    updateCheckFinished = pyqtSignal(bool)

    VERSION_ENDPOINTS = [
        "http://127.0.0.1:5500/index.html?format=code&app=target-tracker",
        f"{VERSION_TRACKER_BASE}?format=json&app={VERSION_APP_ID}",
        f"{VERSION_TRACKER_BASE}?format=json",
        f"{VERSION_TRACKER_BASE}?format=code&app={VERSION_APP_ID}",
    ]

    RELEASES_URL = "https://github.com/skillerious/TornTargetTracker/releases"

    def __init__(
        self,
        parent=None,
        app_name: str = "Target Tracker",
        app_version: Optional[str] = None,
    ):

        super().__init__(parent)

        self.setWindowTitle(f"About {app_name}")

        self.setModal(True)

        self.setMinimumWidth(580)

        # ---------- logging ----------

        self._log = logging.getLogger("TargetTracker.About")

        if not self._log.handlers:

            h = logging.StreamHandler()

            h.setFormatter(
                logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
            )

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

            return app_version or "-"

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
            if not remote:
                return False
            local = local or "0"
            if pkg_version is not None:
                try:
                    newer = pkg_version.parse(remote) > pkg_version.parse(local)
                    self._log.info(
                        "Compare versions (packaging): remote=%s local=%s -> newer=%s",
                        remote,
                        local,
                        newer,
                    )
                    return newer
                except Exception as exc:
                    self._log.info("Version parse via packaging failed: %s", exc)
            r, l = _parse_version(remote), _parse_version(local)
            newer = r > l
            self._log.info(
                "Compare versions (fallback tuple): remote=%s parsed=%s  local=%s parsed=%s  -> newer=%s",
                remote,
                r,
                local,
                l,
                newer,
            )
            return newer

        self.local_version = _detect_local_version()

        self._log.info(
            "About opened: local_version=%s Python=%s PyQt=%s Qt=%s",
            self.local_version,
            self.py_ver,
            self.pyqt_ver,
            self.qt_ver,
        )

        # ---------- header (transparent) ----------

        self.header = QWidget(self)

        self.header.setObjectName("aboutHeader")

        self.header.setStyleSheet(
            """

            #aboutHeader { background: transparent; }



            /* Blue version pill - same geometry as update pill */

            QWidget#pillBlue {

                background: transparent;

                border: 1px solid #3d5371;

                border-radius: 11px;

            }

            QLabel#pillBlueText { color:#bcd6ff; font-size:12px; background: transparent; }



            /* Green update pill */

            QWidget#pill {

                background: transparent;

                border-radius: 11px;

                border: 1px solid rgba(46,125,50,0.85);

            }

            QWidget#pill[variant="checking"] {

                border: 1px solid rgba(102,142,200,0.65);

            }

            QWidget#pill[variant="latest"] {

                border: 1px solid rgba(146,118,240,0.85);

            }

            QWidget#pill[variant="update"] {

                border: 1px solid rgba(46,125,50,0.85);

            }

            QWidget#pill QLabel#pillText { color:#d7ffe3; font-size:12px; background: transparent; }

            QWidget#pill[variant="checking"] QLabel#pillText { color:#d3e1f8; }

            QWidget#pill[variant="latest"] QLabel#pillText { color:#e4d9ff; }

        """
        )

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

            pm_logo = pm_logo.scaled(
                56,
                56,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

            logo_label.setPixmap(pm_logo)

            h.addWidget(logo_label, 0, Qt.AlignmentFlag.AlignTop)

        self._log.info("About logo: using %s", logo_used or "none")

        # title

        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        self.lbl_title = QLabel(
            f"<span style='font-size:20px; font-weight:600;'>{app_name}</span>"
        )

        self.lbl_sub = QLabel("A Torn.com target list viewer")

        self.lbl_sub.setStyleSheet("color:#b8c0cc;")

        title_box.addWidget(self.lbl_title)
        title_box.addWidget(self.lbl_sub)

        h.addLayout(title_box, 1)
        h.addStretch(1)

        # Version pill (match height/padding of update pill)

        self.version_pill = QWidget()

        self.version_pill.setObjectName("pillBlue")

        self.version_pill.setAutoFillBackground(False)

        vp_l = QHBoxLayout(self.version_pill)

        vp_l.setContentsMargins(10, 3, 10, 3)  # same padding as update pill

        vp_l.setSpacing(6)

        self._version_text = QLabel(f"Version {self.local_version}")

        self._version_text.setObjectName("pillBlueText")

        vp_l.addWidget(self._version_text)

        h.addWidget(self.version_pill, 0, Qt.AlignmentFlag.AlignTop)

        # green pill (transparent interior) for updates

        self.update_pill = QWidget()

        self.update_pill.setObjectName("pill")

        self.update_pill.setAutoFillBackground(False)

        self.update_pill.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )

        pill_l = QHBoxLayout(self.update_pill)

        pill_l.setContentsMargins(10, 3, 10, 3)

        pill_l.setSpacing(6)

        self._update_spinner = _MiniSpinner(14, QColor(72, 187, 120), self.update_pill)
        self._pill_icon = QLabel()
        self._pill_icon_color_key: Optional[str] = None
        self._set_pill_icon_color(QColor(72, 187, 120))

        self._pill_text = QLabel("Update available")

        self._pill_text.setObjectName("pillText")

        self._pill_text.setWordWrap(False)

        pill_l.addWidget(self._update_spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        pill_l.addWidget(self._pill_icon)
        pill_l.addWidget(self._pill_text)

        self._set_pill_variant("update")
        self._update_spinner.hide()
        self.update_pill.setVisible(False)

        h.addWidget(self.update_pill, 0, Qt.AlignmentFlag.AlignTop)

        # ---------- body ----------

        info = QTextBrowser(self)

        info.setOpenExternalLinks(True)

        info.setStyleSheet("QTextBrowser { border: none; }")

        info.setHtml(
            f"""

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

                  <td>Skillerious - <a href="https://github.com/skillerious">GitHub</a></td></tr>

              <tr><td class="k">Torn Profile</td>

                  <td><a href="https://www.torn.com/profiles.php?XID=3212954">Open</a></td></tr>

              <tr><td class="k">Environment</td>

                  <td>Python {self.py_ver} • PyQt {self.pyqt_ver} • Qt {self.qt_ver}</td></tr>

              <tr><td class="k">Libraries</td>

                  <td><a href="https://qdarkstylesheet.readthedocs.io">QDarkStyle</a>, PyQt6</td></tr>

            </table>



            <p class="muted" style="margin-top:10px;">

            TornÂ(c) is a trademark of Torn EQ Ltd. This tool is an independent, fan-made viewer and is not affiliated

            with Torn or its developers.

            </p>

        """
        )

        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)

        # ---------- footer ----------

        def _btn(text: str, icon_name: Optional[str], url: Optional[str]):

            b = QPushButton(text)

            if icon_name:
                b.setIcon(icon(icon_name))

            if url:
                b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))

            return b

        btn_github = _btn("GitHub", "github", "https://github.com/skillerious")

        btn_torn = _btn(
            "Torn Profile", "link", "https://www.torn.com/profiles.php?XID=3212954"
        )

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

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        btns = QHBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(8)

        btns.addStretch(1)

        for b in (btn_github, btn_torn, self.btn_release, copy_btn, close_btn):
            btns.addWidget(b)

        # ---------- root layout ----------

        root = QVBoxLayout(self)

        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        root.addWidget(self.header)
        root.addWidget(info)
        root.addWidget(sep)
        root.addLayout(btns)

        self._has_update = False

        # Connect the queued signal for reliable UI updates

        self.updateFound.connect(self._apply_update_visuals)
        self.updateCheckFinished.connect(self._on_update_check_finished)

        # start async update check

        QTimer.singleShot(0, lambda: self._check_for_update(_is_newer))

    def _set_update_checking(self, active: bool, *, keep_pill_when_stopping: bool = False) -> None:
        if active:
            self._has_update = False
            self._set_pill_variant("checking")
            self._pill_text.setText("Checking for updates...")
            self._set_pill_icon_color(None)
            self._update_spinner.start()
            self.update_pill.setVisible(True)
            self.btn_release.setVisible(False)
            self.update_pill.setMinimumWidth(0)
            self.update_pill.adjustSize()
        else:
            self._update_spinner.stop()
            if not keep_pill_when_stopping:
                self.update_pill.setVisible(False)
            else:
                self.update_pill.setVisible(True)
            self.update_pill.adjustSize()

        header_layout = self.header.layout()
        if header_layout is not None:
            header_layout.invalidate()
            self.header.adjustSize()

    def _on_update_check_finished(self, has_update: bool) -> None:
        self._has_update = has_update
        self._set_update_checking(False, keep_pill_when_stopping=True)
        if has_update:
            return

        self._set_pill_variant("latest")
        self._pill_text.setText("Using latest version")
        self._set_pill_icon_color(QColor(148, 118, 240))
        self.btn_release.setVisible(False)
        self.update_pill.setMinimumWidth(0)
        self.update_pill.adjustSize()

    # ---------- update logic ----------

    def _set_pill_variant(self, variant: str) -> None:
        if self.update_pill.property("variant") == variant:
            return
        self.update_pill.setProperty("variant", variant)
        self._refresh_update_pill_style()

    def _refresh_update_pill_style(self) -> None:
        style = self.update_pill.style()
        if style is None:
            self.update_pill.update()
            self._pill_text.update()
            return
        style.unpolish(self.update_pill)
        style.polish(self.update_pill)
        self.update_pill.update()
        self._pill_text.update()

    def _set_pill_icon_color(self, color: Optional[QColor]) -> None:
        if color is None:
            self._pill_icon_color_key = None
            self._pill_icon.setVisible(False)
            return
        color_obj = QColor(color)
        key = color_obj.name(QColor.NameFormat.HexArgb)
        if self._pill_icon_color_key == key:
            self._pill_icon.setVisible(True)
            return
        self._pill_icon_color_key = key
        pm = self._dot_pixmap(color_obj)
        self._pill_icon.setPixmap(pm)
        self._pill_icon.setVisible(True)

    def _dot_pixmap(self, color: QColor, size: int = 14) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        brush_color = QColor(color)
        painter.setBrush(QBrush(brush_color))
        painter.setPen(QPen(brush_color, 1))
        r = size - 2
        painter.drawEllipse(1, 1, r, r)
        painter.end()
        return pm

    def _check_for_update(self, is_newer_fn):

        import threading

        self._set_update_checking(True)

        headers = {
            "User-Agent": f"{self.app_name}-AboutDialog",
            "Accept": "application/json, text/html;q=0.9, */*;q=0.1",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

        def _extract_version_candidate(data) -> Optional[str]:
            """Return a semantic version string if present."""
            if isinstance(data, dict):
                latest = data.get("latest")
                if isinstance(latest, dict):
                    ver = latest.get("version")
                    if isinstance(ver, str) and ver.strip():
                        return ver.strip()
                ver = data.get("version")
                if isinstance(ver, str) and ver.strip():
                    return ver.strip()
                tracks = data.get("tracks")
                if isinstance(tracks, dict):
                    for track in tracks.values():
                        if isinstance(track, dict):
                            ver = track.get("version")
                            if isinstance(ver, str) and ver.strip():
                                return ver.strip()
                for key in ("apps", "data", "items"):
                    value = data.get(key)
                    if value is not None:
                        candidate = _extract_version_candidate(value)
                        if candidate:
                            return candidate
                return None
            if isinstance(data, (list, tuple, set)):
                for item in data:
                    candidate = _extract_version_candidate(item)
                    if candidate:
                        return candidate
                return None
            if isinstance(data, str):
                for match in re.findall(r'"version"\s*:\s*"([^"]+)"', data, flags=re.IGNORECASE):
                    candidate = match.strip()
                    if candidate:
                        return candidate
                targeted = None
                try:
                    targeted = re.search(
                        rf"{re.escape(VERSION_APP_ID)}[^0-9]*([0-9]+(?:\.[0-9]+){{2}})",
                        data,
                        flags=re.IGNORECASE,
                    )
                except re.error:
                    targeted = None
                if targeted:
                    candidate = targeted.group(1).strip()
                    if candidate:
                        return candidate
                plain_match = re.search(r"\b\d+(?:\.\d+){2}\b", data)
                if plain_match:
                    candidate = plain_match.group(0).strip()
                    if candidate:
                        return candidate
                return None

        def _looks_like_html(text: str) -> bool:
            if not text:
                return False
            snippet = text.lstrip().lower()
            return snippet.startswith("<!doctype") or snippet.startswith("<html")

        def _fallback_version_from_repoversion(base_url: str) -> Optional[str]:
            parsed = urlparse(base_url)
            query = parse_qs(parsed.query or "")
            app_id = (query.get("app") or [VERSION_APP_ID])[0] or VERSION_APP_ID
            track = (query.get("track") or ["stable"])[0] or "stable"

            base_without_query = parsed._replace(query="", params="", fragment="").geturl()
            repoversion_url = urljoin(base_without_query, "repoversion.json")

            try:
                status, body = _http_fetch(repoversion_url)
            except Exception as exc:  # pragma: no cover - network issue
                self._log.info("Repoversion fetch err (%s): %s", repoversion_url, exc)
                return None

            if status < 200 or status >= 300:
                self._log.info(
                    "Repoversion fetch status not OK (%s): %s", repoversion_url, status
                )
                return None

            try:
                data = json.loads(body or "{}")
            except Exception as exc:
                self._log.info("Repoversion parse err (%s): %s", repoversion_url, exc)
                return None

            if not isinstance(data, dict):
                return None

            apps = data.get("apps")
            if isinstance(apps, list):
                app_id_low = str(app_id).strip().lower()
                for app in apps:
                    if not isinstance(app, dict):
                        continue
                    if str(app.get("id", "")).strip().lower() != app_id_low:
                        continue
                    tracks = app.get("tracks") or {}
                    chosen = tracks.get(track) or tracks.get("stable") or {}
                    if isinstance(chosen, dict):
                        ver = chosen.get("version")
                        if isinstance(ver, str) and ver.strip():
                            self._log.info(
                                "Remote version fallback via repoversion.json (%s): %s",
                                repoversion_url,
                                ver,
                            )
                            return ver.strip()
            return None

        def _http_fetch(url: str) -> tuple[int, str]:
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=6) as resp:
                    status = resp.getcode() or 0
                    raw = resp.read()
                    charset = resp.headers.get_content_charset() or "utf-8"
                    body = raw.decode(charset, errors="replace") if raw else ""
                    return status, body
            except urllib.error.HTTPError as err:
                status = err.code or 0
                raw = err.read() or b""
                headers_obj = err.headers
                charset = None
                if headers_obj is not None:
                    try:
                        charset = headers_obj.get_content_charset()
                    except Exception:
                        charset = None
                charset = charset or "utf-8"
                body = raw.decode(charset, errors="replace") if raw else ""
                return status, body

        def _fetch_remote_version() -> Optional[str]:
            for url in self.VERSION_ENDPOINTS:
                try:
                    self._log.info("Fetch version info: %s", url)
                    status, body = _http_fetch(url)
                    self._log.info("Status=%s len=%s", status, len(body))
                    if status < 200 or status >= 300:
                        continue

                    is_json = "format=json" in url
                    is_code = "format=code" in url
                    candidate: Optional[str] = None

                    if is_json:
                        try:
                            data = json.loads(body or "{}")
                        except Exception as exc:
                            self._log.info("JSON parse err (%s): %s", url, exc)
                        else:
                            candidate = _extract_version_candidate(data)
                            if candidate:
                                self._log.info("Remote version candidate (JSON): %s", candidate)
                                return candidate.strip()

                    if candidate is None:
                        candidate = _extract_version_candidate(body)

                    if candidate:
                        self._log.info("Remote version candidate: %s", candidate)
                        return candidate.strip()

                    if (is_code or is_json) and _looks_like_html(body):
                        fallback = _fallback_version_from_repoversion(url)
                        if fallback:
                            return fallback.strip()
                except urllib.error.URLError as exc:
                    self._log.info("Version fetch err (%s): %s", url, exc)
                except Exception as exc:  # pragma: no cover - diagnostic
                    self._log.info("Version fetch unexpected err (%s): %s", url, exc)

            return None

        def _worker():
            has_update = False
            remote: Optional[str] = None
            try:
                remote = _fetch_remote_version()
                self._log.info(
                    "Remote version fetched: %r (local=%r)", remote, self.local_version
                )

                if not remote:
                    self._log.info("Remote version not available; skipping update UI.")
                    return

                try:
                    if is_newer_fn(remote, self.local_version):
                        has_update = True
                        self._log.info("Newer version detected -> emitting signal")
                        self.updateFound.emit(remote)  # queued across threads
                    else:
                        self._log.info("No update available")
                except Exception as e:
                    self._log.info("Version compare failed: %s", e)
            finally:
                self.updateCheckFinished.emit(has_update)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_update_visuals(self, remote_version: str):

        """UI-thread: show transparent green pill and enable Update button."""

        self._set_update_checking(False, keep_pill_when_stopping=True)

        try:

            text = f"Update {remote_version} available"

            self._pill_text.setText(text)
            self._set_pill_variant("update")
            self._set_pill_icon_color(QColor(72, 187, 120))

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

        self.setStyleSheet(
            """

            QToolBar { background: transparent; border: 0; padding: 2px; spacing: 6px; }

            QToolButton { background: transparent; border: 0; padding: 6px; border-radius: 8px; }

            QToolButton:hover { background: rgba(255,255,255,0.06); }

            QToolButton:pressed { background: rgba(255,255,255,0.10); }

            QToolBar::separator { background: rgba(255,255,255,0.10); width: 1px; margin: 6px 4px; }

        """
        )


class MainView(QWidget):

    request_refresh = pyqtSignal()

    request_export = pyqtSignal()
    request_export_json = pyqtSignal()

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

        self.proxy = FilterProxy()
        self.proxy.setSourceModel(self.model)

        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)

        self._ignored_ids: Set[int] = set()

        self._did_size_cols = False  # avoid resizing columns on each refresh

        # fetching + coalesced sort

        self._fetching = False
        self._online_state = True

        self._resort_timer = QTimer(self)

        self._resort_timer.setSingleShot(True)

        self._resort_timer.setInterval(300)  # coalesce updates within 300ms

        self._resort_timer.timeout.connect(self._flush_resort)

        # improved search bar

        self.search_bar = SearchBar()

        self.search_bar.queryChanged.connect(self.proxy.set_search_query)

        self.chk_ok = QCheckBox("Okay")
        self.chk_ok.setChecked(True)

        self.chk_hosp = QCheckBox("Hospital")
        self.chk_hosp.setChecked(True)

        self.chk_jail = QCheckBox("Jail")
        self.chk_jail.setChecked(True)

        self.chk_fed = QCheckBox("Federal")
        self.chk_fed.setChecked(True)

        self.chk_trav = QCheckBox("Traveling")
        self.chk_trav.setChecked(True)

        self.chk_hide_ignored = QCheckBox("Hide Ignored")
        self.chk_hide_ignored.setChecked(False)

        for chk in (
            self.chk_ok,
            self.chk_hosp,
            self.chk_jail,
            self.chk_fed,
            self.chk_trav,
            self.chk_hide_ignored,
        ):

            chk.stateChanged.connect(self._on_filter_flags)

        self.sb_lvl_min = QSpinBox()
        self.sb_lvl_min.setRange(0, 100)
        self.sb_lvl_min.setValue(0)

        self.sb_lvl_max = QSpinBox()
        self.sb_lvl_max.setRange(0, 100)
        self.sb_lvl_max.setValue(100)

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

        self.act_ignore = QAction(icon("ignore"), "Ignore Selected", self)

        _unblock_ic = icon("unignore")

        self.act_unignore = QAction(
            _unblock_ic if not _unblock_ic.isNull() else icon("ignore"),
            "Unignore Selected",
            self,
        )

        self.act_remove = QAction(icon("delete"), "Remove Selected", self)
        self.act_remove.setShortcut("Del")

        self.act_ignore.triggered.connect(self._emit_ignore_selected)

        self.act_unignore.triggered.connect(self._emit_unignore_selected)

        self.act_remove.triggered.connect(self._emit_remove_selected)

        # toolbar

        self.toolbar = StyledToolBar()
        self._toolbar_visible = True

        self.act_refresh = self.toolbar.addAction(icon("refresh"), "Refresh")
        self.act_refresh.setShortcut("Ctrl+R")

        self.act_refresh.triggered.connect(self.request_refresh.emit)

        act_export_csv = self.toolbar.addAction(icon("csv"), "Export CSV")
        act_export_csv.setShortcut("Ctrl+E")

        act_export_csv.triggered.connect(self.request_export.emit)

        act_export_json = self.toolbar.addAction(icon("json"), "Export JSON")
        act_export_json.setShortcut("Ctrl+Shift+E")

        act_export_json.triggered.connect(self.request_export_json.emit)

        self.toolbar.addSeparator()

        act_load = self.toolbar.addAction(icon("folder"), "Load Targets JSON")
        act_load.setShortcut("Ctrl+O")

        act_load.triggered.connect(self.request_load_targets.emit)

        act_add = self.toolbar.addAction(icon("add"), "Add Targets...")
        act_add.setShortcut("Ctrl+N")

        act_add.triggered.connect(self._show_add_dialog)

        act_del = self.toolbar.addAction(icon("delete"), "Remove Selected")
        act_del.setShortcut("Del")

        act_del.triggered.connect(self._emit_remove_selected)

        self.toolbar.addSeparator()

        act_ignore_mgr = self.toolbar.addAction(icon("ignore"), "Ignored...")
        act_ignore_mgr.triggered.connect(self.request_manage_ignore.emit)

        self.toolbar.addSeparator()

        act_settings = self.toolbar.addAction(icon("settings"), "Settings...")
        act_settings.setShortcut("Ctrl+,")

        act_settings.triggered.connect(self.request_open_settings.emit)

        act_about = self.toolbar.addAction(icon("info"), "About")
        act_about.triggered.connect(self.request_show_about.emit)

        # filters row

        self.filters_row = QWidget()
        self._filters_visible = True
        fh = QHBoxLayout(self.filters_row)
        fh.setContentsMargins(0, 0, 0, 0)

        fh.addWidget(QLabel("Filters:"))

        for w in (
            self.chk_ok,
            self.chk_hosp,
            self.chk_jail,
            self.chk_fed,
            self.chk_trav,
            self.chk_hide_ignored,
        ):

            fh.addWidget(w)

        fh.addSpacing(12)

        fh.addWidget(QLabel("Level"))

        fh.addWidget(self.sb_lvl_min)
        fh.addWidget(QLabel("to"))
        fh.addWidget(self.sb_lvl_max)

        fh.addStretch(1)

        fh.addWidget(self.search_bar)

        lay = QVBoxLayout(self)

        lay.addWidget(self.toolbar)
        lay.addWidget(self.filters_row)
        lay.addWidget(self.table)

    # ---- connectivity state ----

    def set_online(self, online: bool):
        online = bool(online)
        if getattr(self, "_online_state", True) == online:
            return
        self._online_state = online
        self.act_refresh.setEnabled(online)
        if online:
            self.act_refresh.setToolTip("Refresh targets")
        else:
            self.act_refresh.setToolTip("Refresh disabled while offline")

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

        self.proxy.sort(
            col if col >= 0 else 2,
            order if order is not None else Qt.SortOrder.DescendingOrder,
        )

        self.table.setUpdatesEnabled(True)

        self._restore_selection(prev_sel)

        vbar.setValue(prev_scroll)

    # ---- selection helpers to persist highlights while refreshing ----

    def _selected_targets(self) -> List[TargetInfo]:

        targets: List[TargetInfo] = []

        sel = self.table.selectionModel()

        if not sel:

            return targets

        for proxy_idx in sel.selectedRows():

            src = self.proxy.mapToSource(proxy_idx)

            row_idx = src.row()

            try:

                targets.append(self.model.row(row_idx))

            except Exception:

                continue

        return targets

    def _selected_ids(self) -> List[int]:

        ids: List[int] = []

        for tgt in self._selected_targets():

            uid = getattr(tgt, "user_id", None)

            if isinstance(uid, int):

                ids.append(int(uid))

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

                sm.select(
                    proxy_idx,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )

        if first_idx is not None:

            self.table.setCurrentIndex(first_idx)

    # ---- batch-friendly API ----

    def update_or_insert(self, info: TargetInfo):

        """Used by controller per completed fetch item-no full reset."""

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

        self.sb_lvl_min.blockSignals(True)
        self.sb_lvl_max.blockSignals(True)

        self.sb_lvl_min.setValue(lo)
        self.sb_lvl_max.setValue(hi)

        self.sb_lvl_min.blockSignals(False)
        self.sb_lvl_max.blockSignals(False)

        self.proxy.set_level_bounds(lo, hi)

    def _index_to_row(self, idx: QModelIndex) -> TargetInfo:

        idx = self._normalize_index(idx)
        if not idx or not idx.isValid():
            raise ValueError("Invalid model index")

        src = self.proxy.mapToSource(idx)
        if not src or not src.isValid():
            raise ValueError("Invalid source index")

        row = src.row()
        total = self.model.rowCount()
        if row < 0 or row >= total:
            raise IndexError(f"Row {row} out of range (total {total})")

        return self.model.row(row)

    def _normalize_index(self, idx):
        if isinstance(idx, QPersistentModelIndex):
            if not idx.isValid():
                return QModelIndex()
            try:
                return QModelIndex(idx)
            except TypeError:
                model = idx.model()
                if model is None or not idx.isValid():
                    return QModelIndex()
                try:
                    parent_idx = idx.parent()
                    if isinstance(parent_idx, QPersistentModelIndex):
                        try:
                            parent_idx = QModelIndex(parent_idx)
                        except TypeError:
                            parent_idx = QModelIndex()
                    elif not isinstance(parent_idx, QModelIndex):
                        parent_idx = QModelIndex()
                    return model.index(idx.row(), idx.column(), parent_idx)
                except Exception:
                    return QModelIndex()
        if isinstance(idx, QModelIndex):
            return idx
        return QModelIndex()

    def _show_message(self, text: str, title: str = "Target Tracker"):
        try:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(title)
            box.setText(text)
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.setModal(False)
            box.open()
        except Exception:
            log.warning("Unable to display message box: %s", text)

    def _launch_url(self, url: QUrl, kind: str):
        if not url.isValid():
            log.warning("Skipping %s launch due to invalid URL: %s", kind, url)
            self._show_message(f"Could not open the {kind} link because the URL is invalid.")
            return
        try:
            ok = QDesktopServices.openUrl(url)
        except Exception as exc:
            log.exception("Failed to launch %s URL %s: %s", kind, url.toString(), exc)
            self._show_message(f"Unable to open the {kind} link. See logs for details.")
            return
        if not ok:
            log.warning("Opening %s URL returned False: %s", kind, url.toString())
            self._show_message(f"Your system refused to open the {kind} link.")

    def _target_for_index(self, idx: QModelIndex) -> Optional[TargetInfo]:
        idx = self._normalize_index(idx)
        if not idx or not idx.isValid():
            return None
        try:
            return self._index_to_row(idx)
        except Exception:
            return None

    def _prepare_target_action(
        self, idx: QModelIndex, kind: str
    ) -> Optional[tuple[TargetInfo, QUrl, int]]:
        idx = self._normalize_index(idx)
        if not idx or not idx.isValid():
            log.warning("Unable to open %s: invalid index.", kind)
            self._show_message(
                f"Unable to open the {kind} link because the selected target is no longer available."
            )
            return None

        try:
            target = self._index_to_row(idx)
        except Exception as exc:
            log.exception("Failed to resolve target for %s action: %s", kind, exc)
            self._show_message(
                f"Unable to open the {kind} link because the target is no longer available."
            )
            return None

        uid = getattr(target, "user_id", None)
        if not isinstance(uid, int) or uid <= 0:
            log.warning("Skipping %s open: invalid target id %r", kind, uid)
            self._show_message("This target does not have a valid Torn ID.")
            return None

        build_url = profile_url if kind == "profile" else attack_url
        try:
            url = QUrl.fromUserInput(build_url(uid))
        except Exception as exc:
            log.exception("Failed to build %s URL for %s: %s", kind, uid, exc)
            self._show_message(f"Unable to build a {kind} URL for this target.")
            return None

        return target, url, uid

    # ---- openers ----

    def _open_profile(self, idx: QModelIndex):
        resolved = self._prepare_target_action(idx, "profile")
        if not resolved:
            return
        target, url, uid = resolved

        if uid in self._ignored_ids:

            dlg = IgnoredPromptDialog(
                target.name or str(uid), self, open_what="profile"
            )

            if dlg.exec():

                if dlg.result_choice == "unignore_open":

                    self.unignore_ids.emit([uid])

                    self._launch_url(url, "profile")

                elif dlg.result_choice == "open_once":

                    self._launch_url(url, "profile")

            return

        self._launch_url(url, "profile")

    def _open_attack(self, idx: QModelIndex):
        resolved = self._prepare_target_action(idx, "attack")
        if not resolved:
            return
        target, url, uid = resolved

        if uid in self._ignored_ids:

            dlg = IgnoredPromptDialog(
                target.name or str(uid), self, open_what="attack"
            )

            if dlg.exec():

                if dlg.result_choice == "unignore_open":

                    self.unignore_ids.emit([uid])

                    self._launch_url(url, "attack")

                elif dlg.result_choice == "open_once":

                    self._launch_url(url, "attack")

            return

        self._launch_url(url, "attack")

    # ---- context menu ----

    def _menu(self, pos):

        idx = self.table.indexAt(pos)
        if idx.isValid():

            self.table.setFocus()

            if not self.table.selectionModel().isSelected(idx):

                self.table.clearSelection()

                self.table.selectRow(idx.row())

        menu = QMenu(self)

        r = None
        persistent_idx = None

        if idx.isValid():
            persistent_idx = QPersistentModelIndex(idx)
            r = self._target_for_index(persistent_idx)

            act_open_profile = QAction(icon("profile"), "Open Profile", self)
            act_open_profile.setEnabled(r is not None)
            act_open_profile.triggered.connect(
                lambda chk=False, i=persistent_idx: self._open_profile(i)
            )

            act_open_attack = QAction(icon("attack"), "Open attack window", self)
            act_open_attack.setEnabled(r is not None)
            act_open_attack.triggered.connect(
                lambda chk=False, i=persistent_idx: self._open_attack(i)
            )

            act_copy_profile = QAction(icon("copy"), "Copy Profile URL", self)
            if r is not None:
                act_copy_profile.triggered.connect(
                    lambda chk=False, uid=r.user_id: self._copy(profile_url(uid))
                )
            else:
                act_copy_profile.setEnabled(False)

            act_copy_id = QAction(icon("id"), "Copy ID", self)
            if r is not None:
                act_copy_id.triggered.connect(
                    lambda chk=False, uid=r.user_id: self._copy(str(uid))
                )
            else:
                act_copy_id.setEnabled(False)

            act_copy_attack = QAction(icon("copy"), "Copy Attack URL", self)
            if r is not None:
                act_copy_attack.triggered.connect(
                    lambda chk=False, uid=r.user_id: self._copy(attack_url(uid))
                )
            else:
                act_copy_attack.setEnabled(False)

            menu.addAction(act_open_profile)

            menu.addAction(act_open_attack)

            menu.addSeparator()

            menu.addAction(act_copy_profile)

            menu.addAction(act_copy_id)

            menu.addAction(act_copy_attack)

            menu.addSeparator()

        prev_ignore_enabled = self.act_ignore.isEnabled()
        prev_unignore_enabled = self.act_unignore.isEnabled()
        prev_remove_enabled = self.act_remove.isEnabled()

        if r is None:
            self.act_ignore.setEnabled(False)
            self.act_unignore.setEnabled(False)
            self.act_remove.setEnabled(False)

        menu.addAction(self.act_ignore)

        menu.addAction(self.act_unignore)

        menu.addSeparator()

        menu.addAction(self.act_remove)

        try:
            menu.exec(self.table.viewport().mapToGlobal(pos))
        finally:
            self.act_ignore.setEnabled(prev_ignore_enabled)
            self.act_unignore.setEnabled(prev_unignore_enabled)
            self.act_remove.setEnabled(prev_remove_enabled)

    def _copy(self, text: str):

        try:

            from PyQt6.QtWidgets import QApplication

            QApplication.clipboard().setText(text)

        except Exception:

            pass

    def _emit_ignore_selected(self) -> bool:

        ids = self._selected_ids()

        if not ids:

            return False

        self.ignore_ids.emit(ids)

        return True

    def _emit_unignore_selected(self) -> bool:

        ids = self._selected_ids()

        if not ids:

            return False

        self.unignore_ids.emit(ids)

        return True

    def _emit_remove_selected(self) -> bool:

        targets = self._selected_targets()

        if not targets:

            return False

        dlg = RemoveTargetsDialog(targets, self)

        if dlg.exec() != int(QDialog.DialogCode.Accepted):

            return False

        self.request_remove_targets.emit([int(t.user_id) for t in targets])

        return True

    # ---- public helpers for external triggers (menu/shortcuts) ----

    def ignore_selected(self) -> bool:

        return self._emit_ignore_selected()

    def unignore_selected(self) -> bool:

        return self._emit_unignore_selected()

    def remove_selected(self) -> bool:

        return self._emit_remove_selected()

    def copy_selected_ids(self) -> bool:

        ids = self._selected_ids()

        if not ids:

            return False

        self._copy("\n".join(str(i) for i in ids))

        return True

    def show_add_dialog(self):

        self._show_add_dialog()

    def focus_search_bar(self):

        try:

            self.search_bar.focus()

        except AttributeError:

            self.search_bar.setFocus()

    def reset_sorting(self):

        header = self.table.horizontalHeader()

        header.setSortIndicator(2, Qt.SortOrder.DescendingOrder)

        self.table.sortByColumn(2, Qt.SortOrder.DescendingOrder)

    def toggle_filters_visible(self) -> bool:

        self._filters_visible = not getattr(self, "_filters_visible", True)

        self.filters_row.setVisible(self._filters_visible)

        return self._filters_visible

    def filters_visible(self) -> bool:

        return getattr(self, "_filters_visible", True)

    def set_filters_visible(self, visible: bool):

        self._filters_visible = bool(visible)

        self.filters_row.setVisible(self._filters_visible)

    def toggle_toolbar_visible(self) -> bool:

        self._toolbar_visible = not getattr(self, "_toolbar_visible", True)

        self.toolbar.setVisible(self._toolbar_visible)

        return self._toolbar_visible

    def toolbar_visible(self) -> bool:

        return getattr(self, "_toolbar_visible", True)

    def set_toolbar_visible(self, visible: bool):

        self._toolbar_visible = bool(visible)

        self.toolbar.setVisible(self._toolbar_visible)

    def _show_add_dialog(self):

        dlg = AddTargetsDialog(self)

        dlg.accepted_ids.connect(
            lambda ids: self.request_add_targets.emit(ids) if ids else None
        )

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

        self.ed.setPlaceholderText(
            "Paste here... e.g. https://www.torn.com/profiles.php?XID=3212954, 123456"
        )

        lay.addWidget(self.ed)

        self.list_preview = QListWidget()
        self.list_preview.setMinimumHeight(140)

        lay.addWidget(self.list_preview)

        self.lbl_count = QLabel("0 ID(s) found")
        lay.addWidget(self.lbl_count)

        btns = QHBoxLayout()
        btns.addStretch(1)

        self.btn_ok = QPushButton("Add")
        self.btn_ok.setEnabled(False)

        btn_cancel = QPushButton("Cancel")

        btns.addWidget(self.btn_ok)
        btns.addWidget(btn_cancel)

        lay.addLayout(btns)

        self.ed.textChanged.connect(self._reparse)

        self.btn_ok.clicked.connect(self._emit)

        btn_cancel.clicked.connect(self.reject)

        self._ids: List[int] = []

    def _parse_text_ids(self, text: str) -> List[int]:

        text = text.strip()
        ids: List[int] = []

        if not text:
            return ids

        for m in re.findall(r"XID=(\d+)", text, flags=re.IGNORECASE):

            try:
                ids.append(int(m))

            except ValueError:
                pass

        for m in re.findall(r"\b(\d{1,10})\b", text):

            try:

                val = int(m)

                if val not in ids:
                    ids.append(val)

            except ValueError:
                pass

        seen = set()
        out = []

        for i in ids:

            if i not in seen:
                seen.add(i)
                out.append(i)

        return out

    def _reparse(self):

        ids = self._parse_text_ids(self.ed.text())

        self._ids = ids

        self.list_preview.clear()

        for i in ids:
            self.list_preview.addItem(QListWidgetItem(str(i)))

        self.lbl_count.setText(f"{len(ids)} ID(s) found")

        self.btn_ok.setEnabled(len(ids) > 0)

    def _emit(self):

        self.accepted_ids.emit(self._ids)
        self.accept()
