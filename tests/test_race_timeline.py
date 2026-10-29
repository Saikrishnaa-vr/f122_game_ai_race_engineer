"""Synthetic 2022 race-control packets; the Bahrain fixture has no SC/VSC."""

from io import StringIO
import json
import struct
import zlib

import pytest

from app import replay_dashboard
from app.engineer import DiagnosticMonitor
from app.fuel_view import FuelView
from app.recordings import AutoRaceRecorder, list_recordings
from app.telemetry import capture
from app.telemetry.payloads import decode_payload
from app.telemetry.protocol import decode_packet
from app.telemetry.receiver import ReceptionStats, process_packet
from app.telemetry.state import RaceState


UID = 8822


def session_packet(factory, elapsed: float, game_time: float, safety: int) -> bytes:
    raw = bytearray(factory(1, size=632, session_uid=UID, session_time=game_time,
                            frame=int(elapsed * 100) + 1))
    raw[24 + 3] = 14
    struct.pack_into("<H", raw, 24 + 4, 5412)
    raw[24 + 6] = 10
    raw[148] = safety
    return bytes(raw)


def lap_packet(factory, elapsed: float, game_time: float, lap: int,
               pit: int = 0) -> bytes:
    raw = bytearray(factory(2, size=972, session_uid=UID, session_time=game_time,
                            frame=int(elapsed * 100) + 1))
    offset = 24 + 2 * 43
    struct.pack_into("<IIHHfff", raw, offset, 90_000, 10_000, 30_000, 30_000,
                     1100.0, (lap - 1) * 5412 + 1100.0, 0.0)
    struct.pack_into("<7B", raw, offset + 24, 5, lap, pit, int(pit > 0), 0, 0, 0)
    raw[offset + 40] = 2  # Game result status: active.
    return bytes(raw)


def event_packet(factory, elapsed: float, game_time: float, code: bytes) -> bytes:
    raw = bytearray(factory(3, size=40, session_uid=UID, session_time=game_time,
                            frame=int(elapsed * 100) + 1))
    raw[24:28] = code
    return bytes(raw)


def motion_packet(factory, elapsed: float, game_time: float, x: float) -> bytes:
    raw = bytearray(factory(0, size=1464, session_uid=UID, session_time=game_time,
                            frame=int(elapsed * 100) + 1))
    struct.pack_into("<3f", raw, 24 + 2 * 60, x, 0.0, 200.0)
    return bytes(raw)


def apply(state: RaceState, stats: ReceptionStats, elapsed: float, raw: bytes) -> None:
    assert process_packet(stats, state, raw, elapsed) is not None


def test_observed_sc_vsc_restart_pit_and_forward_jump(packet_factory) -> None:
    state, stats = RaceState(), ReceptionStats(0.0, 0.0)
    sequence = [
        (0.0, session_packet(packet_factory, 0.0, 0.0, 0)),
        (0.2, lap_packet(packet_factory, 0.2, 0.2, 1)),
        (1.0, session_packet(packet_factory, 1.0, 1.0, 1)),
        (1.2, lap_packet(packet_factory, 1.2, 1.2, 1, 1)),
        (2.0, session_packet(packet_factory, 2.0, 2.0, 2)),
        (2.2, lap_packet(packet_factory, 2.2, 2.2, 1, 0)),
        (3.0, session_packet(packet_factory, 3.0, 3.0, 0)),
        (3.2, lap_packet(packet_factory, 3.2, 33.0, 3)),
    ]
    statuses = []
    for elapsed, raw in sequence:
        apply(state, stats, elapsed, raw)
        statuses.append(state.timeline.status(elapsed))
    assert statuses == ["GREEN", "GREEN", "SC", "SC", "VSC", "VSC", "GREEN", "GREEN"]
    events = list(state.timeline.events)
    assert {event["kind"] for event in events} >= {
        "green_observed", "sc_active", "vsc_active", "restart", "pit_entry",
        "pit_exit", "unobserved_interval"}
    assert next(event for event in events if event["kind"] == "pit_entry")["race_status"] == "SC"
    assert next(event for event in events if event["kind"] == "pit_exit")["race_status"] == "VSC"
    assert next(event for event in events if event["kind"] == "unobserved_interval")["lap_jump"] == 2
    assert state.resets == 0  # Forward jump is not a flashback.
    assert state.timeline.epoch == 0

    assert state.timeline.status(7.3) == "UNKNOWN"  # Session is stale, packets may still arrive.
    assert state.timeline.status(8.3) == "STALE"
    apply(state, stats, 11.0, session_packet(packet_factory, 11.0, 45.0, 0))
    gap = [event for event in state.timeline.events if event["kind"] == "unobserved_interval"][-1]
    assert gap["arrival_gap_s"] == 7.8
    assert state.timeline.status(11.0) == "GREEN"
    assert state.resets == 0


def test_explicit_flashback_is_distinct_from_gap_and_session_end(packet_factory) -> None:
    state, stats = RaceState(), ReceptionStats(0.0, 0.0)
    for elapsed, raw in (
        (0.0, session_packet(packet_factory, 0.0, 10.0, 1)),
        (0.2, lap_packet(packet_factory, 0.2, 10.2, 2)),
        (1.0, event_packet(packet_factory, 1.0, 1.0, b"FLBK")),
        (1.2, session_packet(packet_factory, 1.2, 1.2, 0)),
        (1.4, event_packet(packet_factory, 1.4, 1.4, b"SEND")),
    ):
        apply(state, stats, elapsed, raw)
    kinds = [event["kind"] for event in state.timeline.events]
    assert "flashback" in kinds and "session_ended" in kinds
    assert state.timeline.epoch == 1 and state.resets == 1
    assert state.timeline.status(1.4) == "GREEN"


def test_short_forward_clock_jump_is_unobserved_not_flashback(packet_factory) -> None:
    state, stats = RaceState(), ReceptionStats(0.0, 0.0)
    apply(state, stats, 0.0, session_packet(packet_factory, 0.0, 0.0, 1))
    apply(state, stats, 0.5, session_packet(packet_factory, 0.5, 8.0, 1))
    assert state.resets == 0
    assert state.timeline.status(0.5) == "SC"
    assert [event["kind"] for event in state.timeline.events].count("unobserved_interval") == 1
    assert not any(event["kind"] in ("flashback", "clock_rewind")
                   for event in state.timeline.events)


def test_foreign_menu_packet_cannot_clear_live_race(packet_factory) -> None:
    state, stats = RaceState(), ReceptionStats(0.0, 0.0)
    apply(state, stats, 0.0, session_packet(packet_factory, 0.0, 0.0, 0))
    menu = packet_factory(1, size=632, session_uid=999, session_time=0.0, frame=2)
    apply(state, stats, 0.5, menu)
    assert state.session_uid == UID and state.resets == 0
    assert state.timeline.status(0.5) == "GREEN"
    apply(state, stats, 0.6, lap_packet(packet_factory, 0.6, 0.6, 1))
    assert state.timeline.last_lap == 1


def test_neutralization_discards_pre_safety_car_diagnostics(packet_factory) -> None:
    state, stats = RaceState(), ReceptionStats(0.0, 0.0)
    apply(state, stats, 0.0, session_packet(packet_factory, 0.0, 0.0, 0))
    monitor = DiagnosticMonitor()
    monitor.fuel_rate = 2.1
    monitor.wear_rate = (2.0, 2.0, 2.0, 2.0)
    apply(state, stats, 1.0, session_packet(packet_factory, 1.0, 1.0, 1))
    assert "green-flag" in monitor.evaluate(state, 1.0)[0]
    assert monitor.fuel_rate is None and monitor.wear_rate is None
    apply(state, stats, 2.0, session_packet(packet_factory, 2.0, 2.0, 0))
    assert state.timeline.pace_ready is False
    apply(state, stats, 2.1, lap_packet(packet_factory, 2.1, 2.1, 2))
    assert state.timeline.pace_ready is False
    apply(state, stats, 2.5, lap_packet(packet_factory, 2.5, 2.5, 3))
    assert state.timeline.pace_ready is True


def test_stale_race_status_withholds_lift_cue_even_if_old_session_says_green() -> None:
    facts = {"session_uid": UID, "player_index": 2, "state_resets": 0,
             "race_phase": {"status": "STALE", "generation": 2},
             "fields": {
                 "session": {"value": {"session_type": 10, "track_length_m": 5412,
                                       "safety_car": 0}},
                 "lap": {"value": {"lap_number": 3, "lap_distance_m": 1500,
                                   "pit_status": 0, "pit_stops": 0, "invalid": 0}},
                 "car_status": {"value": {"fuel_kg": 20.0, "actual_compound": 18}},
                 "driving": {"value": {"brake": 0.8, "speed_kph": 220}}},
             "estimates": {"fuel_plan": {"status": "short"}}}
    result = FuelView().update(facts)
    assert result["status"] == "hold"
    assert "target_distance_m" not in result


def test_saved_replay_shows_silence_and_observed_transitions(packet_factory,
                                                             monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(capture, "CAPTURE_DIRECTORY", tmp_path / "recordings")
    sequence = [
        (0.0, session_packet(packet_factory, 0.0, 0.0, 0)),
        (0.1, lap_packet(packet_factory, 0.1, 0.1, 1)),
        (0.6, motion_packet(packet_factory, 0.6, 0.6, 100.0)),
        (1.0, session_packet(packet_factory, 1.0, 1.0, 1)),
        (1.1, lap_packet(packet_factory, 1.1, 1.1, 1, 1)),
        (11.0, session_packet(packet_factory, 11.0, 31.0, 2)),
        (11.1, lap_packet(packet_factory, 11.1, 31.1, 2, 1)),
        (11.2, motion_packet(packet_factory, 11.2, 31.2, 300.0)),
        (12.0, session_packet(packet_factory, 12.0, 32.0, 0)),
        (12.1, lap_packet(packet_factory, 12.1, 32.1, 2, 0)),
    ]
    writer = capture.CaptureWriter("race-sc-synthetic.f1cap")
    for elapsed, raw in sequence:
        assert writer.write(elapsed, raw)
    writer.close()
    assert list(capture.read_capture("race-sc-synthetic.f1cap")) == sequence

    frames = replay_dashboard.load_frames("race-sc-synthetic.f1cap")
    snapshots = [json.loads(zlib.decompress(frame.payload)) for frame in frames]
    phases = [item["facts"]["race_phase"]["status"] for item in snapshots]
    assert "SC" in phases and "VSC" in phases and "STALE" in phases
    stale = next(item for item in snapshots if item["facts"]["race_phase"]["status"] == "STALE")
    assert stale["facts"]["fields"]["lap"] is None
    assert stale["track_map"]["markers"] == []
    resumed = next(item for item in snapshots if item["facts"]["race_phase"]["status"] == "VSC")
    assert any(event["kind"] == "unobserved_interval" for event in
               resumed["facts"]["race_phase"]["events"])
    assert any(event["kind"] == "restart" for event in snapshots[-1]["facts"]["race_phase"]["events"])

    # The same packet sequence stays in one incomplete file across silence.
    recorder = AutoRaceRecorder(StringIO())
    for elapsed, raw in sequence:
        header = decode_packet(raw)
        session = decode_payload(raw, header) if header.packet_id == 1 else None
        recorder.observe(elapsed, raw, header, session)
        recorder.tick(elapsed)
    recorder.close()
    [item] = [item for item in list_recordings() if item["name"] != "race-sc-synthetic.f1cap"]
    assert item["status"] == "incomplete" and item["packets"] == len(sequence)
    assert [packet for _, packet in capture.read_capture(item["name"])] == [packet for _, packet in sequence]
