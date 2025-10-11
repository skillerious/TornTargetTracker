from __future__ import annotations

import os
import logging
import threading
from typing import List, Set, Callable, Optional, Dict

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

# App modules
from models import TargetInfo
from storage import (
    load_settings, save_settings, load_ignore, save_ignore,
    load_targets_from_file, load_cache, save_cache, add_targets_to_file,
    remove_targets_from_file
)
from api import TornAPI
from workers import BatchFetcher
from views import MainView, IgnoreDialog, AboutDialog, AddTargetsDialog
from settings_dialog import SettingsDialog
from rate_limiter import RateLimiter
from controllers import MainWindow, apply_darkstyle, ensure_first_run_targets

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
    """
    Controller with incremental UI updates to prevent table jumping while fetching:
      • View is populated once (from cache), then each fetched row is upserted.
      • Sorting is coalesced in the view; selection & scroll are preserved.

    Shutdown-aware: exposes shutdown() to stop workers and timers on app close.
    """

    def __init__(self, view: MainView):
        super().__init__(view)
        self.view = view

        # Settings & persistent lists
        self.settings = load_settings()
        self.ignored: Set[int] = load_ignore()

        # In-memory rows & indices
        self.rows: List[TargetInfo] = []
        self._rows_by_id: Dict[int, int] = {}
        self._cache_map: Optional[Dict[int, TargetInfo]] = None

        # Prevent updates for removed IDs mid-flight
        self._current_target_set: Set[int] = set()

        # Auto-refresh
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self.refresh)

        # UI status callbacks (set from MainWindow)
        self._set_status: Optional[Callable[[str], None]] = None
        self._set_progress: Optional[Callable[[int, int], None]] = None
        self._set_meta: Optional[Callable[[str], None]] = None

        # Fetch machinery & accounting
        self._fetcher: Optional[BatchFetcher] = None
        self._errors = 0
        self._done = 0
        self._save_every = int(self.settings.get("save_cache_every", 20))
        self._since_last_save = 0

        # Global shutdown flag for all workers/backoff waits
        self._shutdown_event = threading.Event()

        # Wire view events
        self.view.request_refresh.connect(self.refresh)
        self.view.request_export.connect(self.export_csv)
        self.view.request_open_settings.connect(self.show_settings)
        self.view.request_manage_ignore.connect(self.show_ignore)
        self.view.request_load_targets.connect(self.pick_targets_file)
        self.view.request_show_about.connect(self.show_about)
        self.view.request_add_targets.connect(self.add_targets)
        self.view.request_remove_targets.connect(self.remove_targets)

        self.view.ignore_ids.connect(self.ignore_ids_add)
        self.view.unignore_ids.connect(self.unignore_ids_remove)
        self.view.set_ignored(self.ignored)

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
    ):
        self._set_status = status_cb
        self._set_progress = progress_cb
        self._set_meta = meta_cb

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

    def _visible_count(self) -> int:
        if not self.rows:
            return 0
        return sum(1 for r in self.rows if r.user_id not in self.ignored)

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

    # ---------------- Refresh / Fetching ----------------

    def refresh(self):
        """Start a new batched fetch if not already running."""
        if self._fetcher is not None:
            self._status("Fetch already in progress…")
            logger.warning("Refresh skipped: fetch already in progress.")
            return

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
            if info.user_id not in self._current_target_set:
                return
            idx = self._rows_by_id.get(info.user_id, None)
            if idx is None:
                self._rows_by_id[info.user_id] = len(self.rows)
                self.rows.append(info)
            else:
                self.rows[idx] = info
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

        fetcher.signals.one_done.connect(on_one)
        fetcher.signals.one_done.connect(lambda *_: fetcher.dec_and_maybe_finish())
        fetcher.signals.batch_done.connect(on_done)
        fetcher.fetch_ids(ids)

    # ---------------- CSV Export ----------------

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
        self._status("Export complete.")
        logger.info("Exported CSV to %r", path)

    # ---------------- Dialogs ----------------

    def show_settings(self):
        dlg = SettingsDialog(self.settings, self.view)
        new_st: dict = {}
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
        self.rows = [r for r in self.rows if r.user_id not in to_remove]
        self._rows_by_id = {r.user_id: i for i, r in enumerate(self.rows)}
        self.view.set_rows(self.rows)
        self._current_target_set.difference_update(to_remove)
        self._update_meta(self._done, len(self.rows))
        QMessageBox.information(self.view, "Remove Targets", f"Removed {len(ids)} target(s).")

    # ---------------- Auto refresh ----------------

    def _update_auto_timer(self):
        sec = int(self.settings.get("auto_refresh_sec", 0))
        if sec > 0:
            self._auto_timer.start(1000 * sec)
            logger.info("Auto-refresh enabled: every %s second(s)", sec)
        else:
            self._auto_timer.stop()
            logger.info("Auto-refresh disabled")

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
            if self._fetcher is not None:
                self._fetcher.stop(wait_ms=2000)
                self._fetcher = None
        except Exception:
            pass


# ---------------- main ----------------
def main():
    # Qt6 HiDPI is fine by default; slight nudge:
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    app = QApplication([])
    app.setApplicationName("Target Tracker")

    apply_darkstyle(app)

    # ensure AppData folder & first-run targets.json
    ensure_first_run_targets()

    # single window only
    win = MainWindow()

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
                w, h = int(last_size[0]), int(last_size[1])
                if w > 300 and h > 200:
                    win.resize(w, h)
            if isinstance(last_pos, list) and len(last_pos) == 2:
                x, y = int(last_pos[0]), int(last_pos[1])
                win.move(x, y)
            win.show()
    except Exception:
        win.showMaximized()

    # Show onboarding after the window is up (first run / missing API key)
    QTimer.singleShot(0, lambda: maybe_show_onboarding(win))

    # Run the app
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
