from __future__ import annotations
import json
import os
import sys
import re
import logging
from typing import Dict, List, Set, Optional
from datetime import datetime, timezone
from models import TargetInfo
from config import CONFIG

logger = logging.getLogger("TargetTracker.Storage")

APP_NAME = CONFIG.APP_NAME
TARGET_FILE_DEFAULT = CONFIG.DEFAULT_TARGET_FILE

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
    return os.path.join(get_appdata_dir(), CONFIG.CACHE_FILE)

def _encryption_key_path() -> str:
    return os.path.join(get_appdata_dir(), CONFIG.ENCRYPTION_KEY_FILE)

# -------- Encryption for sensitive data --------
def _get_or_create_encryption_key() -> bytes:
    """
    Get or create encryption key for sensitive data.
    Uses Fernet symmetric encryption (based on AES).
    Falls back to base64 encoding if cryptography package is not available.
    """
    key_path = _encryption_key_path()

    # Try to load existing key
    if os.path.exists(key_path):
        try:
            with open(key_path, "rb") as f:
                return f.read()
        except Exception as e:
            logger.warning("Failed to load encryption key, creating new one: %s", e)

    # Create new key
    try:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
    except ImportError:
        # Fallback: use a simple base64 key (less secure but better than plaintext)
        import base64
        import secrets
        key = base64.urlsafe_b64encode(secrets.token_bytes(32))
        logger.warning("cryptography package not available, using basic encoding")

    # Save key with restricted permissions
    try:
        with open(key_path, "wb") as f:
            f.write(key)
        # Set file permissions to read/write for owner only (Unix-like systems)
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            pass  # Windows doesn't support chmod
        logger.info("Created new encryption key at %s", key_path)
    except Exception as e:
        logger.error("Failed to save encryption key: %s", e)

    return key

def encrypt_value(value: str) -> str:
    """Encrypt a string value for storage."""
    if not value:
        return ""

    try:
        try:
            from cryptography.fernet import Fernet
            key = _get_or_create_encryption_key()
            f = Fernet(key)
            encrypted = f.encrypt(value.encode('utf-8'))
            return encrypted.decode('ascii')
        except ImportError:
            # Fallback to base64 encoding (obfuscation, not true encryption)
            import base64
            return base64.b64encode(value.encode('utf-8')).decode('ascii')
    except Exception as e:
        logger.error("Encryption failed: %s", e)
        return value  # Return plaintext as fallback

def decrypt_value(encrypted: str) -> str:
    """Decrypt an encrypted string value."""
    if not encrypted:
        return ""

    try:
        try:
            from cryptography.fernet import Fernet
            key = _get_or_create_encryption_key()
            f = Fernet(key)
            decrypted = f.decrypt(encrypted.encode('ascii'))
            return decrypted.decode('utf-8')
        except ImportError:
            # Fallback to base64 decoding
            import base64
            return base64.b64decode(encrypted.encode('ascii')).decode('utf-8')
    except Exception as e:
        logger.warning("Decryption failed, returning as-is: %s", e)
        return encrypted  # Might be plaintext from old version

# -------- Settings validation --------
def validate_settings(settings: dict) -> dict:
    """Validate and sanitize settings, returning corrected values."""
    validated = settings.copy()

    # Validate concurrency
    conc = validated.get("concurrency", CONFIG.DEFAULT_CONCURRENCY)
    if not isinstance(conc, int) or conc < CONFIG.MIN_CONCURRENCY or conc > CONFIG.MAX_CONCURRENCY:
        logger.warning("Invalid concurrency %s, using default %d", conc, CONFIG.DEFAULT_CONCURRENCY)
        validated["concurrency"] = CONFIG.DEFAULT_CONCURRENCY

    # Validate auto refresh interval
    auto_refresh = validated.get("auto_refresh_sec", 0)
    if not isinstance(auto_refresh, (int, float)) or auto_refresh < CONFIG.MIN_AUTO_REFRESH_SEC:
        validated["auto_refresh_sec"] = 0
    elif auto_refresh > CONFIG.MAX_AUTO_REFRESH_SEC:
        logger.warning("Auto refresh too large (%s), capping at %d", auto_refresh, CONFIG.MAX_AUTO_REFRESH_SEC)
        validated["auto_refresh_sec"] = CONFIG.MAX_AUTO_REFRESH_SEC

    # Validate request delay
    req_delay = validated.get("req_delay_ms", CONFIG.DEFAULT_MIN_INTERVAL_MS)
    if not isinstance(req_delay, (int, float)) or req_delay < 0:
        validated["req_delay_ms"] = CONFIG.DEFAULT_MIN_INTERVAL_MS

    # Validate min interval
    min_interval = validated.get("min_interval_ms", CONFIG.DEFAULT_MIN_INTERVAL_MS)
    if not isinstance(min_interval, (int, float)) or min_interval < 0:
        validated["min_interval_ms"] = CONFIG.DEFAULT_MIN_INTERVAL_MS

    # Validate rate limit
    rate_limit = validated.get("rate_max_per_min", CONFIG.DEFAULT_MAX_CALLS_PER_MIN)
    if not isinstance(rate_limit, int) or rate_limit < 1 or rate_limit > 200:
        logger.warning("Invalid rate limit %s, using default %d", rate_limit, CONFIG.DEFAULT_MAX_CALLS_PER_MIN)
        validated["rate_max_per_min"] = CONFIG.DEFAULT_MAX_CALLS_PER_MIN

    # Validate API key format (basic check)
    api_key = validated.get("api_key", "")
    if api_key and not re.match(r'^[a-zA-Z0-9_\-+=]+$', api_key):
        logger.warning("API key contains unexpected characters")

    # Validate boolean settings
    for bool_key in ["load_cache_at_start", "start_maximized"]:
        if bool_key in validated and not isinstance(validated[bool_key], bool):
            validated[bool_key] = bool(validated[bool_key])

    return validated

def load_settings() -> dict:
    """Load and validate settings from disk."""
    p = _settings_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    logger.warning("Settings file is not a dict, using defaults")
                    data = {}

                # Decrypt API key if it's encrypted (backward compatible)
                api_key = data.get("api_key", "")
                if api_key:
                    # Try to detect if it's already encrypted (contains valid Fernet token or base64)
                    try:
                        decrypted = decrypt_value(api_key)
                        data["api_key"] = decrypted
                    except Exception:
                        # Probably plaintext from old version, keep as-is
                        pass

                return validate_settings(data)
        except json.JSONDecodeError as e:
            logger.error("Settings file is corrupted: %s", e)
        except IOError as e:
            logger.error("Failed to read settings file: %s", e)
        except Exception as e:
            logger.exception("Unexpected error loading settings: %s", e)

    # Return defaults
    defaults = {
        "api_key": "",
        "targets_file": TARGET_FILE_DEFAULT,
        "concurrency": CONFIG.DEFAULT_CONCURRENCY,
        "req_delay_ms": CONFIG.DEFAULT_MIN_INTERVAL_MS,
        "auto_refresh_sec": 0,
        "load_cache_at_start": True,
        "rate_max_per_min": CONFIG.DEFAULT_MAX_CALLS_PER_MIN,
        "min_interval_ms": CONFIG.DEFAULT_MIN_INTERVAL_MS,
    }
    return validate_settings(defaults)

def save_settings(st: dict) -> None:
    """Save settings to disk with API key encryption."""
    try:
        # Validate settings before saving
        validated = validate_settings(st)

        # Encrypt sensitive data
        save_data = validated.copy()
        api_key = save_data.get("api_key", "")
        if api_key:
            save_data["api_key"] = encrypt_value(api_key)

        with open(_settings_path(), "w", encoding="utf-8") as f:
            json.dump(save_data, f, indent=2)
        logger.debug("Settings saved successfully")
    except IOError as e:
        logger.error("Failed to save settings (I/O error): %s", e)
    except Exception as e:
        logger.exception("Unexpected error saving settings: %s", e)

def load_ignore() -> Set[int]:
    """Load ignored user IDs from disk."""
    p = _ignore_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    result = set()
                    for x in data:
                        try:
                            uid = int(x)
                            if CONFIG.MIN_USER_ID <= uid <= CONFIG.MAX_USER_ID:
                                result.add(uid)
                            else:
                                logger.warning("Ignored ID out of range: %s", uid)
                        except (ValueError, TypeError):
                            logger.warning("Invalid ignored ID skipped: %r", x)
                    return result
        except json.JSONDecodeError as e:
            logger.error("Ignore file is corrupted: %s", e)
        except IOError as e:
            logger.error("Failed to read ignore file: %s", e)
        except Exception as e:
            logger.exception("Unexpected error loading ignore list: %s", e)
    return set()

def save_ignore(ids: Set[int]) -> None:
    """Save ignored user IDs to disk."""
    try:
        # Validate IDs before saving
        valid_ids = [uid for uid in ids if CONFIG.MIN_USER_ID <= uid <= CONFIG.MAX_USER_ID]
        if len(valid_ids) != len(ids):
            logger.warning("Filtered %d invalid IDs from ignore list", len(ids) - len(valid_ids))

        with open(_ignore_path(), "w", encoding="utf-8") as f:
            json.dump(sorted(valid_ids), f, indent=2)
        logger.debug("Saved %d ignored ID(s)", len(valid_ids))
    except IOError as e:
        logger.error("Failed to save ignore list: %s", e)
    except Exception as e:
        logger.exception("Unexpected error saving ignore list: %s", e)

def load_targets_from_file(path: Optional[str]) -> List[int]:
    """Load target user IDs from JSON file with validation."""
    if not path:
        logger.debug("No targets file path provided")
        return []

    if not os.path.exists(path):
        logger.warning("Targets file not found: %s", path)
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw = data.get("targets") if isinstance(data, dict) else data
        out: List[int] = []

        if isinstance(raw, list):
            for x in raw:
                try:
                    uid = int(str(x))
                    if CONFIG.MIN_USER_ID <= uid <= CONFIG.MAX_USER_ID:
                        out.append(uid)
                    else:
                        logger.warning("Target ID out of valid range: %s", uid)
                except (ValueError, TypeError):
                    logger.warning("Invalid target ID skipped: %r", x)
        else:
            logger.warning("Targets file has unexpected format (expected list)")

        logger.info("Loaded %d valid target(s) from %s", len(out), path)
        return out

    except json.JSONDecodeError as e:
        logger.error("Targets file is corrupted: %s", e)
        return []
    except IOError as e:
        logger.error("Failed to read targets file: %s", e)
        return []
    except Exception as e:
        logger.exception("Unexpected error loading targets: %s", e)
        return []

def _write_targets_file(path: str, ids: List[int]) -> bool:
    """Write targets to JSON file with validation."""
    # Validate IDs
    valid_ids = [uid for uid in ids if CONFIG.MIN_USER_ID <= uid <= CONFIG.MAX_USER_ID]
    if len(valid_ids) != len(ids):
        logger.warning("Filtered %d invalid IDs before writing", len(ids) - len(valid_ids))

    base: Dict[str, object] = {
        "app": CONFIG.APP_NAME,
        "version": CONFIG.APP_VERSION,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "targets": [str(i) for i in valid_ids],
    }

    try:
        # Try to preserve existing metadata if file exists
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cur = json.load(f)
                if isinstance(cur, dict):
                    cur["targets"] = [str(i) for i in valid_ids]
                    cur["exportedAt"] = datetime.now(timezone.utc).isoformat()
                    cur["version"] = CONFIG.APP_VERSION
                    base = cur
            except Exception as e:
                logger.debug("Could not preserve existing metadata: %s", e)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(base, f, indent=2)
        logger.debug("Wrote %d target(s) to %s", len(valid_ids), path)
        return True

    except IOError as e:
        logger.error("Failed to write targets file: %s", e)
        return False
    except Exception as e:
        logger.exception("Unexpected error writing targets file: %s", e)
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
    """Load cached target info from disk."""
    p = _cache_path()
    if not os.path.exists(p):
        logger.debug("No cache file found at %s", p)
        return []

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        items = data.get("items", []) if isinstance(data, dict) else []
        out: List[TargetInfo] = []

        for obj in items:
            if isinstance(obj, dict) and "user_id" in obj:
                try:
                    info = TargetInfo.from_cache_dict(obj)
                    out.append(info)
                except Exception as e:
                    logger.warning("Skipped corrupted cache entry: %s", e)

        logger.info("Loaded %d cached target(s)", len(out))
        return out

    except json.JSONDecodeError as e:
        logger.error("Cache file is corrupted: %s", e)
        return []
    except IOError as e:
        logger.error("Failed to read cache file: %s", e)
        return []
    except Exception as e:
        logger.exception("Unexpected error loading cache: %s", e)
        return []

def save_cache(items: List[TargetInfo]) -> None:
    """Save target cache to disk."""
    # Deduplicate by user_id, keeping latest
    latest: Dict[int, TargetInfo] = {}
    for it in items:
        if it.user_id and CONFIG.MIN_USER_ID <= it.user_id <= CONFIG.MAX_USER_ID:
            latest[it.user_id] = it

    payload = {
        "app": CONFIG.APP_NAME,
        "version": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": [it.to_cache_dict() for it in latest.values()],
    }

    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.debug("Saved cache with %d item(s)", len(latest))
    except IOError as e:
        logger.error("Failed to save cache: %s", e)
    except Exception as e:
        logger.exception("Unexpected error saving cache: %s", e)
