"""Experimental, read-only monitoring of fresh player telemetry."""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from .telemetry.payloads import CarDamage, CarStatus, Lap, Session
from .telemetry.state import RaceState
from .strategy import StrategyAssumptions, compare_pit_scenarios


@dataclass(frozen=True, slots=True)
class Alert:
    key: str
    priority: int
    message: str


@dataclass(frozen=True, slots=True)
class AlertConfig:
    fuel_kg: float = 2.0
    tyre_wear_percent: float = 70.0
    ers_mj: float = 0.4
    cooldown_seconds: float = 60.0

    def __post_init__(self) -> None:
        values = (self.fuel_kg, self.tyre_wear_percent, self.ers_mj, self.cooldown_seconds)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("alert thresholds and cooldown must be finite")
        if self.fuel_kg < 0 or not 0 <= self.tyre_wear_percent <= 100 or self.ers_mj < 0:
            raise ValueError("invalid alert threshold")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown must be non-negative")


@dataclass(slots=True)
class AlertMonitor:
    config: AlertConfig = field(default_factory=AlertConfig)
    last_emitted: dict[str, float] = field(default_factory=dict)
    state_identity: tuple[int | None, int | None, int] | None = None
    last_now: float | None = None
    previous_weather: int | None = None
    previous_safety_car: int | None = None
    announced_pit_window: tuple[int, int] | None = None
    announced_pit_latest: tuple[int, int] | None = None

    def evaluate(self, state: RaceState, now: float) -> list[Alert]:
        identity = (state.session_uid, state.player_index, state.resets)
        if identity != self.state_identity or (self.last_now is not None and now < self.last_now):
            self.last_emitted.clear()
            self.state_identity = identity
            self.previous_weather = None
            self.previous_safety_car = None
            self.announced_pit_window = None
            self.announced_pit_latest = None
        self.last_now = now

        candidates: list[Alert] = []
        session = state.get_fresh(1, now)
        if session is not None and isinstance(session.value, Session):
            value = session.value
            if value.safety_car in (1, 2) and value.safety_car != self.previous_safety_car:
                kind = "Safety car" if value.safety_car == 1 else "Virtual safety car"
                candidates.append(Alert("safety_car", 0, f"{kind} deployed; reassess pit loss and track conditions."))
            if self.previous_safety_car in (1, 2) and value.safety_car == 0:
                candidates.append(Alert("safety_car_clear", 0, "Safety car period ended; reassess pace and gaps."))
            if self.previous_weather is not None and value.weather != self.previous_weather:
                candidates.append(Alert("weather", 0, f"Weather code changed from {self.previous_weather} to {value.weather}; review tyre choice."))
            if value.weather < 3:
                rain = [sample for sample in value.forecasts
                        if sample.session_type in (10, 11, 12) and sample.weather >= 3
                        and 0 <= sample.offset_minutes <= 15]
                if rain:
                    nearest = min(rain, key=lambda sample: sample.offset_minutes)
                    candidates.append(Alert("rain_forecast", 1,
                                            f"Game forecast indicates rain code {nearest.weather} in "
                                            f"{nearest.offset_minutes} minutes; crossover pace is unknown."))
            self.previous_weather = value.weather
            self.previous_safety_car = value.safety_car
            lap_obs = state.get_fresh(2, now)
            if (value.session_type in (10, 11, 12) and value.safety_car != 3
                    and 1 <= value.pit_ideal_lap <= value.pit_latest_lap <= value.total_laps
                    and lap_obs is not None and isinstance(lap_obs.value, Lap)
                    and lap_obs.value.pit_status == 0):
                window = (value.pit_ideal_lap, value.pit_latest_lap)
                lap_number = lap_obs.value.lap_number
                if (value.pit_ideal_lap <= lap_number <= value.pit_latest_lap
                        and self.announced_pit_window != window):
                    candidates.append(Alert(
                        f"pit_window_{window}", 0,
                        f"Game pit window is open: ideal lap {window[0]}, latest lap {window[1]}. "
                        "Review tyres and traffic before deciding."))
                    self.announced_pit_window = window
                if (window[0] < window[1] and lap_number == window[1]
                        and self.announced_pit_latest != window):
                    candidates.append(Alert(
                        f"pit_latest_{window}", 0,
                        f"Game pit window reaches its latest lap {window[1]}; reassess the stop plan."))
                    self.announced_pit_latest = window
        status = state.get_fresh(7, now)
        if status is not None and isinstance(status.value, CarStatus):
            value = status.value
            if (0 <= value.fuel_kg <= self.config.fuel_kg
                    and value.fuel_kg <= value.fuel_capacity_kg and value.fuel_capacity_kg > 0):
                candidates.append(Alert("fuel", 0, f"Fuel low: {value.fuel_kg:.2f} kg (threshold {self.config.fuel_kg:.2f} kg)."))
            if 0 <= value.ers_joules <= self.config.ers_mj * 1_000_000:
                candidates.append(Alert("ers", 2, f"ERS energy low: {value.ers_joules / 1_000_000:.2f} MJ."))

        damage = state.get_fresh(10, now)
        if damage is not None and isinstance(damage.value, CarDamage):
            wear = damage.value.tyre_wear_percent
            wing_damage = max(damage.value.front_left_wing_percent, damage.value.front_right_wing_percent,
                              damage.value.rear_wing_percent)
            if wing_damage >= 50:
                candidates.append(Alert("wing_damage", -1, f"Significant wing damage observed: {wing_damage}%."))
            if all(0 <= value <= 100 for value in wear):
                index = max(range(4), key=wear.__getitem__)
                if wear[index] >= self.config.tyre_wear_percent:
                    wheel = ("rear-left", "rear-right", "front-left", "front-right")[index]
                    candidates.append(Alert("tyre", 1, f"Tyre wear: {wheel} {wear[index]:.1f}%."))

        alerts = []
        for alert in sorted(candidates, key=lambda item: item.priority):
            previous = self.last_emitted.get(alert.key)
            if previous is None or now - previous >= self.config.cooldown_seconds:
                self.last_emitted[alert.key] = now
                alerts.append(alert)
        return alerts


@dataclass(frozen=True, slots=True)
class LapSample:
    lap: int
    distance_m: float
    fuel_kg: float
    wear: tuple[float, float, float, float]
    compound: int
    tyre_age: int
    pit_stops: int
    time: float
    ers_joules: float


@dataclass(slots=True)
class DiagnosticMonitor:
    wear_target_percent: float = 70.0
    identity: tuple[int | None, int | None, int, int] | None = None
    sample: LapSample | None = None
    wear_rate: tuple[float, float, float, float] | None = None
    previous_wear_rate: tuple[float, float, float, float] | None = None
    fuel_rate: float | None = None
    ers_delta_mj_per_lap: float | None = None
    last_now: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.wear_target_percent) or not 0 < self.wear_target_percent <= 100:
            raise ValueError("wear target must be greater than zero and at most 100")

    def evaluate(self, state: RaceState, now: float) -> list[str]:
        identity = (state.session_uid, state.player_index, state.resets,
                    state.timeline.generation)
        if identity != self.identity or (self.last_now is not None and now < self.last_now):
            self.sample = None
            self.wear_rate = None
            self.previous_wear_rate = None
            self.fuel_rate = None
            self.ers_delta_mj_per_lap = None
            self.identity = identity
        self.last_now = now

        if (state.timeline.last_packet_at is not None
                and state.timeline.status(now) != "GREEN"):
            self.sample = None
            self.wear_rate = None
            self.previous_wear_rate = None
            self.fuel_rate = None
            self.ers_delta_mj_per_lap = None
            return ["Estimates unavailable until fresh green-flag laps are observed."]

        observations = [state.get_fresh(packet_id, now) for packet_id in (1, 2, 7, 10)]
        if any(observation is None for observation in observations):
            self.sample = None
            self.wear_rate = None
            self.previous_wear_rate = None
            self.fuel_rate = None
            self.ers_delta_mj_per_lap = None
            return ["Estimates unavailable: fresh session, lap, status and damage required."]
        session, lap, status, damage = (observation.value for observation in observations if observation is not None)
        if not (isinstance(session, Session) and isinstance(lap, Lap)
                and isinstance(status, CarStatus) and isinstance(damage, CarDamage)):
            return ["Estimates unavailable: unexpected telemetry types."]

        wear = damage.tyre_wear_percent
        # Track length is transmitted as an integer, while lap distance is a
        # float and can briefly exceed it by less than a metre at the line.
        valid = (session.track_length_m > 0 and lap.lap_number > 0
                 and 0 <= lap.lap_distance_m <= session.track_length_m + 2.0
                 and 0 <= status.fuel_kg <= status.fuel_capacity_kg
                 and all(0 <= value <= 100 for value in wear))
        if not valid or lap.pit_status != 0:
            self.sample = None
            self.wear_rate = None
            self.previous_wear_rate = None
            self.fuel_rate = None
            self.ers_delta_mj_per_lap = None
            return ["Estimates unavailable: invalid values or car in pit lane."]

        previous = self.sample
        if previous is not None and (
            lap.lap_number < previous.lap or lap.lap_number > previous.lap + 1
            or status.actual_compound != previous.compound or status.tyre_age_laps < previous.tyre_age
            or lap.pit_stops != previous.pit_stops or now - previous.time > 600
            or any(current < old for current, old in zip(wear, previous.wear))
            or status.fuel_kg > previous.fuel_kg + 0.05
        ):
            self.sample = None
            self.wear_rate = None
            self.previous_wear_rate = None
            self.fuel_rate = None
            self.ers_delta_mj_per_lap = None
            previous = None

        if lap.lap_distance_m <= min(250.0, session.track_length_m * 0.1) and (
            previous is None or lap.lap_number > previous.lap
        ):
            current = LapSample(lap.lap_number, lap.lap_distance_m, status.fuel_kg, wear,
                                status.actual_compound, status.tyre_age_laps, lap.pit_stops, now,
                                status.ers_joules)
            previous_wear_rate = self.wear_rate
            self.wear_rate = None
            self.previous_wear_rate = None
            self.fuel_rate = None
            self.ers_delta_mj_per_lap = None
            if previous is not None and current.lap == previous.lap + 1 and abs(current.distance_m - previous.distance_m) <= 100:
                self.wear_rate = tuple(new - old for new, old in zip(current.wear, previous.wear))
                self.previous_wear_rate = previous_wear_rate
                self.fuel_rate = previous.fuel_kg - current.fuel_kg
                self.ers_delta_mj_per_lap = (current.ers_joules - previous.ers_joules) / 1_000_000
            self.sample = current

        lines = []
        if (session.session_type in (10, 11, 12) and session.safety_car != 3
                and 1 <= lap.lap_number <= session.total_laps and self.fuel_rate is not None and self.fuel_rate > 0):
            lap_fraction = min(lap.lap_distance_m / session.track_length_m, 1.0)
            remaining = session.total_laps - lap.lap_number + 1 - lap_fraction
            margin = status.fuel_kg / self.fuel_rate - remaining
            lines.append(f"Approximate race fuel margin: {margin:+.2f} laps (one-lap consumption extrapolation; unverified).")
        else:
            lines.append("Race fuel margin unavailable (need race data and a positive lap-use estimate).")

        if self.fuel_rate is not None and self.fuel_rate >= 0:
            lines.append(f"Approximate fuel use: {self.fuel_rate:.2f} kg/lap from one pair of lap-start samples.")
        else:
            lines.append("Fuel use unavailable: need comparable consecutive lap-start samples.")

        projected = [max(0.0, (self.wear_target_percent - current) / rate)
                     for current, rate in zip(wear, self.wear_rate or ()) if rate > 0]
        if projected:
            distance_left = min(projected)
            projected_lap = lap.lap_number + lap.lap_distance_m / session.track_length_m + distance_left
            lines.append(f"Wear threshold projection: lap {projected_lap:.1f} at {self.wear_target_percent:.0f}% "
                         "(one-lap linear estimate; not a pit recommendation).")
        else:
            lines.append("Wear threshold projection unavailable: need comparable consecutive lap-start samples with increasing wear.")
        if self.wear_rate is not None and self.previous_wear_rate is not None:
            early = []
            late = []
            for current, recent, earlier in zip(wear, self.wear_rate, self.previous_wear_rate):
                remaining = max(0.0, self.wear_target_percent - current)
                if remaining == 0:
                    early.append(0.0)
                    late.append(0.0)
                else:
                    fastest, slowest = max(recent, earlier), min(recent, earlier)
                    if fastest > 0:
                        early.append(remaining / fastest)
                    if slowest > 0:
                        late.append(remaining / slowest)
            if early and late:
                position = lap.lap_number + lap.lap_distance_m / session.track_length_m
                lines.append(f"Indicative wear-threshold window: laps {position + min(early):.1f}-{position + min(late):.1f} "
                             "(range of two observed lap rates; not a pit recommendation).")
            else:
                lines.append("Wear-threshold window unavailable: observed rates do not provide both bounds.")
        else:
            lines.append("Wear-threshold window unavailable: need three comparable consecutive lap-start samples.")
        if self.ers_delta_mj_per_lap is not None:
            lines.append(f"Observed ERS net change: {self.ers_delta_mj_per_lap:+.2f} MJ over the last comparable lap; "
                         "harvest and deployment causes are not isolated.")
        else:
            lines.append("ERS trend unavailable: need comparable consecutive lap-start samples.")
        return lines


@dataclass(slots=True)
class Engineer:
    alerts: AlertMonitor = field(default_factory=AlertMonitor)
    diagnostics: DiagnosticMonitor = field(default_factory=DiagnosticMonitor)
    strategy_assumptions: StrategyAssumptions = field(default_factory=StrategyAssumptions)
    pending_alerts: list[Alert] = field(default_factory=list)

    def on_packet(self, state: RaceState, now: float) -> list[Alert]:
        # Lap-start distance lasts only a few seconds at racing speed. Sampling
        # here keeps estimates available even when periodic reports miss it.
        self.diagnostics.evaluate(state, now)
        new = self.alerts.evaluate(state, now)
        self.pending_alerts.extend(new)
        return new

    def report(self, state: RaceState, now: float) -> str:
        alerts = sorted(self.pending_alerts + self.alerts.evaluate(state, now),
                        key=lambda item: item.priority)
        self.pending_alerts.clear()
        lines = ["Experimental engineer (unverified; advisory only):"]
        if alerts:
            for alert in alerts:
                priority = "critical" if alert.priority < 0 else "high" if alert.priority == 0 else "normal"
                lines.append(f"  Alert [{priority}]: {alert.message}")
        else:
            lines.append("  No new alerts.")
        session = state.get_fresh(1, now)
        if (session is not None and isinstance(session.value, Session)
                and session.value.session_type in (10, 11, 12) and session.value.safety_car != 3
                and 1 <= session.value.pit_ideal_lap <= session.value.pit_latest_lap <= session.value.total_laps):
            lines.append(f"  Game pit strategy: ideal lap {session.value.pit_ideal_lap}, "
                         f"latest lap {session.value.pit_latest_lap} (unverified game values).")
        else:
            lines.append("  Game pit strategy unavailable.")
        lines.extend(f"  {line}" for line in self.diagnostics.evaluate(state, now))
        advice = compare_pit_scenarios(state, now, self.diagnostics.wear_rate,
                                       self.strategy_assumptions)
        lines.extend(f"  {line}" for line in advice.format().splitlines())
        return "\n".join(lines)
