"""Limited position and pace analysis from shared lap data only."""

from __future__ import annotations

from .driver_identity import driver_code, team_identity
from .telemetry.payloads import Lap, Participant, Session
from .telemetry.protocol import CAR_COUNT


def full_grid(lap: Lap, session: Session, player_index: int,
              participant: Participant | None = None,
              timed_gaps: dict[int, tuple[float, float]] | None = None) -> list[dict]:
    """Observed shared timing for cars with a valid running position."""
    if not 0 <= player_index < CAR_COUNT or not 1 <= lap.position <= CAR_COUNT or lap.lap_number <= 0:
        return []
    names = participant.car_names if participant is not None else ()
    team_ids = participant.car_team_ids if participant is not None else ()
    race_numbers = participant.car_race_numbers if participant is not None else ()
    player_distance = ((lap.lap_number - 1) * session.track_length_m + lap.lap_distance_m
                       if session.track_length_m > 0
                       and 0 <= lap.lap_distance_m <= session.track_length_m + 2 else None)
    reference_lap_s = (lap.last_lap_ms / 1000 if not lap.invalid
                       and 30_000 <= lap.last_lap_ms <= 300_000 else None)
    laps_left = (session.total_laps - lap.lap_number + 1
                 - max(0.0, lap.lap_distance_m) / session.track_length_m
                 if session.session_type in (10, 11, 12) and session.total_laps >= lap.lap_number
                 and session.track_length_m > 0 else None)

    def row(car_index: int, position: int, lap_number: int, distance_m: float,
            last_lap_ms: int, sector1_ms: int, sector2_ms: int, pit_status: int,
            pit_stops: int, penalties_s: int, invalid: int, result_status: int) -> dict:
        is_player = car_index == player_index
        name = names[car_index] if car_index < len(names) else None
        team_id = team_ids[car_index] if car_index < len(team_ids) else (participant.team_id if is_player and participant else None)
        race_number = (race_numbers[car_index] if car_index < len(race_numbers)
                       else participant.race_number if is_player and participant else None)
        team_name, team_color, team_text_color = team_identity(team_id)
        relative_distance = None
        if player_distance is not None and 0 <= distance_m <= session.track_length_m + 2:
            relative_distance = round((lap_number - 1) * session.track_length_m
                                      + distance_m - player_distance)
        gap_s = (round(relative_distance / session.track_length_m * reference_lap_s, 2)
                 if relative_distance is not None and reference_lap_s is not None
                 and abs(relative_distance) <= session.track_length_m else None)
        observed = (timed_gaps or {}).get(car_index)
        best_gap = observed[0] if observed else gap_s
        pace_needed = (round((best_gap + 1.0) / laps_left, 2)
                       if not is_player and position < lap.position
                       and best_gap is not None and best_gap > 0
                       and laps_left is not None and laps_left > 0.1 else None)
        delta_last_lap = None
        if (not is_player and not invalid and not lap.invalid
                and last_lap_ms > 0 and lap.last_lap_ms > 0):
            delta_last_lap = last_lap_ms - lap.last_lap_ms
        return {"car_index": car_index, "position": position, "name": name or ("You" if is_player else f"Car {car_index + 1}"),
                "driver_code": driver_code(name, car_index, is_player),
                "race_number": race_number, "team_id": team_id, "team_name": team_name,
                "team_color": team_color, "team_text_color": team_text_color,
                "is_player": is_player, "lap_number": lap_number, "lap_distance_m": round(distance_m, 1),
                "last_lap_ms": last_lap_ms if last_lap_ms > 0 and not invalid else None,
                "sector1_ms": sector1_ms or None, "sector2_ms": sector2_ms or None,
                "pit_status": pit_status, "pit_stops": pit_stops, "penalties_s": penalties_s,
                "result_status": result_status,
                "relative_distance_m": relative_distance,
                "estimated_gap_seconds": gap_s,
                "gap_seconds": best_gap,
                "gap_source": "shared same-checkpoint crossing times" if observed else "distance-scaled estimate",
                "gap_age_seconds": observed[1] if observed else None,
                "pace_to_pass_seconds_per_lap": pace_needed,
                "gap_method": "distance divided by track length times your last valid lap; not official timing",
                "delta_last_lap_ms": delta_last_lap,
                "source": "shared Lap timing and optional Participants name; no rival fuel, tyre wear or ERS"}

    rows = [row(player_index, lap.position, lap.lap_number, lap.lap_distance_m,
                lap.last_lap_ms, lap.sector1_ms, lap.sector2_ms, lap.pit_status,
                lap.pit_stops, lap.penalties_s, lap.invalid, lap.result_status)]
    positions = {lap.position}
    for rival in lap.rivals:
        if rival.position in positions or rival.car_index == player_index:
            continue
        positions.add(rival.position)
        rows.append(row(rival.car_index, rival.position, rival.lap_number, rival.lap_distance_m,
                        rival.last_lap_ms, rival.sector1_ms, rival.sector2_ms, rival.pit_status,
                        rival.pit_stops, rival.penalties_s, rival.invalid, rival.result_status))
    return sorted(rows, key=lambda item: item["position"])


def adjacent_rivals(lap: Lap, session: Session) -> dict:
    result: dict = {}
    if session.track_length_m <= 0 or not 1 <= lap.position <= 22:
        return result
    for label, position in (("ahead", lap.position - 1), ("behind", lap.position + 1)):
        rival = next((car for car in lap.rivals if car.position == position), None)
        if rival is None:
            continue
        player_distance = (lap.lap_number - 1) * session.track_length_m + lap.lap_distance_m
        rival_distance = (rival.lap_number - 1) * session.track_length_m + rival.lap_distance_m
        gap = rival_distance - player_distance if label == "ahead" else player_distance - rival_distance
        if gap < 0 or gap > session.track_length_m * 1.5:
            continue
        entry = {"car_index": rival.car_index, "position": rival.position,
                 "distance_gap_m": round(gap), "last_lap_ms": rival.last_lap_ms or None,
                 "source": "shared lap packet; rival fuel, tyres and ERS unavailable"}
        if lap.last_lap_ms > 0 and rival.last_lap_ms > 0 and not lap.invalid and not rival.invalid:
            closing_ms = rival.last_lap_ms - lap.last_lap_ms if label == "ahead" else lap.last_lap_ms - rival.last_lap_ms
            entry["last_lap_relative_pace_ms"] = closing_ms
            if closing_ms > 0 and 0 <= gap <= session.track_length_m:
                seconds_gap = gap / session.track_length_m * lap.last_lap_ms / 1000
                entry["rough_catch_laps"] = round(seconds_gap / (closing_ms / 1000), 1)
                entry["confidence"] = "very low: one-lap pace and distance-derived gap"
        result[label] = entry
    return result
