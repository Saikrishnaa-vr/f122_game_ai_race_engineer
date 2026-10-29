"""Observed race-control and telemetry continuity events shared by live and replay.

The 2022 protocol reports SC/VSC in Session packets, not in a dedicated
Safety Car event. Gaps describe missing observations, never inferred motion.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .telemetry.payloads import Event, Lap, Payload, Session
from .telemetry.protocol import PacketHeader


STALE_SECONDS = 5.0
SESSION_FRESH_SECONDS = 4.0
FORWARD_JUMP_MIN_SECONDS = 5.0
GAME_TIME_SKEW_SECONDS = 3.0
PHASES = {0: "GREEN", 1: "SC", 2: "VSC"}


@dataclass(slots=True)
class RaceTimeline:
    """Keep only protocol-supported transitions and bounded recent evidence."""

    events: deque[dict] = field(default_factory=lambda: deque(maxlen=40))
    generation: int = 0
    epoch: int = 0
    last_packet_at: float | None = None
    last_session_at: float | None = None
    last_session_time: float | None = None
    last_lap: int | None = None
    last_pit_status: int | None = None
    last_safety_code: int | None = None
    green_lap_baseline: int | None = None
    pace_ready: bool = False

    def reset(self) -> None:
        self.events.clear()
        self.generation += 1
        self.epoch = 0
        self.last_packet_at = None
        self.last_session_at = None
        self.last_session_time = None
        self.last_lap = None
        self.last_pit_status = None
        self.last_safety_code = None
        self.green_lap_baseline = None
        self.pace_ready = False

    def _add(self, kind: str, header: PacketHeader, now: float, **details: object) -> None:
        self.events.append({"kind": kind, "observed_at": now,
                            "session_time": round(header.session_time, 3),
                            "lap": self.last_lap, "epoch": self.epoch,
                            "source_packet_id": header.packet_id, **details})

    def flashback(self, header: PacketHeader, now: float, source: str) -> None:
        self.epoch += 1
        self.generation += 1
        self.last_session_at = None
        self.last_session_time = None
        self.last_lap = None
        self.last_pit_status = None
        self.last_safety_code = None
        self.green_lap_baseline = None
        self.pace_ready = False
        self._add("flashback" if source == "FLBK" else "clock_rewind", header, now,
                  source=source)

    def status(self, now: float) -> str:
        if self.last_packet_at is None or now - self.last_packet_at >= STALE_SECONDS:
            return "STALE"
        if (self.last_session_at is None or now - self.last_session_at >= SESSION_FRESH_SECONDS):
            return "UNKNOWN"
        return PHASES.get(self.last_safety_code, "UNKNOWN")

    def observe(self, value: Payload | None, header: PacketHeader, now: float) -> None:
        if self.last_packet_at is not None:
            arrival_gap = now - self.last_packet_at
            game_gap = (header.session_time - self.last_session_time
                        if self.last_session_time is not None else 0.0)
            lap_jump = (value.lap_number - self.last_lap
                        if isinstance(value, Lap) and self.last_lap is not None else 0)
            accelerated_clock = game_gap > max(FORWARD_JUMP_MIN_SECONDS,
                                               arrival_gap + GAME_TIME_SKEW_SECONDS)
            if arrival_gap >= STALE_SECONDS or accelerated_clock or lap_jump > 1:
                self.generation += 1
                self.green_lap_baseline = None
                self.pace_ready = False
                self._add("unobserved_interval", header, now,
                          from_observed_at=self.last_packet_at,
                          arrival_gap_s=round(max(0.0, arrival_gap), 3),
                          game_time_gap_s=round(max(0.0, game_gap), 3),
                          lap_jump=max(0, lap_jump))
        self.last_packet_at = now
        self.last_session_time = header.session_time
        if isinstance(value, Session):
            previous = self.last_safety_code
            self.last_session_at = now
            self.last_safety_code = value.safety_car
            if previous != value.safety_car:
                self.generation += 1
                self.green_lap_baseline = None
                self.pace_ready = False
                kind = ("restart" if previous in (1, 2) and value.safety_car == 0 else
                        "sc_active" if value.safety_car == 1 else
                        "vsc_active" if value.safety_car == 2 else
                        "green_observed" if value.safety_car == 0 else "status_unknown")
                self._add(kind, header, now, previous_code=previous,
                          safety_code=value.safety_car)
        elif isinstance(value, Lap):
            previous = self.last_pit_status
            self.last_lap = value.lap_number
            self.last_pit_status = value.pit_status
            if self.status(now) == "GREEN" and value.pit_status == 0:
                if (self.green_lap_baseline is not None
                        and value.lap_number == self.green_lap_baseline + 1):
                    self.pace_ready = True
                if self.green_lap_baseline != value.lap_number:
                    self.green_lap_baseline = value.lap_number
            else:
                self.green_lap_baseline = None
                self.pace_ready = False
            if previous == 0 and value.pit_status in (1, 2):
                self._add("pit_entry", header, now, race_status=self.status(now))
            elif previous in (1, 2) and value.pit_status == 0:
                self._add("pit_exit", header, now, race_status=self.status(now))
        elif isinstance(value, Event) and value.code == "SEND":
            self._add("session_ended", header, now)

    def snapshot(self, now: float) -> dict:
        return {"status": self.status(now),
                "last_confirmed": PHASES.get(self.last_safety_code, "UNKNOWN"),
                "last_packet_age_s": (round(max(0.0, now - self.last_packet_at), 2)
                                      if self.last_packet_at is not None else None),
                "generation": self.generation, "epoch": self.epoch,
                "pace_ready": self.pace_ready,
                "events": list(self.events)[-12:]}
