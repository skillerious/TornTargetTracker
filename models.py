from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import logging

logger = logging.getLogger("TargetTracker.Models")

@dataclass
class TargetInfo:
    user_id: int
    name: str = ""
    level: Optional[int] = None
    status_state: str = "Unknown"
    status_desc: str = ""
    status_until: Optional[int] = None
    last_action_status: str = ""
    last_action_relative: str = ""
    faction: str = ""
    ok: bool = False
    error: Optional[str] = None

    def until_human(self) -> str:
        if not self.status_until:
            return ""
        try:
            dt = datetime.fromtimestamp(self.status_until, tz=timezone.utc)
            return dt.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def profile_url(self) -> str:
        return f"https://www.torn.com/profiles.php?XID={self.user_id}"

    def status_chip(self) -> str:
        state = (self.status_state or "").strip()
        desc = (self.status_desc or "").strip()
        combined = f"{state} {desc}".strip().lower()
        if "okay" in combined:
            return "Okay"
        if "hospital" in combined:
            return "Hospital"
        if "federal" in combined and "jail" in combined:
            return "Federal Jail"
        if "jail" in combined:
            return "Jail"
        if "abroad" in combined:
            return "Abroad"
        if "travel" in combined:
            return "Traveling"
        if "offline" in combined:
            return "Offline"
        return state or desc or "Unknown"

    def matches(self, text: str) -> bool:
        t = text.lower().strip()
        if not t:
            return True
        return t in str(self.user_id) or t in (self.name or "").lower()

    # Cache helpers
    def to_cache_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @staticmethod
    def from_cache_dict(d: Dict[str, Any]) -> "TargetInfo":
        """
        Deserialize from cache dictionary with robust validation.
        Returns a TargetInfo with error set if data is invalid.
        """
        try:
            # Validate and extract user_id (required field)
            user_id = d.get("user_id")
            if user_id is None:
                raise ValueError("user_id is required")

            try:
                user_id = int(user_id)
            except (ValueError, TypeError):
                raise ValueError(f"Invalid user_id: {user_id!r}")

            if user_id <= 0:
                raise ValueError(f"user_id must be positive: {user_id}")

            # Safely extract and validate optional fields
            level = d.get("level")
            if level is not None:
                try:
                    level = int(level)
                    if level < 1 or level > 10000:  # Torn level range
                        logger.warning("Level out of expected range: %s", level)
                except (ValueError, TypeError):
                    logger.warning("Invalid level value: %r, ignoring", level)
                    level = None

            status_until = d.get("status_until")
            if status_until is not None:
                try:
                    status_until = int(status_until)
                    if status_until < 0:
                        status_until = None
                except (ValueError, TypeError):
                    logger.warning("Invalid status_until value: %r, ignoring", status_until)
                    status_until = None

            # Build TargetInfo with validated data
            return TargetInfo(
                user_id=user_id,
                name=str(d.get("name", "")),
                level=level,
                status_state=str(d.get("status_state", "Unknown")),
                status_desc=str(d.get("status_desc", "")),
                status_until=status_until,
                last_action_status=str(d.get("last_action_status", "")),
                last_action_relative=str(d.get("last_action_relative", "")),
                faction=str(d.get("faction", "")),
                ok=bool(d.get("ok", False)),
                error=d.get("error"),  # Keep error as-is (can be None or str)
            )

        except ValueError as e:
            # Data validation failed - return error target
            logger.warning("Failed to deserialize TargetInfo: %s", e)
            fallback_id = 0
            try:
                fallback_id = int(d.get("user_id", 0))
            except Exception:
                pass
            return TargetInfo(
                user_id=fallback_id if fallback_id > 0 else 0,
                error=f"Cache data corrupted: {e}"
            )
        except Exception as e:
            # Unexpected error
            logger.exception("Unexpected error deserializing TargetInfo: %s", e)
            return TargetInfo(user_id=0, error="Failed to load cached data")
