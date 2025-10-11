from __future__ import annotations

import json
import logging
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import threading
from typing import Any, Dict, Optional

from models import TargetInfo
from rate_limiter import RateLimiter

logger = logging.getLogger("TargetTracker.API")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


class TornAPI:
    """
    Thin wrapper around Torn's API with:
      - global shared RateLimiter (<=100/min by default),
      - robust retries (429 / transient 5xx / network),
      - backoff with jitter,
      - Retry-After header support,
      - graceful parsing when selections are missing (limited keys).

    Stop-aware: accepts an optional threading.Event to cancel waits and retries.
    """

    BASE = "https://api.torn.com"

    def __init__(self, api_key: str, limiter: RateLimiter):
        self.api_key = (api_key or "").strip()
        self.limiter = limiter

        # One opener for keep-alive
        self._opener = urllib.request.build_opener()
        self._default_headers = {
            "User-Agent": "TargetTracker/2.6 (https://github.com/skillerious) Python-urllib",
            "Accept": "application/json",
            "Connection": "keep-alive",
        }

    # ------------------ public ------------------
    def fetch_user(self, user_id: int, stop_event: Optional[threading.Event] = None) -> TargetInfo:
        """
        Fetches a user's info. Retries on 429 and transient conditions with an exponential backoff.
        Returns a populated TargetInfo (with .error on failure). Stops early if stop_event is set.
        """
        uid = int(user_id)
        selections = "basic,profile"
        qs = urllib.parse.urlencode({"selections": selections, "key": self.api_key})
        url = f"{self.BASE}/user/{uid}?{qs}"

        # Retry policy (kept generous, but lower per-attempt timeout for shutdown responsiveness)
        max_attempts = 8
        base_backoff = 0.6
        max_backoff = 8.0
        hard_timeout = 10.0

        def _cancelled() -> Optional[TargetInfo]:
            if stop_event and stop_event.is_set():
                return self._info_with_error(uid, "Cancelled")
            return None

        for attempt in range(1, max_attempts + 1):
            c = _cancelled()
            if c:
                return c

            # global rate limit gate (stop-aware)
            if not self.limiter.acquire_or_stop(stop_event):
                return self._info_with_error(uid, "Cancelled")

            try:
                req = urllib.request.Request(url, headers=self._default_headers, method="GET")
                with self._opener.open(req, timeout=hard_timeout) as resp:
                    code = getattr(resp, "status", 200)
                    payload = resp.read()
                    data = self._parse_json_safely(payload)

                # Torn logical error inside 200?
                err = self._extract_torn_error(data)
                if err:
                    code_num, msg = err
                    if self._is_retryable_torn_error(code_num, msg):
                        delay = self._advise_backoff(resp_headers=getattr(resp, "headers", {}), attempt=attempt,
                                                     base=base_backoff, cap=max_backoff)
                        self._apply_penalty(delay, why=f"Torn error {code_num}: {msg}")
                        if self._sleep(delay, stop_event):
                            return self._info_with_error(uid, "Cancelled")
                        continue
                    return self._info_with_error(uid, msg or f"Torn error {code_num}")

                # OK → parse into TargetInfo
                info = self._to_target_info(uid, data)
                return info

            except urllib.error.HTTPError as e:
                # HTTP layer error (e.g., 429, 5xx)
                if e.code == 429 or (500 <= e.code < 600):
                    retry_after = self._parse_retry_after(e.headers)
                    delay = self._advise_backoff(resp_headers=e.headers, attempt=attempt,
                                                 base=base_backoff, cap=max_backoff, retry_after=retry_after)
                    self._apply_penalty(delay, why=f"HTTP {e.code}")
                    if self._sleep(delay, stop_event):
                        return self._info_with_error(uid, "Cancelled")
                    continue
                if e.code in (401, 403):
                    return self._info_with_error(uid, "Unauthorized / incorrect API key")
                if e.code == 404:
                    return self._info_with_error(uid, "User not found")
                # Other client errors: surface
                return self._info_with_error(uid, f"HTTP {e.code}")

            except (urllib.error.URLError, TimeoutError) as e:
                # Network flake — retry
                delay = self._advise_backoff(resp_headers=None, attempt=attempt,
                                             base=base_backoff, cap=max_backoff)
                if self._sleep(delay, stop_event):
                    return self._info_with_error(uid, "Cancelled")
                continue

            except Exception as e:
                # Unexpected — don't explode the worker; record and stop
                return self._info_with_error(uid, f"Unexpected error: {e!r}")

        # Gave up after retries
        return self._info_with_error(uid, "Too many requests / temporary failure (retried and gave up)")

    # ------------------ helpers ------------------
    def _parse_json_safely(self, payload: bytes) -> Dict[str, Any]:
        try:
            return json.loads(payload.decode("utf-8"))
        except Exception:
            return {}

    def _extract_torn_error(self, data: Dict[str, Any]) -> Optional[tuple]:
        err = data.get("error")
        if isinstance(err, dict):
            return (err.get("code"), err.get("error"))
        return None

    def _is_retryable_torn_error(self, code: Optional[int], msg: Optional[str]) -> bool:
        if code == 5:
            return True
        m = (msg or "").lower()
        if "too many request" in m or "rate limit" in m or "try again later" in m:
            return True
        return False

    def _parse_retry_after(self, headers) -> Optional[float]:
        if not headers:
            return None
        try:
            ra = headers.get("Retry-After")
            if not ra:
                return None
            return float(ra)
        except Exception:
            return None

    def _advise_backoff(
        self,
        resp_headers,
        attempt: int,
        base: float,
        cap: float,
        retry_after: Optional[float] = None,
    ) -> float:
        if retry_after is None and resp_headers:
            retry_after = self._parse_retry_after(resp_headers)

        if retry_after and retry_after > 0:
            return min(cap, retry_after + random.uniform(0.05, 0.25))

        backoff = base * (2 ** (attempt - 1))
        backoff = min(cap, backoff)
        jitter = backoff * random.uniform(0.0, 0.3)
        return backoff + jitter

    def _apply_penalty(self, seconds: float, why: str = "") -> None:
        self.limiter.penalize(seconds)
        if seconds >= 1.0:
            logger.info("Backoff %.2fs due to %s", seconds, why)

    def _sleep(self, seconds: float, stop_event: Optional[threading.Event] = None) -> bool:
        """
        Sleeps for up to `seconds`. If stop_event is set during the wait,
        returns True (caller should treat as 'cancelled').
        """
        if seconds <= 0:
            return False
        if stop_event:
            return stop_event.wait(seconds)
        time.sleep(seconds)
        return False

    # ------------ mapping to TargetInfo ------------
    def _to_target_info(self, uid: int, data: Dict[str, Any]) -> TargetInfo:
        # 'basic' fields
        name = data.get("name")
        level = data.get("level")
        status = data.get("status") or {}

        status_state = status.get("state") or None
        status_desc = status.get("description") or None
        status_until = status.get("until") or None  # epoch seconds; may be 0

        # 'last_action'
        last_action = data.get("last_action") or {}
        last_action_status = last_action.get("status") or None
        last_action_relative = last_action.get("relative") or None

        # 'profile' fields — faction may be an object or absent
        faction_name = None
        fac = data.get("faction")
        if isinstance(fac, dict):
            fname = fac.get("faction_name") or fac.get("name")
            fid = fac.get("faction_id") or fac.get("ID") or fac.get("id")
            if fname:
                faction_name = fname if fid is None else f"{fname} [{fid}]"

        info = TargetInfo(
            user_id=uid,
            name=name,
            level=level if isinstance(level, int) else None,
            status_state=status_state,
            status_desc=status_desc,
            status_until=status_until if isinstance(status_until, int) and status_until > 0 else None,
            last_action_status=last_action_status,
            last_action_relative=last_action_relative,
            faction=faction_name,
        )
        try:
            info.ok = (str(status_state).lower() == "okay")
        except Exception:
            pass
        return info

    def _info_with_error(self, uid: int, msg: str) -> TargetInfo:
        info = TargetInfo(user_id=uid)
        info.error = msg
        return info
