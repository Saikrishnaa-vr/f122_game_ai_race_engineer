"""Seekable replay controls preserve the recorded dashboard snapshots."""

import json
import zlib

import pytest

from app.replay_dashboard import ReplayController, ReplayFrame


def frame(elapsed: float, lap: int) -> ReplayFrame:
    payload = {"connection": "receiving", "facts": {"fields": {}, "lap_marker": lap}}
    return ReplayFrame(elapsed, lap, zlib.compress(json.dumps(payload).encode()))


def test_replay_seek_lap_and_speed_keep_original_facts(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.replay_dashboard.time.monotonic", lambda: clock[0])
    controller = ReplayController([frame(5, 1), frame(6, 1), frame(7, 2)], 22.0)
    assert controller.snapshot()["replay"]["index"] == 0
    assert controller.control({"action": "lap", "lap": 2})["facts"]["lap_marker"] == 2
    assert controller.control({"action": "seek", "index": 0})["facts"]["lap_marker"] == 1
    controller.control({"action": "speed", "speed": 2})
    controller.control({"action": "play"})
    clock[0] += 0.6
    assert controller.snapshot()["replay"]["index"] == 1
    controller.control({"action": "pause"})
    clock[0] += 4
    assert controller.snapshot()["replay"]["index"] == 1


@pytest.mark.parametrize("command", [
    {"action": "seek", "index": -1}, {"action": "seek", "index": True},
    {"action": "lap", "lap": 9}, {"action": "speed", "speed": 0},
    {"action": "unknown"}, [],
])
def test_replay_rejects_bad_controls(command):
    controller = ReplayController([frame(0, 1)])
    with pytest.raises(ValueError):
        controller.control(command)
