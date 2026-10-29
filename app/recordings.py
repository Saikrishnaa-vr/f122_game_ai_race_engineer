"""Race recording metadata and automatic Grand Prix capture lifecycle."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
from typing import TextIO
from uuid import uuid4

from .logging_setup import LOGGER
from .telemetry import capture
from .telemetry.capture import CaptureWriter, capture_path, read_capture
from .telemetry.payloads import Event, Lap, Participant, Session, decode_payload
from .telemetry.protocol import PacketError, PacketHeader, decode_packet
from .track_map import CATALOG


CLASSIFICATION_GRACE_SECONDS = 5.0
SESSION_END_GRACE_SECONDS = 60.0


def _started_at(path: Path) -> str:
    match = re.search(r"(20\d{6})-(\d{6})", path.name)
    if match:
        try:
            return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").astimezone().isoformat(timespec="seconds")
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


class RecordingSummary:
    def __init__(self, name: str, started_at: str | None = None) -> None:
        self.name = name
        self.started_at = started_at or _started_at(capture_path(name))
        self.session_uid: int | None = None
        self.track_id: int | None = None
        self.session_type: int | None = None
        self.total_laps: int | None = None
        self.player_name: str | None = None
        self.last_lap: int | None = None
        self.finish_position: int | None = None
        self.duration_seconds = 0.0
        self.classification_seen = False
        self.session_ended = False
        self.packets = 0

    def observe(self, elapsed: float, data: bytes, header: PacketHeader | None = None) -> None:
        try:
            header = header or decode_packet(data)
            value = decode_payload(data, header) if header.packet_id in (1, 2, 3, 4) else None
        except PacketError:
            return
        self.packets += 1
        self.duration_seconds = max(self.duration_seconds, elapsed)
        self.session_uid = header.session_uid
        if isinstance(value, Session):
            self.track_id = value.track_id
            self.session_type = value.session_type
            self.total_laps = value.total_laps
        elif isinstance(value, Lap):
            if value.lap_number > 0:
                self.last_lap = max(self.last_lap or 0, value.lap_number)
            if 1 <= value.position <= 22:
                self.finish_position = value.position
        elif isinstance(value, Participant):
            if value.name:
                self.player_name = value.name
        elif isinstance(value, Event) and value.code == "SEND":
            self.session_ended = True
        if header.packet_id == 8:
            self.classification_seen = True

    def as_dict(self, path: Path, status: str | None = None) -> dict:
        track = CATALOG.get(str(self.track_id), {})
        return {
            "name": self.name,
            "started_at": self.started_at,
            "track_id": self.track_id,
            "track": track.get("name", f"Track {self.track_id}" if self.track_id is not None else "Unknown track"),
            "location": track.get("location"),
            "session_uid": self.session_uid,
            "session_type": self.session_type,
            "total_laps": self.total_laps,
            "last_lap": self.last_lap,
            "player_name": self.player_name,
            "finish_position": self.finish_position,
            "duration_seconds": round(self.duration_seconds, 1),
            "size_bytes": path.stat().st_size,
            "packets": self.packets,
            "status": status or ("completed" if self.classification_seen else "ended" if self.session_ended else "stopped"),
        }


def save_summary(summary: RecordingSummary, status: str | None = None) -> dict:
    path = capture_path(summary.name)
    data = summary.as_dict(path, status)
    sidecar = path.with_suffix(".json")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=path.stem + "-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(sidecar)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return data


def recording_info(name: str) -> dict:
    path = capture_path(name)
    sidecar = path.with_suffix(".json")
    if sidecar.is_file() and not sidecar.is_symlink():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            if data.get("name") == name and data.get("size_bytes") == path.stat().st_size:
                return data
        except (OSError, ValueError, TypeError):
            pass
    summary = RecordingSummary(name)
    for elapsed, packet in read_capture(name):
        summary.observe(elapsed, packet)
    return summary.as_dict(path)


def list_recordings() -> list[dict]:
    if not capture.CAPTURE_DIRECTORY.is_dir() or capture.CAPTURE_DIRECTORY.is_symlink():
        return []
    result = []
    for path in capture.CAPTURE_DIRECTORY.glob("*.f1cap"):
        try:
            item = recording_info(path.name)
            if item["session_type"] == 10 and item["packets"] > 0:
                result.append(item)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Skipping unreadable recording %s: %s", path.name, exc)
    return sorted(result, key=lambda item: item["started_at"], reverse=True)


class AutoRaceRecorder:
    """Open one file per Grand Prix and retain classification before closing."""

    def __init__(self, output: TextIO) -> None:
        self.output = output
        self.writer: CaptureWriter | None = None
        self.summary: RecordingSummary | None = None
        self.session_uid: int | None = None
        self.started_monotonic = 0.0
        self.ended_at: float | None = None
        self.classified_at: float | None = None
        self.completed_uid: int | None = None

    def observe(self, now: float, data: bytes, header: PacketHeader, session: Session | None) -> None:
        if self.writer is not None and header.session_uid != self.session_uid:
            self.close("session_changed")
        if self.writer is None:
            if header.session_uid == self.completed_uid or not isinstance(session, Session) or session.session_type != 10:
                return
            name = f"race-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{uuid4().hex[:8]}.f1cap"
            self.writer = CaptureWriter(name, max_bytes=capture.MAX_AUTO_CAPTURE_BYTES)
            self.summary = RecordingSummary(name, datetime.now().astimezone().isoformat(timespec="seconds"))
            self.session_uid = header.session_uid
            self.started_monotonic = now
            self.ended_at = None
            self.classified_at = None
            print(f"Recording Grand Prix: recordings\\{name}", file=self.output, flush=True)
            LOGGER.info("Grand Prix recording started: %s", name)
        assert self.writer is not None and self.summary is not None
        try:
            has_room = self.writer.write(now - self.started_monotonic, data)
        except OSError as exc:
            LOGGER.warning("Recording failed: %s", exc)
            has_room = False
        if not has_room:
            self.close("size_limit")
            return
        self.summary.observe(now - self.started_monotonic, data, header)
        if header.packet_id == 3 and data[24:28] == b"SEND":
            self.ended_at = now
        elif header.packet_id == 8:
            self.classified_at = now

    def tick(self, now: float) -> None:
        if self.classified_at is not None and now - self.classified_at >= CLASSIFICATION_GRACE_SECONDS:
            self.close("completed")
        elif self.ended_at is not None and now - self.ended_at >= SESSION_END_GRACE_SECONDS:
            self.close("ended")

    def close(self, status: str = "stopped") -> None:
        if self.writer is None or self.summary is None:
            return
        name = self.summary.name
        self.writer.close()
        try:
            data = save_summary(self.summary, status)
            print(f"Saved {data['track']} race: recordings\\{name} ({status}).", file=self.output, flush=True)
        except OSError as exc:
            LOGGER.warning("Could not save recording metadata for %s: %s", name, exc)
            print(f"Saved recording {name}; metadata could not be written: {exc}", file=self.output, flush=True)
        self.completed_uid = self.session_uid
        self.writer = None
        self.summary = None
        self.session_uid = None
        self.ended_at = None
        self.classified_at = None
