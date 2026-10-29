"""Grounded, serializable race facts for radio and local clients."""

from __future__ import annotations

from dataclasses import asdict
import math

from .engineer import Engineer
from .strategy import compare_pit_scenarios
from .rivals import adjacent_rivals, full_grid
from .tactics import project_rejoin
from .telemetry.payloads import CarDamage, CarStatus, CarTelemetry, Event, Lap, Participant, Session, SessionHistory
from .telemetry.state import RaceState


def pit_window_alert(session: Session, lap: Lap) -> dict | None:
    """Show only a fresh, valid game-provided window near the player's stop."""
    ideal, latest = session.pit_ideal_lap, session.pit_latest_lap
    if (session.session_type not in (10, 11, 12) or lap.pit_status
            or not 1 <= ideal <= latest <= session.total_laps):
        return None
    if lap.lap_number == ideal - 1:
        return {"level": "near", "title": "PIT WINDOW NEXT LAP",
                "detail": f"Game ideal lap {ideal}; latest lap {latest}. Prepare your stop."}
    if ideal <= lap.lap_number < latest:
        return {"level": "open", "title": "PIT WINDOW OPEN",
                "detail": f"Game ideal lap {ideal}; latest lap {latest}. Check traffic and tyre plan."}
    if lap.lap_number == latest:
        return {"level": "urgent", "title": "LATEST PIT LAP",
                "detail": f"Game pit window reaches its latest lap {latest}. Reassess your stop."}
    return None


def safety_car_alert(session: Session) -> dict | None:
    if session.safety_car == 1:
        return {"title": "SAFETY CAR", "detail":
                "Active in game. Recheck gaps and pit plan; SC pit loss needs calibration."}
    if session.safety_car == 2:
        return {"title": "VIRTUAL SAFETY CAR", "detail":
                "Active in game. Recheck gaps and pit plan; VSC pit loss needs calibration."}
    return None


def fuel_finish_projection(fuel_kg: float, use_kg_per_lap: float, remaining_laps: float) -> dict | None:
    """Low-confidence fuel budget from one comparable lap-start use rate."""
    if not (math.isfinite(fuel_kg) and math.isfinite(use_kg_per_lap)
            and math.isfinite(remaining_laps) and fuel_kg >= 0
            and use_kg_per_lap > 0 and remaining_laps > 0):
        return None
    finish_kg = fuel_kg - use_kg_per_lap * remaining_laps
    target_use = fuel_kg / remaining_laps
    return {"status": "short" if finish_kg < -0.1 else "on_target",
            "fuel_kg": round(fuel_kg, 2), "use_kg_per_lap": round(use_kg_per_lap, 2),
            "remaining_laps": round(remaining_laps, 2),
            "projected_finish_kg": round(finish_kg, 2),
            "required_save_kg_per_lap": round(max(0.0, use_kg_per_lap - target_use), 2),
            "target_use_kg_per_lap": round(target_use, 2),
            "confidence": "low", "method": "one comparable lap-start fuel pair; assumes constant use"}


def race_facts(state: RaceState, engineer: Engineer, now: float) -> dict:
    """Expose only fresh observations; estimates carry their method and limits."""
    facts: dict = {"session_uid": state.session_uid, "player_index": state.player_index,
                   "state_resets": state.resets, "observed_at_monotonic": now,
                   "fields": {}, "estimates": {}, "grid": [], "sector_leaders": [None, None, None],
                   "pit_alert": None, "safety_alert": None,
                   "race_phase": state.timeline.snapshot(now)}
    green = facts["race_phase"]["status"] == "GREEN"
    comparable_pace = green and (facts["race_phase"]["pace_ready"]
                                 or state.timeline.last_packet_at is None)
    names = {0: "motion", 1: "session", 2: "lap", 3: "event", 4: "participants", 5: "setup",
             6: "driving", 7: "car_status",
             10: "damage", 11: "history"}
    for packet_id, name in names.items():
        obs = state.get_fresh(packet_id, now)
        if obs is not None:
            facts["fields"][name] = {"value": asdict(obs.value),
                                      "observed_at_monotonic": obs.observed_at,
                                      "age_seconds": max(0.0, now - obs.observed_at),
                                      "source_packet_id": packet_id}
        else:
            facts["fields"][name] = None
    lap_obs = state.get_fresh(2, now)
    session_obs = state.get_fresh(1, now)
    if session_obs and isinstance(session_obs.value, Session):
        facts["safety_alert"] = safety_car_alert(session_obs.value)
    if lap_obs and session_obs and isinstance(lap_obs.value, Lap) and isinstance(session_obs.value, Session):
        facts["pit_alert"] = pit_window_alert(session_obs.value, lap_obs.value)
    # Participant names are session metadata. The game can later send a smaller
    # active-car count that omits the player's fixed index; keep the last valid
    # roster until RaceState resets for a new session or flashback.
    participant_obs = state.observations.get(4)
    status_obs = state.get_fresh(7, now)
    damage_obs = state.get_fresh(10, now)
    if (green and lap_obs and session_obs and status_obs and isinstance(lap_obs.value, Lap)
            and isinstance(session_obs.value, Session) and isinstance(status_obs.value, CarStatus)
            and engineer.diagnostics.fuel_rate is not None
            and engineer.diagnostics.fuel_rate > 0 and session_obs.value.track_length_m > 0
            and session_obs.value.session_type in (10, 11, 12)):
        session, lap, status = session_obs.value, lap_obs.value, status_obs.value
        if 1 <= lap.lap_number <= session.total_laps:
            remaining = session.total_laps - lap.lap_number + 1 - lap.lap_distance_m / session.track_length_m
            margin = status.fuel_kg / engineer.diagnostics.fuel_rate - remaining
            if math.isfinite(margin):
                facts["estimates"]["fuel_margin_laps"] = {
                    "value": round(margin, 2), "units": "laps", "method": "one comparable lap-start pair",
                    "confidence": "low"}
                plan = fuel_finish_projection(status.fuel_kg, engineer.diagnostics.fuel_rate, remaining)
                if plan is not None:
                    facts["estimates"]["fuel_plan"] = plan
    if damage_obs and isinstance(damage_obs.value, CarDamage):
        wear = damage_obs.value.tyre_wear_percent
        limiting = max(range(4), key=wear.__getitem__)
        facts["estimates"]["limiting_tyre"] = {
            "value": ("rear-left", "rear-right", "front-left", "front-right")[limiting],
            "wear_percent": wear[limiting], "method": "highest observed wear"}
    if (green and lap_obs and session_obs and damage_obs and engineer.diagnostics.wear_rate
            and isinstance(lap_obs.value, Lap) and isinstance(session_obs.value, Session)
            and isinstance(damage_obs.value, CarDamage) and session_obs.value.track_length_m > 0):
        projections = [(100 - wear) / rate for wear, rate in
                       zip(damage_obs.value.tyre_wear_percent, engineer.diagnostics.wear_rate)
                       if rate > 0]
        if projections:
            facts["estimates"]["tyre_life_laps_to_100_percent"] = {
                "value": round(max(0.0, min(projections)), 1), "units": "laps",
                "method": "linear extrapolation of one comparable lap pair", "confidence": "low",
                "warning": "100% wear is a data bound, not a safe operating target"}
    if green and engineer.diagnostics.ers_delta_mj_per_lap is not None and status_obs:
        facts["estimates"]["ers_net_change_mj_per_lap"] = {
            "value": round(engineer.diagnostics.ers_delta_mj_per_lap, 2), "units": "MJ/lap",
            "method": "two comparable lap-start energy observations", "confidence": "low"}
    if lap_obs and session_obs and isinstance(lap_obs.value, Lap) and isinstance(session_obs.value, Session):
        if comparable_pace:
            facts["estimates"]["rivals"] = adjacent_rivals(lap_obs.value, session_obs.value)
        if state.player_index is not None:
            participant = (participant_obs.value if participant_obs is not None
                           and isinstance(participant_obs.value, Participant) else None)
            timed_gaps = {row.car_index: gap for row in lap_obs.value.rivals
                          if (gap := state.gaps.gap_seconds(state.player_index, row.car_index, now)) is not None}
            facts["grid"] = full_grid(lap_obs.value, session_obs.value, state.player_index,
                                      participant, timed_gaps)
            for row in facts["grid"]:
                history_obs = state.get_fresh_history(row["car_index"], now)
                history = (history_obs.value if history_obs is not None
                           and isinstance(history_obs.value, SessionHistory) else None)
                best_ms = None
                if history is not None and 1 <= history.best_lap_number <= len(history.laps):
                    best_ms = history.laps[history.best_lap_number - 1].lap_ms or None
                row["best_lap_ms"] = best_ms
                row["recorded_laps"] = len(history.laps) if history is not None else None
                row["history_age_seconds"] = round(now - history_obs.observed_at, 1) if history_obs else None
                row["recent_laps"] = ([{"lap_number": number, "lap_ms": entry.lap_ms,
                                         "sector1_ms": entry.sector1_ms,
                                         "sector2_ms": entry.sector2_ms,
                                         "sector3_ms": entry.sector3_ms}
                                        for number, entry in enumerate(history.laps, 1) if entry.lap_ms > 0][-5:]
                                      if history is not None else [])
                sector_fields = ("sector1_ms", "sector2_ms", "sector3_ms")
                row["best_sector_ms"] = [min((getattr(entry, field) for entry in history.laps
                                               if entry.lap_ms > 0 and getattr(entry, field) > 0), default=None)
                                         if history is not None else None for field in sector_fields]
                # S1/S2 can be completed on the current lap before history updates.
                for index, field in enumerate(sector_fields[:2]):
                    current = row.get(field)
                    if current and (row["best_sector_ms"][index] is None
                                    or current < row["best_sector_ms"][index]):
                        row["best_sector_ms"][index] = current
            for index in range(3):
                candidates = (row for row in facts["grid"] if row["best_sector_ms"][index] is not None)
                leader = min(candidates, key=lambda row: row["best_sector_ms"][index], default=None)
                if leader is not None:
                    facts["sector_leaders"][index] = {
                        "car_index": leader["car_index"], "driver_code": leader["driver_code"],
                        "race_number": leader["race_number"], "ms": leader["best_sector_ms"][index]}
            player_row = next((row for row in facts["grid"] if row["is_player"]), None)
            player_best = player_row["best_lap_ms"] if player_row else None
            for row in facts["grid"]:
                row["delta_best_lap_ms"] = (row["best_lap_ms"] - player_best
                                            if not row["is_player"] and row["best_lap_ms"] is not None
                                            and player_best is not None else None)
                if not comparable_pace:
                    row["pace_to_pass_seconds_per_lap"] = None
                    row["delta_last_lap_ms"] = None
                    if row["gap_source"] == "distance-scaled estimate":
                        row["gap_seconds"] = None
                        row["estimated_gap_seconds"] = None
        facts["estimates"]["rejoin"] = (
            {"status": "unavailable", "reason": "wait for a comparable green-flag lap after restart"}
            if green and not comparable_pace else
            project_rejoin(facts["grid"], lap_obs.value, session_obs.value,
                           engineer.strategy_assumptions))
    advice = compare_pit_scenarios(state, now, engineer.diagnostics.wear_rate,
                                   engineer.strategy_assumptions)
    facts["strategy"] = asdict(advice)
    return facts


def answer_question(question: str, facts: dict) -> str:
    """Offline radio response. A missing field produces an explicit gap."""
    q = question.casefold().strip()
    fields = facts["fields"]
    estimates = facts["estimates"]
    if any(word in q for word in ("pit", "box", "strategy", "stop")):
        advice = facts["strategy"]
        reply = advice["recommendation"]
        if advice["expected_benefit_seconds"] is not None:
            reply += (f". Modelled advantage {advice['expected_benefit_seconds']:.1f} seconds, "
                      f"uncertainty at least {advice['uncertainty_seconds']:.1f} seconds")
        if advice["risks"]:
            reply += ". Main risk: " + advice["risks"][0]
        return reply + "."
    if any(word in q for word in ("tyre", "tire", "wear")):
        if fields["damage"] is None:
            return "Tyre data is unavailable or stale."
        wear = fields["damage"]["value"]["tyre_wear_percent"]
        tyre = estimates["limiting_tyre"]
        life = estimates.get("tyre_life_laps_to_100_percent")
        reply = (f"{tyre['value']} is limiting at {tyre['wear_percent']:.0f}% wear. "
                 f"All four are {', '.join(f'{value:.0f}' for value in wear)}% rear-left to front-right")
        driving = fields["driving"]
        if driving is not None:
            inner = driving["value"]["inner_c"]
            hottest = max(range(4), key=inner.__getitem__)
            reply += (f". Hottest inner tyre is {('rear-left', 'rear-right', 'front-left', 'front-right')[hottest]} "
                      f"at {inner[hottest]} Celsius; no compound-specific overheat limit is calibrated")
        if life:
            reply += f". A one-lap linear estimate reaches the data limit in {life['value']:.1f} laps; this is not a safe-life estimate"
        return reply + "."
    if any(word in q for word in ("fuel", "finish", "lift", "coast")):
        if fields["car_status"] is None:
            return "Fuel data is unavailable or stale."
        fuel = fields["car_status"]["value"]["fuel_kg"]
        plan = estimates.get("fuel_plan")
        if plan:
            outlook = (f"Projected finish {plan['projected_finish_kg']:+.2f} kilograms "
                       f"at {plan['use_kg_per_lap']:.2f} kilograms per lap (low confidence).")
            if plan["status"] == "short":
                outlook += (f" Target no more than {plan['target_use_kg_per_lap']:.2f} kilograms per lap; "
                            f"save about {plan['required_save_kg_per_lap']:.2f} kilograms per lap.")
                view = estimates.get("fuel_view", {})
                if view.get("message"):
                    outlook += " " + view["message"]
            return f"Fuel {fuel:.2f} kilograms. {outlook}"
        margin = estimates.get("fuel_margin_laps")
        if margin:
            action = "Consider conserving fuel and recheck next lap." if margin["value"] < 0 else "Keep monitoring."
            return (f"Fuel {fuel:.2f} kilograms. Approximate margin {margin['value']:+.2f} laps "
                    f"from one comparable lap. {action}")
        return f"Fuel {fuel:.2f} kilograms. I cannot estimate the race margin yet."
    if "ers" in q or "battery" in q:
        status = fields["car_status"]
        if status is None:
            return "ERS data is unavailable or stale."
        trend = estimates.get("ers_net_change_mj_per_lap")
        return (f"ERS energy {status['value']['ers_joules'] / 1_000_000:.2f} megajoules. "
                + (f"Net change {trend['value']:+.2f} megajoules last comparable lap. " if trend else "Trend unavailable. ")
                + "Deployment advice needs pace and race context.")
    if "weather" in q or "rain" in q:
        session = fields["session"]
        if session is None:
            return "Weather data is unavailable or stale."
        forecasts = session["value"]["forecasts"]
        if forecasts:
            sample = min(forecasts, key=lambda item: item["offset_minutes"])
            return (f"Game weather code {session['value']['weather']}; next forecast code "
                    f"{sample['weather']} in {sample['offset_minutes']} minutes. Crossover pace is uncalibrated.")
        return f"Game weather code {session['value']['weather']}. No forecast is available."
    if any(phrase in q for phrase in ("fastest", "best lap", "compare laps", "the grid", "the field")):
        grid = facts.get("grid", [])
        timed = [row for row in grid if row.get("best_lap_ms") is not None]
        if not timed:
            return "Full-grid best-lap comparison is unavailable or stale."
        fastest = min(timed, key=lambda row: row["best_lap_ms"])
        reply = (f"Fastest game-reported best lap among {len(timed)} cars with fresh history: "
                 f"P{fastest['position']} {fastest['name']} at {fastest['best_lap_ms'] / 1000:.3f} seconds")
        player = next((row for row in timed if row["is_player"]), None)
        if player is not None and player is not fastest:
            delta = player["best_lap_ms"] - fastest["best_lap_ms"]
            reply += f". Your best is {delta / 1000:.3f} seconds slower"
        return reply + "."
    if "rival" in q or "ahead" in q or "behind" in q or "catch" in q:
        rivals = estimates.get("rivals", {})
        if not rivals:
            return "Adjacent rival timing is unavailable or stale."
        return "; ".join(f"Car {data['position']} {label}, roughly {data['distance_gap_m']} metres by lap position"
                         for label, data in rivals.items()) + ". Rival tyre and fuel data are unavailable."
    if "position" in q or "lap" in q or "pace" in q:
        lap = fields["lap"]
        if lap is None:
            return "Lap and position data is unavailable or stale."
        value = lap["value"]
        return (f"Lap {value['lap_number']}, position {value['position']}. "
                f"Last lap {value['last_lap_ms'] / 1000:.3f} seconds if the game marked it valid.")
    if "damage" in q or "wing" in q:
        damage = fields["damage"]
        if damage is None:
            return "Damage data is unavailable or stale."
        value = damage["value"]
        return (f"Front wings {value['front_left_wing_percent']} and {value['front_right_wing_percent']}% damage; "
                f"rear wing {value['rear_wing_percent']}%, gearbox {value['gearbox_percent']}%.")
    return "Ask about fuel, tyres, ERS, weather, damage, lap, position or pit strategy."
