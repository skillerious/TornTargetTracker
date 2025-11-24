"""
Application configuration constants for Target Tracker.

This module centralizes all magic numbers and configuration values
to make the application more maintainable and easier to configure.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration."""

    # Application Info
    APP_NAME: str = "TargetTracker"
    APP_VERSION: str = "2.6.0"

    # API Settings
    DEFAULT_MAX_CALLS_PER_MIN: int = 100
    DEFAULT_MIN_INTERVAL_MS: int = 620
    DEFAULT_CONCURRENCY: int = 4
    DEFAULT_TIMEOUT_SEC: float = 10.0
    MAX_RETRY_ATTEMPTS: int = 5
    BASE_BACKOFF_SEC: float = 0.6
    MAX_BACKOFF_SEC: float = 8.0

    # Cache Settings
    CACHE_EXPIRY_HOURS: int = 24
    CACHE_SAVE_EVERY_N: int = 50  # Save every N items during batch fetch
    CACHE_SAVE_DEBOUNCE_MS: int = 5000  # Debounce cache saves

    # Network Settings
    CONNECTIVITY_CHECK_INTERVAL_MS: int = 5000
    CONNECTIVITY_TIMEOUT_SEC: float = 3.0

    # User Agent
    @property
    def USER_AGENT(self) -> str:
        return f"{self.APP_NAME}/{self.APP_VERSION} (https://github.com/skillerious) Python-urllib"

    # File Settings
    DEFAULT_TARGET_FILE: str = "target.json"
    SETTINGS_FILE: str = "settings.json"
    IGNORE_FILE: str = "ignore.json"
    CACHE_FILE: str = "cache_targets.json"
    CRASH_LOG_FILE: str = "crash.log"
    APP_LOG_FILE: str = "target_tracker.log"
    ENCRYPTION_KEY_FILE: str = ".key"

    # Logging Settings
    LOG_MAX_BYTES: int = 5 * 1024 * 1024  # 5MB
    LOG_BACKUP_COUNT: int = 3

    # Validation Limits
    MIN_CONCURRENCY: int = 1
    MAX_CONCURRENCY: int = 20
    MIN_AUTO_REFRESH_SEC: int = 0
    MAX_AUTO_REFRESH_SEC: int = 3600
    MIN_USER_ID: int = 1
    MAX_USER_ID: int = 9999999999  # Torn's max possible ID

    # UI Settings
    DEFAULT_WINDOW_WIDTH: int = 1200
    DEFAULT_WINDOW_HEIGHT: int = 720
    MIN_WINDOW_WIDTH: int = 800
    MIN_WINDOW_HEIGHT: int = 600


# Global config instance
CONFIG = AppConfig()
