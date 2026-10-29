"""Bounded, explicitly approximate pit rejoin projections from shared race data."""

from __future__ import annotations

from .strategy import StrategyAssumptions
from .telemetry.payloads import Lap, Session


def project_rejoin(grid: list[dict], lap: Lap, session: Session,
                   assumptions: StrategyAssumptions) -> dict:
    """Estimate ordering after a stop; never imply knowledge of rival pit plans."""
    unavailable = lambda reason: {"status": "unavailable", "reason": reason}
    if session.session_type not in (10, 11, 12) or lap.pit_status or lap.lap_number >= session.total_laps:
        return unavailable("active race stint with laps remaining required")
    if assumptions.pit_loss_seconds is None:
        return unavailable("set a measured pit loss with --pit-loss")
    if session.safety_car not in (0, 1, 2):
        return unavailable("safety-car state is unknown")
    neutralized_factor = (assumptions.safety_car_pit_loss_factor if session.safety_car == 1 else
                          assumptions.vsc_pit_loss_factor if session.safety_car == 2 else 1.0)
    if neutralized_factor is None:
        return unavailable("set a measured pit-loss factor for the current SC/VSC state")
    player = next((row for row in grid if row["is_player"]), None)
    rivals = [row for row in grid if not row["is_player"] and not row.get("out_of_race")
              and row.get("result_status") not in (4, 5, 7)
              and row.get("gap_seconds", row["estimated_gap_seconds"]) is not None]
    if player is None or len(rivals) < 5:
        return unavailable("shared gaps for at least five rivals and a valid player lap required")
    pit_loss = assumptions.pit_loss_seconds * neutralized_factor
    assert pit_loss is not None
    uncertainty = max(3.0, assumptions.uncertainty_seconds_per_lap * 3)
    player_lap_ms = player["last_lap_ms"]

    def scenario(next_lap: bool) -> dict:
        projected = []
        pace_samples = 0
        for row in rivals:
            gap = row.get("gap_seconds", row["estimated_gap_seconds"])
            if next_lap and player_lap_ms and row["last_lap_ms"]:
                gap -= (row["last_lap_ms"] - player_lap_ms) / 1000
                pace_samples += 1
            projected.append(gap + pit_loss)
        spread = uncertainty + (2.0 if next_lap else 0.0)
        return {"position": 1 + sum(value > 0 for value in projected),
                "range": [1 + sum(value > spread for value in projected),
                          1 + sum(value > -spread for value in projected)],
                "rivals_modelled": len(projected), "pace_samples": pace_samples}

    return {"status": "estimate", "box_now": scenario(False),
            "box_next_lap": scenario(True) if session.safety_car == 0 else None,
            "pit_loss_seconds": round(pit_loss, 1),
            "uncertainty_seconds": round(uncertainty, 1),
            "method": ("same-checkpoint gaps where available, distance fallback, plus measured pit loss; "
                       "next lap uses last valid lap pace only under green"),
            "caveat": "Rival stops, traffic, changing pace and exact timing are unknown; positions are indicative."}
