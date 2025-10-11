from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from datetime import datetime, timezone

@dataclass
class TargetInfo:
    user_id: int
    name: str = "—"
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
        s = (self.status_state or "").strip().lower()
        if "okay" in s:
            return "Okay"
        if "hospital" in s:
            return "Hospital"
        if "federal" in s and "jail" in s:
            return "Federal Jail"
        if "jail" in s:
            return "Jail"
        if "travel" in s:
            return "Traveling"
        if "offline" in s:
            return "Offline"
        return self.status_state or "Unknown"

    def matches(self, text: str) -> bool:
        t = text.lower().strip()
        if not t:
            return True
        return t in str(self.user_id) or t in (self.name or "").lower()

    # Cache helpers
    def to_cache_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_cache_dict(d: Dict[str, Any]) -> "TargetInfo":
        return TargetInfo(
            user_id=int(d.get("user_id", 0)),
            name=d.get("name", "—"),
            level=d.get("level", None),
            status_state=d.get("status_state", "Unknown"),
            status_desc=d.get("status_desc", ""),
            status_until=d.get("status_until", None),
            last_action_status=d.get("last_action_status", ""),
            last_action_relative=d.get("last_action_relative", ""),
            faction=d.get("faction", ""),
            ok=bool(d.get("ok", False)),
            error=d.get("error", None),
        )
