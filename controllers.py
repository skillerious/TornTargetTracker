from __future__ import annotations

import os
import sys
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QIcon, QAction, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QLabel,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QMenu, QStyle
)

# App modules
from views import MainView
from storage import load_settings, save_settings, get_appdata_dir
from documentation import DocumentationDialog
from statusbar import StatusBar

# (Onboarding is triggered from main.py; we don't open it here.)

# ---------------- logging ----------------
logger = logging.getLogger("TargetTracker.Main")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)


# ---------------- PyInstaller-safe asset helpers ----------------
def asset_path(rel: str) -> str:
    """
    Resolve a path inside the bundled app. Works both in source and in
    PyInstaller one-file/one-dir builds.
    """
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base, rel)


def _first_existing(candidates) -> Optional[str]:
    for p in candidates:
        # If a candidate is relative, look inside the bundle first
        full = asset_path(p) if not os.path.isabs(p) else p
        if os.path.exists(full):
            return full
        # Also try plain relative (developer runs from repo)
        if not os.path.isabs(p) and os.path.exists(p):
            return p
    return None


def _icon_path(name: str) -> Optional[str]:
    # Preferred locations / names
    return _first_existing([
        os.path.join("assets", f"ic-{name}.svg"),
        os.path.join("assets", f"ic-{name}.png"),
        f"ic-{name}.svg",
        f"ic-{name}.png",
    ])


def _fallback_qt_icon(name: str) -> QIcon:
    """Map our semantic names to Qt standard icons (last-resort)."""
    style = QApplication.instance().style() if QApplication.instance() else None
    if not style:
        return QIcon()
    mapping = {
        "check": QStyle.StandardPixmap.SP_DialogApplyButton,
        "warning": QStyle.StandardPixmap.SP_MessageBoxWarning,
        "error": QStyle.StandardPixmap.SP_MessageBoxCritical,
        "refresh": QStyle.StandardPixmap.SP_BrowserReload,
        "info": QStyle.StandardPixmap.SP_MessageBoxInformation,
    }
    sp = mapping.get(name)
    return style.standardIcon(sp) if sp is not None else QIcon()


def app_icon() -> QIcon:
    """
    Prefer the app's ICO (for Windows taskbar/window), then fall back to ic-app.*
    and finally a standard info icon.
    """
    p = _first_existing([
        os.path.join("assets", "logo.ico"),
        os.path.join("assets", "ic-app.png"),
        os.path.join("assets", "ic-app.svg"),
    ])
    if p:
        return QIcon(p)
    return _fallback_qt_icon("info")


def themed_icon(name: str) -> QIcon:
    """
    Themed icons used across the UI. Tries packaged assets first; if missing,
    falls back to a Qt standard icon so the build still shows something.
    """
    p = _icon_path(name)
    if p:
        return QIcon(p)
    return _fallback_qt_icon(name)


# ---------------- dark style ----------------
def apply_darkstyle(app: QApplication) -> str:
    """
    Return the QDarkStyle stylesheet (plus app-specific tweaks) without
    installing it globally on the QApplication. Callers can apply the
    returned QSS selectively (e.g., to the main window) so other dialogs can
    opt out or override as needed.
    """
    qss = ""
    try:
        import qdarkstyle  # pip install qdarkstyle
        try:
            qss = qdarkstyle.load_stylesheet(qt_api="pyqt6")
        except Exception:
            qss = qdarkstyle.load_stylesheet_pyqt6()
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
    qss = (qss or "") + "\n" + extra_css
    app.setProperty("_tt_dark_qss", qss)
    return qss


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
    if not isinstance(st, dict):
        st = {}
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

        self.view = MainView()
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.setCentralWidget(central)

        self._status = StatusBar(themed_icon, self)
        self.setStatusBar(self._status)

        self._menu_actions: dict[str, QAction] = {}
        self._build_menu()

        self._controller = None  # set via attach_controller()

    # -------- menu + helpers --------

    def _build_menu(self):
        bar = self.menuBar()
        try:
            bar.setNativeMenuBar(False)
            bar.setStyleSheet("")
        except Exception:
            pass

        file_menu = bar.addMenu("&File")
        file_menu.addAction(self._create_action("Refresh Targets", "refresh", "Ctrl+R", self._trigger_refresh))
        file_menu.addSeparator()
        file_menu.addAction(self._create_action("Export CSV...", "csv", "Ctrl+E", self._trigger_export_csv))
        file_menu.addAction(self._create_action("Export JSON...", "json", "Ctrl+Shift+E", self._trigger_export_json))
        file_menu.addSeparator()
        file_menu.addAction(self._create_action("Load Targets JSON...", "folder-open", "Ctrl+O", self._trigger_load_targets))
        file_menu.addAction(self._create_action("Settings...", "settings", "Ctrl+,", self._trigger_open_settings))
        file_menu.addSeparator()
        file_menu.addAction(self._create_action("Exit", "cancel", "Ctrl+Q", self.close))

        targets_menu = bar.addMenu("&Targets")
        targets_menu.addAction(self._create_action("Add Targets...", "add", "Ctrl+N", self._trigger_add_targets))
        targets_menu.addAction(self._create_action("Remove Selected", "delete", "Del", self._trigger_remove_selected))
        targets_menu.addSeparator()
        targets_menu.addAction(self._create_action("Ignore Selected", "block", None, self._trigger_ignore_selected))
        targets_menu.addAction(self._create_action("Unignore Selected", "unblock", None, self._trigger_unignore_selected))
        targets_menu.addAction(self._create_action("Manage Ignored...", "block", None, self._trigger_manage_ignore))
        targets_menu.addSeparator()
        targets_menu.addAction(self._create_action("Copy Selected IDs", "id", "Ctrl+Shift+C", self._trigger_copy_ids))

        view_menu = bar.addMenu("&View")
        self._act_view_toolbar = self._create_action("Show Toolbar", "apply", checkable=True)
        self._act_view_toolbar.setChecked(self.view.toolbar_visible())
        self._act_view_toolbar.toggled.connect(self._toggle_toolbar)
        view_menu.addAction(self._act_view_toolbar)

        self._act_view_filters = self._create_action("Show Filters", "data", checkable=True)
        self._act_view_filters.setChecked(self.view.filters_visible())
        self._act_view_filters.toggled.connect(self._toggle_filters)
        view_menu.addAction(self._act_view_filters)

        view_menu.addSeparator()
        view_menu.addAction(self._create_action("Focus Search", "search", "Ctrl+F", self._trigger_focus_search))
        view_menu.addAction(self._create_action("Reset Sorting", "refresh", None, self._trigger_reset_sort))

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self._create_action("Open Documentation", "help", "F1", self._open_docs))
        help_menu.addAction(self._create_action("Open App Data Folder", "folder-open", None, self._open_appdata_dir))
        help_menu.addSeparator()
        help_menu.addAction(self._create_action("About Target Tracker", "info", None, self._trigger_about))

    def _create_action(self, text: str, icon_name: str, shortcut: Optional[str] = None, slot=None, checkable: bool = False) -> QAction:
        act = QAction(themed_icon(icon_name), text, self)
        if shortcut:
            act.setShortcut(shortcut)
        act.setCheckable(checkable)
        if slot:
            act.triggered.connect(slot)
        self._menu_actions[text] = act
        return act

    def _notify(self, message: str):
        if self._status:
            self._status.set_message(message)

    # -------- action handlers --------

    def _trigger_refresh(self):
        self.view.request_refresh.emit()
        self._notify("Refreshing targets…")

    def _trigger_export_csv(self):
        self.view.request_export.emit()
        self._notify("Exporting CSV…")

    def _trigger_export_json(self):
        self.view.request_export_json.emit()
        self._notify("Exporting JSON…")

    def _trigger_load_targets(self):
        self.view.request_load_targets.emit()
        self._notify("Select a targets JSON file…")

    def _trigger_open_settings(self):
        self.view.request_open_settings.emit()
        self._notify("Opening settings…")

    def _trigger_add_targets(self):
        self.view.show_add_dialog()
        self._notify("Add targets dialog opened.")

    def _trigger_remove_selected(self):
        if self.view.remove_selected():
            self._notify("Removal requested for selected targets.")
        else:
            self._notify("Select targets to remove.")

    def _trigger_ignore_selected(self):
        if self.view.ignore_selected():
            self._notify("Selected targets marked as ignored.")
        else:
            self._notify("Select targets to ignore.")

    def _trigger_unignore_selected(self):
        if self.view.unignore_selected():
            self._notify("Selected targets marked as unignored.")
        else:
            self._notify("Select targets to unignore.")

    def _trigger_manage_ignore(self):
        self.view.request_manage_ignore.emit()
        self._notify("Opening ignore manager…")

    def _trigger_copy_ids(self):
        if self.view.copy_selected_ids():
            self._notify("Copied selected ID(s) to clipboard.")
        else:
            self._notify("Select at least one target to copy IDs.")

    def _trigger_focus_search(self):
        self.view.focus_search_bar()
        self._notify("Search bar focused.")

    def _trigger_reset_sort(self):
        self.view.reset_sorting()
        self._notify("Sorting reset to default.")

    def _toggle_filters(self, checked: bool):
        self.view.set_filters_visible(checked)
        self._notify("Filters shown." if checked else "Filters hidden.")

    def _toggle_toolbar(self, checked: bool):
        self.view.set_toolbar_visible(checked)
        self._notify("Toolbar shown." if checked else "Toolbar hidden.")

    def _open_appdata_dir(self):
        path = get_appdata_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))
        self._notify("Opened app data folder.")

    def _open_docs(self):
        try:
            dlg = DocumentationDialog(self)
            dlg.exec()
            self._notify("Documentation opened.")
        except Exception as exc:
            doc = _first_existing([
                "README.md",
                os.path.join("assets", "Updated_README.rtf"),
                os.path.join("assets", "README.rtf"),
            ])
            if doc and os.path.exists(doc):
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(doc)))
                self._notify("Opened fallback documentation.")
            else:
                QDesktopServices.openUrl(QUrl("https://github.com/"))
                self._notify("Opened online documentation.")
            logger.exception("Failed to open documentation dialog: %s", exc)

    def _trigger_about(self):
        self.view.request_show_about.emit()
        self._notify("About dialog opened.")

    def attach_controller(self, controller):
        """Attach the app Controller and wire status callbacks."""
        self._controller = controller
        controller.set_status_handlers(
            status_cb=self._status.set_message,
            progress_cb=self._status.set_progress,
            meta_cb=self._status.set_meta,
            auto_cb=self._status.set_auto_eta,
        )

    def closeEvent(self, e):
        try:
            # ensure workers/timers stop before closing
            if self._controller:
                self._controller.shutdown()
            st = load_settings()
            st["last_size"] = [self.width(), self.height()]
            st["last_pos"] = [self.x(), self.y()]
            save_settings(st)
        except Exception:
            pass
        super().closeEvent(e)
