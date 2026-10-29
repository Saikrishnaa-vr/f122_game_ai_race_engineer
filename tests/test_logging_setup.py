import logging
from pathlib import Path

import pytest

from app import logging_setup


def test_log_rotation_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logging_setup, "LOG_DIRECTORY", tmp_path)
    monkeypatch.setattr(logging_setup, "MAX_LOG_BYTES", 200)
    monkeypatch.setattr(logging_setup, "BACKUP_COUNT", 2)
    handler = logging_setup.create_log_handler()
    try:
        for _ in range(20):
            handler.handle(logging.LogRecord("test", logging.INFO, __file__, 1, "x" * 50, (), None))
    finally:
        handler.close()
    files = sorted(tmp_path.glob("race-engineer.log*"))
    assert [path.name for path in files] == [
        "race-engineer.log", "race-engineer.log.1", "race-engineer.log.2"
    ]
    assert all(path.stat().st_size <= 200 for path in files)
    assert "Z INFO " in files[0].read_text(encoding="utf-8")


def test_log_write_failure_is_visible_even_when_logging_exceptions_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(logging_setup, "LOG_DIRECTORY", tmp_path)
    monkeypatch.setattr(logging, "raiseExceptions", False)
    handler = logging_setup.create_log_handler()

    def fail(_record: logging.LogRecord) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(handler, "shouldRollover", fail)
    try:
        handler.handle(logging.LogRecord("test", logging.ERROR, __file__, 1, "message", (), None))
    finally:
        handler.close()
    error = capsys.readouterr().err
    assert "Could not write diagnostic log" in error
    assert "disk full" in error
    assert "Console diagnostics remain available" in error
