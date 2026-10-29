"""Per-car race status from the game, with a cautious stationary fallback."""

from __future__ import annotations

from dataclasses import dataclass, field
import math


STATIONARY_PLAYER_LAPS = 2
MOVEMENT_M = 25.0


@dataclass(slots=True)
class CarWatch:
    progress_m: float
    position: tuple[float, float] | None
    player_progress_at_move: float
    last_row: dict
    inferred_dnf: bool = False


@dataclass(slots=True)
class RaceStatusTracker:
    identity: tuple[int | None, int | None, int] | None = None
    cars: dict[int, CarWatch] = field(default_factory=dict)

    def update(self, facts: dict) -> None:
        identity = (facts.get("session_uid"), facts.get("player_index"), facts.get("state_resets", 0))
        if identity != self.identity:
            self.identity = identity
            self.cars.clear()
        fields = facts.get("fields", {})
        lap_field, session_field = fields.get("lap"), fields.get("session")
        if not lap_field or not session_field or not facts.get("grid"):
            return
        lap, session = lap_field["value"], session_field["value"]
        length = session.get("track_length_m", 0)
        if session.get("session_type") not in (10, 11, 12) or length <= 0:
            return
        player_progress = (lap.get("lap_number", 0) - 1) * length + lap.get("lap_distance_m", -1)
        if not math.isfinite(player_progress) or player_progress < 0:
            return
        motion_field = fields.get("motion")
        positions = motion_field["value"].get("car_positions_m", ()) if motion_field else ()
        rows = facts["grid"]
        current = {row["car_index"]: row for row in rows}
        for index in set(current) | set(self.cars):
            row = current.get(index)
            if row is not None and row.get("is_player"):
                continue
            watch = self.cars.get(index)
            if row is None:
                if watch is None:
                    continue
                row = watch.last_row.copy()
                for key in ("race_status", "race_status_source", "out_of_race"):
                    row.pop(key, None)
            status = row.get("result_status", 0)
            progress = (row.get("lap_number", 0) - 1) * length + row.get("lap_distance_m", -1)
            position = None
            if 0 <= index < len(positions) and positions[index] is not None:
                x, _, z = positions[index]
                if math.isfinite(x) and math.isfinite(z):
                    position = (x, z)
            if watch is None:
                watch = CarWatch(progress, position, player_progress, row.copy())
                self.cars[index] = watch
            moved = (progress > watch.progress_m + MOVEMENT_M or
                     (position is not None and watch.position is not None
                      and math.dist(position, watch.position) > MOVEMENT_M))
            if moved or player_progress < watch.player_progress_at_move - MOVEMENT_M:
                watch.progress_m = progress
                watch.position = position
                watch.player_progress_at_move = player_progress
                watch.inferred_dnf = False
            elif watch.position is None and position is not None:
                watch.position = position
            if (session.get("safety_car") or row.get("pit_status") or status != 2
                    or position is None or motion_field is None):
                watch.player_progress_at_move = player_progress
            elif player_progress - watch.player_progress_at_move >= STATIONARY_PLAYER_LAPS * length:
                watch.inferred_dnf = True
            if status in (4, 7) or watch.inferred_dnf:
                row["race_status"] = "DNF"
                row["race_status_source"] = "game result status" if status in (4, 7) else "stationary for two player laps; inferred"
                row["out_of_race"] = True
            elif status == 5:
                row["race_status"] = "DSQ"
                row["race_status_source"] = "game result status"
                row["out_of_race"] = True
            if index in current:
                watch.last_row = row.copy()
            elif row.get("out_of_race"):
                rows.append(row)
