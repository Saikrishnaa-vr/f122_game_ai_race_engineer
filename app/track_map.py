"""Bounded, observed track trace and current shared car positions for the dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path


SAMPLE_DISTANCE_M = 12.0
MAX_ROUTE_POINTS = 512
MAX_SAMPLE_JUMP_M = 300.0
MIN_COMPLETE_POINTS = 20
CATALOG = json.loads((Path(__file__).parent / "track_catalog.json").read_text(encoding="utf-8"))["tracks"]
BAHRAIN_GAME_ROUTE = json.loads((Path(__file__).parent / "bahrain_game_route.json").read_text(encoding="utf-8-sig"))


def _point_on_route(route: list[list[float]] | list[tuple[float, float]], fraction: float) -> tuple[float, float]:
    """Locate an approximate lap fraction along a bundled outline."""
    if not route:
        return (0.0, 0.0)
    if len(route) == 1:
        return tuple(route[0])
    lengths = [math.dist(a, b) for a, b in zip(route, route[1:])]
    total = sum(lengths)
    target = max(0.0, min(1.0, fraction)) * total
    for index, length in enumerate(lengths):
        if target <= length:
            ratio = target / length if length else 0.0
            return (route[index][0] + (route[index + 1][0] - route[index][0]) * ratio,
                    route[index][1] + (route[index + 1][1] - route[index][1]) * ratio)
        target -= length
    return tuple(route[-1])


@dataclass(slots=True)
class ObservedTrackMap:
    identity: tuple[int | None, int | None, int] | None = None
    candidate_lap: int | None = None
    started_at_line: bool = False
    invalidated: bool = False
    candidate: list[tuple[float, float]] = field(default_factory=list)
    complete: list[tuple[float, float]] = field(default_factory=list)
    invalidations: list[tuple[float, float, int]] = field(default_factory=list)
    last_invalid: bool = False
    invalidation_distances: list[float] = field(default_factory=list)
    motion_previous: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    slow_since: dict[int, float] = field(default_factory=dict)
    sector_splits: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    last_sector_lap: int | None = None
    last_sector: int | None = None
    last_phase_generation: int | None = None

    def update(self, facts: dict) -> dict:
        phase = facts.get("race_phase", {})
        phase_generation = phase.get("generation", 0)
        if phase_generation != self.last_phase_generation:
            self.last_phase_generation = phase_generation
            self.motion_previous.clear()
            self.slow_since.clear()
            if self.candidate:
                self.candidate.clear()
                self.candidate_lap = None
                self.started_at_line = False
        identity = (facts.get("session_uid"), facts.get("player_index"), facts.get("state_resets", 0))
        if identity != self.identity:
            self.identity = identity
            self.candidate_lap = None
            self.started_at_line = False
            self.invalidated = False
            self.candidate.clear()
            self.complete.clear()
            self.invalidations.clear()
            self.last_invalid = False
            self.invalidation_distances.clear()
            self.motion_previous.clear()
            self.slow_since.clear()
            self.sector_splits.clear()
            self.last_sector_lap = None
            self.last_sector = None

        fields = facts.get("fields", {})
        lap_field = fields.get("lap")
        motion_field = fields.get("motion")
        grid = facts.get("grid", [])
        session_field = fields.get("session")
        session = session_field["value"] if session_field else {}
        track_length = session.get("track_length_m") or 0
        if lap_field and motion_field and grid:
            lap = lap_field["value"]
            motion = motion_field["value"]
            lap_number = lap["lap_number"]
            position = motion["position_m"]
            point = (position[0], position[2])
            sector = lap.get("sector")
            distance = lap.get("lap_distance_m", -1)
            if (self.last_sector_lap == lap_number and self.last_sector in (0, 1)
                    and sector == self.last_sector + 1 and sector not in self.sector_splits
                    and track_length > 0 and 0 <= distance <= track_length
                    and self._valid_point(point) and not lap.get("pit_status")):
                self.sector_splits[sector] = (point[0], point[1], distance / track_length)
            self.last_sector_lap, self.last_sector = lap_number, sector
            invalid = bool(lap.get("invalid", 0))
            if invalid and not self.last_invalid and self._valid_point(point):
                self.invalidations.append((point[0], point[1], lap_number))
                self.invalidations = self.invalidations[-20:]
                self.invalidation_distances.append(lap.get("lap_distance_m", 0))
                self.invalidation_distances = self.invalidation_distances[-20:]
            self.last_invalid = invalid
            if self.candidate_lap != lap_number and not self.complete:
                consecutive = self.candidate_lap is not None and lap_number == self.candidate_lap + 1
                if (consecutive and self.started_at_line and not self.invalidated
                        and len(self.candidate) >= MIN_COMPLETE_POINTS):
                    self.complete = self.candidate.copy()
                self.candidate = []
                self.candidate_lap = lap_number
                self.started_at_line = consecutive or lap["lap_distance_m"] <= 100
                self.invalidated = False
            if not self.complete:
                if lap["pit_status"]:
                    self.invalidated = True
                    self.candidate.clear()
                elif not self.invalidated:
                    if self._valid_point(point):
                        if not self.candidate:
                            self.candidate.append(point)
                        else:
                            gap = math.dist(point, self.candidate[-1])
                            if gap > MAX_SAMPLE_JUMP_M:
                                self.invalidated = True
                                self.candidate.clear()
                            elif gap >= SAMPLE_DISTANCE_M and len(self.candidate) < MAX_ROUTE_POINTS:
                                self.candidate.append(point)

        track_id = session.get("track_id")
        catalog = CATALOG.get(str(track_id))
        game_route = (BAHRAIN_GAME_ROUTE["route"] if track_id == BAHRAIN_GAME_ROUTE["track_id"]
                      and abs(track_length - BAHRAIN_GAME_ROUTE["track_length_m"]) < 40 else None)
        if self.complete:
            route = [[round(x, 1), round(z, 1)] for x, z in self.complete]
            route_source = "live observed game path"
            exact = True
            orientation = "game-world"
        elif game_route:
            route = game_route
            route_source = "recorded F1 22 game path"
            exact = True
            orientation = "game-world"
        elif catalog:
            route = catalog["route"]
            route_source = "bundled approximate circuit outline"
            exact = False
            orientation = "geographic"
        else:
            route = [[round(x, 1), round(z, 1)] for x, z in self.candidate]
            route_source = "learning game path"
            exact = bool(route)
            orientation = "game-world"
        markers = []
        if motion_field and grid:
            positions = motion_field["value"].get("car_positions_m", [])
            observed_time = motion_field.get("observed_at_monotonic")
            player_speed = math.dist(motion_field["value"].get("velocity_m_per_s", (0, 0, 0)), (0, 0, 0))
            for row in grid:
                if row.get("out_of_race"):
                    continue
                index = row["car_index"]
                if not 0 <= index < len(positions):
                    continue
                position = positions[index]
                if position is None:
                    continue
                point = (position[0], position[2])
                if self._valid_point(point):
                    slow = (phase.get("status", "GREEN") == "GREEN" and observed_time is not None
                            and index in self.slow_since
                            and observed_time - self.slow_since[index] >= 2)
                    if (phase.get("status", "GREEN") == "GREEN" and observed_time is not None
                            and not row["is_player"] and not row.get("pit_status")):
                        previous = self.motion_previous.get(index)
                        if previous and 0.2 <= observed_time - previous[2] <= 5:
                            speed = math.dist(point, previous[:2]) / (observed_time - previous[2])
                            if speed < 12 and player_speed > 35 and row.get("lap_distance_m", 0) > 100:
                                self.slow_since.setdefault(index, observed_time)
                                slow = observed_time - self.slow_since[index] >= 2
                            else:
                                self.slow_since.pop(index, None)
                        self.motion_previous[index] = (point[0], point[1], observed_time)
                    if not exact and track_length > 0 and route:
                        display = _point_on_route(route, row.get("lap_distance_m", 0) / track_length)
                    else:
                        display = point
                    markers.append({"car_index": index, "position": row["position"],
                                    "name": row["name"], "is_player": row["is_player"],
                                    "driver_code": row.get("driver_code"),
                                    "race_number": row.get("race_number"),
                                    "team_name": row.get("team_name"),
                                    "team_color": row.get("team_color"),
                                    "team_text_color": row.get("team_text_color"),
                                    "x_m": round(display[0], 1), "z_m": round(display[1], 1),
                                    "slow": slow})
        flags = []
        zones = sorted(session.get("marshal_zones", ()))
        for zone_index, (start, flag) in enumerate(zones):
            if flag in (2, 3, 4):
                end = zones[zone_index + 1][0] if zone_index + 1 < len(zones) else 1.0
                if end > start:
                    flags.append({"start": start, "end": end, "flag": flag})
        displayed_invalidations = []
        for (x, z, lap_number), distance in zip(self.invalidations, self.invalidation_distances):
            if not exact and track_length > 0 and route:
                x, z = _point_on_route(route, distance / track_length)
            displayed_invalidations.append({"x_m": round(x, 1), "z_m": round(z, 1), "lap": lap_number})
        sector_markers = []
        if route:
            for sector in (1, 2):
                observed = self.sector_splits.get(sector)
                if observed and exact:
                    point = observed[:2]
                else:
                    point = _point_on_route(route, observed[2] if observed else sector / 3)
                sector_markers.append({"label": f"S{sector + 1}", "x_m": round(point[0], 1),
                                       "z_m": round(point[1], 1),
                                       "approximate": observed is None or not exact})
        fuel_lift = None
        lift = facts.get("estimates", {}).get("fuel_view", {})
        if (route and track_length > 0 and lift.get("status") == "candidate"
                and isinstance(lift.get("target_distance_m"), (int, float))):
            x, z = _point_on_route(route, lift["target_distance_m"] / track_length)
            fuel_lift = {"x_m": round(x, 1), "z_m": round(z, 1),
                         "sector": lift["sector"], "approximate": not exact}
        return {"route": route, "route_source": route_source, "orientation": orientation,
                "approximate_positions": not exact,
                "track_name": catalog["name"] if catalog else ("Bahrain International Circuit" if game_route else None),
                "flags": flags, "complete": bool(self.complete), "markers": markers,
                "sector_markers": sector_markers,
                "fuel_lift": fuel_lift,
                "lap_invalidations": [{"x_m": round(x, 1), "z_m": round(z, 1), "lap": lap}
                                      for x, z, lap in self.invalidations],
                "display_invalidations": displayed_invalidations,
                "motion_age_seconds": motion_field["age_seconds"] if motion_field and markers else None,
                "source": route_source}

    @staticmethod
    def _valid_point(point: tuple[float, float]) -> bool:
        return all(math.isfinite(axis) and abs(axis) <= 100_000 for axis in point)
