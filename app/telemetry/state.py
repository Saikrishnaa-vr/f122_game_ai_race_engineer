"""Freshness-aware player state with shared rival lap history."""

from __future__ import annotations

from dataclasses import dataclass, field
from ..gaps import CheckpointGaps
from ..race_timeline import RaceTimeline

from .payloads import CarDamage, CarStatus, CarTelemetry, Event, Lap, Payload, Session, SessionHistory, decode_payload
from .protocol import PacketHeader
from .session_types import DRIVING_SESSION_TYPES


FRESH_SECONDS = {0: 2.0, 1: 4.0, 2: 2.0, 3: 10.0, 4: 15.0, 5: 15.0, 6: 2.0, 7: 2.0, 10: 4.0, 11: 10.0}
REWIND_SECONDS = 2.0
REWIND_CONFIRM_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class Observation:
    value: Payload
    header: PacketHeader
    observed_at: float


@dataclass(slots=True)
class RaceState:
    session_uid: int | None = None
    player_index: int | None = None
    observations: dict[int, Observation] = field(default_factory=dict)
    resets: int = 0
    reordered: int = 0
    latest_session_time: float | None = None
    rewind_candidate: tuple[float, float] | None = None
    histories: dict[int, Observation] = field(default_factory=dict)
    gaps: CheckpointGaps = field(default_factory=CheckpointGaps)
    timeline: RaceTimeline = field(default_factory=RaceTimeline)

    def apply(self, data: bytes, header: PacketHeader, now: float) -> None:
        # Decode before changing state so malformed input cannot clear it.
        value = decode_payload(data, header)
        if (self.session_uid is not None and header.session_uid != self.session_uid
                and not (isinstance(value, Session) and header.session_uid != 0
                         and value.session_type in DRIVING_SESSION_TYPES)):
            # A stray lobby/menu datagram is not evidence that the race ended.
            return
        if self.session_uid != header.session_uid or self.player_index != header.player_car_index:
            if self.session_uid is not None:
                self.resets += 1
            self.observations.clear()
            self.histories.clear()
            self.gaps.clear()
            self.session_uid = header.session_uid
            self.player_index = header.player_car_index
            self.latest_session_time = None
            self.rewind_candidate = None
            self.timeline.reset()
        if isinstance(value, Event) and value.code == "FLBK":
            self.observations.clear()
            self.histories.clear()
            self.gaps.clear()
            self.resets += 1
            self.latest_session_time = None
            self.rewind_candidate = None
            self.timeline.flashback(header, now, "FLBK")
        elif (self.latest_session_time is not None
              and header.session_time < self.latest_session_time - REWIND_SECONDS):
            candidate = self.rewind_candidate
            if (candidate is None or now - candidate[1] > REWIND_CONFIRM_SECONDS
                    or abs(header.session_time - candidate[0]) > REWIND_CONFIRM_SECONDS):
                # One delayed UDP packet should not erase current observations.
                self.rewind_candidate = (header.session_time, now)
                return
            # Two consecutive packets on a rewound game clock recover a lost
            # FLBK event (or a same-UID restart) without keeping future data.
            self.observations.clear()
            self.histories.clear()
            self.gaps.clear()
            self.resets += 1
            self.latest_session_time = None
            self.rewind_candidate = None
            self.timeline.flashback(header, now, "confirmed_rewind")
        else:
            self.rewind_candidate = None
        if self.latest_session_time is None or header.session_time > self.latest_session_time:
            self.latest_session_time = header.session_time
        if value is None:
            previous_generation = self.timeline.generation
            self.timeline.observe(value, header, now)
            if self.timeline.generation != previous_generation:
                self.gaps.clear()
            return
        if isinstance(value, SessionHistory):
            previous_history = self.histories.get(value.car_index)
            if previous_history is not None:
                difference = (header.frame_identifier - previous_history.header.frame_identifier) & 0xFFFFFFFF
                if difference >= 0x80000000:
                    self.reordered += 1
                    return
            self.histories[value.car_index] = Observation(value, header, now)
            if value.car_index != self.player_index:
                previous_generation = self.timeline.generation
                self.timeline.observe(value, header, now)
                if self.timeline.generation != previous_generation:
                    self.gaps.clear()
                return
        previous = self.observations.get(header.packet_id)
        if previous is not None:
            difference = (header.frame_identifier - previous.header.frame_identifier) & 0xFFFFFFFF
            if difference >= 0x80000000:
                self.reordered += 1
                return
        self.observations[header.packet_id] = Observation(value, header, now)
        previous_generation = self.timeline.generation
        self.timeline.observe(value, header, now)
        if self.timeline.generation != previous_generation:
            self.gaps.clear()
        if isinstance(value, Lap):
            session_obs = self.get_fresh(1, now)
            if session_obs is not None and isinstance(session_obs.value, Session):
                self.gaps.update(value, header.player_car_index, session_obs.value.track_length_m, now)

    def get_fresh_history(self, car_index: int, now: float) -> Observation | None:
        observation = self.histories.get(car_index)
        if (observation is None or now < observation.observed_at
                or now - observation.observed_at >= FRESH_SECONDS[11]):
            return None
        return observation

    def get_fresh(self, packet_id: int, now: float) -> Observation | None:
        observation = self.observations.get(packet_id)
        if observation is None or now < observation.observed_at:
            return None
        if now - observation.observed_at >= FRESH_SECONDS[packet_id]:
            return None
        return observation

    def _label(self, packet_id: int, now: float) -> str:
        observation = self.observations.get(packet_id)
        if observation is None:
            return "unavailable"
        age = max(0.0, now - observation.observed_at)
        if self.get_fresh(packet_id, now) is None:
            return f"stale ({age:.1f}s old)"
        return f"packet {packet_id}, {age:.1f}s old"

    def format_snapshot(self, now: float) -> str:
        """Only show current observations; never substitute older or estimated values."""
        lines = [
            "Telemetry snapshot (decoded values; live HUD verification pending):",
            f"  State resets={self.resets}, ignored older packets={self.reordered}",
        ]
        telemetry = self.get_fresh(6, now)
        if telemetry is not None and isinstance(telemetry.value, CarTelemetry):
            v = telemetry.value
            lines.append(
                f"  Driving: {v.speed_kph} km/h, gear {v.gear}, {v.engine_rpm} rpm, "
                f"throttle {v.throttle * 100:.0f}%, brake {v.brake * 100:.0f}%, DRS {v.drs} "
                f"[{self._label(6, now)}]"
            )
            lines.append(
                f"  Tyre temperatures RL/RR/FL/FR: surface {'/'.join(map(str, v.surface_c))} C, "
                f"inner {'/'.join(map(str, v.inner_c))} C [{self._label(6, now)}]"
            )
        else:
            lines.append(f"  Driving: {self._label(6, now)}")
        lap = self.get_fresh(2, now)
        if lap is not None and isinstance(lap.value, Lap):
            v = lap.value
            lines.append(
                f"  Lap: {v.lap_number}, position {v.position}, current {v.current_lap_ms} ms, "
                f"last {v.last_lap_ms} ms, sectors {v.sector1_ms}/{v.sector2_ms} ms, "
                f"pit status {v.pit_status}, stops {v.pit_stops}, penalties {v.penalties_s} s "
                f"[{self._label(2, now)}]"
            )
        else:
            lines.append(f"  Lap: {self._label(2, now)}")
        status = self.get_fresh(7, now)
        if status is not None and isinstance(status.value, CarStatus):
            v = status.value
            lines.append(
                f"  Fuel: {v.fuel_kg:.2f} kg, game fuel-laps field {v.fuel_remaining_laps:.2f}; "
                f"ERS: {v.ers_joules / 1_000_000:.2f} MJ, deploy mode {v.ers_deploy_mode}; "
                f"tyre actual/visual codes {v.actual_compound}/{v.visual_compound}, "
                f"age {v.tyre_age_laps} laps [{self._label(7, now)}]"
            )
        else:
            lines.append(f"  Fuel/ERS/compound: {self._label(7, now)}")
        damage = self.get_fresh(10, now)
        if damage is not None and isinstance(damage.value, CarDamage):
            wear = damage.value.tyre_wear_percent
            lines.append(
                f"  Tyre wear RL/RR/FL/FR: {wear[0]:.1f}/{wear[1]:.1f}/{wear[2]:.1f}/{wear[3]:.1f}% "
                f"[{self._label(10, now)}]"
            )
            lines.append(
                f"  Wing damage FL/FR/rear: {damage.value.front_left_wing_percent}/"
                f"{damage.value.front_right_wing_percent}/{damage.value.rear_wing_percent}% "
                f"[{self._label(10, now)}]"
            )
        else:
            lines.append(f"  Tyre wear: {self._label(10, now)}")
        session = self.get_fresh(1, now)
        if session is not None and isinstance(session.value, Session):
            v = session.value
            lines.append(
                f"  Session: track code {v.track_id}, weather code {v.weather}, "
                f"track {v.track_c} C, air {v.air_c} C, safety car code {v.safety_car}, "
                f"game pit ideal/latest laps {v.pit_ideal_lap}/{v.pit_latest_lap} "
                f"[{self._label(1, now)}]"
            )
        else:
            lines.append(f"  Session: {self._label(1, now)}")
        return "\n".join(lines)
