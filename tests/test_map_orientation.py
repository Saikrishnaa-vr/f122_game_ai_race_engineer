"""Protect the game-world/map handedness found from the recorded Bahrain lap."""

from __future__ import annotations

import math

from app.track_map import BAHRAIN_GAME_ROUTE, CATALOG, ObservedTrackMap


def _sample_loop(route: list[list[float]], count: int = 128) -> list[tuple[float, float]]:
    lengths = [math.dist(route[i], route[(i + 1) % len(route)]) for i in range(len(route))]
    total = sum(lengths)
    result = []
    for step in range(count):
        remaining = total * step / count
        for i, length in enumerate(lengths):
            if remaining <= length:
                fraction = remaining / length if length else 0
                a, b = route[i], route[(i + 1) % len(route)]
                result.append((a[0] + (b[0] - a[0]) * fraction,
                               a[1] + (b[1] - a[1]) * fraction))
                break
            remaining -= length
    mean_x = sum(point[0] for point in result) / count
    mean_y = sum(point[1] for point in result) / count
    return [(x - mean_x, y - mean_y) for x, y in result]


def _best_rms(reference: list[tuple[float, float]], observed: list[tuple[float, float]],
              vertical_sign: int) -> float:
    count = len(reference)
    return min(math.sqrt(sum((reference[i][0] - observed[(i + shift) % count][0]) ** 2
                             + (reference[i][1] - vertical_sign * observed[(i + shift) % count][1]) ** 2
                             for i in range(count)) / count)
               for shift in range(count))


def test_bahrain_game_z_is_opposite_to_geographic_north() -> None:
    geographic = _sample_loop(CATALOG["3"]["route"])
    game = _sample_loop(BAHRAIN_GAME_ROUTE["route"])
    assert _best_rms(geographic, game, -1) < 25
    assert _best_rms(geographic, game, 1) > 200


def test_track_map_selects_projection_by_coordinate_system() -> None:
    bahrain = ObservedTrackMap().update({"fields": {"session": {"value": {
        "track_id": 3, "track_length_m": BAHRAIN_GAME_ROUTE["track_length_m"]}}}})
    assert bahrain["orientation"] == "game-world"
    assert bahrain["route_source"] == "recorded F1 22 game path"

    melbourne = ObservedTrackMap().update({"fields": {"session": {"value": {
        "track_id": 0, "track_length_m": 5278}}}})
    assert melbourne["orientation"] == "geographic"
    assert melbourne["route_source"] == "bundled approximate circuit outline"
