"""
Centralized logging configuration for Target Tracker.

Provides rotating file handlers with proper formatting and
multiple log levels for different components.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from typing import Optional

from config import CONFIG
from storage import get_appdata_dir


def setup_logging(log_level: str = "INFO", console: bool = True) -> None:
    """
    Configure application-wide logging with rotating file handler.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console: Whether to also log to console
    """
    # Get log directory
    log_dir = get_appdata_dir()
    log_file = os.path.join(log_dir, CONFIG.APP_LOG_FILE)

    # Create formatter
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s [%(name)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Root logger for TargetTracker
    root_logger = logging.getLogger("TargetTracker")
    root_logger.setLevel(logging.DEBUG)  # Capture everything, handlers filter
    root_logger.handlers.clear()  # Remove any existing handlers

    # Rotating file handler (keeps 3 backups of 5MB each = 20MB total)
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=CONFIG.LOG_MAX_BYTES,
            backupCount=CONFIG.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)  # File gets all debug info
        root_logger.addHandler(file_handler)
    except Exception as e:
        print(f"Warning: Failed to create rotating file handler: {e}", file=sys.stderr)

    # Console handler (optional, less verbose)
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)

        # Parse log level
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        console_level = level_map.get(log_level.upper(), logging.INFO)
        console_handler.setLevel(console_level)
        root_logger.addHandler(console_handler)

    # Log startup message
    root_logger.info(
        "Logging initialized: file=%s (DEBUG), console=%s (%s)",
        log_file,
        "enabled" if console else "disabled",
        log_level.upper() if console else "N/A",
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific module.

    Args:
        name: Logger name (typically __name__ of the module)

    Returns:
        Configured logger instance
    """
    # Ensure name is under TargetTracker namespace
    if not name.startswith("TargetTracker"):
        name = f"TargetTracker.{name}"

    return logging.getLogger(name)


def set_log_level(level: str, handler_type: Optional[str] = None) -> None:
    """
    Change log level dynamically at runtime.

    Args:
        level: New log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        handler_type: Specific handler to update ("file", "console", or None for all)
    """
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    new_level = level_map.get(level.upper(), logging.INFO)
    root_logger = logging.getLogger("TargetTracker")

    if handler_type is None:
        # Update all handlers
        for handler in root_logger.handlers:
            handler.setLevel(new_level)
        root_logger.info("Log level changed to %s for all handlers", level.upper())
    else:
        # Update specific handler type
        for handler in root_logger.handlers:
            if handler_type.lower() == "file" and isinstance(
                handler, logging.handlers.RotatingFileHandler
            ):
                handler.setLevel(new_level)
                root_logger.info("File handler log level changed to %s", level.upper())
            elif handler_type.lower() == "console" and isinstance(
                handler, logging.StreamHandler
            ):
                handler.setLevel(new_level)
                root_logger.info("Console handler log level changed to %s", level.upper())


def get_log_file_path() -> str:
    """Get the path to the current log file."""
    return os.path.join(get_appdata_dir(), CONFIG.APP_LOG_FILE)


def clear_old_logs(keep_days: int = 7) -> int:
    """
    Remove log files older than specified days.

    Args:
        keep_days: Number of days to keep logs

    Returns:
        Number of files deleted
    """
    import glob
    import time

    log_dir = get_appdata_dir()
    pattern = os.path.join(log_dir, "*.log*")

    deleted = 0
    cutoff_time = time.time() - (keep_days * 86400)

    for log_file in glob.glob(pattern):
        try:
            if os.path.getmtime(log_file) < cutoff_time:
                os.remove(log_file)
                deleted += 1
        except Exception as e:
            logging.getLogger("TargetTracker.Logging").warning(
                "Failed to delete old log %s: %s", log_file, e
            )

    return deleted
