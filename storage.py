from __future__ import annotations
import json
import os
import sys
from typing import Dict, List, Set, Optional
from datetime import datetime, timezone
from models import TargetInfo

APP_NAME = "TargetTracker"
TARGET_FILE_DEFAULT = "target.json"

def _platform_appdata_root() -> str:
    if sys.platform.startswith("win"):
        return os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    return os.path.join(os.path.expanduser("~"), ".local", "share")

def get_appdata_dir() -> str:
    path = os.path.join(_platform_appdata_root(), APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path

def _settings_path() -> str:
    return os.path.join(get_appdata_dir(), "settings.json")

def _ignore_path() -> str:
    return os.path.join(get_appdata_dir(), "ignore.json")

def _cache_path() -> str:
    return os.path.join(get_appdata_dir(), "cache_targets.json")

def load_settings() -> dict:
    p = _settings_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {
        "api_key": "",
        "targets_file": TARGET_FILE_DEFAULT,
        "concurrency": 4,
        "req_delay_ms": 350,
        "auto_refresh_sec": 0,
        "load_cache_at_start": True,
    }

def save_settings(st: dict) -> None:
    try:
        with open(_settings_path(), "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
    except Exception:
        pass

def load_ignore() -> Set[int]:
    p = _ignore_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return {int(x) for x in data if str(x).isdigit()}
        except Exception:
            pass
    return set()

def save_ignore(ids: Set[int]) -> None:
    try:
        with open(_ignore_path(), "w", encoding="utf-8") as f:
            json.dump(sorted(list(ids)), f, indent=2)
    except Exception:
        pass

def load_targets_from_file(path: Optional[str]) -> List[int]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("targets") if isinstance(data, dict) else data
        out: List[int] = []
        if isinstance(raw, list):
            for x in raw:
                try:
                    out.append(int(str(x)))
                except Exception:
                    pass
        return out
    except Exception:
        return []

def _write_targets_file(path: str, ids: List[int]) -> bool:
    base: Dict[str, object] = {
        "app": "Target Tracker",
        "version": "2.5.x",
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "targets": [str(i) for i in ids],
    }
    try:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cur = json.load(f)
                if isinstance(cur, dict):
                    cur["targets"] = [str(i) for i in ids]
                    cur["exportedAt"] = datetime.now(timezone.utc).isoformat()
                    base = cur
            except Exception:
                pass
        with open(path, "w", encoding="utf-8") as f:
            json.dump(base, f, indent=2)
        return True
    except Exception:
        return False

def add_targets_to_file(path: Optional[str], new_ids: List[int]) -> Optional[List[int]]:
    if not path:
        return None
    existing = load_targets_from_file(path)
    merged = list(dict.fromkeys(existing + [int(i) for i in new_ids]))  # de-dup preserve order
    ok = _write_targets_file(path, merged)
    return merged if ok else None

def remove_targets_from_file(path: Optional[str], remove_ids: List[int]) -> Optional[List[int]]:
    """Remove IDs from the targets JSON; returns updated list or None on failure."""
    if not path:
        return None
    existing = load_targets_from_file(path)
    remset = {int(i) for i in remove_ids}
    updated = [i for i in existing if i not in remset]
    ok = _write_targets_file(path, updated)
    return updated if ok else None

def load_cache() -> List[TargetInfo]:
    p = _cache_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items", []) if isinstance(data, dict) else []
        out: List[TargetInfo] = []
        for obj in items:
            if isinstance(obj, dict) and "user_id" in obj:
                out.append(TargetInfo.from_cache_dict(obj))
        return out
    except Exception:
        return []

def save_cache(items: List[TargetInfo]) -> None:
    latest: Dict[int, TargetInfo] = {}
    for it in items:
        latest[it.user_id] = it
    payload = {
        "app": APP_NAME,
        "version": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": [it.to_cache_dict() for it in latest.values()],
    }
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass
