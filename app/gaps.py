"""Observed same-checkpoint gaps from shared lap-distance telemetry."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math

from .telemetry.payloads import Lap


CHECKPOINT_M = 25
MAX_CROSSINGS = 256


@dataclass(slots=True)
class CheckpointGaps:
    previous: dict[int, tuple[float, float]] = field(default_factory=dict)
    crossings: dict[int, deque[tuple[int, float]]] = field(default_factory=dict)
    last_update: float = -1.0

    def clear(self) -> None:
        self.previous.clear()
        self.crossings.clear()
        self.last_update = -1.0

    def update(self, lap: Lap, player_index: int, track_length_m: int, now: float) -> None:
        if track_length_m <= 0 or now - self.last_update < 0.18:
            return
        self.last_update = now
        cars = [(player_index, lap.lap_number, lap.lap_distance_m)]
        cars.extend((r.car_index, r.lap_number, r.lap_distance_m) for r in lap.rivals)
        for index, number, distance in cars:
            if not (number > 0 and math.isfinite(distance) and 0 <= distance <= track_length_m + 2):
                continue
            progress = (number - 1) * track_length_m + distance
            previous = self.previous.get(index)
            self.previous[index] = (progress, now)
            if previous is None:
                continue
            old_progress, old_time = previous
            travelled = progress - old_progress
            if travelled < -2:
                self.crossings.pop(index, None)
                continue
            if not 0 < travelled <= 100 or not 0 < now - old_time <= 2.0:
                continue
            first = math.floor(old_progress / CHECKPOINT_M) + 1
            last = math.floor(progress / CHECKPOINT_M)
            history = self.crossings.setdefault(index, deque(maxlen=MAX_CROSSINGS))
            for checkpoint in range(first, last + 1):
                line = checkpoint * CHECKPOINT_M
                crossed_at = old_time + (line - old_progress) / travelled * (now - old_time)
                history.append((checkpoint, crossed_at))

    def gap_seconds(self, player_index: int, rival_index: int, now: float) -> tuple[float, float] | None:
        player = self.crossings.get(player_index)
        rival = self.crossings.get(rival_index)
        if not player or not rival:
            return None
        rival_times = dict(rival)
        for checkpoint, player_time in reversed(player):
            rival_time = rival_times.get(checkpoint)
            if rival_time is None:
                continue
            age = now - max(player_time, rival_time)
            if age < 0 or age > 15:
                return None
            gap = player_time - rival_time
            if abs(gap) > 120:
                return None
            return round(gap, 2), round(age, 2)
        return None
