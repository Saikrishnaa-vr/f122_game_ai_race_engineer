"""Local structured history, debriefs and exports. No raw UDP or audio is stored."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from statistics import median
from queue import Empty, Full, Queue
from threading import Event as ThreadEvent, Thread

from .telemetry.payloads import CarDamage, CarSetup, CarStatus, Event, Lap, Session, SessionHistory
from .telemetry.state import RaceState
from .paths import data_root
from .logging_setup import LOGGER


DEFAULT_DB = data_root() / "data" / "races.sqlite3"
# These are per-button and all-car speed-trap notifications, not useful
# session-level race incidents. They can arrive thousands of times per race.
NOISY_EVENT_CODES = frozenset({"BUTN", "SPTP"})


class RaceHistory:
    def __init__(self, path: Path = DEFAULT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                uid TEXT PRIMARY KEY, started_utc TEXT NOT NULL, track_id INTEGER,
                session_type INTEGER, total_laps INTEGER, final_position INTEGER
            );
            CREATE TABLE IF NOT EXISTS laps (
                uid TEXT NOT NULL, lap_number INTEGER NOT NULL, last_lap_ms INTEGER,
                position INTEGER, fuel_kg REAL, tyre_wear_max REAL, compound INTEGER,
                ers_mj REAL, pit_stops INTEGER, weather INTEGER, recorded_utc TEXT NOT NULL,
                PRIMARY KEY(uid, lap_number), FOREIGN KEY(uid) REFERENCES sessions(uid)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, uid TEXT NOT NULL, lap_number INTEGER,
                event_code TEXT NOT NULL, recorded_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS advice (
                id INTEGER PRIMARY KEY, uid TEXT NOT NULL, lap_number INTEGER,
                message TEXT NOT NULL, recorded_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sectors (
                uid TEXT NOT NULL, lap_number INTEGER NOT NULL, lap_ms INTEGER,
                sector1_ms INTEGER, sector2_ms INTEGER, sector3_ms INTEGER,
                validity_flags INTEGER, PRIMARY KEY(uid, lap_number)
            );
            CREATE TABLE IF NOT EXISTS setup_labels (
                track_id INTEGER NOT NULL, fingerprint TEXT NOT NULL, label TEXT NOT NULL,
                PRIMARY KEY(track_id, fingerprint)
            );
        """)
        if "setup_fingerprint" not in {row[1] for row in self.db.execute("PRAGMA table_info(laps)")}:
            self.db.execute("ALTER TABLE laps ADD COLUMN setup_fingerprint TEXT")
        self.db.commit()
        self.last_lap_key: tuple[str, int] | None = None
        self.last_event_frame: tuple[str, int] | None = None
        self.last_history_frame: tuple[str, int] | None = None
        self.last_setup: dict[str, tuple[int, str]] = {}

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "RaceHistory":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _utc() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def observe(self, state: RaceState, now: float) -> None:
        if not state.session_uid:
            return
        uid = str(state.session_uid)
        session_obs, lap_obs = state.get_fresh(1, now), state.get_fresh(2, now)
        session = session_obs.value if session_obs and isinstance(session_obs.value, Session) else None
        lap = lap_obs.value if lap_obs and isinstance(lap_obs.value, Lap) else None
        setup_obs = state.get_fresh(5, now)
        setup = setup_obs.value if setup_obs and isinstance(setup_obs.value, CarSetup) else None
        self.db.execute("INSERT OR IGNORE INTO sessions(uid,started_utc) VALUES (?,?)", (uid, self._utc()))
        if session:
            self.db.execute("UPDATE sessions SET track_id=?,session_type=?,total_laps=? WHERE uid=?",
                            (session.track_id, session.session_type, session.total_laps, uid))
        if lap:
            self.db.execute("UPDATE sessions SET final_position=? WHERE uid=?", (lap.position, uid))
            key = (uid, lap.lap_number)
            if lap.lap_number > 1 and key != self.last_lap_key:
                previous_setup = self.last_setup.get(uid)
                fingerprint = (previous_setup[1] if previous_setup and previous_setup[0] == lap.lap_number - 1
                               else None)
                status_obs, damage_obs = state.get_fresh(7, now), state.get_fresh(10, now)
                status = status_obs.value if status_obs and isinstance(status_obs.value, CarStatus) else None
                damage = damage_obs.value if damage_obs and isinstance(damage_obs.value, CarDamage) else None
                self.db.execute("""INSERT OR REPLACE INTO laps
                    (uid,lap_number,last_lap_ms,position,fuel_kg,tyre_wear_max,compound,ers_mj,pit_stops,weather,recorded_utc,setup_fingerprint)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (uid, lap.lap_number - 1, lap.last_lap_ms, lap.position,
                     status.fuel_kg if status else None,
                     max(damage.tyre_wear_percent) if damage else None,
                     status.actual_compound if status else None,
                     status.ers_joules / 1_000_000 if status else None,
                     lap.pit_stops, session.weather if session else None, self._utc(), fingerprint))
                self.last_lap_key = key
            if setup:
                self.last_setup[uid] = (lap.lap_number, setup.fingerprint)
        event_obs = state.get_fresh(3, now)
        if (event_obs and isinstance(event_obs.value, Event)
                and event_obs.value.code not in NOISY_EVENT_CODES):
            frame = (uid, event_obs.header.frame_identifier)
            if frame != self.last_event_frame:
                self.db.execute("INSERT INTO events(uid,lap_number,event_code,recorded_utc) VALUES (?,?,?,?)",
                                (uid, lap.lap_number if lap else None, event_obs.value.code, self._utc()))
                self.last_event_frame = frame
        history_obs = state.get_fresh(11, now)
        if history_obs and isinstance(history_obs.value, SessionHistory):
            frame = (uid, history_obs.header.frame_identifier)
            if frame != self.last_history_frame:
                self.db.executemany("""INSERT OR REPLACE INTO sectors
                    (uid,lap_number,lap_ms,sector1_ms,sector2_ms,sector3_ms,validity_flags)
                    VALUES (?,?,?,?,?,?,?)""",
                    [(uid, number, entry.lap_ms, entry.sector1_ms, entry.sector2_ms,
                      entry.sector3_ms, entry.validity_flags)
                     for number, entry in enumerate(history_obs.value.laps, start=1)])
                self.last_history_frame = frame
        self.db.commit()

    def record_advice(self, uid: int | None, lap: int | None, message: str) -> None:
        if not uid:
            return
        self.db.execute("INSERT INTO advice(uid,lap_number,message,recorded_utc) VALUES (?,?,?,?)",
                        (str(uid), lap, message[:1000], self._utc()))
        self.db.commit()

    def sessions(self) -> list[dict]:
        self.db.row_factory = sqlite3.Row
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM sessions WHERE uid <> '0' ORDER BY started_utc DESC")]

    def label_setup(self, track_id: int, fingerprint: str, label: str) -> None:
        if not -1 <= track_id <= 255 or len(fingerprint) != 16 or any(c not in "0123456789abcdef" for c in fingerprint):
            raise ValueError("invalid track or setup fingerprint")
        label = label.strip()
        if not 1 <= len(label) <= 48 or any(ord(c) < 32 for c in label):
            raise ValueError("setup label must contain 1-48 printable characters")
        self.db.execute("INSERT INTO setup_labels(track_id,fingerprint,label) VALUES (?,?,?) "
                        "ON CONFLICT(track_id,fingerprint) DO UPDATE SET label=excluded.label",
                        (track_id, fingerprint, label))
        self.db.commit()

    def practice_summary(self, track_id: int) -> list[dict]:
        if not -1 <= track_id <= 255:
            raise ValueError("invalid track")
        self.db.row_factory = sqlite3.Row
        rows = self.db.execute("""SELECT l.setup_fingerprint, l.last_lap_ms, s.session_type,
                                      sl.label FROM laps l JOIN sessions s ON s.uid=l.uid
                           LEFT JOIN setup_labels sl ON sl.track_id=s.track_id
                                AND sl.fingerprint=l.setup_fingerprint
                           WHERE s.track_id=? AND s.session_type IN (1,2,3,4)
                             AND l.setup_fingerprint IS NOT NULL AND l.last_lap_ms BETWEEN 30000 AND 300000
                           ORDER BY l.recorded_utc""", (track_id,)).fetchall()
        groups: dict[str, dict] = {}
        for row in rows:
            fingerprint = row["setup_fingerprint"]
            group = groups.setdefault(fingerprint, {"fingerprint": fingerprint,
                                                      "label": row["label"] or f"Setup {fingerprint[:6]}",
                                                      "lap_times_ms": [], "session_types": set()})
            group["lap_times_ms"].append(row["last_lap_ms"])
            group["session_types"].add(row["session_type"])
        return sorted(({"fingerprint": group["fingerprint"], "label": group["label"],
                        "laps": len(group["lap_times_ms"]),
                        "best_lap_ms": min(group["lap_times_ms"]),
                        "median_lap_ms": round(median(group["lap_times_ms"])),
                        "session_types": sorted(group["session_types"]),
                        "note": "Observed practice laps; fuel, traffic and validity may differ."}
                       for group in groups.values()), key=lambda group: group["best_lap_ms"])

    def debrief(self, uid: str) -> dict:
        self.db.row_factory = sqlite3.Row
        session = self.db.execute("SELECT * FROM sessions WHERE uid=?", (uid,)).fetchone()
        if session is None:
            raise ValueError("session not found")
        laps = [dict(row) for row in self.db.execute("SELECT * FROM laps WHERE uid=? ORDER BY lap_number", (uid,))]
        events = [dict(row) for row in self.db.execute(
            "SELECT * FROM events WHERE uid=? AND event_code NOT IN ('BUTN', 'SPTP') ORDER BY id", (uid,))]
        advice = [dict(row) for row in self.db.execute("SELECT * FROM advice WHERE uid=? ORDER BY id", (uid,))]
        sectors = [dict(row) for row in self.db.execute("SELECT * FROM sectors WHERE uid=? ORDER BY lap_number", (uid,))]
        valid = [lap["last_lap_ms"] for lap in laps if lap["last_lap_ms"] and lap["last_lap_ms"] > 0]
        comparable = [(before, after) for before, after in zip(laps, laps[1:])
                      if before["compound"] is not None and before["compound"] == after["compound"]
                      and before["pit_stops"] == after["pit_stops"]
                      and before["weather"] == after["weather"]]
        fuel_use = [a["fuel_kg"] - b["fuel_kg"] for a, b in comparable
                    if a["fuel_kg"] is not None and b["fuel_kg"] is not None
                    and 0 <= a["fuel_kg"] - b["fuel_kg"] <= 10]
        wear_gain = [b["tyre_wear_max"] - a["tyre_wear_max"] for a, b in comparable
                     if a["tyre_wear_max"] is not None and b["tyre_wear_max"] is not None
                     and 0 <= b["tyre_wear_max"] - a["tyre_wear_max"] <= 30]
        pace_changes = [(b["last_lap_ms"] - a["last_lap_ms"]) / 1000 for a, b in comparable
                        if a["last_lap_ms"] and b["last_lap_ms"]
                        and 30_000 <= a["last_lap_ms"] <= 300_000
                        and 30_000 <= b["last_lap_ms"] <= 300_000]
        complete_sectors = [row for row in sectors if all(row[key] and row[key] > 0
                            for key in ("sector1_ms", "sector2_ms", "sector3_ms"))]
        sector_spread = {}
        if len(complete_sectors) >= 3:
            for number in (1, 2, 3):
                values = [row[f"sector{number}_ms"] for row in complete_sectors]
                sector_spread[f"sector_{number}"] = round((median(values) - min(values)) / 1000, 3)
        coaching = (f"Sector {max(range(1, 4), key=lambda number: sector_spread[f'sector_{number}'])} "
                    "has the largest median gap to its best observed time; review consistency."
                    if sector_spread else "Sector coaching unavailable: need three complete history laps.")
        return {"session": dict(session), "laps": laps, "sectors": sectors, "events": events, "advice": advice,
                "summary": {"recorded_laps": len(laps), "best_observed_lap_ms": min(valid) if valid else None,
                            "mean_observed_lap_ms": round(sum(valid) / len(valid)) if valid else None,
                            "final_observed_position": session["final_position"],
                            "median_comparable_fuel_use_kg_per_lap": round(median(fuel_use), 3) if fuel_use else None,
                            "median_comparable_wear_gain_percent_per_lap": round(median(wear_gain), 2) if wear_gain else None,
                            "median_comparable_pace_change_seconds_per_lap": round(median(pace_changes), 3) if pace_changes else None,
                            "comparable_pair_count": len(comparable),
                            "observed_pit_stops": max((lap["pit_stops"] or 0 for lap in laps), default=0),
                            "sector_median_gap_to_best_seconds": sector_spread,
                            "coaching": coaching,
                            "note": "Observed data only; sector validity flags are stored but not interpreted; no hypothetical time gain is inferred."}}

    def export(self, uid: str, destination: Path, format: str) -> None:
        report = self.debrief(uid)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if format == "json":
            destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        elif format == "csv":
            with destination.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=("uid", "lap_number", "last_lap_ms", "position", "fuel_kg",
                                                          "tyre_wear_max", "compound", "ers_mj", "pit_stops", "weather", "recorded_utc", "setup_fingerprint"))
                writer.writeheader()
                writer.writerows(report["laps"])
        else:
            raise ValueError("format must be csv or json")


class HistoryWorker:
    """Bounded, best-effort storage so disk delays do not block UDP reception."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.queue: Queue[tuple] = Queue(maxsize=32)
        self.stop = ThreadEvent()
        self.thread = Thread(target=self._run, name="race-history", daemon=True)
        self.thread.start()

    def observe(self, state: RaceState, now: float) -> None:
        copy = RaceState(state.session_uid, state.player_index, dict(state.observations),
                         state.resets, state.reordered)
        self._enqueue(("observe", copy, now))

    def record_advice(self, uid: int | None, lap: int | None, message: str) -> None:
        self._enqueue(("advice", uid, lap, message))

    def _enqueue(self, item: tuple) -> None:
        try:
            self.queue.put_nowait(item)
        except Full:
            LOGGER.warning("History queue full; one record was dropped")

    def _run(self) -> None:
        try:
            with RaceHistory(self.path) as store:
                while not self.stop.is_set() or not self.queue.empty():
                    try:
                        item = self.queue.get(timeout=0.2)
                    except Empty:
                        continue
                    try:
                        if item[0] == "observe":
                            store.observe(item[1], item[2])
                        else:
                            store.record_advice(item[1], item[2], item[3])
                    except (OSError, sqlite3.Error, ValueError) as exc:
                        LOGGER.warning("History record skipped: %s", exc)
        except (OSError, sqlite3.Error) as exc:
            LOGGER.warning("History disabled: %s", exc)

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=3)
