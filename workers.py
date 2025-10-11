from __future__ import annotations
import logging
from typing import Iterable
from PyQt6.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool
from api import TornAPI
from models import TargetInfo

logger = logging.getLogger("TargetTracker.Worker")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

class FetchSignals(QObject):
    one_done = pyqtSignal(object)  # TargetInfo
    batch_done = pyqtSignal()

class FetchWorker(QRunnable):
    def __init__(self, api: TornAPI, user_id: int, signals: FetchSignals):
        super().__init__()
        self.api = api
        self.user_id = user_id
        self.signals = signals

    def run(self):
        try:
            logger.debug("Fetching user %s …", self.user_id)
            info: TargetInfo = self.api.fetch_user(self.user_id)
        except Exception as e:
            # Never let a worker crash stall the batch: emit an error result.
            logger.exception("Worker crashed for user %s: %s", self.user_id, e)
            info = TargetInfo(user_id=self.user_id, error=f"Worker exception: {e}")
        # Emit safely even if UI was closed
        try:
            self.signals.one_done.emit(info)
        except RuntimeError:
            logger.debug("Signal target deleted while emitting for user %s", self.user_id)

class BatchFetcher(QObject):
    def __init__(self, api: TornAPI, concurrency: int = 4):
        super().__init__()
        self.api = api
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(max(1, int(concurrency)))
        self.signals = FetchSignals(self)
        self._pending = 0

    def fetch_ids(self, ids: Iterable[int]):
        self._pending = 0
        count = 0
        for uid in ids:
            count += 1
            self._pending += 1
            w = FetchWorker(self.api, uid, self.signals)
            self.pool.start(w)
        logger.info("Enqueued %d worker(s); pending=%d; pool max=%d", count, self._pending, self.pool.maxThreadCount())
        if self._pending == 0:
            # Nothing to do — finish immediately (consistency)
            try:
                self.signals.batch_done.emit()
            except RuntimeError:
                pass

    def dec_and_maybe_finish(self):
        self._pending -= 1
        if self._pending <= 0:
            logger.info("Batch complete (pending=%d).", self._pending)
            try:
                self.signals.batch_done.emit()
            except RuntimeError:
                pass
