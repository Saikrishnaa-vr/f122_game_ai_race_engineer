"""Grand Prix recording lifecycle and the saved-race browser contract."""

from io import StringIO
import json
import logging
from pathlib import Path
import struct
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zlib

import pytest

from app import replay_dashboard
from app.recordings import AutoRaceRecorder, list_recordings, recording_info
from app.telemetry import capture
from app.telemetry.payloads import Session, decode_payload
from app.telemetry.protocol import decode_packet
from app.telemetry.protocol import PACKET_TYPES


@pytest.fixture(autouse=True)
def isolate_recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(capture, "CAPTURE_DIRECTORY", tmp_path / "recordings")


def session_packet(packet_factory, uid: int, session_type: int = 10) -> bytes:
    data = bytearray(packet_factory(1, size=632, session_uid=uid))
    data[24 + 3] = 14
    struct.pack_into("<H", data, 24 + 4, 5412)
    data[24 + 6] = session_type
    struct.pack_into("<b", data, 24 + 7, 3)
    return bytes(data)


def packet_event(packet_factory, uid: int, code: bytes) -> bytes:
    data = bytearray(packet_factory(3, size=40, session_uid=uid))
    data[24:28] = code
    return bytes(data)


def lap_packet(packet_factory, uid: int, lap: int) -> bytes:
    data = bytearray(packet_factory(2, size=972, session_uid=uid, session_time=lap * 90.0,
                                    frame=lap * 100))
    struct.pack_into("<7B", data, 24 + 2 * 43 + 24, 5, lap, 0, 0, 0, 0, 0)
    return bytes(data)


def test_auto_recording_waits_for_race_and_saves_after_classification(packet_factory) -> None:
    output = StringIO()
    recorder = AutoRaceRecorder(output)
    menu = packet_factory(3, size=40, session_uid=1)
    recorder.observe(1.0, menu, decode_packet(menu), None)
    assert not list_recordings()

    race = session_packet(packet_factory, 101)
    decoded = decode_payload(race, decode_packet(race))
    assert isinstance(decoded, Session)
    recorder.observe(2.0, race, decode_packet(race), decoded)
    finish = packet_event(packet_factory, 101, b"SEND")
    recorder.observe(10.0, finish, decode_packet(finish), decoded)
    recorder.tick(20.0)
    assert recorder.writer is not None  # Classification can arrive well after SEND.
    result = packet_factory(8, size=1015, session_uid=101)
    recorder.observe(39.0, result, decode_packet(result), decoded)
    recorder.tick(45.0)
    assert recorder.writer is None
    first = list_recordings()
    assert len(first) == 1
    assert first[0]["status"] == "completed"
    assert first[0]["track"] == "Bahrain International Circuit"
    assert first[0]["total_laps"] == 14
    assert len(list(capture.read_capture(first[0]["name"]))) == 3

    recorder.observe(46.0, race, decode_packet(race), decoded)
    assert len(list_recordings()) == 1  # Old session cannot reopen.
    next_race = session_packet(packet_factory, 202)
    recorder.observe(50.0, next_race, decode_packet(next_race), decoded)
    recorder.close()
    items = list_recordings()
    assert len(items) == 2
    assert {item["status"] for item in items} == {"completed", "incomplete"}
    assert items[0]["name"] != items[1]["name"]


@pytest.mark.parametrize("session_type", [10, 11, 12])
def test_race_continues_past_lap_two_through_pause_and_lobby_packets(packet_factory,
                                                                       session_type: int) -> None:
    recorder = AutoRaceRecorder(StringIO())
    race = session_packet(packet_factory, 501, session_type)
    session = decode_payload(race, decode_packet(race))
    packets = [race]
    recorder.observe(0.0, race, decode_packet(race), session)
    for lap in (1, 2):
        packet = lap_packet(packet_factory, 501, lap)
        packets.append(packet)
        recorder.observe(float(lap), packet, decode_packet(packet), None)
    # Connection loss and unrelated lobby traffic cannot terminate this race.
    recorder.tick(30.0)
    lobby = packet_event(packet_factory, 0, b"BUTN")
    recorder.observe(31.0, lobby, decode_packet(lobby), None)
    assert recorder.writer is not None
    for lap in (3, 4, 5):
        packet = lap_packet(packet_factory, 501, lap)
        packets.append(packet)
        recorder.observe(31.0 + lap, packet, decode_packet(packet), None)
    recorder.close()
    [item] = list_recordings()
    assert item["last_lap"] == 5
    assert item["session_type"] == session_type
    assert item["status"] == "incomplete"
    assert item["stop_reason"] == "stopped"
    assert [data for _elapsed, data in capture.read_capture(item["name"])] == packets


def test_genuine_new_race_is_separate_and_ended_session_is_identified(packet_factory) -> None:
    recorder = AutoRaceRecorder(StringIO())
    first = session_packet(packet_factory, 601)
    recorder.observe(0.0, first, decode_packet(first), decode_payload(first, decode_packet(first)))
    ended = packet_event(packet_factory, 601, b"SEND")
    recorder.observe(2.0, ended, decode_packet(ended), None)
    recorder.tick(10.0)
    assert recorder.writer is not None
    second = session_packet(packet_factory, 602, 11)
    recorder.observe(11.0, second, decode_packet(second), decode_payload(second, decode_packet(second)))
    recorder.close()
    items = list_recordings()
    assert len(items) == 2
    by_uid = {item["session_uid"]: item for item in items}
    assert by_uid[601]["status"] == "ended"
    assert by_uid[601]["stop_reason"] == "session_changed"
    assert by_uid[602]["status"] == "incomplete"
    assert [data for _time, data in capture.read_capture(by_uid[602]["name"])] == [second]


def test_auto_capture_rolls_over_without_losing_boundary_packet(packet_factory,
                                                                  monkeypatch) -> None:
    monkeypatch.setattr(capture, "MAX_AUTO_CAPTURE_BYTES", 1700)
    recorder = AutoRaceRecorder(StringIO())
    race = session_packet(packet_factory, 701)
    first_lap = lap_packet(packet_factory, 701, 1)
    second_lap = lap_packet(packet_factory, 701, 2)
    recorder.observe(0.0, race, decode_packet(race), decode_payload(race, decode_packet(race)))
    recorder.observe(1.0, first_lap, decode_packet(first_lap), None)
    recorder.observe(2.0, second_lap, decode_packet(second_lap), None)
    recorder.close()
    items = list_recordings()
    assert len(items) == 2
    first = next(item for item in items if item["status"] == "continued")
    second = next(item for item in items if item["continued_from"] == first["name"])
    assert first["stop_reason"] == "size_limit"
    assert second["status"] == "incomplete"
    assert [data for _time, data in capture.read_capture(first["name"])] == [race, first_lap]
    assert [data for _time, data in capture.read_capture(second["name"])] == [race, second_lap]


def test_same_uid_race_restart_after_send_gets_its_own_capture(packet_factory) -> None:
    recorder = AutoRaceRecorder(StringIO())
    first = session_packet(packet_factory, 801)
    recorder.observe(0.0, first, decode_packet(first), decode_payload(first, decode_packet(first)))
    lap = lap_packet(packet_factory, 801, 2)
    recorder.observe(1.0, lap, decode_packet(lap), None)
    ended = packet_event(packet_factory, 801, b"SEND")
    recorder.observe(2.0, ended, decode_packet(ended), None)
    restarted = bytearray(session_packet(packet_factory, 801))
    struct.pack_into("<f", restarted, 14, 0.0)
    restarted = bytes(restarted)
    recorder.observe(5.0, restarted, decode_packet(restarted),
                     decode_payload(restarted, decode_packet(restarted)))
    lap_three = lap_packet(packet_factory, 801, 3)
    recorder.observe(6.0, lap_three, decode_packet(lap_three), None)
    recorder.close()
    items = list_recordings()
    assert len(items) == 2
    first_item = next(item for item in items if item["status"] == "ended")
    second_item = next(item for item in items if item["status"] == "incomplete")
    assert first_item["stop_reason"] == "session_restarted"
    assert [data for _time, data in capture.read_capture(first_item["name"])] == [first, lap, ended]
    assert [data for _time, data in capture.read_capture(second_item["name"])] == [restarted, lap_three]


def test_recorder_requires_current_session_packet_and_reports_health(packet_factory,
                                                                      caplog) -> None:
    caplog.set_level(logging.INFO, logger="f1_race_engineer")
    recorder = AutoRaceRecorder(StringIO())
    race = session_packet(packet_factory, 901)
    session = decode_payload(race, decode_packet(race))
    unrelated = packet_event(packet_factory, 901, b"BUTN")
    recorder.observe(0.0, unrelated, decode_packet(unrelated), session)
    assert recorder.writer is None  # A stale Session observation cannot start a file.
    recorder.reject("header needs 24 bytes")
    recorder.observe(1.0, race, decode_packet(race), session)
    recorder.diagnostics(2.0)
    recorder.close()
    messages = [record.message for record in caplog.records]
    assert any("event=packet_rejected" in message and "rejected=1" in message
               for message in messages)
    assert any("event=health" in message and "accepted=1" in message
               and "rejected=1" in message and "bytes=650" in message
               for message in messages)
    assert any("event=stop" in message and "status=incomplete" in message
               for message in messages)


def test_same_uid_race_type_change_is_a_new_recording(packet_factory) -> None:
    recorder = AutoRaceRecorder(StringIO())
    first = session_packet(packet_factory, 902, 10)
    next_race = session_packet(packet_factory, 902, 11)
    for now, packet in ((0.0, first), (2.0, next_race)):
        recorder.observe(now, packet, decode_packet(packet),
                         decode_payload(packet, decode_packet(packet)))
    recorder.close()
    items = list_recordings()
    assert len(items) == 2
    assert {item["session_type"] for item in items} == {10, 11}
    assert {item["stop_reason"] for item in items} == {"session_changed", "stopped"}


def test_same_uid_restart_after_ended_file_was_closed(packet_factory) -> None:
    recorder = AutoRaceRecorder(StringIO())
    first = session_packet(packet_factory, 903)
    recorder.observe(0.0, first, decode_packet(first), decode_payload(first, decode_packet(first)))
    lap = lap_packet(packet_factory, 903, 2)
    recorder.observe(1.0, lap, decode_packet(lap), None)
    ended = packet_event(packet_factory, 903, b"SEND")
    recorder.observe(2.0, ended, decode_packet(ended), None)
    recorder.tick(63.0)
    assert recorder.writer is None
    restarted = bytearray(session_packet(packet_factory, 903))
    struct.pack_into("<f", restarted, 14, 0.0)
    restarted = bytes(restarted)
    recorder.observe(64.0, restarted, decode_packet(restarted),
                     decode_payload(restarted, decode_packet(restarted)))
    recorder.close()
    items = list_recordings()
    assert len(items) == 2
    assert {item["status"] for item in items} == {"ended", "incomplete"}


def test_legacy_recording_is_indexed_without_modifying_it(packet_factory) -> None:
    writer = capture.CaptureWriter("grand-prix-20261028-212717.f1cap")
    race = session_packet(packet_factory, 45)
    writer.write(0.0, race)
    writer.write(4.0, packet_factory(8, size=1015, session_uid=45))
    writer.close()
    path = capture.capture_path("grand-prix-20261028-212717.f1cap")
    before = path.read_bytes()
    info = recording_info(path.name)
    assert info["track"] == "Bahrain International Circuit"
    assert info["status"] == "completed"
    assert info["started_at"].startswith("2026-10-28")
    assert path.read_bytes() == before
    assert not path.with_suffix(".json").exists()


def test_library_lists_and_selects_multiple_recordings(packet_factory, monkeypatch) -> None:
    for name, uid in (("race-20261028-100000-000001.f1cap", 1),
                      ("race-20261029-100000-000001.f1cap", 2)):
        writer = capture.CaptureWriter(name)
        writer.write(0.0, session_packet(packet_factory, uid))
        writer.close()

    def fake_frames(name: str, _pit_loss: float | None = None):
        payload = json.dumps({"facts": {"fields": {}, "recording": name}, "connection": "receiving"}).encode()
        return [replay_dashboard.ReplayFrame(0, 1, zlib.compress(payload))]

    monkeypatch.setattr(replay_dashboard, "load_frames", fake_frames)
    library = replay_dashboard.ReplayLibrary()
    server = replay_dashboard.create_replay_server(library, 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + "/", timeout=2) as response:
            assert b"Saved races" in response.read()
        with urlopen(base + "/api/replays", timeout=2) as response:
            items = json.load(response)
        assert len(items) == 2
        with pytest.raises(HTTPError) as missing:
            urlopen(base + "/api/state", timeout=2)
        assert missing.value.code == 409
        for item in items:
            body = json.dumps({"name": item["name"]}).encode()
            request = Request(base + "/api/select", data=body,
                              headers={"Content-Type": "application/json", "Origin": base}, method="POST")
            with urlopen(request, timeout=2) as response:
                assert json.load(response)["name"] == item["name"]
            with urlopen(base + "/api/state", timeout=2) as response:
                state = json.load(response)
            assert state["facts"]["recording"] == item["name"]
            assert state["replay"]["library_mode"] is True
            with urlopen(base + "/race", timeout=2) as response:
                assert response.read() == replay_dashboard.HTML.encode("utf-8")
        bad = Request(base + "/api/select", data=b'{"name":"../missing.f1cap"}',
                      headers={"Content-Type": "application/json", "Origin": base}, method="POST")
        with pytest.raises(HTTPError) as rejected:
            urlopen(bad, timeout=2)
        assert rejected.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_saved_race_rebuilds_current_dashboard_identity_from_raw_packets(packet_factory) -> None:
    """A real replay must carry the same roster and map identities as live facts."""
    writer = capture.CaptureWriter("race-20261029-100000-000001.f1cap")
    try:
        writer.write(0.0, session_packet(packet_factory, 301))

        participants = bytearray(packet_factory(4, size=PACKET_TYPES[4][1], session_uid=301))
        participants[24] = 3
        struct.pack_into("<7B48sB", participants, 25 + 1 * 56,
                         0, 1, 0, 2, 0, 33, 0, b"Max Verstappen", 0)
        struct.pack_into("<7B48sB", participants, 25 + 2 * 56,
                         0, 2, 0, 1, 0, 16, 0, b"Charles Leclerc", 0)
        writer.write(0.1, bytes(participants))

        lap = bytearray(packet_factory(2, size=PACKET_TYPES[2][1], session_uid=301))
        for car, position, distance in ((1, 1, 1100.0), (2, 2, 1050.0)):
            base = 24 + car * 43
            struct.pack_into("<IIHHfff", lap, base,
                             90_000, 15_000, 30_000, 31_000, distance, distance, 0.0)
            struct.pack_into("<7B", lap, base + 24, position, 2, 0, 0, 0, 0, 0)
        writer.write(0.2, bytes(lap))

        motion = bytearray(packet_factory(0, size=PACKET_TYPES[0][1], session_uid=301))
        struct.pack_into("<3f", motion, 24 + 1 * 60, 100.0, 0.0, 200.0)
        struct.pack_into("<3f", motion, 24 + 2 * 60, 110.0, 0.0, 210.0)
        writer.write(0.6, bytes(motion))
    finally:
        writer.close()

    library = replay_dashboard.ReplayLibrary()
    library.select("race-20261029-100000-000001.f1cap")
    snapshot = library.controller().snapshot()
    rows = {row["race_number"]: row for row in snapshot["facts"]["grid"]}
    markers = {marker["race_number"]: marker for marker in snapshot["track_map"]["markers"]}
    assert snapshot["replay"]["library_mode"] is True
    assert snapshot["replay"]["details"]["track"] == "Bahrain International Circuit"
    assert rows[16]["driver_code"] == markers[16]["driver_code"] == "LEC"
    assert rows[16]["team_color"] == markers[16]["team_color"] == "#e32731"
    assert rows[33]["driver_code"] == markers[33]["driver_code"] == "VER"
    assert rows[33]["team_color"] == markers[33]["team_color"] == "#3357d7"
