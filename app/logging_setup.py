"""Low-volume local diagnostics for the companion process."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import time

from .paths import data_root


LOGGER = logging.getLogger("f1_race_engineer")
LOG_DIRECTORY = data_root() / "logs"
LOG_FILENAME = "race-engineer.log"
MAX_LOG_BYTES = 1024 * 1024
BACKUP_COUNT = 3


class DiagnosticFileHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        error = sys.exc_info()[1]
        print(
            f"WARNING: Could not write diagnostic log {self.baseFilename}: {error}. "
            "Console diagnostics remain available.",
            file=sys.stderr,
            flush=True,
        )


def create_log_handler() -> DiagnosticFileHandler:
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    handler = DiagnosticFileHandler(
        LOG_DIRECTORY / LOG_FILENAME,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    return handler
