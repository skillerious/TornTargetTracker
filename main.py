from __future__ import annotations

import json
import os
import logging
import threading
import socket
import faulthandler
from typing import List, Set, Callable, Optional, Dict

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QUrl
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

# App modules
from models import TargetInfo
from storage import (
    load_settings, save_settings, load_ignore, save_ignore,
    load_targets_from_file, load_cache, save_cache, add_targets_to_file,
    remove_targets_from_file, get_appdata_dir
)
from api import TornAPI
from workers import BatchFetcher
from views import MainView, IgnoreDialog, AboutDialog, AddTargetsDialog
# NOTE: do NOT import SettingsDialog here; we hot-reload it in show_settings()
from rate_limiter import RateLimiter
from controllers import MainWindow, apply_darkstyle, ensure_first_run_targets
from config import CONFIG
from logging_config import setup_logging

# Optional onboarding (no-op if missing)
try:
    from onboarding import maybe_show_onboarding
except Exception:
    def maybe_show_onboarding(parent=None):  # type: ignore
        return


logger = logging.getLogger("TargetTracker.Controller")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)


class Controller(QObject):
    onlineChanged = pyqtSignal(bool)
    """
    Controller with incremental UI updates to prevent table jumping while fetching.
    Shutdown-aware: exposes shutdown() to stop workers and timers on app close.
    """

    def __init__(self, view: MainView):
        super().__init__(view)
        self.view = view

        # Settings & persistent lists
        self.settings = load_settings()
        self.ignored: Set[int] = load_ignore()

        # In-memory rows & indices (thread-safe with lock)
        self.rows: List[TargetInfo] = []
        self._rows_by_id: Dict[int, int] = {}
        self._cache_map: Optional[Dict[int, TargetInfo]] = None
        self._rows_lock = threading.Lock()  # Protect rows and _rows_by_id from race conditions

        # Prevent updates for removed IDs mid-flight
        self._current_target_set: Set[int] = set()

        # UI status callbacks (set from MainWindow)
        self._set_status: Optional[Callable[[str], None]] = None
        self._set_progress: Optional[Callable[[int, int], None]] = None
        self._set_meta: Optional[Callable[[str], None]] = None
        self._set_auto_eta: Optional[Callable[[Optional[int]], None]] = None
        self._auto_eta_state: Optional[int] = -1
        self._settings_dialog_module = None

        # Auto-refresh
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.refresh)
        self._auto_interval = 65  # seconds
        self._notify_auto_eta(-1)

        # Fetch machinery & accounting
        self._fetcher: Optional[BatchFetcher] = None
        self._errors = 0
        self._done = 0
        self._save_every = int(self.settings.get("save_cache_every", CONFIG.CACHE_SAVE_EVERY_N))
        self._since_last_save = 0

        # Cache expiration timestamp (hours)
        self._cache_expiry_hours = CONFIG.CACHE_EXPIRY_HOURS

        # Global shutdown flag for all workers/backoff waits
        self._shutdown_event = threading.Event()

        # Wire view events
        self.view.request_refresh.connect(self.refresh)
        self.view.request_export.connect(self.export_csv)
        self.view.request_export_json.connect(self.export_json)
        self.view.request_open_settings.connect(self.show_settings)
        self.view.request_manage_ignore.connect(self.show_ignore)
        self.view.request_load_targets.connect(self.pick_targets_file)
        self.view.request_show_about.connect(self.show_about)
        self.view.request_add_targets.connect(self.add_targets)
        self.view.request_remove_targets.connect(self.remove_targets)

        self.view.ignore_ids.connect(self.ignore_ids_add)
        self.view.unignore_ids.connect(self.unignore_ids_remove)
        self.view.set_ignored(self.ignored)

        # Connectivity monitoring (single QNetworkAccessManager + periodic HEAD)
        self._netman = QNetworkAccessManager(self)
        self._pending_connectivity_reply: Optional[QNetworkReply] = None
        self._is_online = True
        self._connectivity_check_in_progress = False
        self._last_offline_reason = ""
        self._connectivity_timer = QTimer(self)
        self._connectivity_timer.setInterval(5000)
        self._connectivity_timer.timeout.connect(self._check_connectivity)
        self._connectivity_timer.start()
        self._offline_prompt_shown = False
        QTimer.singleShot(0, self._check_connectivity)

        self._update_auto_timer()

        logger.info(
            "Settings loaded: targets_file=%r, conc=%s, auto_refresh=%ss, load_cache_at_start=%s",
            self.settings.get("targets_file"),
            self.settings.get("concurrency"),
            self.settings.get("auto_refresh_sec"),
            self.settings.get("load_cache_at_start"),
        )
        self.refresh()

    # ---------------- Status bar hooks ----------------

    def set_status_handlers(
        self,
        status_cb: Callable[[str], None],
        progress_cb: Callable[[int, int], None],
        meta_cb: Callable[[str], None],
        auto_cb: Optional[Callable[[Optional[int]], None]] = None,
    ):
        self._set_status = status_cb
        self._set_progress = progress_cb
        self._set_meta = meta_cb
        self._set_auto_eta = auto_cb
        if auto_cb is not None:
            auto_cb(self._auto_eta_state)

    def _status(self, text: str):
        logger.debug("STATUS: %s", text)
        if self._set_status:
            self._set_status(text)

    def _progress(self, cur: int, total: int):
        if self._set_progress:
            self._set_progress(cur, total)
        self._update_meta(cur, total)

    def _update_meta(self, cur: int, total: int):
        if not self._set_meta:
            return
        visible = self._visible_count()
        self._set_meta(f"{cur} / {total}    •   Total: {total} • Ignored: {len(self.ignored)} • Visible: {visible}")

    def _notify_auto_eta(self, seconds: Optional[int]):
        self._auto_eta_state = seconds
        if self._set_auto_eta:
            self._set_auto_eta(seconds)

    def _visible_count(self) -> int:
        if not self.rows:
            return 0
        return sum(1 for r in self.rows if r.user_id not in self.ignored)

    # Future enhancement: Cache expiration
    # TODO: Add timestamp field to TargetInfo to track cache age
    # TODO: Filter expired entries when loading cache

    # ---------------- Targets & API ----------------

    def targets_list(self) -> List[int]:
        path = self.settings.get("targets_file")
        ids = load_targets_from_file(path)
        logger.info("Loaded targets from %r: %d id(s)", path, len(ids))
        if not ids:
            logger.warning("Target list is empty. Check the path or JSON schema.")
        return ids

    def api(self) -> TornAPI:
        max_per_min = int(self.settings.get("rate_max_per_min", 100))
        min_interval_ms = int(self.settings.get("min_interval_ms", self.settings.get("req_delay_ms", 620)))
        limiter = RateLimiter(
            max_calls=max_per_min,
            period=60.0,
            min_interval=max(0.0, min_interval_ms / 1000.0),
        )
        return TornAPI(api_key=self.settings.get("api_key", ""), limiter=limiter)

    def _set_initial_rows_from_cache(self, ids: List[int]) -> None:
        """Pre-populate the table from cache so the UI is instant, then live-update."""
        self.rows = []
        self._rows_by_id.clear()

        cache_items = load_cache() if self.settings.get("load_cache_at_start", True) else []
        self._cache_map = {it.user_id: it for it in cache_items}
        logger.info("Loaded cache items: %d", len(cache_items))

        for uid in ids:
            info = (self._cache_map.get(uid) if self._cache_map else None) or TargetInfo(user_id=uid)
            info.error = None  # clear stale error before display
            self._rows_by_id[uid] = len(self.rows)
            self.rows.append(info)

        self.view.set_rows(self.rows)
        self.view.set_ignored(self.ignored)

    # ---------------- Connectivity ----------------

    def is_online(self) -> bool:
        return self._is_online

    def _check_connectivity(self):
        if self._connectivity_check_in_progress:
            return

        self._connectivity_check_in_progress = True
        if (
            self._pending_connectivity_reply is not None
            and not self._pending_connectivity_reply.isFinished()
        ):
            # Still waiting on a previous probe; skip to avoid piling up requests.
            self._connectivity_check_in_progress = False
            return

        try:
            request = QNetworkRequest(QUrl("https://api.torn.com/ping"))
            follow_attr = getattr(
                QNetworkRequest.Attribute, "FollowRedirectsAttribute", None
            )
            if follow_attr is not None:
                request.setAttribute(follow_attr, True)
            request.setRawHeader(
                b"User-Agent",
                b"TargetTracker/2.6 (connectivity)",
            )
            reply = self._netman.head(request)
            self._pending_connectivity_reply = reply
            reply.finished.connect(lambda r=reply: self._handle_connectivity_reply(r))
        except Exception as exc:
            logger.warning("Connectivity HEAD failed to start: %s", exc)
            self._connectivity_check_in_progress = False
            online, reason = self._fallback_socket_probe()
            self._apply_connectivity_result(online, reason if not online else "")

    def _handle_connectivity_reply(self, reply: QNetworkReply):
        self._connectivity_check_in_progress = False
        if self._pending_connectivity_reply is reply:
            self._pending_connectivity_reply = None

        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        error = reply.error()
        reason = reply.errorString() or ""

        if error == QNetworkReply.NetworkError.OperationCanceledError:
            reply.deleteLater()
            return

        online = False
        if error == QNetworkReply.NetworkError.NoError:
            online = True
        elif status is not None:
            # HTTP response received (even 4xx/5xx) implies connectivity.
            try:
                status_code = int(status)
            except (TypeError, ValueError):
                status_code = None
            if status_code is not None and status_code > 0:
                online = True

        logger.debug(
            "Connectivity check: status=%s error=%s online=%s",
            status,
            error,
            online,
        )

        reply.deleteLater()
        self._apply_connectivity_result(online, reason if not online else "")

    def _fallback_socket_probe(self) -> tuple[bool, str]:
        try:
            with socket.create_connection(("api.torn.com", 443), timeout=3):
                return True, ""
        except OSError as exc:
            return False, str(exc)

    def _apply_connectivity_result(self, online: bool, reason: str = ""):
        self._connectivity_check_in_progress = False
        online = bool(online)
        if online == self._is_online:
            return

        self._is_online = online
        self.onlineChanged.emit(online)

        if online:
            self._status("Back online. Refresh enabled.")
            if self._set_meta:
                self._set_meta("Online")
            self._offline_prompt_shown = False
            if self._current_target_set and not self._auto_timer.isActive():
                self._schedule_next_auto_refresh()
            self._last_offline_reason = ""
        else:
            reason = reason.strip()
            self._last_offline_reason = reason
            msg = "Offline: No internet connection detected."
            if reason:
                msg = f"{msg} ({reason})"
            self._status(msg)
            if self._set_meta:
                self._set_meta("Offline")
            self._auto_timer.stop()
            self._notify_auto_eta(None)
            if not self._offline_prompt_shown:
                try:
                    text = (
                        "No internet connection detected. Refresh controls have been disabled until you're back online."
                    )
                    if reason:
                        text += f"\n\nDetails: {reason}"
                    QMessageBox.warning(
                        self.view,
                        "Offline",
                        text,
                    )
                except Exception:
                    pass
                self._offline_prompt_shown = True

    # ---------------- Refresh / Fetching ----------------

    def refresh(self):
        """Start a new batched fetch if not already running."""
        if self._fetcher is not None:
            self._status("Fetch already in progress…")
            logger.warning("Refresh skipped: fetch already in progress.")
            return
        if not self._is_online:
            reason = self._last_offline_reason.strip()
            msg = "Offline: Unable to refresh without internet."
            if reason:
                msg = f"{msg} ({reason})"
            self._status(msg)
            sender = self.sender()
            if not isinstance(sender, QTimer) and not self._offline_prompt_shown:
                try:
                    text = "Unable to refresh because no internet connection is available."
                    if reason:
                        text += f"\n\nDetails: {reason}"
                    QMessageBox.warning(
                        self.view,
                        "Offline",
                        text,
                    )
                except Exception:
                    pass
                self._offline_prompt_shown = True
            return

        self._auto_timer.stop()
        self._notify_auto_eta(None)

        ids = self.targets_list()
        self._current_target_set = set(ids)
        total = len(ids)

        # --- cache-only startup ---
        if total == 0:
            cache_items = load_cache() if self.settings.get("load_cache_at_start", True) else []
            if cache_items:
                logger.info("No targets file, but cache found: showing %d cached row(s).", len(cache_items))
                self.rows = list(cache_items)
                self._rows_by_id = {r.user_id: i for i, r in enumerate(self.rows)}
                self.view.set_rows(self.rows)
                self.view.set_ignored(self.ignored)
                self._status(f"No targets file selected. Showing cached {len(self.rows)} row(s).")
                self._progress(len(self.rows), len(self.rows))
                QTimer.singleShot(0, self._prompt_add_or_load)
                self._fetcher = None
                return

            self.rows = []
            self._rows_by_id.clear()
            self.view.set_rows([])
            self._status("No targets yet. Use Add Targets… or Load Targets JSON.")
            self._progress(0, 0)
            self._prompt_add_or_load()
            self._fetcher = None
            return

        logger.info("Starting refresh: %d targets", total)
        self._set_initial_rows_from_cache(ids)
        self.view.set_fetching(True)   # prevent churn while streaming
        self._status(f"Loaded targets • {len(self.rows)} rows. Updating…")
        self._progress(0, total)
        self._errors = 0
        self._done = 0
        self._since_last_save = 0

        api = self.api()
        conc = int(self.settings.get("concurrency", 4))
        self._shutdown_event.clear()  # new run
        self._fetcher = BatchFetcher(api, concurrency=conc, stop_event=self._shutdown_event)
        fetcher = self._fetcher

        logger.info("BatchFetcher started: concurrency=%d", conc)

        def update_row_internal(info: TargetInfo):
            """Thread-safe row update."""
            if info.user_id not in self._current_target_set:
                return

            with self._rows_lock:
                idx = self._rows_by_id.get(info.user_id, None)
                if idx is None:
                    self._rows_by_id[info.user_id] = len(self.rows)
                    self.rows.append(info)
                else:
                    # Ensure index is still valid (defensive)
                    if 0 <= idx < len(self.rows):
                        self.rows[idx] = info
                    else:
                        logger.warning("Row index out of sync for user %s, rebuilding", info.user_id)
                        self._rows_by_id[info.user_id] = len(self.rows)
                        self.rows.append(info)

                if self._cache_map is not None:
                    self._cache_map[info.user_id] = info

        def on_one(info: TargetInfo):
            if info.error:
                self._errors += 1
                logger.debug("User %s error: %s", info.user_id, info.error)

            update_row_internal(info)
            self.view.update_or_insert(info)   # incremental update

            self._done += 1
            self._since_last_save += 1

            if self._done % 10 == 0 or self._done == total:
                logger.info("Progress: %d/%d (errors: %d)", self._done, total, self._errors)

            self._progress(self._done, total)
            self._status(f"Updating {self._done}/{total} • errors: {self._errors}")

            if self._since_last_save >= self._save_every:
                save_cache(self.rows)
                logger.info("Cache saved (partial).")
                self._since_last_save = 0

        def on_done():
            self.view.set_fetching(False)  # final resort + re-enable dynamic sort
            self._status(f"Done • {total} targets • {self._errors} errors")
            self._progress(total, total)

            save_cache(self.rows)
            logger.info("Batch finished. Total=%d, errors=%d; cache saved.", total, self._errors)

            self._fetcher = None
            self._schedule_next_auto_refresh()

        fetcher.signals.one_done.connect(on_one)
        fetcher.signals.one_done.connect(lambda *_: fetcher.dec_and_maybe_finish())
        fetcher.signals.batch_done.connect(on_done)
        fetcher.fetch_ids(ids)

    # ---------------- Exports ----------------

    def export_csv(self):
        if not self.rows:
            QMessageBox.information(self.view, "Export CSV", "Nothing to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self.view, "Export CSV", "targets.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            from csv import writer
            w = writer(f)
            w.writerow(["Name", "ID", "Level", "Status", "Details", "Until", "Faction", "Last Action", "Error"])
            for r in self.rows:
                w.writerow([
                    r.name,
                    r.user_id,
                    r.level or "",
                    r.status_chip(),
                    r.status_desc,
                    r.until_human(),
                    r.faction,
                    r.last_action_relative or r.last_action_status or "",
                    r.error or ""
                ])
        self._status("CSV export complete.")
        logger.info("Exported CSV to %r", path)

    def export_json(self):
        if not self.rows:
            QMessageBox.information(self.view, "Export JSON", "Nothing to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self.view, "Export JSON", "targets.json", "JSON (*.json)")
        if not path:
            return
        ids = [int(r.user_id) for r in self.rows if r.user_id is not None]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ids, f, indent=2)
            f.write("\n")
        self._status("JSON export complete.")
        logger.info("Exported JSON to %r", path)

    # ---------------- Dialogs ----------------

    def show_settings(self):
        import importlib

        if self._settings_dialog_module is None:
            self._settings_dialog_module = importlib.import_module("settings_dialog")
        SettingsDialog = self._settings_dialog_module.SettingsDialog
        module_file = getattr(self._settings_dialog_module, "__file__", "<unknown>")
        print("SettingsDialog from:", module_file)

        dlg = SettingsDialog(self.settings, self.view)
        new_st = {}
        dlg.saved.connect(lambda s: new_st.update(s))
        if dlg.exec():
            if new_st:
                self.settings.update(new_st)
                save_settings(self.settings)
                self._save_every = int(self.settings.get("save_cache_every", 20))
                logger.info("Settings saved: %r", new_st)
                self._update_auto_timer()
                self.refresh()


    def show_ignore(self):
        infos: List[TargetInfo] = []
        if self._cache_map is None:
            cache_list = load_cache()
            self._cache_map = {t.user_id: t for t in cache_list}

        for uid in sorted(self.ignored):
            idx = self._rows_by_id.get(uid)
            if idx is not None:
                infos.append(self.rows[idx])
            else:
                info = self._cache_map.get(uid, TargetInfo(user_id=uid))
                infos.append(info)

        dlg = IgnoreDialog(self.ignored, infos, self.view)
        if dlg.exec():
            self.ignored = dlg.ignored
            save_ignore(self.ignored)
            logger.info("Ignore list updated: %d id(s)", len(self.ignored))
            self.view.set_ignored(self.ignored)
            self._update_meta(self._done, len(self.rows))

    def show_about(self):
        AboutDialog(self.view).exec()

    def pick_targets_file(self):
        path, _ = QFileDialog.getOpenFileName(self.view, "Pick targets JSON", "", "JSON (*.json)")
        if not path:
            return
        self.settings["targets_file"] = path
        save_settings(self.settings)
        logger.info("Targets file changed to %r", path)
        self.refresh()

    # ---------------- Ignore (works during fetch) ----------------

    def ignore_ids_add(self, ids: List[int]):
        if not ids:
            return
        for uid in ids:
            self.ignored.add(uid)
        save_ignore(self.ignored)
        logger.info("Ignored %d id(s). Total ignored: %d", len(ids), len(self.ignored))
        self.view.set_ignored(self.ignored)
        self._update_meta(self._done, len(self.rows))

    def unignore_ids_remove(self, ids: List[int]):
        if not ids:
            return
        for uid in ids:
            self.ignored.discard(uid)
        save_ignore(self.ignored)
        logger.info("Unignored; total ignored: %d", len(self.ignored))
        self.view.set_ignored(self.ignored)
        self._update_meta(self._done, len(self.rows))

    # ---------------- Add / Remove targets ----------------

    def add_targets(self, ids: List[int]):
        if not ids:
            return
        path = self.settings.get("targets_file")
        updated_ids = add_targets_to_file(path, ids)
        if updated_ids is None:
            QMessageBox.warning(self.view, "Add Targets", "Failed to write targets file.")
            return

        new_only = [i for i in ids if i not in self._rows_by_id]

        # Thread-safe row addition
        with self._rows_lock:
            for uid in new_only:
                self._rows_by_id[uid] = len(self.rows)
                ti = TargetInfo(user_id=uid)
                self.rows.append(ti)
                self.view.update_or_insert(ti)

        self._current_target_set.update(ids)

        QMessageBox.information(self.view, "Add Targets", f"Added {len(ids)} target(s).")

        if self._fetcher is None:
            self.refresh()

    def remove_targets(self, ids: List[int]):
        if not ids:
            return
        path = self.settings.get("targets_file")
        updated_ids = remove_targets_from_file(path, ids)
        if updated_ids is None:
            QMessageBox.warning(self.view, "Remove Targets", "Failed to update targets file.")
            return

        to_remove = set(ids)

        # Thread-safe row removal
        with self._rows_lock:
            self.rows = [r for r in self.rows if r.user_id not in to_remove]
            self._rows_by_id = {r.user_id: i for i, r in enumerate(self.rows)}

        self.view.set_rows(self.rows)
        self._current_target_set.difference_update(to_remove)
        self._update_meta(self._done, len(self.rows))
        QMessageBox.information(self.view, "Remove Targets", f"Removed {len(ids)} target(s).")

    # ---------------- Auto refresh ----------------

    def _update_auto_timer(self):
        user_sec = int(self.settings.get("auto_refresh_sec", 0))
        if user_sec > 0:
            self._auto_interval = user_sec
            logger.info("Auto-refresh interval (settings): %s second(s)", user_sec)
        else:
            self._auto_interval = 65
            logger.info("Auto-refresh interval set to default: %s second(s)", self._auto_interval)
        self._auto_timer.stop()
        self._notify_auto_eta(-1 if self._auto_interval <= 0 else None)

    def _schedule_next_auto_refresh(self):
        if self._shutdown_event.is_set():
            return
        if self._auto_interval <= 0:
            self._notify_auto_eta(-1)
            return
        if not self._current_target_set:
            self._notify_auto_eta(None)
            return
        self._auto_timer.stop()
        self._auto_timer.start(self._auto_interval * 1000)
        self._notify_auto_eta(self._auto_interval)
        logger.info("Next auto refresh scheduled in %s second(s)", self._auto_interval)

    # ---------------- First-run helpers ----------------

    def _prompt_add_or_load(self):
        msg = QMessageBox(self.view)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("No Targets Yet")
        msg.setText(
            "Your target list is empty.\n\n"
            "Choose an option below to get started."
        )
        btn_add = msg.addButton("Add Targets…", QMessageBox.ButtonRole.AcceptRole)
        btn_load = msg.addButton("Load Targets JSON…", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)

        msg.exec()

        clicked = msg.clickedButton()
        if clicked is btn_add:
            self._show_add_targets_dialog()
        elif clicked is btn_load:
            self.pick_targets_file()

    def _show_add_targets_dialog(self):
        dlg = AddTargetsDialog(self.view)
        added: List[int] = []
        def _accept(ids: List[int]):
            nonlocal added
            added = list(ids or [])
        dlg.accepted_ids.connect(_accept)

        if dlg.exec():
            if added:
                self.add_targets(added)

    # ---------------- Shutdown ----------------

    def shutdown(self):
        """Stop timers, cancel fetch, and wait briefly so the process can exit cleanly."""
        try:
            self._shutdown_event.set()
            self._auto_timer.stop()
            self._connectivity_timer.stop()
            if self._pending_connectivity_reply is not None:
                try:
                    self._pending_connectivity_reply.abort()
                except Exception:
                    pass
                self._pending_connectivity_reply.deleteLater()
                self._pending_connectivity_reply = None
            if self._fetcher is not None:
                self._fetcher.stop(wait_ms=2000)
                self._fetcher = None
        except Exception:
            pass


# ---------------- main ----------------
def main():
    """Application entry point with proper resource management."""
    # Setup logging first (before any other operations)
    setup_logging(log_level="INFO", console=True)

    # Qt6 HiDPI is fine by default; slight nudge:
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    # Setup crash diagnostics with proper resource cleanup
    crash_path = os.path.join(get_appdata_dir(), CONFIG.CRASH_LOG_FILE)
    crash_log_handle = None

    try:
        # Open crash log file
        crash_log_handle = open(crash_path, "w", encoding="utf-8")
        faulthandler.enable(crash_log_handle)
        logger.info("Crash diagnostics enabled: %s", crash_path)
    except Exception as exc:
        logger.warning("Crash diagnostics file setup failed: %s", exc)
        # Fallback to stderr
        try:
            faulthandler.enable()
            logger.debug("Faulthandler enabled to stderr")
        except Exception as fallback_exc:
            logger.debug("Faulthandler fallback failed: %s", fallback_exc)

    exit_code = 1  # Default exit code in case of early failure

    try:
        app = QApplication([])
        app.setApplicationName(CONFIG.APP_NAME)
        app.setApplicationVersion(CONFIG.APP_VERSION)

        dark_qss = apply_darkstyle(app)

        # ensure AppData folder & first-run targets.json
        ensure_first_run_targets()

        # single window only
        win = MainWindow()
        if dark_qss:
            win.setStyleSheet(dark_qss)

        # Construct controller and attach (avoids circular imports)
        controller = Controller(win.view)
        win.attach_controller(controller)

        # ensure controller shuts down on app quit
        app.aboutToQuit.connect(controller.shutdown)

        # Robust "start maximized" while still remembering size/pos in settings
        try:
            st = load_settings()
            last_size = st.get("last_size")
            last_pos = st.get("last_pos")
            start_max = bool(st.get("start_maximized", True))  # default: maximize

            if start_max:
                win.showMaximized()
            else:
                if isinstance(last_size, list) and len(last_size) == 2:
                    w, h = max(CONFIG.MIN_WINDOW_WIDTH, int(last_size[0])), max(CONFIG.MIN_WINDOW_HEIGHT, int(last_size[1]))
                    win.resize(w, h)
                if isinstance(last_pos, list) and len(last_pos) == 2:
                    x, y = int(last_pos[0]), int(last_pos[1])
                    win.move(x, y)
                win.show()
        except Exception as e:
            logger.warning("Failed to restore window state: %s", e)
            win.showMaximized()

        # Show onboarding after the window is up (first run / missing API key)
        QTimer.singleShot(0, lambda: maybe_show_onboarding(win))

        # Run the app
        logger.info("%s v%s starting", CONFIG.APP_NAME, CONFIG.APP_VERSION)
        exit_code = app.exec()
        logger.info("Application exiting with code %d", exit_code)

    except Exception as e:
        logger.exception("Fatal error during application startup: %s", e)
        exit_code = 1

    finally:
        # Clean up crash log file handle
        if crash_log_handle:
            try:
                faulthandler.disable()
                crash_log_handle.close()
                logger.debug("Crash log closed cleanly")
            except Exception as e:
                logger.debug("Error closing crash log: %s", e)

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
