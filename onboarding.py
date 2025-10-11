# onboarding.py
from __future__ import annotations

import os
import json
import re
from typing import Optional, Callable

from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtGui import QIcon, QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTextBrowser, QStackedWidget, QCheckBox, QMessageBox, QToolButton, QApplication
)

# Storage helpers from your app
from storage import load_settings, save_settings, get_appdata_dir


# -------------------- assets helpers --------------------

def _icon_path(name: str) -> Optional[str]:
    """Return an icon path from assets/ic-*.svg|png (with fallbacks)."""
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
    return QIcon(p) if p else QIcon()


# -------------------- main dialog --------------------

class OnboardingDialog(QDialog):
    """
    First-run walkthrough to help the user:
      1) Understand the app and where data is stored.
      2) Create/paste a LIMITED ACCESS Torn API key and save it locally.
      3) Learn how to add targets (file or paste IDs/URLs).
      4) Learn how to fetch & filter while respecting rate limits.

    Settings keys used:
      - api_key: str
      - show_onboarding: bool  (default True)
    """

    API_KEY_HELP_URL = "https://www.torn.com/preferences.php#tab=api"
    SAMPLE_TARGET_URL = "https://www.torn.com/profiles.php?XID=3212954"

    def __init__(self, parent=None, on_settings_changed: Optional[Callable[[dict], None]] = None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Target Tracker")
        self.setModal(True)
        self.setMinimumWidth(760)
        self.setMinimumHeight(520)
        # Hide the "?" help button on Windows
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        # --- ensure settings store exists before we read/write anything
        self._ensure_settings_store()

        # --- state
        self._settings = load_settings()
        if not isinstance(self._settings, dict):
            self._settings = {}
        self._on_settings_changed = on_settings_changed
        self._saved_anything = False

        # -------- header (icon + title + version badge) --------
        header = QWidget(self)
        header.setObjectName("onbHeader")
        header.setStyleSheet("""
            #onbHeader { background: transparent; }
            QLabel#verBadge {
                color:#bcd6ff; border:1px solid #3d5371; border-radius:10px;
                padding:2px 8px; font-size:12px; background: transparent;
            }
        """)
        h = QHBoxLayout(header); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(12)

        if _icon_path("app"):
            app_icon_lbl = QLabel()
            app_icon_lbl.setPixmap(icon("app").pixmap(48, 48))
            h.addWidget(app_icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        title_box = QVBoxLayout(); title_box.setSpacing(2)
        lbl_title = QLabel("<span style='font-size:18px; font-weight:600;'>Target Tracker — First-time Setup</span>")
        lbl_sub = QLabel("A Torn.com target list viewer")
        lbl_sub.setStyleSheet("color:#b8c0cc;")
        title_box.addWidget(lbl_title)
        title_box.addWidget(lbl_sub)
        h.addLayout(title_box, 1)
        h.addStretch(1)

        ver_text = self._detect_version() or "—"
        ver_badge = QLabel(f"Version {ver_text}"); ver_badge.setObjectName("verBadge")
        h.addWidget(ver_badge, 0, Qt.AlignmentFlag.AlignTop)

        # -------- stacked pages --------
        self.stack = QStackedWidget(self)

        self.page_welcome = self._build_welcome_page()
        self.page_api = self._build_api_page()
        self.page_targets = self._build_targets_page()
        self.page_fetch = self._build_fetch_page()
        self.page_finish = self._build_finish_page()

        for p in (self.page_welcome, self.page_api, self.page_targets, self.page_fetch, self.page_finish):
            self.stack.addWidget(p)

        # -------- footer: left checkbox + nav buttons --------
        footer = QWidget(self); f = QHBoxLayout(footer); f.setContentsMargins(0, 0, 0, 0)
        self.chk_hide = QCheckBox("Don’t show on startup")
        self.chk_hide.setChecked(not bool(self._settings.get("show_onboarding", True)))
        self.chk_hide.toggled.connect(self._persist_hide_flag)
        f.addWidget(self.chk_hide, 0, Qt.AlignmentFlag.AlignLeft)
        f.addStretch(1)

        self.btn_back = QPushButton("Back")
        self.btn_next = QPushButton("Next")
        self.btn_finish = QPushButton("Finish")
        self.btn_back.clicked.connect(self._go_back)
        self.btn_next.clicked.connect(self._go_next)
        self.btn_finish.clicked.connect(self._finish)
        f.addWidget(self.btn_back)
        f.addWidget(self.btn_next)
        f.addWidget(self.btn_finish)

        # layout root
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        root.addWidget(header)
        root.addWidget(self.stack, 1)
        root.addWidget(footer)

        self._update_buttons()
        QTimer.singleShot(0, self._center_over_parent)

    # -------------------- pages --------------------

    def _build_welcome_page(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setStyleSheet("QTextBrowser { border: none; }")
        appdata = get_appdata_dir()
        tb.setHtml(f"""
            <style>
              a {{ color: #4da3ff; }}
              ul {{ margin-left: 1.1em; }}
            </style>
            <h3>Welcome!</h3>
            <p>This short guide will get you up and running in under a minute.</p>
            <ul>
              <li>Create a <b>Limited Access</b> Torn API key and paste it here.</li>
              <li>Add targets by loading your <code>target.json</code> or pasting user IDs/URLs.</li>
              <li>Fetch user info safely with built-in rate limiting and caching.</li>
            </ul>
            <p>Your local cache &amp; settings live in:<br/><code>{appdata}</code></p>
        """)
        lay.addWidget(tb, 1)
        return w

    def _build_api_page(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)

        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setStyleSheet("QTextBrowser { border: none; }")
        tb.setHtml(f"""
            <style> a {{ color:#4da3ff; }} </style>
            <h3>Get your Torn API key (Limited Access)</h3>
            <ol>
              <li>Open Torn: <a href="{self.API_KEY_HELP_URL}">{self.API_KEY_HELP_URL}</a></li>
              <li>Create an API key with <b>Limited Access</b>.</li>
              <li>Copy the key and paste it below. We'll store it locally on your device.</li>
            </ol>
        """)
        lay.addWidget(tb)

        row = QWidget(); r = QHBoxLayout(row); r.setContentsMargins(0, 0, 0, 0); r.setSpacing(6)

        self.ed_key = QLineEdit(self._settings.get("api_key", ""))
        self.ed_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_key.setPlaceholderText("Paste your Torn API key here")
        self.ed_key.textChanged.connect(self._maybe_enable_next)
        r.addWidget(self.ed_key, 1)

        btn_show = QToolButton(); btn_show.setText("Show"); btn_show.setCheckable(True)
        btn_show.toggled.connect(lambda ch: self.ed_key.setEchoMode(QLineEdit.EchoMode.Normal if ch else QLineEdit.EchoMode.Password))
        r.addWidget(btn_show)

        btn_paste = QToolButton(); btn_paste.setText("Paste")
        btn_paste.clicked.connect(self._paste_clipboard)
        r.addWidget(btn_paste)

        btn_open = QPushButton("Open API Page")
        btn_open.setIcon(icon("link"))
        btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.API_KEY_HELP_URL)))
        r.addWidget(btn_open)

        btn_test = QPushButton("Test Key")
        btn_test.setIcon(icon("check"))
        btn_test.clicked.connect(self._test_key)
        r.addWidget(btn_test)

        lay.addWidget(row)

        note = QLabel("We only store your key locally. You can change it later in Settings.")
        note.setStyleSheet("color:#b8c0cc;")
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    def _build_targets_page(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setStyleSheet("QTextBrowser { border: none; }")
        tb.setHtml(f"""
            <style>
              a {{ color: #4da3ff; }}
              code {{ background: rgba(255,255,255,0.06); padding: 2px 4px; border-radius: 4px; }}
              ul {{ margin-left: 1.1em; }}
            </style>
            <h3>Add targets</h3>
            <p>You have two options:</p>
            <ul>
              <li>Use the toolbar <b>Load Targets JSON</b> to select your <code>target.json</code>.</li>
              <li>Use <b>Add Targets…</b> and paste Torn profile URLs or IDs (e.g. <code>{self.SAMPLE_TARGET_URL}</code> or <code>3212954</code>).</li>
            </ul>
            <p>The app caches user info so next startup is instant, then live data refreshes in the background.</p>
        """)
        lay.addWidget(tb, 1)
        return w

    def _build_fetch_page(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setStyleSheet("QTextBrowser { border: none; }")
        tb.setHtml("""
            <style> ul { margin-left: 1.1em; } code { background: rgba(255,255,255,0.06); padding:2px 4px; border-radius:4px; } </style>
            <h3>Fetching &amp; filters</h3>
            <ul>
              <li>Click <b>Refresh</b> to fetch. The progress and errors appear in the status bar.</li>
              <li>Use the search bar (text or <code>/regex/</code>), level range, and status chips to filter.</li>
              <li>Right-click rows to <b>Ignore</b> or <b>Remove</b>. Double-click opens the profile.</li>
              <li>We respect Torn’s 100/min limit with pacing and retries. Tune in <b>Settings → Performance</b>.</li>
            </ul>
        """)
        lay.addWidget(tb, 1)
        return w

    def _build_finish_page(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setStyleSheet("QTextBrowser { border: none; }")
        tb.setHtml("""
            <h3>All set!</h3>
            <p>You can close this window and start using Target Tracker.</p>
            <ul>
              <li>Toolbar: Load Targets JSON, Add Targets…, Refresh, Ignored…, Settings, About</li>
              <li>Data lives locally; you can export CSV at any time.</li>
            </ul>
        """)
        lay.addWidget(tb, 1)
        return w

    # -------------------- nav / state --------------------

    def _page_index(self) -> int:
        return self.stack.currentIndex()

    def _page_count(self) -> int:
        return self.stack.count()

    def _go_back(self):
        i = self._page_index()
        if i > 0:
            self.stack.setCurrentIndex(i - 1)
            self._update_buttons()

    def _go_next(self):
        # Auto-save API key when leaving API page
        if self.stack.currentWidget() is self.page_api:
            self._save_api_key_if_valid(prompt_on_empty=False)

        i = self._page_index()
        if i < self._page_count() - 1:
            self.stack.setCurrentIndex(i + 1)
            self._update_buttons()

    def _finish(self):
        # Save API key regardless of which page we're on (best-effort).
        self._save_api_key_if_valid(prompt_on_empty=False)
        # Also persist the flag at finish (toggled already persists live)
        self._persist_hide_flag(self.chk_hide.isChecked())
        self.accept()

    def _update_buttons(self):
        i = self._page_index()
        n = self._page_count()
        self.btn_back.setEnabled(i > 0)
        self.btn_next.setVisible(i < n - 1)
        self.btn_finish.setVisible(i == n - 1)
        self._maybe_enable_next()

    # -------------------- key handling --------------------

    def _maybe_enable_next(self):
        self.btn_next.setEnabled(True)

    def _paste_clipboard(self):
        try:
            clip = QApplication.clipboard().text().strip()
            if clip:
                self.ed_key.setText(clip)
        except Exception:
            pass

    def _is_plausible_key(self, key: str) -> bool:
        return bool(key) and bool(re.fullmatch(r"[A-Za-z0-9]{16,64}", key))

    def _save_api_key_if_valid(self, prompt_on_empty: bool):
        self._ensure_settings_store()

        key = self.ed_key.text().strip()
        if not key:
            if prompt_on_empty:
                QMessageBox.information(self, "API key", "You haven't entered an API key yet. You can add it later in Settings.")
            return
        if not self._is_plausible_key(key):
            QMessageBox.warning(self, "API key", "This doesn't look like a valid Torn API key.\nYou can still save it and fix later in Settings.")

        s = load_settings()
        if not isinstance(s, dict):
            s = {}
        if s.get("api_key") != key:
            s["api_key"] = key
            self._saved_anything = True
        if "show_onboarding" not in s:
            s["show_onboarding"] = True
            self._saved_anything = True
        save_settings(s)
        self._settings = s
        if self._saved_anything and self._on_settings_changed:
            try:
                self._on_settings_changed(dict(s))
            except Exception:
                pass

    def _test_key(self):
        key = self.ed_key.text().strip()
        if not key:
            QMessageBox.information(self, "Test Key", "Please paste your API key first.")
            return
        # Lazy import to speed app startup
        try:
            import requests
        except Exception:
            QMessageBox.information(self, "Test Key", "The 'requests' package isn't installed.")
            return
        try:
            resp = requests.get(
                "https://api.torn.com/user/",
                params={"selections": "basic", "key": key},
                timeout=6.0,
            )
            data = resp.json()
        except Exception as e:
            QMessageBox.warning(self, "Test Key", f"Network error:\n{e}")
            return

        if isinstance(data, dict) and "error" in data:
            err = data.get("error") or {}
            code = err.get("code", "—")
            msg = err.get("error", "Unknown error")
            QMessageBox.warning(self, "Test Key", f"Torn error [{code}]: {msg}")
            return

        name = data.get("name") if isinstance(data, dict) else None
        self._save_api_key_if_valid(prompt_on_empty=False)
        if name:
            QMessageBox.information(self, "Test Key", f"Success! Authenticated as: {name}")
        else:
            QMessageBox.information(self, "Test Key", "Success! Your key works.")

    # -------------------- persistence & centering --------------------

    def _persist_hide_flag(self, checked: bool):
        self._ensure_settings_store()
        show_again = not bool(checked)
        s = load_settings()
        if not isinstance(s, dict):
            s = {}
        if s.get("show_onboarding") != show_again:
            s["show_onboarding"] = show_again
            self._saved_anything = True
            save_settings(s)
            self._settings = s
            if self._on_settings_changed:
                try:
                    self._on_settings_changed(dict(s))
                except Exception:
                    pass

    def _center_over_parent(self):
        try:
            self.adjustSize()
            parent = self.parentWidget()
            if parent and parent.isVisible():
                pg = parent.frameGeometry()
                self.move(pg.center() - self.rect().center())
            else:
                screen = QApplication.primaryScreen()
                geo = screen.availableGeometry() if screen else self.frameGeometry()
                self.move(geo.center() - self.rect().center())
        except Exception:
            pass

    def accept(self):
        self._save_api_key_if_valid(prompt_on_empty=False)
        super().accept()

    def reject(self):
        self._save_api_key_if_valid(prompt_on_empty=False)
        super().reject()

    # -------------------- utils --------------------

    def _ensure_settings_store(self):
        try:
            appdir = get_appdata_dir()
            os.makedirs(appdir, exist_ok=True)
        except Exception:
            pass
        try:
            s = load_settings()
            if not isinstance(s, dict):
                s = {}
            if "show_onboarding" not in s:
                s["show_onboarding"] = True
            save_settings(s)
        except Exception:
            try:
                save_settings({})
            except Exception:
                pass

    def _detect_version(self) -> Optional[str]:
        here = os.path.abspath(os.path.dirname(__file__))

        def _from_json(path: str) -> Optional[str]:
            try:
                if not os.path.exists(path):
                    return None
                with open(path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                v = obj.get("version")
                return str(v).strip() if v else None
            except Exception:
                return None

        for p in (os.path.join(here, "assets", "version.json"),
                  os.path.join(here, "version.json"),
                  os.path.join(get_appdata_dir(), "version.json")):
            v = _from_json(p)
            if v:
                return v

        tf = self._settings.get("targets_file") if isinstance(self._settings, dict) else None
        if tf and os.path.exists(tf):
            v = _from_json(tf)
            if v:
                return v
        return None


# -------------------- public helper --------------------

def maybe_show_onboarding(parent=None, on_settings_changed: Optional[Callable[[dict], None]] = None) -> bool:
    try:
        appdir = get_appdata_dir()
        os.makedirs(appdir, exist_ok=True)
        s = load_settings()
        if not isinstance(s, dict):
            s = {}
        if "show_onboarding" not in s:
            s["show_onboarding"] = True
            save_settings(s)
    except Exception:
        pass

    st = load_settings()
    if not bool(st.get("show_onboarding", True)):
        return False

    dlg = OnboardingDialog(parent, on_settings_changed=on_settings_changed)
    dlg.exec()
    return True
