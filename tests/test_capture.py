from collections.abc import Callable
from io import StringIO
from pathlib import Path
import struct

import pytest

from app import __main__, logging_setup
from app.telemetry import capture
from app.telemetry import receiver
from app.telemetry.receiver import replay_capture


@pytest.fixture(autouse=True)
def isolate_recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(capture, "CAPTURE_DIRECTORY", tmp_path / "recordings")


def test_capture_and_replay_decodes_without_game(packet_factory: Callable[..., bytes], tmp_path: Path) -> None:
    data = bytearray(packet_factory(6, size=1347))
    struct.pack_into("<H", data, 24 + 2 * 60, 275)
    writer = capture.CaptureWriter("synthetic.f1cap")
    try:
        assert writer.write(0.0, bytes(data))
        assert writer.write(1.0, packet_factory())
    finally:
        writer.close()
    output = StringIO()
    replay_capture(output, "synthetic.f1cap")
    assert "Replay finished: 2 datagrams" in output.getvalue()
    assert "275 km/h" in output.getvalue()
    assert "3:Event=1" in output.getvalue()
    with pytest.raises(FileExistsError):
        capture.CaptureWriter("synthetic.f1cap")


def test_cli_replay_never_binds_socket(
    packet_factory: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(logging_setup, "LOG_DIRECTORY", tmp_path / "logs")

    def unexpected_bind(_port: int = 20777) -> None:
        pytest.fail("replay attempted to bind a socket")

    monkeypatch.setattr(receiver, "open_receiver", unexpected_bind)
    writer = capture.CaptureWriter("replay.f1cap")
    try:
        assert writer.write(0.0, packet_factory())
    finally:
        writer.close()
    assert __main__.main(["--replay", "replay.f1cap"]) == 0
    assert "Replay finished: 1 datagrams" in capsys.readouterr().out


def test_replay_engineer_reports_and_suppresses_duplicate_alerts(packet_factory: Callable[..., bytes]) -> None:
    status = bytearray(packet_factory(7, size=1058))
    struct.pack_into("<3f", status, 24 + 2 * 47 + 5, 1.0, 100.0, 0.5)
    struct.pack_into("<f", status, 24 + 2 * 47 + 29, 300000.0)
    writer = capture.CaptureWriter("alerts.f1cap")
    try:
        assert writer.write(0.0, bytes(status))
        assert writer.write(5.0, bytes(status))
        assert writer.write(6.0, bytes(status))
    finally:
        writer.close()
    output = StringIO()
    replay_capture(output, "alerts.f1cap")
    text = output.getvalue()
    assert text.count("Alert [high]: Fuel") == 1
    assert "Alert [normal]: ERS" in text
    assert "No new alerts." in text


def test_capture_limit_does_not_write_partial_record(
    monkeypatch: pytest.MonkeyPatch, packet_factory: Callable[..., bytes]
) -> None:
    monkeypatch.setattr(capture, "MAX_CAPTURE_BYTES", len(capture.MAGIC) + capture.RECORD.size + 40)
    writer = capture.CaptureWriter("bounded.f1cap")
    try:
        assert writer.write(0.0, packet_factory())
        assert not writer.write(1.0, packet_factory())
    finally:
        writer.close()
    assert len(list(capture.read_capture("bounded.f1cap"))) == 1


@pytest.mark.parametrize("name", ["../game.f1cap", "C:\\game.f1cap", "no-extension", ".hidden.f1cap"])
def test_rejects_unsafe_capture_names(name: str) -> None:
    with pytest.raises(ValueError, match="simple filename"):
        capture.capture_path(name)


def test_truncated_and_invalid_capture_are_errors(tmp_path: Path) -> None:
    directory = tmp_path / "recordings"
    directory.mkdir()
    path = directory / "bad.f1cap"
    path.write_bytes(b"not-a-capture")
    with pytest.raises(ValueError, match="header"):
        list(capture.read_capture("bad.f1cap"))
    path.write_bytes(capture.MAGIC + capture.RECORD.pack(0.0, 40) + b"short")
    with pytest.raises(ValueError, match="truncated capture datagram"):
        list(capture.read_capture("bad.f1cap"))
    path.write_bytes(capture.MAGIC + capture.RECORD.pack(float("nan"), 0))
    with pytest.raises(ValueError, match="timestamps"):
        list(capture.read_capture("bad.f1cap"))


def test_replay_rejects_oversized_capture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(capture, "MAX_AUTO_CAPTURE_BYTES", 8)
    directory = tmp_path / "recordings"
    directory.mkdir()
    (directory / "large.f1cap").write_bytes(b"012345678")
    with pytest.raises(ValueError, match="exceeds"):
        list(capture.read_capture("large.f1cap"))
