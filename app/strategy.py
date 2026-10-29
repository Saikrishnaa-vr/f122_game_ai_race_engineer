"""Conditional pit scenarios based on fresh telemetry and explicit assumptions.

The model compares relative time costs, not absolute lap times. It never treats
the game's pit plan or an arbitrary tyre threshold as an independent estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .telemetry.payloads import CarDamage, CarStatus, Lap, Session
from .telemetry.state import RaceState


@dataclass(frozen=True, slots=True)
class StrategyAssumptions:
    pit_loss_seconds: float | None = None
    fresh_tyre_gain_seconds_per_lap: float | None = None
    wear_cost_seconds_per_percent: float | None = None
    next_compound_code: int | None = None
    uncertainty_seconds_per_lap: float = 0.5
    safety_car_pit_loss_factor: float | None = None
    traffic_loss_seconds: float | None = None
    second_compound_code: int | None = None
    profile: str = "balanced"
    vsc_pit_loss_factor: float | None = None

    def __post_init__(self) -> None:
        for value in (self.pit_loss_seconds, self.fresh_tyre_gain_seconds_per_lap,
                      self.wear_cost_seconds_per_percent, self.uncertainty_seconds_per_lap,
                      self.safety_car_pit_loss_factor, self.vsc_pit_loss_factor,
                      self.traffic_loss_seconds):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("strategy assumptions must be finite and non-negative")
        if self.next_compound_code is not None and not 0 <= self.next_compound_code <= 255:
            raise ValueError("next compound code must fit in a byte")
        if self.safety_car_pit_loss_factor is not None and self.safety_car_pit_loss_factor > 1:
            raise ValueError("safety car pit-loss factor must be between zero and one")
        if self.vsc_pit_loss_factor is not None and self.vsc_pit_loss_factor > 1:
            raise ValueError("VSC pit-loss factor must be between zero and one")
        if self.second_compound_code is not None and not 0 <= self.second_compound_code <= 255:
            raise ValueError("second compound code must fit in a byte")
        if self.profile not in ("conservative", "balanced", "aggressive"):
            raise ValueError("strategy profile must be conservative, balanced or aggressive")


@dataclass(frozen=True, slots=True)
class PitScenario:
    action: str
    pit_lap: int | None
    relative_cost_seconds: float
    projected_end_wear_percent: float


@dataclass(frozen=True, slots=True)
class StrategyAdvice:
    recommendation: str
    expected_benefit_seconds: float | None
    alternative: str | None
    uncertainty_seconds: float | None
    risks: tuple[str, ...]
    assumptions: tuple[str, ...]
    scenarios: tuple[PitScenario, ...] = ()
    pit_window_laps: tuple[int, int] | None = None

    def format(self) -> str:
        lines = [f"Pit strategy: {self.recommendation}"]
        if self.expected_benefit_seconds is not None:
            lines.append(f"  Modelled advantage: {self.expected_benefit_seconds:.1f} s; "
                         f"uncertainty at least +/-{self.uncertainty_seconds:.1f} s.")
        if self.alternative:
            lines.append(f"  Alternative: {self.alternative}")
        if self.pit_window_laps:
            start, end = self.pit_window_laps
            lines.append(f"  Modelled pit window: laps {start}-{end} within the uncertainty band.")
        if self.assumptions:
            lines.append("  Assumptions: " + "; ".join(self.assumptions))
        if self.risks:
            lines.append("  Risks: " + "; ".join(self.risks))
        return "\n".join(lines)


def compare_pit_scenarios(
    state: RaceState, now: float, wear_rate: tuple[float, float, float, float] | None,
    assumptions: StrategyAssumptions,
) -> StrategyAdvice:
    """Compare a no-stop finish with one-stop choices, or explain missing inputs.

    The calculation assumes a constant observed wear rate and explicitly
    configured pace/pit-loss terms. It cannot model regulations, compound
    availability, rivals' tyre state or a future weather crossover.
    """
    observations = (state.get_fresh(packet_id, now) for packet_id in (1, 2, 7, 10))
    session_obs, lap_obs, status_obs, damage_obs = observations
    if any(item is None for item in (session_obs, lap_obs, status_obs, damage_obs)):
        return StrategyAdvice("unavailable: fresh session, lap, status and damage data required",
                              None, None, None, (), ())
    session, lap, status, damage = (item.value for item in
                                    (session_obs, lap_obs, status_obs, damage_obs) if item is not None)
    if not (isinstance(session, Session) and isinstance(lap, Lap)
            and isinstance(status, CarStatus) and isinstance(damage, CarDamage)):
        return StrategyAdvice("unavailable: unsupported telemetry values", None, None, None, (), ())
    if (session.session_type not in (10, 11, 12) or session.total_laps <= 0
            or not 1 <= lap.lap_number <= session.total_laps or lap.pit_status != 0
            or session.track_length_m <= 0 or not 0 <= lap.lap_distance_m <= session.track_length_m + 2.0):
        return StrategyAdvice("unavailable outside a valid active race stint", None, None, None, (), ())
    if wear_rate is None or not all(math.isfinite(rate) and rate >= 0 for rate in wear_rate):
        return StrategyAdvice("unavailable: comparable tyre-wear laps required", None, None, None, (), ())
    if not all(0 <= wear <= 100 for wear in damage.tyre_wear_percent):
        return StrategyAdvice("unavailable: tyre-wear data is invalid", None, None, None, (), ())
    missing = [name for name, value in (
        ("pit loss", assumptions.pit_loss_seconds),
        ("fresh-tyre pace gain", assumptions.fresh_tyre_gain_seconds_per_lap),
        ("wear pace cost", assumptions.wear_cost_seconds_per_percent),
        ("next compound", assumptions.next_compound_code),
    ) if value is None]
    if missing:
        return StrategyAdvice("unavailable: configure " + ", ".join(missing),
                              None, None, None, (), ())
    if session.safety_car not in (0, 1, 2):
        return StrategyAdvice("unavailable: unsupported safety-car state", None, None, None, (), ())
    neutralized_factor = (assumptions.safety_car_pit_loss_factor if session.safety_car == 1 else
                          assumptions.vsc_pit_loss_factor if session.safety_car == 2 else 1.0)
    if neutralized_factor is None:
        kind = "safety-car" if session.safety_car == 1 else "VSC"
        return StrategyAdvice(f"unavailable: {kind} pit loss has not been calibrated",
                              None, None, None, (), ())
    if any(sample.weather >= 3 and sample.offset_minutes <= 30
           for sample in session.forecasts if sample.session_type in (10, 11, 12)) and session.weather < 3:
        return StrategyAdvice("defer a dry-tyre calculation: rain is forecast within 30 minutes",
                              None, None, None,
                              ("forecast timing and crossover pace are uncalibrated",), ())

    remaining = session.total_laps - lap.lap_number + 1
    wear = max(damage.tyre_wear_percent)
    rate = max(wear_rate)
    pit_loss = assumptions.pit_loss_seconds or 0.0
    pit_loss *= neutralized_factor
    gain = assumptions.fresh_tyre_gain_seconds_per_lap or 0.0
    wear_cost = assumptions.wear_cost_seconds_per_percent or 0.0
    traffic_loss = assumptions.traffic_loss_seconds or 0.0
    max_wear = {"conservative": 75, "balanced": 85, "aggressive": 95}[assumptions.profile]
    scenarios: list[PitScenario] = []
    def old_cost(laps: int, starting_wear: float) -> float:
        return (laps * starting_wear + rate * laps * (laps - 1) / 2) * wear_cost

    def fresh_cost(laps: int) -> float:
        return rate * laps * (laps - 1) / 2 * wear_cost - gain * laps

    no_stop_cost = old_cost(remaining, wear)
    if wear + rate * remaining <= max_wear:
        scenarios.append(PitScenario("stay out to finish", None, no_stop_cost, wear + rate * remaining))
    for old_laps in range(1, remaining):
        before_cost = old_cost(old_laps, wear)
        after_cost = fresh_cost(remaining - old_laps)
        end_wear = rate * (remaining - old_laps)
        if wear + rate * old_laps <= max_wear and end_wear <= max_wear:
            scenarios.append(PitScenario("box this lap" if old_laps == 1 else f"box in {old_laps - 1} lap(s)",
                                         lap.lap_number + old_laps - 1,
                                         pit_loss + traffic_loss + before_cost + after_cost,
                                         end_wear))
    if assumptions.second_compound_code is not None and remaining >= 3:
        for first in range(1, remaining - 1):
            for second in range(first + 1, remaining):
                first_wear = wear + rate * first
                second_wear = rate * (second - first)
                final_wear = rate * (remaining - second)
                if max(first_wear, second_wear, final_wear) > max_wear:
                    continue
                old = old_cost(first, wear)
                middle = fresh_cost(second - first)
                final = fresh_cost(remaining - second)
                scenarios.append(PitScenario(f"two stops: laps {lap.lap_number + first - 1} and {lap.lap_number + second - 1}",
                                             lap.lap_number + first - 1,
                                             2 * (pit_loss + traffic_loss) + old + middle + final,
                                             final_wear))
    if not scenarios:
        return StrategyAdvice("unavailable: no modelled plan stays below the profile wear limit", None, None, None,
                              ("actual safe tyre life is uncalibrated",), (f"profile {assumptions.profile}",))
    scenarios.sort(key=lambda item: item.relative_cost_seconds)
    if len(scenarios) == 1:
        return StrategyAdvice("one feasible modelled plan: " + scenarios[0].action, None, None, None,
                              ("no comparable alternative",), (f"profile {assumptions.profile}",), tuple(scenarios))
    best, alternative = scenarios[:2]
    advantage = alternative.relative_cost_seconds - best.relative_cost_seconds
    uncertainty = max(2.0, assumptions.uncertainty_seconds_per_lap * remaining)
    recommendation = (best.action if advantage > uncertainty
                      else "no decisive pit call: modelled alternatives overlap")
    near_pits = [plan.pit_lap for plan in scenarios if plan.pit_lap is not None
                 and not plan.action.startswith("two stops")
                 and plan.relative_cost_seconds - best.relative_cost_seconds <= uncertainty]
    window = (min(near_pits), max(near_pits)) if near_pits else None
    shown = scenarios[:10]
    for kind in ("stay out", "box this lap", "two stops"):
        representative = next((plan for plan in scenarios if plan.action.startswith(kind)), None)
        if representative is not None and representative not in shown:
            shown.append(representative)
    return StrategyAdvice(
        recommendation, advantage, alternative.action, uncertainty,
        ("rival stops and rejoin position are not modelled", "compound availability and race rules need driver confirmation",
         "constant wear and pace assumptions may fail after weather or safety-car changes"),
        (f"pit loss {pit_loss:.1f} s", f"fresh-tyre gain {gain:.2f} s/lap",
         f"wear cost {wear_cost:.3f} s/percentage point", f"next compound code {assumptions.next_compound_code}",
         f"traffic allowance {traffic_loss:.1f} s", f"profile {assumptions.profile}, wear limit {max_wear}%"),
        tuple(shown), window,
    )
