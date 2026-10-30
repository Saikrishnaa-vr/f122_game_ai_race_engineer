"""Dashboard analysis must not read mutable receiver state across threads."""

import json
from threading import Event, Thread

import app.dashboard_worker as worker_module
from app.dashboard import DashboardState
from app.dashboard_worker import DashboardPublisher
from app.engineer import Engineer
from app.telemetry.state import RaceState


def test_publication_uses_copied_state_and_wakes_waiting_client() -> None:
    dashboard = DashboardState()
    publisher = DashboardPublisher(dashboard)
    state = RaceState(session_uid=17, player_index=2)
    started = Event()
    result: list[tuple[int, str]] = []

    def wait() -> None:
        started.set()
        result.append(dashboard.wait_for_newer(0, timeout=2))

    waiter = Thread(target=wait)
    waiter.start()
    try:
        assert started.wait(timeout=2)
        publisher.submit(state, Engineer(), "receiving", False, "off")
        state.session_uid = 18  # Receiver continues while the worker publishes.
        state.timeline.reset()
        waiter.join(timeout=3)
        assert not waiter.is_alive()
        assert result[0][0] == 1
        facts = json.loads(result[0][1])["facts"]
        assert facts["session_uid"] == 17
        assert publisher.last_copy_ms >= 0
        assert publisher.last_queue_ms >= 0
    finally:
        publisher.close()
        waiter.join(timeout=3)
    assert publisher.published == 1


def test_failed_display_frame_does_not_stop_later_publication(monkeypatch) -> None:
    dashboard = DashboardState()
    publisher = DashboardPublisher(dashboard)
    original = worker_module.race_facts
    first_failed = Event()
    calls = 0

    def sometimes_fail(state, engineer, now):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_failed.set()
            raise RuntimeError("synthetic display failure")
        return original(state, engineer, now)

    monkeypatch.setattr(worker_module, "race_facts", sometimes_fail)
    try:
        publisher.submit(RaceState(session_uid=1), Engineer(), "receiving", False, "off")
        assert first_failed.wait(timeout=2)
        publisher.submit(RaceState(session_uid=2), Engineer(), "receiving", False, "off")
        version, payload = dashboard.wait_for_newer(0, timeout=2)
        assert version == 1
        assert json.loads(payload)["facts"]["session_uid"] == 2
    finally:
        publisher.close()
    assert publisher.published == 1
