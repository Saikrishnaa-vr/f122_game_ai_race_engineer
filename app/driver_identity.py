"""Stable 2022 driver labels and readable approximations of team livery colours."""

from __future__ import annotations

import re
import unicodedata


# F1 22 UDP team IDs 0-9. These are dashboard colours chosen for contrast,
# not official colour specifications or values supplied by game telemetry.
TEAMS: dict[int, tuple[str, str, str]] = {
    0: ("Mercedes", "#00b8a9", "#061923"),
    1: ("Ferrari", "#e32731", "#ffffff"),
    2: ("Red Bull Racing", "#3357d7", "#ffffff"),
    3: ("Williams", "#3585ee", "#ffffff"),
    4: ("Aston Martin", "#13846f", "#ffffff"),
    5: ("Alpine", "#158fd0", "#ffffff"),
    6: ("AlphaTauri", "#526980", "#ffffff"),
    7: ("Haas", "#bec7d1", "#0b1725"),
    8: ("McLaren", "#ef8309", "#071624"),
    9: ("Alfa Romeo", "#ac243b", "#ffffff"),
}

SPECIAL_CODES = {
    "SCHUMACHER": "MSC", "GUANYU": "ZHO", "ZHOU": "ZHO",
    "DE VRIES": "DEV", "PEREZ": "PER",
}


def driver_code(name: str | None, car_index: int, is_player: bool = False) -> str:
    """Use familiar three-letter codes; never invent a real driver's name."""
    if not name:
        return "YOU" if is_player else f"C{car_index + 1:02d}"
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").upper()
    normalized = " ".join(re.findall(r"[A-Z]+", normalized))
    if normalized in SPECIAL_CODES:
        return SPECIAL_CODES[normalized]
    parts = normalized.split()
    if not parts:
        return "YOU" if is_player else f"C{car_index + 1:02d}"
    surname = parts[-1]
    return (surname[:3] if len(surname) >= 3 else surname.ljust(3, "_"))


def team_identity(team_id: int | None) -> tuple[str, str, str]:
    return TEAMS.get(team_id, ("Team unknown", "#718395", "#ffffff"))
