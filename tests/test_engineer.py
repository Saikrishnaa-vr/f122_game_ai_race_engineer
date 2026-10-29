from collections.abc import Callable
from dataclasses import replace
import struct

import pytest

from app.engineer import AlertConfig, AlertMonitor, DiagnosticMonitor, Engineer
from app.telemetry.payloads import CarDamage, CarStatus, Lap, Session
from app.telemetry.protocol import decode_packet
from app.telemetry.state import Observation, RaceState


def test_alerts_use_fresh_player_data_priority_and_cooldowns(packet_factory: Callable[..., bytes]) -> None:
    state = RaceState()
    status = bytearray(packet_factory(7, size=1058))
    struct.pack_into("<3f", status, 24 + 2 * 47 + 5, 1.0, 100.0, 0.5)
    struct.pack_into("<f", status, 24 + 2 * 47 + 29, 300000.0)
    damage = bytearray(packet_factory(10, size=948))
    struct.pack_into("<4f", damage, 24 + 2 * 42, 71.0, 72.0, 73.0, 74.0)
    for data in (status, damage):
        raw = bytes(data)
        state.apply(raw, decode_packet(raw), 1.0)

    monitor = AlertMonitor(AlertConfig(cooldown_seconds=2.0))
    alerts = monitor.evaluate(state, 1.0)
    assert [alert.key for alert in alerts] == ["fuel", "tyre", "ers"]
    assert "front-right 74.0%" in alerts[1].message
    assert monitor.evaluate(state, 2.0) == []
    assert [alert.key for alert in monitor.evaluate(state, 3.0)] == ["tyre"]
    assert monitor.evaluate(state, 5.0) == []


@pytest.mark.parametrize("config", [
    {"fuel_kg": -1}, {"tyre_wear_percent": 101}, {"ers_mj": float("nan")},
    {"cooldown_seconds": -1},
])
def test_invalid_alert_configuration_is_rejected(config: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        AlertConfig(**config)


def make_state(packet_factory: Callable[..., bytes], now: float, *, lap_number: int = 2,
               distance: float = 50.0, fuel: float = 20.0, wear: float = 20.0,
               session_type: int = 10, pit_status: int = 0, pit_stops: int = 0) -> RaceState:
    state = RaceState(session_uid=1, player_index=2)
    values = {
        1: Session(0, 25, 20, 10, 5000, session_type, 1, 0, 0, ()),
        2: Lap(0, 0, 0, 0, distance, 1, lap_number, pit_status, pit_stops, 0, 0, 0),
        7: CarStatus(fuel, 100.0, 8.0, 16, 16, lap_number - 1, 1000000.0, 0, 0, 0, 0, 0),
        10: CarDamage((wear, wear + 1, wear + 2, wear + 3), (0, 0, 0, 0),
                      (0, 0, 0, 0), 0, 0, 0, 0, 0),
    }
    for packet_id, value in values.items():
        sizes = {1: 632, 2: 972, 7: 1058, 10: 948}
        header = decode_packet(packet_factory(packet_id, size=sizes[packet_id]))
        state.observations[packet_id] = Observation(value, header, now)
    return state


def test_lap_start_samples_produce_labeled_estimates(packet_factory: Callable[..., bytes]) -> None:
    monitor = DiagnosticMonitor()
    first = make_state(packet_factory, 0.0)
    lines = monitor.evaluate(first, 0.0)
    assert "unavailable" in lines[0]
    assert "unavailable" in lines[1]
    second = make_state(packet_factory, 90.0, lap_number=3, fuel=18.0, wear=22.0)
    lines = monitor.evaluate(second, 90.0)
    assert "+1.01 laps" in lines[0]
    assert "2.00 kg/lap" in lines[1]
    assert "lap 25.5" in lines[2]
    assert "not a pit recommendation" in lines[2]
    assert "three comparable" in lines[3]
    third = make_state(packet_factory, 180.0, lap_number=4, fuel=16.0, wear=25.0)
    lines = monitor.evaluate(third, 180.0)
    assert "laps 18.0-25.0" in lines[3]
    assert "not a pit recommendation" in lines[3]


def test_packet_sampling_survives_reports_that_miss_lap_start(packet_factory: Callable[..., bytes]) -> None:
    engineer = Engineer()
    engineer.on_packet(make_state(packet_factory, 1.0, distance=50.0), 1.0)
    engineer.report(make_state(packet_factory, 5.0, distance=1000.0,
                               fuel=19.8, wear=20.5), 5.0)
    engineer.on_packet(make_state(packet_factory, 90.0, lap_number=3,
                                  distance=50.0, fuel=18.0, wear=22.0), 90.0)
    report = engineer.report(make_state(packet_factory, 95.0, lap_number=3,
                                        distance=1000.0, fuel=17.8, wear=22.5), 95.0)
    assert "Approximate fuel use: 2.00 kg/lap" in report
    assert "Wear threshold projection: lap" in report


def test_fractional_distance_past_integer_track_length_keeps_rates(packet_factory: Callable[..., bytes]) -> None:
    monitor = DiagnosticMonitor()
    monitor.evaluate(make_state(packet_factory, 0.0), 0.0)
    monitor.evaluate(make_state(packet_factory, 90.0, lap_number=3,
                                fuel=18.0, wear=22.0), 90.0)
    near_line = make_state(packet_factory, 180.0, lap_number=3, distance=5000.7,
                           fuel=16.1, wear=24.0)
    lines = monitor.evaluate(near_line, 180.0)
    assert "2.00 kg/lap" in lines[1]
    assert monitor.fuel_rate == 2.0
    next_lap = make_state(packet_factory, 181.0, lap_number=4,
                          distance=1.0, fuel=16.0, wear=25.0)
    assert "2.00 kg/lap" in monitor.evaluate(next_lap, 181.0)[1]


def test_stale_data_pit_stops_and_reset_discard_samples(packet_factory: Callable[..., bytes]) -> None:
    monitor = DiagnosticMonitor()
    monitor.evaluate(make_state(packet_factory, 0.0), 0.0)
    assert "fresh" in monitor.evaluate(make_state(packet_factory, 0.0), 5.0)[0]
    lines = monitor.evaluate(make_state(packet_factory, 90.0, lap_number=3, fuel=18.0, wear=22.0), 90.0)
    assert "unavailable" in lines[1]
    pit = make_state(packet_factory, 100.0, lap_number=3, pit_status=1)
    assert "pit lane" in monitor.evaluate(pit, 100.0)[0]
    monitor.evaluate(make_state(packet_factory, 110.0, lap_number=3), 110.0)
    state = make_state(packet_factory, 200.0, lap_number=4, fuel=18.0, wear=22.0)
    state.resets = 1
    assert "unavailable" in monitor.evaluate(state, 200.0)[1]


def test_non_race_and_noncomparable_samples_do_not_fabricate_estimates(packet_factory: Callable[..., bytes]) -> None:
    monitor = DiagnosticMonitor()
    monitor.evaluate(make_state(packet_factory, 0.0, session_type=1), 0.0)
    lines = monitor.evaluate(make_state(packet_factory, 90.0, lap_number=3, distance=200.0,
                                        fuel=18.0, wear=22.0, session_type=1), 90.0)
    assert "unavailable" in lines[0]
    assert "unavailable" in lines[1]
    assert "unavailable" in lines[2]


def test_alert_cooldown_resets_on_state_reset_and_time_rewind(packet_factory: Callable[..., bytes]) -> None:
    state = make_state(packet_factory, 10.0, wear=80.0)
    monitor = AlertMonitor()
    assert [alert.key for alert in monitor.evaluate(state, 10.0)] == ["tyre"]
    state.resets += 1
    assert [alert.key for alert in monitor.evaluate(state, 10.0)] == ["tyre"]
    state = make_state(packet_factory, 5.0, wear=80.0)
    assert [alert.key for alert in monitor.evaluate(state, 5.0)] == ["tyre"]


def test_game_pit_strategy_decoding_freshness_and_range(packet_factory: Callable[..., bytes]) -> None:
    data = bytearray(packet_factory(1, size=632))
    data[27] = 20
    data[30] = 10
    data[613:615] = bytes((8, 12))
    state = RaceState()
    state.apply(bytes(data), decode_packet(bytes(data)), 0.0)
    report = Engineer().report(state, 0.0)
    assert "Game pit strategy: ideal lap 8, latest lap 12" in report
    assert "Game pit strategy unavailable" in Engineer().report(state, 4.0)
    data[614] = 7
    state.apply(bytes(data), decode_packet(bytes(data)), 1.0)
    assert "Game pit strategy unavailable" in Engineer().report(state, 1.0)


def test_game_pit_window_alerts_once_per_window(packet_factory: Callable[..., bytes]) -> None:
    monitor = AlertMonitor()

    def race_at(now: float, lap_number: int) -> RaceState:
        state = make_state(packet_factory, now, lap_number=lap_number)
        observed = state.observations[1]
        state.observations[1] = Observation(
            replace(observed.value, pit_ideal_lap=3, pit_latest_lap=5),
            observed.header, now)
        return state

    assert not [alert for alert in monitor.evaluate(race_at(1.0, 2), 1.0)
                if alert.key.startswith("pit_")]
    opened = monitor.evaluate(race_at(2.0, 3), 2.0)
    assert [alert.key for alert in opened if alert.key.startswith("pit_")] == ["pit_window_(3, 5)"]
    assert not [alert for alert in monitor.evaluate(race_at(70.0, 3), 70.0)
                if alert.key.startswith("pit_")]
    latest = monitor.evaluate(race_at(170.0, 5), 170.0)
    assert [alert.key for alert in latest if alert.key.startswith("pit_")] == ["pit_latest_(3, 5)"]
    assert not [alert for alert in monitor.evaluate(race_at(171.0, 5), 171.0)
                if alert.key.startswith("pit_")]


def test_lap_gaps_and_tyre_changes_discard_rates(packet_factory: Callable[..., bytes]) -> None:
    monitor = DiagnosticMonitor()
    monitor.evaluate(make_state(packet_factory, 0.0), 0.0)
    monitor.evaluate(make_state(packet_factory, 90.0, lap_number=3, fuel=18.0, wear=22.0), 90.0)
    lines = monitor.evaluate(make_state(packet_factory, 180.0, lap_number=4, fuel=16.0, wear=0.0), 180.0)
    assert "unavailable" in lines[1]
    assert "unavailable" in lines[3]
    lines = monitor.evaluate(make_state(packet_factory, 360.0, lap_number=6, fuel=12.0, wear=4.0), 360.0)
    assert "unavailable" in lines[1]
