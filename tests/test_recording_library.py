"""Grand Prix recording lifecycle and the saved-race browser contract."""

from io import StringIO
import json
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


def session_packet(packet_factory, uid: int) -> bytes:
    data = bytearray(packet_factory(1, size=632, session_uid=uid))
    data[24 + 3] = 14
    struct.pack_into("<H", data, 24 + 4, 5412)
    data[24 + 6] = 10
    struct.pack_into("<b", data, 24 + 7, 3)
    return bytes(data)


def packet_event(packet_factory, uid: int, code: bytes) -> bytes:
    data = bytearray(packet_factory(3, size=40, session_uid=uid))
    data[24:28] = code
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
    assert {item["status"] for item in items} == {"completed", "stopped"}
    assert items[0]["name"] != items[1]["name"]


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
