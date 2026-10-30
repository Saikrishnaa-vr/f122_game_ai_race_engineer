"""Offline analytics consumes copies of received packets, never game state."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import struct

import pytest

from app import analytics
from app.telemetry import capture
from app.telemetry.protocol import PACKET_TYPES


def _packet(factory: Callable[..., bytes], packet_id: int, *, frame: int,
            time: float, uid: int = 101) -> bytearray:
    return bytearray(factory(packet_id, size=PACKET_TYPES[packet_id][1],
                             frame=frame, session_time=time, session_uid=uid))


def _session(factory, frame: int, time: float, uid: int = 101) -> bytes:
    raw = _packet(factory, 1, frame=frame, time=time, uid=uid)
    struct.pack_into("<BbbBHBbBHH6B", raw, 24, 0, 30, 25, 10, 5412, 10, 3,
                     0, 900, 900, 80, 0, 0, 0, 0, 0)
    return bytes(raw)


def _lap(factory, frame: int, time: float, lap: int, *, uid: int = 101,
         last_ms: int = 90_000) -> bytes:
    raw = _packet(factory, 2, frame=frame, time=time, uid=uid)
    base = 24 + 2 * 43
    struct.pack_into("<IIHHfff", raw, base, last_ms, 0, 30_000, 30_000,
                     10.0, 5412.0, 0.0)
    struct.pack_into("<7B", raw, base + 24, 1, lap, 0, 0, 0, 0, 0)
    return bytes(raw)


def _history(factory, frame: int, time: float, count: int,
             *, uid: int = 101) -> bytes:
    raw = _packet(factory, 11, frame=frame, time=time, uid=uid)
    struct.pack_into("<7B", raw, 24, 2, count, 1, 1, 1, 1, 1)
    for index in range(count):
        struct.pack_into("<IHHHB", raw, 31 + index * 11,
                         90_000 + index * 1000, 30_000, 30_000, 30_000, 15)
    struct.pack_into("<3B", raw, 1131, 255, 18, 16)
    return bytes(raw)


def _status(factory, frame: int, time: float, fuel: float) -> bytes:
    raw = _packet(factory, 7, frame=frame, time=time)
    struct.pack_into("<3f", raw, 24 + 2 * 47 + 5, fuel, 110.0, 5.0)
    struct.pack_into("<3B", raw, 24 + 2 * 47 + 25, 18, 16, 2)
    return bytes(raw)


def _damage(factory, frame: int, time: float, wear: float) -> bytes:
    raw = _packet(factory, 10, frame=frame, time=time)
    struct.pack_into("<4f", raw, 24 + 2 * 42, *([wear] * 4))
    return bytes(raw)


def _classification(factory, frame: int, time: float) -> bytes:
    raw = _packet(factory, 8, frame=frame, time=time)
    raw[24] = 1
    struct.pack_into("<6BId3B8B8B8B", raw, 25,
                     1, 4, 1, 25, 0, 3, 90_000, 380.0, 0, 0, 1,
                     *([18] + [0] * 7), *([16] + [0] * 7),
                     *([4] + [0] * 7))
    return bytes(raw)


def test_analytics_is_rebuildable_separated_and_as_of_safe(
    packet_factory: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(capture, "CAPTURE_DIRECTORY", tmp_path / "recordings")
    monkeypatch.setattr(analytics, "ANALYTICS_DIRECTORY", tmp_path / "analytics")
    writer = capture.CaptureWriter("sample.f1cap")
    packets = [
        (0.0, _session(packet_factory, 1, 10.0)),
        (0.1, _status(packet_factory, 2, 10.1, 20.0)),
        (0.2, _damage(packet_factory, 3, 10.2, 4.0)),
        (1.0, _lap(packet_factory, 4, 11.0, 2)),
        (1.1, _history(packet_factory, 5, 11.1, 1)),
        (2.0, _status(packet_factory, 6, 12.0, 18.0)),
        (2.1, _damage(packet_factory, 7, 12.1, 10.0)),
        (3.0, _lap(packet_factory, 8, 13.0, 3)),
        (3.1, _history(packet_factory, 9, 13.1, 2)),
        (4.0, bytes(_packet(packet_factory, 3, frame=10, time=14.0))),
        (6.0, _lap(packet_factory, 11, 16.0, 4)),
        (6.5, _classification(packet_factory, 12, 16.5)),
        (7.0, _session(packet_factory, 1, 2.0, uid=202)),
        (7.1, _lap(packet_factory, 2, 2.1, 2, uid=202)),
    ]
    for elapsed, raw in packets:
        assert writer.write(elapsed, raw)
    writer.close()
    source = capture.capture_path("sample.f1cap")
    before = sha256(source.read_bytes()).hexdigest()

    result = analytics.extract_capture("sample.f1cap")
    assert result["datagrams"] == len(packets)
    assert result["segments"] == 2
    assert sha256(source.read_bytes()).hexdigest() == before
    dataset = Path(result["dataset"])
    with pytest.raises(FileExistsError):
        analytics.extract_capture("sample.f1cap")
    with closing(sqlite3.connect(dataset)) as db:
        assert db.execute("SELECT value FROM metadata WHERE key='capture_sha256'").fetchone()[0] == before
        assert db.execute("SELECT COUNT(*) FROM driver_laps").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM tyre_stints").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM segments WHERE session_uid='202'").fetchone()[0] == 1
        assert db.execute("SELECT position FROM final_results").fetchone()[0] == 1
    early = analytics.summarize_dataset(dataset, as_of_elapsed_s=1.5)
    full = analytics.summarize_dataset(dataset)
    assert not early["fuel_burn"]
    assert not early["final_result_rows"]
    assert analytics.summarize_dataset(dataset, as_of_elapsed_s=6.4)["final_result_rows"] == 0
    assert full["final_result_rows"] == 1
    assert full["fuel_burn"][0]["kg_per_lap"] == pytest.approx(2.0)
    assert full["tyre_wear"][0]["percentage_points_per_lap"] == pytest.approx(6.0)
    assert analytics.extract_capture("sample.f1cap", replace=True)["sha256"] == before


def test_flashback_and_unobserved_gap_do_not_create_comparable_laps(
    packet_factory: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(capture, "CAPTURE_DIRECTORY", tmp_path / "recordings")
    monkeypatch.setattr(analytics, "ANALYTICS_DIRECTORY", tmp_path / "analytics")
    writer = capture.CaptureWriter("gaps.f1cap")
    events = [(0.0, _session(packet_factory, 1, 10)),
              (0.1, _lap(packet_factory, 2, 10.1, 2)),
              (0.2, _history(packet_factory, 3, 10.2, 1)),
              (10.0, _lap(packet_factory, 4, 40.0, 4))]
    flashback = _packet(packet_factory, 3, frame=5, time=41.0)
    flashback[24:28] = b"FLBK"
    events.extend([(10.1, bytes(flashback)), (10.2, _lap(packet_factory, 6, 20.0, 2))])
    for elapsed, raw in events:
        assert writer.write(elapsed, raw)
    writer.close()
    dataset = Path(analytics.extract_capture("gaps.f1cap")["dataset"])
    with closing(sqlite3.connect(dataset)) as db:
        assert db.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] >= 1
        assert db.execute("SELECT COUNT(*) FROM observations WHERE kind='race_transition' AND data_json LIKE '%flashback%'").fetchone()[0] == 1
    assert not analytics.summarize_dataset(dataset)["fuel_burn"]


def test_player_index_change_starts_distinct_analysis_segment(
    packet_factory: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(capture, "CAPTURE_DIRECTORY", tmp_path / "recordings")
    monkeypatch.setattr(analytics, "ANALYTICS_DIRECTORY", tmp_path / "analytics")
    writer = capture.CaptureWriter("join.f1cap")
    first = _session(packet_factory, 1, 10.0)
    second = bytearray(_session(packet_factory, 2, 11.0))
    second[22] = 3  # m_playerCarIndex changed while the UID stayed the same.
    writer.write(0.0, first)
    writer.write(1.0, bytes(second))
    writer.close()
    result = analytics.extract_capture("join.f1cap")
    with closing(sqlite3.connect(result["dataset"])) as db:
        assert db.execute("SELECT player_index FROM segments ORDER BY id").fetchall() == [(2,), (3,)]
