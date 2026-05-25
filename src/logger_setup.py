"""
Structured logging setup with console and rotating file handlers.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from src.config import LoggingConfig

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def setup_logging(cfg: LoggingConfig) -> logging.Logger:
    """Configure the root logger and return the application root logger.

    Both a console handler and a size-rotating file handler are installed.
    Calling this more than once is safe – existing handlers are cleared first.
    """
    log_dir = Path(cfg.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, cfg.level.upper(), logging.INFO)
    fmt = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    root = logging.getLogger()
    # Remove any handlers added by a previous call or by basicConfig.
    root.handlers.clear()
    root.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # Rotating file handler
    log_file = log_dir / "backup_agent.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=cfg.max_bytes,
        backupCount=cfg.backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Silence noisy Azure SDK internals unless we are at DEBUG level.
    if level > logging.DEBUG:
        for noisy in ("azure.core", "azure.identity", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger("backup_agent")
