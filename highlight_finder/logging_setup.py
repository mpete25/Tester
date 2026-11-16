"""Logging to ``logs/highlight_finder.log`` (rotating) and to the console."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config

LOGGER_NAME = "highlight_finder"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def log_file() -> Path:
    return config.log_dir() / "highlight_finder.log"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Idempotent – safe to call on every Streamlit rerun."""
    logger = logging.getLogger(LOGGER_NAME)
    target = str(log_file())
    for handler in logger.handlers:
        if getattr(handler, "baseFilename", None) == target:
            return logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(level)
    logger.propagate = False
    log_file().parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(target, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(file_handler)
    logger.addHandler(console)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def tail_log(lines: int = 200) -> str:
    path = log_file()
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return "".join(fh.readlines()[-lines:])
