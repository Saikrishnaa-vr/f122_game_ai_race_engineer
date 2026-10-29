"""Summarize one local F1 22 recording without modifying it."""

from __future__ import annotations

import argparse
from collections import Counter
import json

from app.telemetry.capture import read_capture
from app.telemetry.payloads import CarDamage, CarStatus, Event, Lap, Session, decode_payload
from app.telemetry.protocol import PacketError, decode_packet


def analyze(name: str) -> dict:
    counts: Counter[int] = Counter()
    events: Counter[str] = Counter()
    sessions: Counter[int] = Counter()
    errors: Counter[str] = Counter()
    transitions: list[dict] = []
    gaps: list[dict] = []
    latest: dict[str, object] = {}
    previous_time: float | None = None
    first_time: float | None = None
    last_time = 0.0
    last_lap: int | None = None
    last_pit_status: int | None = None
    last_pit_stops: int | None = None
    last_compound: int | None = None
    last_uid: int | None = None
    total = 0
    for elapsed, raw in read_capture(name):
        total += 1
        first_time = elapsed if first_time is None else first_time
        last_time = elapsed
        if previous_time is not None and elapsed - previous_time > 2:
            gaps.append({"at_s": round(previous_time, 2), "duration_s": round(elapsed - previous_time, 2)})
        previous_time = elapsed
        try:
            header = decode_packet(raw)
            payload = decode_payload(raw, header)
        except PacketError as exc:
            errors[exc.reason] += 1
            continue
        counts[header.packet_id] += 1
        sessions[header.session_uid] += 1
        if last_uid != header.session_uid:
            transitions.append({"at_s": round(elapsed, 2), "kind": "session", "value": header.session_uid})
            last_uid = header.session_uid
            last_lap = last_pit_status = last_pit_stops = last_compound = None
        if isinstance(payload, Lap):
            latest["lap"] = payload.lap_number
            latest["position"] = payload.position
            if payload.lap_number != last_lap:
                transitions.append({"at_s": round(elapsed, 2), "kind": "lap", "value": payload.lap_number,
                                    "last_lap_ms": payload.last_lap_ms, "position": payload.position,
                                    "pit_stops": payload.pit_stops})
                last_lap = payload.lap_number
            if payload.pit_status != last_pit_status:
                transitions.append({"at_s": round(elapsed, 2), "kind": "pit_status", "value": payload.pit_status,
                                    "lap": payload.lap_number})
                last_pit_status = payload.pit_status
            if payload.pit_stops != last_pit_stops:
                transitions.append({"at_s": round(elapsed, 2), "kind": "pit_stops", "value": payload.pit_stops,
                                    "lap": payload.lap_number})
                last_pit_stops = payload.pit_stops
        elif isinstance(payload, CarStatus):
            latest["fuel_kg"] = round(payload.fuel_kg, 2)
            latest["ers_mj"] = round(payload.ers_joules / 1_000_000, 2)
            if payload.actual_compound != last_compound:
                transitions.append({"at_s": round(elapsed, 2), "kind": "compound", "value": payload.actual_compound,
                                    "visual": payload.visual_compound, "tyre_age": payload.tyre_age_laps,
                                    "lap": last_lap})
                last_compound = payload.actual_compound
        elif isinstance(payload, CarDamage):
            latest["max_wear_percent"] = round(max(payload.tyre_wear_percent), 2)
            latest["wing_damage_percent"] = max(payload.front_left_wing_percent,
                                                 payload.front_right_wing_percent, payload.rear_wing_percent)
        elif isinstance(payload, Session):
            latest["track_id"] = payload.track_id
            latest["total_laps"] = payload.total_laps
            latest["weather"] = payload.weather
            latest["pit_ideal_lap"] = payload.pit_ideal_lap
            latest["pit_latest_lap"] = payload.pit_latest_lap
        elif isinstance(payload, Event):
            events[payload.code] += 1
    return {"capture": name, "datagrams": total, "first_s": first_time, "last_s": round(last_time, 2),
            "packet_counts": dict(sorted(counts.items())), "session_counts": dict(sessions),
            "errors": dict(errors), "gaps_over_2s": gaps, "transitions": transitions,
            "event_counts": dict(events.most_common()), "latest": latest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", help="A simple .f1cap filename in recordings")
    args = parser.parse_args()
    print(json.dumps(analyze(args.capture), indent=2))
