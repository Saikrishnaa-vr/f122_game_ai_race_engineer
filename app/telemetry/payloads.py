"""Selected fields from the packed F1 22 UDP 2022 payload structures."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2s
import math
import struct
from typing import TypeAlias

from .protocol import CAR_COUNT, HEADER, PacketError, PacketHeader


MOTION = struct.Struct("<6f6h6f")
MOTION_POSITION = struct.Struct("<3f")
SESSION = struct.Struct("<BbbBHBbBHH6B")
FORECAST = struct.Struct("<BBBbbbbB")
LAP = struct.Struct("<IIHHfff14BHHB")
PARTICIPANT = struct.Struct("<7B48sB")
TELEMETRY = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")
STATUS = struct.Struct("<5B3f2H2BH3BbfB3fB")
DAMAGE = struct.Struct("<4f4B4B18B")
HISTORY_LAP = struct.Struct("<IHHHB")
SETUP = struct.Struct("<4B4f8B4fBf")
CLASSIFICATION = struct.Struct("<6BId3B8B8B8B")


@dataclass(frozen=True, slots=True)
class Motion:
    position_m: tuple[float, float, float]
    velocity_m_per_s: tuple[float, float, float]
    g_force: tuple[float, float, float]
    orientation_rad: tuple[float, float, float]
    # Shared world positions from the same Motion packet; None marks an
    # unusable slot. The active grid is determined from fresh Lap data.
    car_positions_m: tuple[tuple[float, float, float] | None, ...] = ()


@dataclass(frozen=True, slots=True)
class Forecast:
    session_type: int
    offset_minutes: int
    weather: int
    track_c: int
    air_c: int
    rain_percent: int


@dataclass(frozen=True, slots=True)
class Session:
    weather: int
    track_c: int
    air_c: int
    total_laps: int
    track_length_m: int
    session_type: int
    track_id: int
    time_left_s: int
    safety_car: int
    forecasts: tuple[Forecast, ...]
    pit_ideal_lap: int = 0
    pit_latest_lap: int = 0
    marshal_zones: tuple[tuple[float, int], ...] = ()
    network_game: int | None = None  # m_networkGame: 0 offline, 1 online; not a LAN identifier.
    game_mode: int | None = None  # m_gameMode: documented game-mode ID, not a LAN flag.


@dataclass(frozen=True, slots=True)
class Lap:
    last_lap_ms: int
    current_lap_ms: int
    sector1_ms: int
    sector2_ms: int
    lap_distance_m: float
    position: int
    lap_number: int
    pit_status: int
    pit_stops: int
    sector: int
    invalid: int
    penalties_s: int
    rivals: tuple["RivalLap", ...] = ()
    result_status: int = 2


@dataclass(frozen=True, slots=True)
class RivalLap:
    car_index: int
    position: int
    lap_number: int
    lap_distance_m: float
    last_lap_ms: int
    pit_status: int
    invalid: int
    sector1_ms: int = 0
    sector2_ms: int = 0
    pit_stops: int = 0
    penalties_s: int = 0
    result_status: int = 2


@dataclass(frozen=True, slots=True)
class Participant:
    driver_id: int
    team_id: int
    race_number: int
    name: str
    telemetry_setting: int
    car_names: tuple[str | None, ...] = ()
    car_team_ids: tuple[int | None, ...] = ()
    car_race_numbers: tuple[int | None, ...] = ()


@dataclass(frozen=True, slots=True)
class CarSetup:
    """Stable identity for the game's 49-byte setup; preset names are user supplied."""

    fingerprint: str
    parameters: "SetupParameters | None" = None


@dataclass(frozen=True, slots=True)
class SetupParameters:
    front_wing: int
    rear_wing: int
    on_throttle: int
    off_throttle: int
    front_camber: float
    rear_camber: float
    front_toe: float
    rear_toe: float
    front_suspension: int
    rear_suspension: int
    front_anti_roll_bar: int
    rear_anti_roll_bar: int
    front_suspension_height: int
    rear_suspension_height: int
    brake_pressure: int
    brake_bias: int
    tyre_pressure_psi: tuple[float, float, float, float]
    ballast: int
    fuel_load_kg: float


@dataclass(frozen=True, slots=True)
class CarTelemetry:
    speed_kph: int
    throttle: float
    steer: float
    brake: float
    gear: int
    engine_rpm: int
    drs: int
    # All four-wheel arrays use the protocol order: rear-left, rear-right,
    # front-left, front-right.
    surface_c: tuple[int, int, int, int]
    inner_c: tuple[int, int, int, int]
    pressure_psi: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class CarStatus:
    fuel_kg: float
    fuel_capacity_kg: float
    fuel_remaining_laps: float
    actual_compound: int
    visual_compound: int
    tyre_age_laps: int
    ers_joules: float
    ers_deploy_mode: int
    ers_harvested_mguk_j: float
    ers_harvested_mguh_j: float
    ers_deployed_j: float
    drs_allowed: int


@dataclass(frozen=True, slots=True)
class CarDamage:
    tyre_wear_percent: tuple[float, float, float, float]
    tyre_damage_percent: tuple[int, int, int, int]
    brake_damage_percent: tuple[int, int, int, int]
    front_left_wing_percent: int
    front_right_wing_percent: int
    rear_wing_percent: int
    gearbox_percent: int
    engine_percent: int


@dataclass(frozen=True, slots=True)
class Event:
    code: str


@dataclass(frozen=True, slots=True)
class LapHistory:
    lap_ms: int
    sector1_ms: int
    sector2_ms: int
    sector3_ms: int
    validity_flags: int


@dataclass(frozen=True, slots=True)
class SessionHistory:
    car_index: int
    best_lap_number: int
    laps: tuple[LapHistory, ...]
    # end lap 255 means the current stint.
    tyre_stints: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True, slots=True)
class ClassificationEntry:
    car_index: int
    position: int
    laps: int
    grid_position: int
    points: int
    pit_stops: int
    result_status: int
    best_lap_ms: int
    total_race_s: float
    penalties_s: int
    penalty_count: int
    tyre_stints: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True, slots=True)
class FinalClassification:
    cars: tuple[ClassificationEntry, ...]


Payload: TypeAlias = Motion | Session | Lap | Participant | CarSetup | CarTelemetry | CarStatus | CarDamage | Event | SessionHistory | FinalClassification


def _finite(values: tuple[object, ...]) -> None:
    if any(isinstance(value, float) and not math.isfinite(value) for value in values):
        raise PacketError("payload_value", "payload contains a non-finite value")


def _entry(layout: struct.Struct, data: bytes, index: int, offset: int = HEADER.size) -> tuple:
    values = layout.unpack_from(data, offset + index * layout.size)
    _finite(values)
    return values


def decode_payload(data: bytes, header: PacketHeader) -> Payload | None:
    """Decode supported selected fields after decode_packet has validated length."""
    player = header.player_car_index
    packet_id = header.packet_id
    if packet_id == 0:
        v = _entry(MOTION, data, player)
        positions = []
        for index in range(CAR_COUNT):
            position = MOTION_POSITION.unpack_from(data, HEADER.size + index * MOTION.size)
            positions.append(position if all(math.isfinite(axis) for axis in position) else None)
        return Motion(v[0:3], v[3:6], v[12:15], v[15:18], tuple(positions))
    if packet_id == 1:
        v = SESSION.unpack_from(data, HEADER.size)
        if v[15] > 21:
            raise PacketError("payload_value", f"invalid marshal zone count {v[15]}")
        zones = []
        for index in range(v[15]):
            start, flag = struct.unpack_from("<fb", data, HEADER.size + SESSION.size + index * 5)
            if not math.isfinite(start) or not 0 <= start <= 1 or flag not in (-1, 0, 1, 2, 3, 4):
                raise PacketError("payload_value", "invalid marshal zone")
            zones.append((round(start, 5), flag))
        safety_offset = HEADER.size + SESSION.size + 21 * 5
        safety, network_game, forecast_count = struct.unpack_from("<3B", data, safety_offset)
        if forecast_count > 56:
            raise PacketError("payload_value", f"invalid forecast count {forecast_count}")
        forecasts = []
        for index in range(forecast_count):
            f = FORECAST.unpack_from(data, safety_offset + 3 + index * FORECAST.size)
            forecasts.append(Forecast(f[0], f[1], f[2], f[3], f[5], f[7]))
        pit_offset = safety_offset + 3 + 56 * FORECAST.size + 2 + 3 * 4
        pit_ideal, pit_latest = struct.unpack_from("<2B", data, pit_offset)
        game_mode = data[pit_offset + 3 + 9]  # rejoin-position byte, then nine assist/display bytes.
        return Session(v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[8], safety, tuple(forecasts),
                   pit_ideal, pit_latest, tuple(zones), network_game, game_mode)
    if packet_id == 2:
        v = _entry(LAP, data, player)
        rivals = []
        for index in range(CAR_COUNT):
            if index == player:
                continue
            try:
                other = _entry(LAP, data, index)
            except PacketError:
                continue  # Rival data is advisory; never discard a valid player lap.
            if 1 <= other[7] <= CAR_COUNT and other[8] > 0:
                rivals.append(RivalLap(index, other[7], other[8], other[4], other[0], other[9], other[12],
                                       other[2], other[3], other[10], other[13], other[20]))
        return Lap(v[0], v[1], v[2], v[3], v[4], v[7], v[8], v[9], v[10], v[11], v[12], v[13], tuple(rivals), v[20])
    if packet_id == 3:
        raw = data[HEADER.size:HEADER.size + 4]
        code = raw.decode("ascii") if all(32 <= byte <= 126 for byte in raw) else raw.hex()
        return Event(code)
    if packet_id == 4:
        active = data[HEADER.size]
        if active > CAR_COUNT:
            raise PacketError("payload_value", f"invalid active car count {active}")
        if player >= active:
            return None
        v = _entry(PARTICIPANT, data, player, HEADER.size + 1)
        try:
            name = v[7].split(b"\0", 1)[0].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PacketError("payload_value", "participant name is not valid UTF-8") from exc
        names: list[str | None] = [None] * CAR_COUNT
        team_ids: list[int | None] = [None] * CAR_COUNT
        race_numbers: list[int | None] = [None] * CAR_COUNT
        for index in range(active):
            entry = _entry(PARTICIPANT, data, index, HEADER.size + 1)
            team_ids[index] = entry[3]
            race_numbers[index] = entry[5] if entry[5] > 0 else None
            try:
                names[index] = entry[7].split(b"\0", 1)[0].decode("utf-8") or None
            except UnicodeDecodeError:
                if index == player:
                    raise PacketError("payload_value", "participant name is not valid UTF-8") from None
        return Participant(v[1], v[3], v[5], name, v[8], tuple(names), tuple(team_ids), tuple(race_numbers))
    if packet_id == 5:
        start = HEADER.size + player * SETUP.size
        raw = data[start:start + SETUP.size]
        v = SETUP.unpack(raw)
        _finite(v)
        parameters = SetupParameters(*v[:16], v[16:20], v[20], v[21])
        return CarSetup(blake2s(raw, digest_size=8).hexdigest(), parameters)
    if packet_id == 6:
        v = _entry(TELEMETRY, data, player)
        return CarTelemetry(v[0], v[1], v[2], v[3], v[5], v[6], v[7], v[14:18], v[18:22], v[23:27])
    if packet_id == 7:
        v = _entry(STATUS, data, player)
        return CarStatus(v[5], v[6], v[7], v[13], v[14], v[15], v[17], v[18], v[19], v[20], v[21], v[11])
    if packet_id == 8:
        count = data[HEADER.size]
        if count > CAR_COUNT:
            raise PacketError("payload_value", f"invalid classification car count {count}")
        cars = []
        for index in range(count):
            v = _entry(CLASSIFICATION, data, index, HEADER.size + 1)
            if v[10] > 8:
                raise PacketError("payload_value", f"invalid classification stint count {v[10]}")
            stints = tuple((v[11 + n], v[19 + n], v[27 + n]) for n in range(v[10]))
            cars.append(ClassificationEntry(index, *v[:10], stints))
        return FinalClassification(tuple(cars))
    if packet_id == 10:
        v = _entry(DAMAGE, data, player)
        return CarDamage(v[0:4], v[4:8], v[8:12], v[12], v[13], v[14], v[20], v[21])
    if packet_id == 11:
        car, num_laps, num_stints, best_lap, _s1, _s2, _s3 = struct.unpack_from("<7B", data, HEADER.size)
        if car >= CAR_COUNT or num_laps > 100 or num_stints > 8:
            raise PacketError("payload_value", "invalid session history car, lap, or stint count")
        lap_offset = HEADER.size + 7
        laps = tuple(LapHistory(*HISTORY_LAP.unpack_from(data, lap_offset + n * HISTORY_LAP.size)) for n in range(num_laps))
        stint_offset = lap_offset + 100 * HISTORY_LAP.size
        stints = tuple(struct.unpack_from("<3B", data, stint_offset + n * 3) for n in range(num_stints))
        return SessionHistory(car, best_lap, laps, stints)
    return None
