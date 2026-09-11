"""Centralized logging configuration with rotating file handler, Qt interceptor, and exception hooks."""

import logging
import os
from pathlib import Path
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional
from PyQt6.QtCore import qInstallMessageHandler, QtMsgType

from meshcore_tray.config import get_app_dir

logger = logging.getLogger("meshcore_tray.logger")
_INITIALIZED = False


def get_log_file_path() -> Path:
    """Returns the standardized log file location in the user's config directory."""
    return get_app_dir() / "meshcore_navigator.log"


def setup_app_logging(debug: bool = False) -> Path:
    """Initializes rotating file and console logging, plus Qt/sys.excepthook interceptors."""
    global _INITIALIZED
    log_file = get_log_file_path()
    log_dir = log_file.parent
    log_dir.mkdir(parents=True, exist_ok=True)

    if _INITIALIZED:
        return log_file

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console Handler (sys.stderr)
    has_stream = any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root_logger.handlers)
    if not has_stream:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        console_handler.setFormatter(fmt)
        root_logger.addHandler(console_handler)

    # Rotating File Handler (10MB per file, up to 5 backups)
    has_file = any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers)
    if not has_file:
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        root_logger.addHandler(file_handler)

    # Intercept Qt C++ runtime warnings/errors
    def qt_message_handler(msg_type, context, message):
        qt_logger = logging.getLogger("qt")
        if msg_type == QtMsgType.QtDebugMsg:
            qt_logger.debug(message)
        elif msg_type == QtMsgType.QtInfoMsg:
            qt_logger.info(message)
        elif msg_type == QtMsgType.QtWarningMsg:
            qt_logger.warning(message)
        elif msg_type == QtMsgType.QtCriticalMsg:
            qt_logger.error(message)
        elif msg_type == QtMsgType.QtFatalMsg:
            qt_logger.critical(message)

    try:
        qInstallMessageHandler(qt_message_handler)
    except Exception as e:
        logger.warning(f"Could not install Qt message handler: {e}")

    # Intercept unhandled Python exceptions
    def excepthook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger("crash").critical(
            "Unhandled Python exception:",
            exc_info=(exc_type, exc_value, exc_traceback)
        )

    sys.excepthook = excepthook
    _INITIALIZED = True

    root_logger.info(f"=== MESHCORE NAVIGATOR Logging Initialized (PID {os.getpid()}) -> {log_file} ===")
    return log_file
