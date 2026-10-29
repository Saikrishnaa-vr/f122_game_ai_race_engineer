"""Replay the saved October Grand Prix through the dashboard contract.

Run from the project root: python scripts/verify_grand_prix.py
The 164 MB raw fixture stays in recordings/; this script does not modify it.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys
from zlib import decompress

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dashboard import DashboardState
from app.engineer import Engineer
from app.intelligence import race_facts
from app.replay_dashboard import ReplayLibrary
from app.strategy import StrategyAssumptions
from app.telemetry.capture import read_capture
from app.telemetry.receiver import ReceptionStats, process_packet
from app.telemetry.state import RaceState


NAME = "grand-prix-20261028-212717.f1cap"
EXPECTED_SHA256 = "91d7ca520d1a572610bbd37cfd0b7f22e1e3f16c0ccb442892b5d3c21a081888"


def main() -> int:
    path = Path(__file__).resolve().parents[1] / "recordings" / NAME
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    assert digest.hexdigest() == EXPECTED_SHA256, "saved race fixture has changed"

    stats = ReceptionStats(0.0, 0.0)
    state = RaceState()
    engineer = Engineer(strategy_assumptions=StrategyAssumptions(pit_loss_seconds=22.0))
    dashboard = DashboardState()
    max_grid = max_markers = max_route = max_lap = max_gaps = max_checkpoint_gaps = 0
    complete_route = False
    rejoin_estimates = 0
    max_sector_leaders = max_observed_splits = 0
    pit_alert_levels: Counter[str] = Counter()
    race_status_frames: Counter[str] = Counter()
    fuel_plan_frames = fuel_burn_frames = tyre_wear_frames = 0
    last_publish = -1.0
    ids: Counter[int] = Counter()
    for now, data in read_capture(NAME):
        header = process_packet(stats, state, data, now)
        if header is None:
            continue
        ids[header.packet_id] += 1
        latest = state.get_fresh(header.packet_id, now) if header.packet_id in (1, 7, 10) else None
        if latest is not None and latest.header.frame_identifier == header.frame_identifier:
            for alert in engineer.on_packet(state, now):
                dashboard.message(alert.message)
        if now - last_publish < 0.5:
            continue
        last_publish = now
        facts = race_facts(state, engineer, now)
        dashboard.publish(facts, stats.status(now), False)
        snapshot = json.loads(dashboard.snapshot())
        grid = facts["grid"]
        track = snapshot["track_map"]
        for row in grid:
            if row.get("race_status"):
                race_status_frames[row["race_status_source"]] += 1
        max_grid = max(max_grid, len(grid))
        max_markers = max(max_markers, len(track["markers"]))
        max_route = max(max_route, len(track["route"]))
        complete_route |= track["complete"]
        max_gaps = max(max_gaps, sum(row["estimated_gap_seconds"] is not None for row in grid))
        max_checkpoint_gaps = max(max_checkpoint_gaps,
                                  sum(row["gap_source"] == "shared same-checkpoint crossing times" for row in grid))
        max_sector_leaders = max(max_sector_leaders, sum(leader is not None for leader in facts["sector_leaders"]))
        max_observed_splits = max(max_observed_splits,
                                  sum(not split["approximate"] for split in track["sector_markers"]))
        if facts["pit_alert"]:
            pit_alert_levels[facts["pit_alert"]["level"]] += 1
        fuel_plan_frames += facts["estimates"].get("fuel_plan") is not None
        fuel_burn_frames += bool(facts["estimates"]["fuel_view"]["history"])
        tyre_wear_frames += facts["fields"].get("damage") is not None
        lap = facts["fields"]["lap"]
        if lap:
            max_lap = max(max_lap, lap["value"]["lap_number"])
        rejoin_estimates += facts["estimates"].get("rejoin", {}).get("status") == "estimate"

    result = {"capture": NAME, "sha256": digest.hexdigest(), "packets": stats.valid,
              "invalid": stats.invalid, "payload_errors": stats.payload_invalid,
              "final_classification_packets": ids[8], "max_lap": max_lap,
              "max_grid": max_grid, "max_map_markers": max_markers,
              "max_route_points": max_route, "route_completed": complete_route,
              "max_estimated_gaps": max_gaps, "max_checkpoint_gaps": max_checkpoint_gaps,
              "rejoin_samples_with_test_pit_loss": rejoin_estimates,
              "max_sector_leaders": max_sector_leaders,
              "max_observed_sector_splits": max_observed_splits,
              "fuel_plan_frames": fuel_plan_frames,
              "fuel_burn_history_frames": fuel_burn_frames,
              "tyre_wear_frames": tyre_wear_frames,
              "race_status_row_frames": dict(race_status_frames),
              "pit_alert_samples": dict(pit_alert_levels)}
    print(json.dumps(result, indent=2))
    assert stats.valid == 140_578 and stats.invalid == stats.payload_invalid == 0
    assert ids[8] == 2 and max_lap == 14
    assert max_grid == max_markers == 20 and max_gaps >= 15 and max_checkpoint_gaps >= 10
    assert complete_route and max_route >= 150 and rejoin_estimates > 0
    assert max_sector_leaders == 3 and max_observed_splits == 2
    assert pit_alert_levels["near"] > 0 and pit_alert_levels["open"] > 0
    assert fuel_plan_frames > 0 and fuel_burn_frames > 0 and tyre_wear_frames > 0
    assert not race_status_frames, "completed fixture unexpectedly marks a car out of the race"

    # Exercise the saved-race picker path too. It regenerates snapshots with
    # today's pipeline, so an old raw capture can verify new dashboard fields.
    library = ReplayLibrary(pit_loss_seconds=22.0)
    selection = library.select(NAME)
    controller = library.controller()
    assert controller is not None
    replay_pit_alerts: Counter[str] = Counter()
    for frame in controller.frames:
        alert = json.loads(decompress(frame.payload))["facts"]["pit_alert"]
        if alert:
            replay_pit_alerts[alert["level"]] += 1
    assert replay_pit_alerts["near"] > 0 and replay_pit_alerts["open"] > 0
    replay_snapshot = None
    for index, frame in enumerate(controller.frames):
        if frame.lap != 8:
            continue
        candidate = controller.control({"action": "seek", "index": index})
        if len(candidate["facts"]["grid"]) == len(candidate["track_map"]["markers"]) == 20:
            replay_snapshot = candidate
            break
    assert replay_snapshot is not None, "lap 8 replay has no full-field frame"
    rows = {row["car_index"]: row for row in replay_snapshot["facts"]["grid"]}
    markers = {marker["car_index"]: marker for marker in replay_snapshot["track_map"]["markers"]}
    assert set(rows) == set(markers)
    for car_index, row in rows.items():
        marker = markers[car_index]
        for key in ("position", "name", "race_number", "driver_code", "team_name", "team_color"):
            assert row[key] == marker[key], f"replay map/grid {key} differs for car {car_index}"
        assert row["race_number"] is not None and len(row["driver_code"]) == 3
    player = next(row for row in rows.values() if row["is_player"])
    assert player["race_number"] == 16 and player["driver_code"] == "LEC"
    assert len(replay_snapshot["track_map"]["sector_markers"]) == 2
    assert all(leader is not None for leader in replay_snapshot["facts"]["sector_leaders"])
    fuel_replay_snapshot = None
    for frame in controller.frames:
        if frame.lap < 8:
            continue
        candidate = json.loads(decompress(frame.payload))
        if (candidate["facts"]["estimates"].get("fuel_plan")
                and candidate["facts"]["estimates"]["fuel_view"]["history"]):
            fuel_replay_snapshot = candidate
            break
    assert fuel_replay_snapshot is not None
    assert len(fuel_replay_snapshot["facts"]["fields"]["damage"]["value"]["tyre_wear_percent"]) == 4
    assert replay_snapshot["replay"]["library_mode"] is True
    assert replay_snapshot["replay"]["details"]["track"] == "Bahrain International Circuit"
    print(json.dumps({"saved_replay": selection["name"], "frames": selection["frames"],
                      "checked_lap": 8, "grid_and_map_identities": len(rows),
                      "player": f"#{player['race_number']} {player['driver_code']}",
                      "replay_pit_alert_samples": dict(replay_pit_alerts)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
