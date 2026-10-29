"""Race retirement display follows explicit game status and sustained inactivity."""

import json

from app.dashboard import DashboardState


def facts(player_lap: int, *, rival_distance: float = 2000, rival_x: float = 100,
          rival_status: int = 2, pit: int = 0, safety: int = 0,
          reset: int = 0, include_rival: bool = True) -> dict:
    rows = [{"car_index": 2, "position": 2, "name": "You", "is_player": True,
             "lap_number": player_lap, "lap_distance_m": 100, "result_status": 2}]
    if include_rival:
        rows.append({"car_index": 3, "position": 1, "name": "Rival", "is_player": False,
                     "lap_number": 1, "lap_distance_m": rival_distance,
                     "pit_status": pit, "result_status": rival_status})
    positions = [None] * 22
    positions[2] = (player_lap * 50, 0, 0)
    positions[3] = (rival_x, 0, 100)
    return {"session_uid": 1, "player_index": 2, "state_resets": reset,
            "fields": {"session": {"value": {"session_type": 10, "track_id": 3,
                                               "track_length_m": 5000, "safety_car": safety,
                                               "marshal_zones": []}},
                       "lap": {"value": {"lap_number": player_lap, "lap_distance_m": 100,
                                           "pit_status": 0, "sector": 0, "invalid": 0}},
                       "motion": {"value": {"position_m": positions[2],
                                              "car_positions_m": positions,
                                              "velocity_m_per_s": (50, 0, 0)},
                                  "age_seconds": 0.1}},
            "grid": rows, "estimates": {}}


def published(dashboard: DashboardState, data: dict) -> dict:
    dashboard.publish(data, "receiving", False)
    return json.loads(dashboard.snapshot())


def test_stationary_rival_becomes_inferred_dnf_after_two_player_laps_and_leaves_map() -> None:
    dashboard = DashboardState()
    assert len(published(dashboard, facts(1))["track_map"]["markers"]) == 2
    one_lap = published(dashboard, facts(2))
    assert not one_lap["facts"]["grid"][1].get("out_of_race")
    retired = published(dashboard, facts(3))
    row = retired["facts"]["grid"][1]
    assert row["race_status"] == "DNF"
    assert "inferred" in row["race_status_source"]
    assert [marker["car_index"] for marker in retired["track_map"]["markers"]] == [2]

    missing = published(dashboard, facts(3, include_rival=False))
    assert missing["facts"]["grid"][-1]["race_status"] == "DNF"
    assert len(missing["track_map"]["markers"]) == 1

    moving = published(dashboard, facts(3, rival_distance=3000, rival_x=300))
    assert not moving["facts"]["grid"][1].get("out_of_race")
    assert len(moving["track_map"]["markers"]) == 2


def test_game_result_status_immediate_and_pit_safety_reset_stationary_clock() -> None:
    dashboard = DashboardState()
    explicit = published(dashboard, facts(1, rival_status=4))
    assert explicit["facts"]["grid"][1]["race_status_source"] == "game result status"
    assert len(explicit["track_map"]["markers"]) == 1

    dashboard = DashboardState()
    published(dashboard, facts(1))
    published(dashboard, facts(2, pit=1))
    published(dashboard, facts(3, safety=1))
    clear = published(dashboard, facts(4))
    assert not clear["facts"]["grid"][1].get("out_of_race")
    assert len(clear["track_map"]["markers"]) == 2
    reset = published(dashboard, facts(5, reset=1))
    assert not reset["facts"]["grid"][1].get("out_of_race")


def test_missing_interval_and_restart_do_not_infer_stationary_dnf() -> None:
    dashboard = DashboardState()
    for lap, phase, generation in ((1, "GREEN", 1), (3, "STALE", 2),
                                   (4, "GREEN", 3), (5, "GREEN", 3)):
        data = facts(lap)
        data["race_phase"] = {"status": phase, "generation": generation}
        current = published(dashboard, data)
        assert not current["facts"]["grid"][1].get("out_of_race")
    data = facts(6)
    data["race_phase"] = {"status": "GREEN", "generation": 3}
    assert published(dashboard, data)["facts"]["grid"][1]["race_status"] == "DNF"
