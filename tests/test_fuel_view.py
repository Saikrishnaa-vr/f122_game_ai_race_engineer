"""Fuel budgeting and observed lift-point behaviour across live/replay snapshots."""

from app.fuel_view import FuelView
from app.intelligence import fuel_finish_projection
from app.track_map import ObservedTrackMap


def snapshot(lap: int, distance: float, fuel: float, brake: float = 0.0,
             speed: int = 200, *, reset: int = 0, safety_car: int = 0) -> dict:
    return {"session_uid": 42, "player_index": 2, "state_resets": reset,
            "fields": {"session": {"value": {"session_type": 10, "track_id": 3,
                                             "track_length_m": 5000, "safety_car": safety_car}},
                       "lap": {"value": {"lap_number": lap, "lap_distance_m": distance,
                                          "pit_status": 0, "pit_stops": 0, "invalid": 0,
                                          "sector": 1}},
                       "car_status": {"value": {"fuel_kg": fuel, "actual_compound": 18}},
                       "driving": {"value": {"speed_kph": speed, "brake": brake}}},
            "estimates": {"fuel_plan": fuel_finish_projection(fuel, 2.0, 7.0 - lap)}}


def test_fuel_finish_projection_requires_a_valid_comparable_rate() -> None:
    short = fuel_finish_projection(8.0, 2.0, 5.0)
    assert short is not None
    assert short["status"] == "short"
    assert short["projected_finish_kg"] == -2.0
    assert short["required_save_kg_per_lap"] == 0.4
    assert short["target_use_kg_per_lap"] == 1.6
    assert fuel_finish_projection(10.5, 2.0, 5.0)["status"] == "on_target"
    assert fuel_finish_projection(8.0, 0.0, 5.0) is None


def test_fuel_view_replay_lap_burn_and_two_lap_braking_candidate() -> None:
    view = FuelView()
    assert view.update(snapshot(1, 50, 10))["status"] == "learning"
    view.update(snapshot(1, 1000, 9.6))
    view.update(snapshot(1, 1100, 9.5, brake=0.8, speed=150))
    assert view.update(snapshot(2, 30, 8))["history"] == [{"lap_number": 1, "used_kg": 2}]
    view.update(snapshot(2, 1000, 7.6))
    view.update(snapshot(2, 1120, 7.5, brake=0.8, speed=150))
    candidate = view.update(snapshot(3, 30, 6))
    assert candidate["history"] == [{"lap_number": 1, "used_kg": 2},
                                    {"lap_number": 2, "used_kg": 2}]
    assert candidate["status"] == "candidate"
    assert candidate["sector"] == 2
    assert candidate["target_distance_m"] == 1000
    assert candidate["distance_ahead_m"] == 970
    assert "uncalibrated" in candidate["message"]

    facts = snapshot(3, 30, 6)
    facts["estimates"]["fuel_view"] = candidate
    marker = ObservedTrackMap().update(facts)["fuel_lift"]
    assert marker is not None and marker["sector"] == 2

    held = view.update(snapshot(3, 40, 6, safety_car=1))
    assert held["status"] == "hold"
    restarted = view.update(snapshot(3, 50, 6, reset=1))
    assert restarted["history"] == []
    assert restarted["status"] == "learning"
