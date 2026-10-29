from collections.abc import Callable
from io import StringIO
import logging
from queue import Queue
import socket
import struct
from threading import Event, Thread

import pytest

from app.telemetry import receiver
from app.telemetry.receiver import HOST, ReceptionStats, log_report, open_receiver, process_packet, run_receiver
from app.telemetry.state import RaceState


def test_status_counts_and_rate_use_valid_packets_only(packet_factory: Callable[..., bytes]) -> None:
    stats = ReceptionStats(started_at=0.0, last_report_at=0.0)
    assert stats.status(0.0) == "waiting"
    assert stats.record(b"bad", 1.0) is None
    assert stats.status(1.0) == "waiting"
    assert stats.record(packet_factory(), 2.0) is not None
    assert stats.record(packet_factory(), 3.0) is not None
    report = stats.report(5.0)
    assert "Status: receiving" in report
    assert "datagrams=3, valid=2, invalid=1" in report
    assert "valid rate=0.4 packets/s" in report
    assert "3:Event=2" in report
    assert "short_header=1" in report
    assert stats.status(8.0) == "stale"
    assert "Valid telemetry stopped" in stats.report(8.0)
    stats.record(packet_factory(), 9.0)
    assert stats.status(9.0) == "receiving"


def test_waiting_message_includes_actionable_diagnostics() -> None:
    stats = ReceptionStats(started_at=0.0, last_report_at=0.0)
    report = stats.report(5.0)
    assert "Status: waiting" in report
    assert "Binding alone" not in report
    assert "start a driving session" in report
    assert "format 2022" in report


def test_log_levels_and_aggregated_invalid_details(
    packet_factory: Callable[..., bytes], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="f1_race_engineer")
    stats = ReceptionStats(started_at=0.0, last_report_at=0.0)
    log_report(stats, 5.0)
    assert caplog.records[-1].levelno == logging.WARNING
    assert "Status: waiting" in caplog.records[-1].message
    stats.record(b"bad", 6.0)
    stats.record(packet_factory(), 7.0)
    log_report(stats, 8.0)
    assert caplog.records[-1].levelno == logging.WARNING
    assert "short_header=1" in caplog.records[-1].message
    assert "header needs 24 bytes; got 3" in caplog.records[-1].message
    stats.record(packet_factory(), 9.0)
    log_report(stats, 10.0)
    assert caplog.records[-1].levelno == logging.INFO
    log_report(stats, 15.0, final=True)
    assert caplog.records[-1].levelno == logging.WARNING
    assert "Final Status: stale" in caplog.records[-1].message


def test_real_local_udp_receive_and_release(packet_factory: Callable[..., bytes]) -> None:
    with open_receiver(0) as listener:
        port = listener.getsockname()[1]
        assert listener.getsockname()[0] == HOST
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(packet_factory(), (HOST, port))
        data, _ = listener.recvfrom(65535)
        stats = ReceptionStats(started_at=0.0, last_report_at=0.0)
        assert stats.record(data, 1.0) is not None
        assert stats.status(1.0) == "receiving"
        with pytest.raises(OSError):
            open_receiver(port)
    with open_receiver(port) as rebound:
        assert rebound.getsockname()[1] == port


def test_idle_socket_times_out() -> None:
    with open_receiver(0) as listener:
        listener.settimeout(0.01)
        with pytest.raises(socket.timeout):
            listener.recvfrom(65535)


def test_invalid_payload_is_reported_without_refreshing_state(packet_factory: Callable[..., bytes]) -> None:
    stats = ReceptionStats(started_at=0.0, last_report_at=0.0)
    state = RaceState()
    data = bytearray(packet_factory(6, size=1347))
    struct.pack_into("<H", data, 24 + 2 * 60, 200)
    process_packet(stats, state, bytes(data), 1.0)
    struct.pack_into("<f", data, 24 + 2 * 60 + 2, float("nan"))
    process_packet(stats, state, bytes(data), 2.0)
    assert stats.valid == 2
    assert stats.payload_invalid == 1
    assert state.get_fresh(6, 2.0).observed_at == 1.0
    assert "payload_payload_value=1" in stats.report(2.0)
    assert "non-finite" in stats.last_error


def test_live_loop_displays_decoded_player_data(
    packet_factory: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(receiver, "REPORT_INTERVAL_SECONDS", 0.01)
    caplog.set_level(logging.INFO, logger="f1_race_engineer")
    messages: Queue[str] = Queue()

    class Output(StringIO):
        def flush(self) -> None:
            messages.put(self.getvalue())

    output = Output()
    stop = Event()
    thread = Thread(target=run_receiver, args=(output, 0, stop))
    thread.start()
    try:
        bound = messages.get(timeout=5.0)
        port = int(bound.split("Bound UDP 127.0.0.1:")[1].split(".")[0])
        data = bytearray(packet_factory(6, size=1347))
        struct.pack_into("<H", data, 24 + 2 * 60, 275)
        lap = bytearray(packet_factory(2, size=972))
        struct.pack_into("<I", lap, 24 + 2 * 43, 89319)
        struct.pack_into("<2B", lap, 24 + 2 * 43 + 24, 4, 8)
        status = bytearray(packet_factory(7, size=1058))
        struct.pack_into("<3f", status, 24 + 2 * 47 + 5, 1.0, 100.0, 0.5)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(lap, (HOST, port))
            sender.sendto(data, (HOST, port))
            sender.sendto(status, (HOST, port))
        for _ in range(20):
            text = messages.get(timeout=5.0)
            if "275 km/h" in text and "Lap: 8, position 4" in text and "Alert [high]: Fuel low" in text:
                break
        else:
            pytest.fail("live receiver did not print the decoded speed")
    finally:
        stop.set()
        thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert any("Lap: 8, position 4" in record.message and "last 89319 ms" in record.message
               for record in caplog.records)
    assert any("Alert [high]: Fuel low" in record.message for record in caplog.records)


def test_receiver_loop_reports_synthetic_packet_and_stops(
    packet_factory: Callable[..., bytes], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="f1_race_engineer")
    messages: Queue[str] = Queue()

    class Output(StringIO):
        def flush(self) -> None:
            messages.put(self.getvalue())

    output = Output()
    stop = Event()
    thread = Thread(target=run_receiver, args=(output, 0, stop))
    thread.start()
    try:
        bound = messages.get(timeout=5.0)
        assert "Binding alone does not confirm telemetry" in bound
        port = int(bound.split("Bound UDP 127.0.0.1:")[1].split(".")[0])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"bad", (HOST, port))
            sender.sendto(packet_factory(), (HOST, port))
        received = messages.get(timeout=5.0)
        assert "Valid F1 22 packet received: ID 3 (Event)" in received
        assert "actual gameplay are not verified" in received
    finally:
        stop.set()
        thread.join(timeout=5.0)
    assert not thread.is_alive()
    final_report = next(record for record in caplog.records if record.message.startswith("Final Status:"))
    assert final_report.levelno == logging.WARNING
    assert "datagrams=2, valid=1, invalid=1" in final_report.message
    assert "short_header=1" in final_report.message
    with open_receiver(port):
        pass
