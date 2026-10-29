from collections.abc import Callable
from io import StringIO
from pathlib import Path
import struct
from threading import Event as ThreadEvent
from threading import Thread

import pytest

from app.conversation import Conversation, IntentModel
from app.dashboard import DashboardState, start_polling_dashboard
from app import dashboard as dashboard_module
from app.engineer import Engineer
from app.engineer import AlertMonitor
from app.history import HistoryWorker, RaceHistory
from app.intelligence import answer_question, pit_window_alert, race_facts, safety_car_alert
from app.rivals import adjacent_rivals, full_grid
from app.paths import data_root
from app.strategy import StrategyAssumptions, compare_pit_scenarios
from app.telemetry.payloads import (CarDamage, CarSetup, CarStatus, Event, Lap, LapHistory, Motion,
                                    Participant, RivalLap, Session, SessionHistory, decode_payload)
from app.telemetry.protocol import decode_packet
from app.telemetry.state import Observation, RaceState
from app.telemetry import receiver
from app.telemetry.receiver import run_receiver
from app.track_map import ObservedTrackMap
from app.tactics import project_rejoin
from app.gaps import CheckpointGaps


def state_at(packet_factory: Callable[..., bytes], now: float, lap_number: int = 3) -> RaceState:
    state = RaceState(session_uid=123, player_index=2)
    values = {
        1: Session(0, 25, 20, 10, 5000, 10, 1, 0, 0, ()),
        2: Lap(90_000, 1000, 0, 0, 50.0, 5, lap_number, 0, 0, 0, 0, 0),
        7: CarStatus(17.0, 100.0, 8.0, 16, 16, 2, 1_000_000.0, 0, 0, 0, 0, 0),
        10: CarDamage((25.0, 20.0, 19.0, 22.0), (0, 0, 0, 0), (0, 0, 0, 0), 0, 0, 0, 0, 0),
    }
    for packet_id, value in values.items():
        size = {1: 632, 2: 972, 7: 1058, 10: 948}[packet_id]
        header = decode_packet(packet_factory(packet_id, size=size))
        state.observations[packet_id] = Observation(value, header, now)
    return state


def test_strategy_requires_evidence_and_explicit_assumptions(packet_factory: Callable[..., bytes]) -> None:
    state = state_at(packet_factory, 1.0)
    rates = (3.0, 2.0, 2.0, 2.0)
    assert "configure" in compare_pit_scenarios(state, 1.0, rates, StrategyAssumptions()).recommendation
    configured = StrategyAssumptions(23, 0.5, 0.04, 18, traffic_loss_seconds=2.0,
                                     second_compound_code=17)
    advice = compare_pit_scenarios(state, 1.0, rates, configured)
    assert advice.scenarios
    assert any("two stops" in plan.action for plan in advice.scenarios)
    box_now = next(plan for plan in advice.scenarios if plan.action == "box this lap")
    assert box_now.pit_lap == 3
    assert box_now.projected_end_wear_percent == 21.0
    assert advice.assumptions and advice.risks
    assert "unavailable" in compare_pit_scenarios(state, 6.0, rates, configured).recommendation
    state.observations[1] = Observation(Session(0, 25, 20, 10, 5000, 10, 1, 0, 1, ()),
                                        state.observations[1].header, 1.0)
    assert "safety-car" in compare_pit_scenarios(state, 1.0, rates, configured).recommendation


def test_strategy_keeps_small_finish_line_rounding_overshoot(packet_factory: Callable[..., bytes]) -> None:
    state = state_at(packet_factory, 1.0)
    lap = state.observations[2]
    from dataclasses import replace
    state.observations[2] = Observation(replace(lap.value, lap_distance_m=5000.7), lap.header, 1.0)
    advice = compare_pit_scenarios(state, 1.0, (3.0, 2.0, 2.0, 2.0),
                                   StrategyAssumptions(23, 0.5, 0.04, 18))
    assert advice.scenarios


def test_strategy_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        StrategyAssumptions(pit_loss_seconds=float("nan"))
    with pytest.raises(ValueError):
        StrategyAssumptions(profile="reckless")
    with pytest.raises(ValueError):
        StrategyAssumptions(safety_car_pit_loss_factor=1.1)


def test_history_records_debrief_and_exports(packet_factory: Callable[..., bytes], tmp_path: Path) -> None:
    path = tmp_path / "races.sqlite3"
    with RaceHistory(path) as history:
        first = state_at(packet_factory, 0.0, 2)
        history.observe(first, 0.0)
        second = state_at(packet_factory, 90.0, 3)
        history.observe(second, 90.0)
        history.record_advice(123, 3, "Tyre check")
        report = history.debrief("123")
        assert report["summary"]["recorded_laps"] == 2
        assert report["summary"]["final_observed_position"] == 5
        assert len(report["advice"]) == 1
        history.export("123", tmp_path / "export.csv", "csv")
        history.export("123", tmp_path / "export.json", "json")
    assert "lap_number" in (tmp_path / "export.csv").read_text()
    assert '"recorded_laps": 2' in (tmp_path / "export.json").read_text()


def test_history_ignores_menu_session_and_noisy_events(packet_factory: Callable[..., bytes], tmp_path: Path) -> None:
    with RaceHistory(tmp_path / "races.sqlite3") as history:
        menu = state_at(packet_factory, 0.0)
        menu.session_uid = 0
        history.observe(menu, 0.0)
        history.record_advice(0, 1, "menu alert")

        race = state_at(packet_factory, 1.0)
        for frame, code in enumerate(("BUTN", "SPTP", "PENA"), start=1):
            header = decode_packet(packet_factory(3, frame=frame))
            race.observations[3] = Observation(Event(code), header, 1.0)
            history.observe(race, 1.0)

        # Old recordings can already contain these rows; hide them from debriefs too.
        history.db.execute(
            "INSERT INTO events(uid,lap_number,event_code,recorded_utc) VALUES (?,?,?,?)",
            ("123", 3, "BUTN", "2026-01-01T00:00:00+00:00"),
        )
        history.db.commit()
        assert [row["uid"] for row in history.sessions()] == ["123"]
        assert [event["event_code"] for event in history.debrief("123")["events"]] == ["PENA"]


def test_history_worker_flushes_without_receiver_disk_writes(packet_factory: Callable[..., bytes], tmp_path: Path) -> None:
    path = tmp_path / "async.sqlite3"
    worker = HistoryWorker(path)
    worker.observe(state_at(packet_factory, 1.0, 2), 1.0)
    worker.record_advice(123, 2, "Check tyres")
    worker.close()
    with RaceHistory(path) as history:
        report = history.debrief("123")
    assert report["summary"]["recorded_laps"] == 1
    assert report["advice"][0]["message"] == "Check tyres"


def test_debrief_sector_coaching_uses_observed_history(packet_factory: Callable[..., bytes], tmp_path: Path) -> None:
    state = state_at(packet_factory, 1.0)
    header = decode_packet(packet_factory(11, size=1155))
    state.observations[11] = Observation(SessionHistory(2, 1, (
        LapHistory(90_000, 30_000, 30_000, 30_000, 0),
        LapHistory(91_000, 30_100, 30_800, 30_100, 0),
        LapHistory(92_000, 30_100, 31_200, 30_200, 0),
    ), ()), header, 1.0)
    with RaceHistory(tmp_path / "history.sqlite3") as history:
        history.observe(state, 1.0)
        report = history.debrief("123")
    assert len(report["sectors"]) == 3
    assert "Sector 2" in report["summary"]["coaching"]


def test_rivals_are_limited_to_shared_lap_data(packet_factory: Callable[..., bytes]) -> None:
    raw = bytearray(packet_factory(2, size=972))
    # Each car has a 43-byte packed LapData record; player index is 2.
    for index, pos, distance in ((2, 5, 2000.0), (3, 4, 2200.0), (4, 6, 1800.0)):
        offset = 24 + index * 43
        struct.pack_into("<I", raw, offset, 90_000)
        struct.pack_into("<f", raw, offset + 12, distance)
        struct.pack_into("<2B", raw, offset + 24, pos, 3)
    payload = decode_payload(bytes(raw), decode_packet(bytes(raw)))
    assert isinstance(payload, Lap)
    result = adjacent_rivals(payload, Session(0, 25, 20, 10, 5000, 10, 1, 0, 0, ()))
    assert result["ahead"]["distance_gap_m"] == 200
    assert result["behind"]["distance_gap_m"] == 200
    assert "tyres" in result["ahead"]["source"]


def test_full_grid_uses_shared_timing_and_hides_unavailable_rival_data() -> None:
    lap = Lap(90_000, 1000, 30_000, 31_000, 2000.0, 5, 3, 0, 1, 0, 0, 0,
              (RivalLap(3, 4, 3, 2200.0, 89_000, 0, 0, 29_000, 30_000),
               RivalLap(4, 6, 3, 1800.0, 91_000, 1, 0, 31_000, 32_000)))
    session = Session(0, 25, 20, 10, 5000, 10, 1, 0, 0, ())
    participant = Participant(1, 1, 44, "Driver", 0, (None, None, "Driver", "Ahead", "Behind"))
    grid = full_grid(lap, session, 2, participant)
    assert [row["position"] for row in grid] == [4, 5, 6]
    assert [row["name"] for row in grid] == ["Ahead", "Driver", "Behind"]
    assert grid[1]["driver_code"] == "DRI"
    assert grid[1]["race_number"] == 44
    assert grid[1]["team_name"] == "Ferrari"
    assert [row["relative_distance_m"] for row in grid] == [200, 0, -200]
    assert grid[0]["delta_last_lap_ms"] == -1000
    assert grid[2]["pit_status"] == 1
    assert "fuel" not in grid[0] and "tyre_wear" not in grid[0]
    assert grid[0]["estimated_gap_seconds"] == 3.6
    assert grid[0]["pace_to_pass_seconds_per_lap"] is not None
    assert grid[2]["estimated_gap_seconds"] == -3.6


def test_pit_rejoin_projection_requires_measured_loss_and_discloses_range() -> None:
    session = Session(0, 25, 20, 10, 5000, 10, 1, 0, 0, ())
    lap = Lap(90_000, 1000, 0, 0, 2000.0, 1, 3, 0, 0, 0, 0, 0)
    grid = [{"is_player": True, "estimated_gap_seconds": 0.0, "last_lap_ms": 90_000}]
    grid += [{"is_player": False, "estimated_gap_seconds": -float(gap),
              "last_lap_ms": 91_000} for gap in (5, 10, 15, 20, 25, 30)]
    assert project_rejoin(grid, lap, session, StrategyAssumptions())["status"] == "unavailable"
    result = project_rejoin(grid, lap, session, StrategyAssumptions(pit_loss_seconds=22))
    assert result["status"] == "estimate"
    assert result["box_now"]["position"] == 5
    assert result["box_next_lap"]["position"] == 5
    assert result["box_now"]["range"][0] <= 5 <= result["box_now"]["range"][1]
    safety = Session(0, 25, 20, 10, 5000, 10, 1, 0, 2, ())
    assert project_rejoin(grid, lap, safety, StrategyAssumptions(pit_loss_seconds=22))["status"] == "unavailable"
    reduced = project_rejoin(grid, lap, safety,
                             StrategyAssumptions(pit_loss_seconds=22, vsc_pit_loss_factor=0.5))
    assert reduced["status"] == "estimate" and reduced["box_now"]["position"] == 3
    assert reduced["box_next_lap"] is None  # SC/VSC lap pace is not comparable.
    assert project_rejoin(grid, lap, safety, StrategyAssumptions(
        pit_loss_seconds=22, safety_car_pit_loss_factor=0.5))["status"] == "unavailable"
    full_sc = Session(0, 25, 20, 10, 5000, 10, 1, 0, 1, ())
    assert project_rejoin(grid, lap, full_sc, StrategyAssumptions(
        pit_loss_seconds=22, vsc_pit_loss_factor=0.5))["status"] == "unavailable"


def test_shared_checkpoint_gap_uses_crossing_times_and_expires() -> None:
    gaps = CheckpointGaps()
    for now, player_m, rival_m in ((0.0, 10.0, 40.0), (0.2, 40.0, 70.0),
                                   (0.4, 70.0, 100.0)):
        lap = Lap(90_000, 1000, 0, 0, player_m, 2, 3, 0, 0, 0, 0, 0,
                  (RivalLap(3, 1, 3, rival_m, 89_000, 0, 0),))
        gaps.update(lap, 2, 5000, now)
    observed = gaps.gap_seconds(2, 3, 0.4)
    assert observed is not None and observed[0] == 0.2
    assert gaps.gap_seconds(2, 3, 16.0) is None
    gaps.clear()
    assert gaps.gap_seconds(2, 3, 0.4) is None


def test_dashboard_facts_compare_game_best_laps_only_while_history_is_fresh(
    packet_factory: Callable[..., bytes],
) -> None:
    from dataclasses import replace

    state = state_at(packet_factory, 1.0)
    lap_obs = state.observations[2]
    state.observations[2] = Observation(replace(lap_obs.value, rivals=(
        RivalLap(3, 4, 3, 100.0, 89_500, 0, 0),)), lap_obs.header, 1.0)
    for index, lap_ms in ((2, 90_000), (3, 89_000)):
        header = decode_packet(packet_factory(11, size=1155))
        state.histories[index] = Observation(SessionHistory(index, 1, (
            LapHistory(lap_ms, 30_000, 30_000, 30_000, 0),), ()), header, 1.0)
    facts = race_facts(state, Engineer(), 1.0)
    rival = next(row for row in facts["grid"] if row["car_index"] == 3)
    assert rival["best_lap_ms"] == 89_000
    assert rival["delta_best_lap_ms"] == -1000
    assert rival["recent_laps"] == [{"lap_number": 1, "lap_ms": 89_000,
                                     "sector1_ms": 30_000, "sector2_ms": 30_000,
                                     "sector3_ms": 30_000}]
    assert "P4 Car 4 at 89.000 seconds" in answer_question("Who has the fastest lap?", facts)
    assert "Your best is 1.000 seconds slower" in answer_question("Who has the fastest lap?", facts)
    assert "P4 Car 4 at 89.000 seconds" in Conversation().answer("Who has the fastest lap?", facts)
    dashboard = DashboardState()
    dashboard.publish(facts, "receiving", False)
    import json
    assert json.loads(dashboard.snapshot())["facts"]["grid"][0]["best_lap_ms"] == 89_000

    for packet_id in (1, 2):
        observation = state.observations[packet_id]
        state.observations[packet_id] = Observation(observation.value, observation.header, 12.0)
    stale_history = race_facts(state, Engineer(), 12.0)
    assert len(stale_history["grid"]) == 2
    assert all(row["best_lap_ms"] is None for row in stale_history["grid"])
    assert all(row["recent_laps"] == [] for row in stale_history["grid"])
    assert stale_history["sector_leaders"] == [None, None, None]
    assert "unavailable or stale" in answer_question("Who has the fastest lap?", stale_history)


def test_pit_window_board_uses_game_window_only_near_stop() -> None:
    from dataclasses import replace

    session = Session(0, 25, 20, 10, 5000, 10, 1, 0, 0, (), 5, 7)
    lap = Lap(90_000, 1000, 0, 0, 50.0, 5, 3, 0, 0, 0, 0, 0)
    assert pit_window_alert(session, lap) is None
    assert pit_window_alert(session, replace(lap, lap_number=4))["level"] == "near"
    assert pit_window_alert(session, replace(lap, lap_number=5))["level"] == "open"
    assert pit_window_alert(session, replace(lap, lap_number=7))["level"] == "urgent"
    assert pit_window_alert(session, replace(lap, lap_number=8)) is None
    assert pit_window_alert(session, replace(lap, lap_number=5, pit_status=1)) is None
    assert pit_window_alert(replace(session, pit_latest_lap=0), replace(lap, lap_number=5)) is None


def test_safety_board_only_has_active_sc_or_vsc_state(packet_factory: Callable[..., bytes]) -> None:
    from dataclasses import replace

    state = state_at(packet_factory, 1.0)
    observation = state.observations[1]
    assert safety_car_alert(observation.value) is None
    for value, title in ((1, "SAFETY CAR"), (2, "VIRTUAL SAFETY CAR")):
        state.observations[1] = Observation(replace(observation.value, safety_car=value),
                                            observation.header, 1.0)
        assert race_facts(state, Engineer(), 1.0)["safety_alert"]["title"] == title
    state.observations[1] = Observation(replace(observation.value, safety_car=0),
                                        observation.header, 1.0)
    assert race_facts(state, Engineer(), 1.0)["safety_alert"] is None
    assert race_facts(state, Engineer(), 10.0)["safety_alert"] is None


def test_sector_leaders_use_fresh_complete_history_and_current_completed_sectors(
    packet_factory: Callable[..., bytes],
) -> None:
    from dataclasses import replace

    state = state_at(packet_factory, 1.0)
    lap_obs = state.observations[2]
    state.observations[2] = Observation(replace(lap_obs.value, sector1_ms=29_500,
        rivals=(RivalLap(3, 4, 3, 100.0, 89_500, 0, 0, sector1_ms=28_900),)), lap_obs.header, 1.0)
    header = decode_packet(packet_factory(11, size=1155))
    state.histories[2] = Observation(SessionHistory(2, 1, (
        LapHistory(90_000, 30_000, 31_000, 29_000, 0),), ()), header, 1.0)
    state.histories[3] = Observation(SessionHistory(3, 1, (
        LapHistory(89_500, 29_000, 30_000, 30_500, 0),), ()), header, 1.0)
    facts = race_facts(state, Engineer(), 1.0)
    assert [(leader["car_index"], leader["ms"]) for leader in facts["sector_leaders"]] == [
        (3, 28_900), (3, 30_000), (2, 29_000)]


def test_sector_splits_use_observed_transition_and_reset_between_sessions() -> None:
    track = ObservedTrackMap()
    facts = {"session_uid": 1, "player_index": 2, "state_resets": 0,
             "grid": [{"car_index": 2, "position": 1, "name": "You", "is_player": True}],
             "fields": {"session": {"value": {"track_id": 3, "track_length_m": 5412}}}}
    positions = [None] * 22
    positions[2] = (100.0, 0.0, 200.0)
    facts["fields"].update({"lap": {"value": {"lap_number": 2, "lap_distance_m": 1000,
                                               "pit_status": 0, "sector": 0}},
                            "motion": {"value": {"position_m": positions[2],
                                                  "car_positions_m": positions}, "age_seconds": 0.0}})
    fallback = track.update(facts)
    assert [item["approximate"] for item in fallback["sector_markers"]] == [True, True]
    facts["fields"]["lap"]["value"].update(lap_distance_m=1800, sector=1)
    observed = track.update(facts)
    assert observed["sector_markers"][0] == {"label": "S2", "x_m": 100.0, "z_m": 200.0,
                                               "approximate": False}
    facts["state_resets"] = 1
    assert all(item["approximate"] for item in track.update(facts)["sector_markers"])


def test_grid_retains_last_valid_participant_names_for_session(
    packet_factory: Callable[..., bytes],
) -> None:
    from dataclasses import replace

    state = state_at(packet_factory, 1.0)
    lap_obs = state.observations[2]
    state.observations[2] = Observation(replace(lap_obs.value, rivals=(
        RivalLap(3, 4, 3, 100.0, 89_500, 0, 0),)), lap_obs.header, 20.0)
    session_obs = state.observations[1]
    state.observations[1] = Observation(session_obs.value, session_obs.header, 20.0)
    participant = Participant(1, 1, 44, "Driver", 0, (None, None, "Driver", "Ahead"))
    state.observations[4] = Observation(participant, lap_obs.header, 1.0)

    facts = race_facts(state, Engineer(), 20.0)
    assert facts["fields"]["participants"] is None  # Raw packet is stale.
    assert [row["name"] for row in facts["grid"]] == ["Ahead", "Driver"]


def test_observed_track_map_completes_clean_lap_and_masks_stale_positions() -> None:
    from math import cos, pi, sin

    track = ObservedTrackMap()
    facts = {"session_uid": 1, "player_index": 2, "state_resets": 0,
             "grid": [{"car_index": 2, "position": 5, "name": "You", "is_player": True,
                       "driver_code": "LEC", "race_number": 16, "team_name": "Ferrari",
                       "team_color": "#e32731", "team_text_color": "#ffffff"},
                      {"car_index": 3, "position": 4, "name": "Ahead", "is_player": False}],
             "fields": {}}
    for i in range(25):
        x, z = 100 * cos(2 * pi * i / 25), 100 * sin(2 * pi * i / 25)
        positions = [None] * 22
        positions[2], positions[3] = (x, 0, z), (x + 5, 0, z)
        facts["fields"] = {
            "lap": {"value": {"lap_number": 1, "lap_distance_m": i * 20, "pit_status": 0}},
            "motion": {"value": {"position_m": (x, 0, z), "car_positions_m": positions},
                       "age_seconds": 0.1},
        }
        snapshot = track.update(facts)
    assert not snapshot["complete"]
    facts["fields"]["lap"]["value"]["lap_number"] = 2
    snapshot = track.update(facts)
    assert snapshot["complete"] and len(snapshot["route"]) >= 20
    assert len(snapshot["markers"]) == 2
    player_marker = next(marker for marker in snapshot["markers"] if marker["is_player"])
    assert (player_marker["driver_code"], player_marker["race_number"], player_marker["team_color"]) == ("LEC", 16, "#e32731")
    facts["fields"] = {}
    facts["grid"] = []
    stale = track.update(facts)
    assert stale["complete"] and stale["markers"] == []
    facts["state_resets"] = 1
    reset = track.update(facts)
    assert reset["route"] == [] and not reset["complete"]


def test_track_map_discards_pit_lane_trace() -> None:
    track = ObservedTrackMap()
    facts = {"session_uid": 1, "player_index": 2, "state_resets": 0,
             "grid": [{"car_index": 2, "position": 1, "name": "You", "is_player": True}],
             "fields": {"lap": {"value": {"lap_number": 1, "lap_distance_m": 0, "pit_status": 0}},
                        "motion": {"value": {"position_m": (0, 0, 0),
                                             "car_positions_m": [(0, 0, 0)] * 22},
                                   "age_seconds": 0.0}}}
    assert len(track.update(facts)["route"]) == 1
    facts["fields"]["lap"]["value"]["pit_status"] = 1
    assert track.update(facts)["route"] == []
    facts["fields"]["lap"]["value"].update(lap_number=2, pit_status=0)
    assert not track.update(facts)["complete"]


def test_track_map_marks_lap_invalidation_without_claiming_track_boundary() -> None:
    track = ObservedTrackMap()
    facts = {"session_uid": 1, "player_index": 2, "state_resets": 0,
             "grid": [{"car_index": 2, "position": 1, "name": "You", "is_player": True}],
             "fields": {"lap": {"value": {"lap_number": 2, "lap_distance_m": 200, "pit_status": 0,
                                          "invalid": 0}},
                        "motion": {"value": {"position_m": (20, 0, 30),
                                             "car_positions_m": [(20, 0, 30)] * 22}, "age_seconds": 0.0}}}
    assert not track.update(facts)["lap_invalidations"]
    facts["fields"]["lap"]["value"]["invalid"] = 1
    marked = track.update(facts)
    assert marked["lap_invalidations"] == [{"x_m": 20, "z_m": 30, "lap": 2}]
    assert len(track.update(facts)["lap_invalidations"]) == 1


def test_practice_setup_fingerprint_history_and_labels(packet_factory: Callable[..., bytes], tmp_path: Path) -> None:
    raw = bytearray(packet_factory(5, size=1102))
    start = 24 + 2 * 49
    raw[start:start + 49] = bytes(range(49))
    setup = decode_payload(bytes(raw), decode_packet(bytes(raw)))
    assert isinstance(setup, CarSetup)
    raw[start] ^= 1
    assert decode_payload(bytes(raw), decode_packet(bytes(raw))) != setup

    from dataclasses import replace
    path = tmp_path / "practice.sqlite3"
    with RaceHistory(path) as history:
        for number, now in ((1, 1.0), (2, 91.0), (3, 182.0)):
            state = state_at(packet_factory, now, number)
            session_obs = state.observations[1]
            state.observations[1] = Observation(replace(session_obs.value, session_type=1),
                                                 session_obs.header, now)
            state.observations[5] = Observation(setup, decode_packet(packet_factory(5, size=1102)), now)
            history.observe(state, now)
        summary = history.practice_summary(1)
        assert len(summary) == 1 and summary[0]["laps"] == 2
        assert summary[0]["fingerprint"] == setup.fingerprint
        history.label_setup(1, setup.fingerprint, "Balanced preset")
        assert history.practice_summary(1)[0]["label"] == "Balanced preset"
        with pytest.raises(ValueError):
            history.label_setup(1, setup.fingerprint, " ")


def test_existing_lap_rows_survive_additive_setup_migration(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as old:
        old.execute("""CREATE TABLE laps (uid TEXT, lap_number INTEGER, last_lap_ms INTEGER,
                     position INTEGER, fuel_kg REAL, tyre_wear_max REAL, compound INTEGER,
                     ers_mj REAL, pit_stops INTEGER, weather INTEGER, recorded_utc TEXT,
                     PRIMARY KEY(uid,lap_number))""")
        old.execute("INSERT INTO laps(uid,lap_number,last_lap_ms,recorded_utc) VALUES (?,?,?,?)",
                    ("123", 1, 90000, "2026-10-28T00:00:00Z"))
    with RaceHistory(path) as history:
        row = history.db.execute("SELECT last_lap_ms,setup_fingerprint FROM laps WHERE uid='123'").fetchone()
        assert row == (90000, None)


def test_bad_rival_slot_does_not_discard_player_lap(packet_factory: Callable[..., bytes]) -> None:
    raw = bytearray(packet_factory(2, size=972))
    struct.pack_into("<2B", raw, 24 + 2 * 43 + 24, 5, 3)
    struct.pack_into("<f", raw, 24 + 3 * 43 + 12, float("nan"))
    payload = decode_payload(bytes(raw), decode_packet(bytes(raw)))
    assert isinstance(payload, Lap)
    assert payload.position == 5
    assert payload.rivals == ()


def test_grounded_answers_and_conversation_followup(packet_factory: Callable[..., bytes]) -> None:
    state = state_at(packet_factory, 1.0)
    engineer = Engineer()
    facts = race_facts(state, engineer, 1.0)
    dashboard = DashboardState(Conversation())
    dashboard.publish(facts, "receiving", False)
    assert "25% wear" in dashboard.ask("How are my tyres?")
    assert "25% wear" in dashboard.ask("Why?")
    dashboard.publish(race_facts(state, engineer, 6.0), "stale", False)
    assert "unavailable" in dashboard.ask("How are my tyres?")


def test_dependency_free_dashboard_serves_state_and_questions(packet_factory: Callable[..., bytes],
                                                               tmp_path: Path) -> None:
    import json
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    dashboard = DashboardState(history_path=str(tmp_path / "history.sqlite3"))
    dashboard.publish(race_facts(state_at(packet_factory, 1.0), Engineer(), 1.0), "receiving", False)
    thread, server = start_polling_dashboard(dashboard, port=0)
    base = f"http://127.0.0.1:{server.server.server_address[1]}"
    try:
        with urlopen(base + "/", timeout=2) as response:
            assert b"Track map" in response.read()
        with urlopen(base + "/api/state", timeout=2) as response:
            assert json.load(response)["connection"] == "receiving"
        with urlopen(base + "/practice", timeout=2) as response:
            assert b"Practice &amp; preset comparison" in response.read()
        with urlopen(base + "/api/practice?track_id=1", timeout=2) as response:
            assert json.load(response)["setups"] == []
        request = Request(base + "/api/setup-label",
                          data=b'{"track_id":1,"fingerprint":"0123456789abcdef","label":"Balanced"}',
                          headers={"Content-Type": "application/json", "Origin": base}, method="POST")
        with urlopen(request, timeout=2) as response:
            assert json.load(response)["saved"] is True
        request = Request(base + "/api/setup-label",
                          data=b'{"track_id":1,"fingerprint":"0123456789abcdef","label":"Balanced"}',
                          headers={"Content-Type": "application/json", "Origin": "http://example.com"},
                          method="POST")
        with pytest.raises(HTTPError) as exc:
            urlopen(request, timeout=2)
        assert exc.value.code == 403
        request = Request(base + "/api/ask", data=b'{"question":"How are my tyres?"}',
                          headers={"Content-Type": "application/json", "Origin": base}, method="POST")
        with urlopen(request, timeout=2) as response:
            assert "25% wear" in json.load(response)["answer"]
        request = Request(base + "/api/ask", data=b'{"question":"fuel"}',
                          headers={"Content-Type": "application/json", "Origin": "http://example.com"},
                          method="POST")
        with pytest.raises(HTTPError) as exc:
            urlopen(request, timeout=2)
        assert exc.value.code == 403
    finally:
        server.should_exit = True
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_llm_endpoint_restricts_plain_http() -> None:
    with pytest.raises(ValueError):
        IntentModel("http://example.com/v1/chat/completions", "model")
    with pytest.raises(ValueError):
        IntentModel("http://127.0.0.1:1234/other", "model")


def test_safety_weather_and_damage_alerts(packet_factory: Callable[..., bytes]) -> None:
    state = state_at(packet_factory, 1.0)
    monitor = AlertMonitor()
    assert monitor.evaluate(state, 1.0) == []
    original = state.observations[1]
    state.observations[1] = Observation(Session(3, 25, 20, 10, 5000, 10, 1, 0, 1, ()), original.header, 2.0)
    damage = state.observations[10]
    state.observations[10] = Observation(CarDamage((25.0, 20.0, 19.0, 22.0), (0, 0, 0, 0),
                                                  (0, 0, 0, 0), 55, 0, 0, 0, 0), damage.header, 2.0)
    alerts = monitor.evaluate(state, 2.0)
    assert [item.key for item in alerts] == ["wing_damage", "safety_car", "weather"]


def test_realtime_alert_remains_in_next_engineer_report(packet_factory: Callable[..., bytes]) -> None:
    state = state_at(packet_factory, 1.0)
    status = state.observations[7]
    state.observations[7] = Observation(CarStatus(1.0, 100.0, 0.5, 16, 16, 2,
                                                 1_000_000.0, 0, 0, 0, 0, 0),
                                        status.header, 1.0)
    engineer = Engineer()
    assert [alert.key for alert in engineer.on_packet(state, 1.0)] == ["fuel"]
    assert "Alert [high]: Fuel low" in engineer.report(state, 1.5)
    assert "Alert [high]: Fuel low" not in engineer.report(state, 2.0)


def test_packaged_data_path_stays_beside_executable_not_local_appdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "F1RaceEngineer.exe"))
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\saikr\AppData\Local")
    assert data_root() == tmp_path


def test_performance_mode_skips_dashboard_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def unexpected_start(*_args: object) -> None:
        pytest.fail("dashboard started in performance mode")

    monkeypatch.setattr(dashboard_module, "start_dashboard", unexpected_start)
    stop = ThreadEvent()
    stop.set()
    output = StringIO()
    run_receiver(output, port=0, stop=stop, history_path=str(tmp_path / "races.sqlite3"),
                 dashboard=True, performance_mode=True)
    assert "Bound UDP" in output.getvalue()
    assert (tmp_path / "races.sqlite3").exists()


def test_stop_file_closes_receiver_cleanly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(receiver, "CONTROL_DIRECTORY", tmp_path / "control")
    ready = ThreadEvent()

    class Output(StringIO):
        def flush(self) -> None:
            ready.set()

    output = Output()
    thread = Thread(target=run_receiver, args=(output,), kwargs={"port": 0, "stop_name": "test.stop"})
    thread.start()
    try:
        assert ready.wait(5)
        (tmp_path / "control" / "test.stop").write_text("stop", encoding="ascii")
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert "Bound UDP" in output.getvalue()
    finally:
        if thread.is_alive():
            (tmp_path / "control" / "test.stop").write_text("stop", encoding="ascii")
            thread.join(timeout=3)
