"""Build dashboard facts outside the UDP receive loop from bounded snapshots."""

from __future__ import annotations

from collections import deque
from copy import copy
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Thread
import time

from .dashboard import DashboardState
from .engineer import Engineer
from .gaps import CheckpointGaps
from .intelligence import race_facts
from .logging_setup import LOGGER
from .telemetry.state import RaceState


@dataclass(frozen=True, slots=True)
class Publication:
    state: RaceState
    engineer: Engineer
    submitted_at: float
    connection: str
    performance_mode: bool
    voice_status: str


def copy_for_publication(state: RaceState, engineer: Engineer) -> tuple[RaceState, Engineer]:
    """Copy mutable containers; decoded payloads and observations are frozen."""
    view = copy(state)
    view.observations = state.observations.copy()
    view.histories = state.histories.copy()
    view.gaps = CheckpointGaps(state.gaps.previous.copy(),
                               {car: deque(points, maxlen=points.maxlen)
                                for car, points in state.gaps.crossings.items()},
                               state.gaps.last_update)
    view.timeline = copy(state.timeline)
    view.timeline.events = deque(state.timeline.events, maxlen=state.timeline.events.maxlen)
    analysis = copy(engineer)
    analysis.diagnostics = copy(engineer.diagnostics)
    return view, analysis


class DashboardPublisher:
    """Coalesce stale display snapshots; never drop or reorder raw UDP packets."""

    def __init__(self, dashboard: DashboardState) -> None:
        self.dashboard = dashboard
        self.queue: Queue[Publication] = Queue(maxsize=1)
        self.stop = Event()
        self.dropped_snapshots = 0
        self.published = 0
        self.last_copy_ms = 0.0
        self.last_queue_ms = 0.0
        self.last_build_ms = 0.0
        self.last_source_age_ms: float | None = None
        self.thread = Thread(target=self._run, name="race-dashboard-publisher", daemon=True)
        self.thread.start()

    def submit(self, state: RaceState, engineer: Engineer, connection: str,
               performance_mode: bool, voice_status: str) -> None:
        started = time.perf_counter()
        state_view, engineer_view = copy_for_publication(state, engineer)
        self.last_copy_ms = (time.perf_counter() - started) * 1000
        item = Publication(state_view, engineer_view, time.monotonic(), connection,
                           performance_mode, voice_status)
        try:
            self.queue.put_nowait(item)
        except Full:
            try:
                self.queue.get_nowait()
                self.dropped_snapshots += 1
            except Empty:
                pass
            self.queue.put_nowait(item)

    def _run(self) -> None:
        next_report = time.monotonic() + 30.0
        while not self.stop.is_set() or not self.queue.empty():
            try:
                item = self.queue.get(timeout=0.2)
            except Empty:
                continue
            now = time.monotonic()
            self.last_queue_ms = max(0.0, (now - item.submitted_at) * 1000)
            started = time.perf_counter()
            try:
                facts = race_facts(item.state, item.engineer, now)
                self.dashboard.publish(facts, item.connection, item.performance_mode,
                                       item.voice_status)
            except Exception:
                # A bad dashboard frame must not terminate publication for the race.
                LOGGER.exception("telemetry event=dashboard_publish_error")
            else:
                self.published += 1
            self.last_build_ms = (time.perf_counter() - started) * 1000
            self.last_source_age_ms = (
                max(0.0, (time.monotonic() - item.state.timeline.last_packet_at) * 1000)
                if item.state.timeline.last_packet_at is not None else None)
            if now >= next_report:
                self.report()
                next_report = now + 30.0

    def report(self) -> None:
        LOGGER.info("telemetry event=dashboard_health published=%d coalesced=%d "
                    "copy_ms=%.3f queue_ms=%.3f build_ms=%.3f "
                    "source_to_publish_ms=%s",
                    self.published, self.dropped_snapshots, self.last_copy_ms,
                    self.last_queue_ms, self.last_build_ms,
                    round(self.last_source_age_ms, 2)
                    if self.last_source_age_ms is not None else None)

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=5)
        self.report()
        if self.thread.is_alive():
            LOGGER.warning("telemetry event=dashboard_publisher_still_running pending=%d",
                           self.queue.qsize())
