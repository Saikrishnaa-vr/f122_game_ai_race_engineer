from pathlib import Path
import re

import pytest

from app import __main__, logging_setup


@pytest.fixture(autouse=True)
def isolate_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logging_setup, "LOG_DIRECTORY", tmp_path / "logs")


def test_interrupt_reports_clean_shutdown(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def interrupt(_output: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(__main__, "run_receiver", interrupt)
    assert __main__.main([]) == 0
    assert "socket closed" in capsys.readouterr().out
    log = (tmp_path / "logs" / "race-engineer.log").read_text(encoding="utf-8")
    assert "Companion starting" in log
    assert "Receiver stopped by user; socket closed." in log


def test_bind_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def fail(_output: object, **_kwargs: object) -> None:
        raise OSError("address in use")

    monkeypatch.setattr(__main__, "run_receiver", fail)
    assert __main__.main([]) == 1
    error = capsys.readouterr().err
    assert "127.0.0.1:20777" in error
    assert "address in use" in error
    log = (tmp_path / "logs" / "race-engineer.log").read_text(encoding="utf-8")
    assert "ERROR Receiver or capture failed." in log
    assert "OSError: address in use" in log


def test_log_setup_failure_stops_before_socket_binding(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail() -> logging_setup.DiagnosticFileHandler:
        raise OSError("permission denied")

    def unexpected_start(_output: object) -> None:
        pytest.fail("receiver started without a log")

    monkeypatch.setattr(__main__, "create_log_handler", fail)
    monkeypatch.setattr(__main__, "run_receiver", unexpected_start)
    assert __main__.main([]) == 1
    assert "Cannot create diagnostic log: permission denied. Receiver not started." in capsys.readouterr().err


def test_unexpected_error_is_logged_and_propagated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail(_output: object, **_kwargs: object) -> None:
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(__main__, "run_receiver", fail)
    with pytest.raises(RuntimeError, match="unexpected failure"):
        __main__.main([])
    log = (tmp_path / "logs" / "race-engineer.log").read_text(encoding="utf-8")
    assert "ERROR Unexpected companion failure." in log
    assert "RuntimeError: unexpected failure" in log


def test_alert_cooldown_cli_is_validated_and_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    configs = []

    def receive(_output: object, **kwargs: object) -> None:
        configs.append(kwargs["alert_config"])

    monkeypatch.setattr(__main__, "run_receiver", receive)
    assert __main__.main(["--alert-cooldown", "15"]) == 0
    assert configs[0].cooldown_seconds == 15
    with pytest.raises(SystemExit) as exc:
        __main__.main(["--alert-cooldown", "nan"])
    assert exc.value.code == 2


def test_drive_mode_starts_logging_capture_and_visible_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def receive(_output: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(__main__, "run_receiver", receive)
    assert __main__.main(["--drive"]) == 0
    launched = calls[0]
    assert launched["dashboard"] is True
    assert launched["open_dashboard"] is True
    assert launched["history_path"] is not None
    assert launched["record_name"] is None
    assert launched["auto_record"] is True

    with pytest.raises(SystemExit) as exc:
        __main__.main(["--drive", "--performance-mode"])
    assert exc.value.code == 2


def test_open_dashboard_requires_live_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(__main__, "run_receiver", lambda _output, **_kwargs: None)
    with pytest.raises(SystemExit) as exc:
        __main__.main(["--open-dashboard"])
    assert exc.value.code == 2
