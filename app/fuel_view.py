"""Replayable fuel-use history and cautious, observed lift/coast candidates."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FuelView:
    identity: tuple[int | None, int | None, int, int] | None = None
    lap_start: tuple[int, float, int, int] | None = None
    history: list[dict] = field(default_factory=list)
    previous_brake: tuple[int, float, float] | None = None
    braking_zones: list[dict] = field(default_factory=list)

    def update(self, facts: dict) -> dict:
        phase = facts.get("race_phase", {})
        identity = (facts.get("session_uid"), facts.get("player_index"),
                    facts.get("state_resets", 0), phase.get("generation", 0))
        if identity != self.identity:
            self.identity = identity
            self.lap_start = None
            self.history.clear()
            self.previous_brake = None
            self.braking_zones.clear()

        fields = facts.get("fields", {})
        lap_field, status_field = fields.get("lap"), fields.get("car_status")
        session_field, driving_field = fields.get("session"), fields.get("driving")
        result = {"history": self.history.copy(), "status": "unavailable",
                  "message": "Need comparable lap-start fuel readings before a finish estimate."}
        if not (lap_field and status_field and session_field):
            return result
        if phase.get("status", "GREEN") != "GREEN":
            result.update(status="hold", message="Reassess fuel after fresh green-flag laps.")
            return result
        lap, car, session = lap_field["value"], status_field["value"], session_field["value"]
        length = session.get("track_length_m", 0)
        number = lap.get("lap_number", 0)
        distance = lap.get("lap_distance_m", -1)
        if session.get("session_type") not in (10, 11, 12) or length <= 0 or number <= 0:
            return result

        if lap.get("pit_status") or (self.lap_start and
                (self.lap_start[2] != car["actual_compound"] or self.lap_start[3] != lap["pit_stops"])):
            self.lap_start = None
            self.history.clear()
            self.previous_brake = None
            self.braking_zones.clear()
        near_line = 0 <= distance <= min(250.0, length * 0.1)
        if self.lap_start is None and near_line and not lap.get("pit_status"):
            self.lap_start = (number, car["fuel_kg"], car["actual_compound"], lap["pit_stops"])
        elif self.lap_start is not None and number != self.lap_start[0] and near_line:
            old_number, old_fuel, compound, stops = self.lap_start
            used = old_fuel - car["fuel_kg"]
            if (number == old_number + 1 and compound == car["actual_compound"]
                    and stops == lap["pit_stops"] and 0 <= used <= 10):
                self.history.append({"lap_number": old_number, "used_kg": round(used, 2)})
                del self.history[:-8]
            self.lap_start = (number, car["fuel_kg"], car["actual_compound"], lap["pit_stops"])
        result["history"] = self.history.copy()

        if (driving_field and session.get("safety_car") == 0
                and not lap.get("pit_status") and not lap.get("invalid")
                and 0 <= distance <= length + 2):
            driving = driving_field["value"]
            brake = driving["brake"]
            speed = driving["speed_kph"]
            old = self.previous_brake
            if (old and old[0] == number and old[1] < 0.15 and brake >= 0.55
                    and old[2] >= 160 and speed >= 100):
                zone = next((item for item in self.braking_zones
                             if min(abs(item["distance_m"] - distance),
                                    abs(length - abs(item["distance_m"] - distance))) <= 120), None)
                if zone is None:
                    zone = {"distance_m": distance, "sector": lap.get("sector", 0), "laps": set()}
                    self.braking_zones.append(zone)
                zone["laps"].add(number)
            self.previous_brake = (number, brake, speed)
        else:
            self.previous_brake = None

        plan = facts.get("estimates", {}).get("fuel_plan")
        if not plan:
            return result
        if plan["status"] != "short":
            result.update(status="on_target", message="Current fuel-use estimate reaches the finish. Keep monitoring each lap.")
            return result
        if session.get("safety_car") != 0 or lap.get("pit_status"):
            result.update(status="hold", message="Fuel estimate is short; reassess after the flag or pit phase.")
            return result
        ready = [zone for zone in self.braking_zones if len(zone["laps"]) >= 2]
        if not ready:
            result.update(status="learning", message="Fuel estimate is short. Lift before your usual major braking zones; exact points need two observed clean laps.")
            return result
        candidates = []
        for zone in ready:
            target = (zone["distance_m"] - 100) % length
            ahead = (target - distance) % length
            if ahead > 120:
                candidates.append((ahead, target, zone))
        if not candidates:
            result.update(status="learning", message="Fuel estimate is short. Next observed lift point is too close to call safely.")
            return result
        ahead, target, zone = min(candidates, key=lambda item: item[0])
        sector = int(zone["sector"]) + 1
        result.update(status="candidate", target_distance_m=round(target),
                      distance_ahead_m=round(ahead), sector=sector,
                      observed_laps=len(zone["laps"]),
                      message=(f"Candidate in S{sector}: lift about 100 m before your usual heavy braking point, "
                               f"roughly {ahead:.0f} m ahead. Coast to your normal brake marker; saving is uncalibrated."))
        return result
