from __future__ import annotations

import json
import os
from typing import Iterable, Optional, Sequence, Union, Dict, List

from PyQt6.QtCore import Qt, QEvent, QSize, QUrl, QTimer
from PyQt6.QtGui import (
    QColor,
    QIcon,
    QPalette,
    QDesktopServices,
    QAction,
    QKeySequence,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyleFactory,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QScrollBar,
)

from storage import get_appdata_dir


__all__ = ["DocumentationDialog"]


# ----------------------------- assets helpers -----------------------------

def _icon_path(name: str) -> Optional[str]:
    for candidate in (
        os.path.join("assets", f"ic-{name}.svg"),
        os.path.join("assets", f"ic-{name}.png"),
        f"ic-{name}.svg",
        f"ic-{name}.png",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def icon(name: str) -> QIcon:
    path = _icon_path(name)
    return QIcon(path) if path else QIcon()


def _load_version() -> str:
    version_path = os.path.join("assets", "version.json")
    try:
        with open(version_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict) and "version" in data:
                return str(data["version"])
    except Exception:
        pass
    return "unknown"


def _pre_block(content: str) -> str:
    return (
        "<pre style=\"margin:8px 0;padding:10px 12px;background:#2f343a;"
        "border:1px solid #3a4047;border-radius:10px;color:#e9edf3;"
        "font-family:'Cascadia Code','Consolas','Courier New',monospace;font-size:12px;\">"
        f"{content}"
        "</pre>"
    )


# --------------------------------- widgets --------------------------------

Block = Union[str, QWidget]


class PillLabel(QLabel):
    """Rounded 'badge' for metadata chips."""

    def __init__(self, text: str, tone: str = "default", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setObjectName("pill")
        self.setProperty("tone", tone)
        self.setMinimumHeight(22)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class Callout(QFrame):
    """Callout box for tips/notes/warnings with an accent bar."""

    def __init__(self, text_html: str, kind: str = "info", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("callout")
        self.setProperty("kind", kind)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)

        ic_name = {"info": "info", "tip": "check", "warn": "warning", "danger": "error"}.get(kind, "info")
        ic = icon(ic_name)
        if not ic.isNull():
            il = QLabel()
            il.setPixmap(ic.pixmap(18, 18))
            il.setFixedSize(20, 20)
            lay.addWidget(il, 0, Qt.AlignmentFlag.AlignTop)

        lbl = QLabel(text_html)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        lbl.setOpenExternalLinks(True)
        lay.addWidget(lbl, 1)


class DocSection(QGroupBox):
    """A titled document section rendered as a modern 'card' (collapsible)."""

    def __init__(self, title: str, blocks: Sequence[Block]):
        super().__init__(title)
        self.setObjectName("docSection")
        self._title = title
        self._text_cache = title.lower()

        # Collapsible
        self.setCheckable(True)
        self.setChecked(True)

        self._body = QWidget()
        v = QVBoxLayout(self._body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        for block in blocks:
            if isinstance(block, str):
                label = QLabel(block)
                label.setObjectName("sectionBody")
                label.setWordWrap(True)
                label.setTextFormat(Qt.TextFormat.RichText)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
                label.setOpenExternalLinks(True)
                v.addWidget(label)
                self._text_cache += " " + label.text().lower()
            else:
                v.addWidget(block)
        v.addStretch(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        root.addWidget(self._body)

        self.toggled.connect(self._on_toggled)

    @property
    def title(self) -> str:
        return self._title

    def matches(self, needle: str) -> bool:
        if not needle:
            return True
        return needle.lower() in self._text_cache

    def _on_toggled(self, checked: bool) -> None:
        self._body.setVisible(checked)


class SectionGrid(QWidget):
    """Responsive 1–2 column grid; supports text filtering."""

    def __init__(self, sections: Sequence[DocSection]):
        super().__init__()
        self.setObjectName("sectionGrid")
        self._all_sections: List[DocSection] = list(sections)
        self._visible_sections: List[DocSection] = list(sections)
        self._cols = 1

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(12, 12, 12, 12)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(12)

        self._reflow()

    def sections(self) -> List[DocSection]:
        return list(self._all_sections)

    def set_filter_text(self, text: str) -> None:
        self._visible_sections = [s for s in self._all_sections if s.matches(text)]
        self._reflow()

    def _reflow(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

        self._cols = 2 if self.width() >= 900 else 1

        r = c = 0
        for sec in self._visible_sections:
            self._grid.addWidget(sec, r, c)
            c += 1
            if c >= self._cols:
                c = 0
                r += 1

        self._grid.setRowStretch(self._grid.rowCount(), 1)

    def resizeEvent(self, event) -> None:
        new_cols = 2 if self.width() >= 900 else 1
        if new_cols != self._cols:
            self._reflow()
        super().resizeEvent(event)


# --------------------------------- dialog ---------------------------------

class DocumentationDialog(QDialog):
    """
    Target Tracker — Documentation
    * Premium dark header with chips + search + jump
    * Tabs with icons; responsive section grid
    * Left TOC; live search; collapse/expand; compact toggle
    * Opens maximized
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        # Theme
        self._apply_fusion_dark()
        self.setObjectName("TTDocRoot")
        self.setWindowTitle("Target Tracker — Documentation")
        self.resize(1120, 720)
        self.setMinimumSize(820, 580)

        # Facts
        self._version = _load_version()
        self._data_dir = get_appdata_dir()

        # QSS
        self.setStyleSheet("")
        self._apply_scoped_qss()

        # Root layout
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ------------------------------- Header -------------------------------
        header = QWidget(objectName="header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 12, 12, 12)
        hl.setSpacing(12)

        # App/icon in rounded square
        icon_wrap = QFrame()
        icon_wrap.setObjectName("iconWrap")
        iw = QHBoxLayout(icon_wrap)
        iw.setContentsMargins(6, 6, 6, 6)
        iw.setSpacing(0)

        app_ic = icon("help")
        if app_ic.isNull():
            app_ic = icon("info")
        ic_lbl = QLabel()
        if not app_ic.isNull():
            ic_lbl.setPixmap(app_ic.pixmap(18, 18))
        iw.addWidget(ic_lbl)

        # Title stack
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(3)

        title = QLabel("<span class='h1'>Target Tracker — Documentation</span>")
        subtitle = QLabel("Install • Configure • Master the workflow")
        subtitle.setProperty("muted", True)

        # Pills row (keep your square look)
        chips = QWidget()
        chips_l = QHBoxLayout(chips)
        chips_l.setContentsMargins(0, 0, 0, 0)
        chips_l.setSpacing(6)
        chips_l.addWidget(PillLabel("Pure PyQt6", "blue"))
        chips_l.addWidget(PillLabel("Local only", "green"))
        chips_l.addWidget(PillLabel("Dark UI", "purple"))
        chips_l.addWidget(PillLabel(f"v{self._version}", "neutral"))
        chips_l.addStretch(1)

        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        title_col.addWidget(chips)

        # Actions: search + jump + toggles + data folder
        actions = QWidget()
        actions_l = QHBoxLayout(actions)
        actions_l.setContentsMargins(0, 0, 0, 0)
        actions_l.setSpacing(6)

        # Search with leading icon action
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchEdit")
        self.search_edit.setPlaceholderText("Search this tab…")
        self.search_edit.textChanged.connect(self._apply_search_to_current_tab)
        self.search_edit.installEventFilter(self)

        search_act = QAction(self)
        si = icon("search")
        if not si.isNull():
            search_act.setIcon(si)
        self.search_edit.addAction(search_act, QLineEdit.ActionPosition.LeadingPosition)

        # Jump to section
        self.jump_combo = QComboBox()
        self.jump_combo.setObjectName("jumpCombo")
        self.jump_combo.setPlaceholderText("Jump to section")
        self.jump_combo.setMinimumWidth(240)
        self.jump_combo.activated.connect(self._jump_to_section)

        # Density + collapse/expand
        self.btn_compact = QToolButton()
        self.btn_compact.setObjectName("linkBtn")
        self.btn_compact.setCheckable(True)
        self.btn_compact.setText("Compact")
        self.btn_compact.setToolTip("Reduce paddings for dense reading")
        self.btn_compact.toggled.connect(self._toggle_density)

        self.btn_collapse = QToolButton()
        self.btn_collapse.setObjectName("linkBtn")
        self.btn_collapse.setText("Collapse all")
        self.btn_collapse.clicked.connect(self._collapse_all_in_tab)

        self.btn_expand = QToolButton()
        self.btn_expand.setObjectName("linkBtn")
        self.btn_expand.setText("Expand all")
        self.btn_expand.clicked.connect(self._expand_all_in_tab)

        # Data folder actions
        self.btn_open = QToolButton()
        self.btn_open.setText("Open data")
        self.btn_open.setObjectName("linkBtn")
        if not icon("folder-open").isNull():
            self.btn_open.setIcon(icon("folder-open"))
        self.btn_open.clicked.connect(self._open_data_dir)

        self.btn_copy = QToolButton()
        self.btn_copy.setText("Copy path")
        self.btn_copy.setObjectName("linkBtn")
        self.btn_copy.clicked.connect(self._copy_data_dir)

        actions_l.addWidget(self.search_edit, 1)
        actions_l.addWidget(self.jump_combo, 0)
        actions_l.addWidget(self.btn_compact, 0)
        actions_l.addWidget(self.btn_collapse, 0)
        actions_l.addWidget(self.btn_expand, 0)
        actions_l.addWidget(self.btn_open, 0)
        actions_l.addWidget(self.btn_copy, 0)

        # Header layout
        hl.addWidget(icon_wrap, 0, Qt.AlignmentFlag.AlignTop)
        hl.addLayout(title_col, 1)
        hl.addWidget(actions, 0, Qt.AlignmentFlag.AlignBottom)

        root.addWidget(header)

        # ------------------------------- Splitter -----------------------------
        self.splitter = QSplitter()
        self.splitter.setOrientation(Qt.Orientation.Horizontal)

        # Left: TOC panel
        self.nav_panel = QWidget(objectName="navPanel")
        nav_v = QVBoxLayout(self.nav_panel)
        nav_v.setContentsMargins(10, 10, 10, 10)
        nav_v.setSpacing(8)

        nav_header = QLabel("On this tab")
        nav_header.setObjectName("navHeader")

        self.nav_count = QLabel("")
        self.nav_count.setProperty("muted", True)

        nav_top = QHBoxLayout()
        nav_top.setContentsMargins(0, 0, 0, 0)
        nav_top.setSpacing(6)
        nav_top.addWidget(nav_header)
        nav_top.addStretch(1)
        nav_top.addWidget(self.nav_count)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self.nav_list.itemActivated.connect(self._nav_jump)
        self.nav_list.itemClicked.connect(self._nav_jump)

        nav_v.addLayout(nav_top)
        nav_v.addWidget(self.nav_list, 1)

        self.splitter.addWidget(self.nav_panel)

        # Right: Tabs
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.tabs = QTabWidget(objectName="tabs")
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        right_layout.addWidget(self.tabs)
        self.splitter.addWidget(right_container)

        self.splitter.setSizes([250, 870])
        root.addWidget(self.splitter, 1)

        # ------------------------------ Footer -------------------------------
        footer = QWidget(objectName="footer")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(6, 6, 6, 6)
        fl.setSpacing(6)

        self.btn_close = QPushButton("Close")
        self.btn_close.setMinimumWidth(96)
        self.btn_close.clicked.connect(self.accept)

        fl.addStretch(1)
        fl.addWidget(self.btn_close)
        root.addWidget(footer)

        # Shortcuts
        find_action = QAction(self)
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(lambda: (self.search_edit.setFocus(), self.search_edit.selectAll()))
        self.addAction(find_action)

        # Book-keeping
        self._tab_grids: Dict[str, SectionGrid] = {}
        self._tab_scrollareas: Dict[str, QScrollArea] = {}
        self._tab_topbtns: Dict[str, QPushButton] = {}
        self._tab_titles: List[str] = []

        # Build tabs
        self._build_tabs()

        # Initialize TOC for the first tab
        QTimer.singleShot(0, lambda: self._on_tab_changed(0))

        # Open maximized
        QTimer.singleShot(0, self.showMaximized)

    # ------------------------------ Theming/QSS -----------------------------

    def _apply_fusion_dark(self) -> None:
        self.setStyle(QStyleFactory.create("Fusion"))
        palette = QPalette()

        win = QColor("#242629")
        panel = QColor("#2a2e32")
        base = QColor("#2d3136")
        text = QColor("#f2f4f8")
        muted = QColor("#c8cdd5")
        accent = QColor("#3d7cff")
        highlight_text = QColor("#0d1115")

        palette.setColor(QPalette.ColorRole.Window, win)
        palette.setColor(QPalette.ColorRole.Base, base)
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#343941"))
        palette.setColor(QPalette.ColorRole.Button, panel)
        palette.setColor(QPalette.ColorRole.ButtonText, text)
        palette.setColor(QPalette.ColorRole.Text, text)
        palette.setColor(QPalette.ColorRole.WindowText, text)
        palette.setColor(QPalette.ColorRole.BrightText, text)
        palette.setColor(QPalette.ColorRole.ToolTipBase, panel)
        palette.setColor(QPalette.ColorRole.ToolTipText, text)
        palette.setColor(QPalette.ColorRole.Highlight, accent)
        palette.setColor(QPalette.ColorRole.HighlightedText, highlight_text)

        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, muted)
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, muted)

        self.setPalette(palette)

    def _apply_scoped_qss(self) -> None:
        self.setStyleSheet(
            """
/* ---------- base ---------- */
#TTDocRoot { background: #242629; color: #f2f4f8; font-size: 12px; }
#TTDocRoot * { background: transparent; }

/* ---------- header ---------- */
#TTDocRoot QWidget#header {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2c3137, stop:1 #262a2f);
    border: 1px solid #3a4047;
    border-radius: 12px;
}
#TTDocRoot #iconWrap {
    background: #2f343a;
    border: 1px solid #414852;
    border-radius: 10px;
    min-width: 36px;
    min-height: 36px;
}
#TTDocRoot .h1 { font-size: 16px; font-weight: 700; letter-spacing: .2px; }
#TTDocRoot [muted="true"] { color: #c8cdd5; }

/* chips (square-ish) */
#TTDocRoot #pill[tone="blue"]    { background: #223656; color: #cfe3ff; border: 1px solid #395a99; border-radius: 8px; padding: 2px 8px; font-weight: 600; }
#TTDocRoot #pill[tone="green"]   { background: #1f3a2f; color: #b6f2d2; border: 1px solid #3e7b64; border-radius: 8px; padding: 2px 8px; font-weight: 600; }
#TTDocRoot #pill[tone="purple"]  { background: #302b40; color: #e1d5ff; border: 1px solid #6b5fb0; border-radius: 8px; padding: 2px 8px; font-weight: 600; }
#TTDocRoot #pill[tone="neutral"] { background: #30353a; color: #e6e9ee; border: 1px solid #4b525b; border-radius: 8px; padding: 2px 8px; font-weight: 600; }

/* header actions */
#TTDocRoot #searchEdit {
    background: #2f343a;
    border: 1px solid #414852;
    border-radius: 10px;
    padding: 7px 10px;
    color: #f2f4f8;
    selection-background-color: #3d7cff;
}
#TTDocRoot #searchEdit:focus { border-color: #5b84ff; }

#TTDocRoot #jumpCombo {
    background: #2f343a;
    border: 1px solid #414852;
    border-radius: 10px;
    padding: 6px 8px;
    color: #f2f4f8;
}
#TTDocRoot #linkBtn {
    background: #30353a;
    border: 1px solid #454c57;
    border-radius: 10px;
    padding: 7px 10px;
    color: #e6e9ee;
}
#TTDocRoot #linkBtn:hover { background: #383d43; }

/* ---------- splitter + nav ---------- */
#TTDocRoot QSplitter::handle { background: #2f343a; width: 6px; }
#TTDocRoot QWidget#navPanel {
    background: #2a2e32;
    border: 1px solid #3a4047;
    border-radius: 12px;
}
#TTDocRoot #navHeader { font-weight: 700; }
#TTDocRoot #navList {
    background: #2a2e32;
    border: 1px solid #3a4047;
    border-radius: 10px;
    padding: 4px;
}
#TTDocRoot #navList::item { padding: 6px 8px; }
#TTDocRoot #navList::item:selected { background: #354158; color: #ffffff; border-radius: 6px; }

/* ---------- tabs ---------- */
#TTDocRoot QTabWidget#tabs::pane {
    background: #242629;
    border: 1px solid #3a4047;
    border-radius: 12px;
    top: -1px;
}
#TTDocRoot QTabBar::tab {
    background: #2f343a;
    border: 1px solid #3a4047;
    border-bottom: none;
    padding: 7px 12px;
    margin-right: 2px;
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
    color: #e6e9ee;
}
#TTDocRoot QTabBar::tab:selected { background: #242629; color: #ffffff; border-color: #4a5260; }
#TTDocRoot QTabBar::tab:hover { background: #353a41; }

/* ---------- doc sections ---------- */
#TTDocRoot QGroupBox#docSection {
    background: #2a2e32;
    border: 1px solid #3a4047;
    border-radius: 12px;
    margin-top: 16px;
    padding-top: 12px;    /* room for continuous top border */
}
/* Title pill — we also draw a 1px top border to visually continue the card's top line */
#TTDocRoot QGroupBox#docSection::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    color: #f2f4f8;
    background: #2a2e32;                /* match the card background so it blends */
    border: 1px solid #3a4047;          /* outline around the pill */
    border-top: 1px solid #3a4047;      /* ensures the 'missing' top border is visible */
    border-radius: 10px;
    margin-left: 12px;
    font-weight: 700;
}
#TTDocRoot QLabel.sectionBody { font-size: 12px; line-height: 1.46; }

/* callouts */
#TTDocRoot #callout { border: 1px solid #3a4047; border-radius: 10px; background: #2f343a; }
#TTDocRoot #callout[kind="info"]   { border-left: 3px solid #5b84ff; }
#TTDocRoot #callout[kind="tip"]    { border-left: 3px solid #39c08f; }
#TTDocRoot #callout[kind="warn"]   { border-left: 3px solid #e0a14a; }
#TTDocRoot #callout[kind="danger"] { border-left: 3px solid #e05b5b; }

/* scrollbars */
#TTDocRoot QScrollArea { background: transparent; border: none; }
#TTDocRoot QScrollArea QWidget#qt_scrollarea_viewport { background: transparent; }
#TTDocRoot QScrollBar:vertical { background: transparent; width: 12px; margin: 4px 3px; }
#TTDocRoot QScrollBar::handle:vertical { background: #4a5260; border-radius: 6px; }
#TTDocRoot QScrollBar::handle:vertical:hover { background: #5b6b80; }

/* top button */
#TTDocRoot QPushButton#topBtn {
    background: #2f343a;
    border: 1px solid #414852;
    border-radius: 16px;
    padding: 6px 10px;
    color: #f2f4f8;
}

/* footer + buttons */
#TTDocRoot QWidget#footer { border-top: 1px solid #3a4047; margin-top: 8px; }
#TTDocRoot QPushButton {
    background: #30353a;
    border: 1px solid #454c57;
    border-radius: 10px;
    padding: 7px 12px;
    color: #f2f4f8;
}
#TTDocRoot QPushButton:hover { background: #383d43; }
#TTDocRoot QPushButton:pressed { background: #3f454c; }

/* density */
#TTDocRoot[dense="true"] QLabel.sectionBody { font-size: 11px; line-height: 1.36; }
#TTDocRoot[dense="true"] QGroupBox#docSection { margin-top: 12px; }
"""
        )

    # ------------------------------- content --------------------------------

    def _build_tabs(self) -> None:
        self._add_tab(
            "Quick Start",
            self._sections_quick_start(),
            tab_icon=icon("help") if not icon("info").isNull() else icon("info"),
        )
        self._add_tab(
            "Main Window",
            self._sections_main_window(),
            tab_icon=icon("app"),
        )
        # Escape '&' so Qt doesn't treat it as mnemonic → avoids underscores in some styles
        self._add_tab(
            "Targets && Ignore",
            self._sections_targets(),
            tab_icon=icon("id") if not icon("id").isNull() else icon("profile"),
        )
        self._add_tab(
            "Exports && Data",
            self._sections_exports(),
            tab_icon=icon("data") if not icon("json").isNull() else icon("csv"),
        )
        self._add_tab(
            "Settings && Automation",
            self._sections_settings(),
            tab_icon=icon("settings") if not icon("performance").isNull() else icon("performance"),
        )
        self._add_tab(
            "Tips & Troubleshooting",
            self._sections_tips(),
            tab_icon=icon("warning") if not icon("info").isNull() else icon("info"),
        )

    def _add_tab(self, title: str, sections: Sequence[DocSection], tab_icon: Optional[QIcon] = None) -> None:
        grid = SectionGrid(sections)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(grid)

        # Floating "Top" button
        top_btn = QPushButton("↑ Top", parent=scroll.viewport())
        top_btn.setObjectName("topBtn")
        top_btn.hide()
        top_btn.clicked.connect(lambda: scroll.verticalScrollBar().setValue(0))

        vbar = scroll.verticalScrollBar()
        vbar.valueChanged.connect(lambda _: self._update_top_btn(scroll, top_btn))
        scroll.viewport().installEventFilter(self)

        idx = self.tabs.addTab(scroll, title)
        if tab_icon and not tab_icon.isNull():
            self.tabs.setTabIcon(idx, tab_icon)

        self._tab_grids[title] = grid
        self._tab_scrollareas[title] = scroll
        self._tab_topbtns[title] = top_btn
        self._tab_titles.append(title)

    # ------------------------------- tabs data ------------------------------

    def _sections_quick_start(self) -> List[DocSection]:
        return [
            DocSection(
                "Welcome",
                [
                    (
                        "<p>Target Tracker keeps a live view of Torn targets so you can plan chains, hunting, "
                        "or revives without refreshing profiles manually. The controller coordinates background workers, "
                        "caching, and the polished dark UI you see in the main window.</p>"
                    ),
                    (
                        "<p>The application ships as pure <b>PyQt6</b> — no browser automation or third-party binaries. "
                        "All data stays local on your machine.</p>"
                    ),
                    Callout("Use a separate API key for tooling where possible. It keeps access minimal.", "tip"),
                ],
            ),
            DocSection(
                "What you need",
                [
                    "<ul>"
                    "<li>Python 3.10+ with the packages from <code>requirements.txt</code>.</li>"
                    "<li>A Torn API key with permission to read basic player info.</li>"
                    "<li>A simple JSON list of player IDs (you can paste/import later).</li>"
                    "</ul>",
                ],
            ),
            DocSection(
                "First run",
                [
                    (
                        "<ol>"
                        "<li>Launch <code>python main.py</code>. Onboarding opens if no configuration exists.</li>"
                        f"<li>Paste your Torn API key. It’s stored locally in <code>{self._data_dir}</code>.</li>"
                        "<li>Pick or create <code>target.json</code> — default lives in the same folder.</li>"
                        "<li>Paste targets (IDs, comma/space lists, or full profile URLs) and save.</li>"
                        "<li>Tune concurrency, auto refresh, and caching in <b>Settings</b> (you can change later).</li>"
                        "</ol>"
                    ),
                    Callout("You can revisit onboarding choices anytime via Settings or Add Targets.", "info"),
                ],
            ),
            DocSection(
                "Daily workflow",
                [
                    (
                        "<ol>"
                        "<li>Hit <b>Refresh</b> (<code>Ctrl+R</code>) to fetch latest status for every target.</li>"
                        "<li>Use filters, level range, and search scope to surface relevant players.</li>"
                        "<li>Double-click a row to open the attack window; context menu has copy/open actions.</li>"
                        "<li>Ignore noisy players so they don’t clutter your list.</li>"
                        "<li>Export CSV/JSON snapshots when you need to share or archive state.</li>"
                        "</ol>"
                    ),
                    Callout("The status bar narrates exactly what’s happening — fetch progress, totals, and errors.", "info"),
                ],
            ),
        ]

    def _sections_main_window(self) -> List[DocSection]:
        return [
            DocSection(
                "Toolbar & menu",
                [
                    (
                        "<p>One-click access to high-level actions:</p>"
                        "<ul>"
                        "<li><b>Refresh</b> (<code>Ctrl+R</code>) — rate-limited background fetch.</li>"
                        "<li><b>Export CSV</b> (<code>Ctrl+E</code>) and <b>Export JSON</b> (<code>Ctrl+Shift+E</code>).</li>"
                        "<li><b>Load Targets</b> (<code>Ctrl+O</code>) or <b>Add Targets</b> (<code>Ctrl+N</code>).</li>"
                        "<li><b>Remove Selected</b> (<code>Del</code>), <b>Ignored…</b>, <b>Settings…</b>, <b>About</b>.</li>"
                        "</ul>"
                    ),
                    (
                        "<p>The <b>View</b> menu toggles toolbar/filters for a minimal layout. "
                        "The <b>Help</b> menu links to this documentation and your data folder.</p>"
                    ),
                ],
            ),
            DocSection(
                "Search & filters",
                [
                    (
                        "<p>The search bar supports:</p>"
                        "<ul>"
                        "<li>Scope selector (All, Name, ID, Faction).</li>"
                        "<li>Regex mode via the <b>.*</b> toggle or when input is <code>/pattern/</code>.</li>"
                        "<li>Case sensitivity via the <b>Aa</b> toggle.</li>"
                        "<li>Debounced typing for smooth filtering on large lists.</li>"
                        "</ul>"
                    ),
                    (
                        "<p>Status checkboxes and level range feed the proxy model in <code>MainView</code>; "
                        "changes are instant and do not re-fetch.</p>"
                    ),
                ],
            ),
            DocSection(
                "Targets table",
                [
                    (
                        "<p>Columns: Name, ID, Level, Status chip, Details, Until, Faction, Last action, Error.</p>"
                        "<ul>"
                        "<li>Sort by clicking headers; choice persists during sessions.</li>"
                        "<li>Double-click to attack; right-click for quick copy/open actions.</li>"
                        "<li>Multi-select to ignore, export, or remove in batches.</li>"
                        "</ul>"
                    ),
                    Callout("Selections survive refreshes — restored via <code>MainView._restore_selection</code>.", "tip"),
                ],
            ),
            DocSection(
                "Context menu",
                [
                    (
                        "<ul>"
                        "<li>Open Profile / Open Attack Window</li>"
                        "<li>Copy Profile URL / Attack URL / ID</li>"
                        "<li>Ignore / Unignore / Remove</li>"
                        "</ul>"
                    ),
                    "<p>The ignore manager mirrors the same patterns for consistency.</p>",
                ],
            ),
            DocSection(
                "Status bar",
                [
                    (
                        "<p><code>FancyStatusBar</code> combines:</p>"
                        "<ul>"
                        "<li>Live message area with hoverable error details.</li>"
                        "<li>Progress indicator (neutral, fetching, success, error).</li>"
                        "<li>Totals/ignored pills and an <b>Updated</b> pill that flips to <b>Fetching…</b> during runs.</li>"
                        "</ul>"
                    ),
                    Callout("Hover the status icon for a precise error message from the last fetch.", "info"),
                ],
            ),
        ]

    def _sections_targets(self) -> List[DocSection]:
        return [
            DocSection(
                "Target list format",
                [
                    (
                        "<p><code>target.json</code> in your data folder "
                        f"(<code>{self._data_dir}</code>) is a simple array:</p>" +
                        _pre_block("[3212954, 1234567, 7654321]")
                    ),
                    "<p>Use <b>File → Load Targets JSON…</b> to switch list files at runtime.</p>",
                ],
            ),
            DocSection(
                "Adding / importing IDs",
                [
                    (
                        "<ul>"
                        "<li>One ID per line</li>"
                        "<li>Comma/space separated lists</li>"
                        "<li>Full profile URLs — the dialog extracts <code>XID</code></li>"
                        "</ul>"
                    ),
                    "<p>Duplicates are filtered; a total is shown before you commit.</p>",
                ],
            ),
            DocSection(
                "Removing / editing",
                [
                    (
                        "<p>Select rows and press <b>Del</b> (or use <b>Targets → Remove Selected</b>). "
                        "The controller updates both JSON and cache.</p>"
                    ),
                    "<p>If you hand-edit the file, reload via <b>Load Targets JSON…</b> or restart.</p>",
                ],
            ),
            DocSection(
                "Ignore workflow",
                [
                    (
                        "<p>Ignored IDs persist via <code>storage.save_ignore</code>. You can:</p>"
                        "<ul>"
                        "<li>Right-click → <b>Ignore Selected</b></li>"
                        "<li>Use toolbar/menu</li>"
                        "<li>Open <b>Targets → Manage Ignored…</b> for bulk import/export</li>"
                        "</ul>"
                    ),
                ],
            ),
            DocSection(
                "Cache & persistence",
                [
                    (
                        "<ul>"
                        "<li><code>cache_targets.json</code> — warm cache for instant startup</li>"
                        "<li><code>settings.json</code> — API key & preferences</li>"
                        "<li><code>ignore.json</code> — ignored IDs</li>"
                        "</ul>"
                    ),
                    Callout("Zip the data directory to migrate machines. Never share your API key.", "warn"),
                ],
            ),
        ]

    def _sections_exports(self) -> List[DocSection]:
        return [
            DocSection(
                "CSV export",
                [
                    (
                        "<p>Spreadsheet including visible columns:</p>"
                        "<ul>"
                        "<li>Name, ID, Level, Status chip</li>"
                        "<li>Details (e.g., hospital timers) & Faction</li>"
                        "<li>Last action text & Error</li>"
                        "</ul>"
                    ),
                ],
            ),
            DocSection(
                "JSON export",
                [
                    "<p>Compact JSON array of IDs currently loaded — perfect for syncing with other tools.</p>",
                ],
            ),
            DocSection(
                "Backups & portability",
                [
                    (
                        "<p>Everything lives under <code>"
                        f"{self._data_dir}</code>:</p>"
                        "<ul>"
                        "<li><code>settings.json</code> — API key, refresh, concurrency</li>"
                        "<li><code>target.json</code> — your tracked IDs</li>"
                        "<li><code>ignore.json</code> — hidden IDs</li>"
                        "<li><code>cache_targets.json</code> — warm cache</li>"
                        "</ul>"
                    ),
                    Callout("Keep encrypted/cloud backups of this folder for safety.", "tip"),
                ],
            ),
            DocSection(
                "Loading alternate data",
                [
                    (
                        "<p>Maintain multiple lists (chains / revives / hunting) and swap via "
                        "<b>File → Load Targets JSON…</b>. The cache updates to avoid re-fetching fresh entries.</p>"
                    ),
                ],
            ),
        ]

    def _sections_settings(self) -> List[DocSection]:
        return [
            DocSection(
                "API key & security",
                [
                    (
                        "<p><code>settings.json</code> stores your key locally and only sends it to Torn when fetching. "
                        "Use the built-in tester in Settings to validate before saving.</p>"
                    ),
                    "<p>Revoke/replace the key in Torn anytime; paste the new value and <b>Apply</b>.</p>",
                ],
            ),
            DocSection(
                "Performance tuning",
                [
                    (
                        "<p><b>Concurrency</b> controls parallel requests. "
                        "<code>BatchFetcher</code> respects <code>RateLimiter</code> to stay within Torn limits.</p>"
                    ),
                    Callout("Use conservative values if multiple tools share a key.", "warn"),
                ],
            ),
            DocSection(
                "Auto refresh",
                [
                    (
                        "<p>Enable auto refresh to poll targets every N seconds. The status bar shows "
                        "<b>Fetching…</b> during runs and flips green when new results land.</p>"
                    ),
                    "<p>Manual refresh waits for an active batch to finish before starting a new one.</p>",
                ],
            ),
            DocSection(
                "Quality of life",
                [
                    (
                        "<p>Options cover cache frequency, onboarding hints, and view toggles. "
                        "Remember to press <b>Apply</b> or <b>OK</b> — both emit <code>saved</code> "
                        "so the controller reloads preferences and refreshes.</p>"
                    ),
                ],
            ),
        ]

    def _sections_tips(self) -> List[DocSection]:
        return [
            DocSection(
                "Keyboard shortcuts",
                [
                    self._shortcut_grid(
                        [
                            ("Ctrl+R", "Refresh targets"),
                            ("Ctrl+E", "Export CSV"),
                            ("Ctrl+Shift+E", "Export JSON"),
                            ("Ctrl+O", "Load targets file"),
                            ("Ctrl+N", "Add targets"),
                            ("Delete", "Remove selected targets"),
                            ("Ctrl+F", "Focus the search bar"),
                            ("Ctrl+Shift+C", "Copy selected IDs"),
                            ("Ctrl+,", "Open Settings"),
                            ("F1", "Open this documentation"),
                        ]
                    ),
                    "<p>OS-level hotkeys can launch the app; inside the app, shortcuts are fixed.</p>",
                ],
            ),
            DocSection(
                "Search bar tricks",
                [
                    (
                        "<ul>"
                        "<li>Paste IDs, names, partial factions, or regex patterns.</li>"
                        "<li>Wrap text in slashes (<code>/hosp/</code>) to auto-enable regex mode.</li>"
                        "<li>Switch scope to <b>ID</b> to match numeric fragments only.</li>"
                        "<li>Press <b>Esc</b> while focused to clear.</li>"
                        "</ul>"
                    ),
                ],
            ),
            DocSection(
                "Troubleshooting",
                [
                    (
                        "<ul>"
                        "<li><b>No rows:</b> confirm <code>target.json</code> has IDs and filters aren’t hiding everything.</li>"
                        "<li><b>API errors:</b> hover the status icon for details — invalid keys or rate limits are common.</li>"
                        "<li><b>Slow:</b> lower concurrency or disable auto refresh temporarily.</li>"
                        "<li><b>Clipboard:</b> some sandboxed environments block access — run from a desktop session.</li>"
                        "</ul>"
                    ),
                    Callout("The console logs every refresh milestone and surfaces exceptions for quick diagnosis.", "info"),
                ],
            ),
            DocSection(
                "Need more help?",
                [
                    "<p>See <code>README.md</code> for installation notes or open the project repository for updates. "
                    "The Help menu links here and to your data directory for quick access.</p>",
                ],
            ),
        ]

    # ------------------------------- helpers --------------------------------

    def _shortcut_grid(self, rows: Sequence[tuple[str, str]]) -> QWidget:
        grid = QWidget()
        layout = QGridLayout(grid)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(20)
        layout.setVerticalSpacing(6)
        for idx, (shortcut, description) in enumerate(rows):
            lbl_shortcut = QLabel(f"<code>{shortcut}</code>")
            lbl_shortcut.setTextFormat(Qt.TextFormat.RichText)
            lbl_shortcut.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl_desc = QLabel(description)
            lbl_desc.setWordWrap(True)
            layout.addWidget(lbl_shortcut, idx, 0)
            layout.addWidget(lbl_desc, idx, 1)
        return grid

    # Header actions
    def _open_data_dir(self) -> None:
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._data_dir))
        except Exception:
            pass

    def _copy_data_dir(self) -> None:
        QApplication.clipboard().setText(self._data_dir)

    # Density toggle
    def _toggle_density(self, dense: bool) -> None:
        self.setProperty("dense", "true" if dense else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    # Search / Jump / TOC
    def _apply_search_to_current_tab(self) -> None:
        title = self._current_tab_title()
        grid = self._tab_grids.get(title)
        if grid:
            grid.set_filter_text(self.search_edit.text())
            self._populate_jump_and_nav_for(title)

    def _populate_jump_and_nav_for(self, title: str) -> None:
        self.jump_combo.blockSignals(True)
        self.jump_combo.clear()
        self.nav_list.clear()

        grid = self._tab_grids.get(title)
        if not grid:
            self.jump_combo.blockSignals(False)
            self.nav_count.setText("")
            return

        count = 0
        for sec in grid.sections():
            if sec.isVisibleTo(grid) or sec.parent() is grid:
                self.jump_combo.addItem(sec.title, sec)
                item = QListWidgetItem(sec.title)
                self.nav_list.addItem(item)
                item.setData(Qt.ItemDataRole.UserRole, sec)
                count += 1

        self.jump_combo.setCurrentIndex(-1)
        self.jump_combo.blockSignals(False)
        self.nav_count.setText(f"{count} section{'s' if count != 1 else ''}")

    def _jump_to_section(self) -> None:
        idx = self.jump_combo.currentIndex()
        if idx < 0:
            return
        sec: DocSection = self.jump_combo.currentData()
        self._scroll_to(sec)

    def _nav_jump(self, item: QListWidgetItem) -> None:
        sec = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(sec, DocSection):
            self._scroll_to(sec)

    def _scroll_to(self, sec: DocSection) -> None:
        title = self._current_tab_title()
        scroll = self._tab_scrollareas.get(title)
        if scroll and sec:
            if not sec.isChecked():
                sec.setChecked(True)
            scroll.ensureWidgetVisible(sec, xMargin=0, yMargin=64)

    def _current_tab_title(self) -> str:
        i = self.tabs.currentIndex()
        return self.tabs.tabText(i) if i >= 0 else ""

    def _on_tab_changed(self, idx: int) -> None:
        title = self._current_tab_title()
        self.search_edit.setPlaceholderText(f"Search “{title}” …")
        self._populate_jump_and_nav_for(title)
        self._update_top_btn(self._tab_scrollareas.get(title), self._tab_topbtns.get(title))

    # Collapse/Expand all
    def _collapse_all_in_tab(self) -> None:
        grid = self._tab_grids.get(self._current_tab_title())
        if not grid:
            return
        for sec in grid.sections():
            if sec.isVisibleTo(grid) or sec.parent() is grid:
                sec.setChecked(False)

    def _expand_all_in_tab(self) -> None:
        grid = self._tab_grids.get(self._current_tab_title())
        if not grid:
            return
        for sec in grid.sections():
            if sec.isVisibleTo(grid) or sec.parent() is grid:
                sec.setChecked(True)

    # Floating Top button placement
    def _update_top_btn(self, scroll: Optional[QScrollArea], btn: Optional[QPushButton]) -> None:
        if not scroll or not btn:
            return
        vbar: QScrollBar = scroll.verticalScrollBar()
        show = vbar.value() > 80
        if show and not btn.isVisible():
            btn.show()
        elif not show and btn.isVisible():
            btn.hide()

        vp = scroll.viewport()
        btn.adjustSize()
        x = max(8, vp.width() - btn.width() - 12)
        y = max(8, vp.height() - btn.height() - 12)
        btn.move(x, y)

    # Keep Top button positioned on viewport resize; select-all on search focus
    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            for title, scroll in self._tab_scrollareas.items():
                if scroll and obj is scroll.viewport():
                    self._update_top_btn(scroll, self._tab_topbtns.get(title))
                    break
        if obj is self.search_edit and event.type() == QEvent.Type.FocusIn:
            self.search_edit.selectAll()
        return super().eventFilter(obj, event)


# --------------------------------- preview ---------------------------------

if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    dlg = DocumentationDialog()
    dlg.showMaximized()  # explicit for standalone run
    sys.exit(app.exec())
