from __future__ import annotations

import threading
import time
from typing import Optional


class RateLimiter:
    """
    Thread-safe global limiter for all API calls.

    Strategy:
      - Token-bucket for <= max_calls per 'period' seconds (shared across threads).
      - Optional min_interval floor between consecutive calls (shared).
      - Supports 'penalize(seconds)' to back off after 429s; acquire() honors a cooldown window.
      - Fair: threads block until a token is available (no busy-wait).

    Typical config for Torn:
      RateLimiter(max_calls=100, period=60.0, min_interval=0.62)
    """

    def __init__(self, max_calls: int, period: float, min_interval: float = 0.0):
        if max_calls <= 0 or period <= 0:
            raise ValueError("max_calls and period must be > 0")
        self._capacity = float(max_calls)
        self._period = float(period)
        self._min_interval = float(min_interval)

        self._lock = threading.Lock()
        self._tokens = float(max_calls)
        self._last_refill = time.monotonic()
        self._last_call = 0.0

        # cooldown_until: block *all* callers until this time (used after 429s)
        self._cooldown_until: float = 0.0

    # ---- internal ----
    def _refill_locked(self, now: float) -> None:
        # refill tokens based on elapsed time
        elapsed = max(0.0, now - self._last_refill)
        rate_per_sec = self._capacity / self._period
        add = elapsed * rate_per_sec
        if add > 0:
            self._tokens = min(self._capacity, self._tokens + add)
            self._last_refill = now

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    # ---- public API ----
    def acquire(self) -> None:
        """
        Block until the caller is allowed to proceed. Must be called
        immediately before performing the HTTP request.
        """
        while True:
            now = time.monotonic()
            with self._lock:
                # Check cooldown first (e.g., after 429)
                if now < self._cooldown_until:
                    wait = self._cooldown_until - now
                else:
                    self._refill_locked(now)

                    if self._tokens >= 1.0:
                        # Enforce global min_interval since *last* call
                        since_last = now - self._last_call
                        if self._min_interval > 0 and since_last < self._min_interval:
                            wait = self._min_interval - since_last
                        else:
                            # Consume token and proceed
                            self._tokens -= 1.0
                            self._last_call = now
                            return
                    else:
                        # Time until next token is available
                        rate_per_sec = self._capacity / self._period
                        deficit = 1.0 - self._tokens
                        wait = max(0.0, deficit / rate_per_sec)

            # sleep outside lock
            self._sleep(wait)

    def acquire_or_stop(self, stop_event: Optional[threading.Event]) -> bool:
        """
        Like acquire(), but returns False if stop_event is set while waiting,
        so callers can abort promptly during shutdown.
        """
        while True:
            now = time.monotonic()
            with self._lock:
                if now < self._cooldown_until:
                    wait = self._cooldown_until - now
                else:
                    self._refill_locked(now)

                    if self._tokens >= 1.0:
                        since_last = now - self._last_call
                        if self._min_interval > 0 and since_last < self._min_interval:
                            wait = self._min_interval - since_last
                        else:
                            self._tokens -= 1.0
                            self._last_call = now
                            return True
                    else:
                        rate_per_sec = self._capacity / self._period
                        deficit = 1.0 - self._tokens
                        wait = max(0.0, deficit / rate_per_sec)

            if stop_event and stop_event.wait(wait):
                return False
            self._sleep(0.0)  # yield

    def penalize(self, seconds: float) -> None:
        """
        Enter a global cooldown window for `seconds`. Subsequent acquire()
        calls will wait until cooldown elapses.
        """
        if seconds <= 0:
            return
        now = time.monotonic()
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, now + seconds)

    def set_min_interval(self, seconds: float) -> None:
        """Adjust the global floor between calls (optional)."""
        if seconds < 0:
            seconds = 0
        with self._lock:
            self._min_interval = float(seconds)

    # Optional helpers for diagnostics
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "tokens": round(self._tokens, 3),
                "capacity": self._capacity,
                "period": self._period,
                "min_interval": self._min_interval,
                "cooldown_remaining": max(0.0, self._cooldown_until - time.monotonic()),
            }

    def __repr__(self) -> str:
        snap = self.snapshot()
        return (f"<RateLimiter tokens={snap['tokens']} cap={snap['capacity']} "
                f"period={snap['period']}s minInt={snap['min_interval']}s "
                f"cooldown={round(snap['cooldown_remaining'], 2)}s>")
