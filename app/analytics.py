"""Rebuildable, offline race datasets from canonical F122CAP1 recordings.

All game fields retain packet provenance. Derived values are produced only by
queries over observations available at the requested capture-relative time.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from statistics import median
import tempfile

from .paths import data_root
from .telemetry.capture import capture_path, read_capture
from .telemetry.payloads import (CarDamage, CarSetup, CarStatus, CarTelemetry,
                                 Event, FinalClassification, Lap, Motion,
                                 Participant, Session, SessionHistory, decode_payload)
from .telemetry.protocol import PacketError, decode_packet
from .telemetry.state import RaceState


SCHEMA_VERSION = 1
ANALYTICS_DIRECTORY = data_root() / "data" / "analytics"
SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE segments (
    id INTEGER PRIMARY KEY, session_uid TEXT NOT NULL, epoch INTEGER NOT NULL,
    start_elapsed_s REAL NOT NULL, end_elapsed_s REAL NOT NULL,
    player_index INTEGER NOT NULL, session_type INTEGER, game_mode INTEGER,
    network_game INTEGER, track_id INTEGER, total_laps INTEGER,
    end_signal TEXT, UNIQUE(session_uid, epoch, start_elapsed_s)
);
CREATE TABLE observations (
    id INTEGER PRIMARY KEY, segment_id INTEGER NOT NULL REFERENCES segments(id),
    elapsed_s REAL NOT NULL, game_time_s REAL NOT NULL, packet_id INTEGER NOT NULL,
    frame INTEGER NOT NULL, car_index INTEGER, lap_number INTEGER,
    kind TEXT NOT NULL, source TEXT NOT NULL, quality TEXT NOT NULL,
    freshness_s REAL, data_json TEXT NOT NULL
);
CREATE INDEX observations_kind_time ON observations(kind, segment_id, elapsed_s);
CREATE INDEX observations_car_lap ON observations(segment_id, car_index, lap_number);
CREATE TABLE driver_laps (
    segment_id INTEGER NOT NULL REFERENCES segments(id), car_index INTEGER NOT NULL,
    lap_number INTEGER NOT NULL, first_seen_elapsed_s REAL NOT NULL,
    game_time_s REAL NOT NULL, packet_frame INTEGER NOT NULL,
    lap_ms INTEGER NOT NULL, sector1_ms INTEGER NOT NULL, sector2_ms INTEGER NOT NULL,
    sector3_ms INTEGER NOT NULL, validity_flags INTEGER NOT NULL,
    quality TEXT NOT NULL, PRIMARY KEY(segment_id, car_index, lap_number)
);
CREATE TABLE tyre_stints (
    segment_id INTEGER NOT NULL REFERENCES segments(id), car_index INTEGER NOT NULL,
    stint_index INTEGER NOT NULL, first_seen_elapsed_s REAL NOT NULL,
    end_lap INTEGER NOT NULL,
    actual_compound INTEGER NOT NULL, visual_compound INTEGER NOT NULL,
    PRIMARY KEY(segment_id, car_index, stint_index, first_seen_elapsed_s)
);
CREATE TABLE lap_boundaries (
    segment_id INTEGER NOT NULL REFERENCES segments(id), lap_number INTEGER NOT NULL,
    elapsed_s REAL NOT NULL, game_time_s REAL NOT NULL, lap_ms INTEGER,
    position INTEGER, pit_stops INTEGER, pit_status INTEGER, current_lap_invalid INTEGER,
    race_status TEXT NOT NULL, fuel_kg REAL, fuel_age_s REAL,
    wear_max_percent REAL, wear_age_s REAL, actual_compound INTEGER,
    tyre_age_laps INTEGER, setup_fingerprint TEXT, setup_age_s REAL,
    quality TEXT NOT NULL, PRIMARY KEY(segment_id, lap_number)
);
CREATE TABLE final_results (
    segment_id INTEGER NOT NULL REFERENCES segments(id), car_index INTEGER NOT NULL,
    observed_elapsed_s REAL NOT NULL, position INTEGER NOT NULL, laps INTEGER NOT NULL,
    grid_position INTEGER NOT NULL, points INTEGER NOT NULL, pit_stops INTEGER NOT NULL,
    result_status INTEGER NOT NULL, best_lap_ms INTEGER NOT NULL,
    total_race_s REAL NOT NULL, penalties_s INTEGER NOT NULL,
    penalty_count INTEGER NOT NULL, tyre_stints_json TEXT NOT NULL,
    PRIMARY KEY(segment_id, car_index)
);
CREATE TABLE gaps (
    segment_id INTEGER NOT NULL REFERENCES segments(id), from_elapsed_s REAL NOT NULL,
    to_elapsed_s REAL NOT NULL, game_time_gap_s REAL NOT NULL,
    lap_jump INTEGER NOT NULL, quality TEXT NOT NULL,
    PRIMARY KEY(segment_id, from_elapsed_s, to_elapsed_s)
);
CREATE TABLE rejected_packets (
    packet_ordinal INTEGER PRIMARY KEY, elapsed_s REAL NOT NULL,
    claimed_packet_id INTEGER, reason TEXT NOT NULL
);
CREATE TABLE packet_counts (
    packet_id INTEGER PRIMARY KEY, header_valid INTEGER NOT NULL,
    payload_valid INTEGER NOT NULL, payload_invalid INTEGER NOT NULL
);
"""


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def _capture_sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fresh(state: RaceState, packet_id: int, now: float):
    observation = state.get_fresh(packet_id, now)
    return (observation.value, round(now - observation.observed_at, 4)) if observation else (None, None)


def _extract_into(db: sqlite3.Connection, name: str, digest: str) -> dict:
    db.executescript(SCHEMA)
    db.execute("INSERT INTO metadata VALUES (?,?)", ("schema_version", str(SCHEMA_VERSION)))
    db.execute("INSERT INTO metadata VALUES (?,?)", ("capture_name", name))
    db.execute("INSERT INTO metadata VALUES (?,?)", ("capture_sha256", digest))
    state = RaceState()
    counts: Counter[int] = Counter()
    decoded: Counter[int] = Counter()
    invalid: Counter[int] = Counter()
    last_sample: dict[tuple[int, str, int | None], float] = {}
    last_history: dict[tuple[int, int], SessionHistory] = {}
    last_stint: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    last_setup: dict[int, str] = {}
    last_weather: dict[int, str] = {}
    last_lap_number: dict[int, int] = {}
    last_pit: dict[int, tuple[int, int]] = {}
    last_classification: dict[int, FinalClassification] = {}
    seen_timeline_events: set[tuple] = set()
    segment_key: tuple[int, int, int, int] | None = None
    segment_id: int | None = None
    segment_end = 0.0
    total = ignored = 0

    def add(kind: str, elapsed: float, header, value: object, *, car: int | None = None,
            lap: int | None = None, quality: str = "game_reported",
            freshness: float | None = 0.0) -> None:
        assert segment_id is not None
        db.execute("""INSERT INTO observations
            (segment_id,elapsed_s,game_time_s,packet_id,frame,car_index,lap_number,
             kind,source,quality,freshness_s,data_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (segment_id, elapsed, header.session_time, header.packet_id,
             header.frame_identifier, car, lap, kind, "F1 22 UDP 2022", quality,
             freshness, _json(value)))

    def sampled(kind: str, elapsed: float, header, value: object, period: float,
                *, car: int | None = None, lap: int | None = None,
                quality: str = "game_reported", freshness: float | None = 0.0) -> None:
        assert segment_id is not None
        key = (segment_id, kind, car)
        if elapsed - last_sample.get(key, -float("inf")) < period:
            return
        last_sample[key] = elapsed
        add(kind, elapsed, header, value, car=car, lap=lap,
            quality=quality, freshness=freshness)

    for ordinal, (elapsed, raw) in enumerate(read_capture(name), start=1):
        total += 1
        try:
            header = decode_packet(raw)
        except PacketError as exc:
            db.execute("INSERT INTO rejected_packets VALUES (?,?,?,?)",
                       (ordinal, elapsed, raw[5] if len(raw) > 5 else None, exc.reason))
            continue
        counts[header.packet_id] += 1
        try:
            value = decode_payload(raw, header)
            state.apply(raw, header, elapsed)
        except PacketError as exc:
            invalid[header.packet_id] += 1
            db.execute("INSERT INTO rejected_packets VALUES (?,?,?,?)",
                       (ordinal, elapsed, header.packet_id, f"payload_{exc.reason}"))
            continue
        decoded[header.packet_id] += 1
        if not header.session_uid or state.session_uid != header.session_uid:
            ignored += 1  # Menu or unrelated session: still in canonical raw capture.
            continue
        accepted = (state.histories.get(value.car_index) if isinstance(value, SessionHistory)
                    else state.observations.get(header.packet_id))
        if value is None or accepted is None or accepted.header is not header:
            # A delayed/reordered packet remains in the capture but must not
            # become a current-state training observation.
            continue
        key = (header.session_uid, state.timeline.epoch,
               header.player_car_index, state.resets)
        if key != segment_key:
            if segment_id is not None:
                db.execute("UPDATE segments SET end_elapsed_s=? WHERE id=?", (segment_end, segment_id))
            segment_key = key
            segment_id = db.execute("""INSERT INTO segments
                (session_uid,epoch,start_elapsed_s,end_elapsed_s,player_index)
                VALUES (?,?,?,?,?)""",
                (str(header.session_uid), state.timeline.epoch, elapsed, elapsed,
                 header.player_car_index)).lastrowid
        segment_end = elapsed

        # The timeline is the shared live/replay authority for status and gaps.
        for event in state.timeline.events:
            if event["observed_at"] != elapsed or event["source_packet_id"] != header.packet_id:
                continue
            event_key = (segment_id, event["kind"], event["observed_at"],
                         event.get("from_observed_at"), event.get("source"))
            if event_key in seen_timeline_events:
                continue
            seen_timeline_events.add(event_key)
            if event["kind"] == "unobserved_interval":
                db.execute("""INSERT OR IGNORE INTO gaps VALUES (?,?,?,?,?,?)""",
                           (segment_id, event["from_observed_at"], elapsed,
                            event["game_time_gap_s"], event["lap_jump"], "unobserved"))
            elif event["kind"] in ("sc_active", "vsc_active", "restart", "green_observed",
                                    "status_unknown", "pit_entry", "pit_exit", "session_ended",
                                    "flashback", "clock_rewind"):
                add("race_transition", elapsed, header, event,
                    quality="game_reported" if event["kind"] not in ("clock_rewind",) else "estimated")

        if isinstance(value, Session):
            db.execute("""UPDATE segments SET session_type=?,game_mode=?,network_game=?,
                track_id=?,total_laps=? WHERE id=?""",
                (value.session_type, value.game_mode, value.network_game,
                 value.track_id, value.total_laps, segment_id))
            weather = {"weather": value.weather, "track_c": value.track_c,
                       "air_c": value.air_c, "safety_car": value.safety_car,
                       "marshal_zones": value.marshal_zones,
                       "forecasts": [asdict(item) for item in value.forecasts],
                       "pit_ideal_lap": value.pit_ideal_lap,
                       "pit_latest_lap": value.pit_latest_lap}
            signature = _json(weather)
            if last_weather.get(segment_id) != signature:
                add("weather_context", elapsed, header, weather)
                last_weather[segment_id] = signature
        elif isinstance(value, Participant):
            sampled("participants", elapsed, header,
                    {"names": value.car_names, "teams": value.car_team_ids,
                     "numbers": value.car_race_numbers}, 5.0)
        elif isinstance(value, Lap):
            cars = [(header.player_car_index, value.position, value.lap_number,
                     value.lap_distance_m, value.last_lap_ms, value.pit_status,
                     value.pit_stops, value.result_status)]
            cars.extend((r.car_index, r.position, r.lap_number, r.lap_distance_m,
                         r.last_lap_ms, r.pit_status, r.pit_stops, r.result_status)
                        for r in value.rivals)
            for car, position, lap, distance, last_ms, pit, stops, result in cars:
                sampled("driver_progress", elapsed, header,
                        {"position": position, "lap_distance_m": distance,
                         "last_lap_ms": last_ms, "pit_status": pit,
                         "pit_stops": stops, "result_status": result},
                        1.0, car=car, lap=lap)
            previous = last_lap_number.get(segment_id)
            if value.lap_number != previous:
                last_lap_number[segment_id] = value.lap_number
                if value.lap_number > 1:
                    status, status_age = _fresh(state, 7, elapsed)
                    damage, damage_age = _fresh(state, 10, elapsed)
                    setup, setup_age = _fresh(state, 5, elapsed)
                    quality = ("valid" if isinstance(status, CarStatus)
                               and isinstance(damage, CarDamage) else "missing")
                    db.execute("""INSERT OR IGNORE INTO lap_boundaries VALUES
                        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (segment_id, value.lap_number - 1, elapsed, header.session_time,
                         value.last_lap_ms or None, value.position, value.pit_stops,
                         value.pit_status, value.invalid, state.timeline.status(elapsed),
                         status.fuel_kg if isinstance(status, CarStatus) else None,
                         status_age, max(damage.tyre_wear_percent)
                         if isinstance(damage, CarDamage) else None, damage_age,
                         status.actual_compound if isinstance(status, CarStatus) else None,
                         status.tyre_age_laps if isinstance(status, CarStatus) else None,
                         setup.fingerprint if isinstance(setup, CarSetup) else None,
                         setup_age, quality))
            previous_pit = last_pit.get(segment_id)
            current_pit = (value.pit_status, value.pit_stops)
            if previous_pit is not None and value.pit_stops > previous_pit[1]:
                add("pit_stop_count_change", elapsed, header,
                    {"from": previous_pit[1], "to": value.pit_stops,
                     "race_status": state.timeline.status(elapsed)}, lap=value.lap_number)
            last_pit[segment_id] = current_pit
        elif isinstance(value, Motion):
            lap_observation = state.get_fresh(2, elapsed)
            lap = lap_observation.value if lap_observation else None
            age = round(elapsed - lap_observation.observed_at, 4) if lap_observation else None
            sampled("motion_trace", elapsed, header,
                    {"position_m": value.position_m, "velocity_m_per_s": value.velocity_m_per_s,
                     "g_force": value.g_force, "orientation_rad": value.orientation_rad,
                     "lap_distance_m": lap.lap_distance_m if isinstance(lap, Lap) else None,
                     "lap_source_age_s": age,
                     "lap_source_frame": lap_observation.header.frame_identifier
                     if lap_observation else None}, 0.2,
                    car=header.player_car_index,
                    lap=lap.lap_number if isinstance(lap, Lap) else None,
                    quality="game_reported" if lap is not None else "missing", freshness=age)
        elif isinstance(value, CarTelemetry):
            lap_observation = state.get_fresh(2, elapsed)
            lap = lap_observation.value if lap_observation else None
            age = round(elapsed - lap_observation.observed_at, 4) if lap_observation else None
            sampled("driving_trace", elapsed, header,
                    {"speed_kph": value.speed_kph, "throttle": value.throttle,
                     "brake": value.brake, "steer": value.steer,
                     "gear": value.gear, "rpm": value.engine_rpm,
                     "surface_c": value.surface_c, "inner_c": value.inner_c,
                     "pressure_psi": value.pressure_psi,
                     "lap_distance_m": lap.lap_distance_m if isinstance(lap, Lap) else None,
                     "lap_source_age_s": age,
                     "lap_source_frame": lap_observation.header.frame_identifier
                     if lap_observation else None}, 0.2, car=header.player_car_index,
                    lap=lap.lap_number if isinstance(lap, Lap) else None,
                    quality="game_reported" if lap is not None else "missing", freshness=age)
        elif isinstance(value, CarStatus):
            sampled("fuel_compound_ers", elapsed, header, asdict(value), 1.0,
                    car=header.player_car_index)
        elif isinstance(value, CarDamage):
            sampled("tyre_wear_damage", elapsed, header, asdict(value), 1.0,
                    car=header.player_car_index)
        elif isinstance(value, CarSetup):
            if last_setup.get(segment_id) != value.fingerprint:
                add("player_setup", elapsed, header, asdict(value), car=header.player_car_index)
                last_setup[segment_id] = value.fingerprint
        elif isinstance(value, SessionHistory):
            history_key = (segment_id, value.car_index)
            if last_history.get(history_key) != value:
                last_history[history_key] = value
                for lap_number, lap in enumerate(value.laps, start=1):
                    if lap.lap_ms <= 0 or min(lap.sector1_ms, lap.sector2_ms, lap.sector3_ms) <= 0:
                        continue  # Current partial lap cannot be a training target.
                    db.execute("""INSERT OR IGNORE INTO driver_laps VALUES
                        (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (segment_id, value.car_index, lap_number, elapsed,
                         header.session_time, header.frame_identifier, lap.lap_ms,
                         lap.sector1_ms, lap.sector2_ms, lap.sector3_ms,
                         lap.validity_flags, "game_reported"))
                for index, (end_lap, actual, visual) in enumerate(value.tyre_stints):
                    stint_key = (segment_id, value.car_index, index)
                    stint_value = (end_lap, actual, visual)
                    if last_stint.get(stint_key) != stint_value:
                        db.execute("""INSERT OR IGNORE INTO tyre_stints VALUES (?,?,?,?,?,?,?)""",
                                   (segment_id, value.car_index, index, elapsed,
                                    end_lap, actual, visual))
                        last_stint[stint_key] = stint_value
        elif isinstance(value, Event):
            if value.code not in ("BUTN", "SPTP"):
                add("race_event", elapsed, header, {"code": value.code},
                    lap=state.timeline.last_lap)
            if value.code == "SEND":
                db.execute("UPDATE segments SET end_signal='SEND' WHERE id=?", (segment_id,))
        elif isinstance(value, FinalClassification):
            if last_classification.get(segment_id) != value:
                last_classification[segment_id] = value
                for car in value.cars:
                    db.execute("""INSERT OR IGNORE INTO final_results VALUES
                        (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (segment_id, car.car_index, elapsed, car.position, car.laps,
                         car.grid_position, car.points, car.pit_stops,
                         car.result_status, car.best_lap_ms, car.total_race_s,
                         car.penalties_s, car.penalty_count, _json(car.tyre_stints)))
                db.execute("UPDATE segments SET end_signal='classification' WHERE id=?",
                           (segment_id,))
    if segment_id is not None:
        db.execute("UPDATE segments SET end_elapsed_s=? WHERE id=?", (segment_end, segment_id))
    for packet_id in sorted(counts):
        db.execute("INSERT INTO packet_counts VALUES (?,?,?,?)",
                   (packet_id, counts[packet_id], decoded[packet_id], invalid[packet_id]))
    db.execute("INSERT INTO metadata VALUES (?,?)", ("datagrams", str(total)))
    db.execute("INSERT INTO metadata VALUES (?,?)", ("ignored_menu_or_other_uid", str(ignored)))
    db.commit()
    return {"capture": name, "sha256": digest, "datagrams": total,
            "segments": db.execute("SELECT COUNT(*) FROM segments").fetchone()[0],
            "observations": db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "rejected": db.execute("SELECT COUNT(*) FROM rejected_packets").fetchone()[0]}


def extract_capture(name: str, *, replace: bool = False) -> dict:
    """Create an independent SQLite dataset; never mutate the input capture."""
    source = capture_path(name)
    if not source.is_file():
        raise FileNotFoundError(source)
    if ANALYTICS_DIRECTORY.is_symlink() or ANALYTICS_DIRECTORY.parent.is_symlink():
        raise ValueError("analytics directory must not be a symbolic link")
    ANALYTICS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    destination = ANALYTICS_DIRECTORY / (Path(name).stem + ".sqlite3")
    if destination.is_symlink():
        raise ValueError("analytics directory and dataset must not be symbolic links")
    if destination.exists() and not replace:
        raise FileExistsError(f"analytics dataset already exists: {destination}; use --replace")
    descriptor, temporary = tempfile.mkstemp(prefix=Path(name).stem + "-", suffix=".tmp",
                                            dir=ANALYTICS_DIRECTORY)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        source_digest = _capture_sha(source)
        with closing(sqlite3.connect(temporary_path)) as db:
            result = _extract_into(db, name, source_digest)
        if _capture_sha(source) != source_digest:
            raise RuntimeError("capture changed during extraction; retry after recording closes")
        if replace:
            os.replace(temporary_path, destination)
        else:
            os.link(temporary_path, destination)  # Atomic no-overwrite publication.
        result["dataset"] = str(destination)
        return result
    finally:
        temporary_path.unlink(missing_ok=True)


def summarize_dataset(path: Path, *, as_of_elapsed_s: float | None = None) -> dict:
    """Only use rows already observed at the requested capture-relative time."""
    if as_of_elapsed_s is not None and as_of_elapsed_s < 0:
        raise ValueError("as-of time must be nonnegative")
    with closing(sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        cutoff = float("inf") if as_of_elapsed_s is None else as_of_elapsed_s
        segments = [dict(row) for row in db.execute(
            "SELECT * FROM segments WHERE start_elapsed_s<=? ORDER BY id", (cutoff,))]
        for segment in segments:
            if segment["end_elapsed_s"] > cutoff:
                segment["end_elapsed_s"] = cutoff
                segment["end_signal"] = None
            if not db.execute("""SELECT 1 FROM observations WHERE segment_id=?
                    AND kind='weather_context' AND elapsed_s<=? LIMIT 1""",
                    (segment["id"], cutoff)).fetchone():
                for field in ("session_type", "game_mode", "network_game",
                              "track_id", "total_laps"):
                    segment[field] = None
        boundaries = defaultdict(list)
        for row in db.execute("SELECT * FROM lap_boundaries WHERE elapsed_s<=? ORDER BY segment_id,lap_number", (cutoff,)):
            boundaries[row["segment_id"]].append(dict(row))
        lap_validity = {(row["segment_id"], row["lap_number"]): row["validity_flags"]
                        for row in db.execute("""SELECT segment_id,lap_number,validity_flags
                            FROM driver_laps JOIN segments ON segments.id=driver_laps.segment_id
                            WHERE car_index=segments.player_index AND first_seen_elapsed_s<=?""", (cutoff,))}
        gaps = defaultdict(list)
        for row in db.execute("SELECT * FROM gaps WHERE to_elapsed_s<=?", (cutoff,)):
            gaps[row["segment_id"]].append((row["from_elapsed_s"], row["to_elapsed_s"]))
        fuel, wear, degradation, pit_loss, neutralized_behavior = [], [], [], [], []
        for segment in segments:
            if segment["session_type"] is None:
                continue  # A terminal clock-reset epoch has no verified race context.
            rows = boundaries[segment["id"]]
            pit_affected = {row["lap_number"] for index, row in enumerate(rows)
                            if row["pit_status"] != 0 or (index > 0 and row["pit_stops"] is not None
                            and row["pit_stops"] > (rows[index - 1]["pit_stops"] or 0))}
            for earlier, later in zip(rows, rows[1:]):
                safety_codes = {item[0] for item in db.execute("""SELECT DISTINCT
                    json_extract(data_json,'$.safety_car') FROM observations
                    WHERE segment_id=? AND kind='weather_context'
                    AND elapsed_s>? AND elapsed_s<=?""",
                    (segment["id"], earlier["elapsed_s"], later["elapsed_s"]))}
                observed_phases = {phase for phase in (earlier["race_status"],
                                   later["race_status"]) if phase in ("SC", "VSC")}
                observed_phases.update({1: "SC", 2: "VSC"}[code]
                                       for code in safety_codes if code in (1, 2))
                interval_has_gap = any(start < later["elapsed_s"] and end > earlier["elapsed_s"]
                                       for start, end in gaps[segment["id"]])
                if (observed_phases and not interval_has_gap
                        and later["lap_number"] == earlier["lap_number"] + 1
                        and later["lap_number"] not in pit_affected
                        and earlier["lap_number"] not in pit_affected
                        and lap_validity.get((segment["id"], later["lap_number"]), 0) & 1):
                    phase = next(iter(observed_phases)) if len(observed_phases) == 1 else "MIXED"
                    neutralized_behavior.append({"segment_id": segment["id"],
                        "lap_number": later["lap_number"], "phase_observed": phase,
                        "available_at_s": later["elapsed_s"], "lap_ms": later["lap_ms"],
                        "fuel_used_kg": (round(earlier["fuel_kg"] - later["fuel_kg"], 3)
                            if earlier["fuel_kg"] is not None and later["fuel_kg"] is not None
                            and earlier["fuel_kg"] >= later["fuel_kg"] else None),
                        "quality": "estimated"})
                comparable = (later["lap_number"] == earlier["lap_number"] + 1
                              and earlier["lap_number"] not in pit_affected
                              and later["lap_number"] not in pit_affected
                              and earlier["race_status"] == later["race_status"] == "GREEN"
                              and earlier["pit_stops"] == later["pit_stops"]
                              and earlier["pit_status"] == later["pit_status"] == 0
                              and all(lap_validity.get((segment["id"], item["lap_number"]), 0) & 1
                                      for item in (earlier, later))
                              and earlier["actual_compound"] == later["actual_compound"]
                              and not interval_has_gap
                              and not safety_codes.intersection((1, 2, 3)))
                if not comparable:
                    continue
                key = {"segment_id": segment["id"], "lap_number": later["lap_number"],
                       "available_at_s": later["elapsed_s"], "compound": later["actual_compound"],
                       "stint_index": later["pit_stops"], "quality": "estimated"}
                if earlier["fuel_kg"] is not None and later["fuel_kg"] is not None:
                    delta = earlier["fuel_kg"] - later["fuel_kg"]
                    if 0 <= delta <= 15:
                        fuel.append({**key, "kg_per_lap": round(delta, 3)})
                if earlier["wear_max_percent"] is not None and later["wear_max_percent"] is not None:
                    delta = later["wear_max_percent"] - earlier["wear_max_percent"]
                    if 0 <= delta <= 100:
                        wear.append({**key, "percentage_points_per_lap": round(delta, 3)})
                if earlier["lap_ms"] and later["lap_ms"]:
                    degradation.append({**key, "lap_time_delta_ms": later["lap_ms"] - earlier["lap_ms"]})
            for index, row in enumerate(rows):
                if index < 2 or row["lap_ms"] is None or row["pit_stops"] is None:
                    continue
                if row["pit_stops"] <= (rows[index - 1]["pit_stops"] or 0):
                    continue
                prior = [item["lap_ms"] for item in rows[max(0, index - 3):index]
                         if item["lap_ms"] and item["race_status"] == "GREEN"
                         and item["pit_status"] == 0]
                if len(prior) >= 2:
                    pit_loss.append({"segment_id": segment["id"], "lap_number": row["lap_number"],
                                     "available_at_s": row["elapsed_s"], "quality": "estimated",
                                     "lap_excess_ms": round(row["lap_ms"] - median(prior))})

        player_laps = defaultdict(list)
        rival_laps = defaultdict(list)
        backtest = []
        for segment in segments:
            if segment["session_type"] is None:
                continue
            for row in db.execute("""SELECT * FROM driver_laps WHERE segment_id=? AND
                    first_seen_elapsed_s<=? AND (validity_flags & 1)=1
                    ORDER BY first_seen_elapsed_s,lap_number""", (segment["id"], cutoff)):
                target = player_laps if row["car_index"] == segment["player_index"] else rival_laps
                target[(segment["id"], row["car_index"])].append(dict(row))
            history = player_laps.get((segment["id"], segment["player_index"]), [])
            for index, row in enumerate(history):
                prior = [old["lap_ms"] for old in history[:index]
                         if old["first_seen_elapsed_s"] < row["first_seen_elapsed_s"]]
                if len(prior) < 3:
                    continue
                prediction = median(prior[-3:])
                prediction_as_of = max(old["first_seen_elapsed_s"] for old in history[:index]
                                       if old["first_seen_elapsed_s"] < row["first_seen_elapsed_s"])
                backtest.append({"segment_id": segment["id"], "target_lap": row["lap_number"],
                                 "prediction_as_of_s": prediction_as_of,
                                 "actual_available_at_s": row["first_seen_elapsed_s"],
                                 "predicted_ms": round(prediction), "actual_ms": row["lap_ms"],
                                 "absolute_error_ms": round(abs(prediction - row["lap_ms"])),
                                 "quality": "estimated"})
        rival_pace = []
        for segment in segments:
            if segment["session_type"] is None:
                continue
            player = player_laps.get((segment["id"], segment["player_index"]), [])
            own = {row["lap_number"]: row["lap_ms"] for row in player}
            for (segment_id, car), rows in rival_laps.items():
                if segment_id != segment["id"]:
                    continue
                deltas = [row["lap_ms"] - own[row["lap_number"]] for row in rows
                          if row["lap_number"] in own]
                if len(deltas) >= 2:
                    rival_pace.append({"segment_id": segment_id, "car_index": car,
                                       "common_laps": len(deltas),
                                       "median_rival_minus_player_ms": round(median(deltas)),
                                       "quality": "estimated"})
        status_counts = {row["status"]: row["count"] for row in db.execute("""SELECT
            json_extract(data_json,'$.safety_car') AS status, COUNT(*) AS count
            FROM observations WHERE kind='weather_context' AND elapsed_s<=?
            GROUP BY status""", (cutoff,))}
        setup_groups = defaultdict(list)
        for segment in segments:
            if segment["session_type"] not in (1, 2, 3, 4):
                continue
            for row in boundaries[segment["id"]]:
                if (row["setup_fingerprint"] and row["lap_ms"]
                        and lap_validity.get((segment["id"], row["lap_number"]), 0) & 1):
                    setup_groups[(segment["track_id"], row["setup_fingerprint"])].append(row["lap_ms"])
        practice_setups = [{"track_id": track, "fingerprint": fingerprint,
                            "laps": len(times), "best_lap_ms": min(times),
                            "median_lap_ms": round(median(times)), "quality": "game_reported"}
                           for (track, fingerprint), times in setup_groups.items()]
        corner_bins = defaultdict(dict)
        for row in db.execute("""SELECT segment_id,lap_number,data_json FROM observations
                WHERE kind='driving_trace' AND elapsed_s<=? AND lap_number IS NOT NULL""", (cutoff,)):
            data = json.loads(row["data_json"])
            distance = data.get("lap_distance_m")
            if distance is None or data["brake"] < 0.7 or distance < 0:
                continue
            key = (row["segment_id"], int(distance // 100))
            corner_bins[key].setdefault(row["lap_number"], []).append(data["speed_kph"])
        corner_comparisons = [{"segment_id": segment, "distance_bin_m": bin_number * 100,
                               "laps": len(laps), "median_braking_speed_kph": round(median(
                                   [median(speeds) for speeds in laps.values()])),
                               "quality": "estimated"}
                              for (segment, bin_number), laps in corner_bins.items() if len(laps) >= 2]
        final_count = db.execute("SELECT COUNT(*) FROM final_results WHERE observed_elapsed_s<=?", (cutoff,)).fetchone()[0]
        return {"as_of_elapsed_s": as_of_elapsed_s, "sessions": segments,
                "fuel_burn": fuel, "tyre_wear": wear,
                "lap_time_change": degradation, "pit_lap_excess": pit_loss,
                "rival_relative_pace": rival_pace,
                "neutralized_lap_behavior": neutralized_behavior,
                "corner_braking_bins": corner_comparisons,
                "safety_observation_counts": status_counts,
                "practice_setups": practice_setups,
                "final_result_rows": final_count,
                "backtest": {"method": "median of three earlier observed valid player laps",
                             "samples": len(backtest),
                             "median_absolute_error_ms": round(median(
                                 row["absolute_error_ms"] for row in backtest)) if backtest else None,
                             "predictions": backtest}}
