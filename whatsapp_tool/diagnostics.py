from __future__ import annotations

import logging
import platform
import shutil
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__

LOGGER_NAME = "mayfair_whatsapp"
DEFAULT_LOG_DIRECTORY = Path.home() / ".mayfair-whatsapp-web" / "logs"
DEFAULT_LOG_FILE = DEFAULT_LOG_DIRECTORY / "app.log"


def configure_logging() -> Path:
    """Configure a persistent rotating log and return its path."""

    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        handler = logger.handlers[0]
        return Path(getattr(handler, "baseFilename", DEFAULT_LOG_FILE))

    log_file = DEFAULT_LOG_FILE
    try:
        DEFAULT_LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    except OSError:
        fallback_directory = Path(tempfile.gettempdir()) / "mayfair-whatsapp-web-logs"
        fallback_directory.mkdir(parents=True, exist_ok=True)
        log_file = fallback_directory / "app.log"
    handler = RotatingFileHandler(
        log_file,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(threadName)s | %(name)s | %(message)s"
        )
    )
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False

    logger.info("Application started (version %s)", __version__)
    logger.info("Python %s at %s", platform.python_version(), sys.executable)
    logger.info("Platform: %s", platform.platform())
    logger.info(
        "Chrome executable: %s",
        shutil.which("google-chrome") or shutil.which("chrome") or "not found on PATH",
    )
    logger.info("Diagnostic log: %s", log_file)
    return log_file


def get_logger(component: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{component}")
