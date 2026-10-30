from collections.abc import Callable
import math
import struct

import pytest

from app.telemetry.payloads import (
    CarDamage, CarSetup, CarStatus, CarTelemetry, Event, FinalClassification,
    Lap, Motion, Participant, Session, SessionHistory, decode_payload,
)
from app.telemetry.protocol import PACKET_TYPES, PacketError, decode_packet
from app.telemetry.state import RaceState


def packet(packet_factory: Callable[..., bytes], packet_id: int, **kwargs: object) -> bytearray:
    return bytearray(packet_factory(packet_id, size=PACKET_TYPES[packet_id][1], **kwargs))


def decode(data: bytearray) -> object:
    raw = bytes(data)
    return decode_payload(raw, decode_packet(raw))


def test_player_telemetry_uses_2022_offsets_and_wheel_order(packet_factory: Callable[..., bytes]) -> None:
    data = packet(packet_factory, 6)
    base = 24 + 2 * 60
    struct.pack_into("<HfffBbHBBH", data, base, 287, 0.75, -0.25, 0.5, 3, 7, 11111, 1, 50, 123)
    struct.pack_into("<4H4B4BH4f4B", data, base + 22, 400, 401, 402, 403, 81, 82, 83, 84,
                     91, 92, 93, 94, 105, 21.0, 22.0, 23.0, 24.0, 1, 2, 3, 4)
    result = decode(data)
    assert isinstance(result, CarTelemetry)
    assert (result.speed_kph, result.gear, result.engine_rpm) == (287, 7, 11111)
    assert (result.throttle, result.steer, result.brake, result.drs) == (0.75, -0.25, 0.5, 1)
    assert result.surface_c == (81, 82, 83, 84)
    assert result.inner_c == (91, 92, 93, 94)
    assert result.pressure_psi == (21.0, 22.0, 23.0, 24.0)


def test_player_fuel_ers_and_damage_offsets(packet_factory: Callable[..., bytes]) -> None:
    data = packet(packet_factory, 7)
    base = 24 + 2 * 47
    struct.pack_into("<3f", data, base + 5, 34.5, 110.0, 18.5)
    struct.pack_into("<3B", data, base + 25, 18, 17, 7)
    struct.pack_into("<fB3f", data, base + 29, 2500000.0, 3, 100000.0, 200000.0, 300000.0)
    status = decode(data)
    assert isinstance(status, CarStatus)
    assert (status.fuel_kg, status.fuel_capacity_kg, status.fuel_remaining_laps) == (34.5, 110.0, 18.5)
    assert (status.actual_compound, status.visual_compound, status.tyre_age_laps) == (18, 17, 7)
    assert (status.ers_joules, status.ers_deploy_mode, status.ers_deployed_j) == (2500000.0, 3, 300000.0)

    data = packet(packet_factory, 10)
    base = 24 + 2 * 42
    struct.pack_into("<4f4B4B", data, base, 1.0, 2.0, 3.0, 4.0, 5, 6, 7, 8, 9, 10, 11, 12)
    struct.pack_into("<3B", data, base + 24, 21, 22, 23)
    struct.pack_into("<2B", data, base + 32, 31, 32)
    damage = decode(data)
    assert isinstance(damage, CarDamage)
    assert damage.tyre_wear_percent == (1.0, 2.0, 3.0, 4.0)
    assert damage.tyre_damage_percent == (5, 6, 7, 8)
    assert (damage.front_left_wing_percent, damage.gearbox_percent, damage.engine_percent) == (21, 31, 32)


def test_lap_session_and_forecast_fields(packet_factory: Callable[..., bytes]) -> None:
    data = packet(packet_factory, 2)
    base = 24 + 2 * 43
    struct.pack_into("<IIHHfff", data, base, 92123, 12345, 27100, 31000, 1234.5, 4567.0, 1.0)
    struct.pack_into("<7B", data, base + 24, 4, 8, 1, 2, 2, 1, 5)
    lap = decode(data)
    assert isinstance(lap, Lap)
    assert (lap.position, lap.lap_number, lap.pit_status, lap.pit_stops) == (4, 8, 1, 2)
    assert (lap.last_lap_ms, lap.current_lap_ms, lap.lap_distance_m, lap.penalties_s) == (92123, 12345, 1234.5, 5)
    struct.pack_into("<B", data, base + 37, 4)  # Player resultStatus: DNF.
    rival_base = 24 + 3 * 43
    struct.pack_into("<2B", data, rival_base + 24, 3, 8)
    struct.pack_into("<B", data, rival_base + 37, 7)  # Rival resultStatus: retired.
    lap = decode(data)
    assert lap.result_status == 4
    assert next(rival for rival in lap.rivals if rival.car_index == 3).result_status == 7

    data = packet(packet_factory, 1)
    struct.pack_into("<BbbBHBbBHH6B", data, 24, 3, 31, 23, 53, 5793, 10, 13, 0, 300, 3600, 80, 0, 0, 0, 0, 0)
    struct.pack_into("<3B", data, 148, 2, 1, 1)
    data[625] = 19  # m_gameMode: Career '22.
    struct.pack_into("<BBBbbbbB", data, 151, 10, 15, 4, 27, 1, 21, 2, 70)
    session = decode(data)
    assert isinstance(session, Session)
    assert (session.track_id, session.total_laps, session.track_length_m, session.safety_car) == (13, 53, 5793, 2)
    assert (session.weather, session.track_c, session.air_c, session.time_left_s) == (3, 31, 23, 300)
    assert session.network_game == 1
    assert session.game_mode == 19
    assert (session.forecasts[0].offset_minutes, session.forecasts[0].rain_percent) == (15, 70)


def test_motion_participant_event_and_player_history(packet_factory: Callable[..., bytes]) -> None:
    data = packet(packet_factory, 0)
    struct.pack_into("<6f6h6f", data, 24 + 2 * 60, 1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6, 0.5, 0.25, 1.0, 0.1, 0.2, 0.3)
    struct.pack_into("<3f", data, 24 + 3 * 60, 11, 12, 13)
    motion = decode(data)
    assert isinstance(motion, Motion)
    assert motion.position_m == (1.0, 2.0, 3.0)
    assert motion.velocity_m_per_s == (4.0, 5.0, 6.0)
    assert motion.g_force == (0.5, 0.25, 1.0)
    assert motion.car_positions_m[2] == (1.0, 2.0, 3.0)
    assert motion.car_positions_m[3] == (11.0, 12.0, 13.0)

    data = packet(packet_factory, 4)
    data[24] = 3
    struct.pack_into("<7B48sB", data, 25 + 1 * 56, 0, 12, 0, 6, 0, 44, 0, b"Rival", 0)
    struct.pack_into("<7B48sB", data, 25 + 2 * 56, 0, 42, 0, 6, 0, 16, 0, b"Driver", 0)
    participant = decode(data)
    assert isinstance(participant, Participant)
    assert (participant.driver_id, participant.team_id, participant.race_number, participant.name) == (42, 6, 16, "Driver")
    assert participant.car_names[1:3] == ("Rival", "Driver")
    assert participant.car_team_ids[1:3] == (6, 6)
    assert participant.car_race_numbers[1:3] == (44, 16)

    data = packet(packet_factory, 3)
    data[24:28] = b"FLBK"
    assert decode(data) == Event("FLBK")

    data = packet(packet_factory, 11)
    struct.pack_into("<7B", data, 24, 2, 1, 1, 1, 0, 0, 0)
    struct.pack_into("<IHHHB", data, 31, 90200, 30000, 30100, 30100, 15)
    struct.pack_into("<3B", data, 1131, 255, 18, 17)
    history = decode(data)
    assert isinstance(history, SessionHistory)
    assert (history.laps[0].lap_ms, history.laps[0].validity_flags) == (90200, 15)
    assert history.tyre_stints == ((255, 18, 17),)
    data[24] = 1
    assert isinstance(decode(data), SessionHistory)


def test_bad_rival_motion_slot_does_not_discard_player_position(packet_factory: Callable[..., bytes]) -> None:
    data = packet(packet_factory, 0)
    struct.pack_into("<3f", data, 24 + 2 * 60, 100, 0, 200)
    struct.pack_into("<f", data, 24 + 3 * 60, math.nan)
    motion = decode(data)
    assert isinstance(motion, Motion)
    assert motion.position_m == (100.0, 0.0, 200.0)
    assert motion.car_positions_m[3] is None


def test_rival_history_is_kept_separately_and_expires(packet_factory: Callable[..., bytes]) -> None:
    state = RaceState()
    player = packet(packet_factory, 11, frame=10)
    struct.pack_into("<4B", player, 24, 2, 1, 0, 1)
    struct.pack_into("<I", player, 31, 90_000)
    rival = packet(packet_factory, 11, frame=11)
    struct.pack_into("<4B", rival, 24, 3, 1, 0, 1)
    struct.pack_into("<I", rival, 31, 89_000)
    for data in (player, rival):
        raw = bytes(data)
        state.apply(raw, decode_packet(raw), 1.0)
    assert isinstance(state.get_fresh(11, 1.0).value, SessionHistory)
    assert state.get_fresh(11, 1.0).value.car_index == 2
    assert state.get_fresh_history(3, 1.0).value.laps[0].lap_ms == 89_000
    assert state.get_fresh_history(3, 11.0) is None
    flashback = packet(packet_factory, 3, frame=12)
    flashback[24:28] = b"FLBK"
    raw = bytes(flashback)
    state.apply(raw, decode_packet(raw), 2.0)
    assert state.get_fresh_history(3, 2.0) is None


def test_official_v16_setup_and_classification_layouts(packet_factory: Callable[..., bytes]) -> None:
    setup_raw = packet(packet_factory, 5)
    values = (7, 8, 60, 55, -2.5, -1.1, 0.07, 0.2, 4, 5, 6, 7, 8, 9, 95, 56,
              22.1, 22.2, 23.1, 23.2, 6, 28.5)
    struct.pack_into("<4B4f8B4fBf", setup_raw, 24 + 2 * 49, *values)
    setup = decode(setup_raw)
    assert isinstance(setup, CarSetup)
    assert setup.parameters.front_wing == 7
    assert setup.parameters.front_camber == pytest.approx(-2.5)
    assert setup.parameters.tyre_pressure_psi == pytest.approx((22.1, 22.2, 23.1, 23.2))
    assert setup.parameters.fuel_load_kg == pytest.approx(28.5)

    raw = packet(packet_factory, 8)
    raw[24] = 2
    struct.pack_into("<6BId3B8B8B8B", raw, 25,
                     1, 14, 2, 25, 1, 3, 90_123, 5500.5, 5, 1, 2,
                     *([18, 19] + [0] * 6), *([16, 17] + [0] * 6),
                     *([6, 14] + [0] * 6))
    result = decode(raw)
    assert isinstance(result, FinalClassification)
    assert len(result.cars) == 2
    assert result.cars[0].position == 1
    assert result.cars[0].best_lap_ms == 90_123
    assert result.cars[0].tyre_stints == ((18, 16, 6), (19, 17, 14))
    raw[24] = 23
    with pytest.raises(PacketError, match="classification car count"):
        decode(raw)


def test_bad_values_are_explicit(packet_factory: Callable[..., bytes]) -> None:
    data = packet(packet_factory, 6)
    struct.pack_into("<f", data, 24 + 2 * 60 + 2, math.nan)
    with pytest.raises(PacketError, match="non-finite"):
        decode(data)
    data = packet(packet_factory, 1)
    data[150] = 57
    with pytest.raises(PacketError, match="forecast count"):
        decode(data)
    data = packet(packet_factory, 11)
    data[25] = 101
    with pytest.raises(PacketError, match="history"):
        decode(data)


def test_state_freshness_session_changes_reordering_and_flashback(packet_factory: Callable[..., bytes]) -> None:
    state = RaceState()

    def apply(data: bytearray, now: float) -> None:
        raw = bytes(data)
        state.apply(raw, decode_packet(raw), now)

    first = packet(packet_factory, 6, frame=100)
    struct.pack_into("<H", first, 24 + 2 * 60, 250)
    apply(first, 1.0)
    assert "250 km/h" in state.format_snapshot(2.0)
    assert "Driving: stale" in state.format_snapshot(3.0)
    older = packet(packet_factory, 6, frame=99)
    struct.pack_into("<H", older, 24 + 2 * 60, 100)
    apply(older, 2.0)
    assert state.reordered == 1
    assert "250 km/h" in state.format_snapshot(2.0)
    flashback = packet(packet_factory, 3, frame=80)
    flashback[24:28] = b"FLBK"
    apply(flashback, 3.0)
    assert state.resets == 1
    assert "Driving: unavailable" in state.format_snapshot(3.0)
    apply(packet(packet_factory, 6, frame=81), 3.1)
    assert state.get_fresh(6, 3.1) is not None
    new_session = packet(packet_factory, 1, session_uid=999)
    new_session[24 + 6] = 10  # A recognized driving Session can replace the old UID.
    apply(new_session, 4.0)
    assert state.resets == 2
    assert state.get_fresh(6, 4.0) is None


def test_snapshot_shows_fresh_hud_fields_and_hides_expired_values(packet_factory: Callable[..., bytes]) -> None:
    state = RaceState()
    telemetry = packet(packet_factory, 6)
    struct.pack_into("<H", telemetry, 24 + 2 * 60, 287)
    telemetry[24 + 2 * 60 + 18] = 1  # DRS
    struct.pack_into("<4B4B", telemetry, 24 + 2 * 60 + 30,
                     81, 82, 83, 84, 91, 92, 93, 94)
    lap = packet(packet_factory, 2)
    struct.pack_into("<IIHH", lap, 24 + 2 * 43, 92123, 12345, 27100, 31000)
    struct.pack_into("<4B", lap, 24 + 2 * 43 + 24, 4, 8, 1, 2)
    status = packet(packet_factory, 7)
    struct.pack_into("<3B", status, 24 + 2 * 47 + 25, 18, 17, 7)
    damage = packet(packet_factory, 10)
    struct.pack_into("<3B", damage, 24 + 2 * 42 + 24, 21, 22, 23)
    session = packet(packet_factory, 1)
    struct.pack_into("<2B", session, 613, 5, 9)
    for data in (telemetry, lap, status, damage, session):
        raw = bytes(data)
        state.apply(raw, decode_packet(raw), 5.0)

    fresh = state.format_snapshot(5.1)
    assert "287 km/h" in fresh and "DRS 1" in fresh
    assert "surface 81/82/83/84 C, inner 91/92/93/94 C" in fresh
    assert "sectors 27100/31000 ms, pit status 1, stops 2" in fresh
    assert "tyre actual/visual codes 18/17, age 7 laps" in fresh
    assert "Wing damage FL/FR/rear: 21/22/23%" in fresh
    assert "game pit ideal/latest laps 5/9" in fresh

    expired = state.format_snapshot(11.0)
    assert "Driving: stale" in expired
    assert "Lap: stale" in expired
    assert "Fuel/ERS/compound: stale" in expired
    assert "Tyre wear: stale" in expired
    assert "Session: stale" in expired
    assert "81/82/83/84" not in expired
    assert "21/22/23%" not in expired
    assert "5/9" not in expired


def test_frame_counter_wrap_is_forward(packet_factory: Callable[..., bytes]) -> None:
    state = RaceState()
    for frame, now in [(0xFFFFFFFE, 1.0), (1, 2.0)]:
        data = bytes(packet(packet_factory, 6, frame=frame))
        state.apply(data, decode_packet(data), now)
    assert state.reordered == 0
    assert state.get_fresh(6, 2.0).header.frame_identifier == 1


def test_rewind_recovers_when_flashback_event_is_lost(packet_factory: Callable[..., bytes]) -> None:
    state = RaceState()
    for frame, game_time, now in ((100, 100.0, 1.0), (101, 100.1, 1.1)):
        raw = bytes(packet(packet_factory, 6, frame=frame, session_time=game_time))
        state.apply(raw, decode_packet(raw), now)
    old = bytes(packet(packet_factory, 6, frame=80, session_time=80.0))
    state.apply(old, decode_packet(old), 2.0)
    assert state.resets == 0
    assert state.get_fresh(6, 2.0).header.frame_identifier == 101
    resumed = bytes(packet(packet_factory, 7, frame=81, session_time=80.1))
    state.apply(resumed, decode_packet(resumed), 2.1)
    assert state.resets == 1
    assert state.timeline.epoch == 1
    assert any(event["kind"] == "clock_rewind" for event in state.timeline.events)
    assert state.get_fresh(6, 2.1) is None
    assert state.get_fresh(7, 2.1).header.frame_identifier == 81


def test_isolated_delayed_packet_does_not_reset_race(packet_factory: Callable[..., bytes]) -> None:
    state = RaceState()
    for frame, game_time, now in ((100, 100.0, 1.0), (80, 80.0, 2.0), (101, 100.1, 2.1)):
        raw = bytes(packet(packet_factory, 6, frame=frame, session_time=game_time))
        state.apply(raw, decode_packet(raw), now)
    assert state.resets == 0
    assert state.get_fresh(6, 2.1).header.frame_identifier == 101
