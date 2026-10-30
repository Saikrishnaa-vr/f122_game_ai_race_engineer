"""Session recording metadata and automatic capture lifecycle."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import TextIO
from uuid import uuid4

from .logging_setup import LOGGER
from .telemetry import capture
from .telemetry.capture import MAGIC, RECORD, CaptureWriter, capture_path, read_capture
from .telemetry.payloads import Event, Lap, Participant, Session, decode_payload
from .telemetry.protocol import PacketError, PacketHeader, decode_packet
from .telemetry.session_types import (DRIVING_SESSION_TYPES, QUALIFYING_SESSION_TYPES,
                                      RACE_SESSION_TYPES, PRACTICE_SESSION_TYPES,
                                      game_mode_name, session_name)
from .track_map import CATALOG


CLASSIFICATION_GRACE_SECONDS = 5.0
SESSION_END_GRACE_SECONDS = 60.0
RESTART_REWIND_SECONDS = 30.0

@dataclass(frozen=True)
class RecordingPolicy:
    """Session types eligible for automatic recording; default is races only."""

    name: str = "races"

    def __post_init__(self) -> None:
        if self.name not in ("races", "weekend", "all-driving"):
            raise ValueError("recording policy must be races, weekend, or all-driving")

    @property
    def session_types(self) -> frozenset[int]:
        if self.name == "races":
            return RACE_SESSION_TYPES
        if self.name == "weekend":
            return RACE_SESSION_TYPES | PRACTICE_SESSION_TYPES | QUALIFYING_SESSION_TYPES
        return DRIVING_SESSION_TYPES


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
        self.network_game: int | None = None
        self.game_mode: int | None = None
        self.total_laps: int | None = None
        self.player_name: str | None = None
        self.last_lap: int | None = None
        self.observed_laps: set[int] = set()
        self.finish_position: int | None = None
        self.duration_seconds = 0.0
        self.classification_seen = False
        self.session_ended = False
        self.player_indices: set[int] = set()
        self.active_cars: int | None = None
        self.packets = 0
        self.continued_from: str | None = None

    def observe(self, elapsed: float, data: bytes, header: PacketHeader | None = None) -> None:
        try:
            header = header or decode_packet(data)
        except PacketError:
            return
        self.packets += 1
        self.duration_seconds = max(self.duration_seconds, elapsed)
        self.session_uid = header.session_uid
        self.player_indices.add(header.player_car_index)
        try:
            value = decode_payload(data, header) if header.packet_id in (1, 2, 3, 4) else None
        except PacketError:
            value = None  # Raw capture still contains this header-valid datagram.
        if isinstance(value, Session):
            self.track_id = value.track_id
            self.session_type = value.session_type
            self.network_game = value.network_game
            self.game_mode = value.game_mode
            self.total_laps = value.total_laps
        elif isinstance(value, Lap):
            if value.lap_number > 0:
                self.last_lap = max(self.last_lap or 0, value.lap_number)
                self.observed_laps.add(value.lap_number)
            if 1 <= value.position <= 22:
                self.finish_position = value.position
        elif isinstance(value, Participant):
            if value.name:
                self.player_name = value.name
        if header.packet_id == 4 and data[24] <= 22:
            self.active_cars = data[24]
        elif isinstance(value, Event) and value.code == "SEND":
            self.session_ended = True
        if header.packet_id == 8:
            self.classification_seen = True

    def as_dict(self, path: Path, status: str | None = None,
                stop_reason: str | None = None) -> dict:
        track = CATALOG.get(str(self.track_id), {})
        return {
            "name": self.name,
            "started_at": self.started_at,
            "track_id": self.track_id,
            "track": track.get("name", f"Track {self.track_id}" if self.track_id is not None else "Unknown track"),
            "location": track.get("location"),
            "session_uid": self.session_uid,
            "session_type": self.session_type,
            "session_name": session_name(self.session_type),
            "game_mode": self.game_mode,
            "game_mode_name": game_mode_name(self.game_mode),
            "network_game": self.network_game,
            "network_mode": {0: "Offline", 1: "Online"}.get(self.network_game, "Unknown"),
            "total_laps": self.total_laps,
            "last_lap": self.last_lap,
            "laps_observed": len(self.observed_laps),
            "player_name": self.player_name,
            "player_indices_observed": sorted(self.player_indices),
            "last_active_cars": self.active_cars,
            "finish_position": self.finish_position,
            "last_observed_position": self.finish_position,
            "duration_seconds": round(self.duration_seconds, 1),
            "size_bytes": path.stat().st_size,
            "packets": self.packets,
            "completion_confidence": ("classification_seen" if self.classification_seen else
                                      "session_end_seen" if self.session_ended else "no_end_signal"),
            "status": status or ("completed" if self.classification_seen else "ended" if self.session_ended else "incomplete"),
            "stop_reason": stop_reason,
            "continued_from": self.continued_from,
        }


def save_summary(summary: RecordingSummary, status: str | None = None,
                 stop_reason: str | None = None) -> dict:
    path = capture_path(summary.name)
    data = summary.as_dict(path, status, stop_reason)
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
            if (isinstance(data, dict) and data.get("name") == name
                    and data.get("size_bytes") == path.stat().st_size
                    and all(key in data for key in ("session_type", "packets", "started_at", "status"))):
                # Older sidecars remain usable without rewriting any capture.
                if "game_mode" not in data or "network_game" not in data:
                    for index, (_elapsed, packet) in enumerate(read_capture(name)):
                        if index >= 4096:
                            break
                        try:
                            header = decode_packet(packet)
                            session = decode_payload(packet, header) if header.packet_id == 1 else None
                        except PacketError:
                            continue
                        if isinstance(session, Session):
                            data.setdefault("game_mode", session.game_mode)
                            data.setdefault("network_game", session.network_game)
                            break
                data.setdefault("session_name", session_name(data.get("session_type")))
                data.setdefault("network_mode", {0: "Offline", 1: "Online"}.get(
                    data.get("network_game"), "Unknown"))
                data.setdefault("network_game", None)
                data.setdefault("game_mode", None)
                data.setdefault("game_mode_name", game_mode_name(data.get("game_mode")))
                data.setdefault("laps_observed", None)
                data.setdefault("last_observed_position", data.get("finish_position"))
                data.setdefault("completion_confidence", {
                    "completed": "classification_seen", "ended": "session_end_seen",
                }.get(data.get("status"), "unknown"))
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
            if item["session_type"] in DRIVING_SESSION_TYPES and item["packets"] > 0:
                result.append(item)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Unreadable recording %s: %s", path.name, exc)
            try:
                started_at = _started_at(path)
            except OSError:
                continue  # The file disappeared while the library was scanned.
            result.append({"name": path.name, "started_at": started_at,
                           "track": "Unreadable recording", "session_type": None,
                           "session_name": "Unknown session", "game_mode_name": "Unknown mode",
                           "network_mode": "Unknown", "last_lap": None,
                           "laps_observed": None, "packets": 0, "status": "unreadable",
                           "completion_confidence": "unknown", "error": str(exc)})
    return sorted(result, key=lambda item: item["started_at"], reverse=True)


class AutoRaceRecorder:
    """Capture eligible sessions, preserving race boundaries and partial files."""

    def __init__(self, output: TextIO, policy: RecordingPolicy | None = None) -> None:
        self.output = output
        self.policy = policy or RecordingPolicy()
        self.writer: CaptureWriter | None = None
        self.summary: RecordingSummary | None = None
        self.session_uid: int | None = None
        self.started_monotonic = 0.0
        self.ended_at: float | None = None
        self.classified_at: float | None = None
        self.completed_uid: int | None = None
        self.completed_game_time = 0.0
        self.completed_had_end = False
        self.last_packet_at: float | None = None
        self.rejected_packets = 0
        self.ignored_other_session = 0
        self.session_packet: bytes | None = None
        self.latest_game_time = 0.0
        self.last_player_index: int | None = None
        self.last_active_cars: int | None = None

    def reject(self, reason: str) -> None:
        """Account for datagrams that failed protocol validation."""
        self.rejected_packets += 1
        if self.rejected_packets == 1 or self.rejected_packets % 100 == 0:
            LOGGER.warning("recording event=packet_rejected reason=%s rejected=%d active_uid=%s",
                           reason, self.rejected_packets, self.session_uid)

    def diagnostics(self, now: float) -> None:
        """Periodic bounded counters, including the last successful write."""
        LOGGER.info("recording event=health active_uid=%s session_type=%s lap=%s "
                    "accepted=%d rejected=%d ignored_other_session=%d bytes=%d "
                    "last_write_monotonic=%s last_write_age_s=%s",
                    self.session_uid, self.summary.session_type if self.summary else None,
                    self.summary.last_lap if self.summary else None,
                    self.summary.packets if self.summary else 0, self.rejected_packets,
                    self.ignored_other_session, self.writer.size if self.writer else 0,
                    round(self.last_packet_at, 3) if self.last_packet_at is not None else None,
                    round(now - self.last_packet_at, 1) if self.last_packet_at is not None else None)

    def observe(self, now: float, data: bytes, header: PacketHeader, session: Session | None) -> None:
        valid_session = header.packet_id == 1 and isinstance(session, Session)
        known_session = valid_session and session.session_type in DRIVING_SESSION_TYPES
        eligible = known_session and session.session_type in self.policy.session_types
        if (self.writer is not None and header.session_uid == self.session_uid
                and known_session and self.summary is not None
                and (session.session_type != self.summary.session_type
                     or session.track_id != self.summary.track_id
                     or (self.summary.game_mode is not None and session.game_mode is not None
                         and session.game_mode != self.summary.game_mode))):
            LOGGER.info("recording event=session_change uid=%s old_session_type=%s "
                        "new_session_type=%d old_game_mode=%s new_game_mode=%s "
                        "old_track=%s new_track=%d old_lap=%s session_time=%.2f",
                        self.session_uid, self.summary.session_type,
                        session.session_type, self.summary.game_mode, session.game_mode,
                        self.summary.track_id, session.track_id,
                        self.summary.last_lap, header.session_time)
            self.close("session_changed")
            self.completed_uid = None
        if (self.writer is not None and header.session_uid == self.session_uid
                and self.ended_at is not None and header.packet_id == 1
                and eligible
                and header.session_time < self.latest_game_time - RESTART_REWIND_SECONDS):
            LOGGER.info("recording event=session_restart uid=%s old_game_time=%.2f "
                        "new_game_time=%.2f old_lap=%s",
                        self.session_uid, self.latest_game_time, header.session_time,
                        self.summary.last_lap if self.summary else None)
            self.close("session_restarted")
            self.completed_uid = None
        if self.writer is not None and header.session_uid != self.session_uid:
            # A lobby packet, delayed UDP datagram, or another sender is not
            # proof that the current race finished. Only a new race Session
            # packet can move the recorder to a different UID.
            if not known_session or header.session_uid == 0:
                self.ignored_other_session += 1
                if self.ignored_other_session == 1 or self.ignored_other_session % 100 == 0:
                    LOGGER.info("recording event=other_session_ignored active_uid=%s seen_uid=%s "
                                "packet_id=%d ignored=%d", self.session_uid, header.session_uid,
                                header.packet_id, self.ignored_other_session)
                return
            LOGGER.info("recording event=session_change old_uid=%s new_uid=%s "
                        "new_session_type=%d old_lap=%s old_session_time=%.2f",
                        self.session_uid, header.session_uid, session.session_type,
                        self.summary.last_lap if self.summary else None, header.session_time)
            self.close("session_changed")
        if self.writer is None:
            if not eligible or header.session_uid == 0:
                return
            if header.session_uid == self.completed_uid:
                if (not self.completed_had_end or header.session_time >=
                        self.completed_game_time - RESTART_REWIND_SECONDS):
                    return
                LOGGER.info("recording event=session_restart_after_close uid=%s "
                            "old_game_time=%.2f new_game_time=%.2f",
                            header.session_uid, self.completed_game_time, header.session_time)
                self.completed_uid = None
            prefix = "race" if session.session_type in RACE_SESSION_TYPES else "session"
            name = f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{uuid4().hex[:8]}.f1cap"
            self.writer = CaptureWriter(name, max_bytes=capture.MAX_AUTO_CAPTURE_BYTES)
            self.summary = RecordingSummary(name, datetime.now().astimezone().isoformat(timespec="seconds"))
            self.session_uid = header.session_uid
            self.started_monotonic = now
            self.ended_at = None
            self.classified_at = None
            self.last_packet_at = None
            self.latest_game_time = 0.0
            print(f"Recording {session_name(session.session_type)}: recordings\\{name}", file=self.output, flush=True)
            LOGGER.info("recording event=start name=%s uid=%s session_type=%d "
                        "game_mode=%s network_game=%s policy=%s lap=%s session_time=%.2f limit_bytes=%d",
                        name, header.session_uid, session.session_type,
                        session.game_mode, session.network_game, self.policy.name, None,
                        header.session_time, self.writer.max_bytes)
        assert self.writer is not None and self.summary is not None
        try:
            has_room = self.writer.write(now - self.started_monotonic, data)
        except OSError as exc:
            LOGGER.exception("recording event=write_error uid=%s lap=%s bytes=%d error=%s",
                             self.session_uid, self.summary.last_lap, self.writer.size, exc)
            self.close("write_error")
            return
        if not has_room:
            LOGGER.warning("recording event=size_limit uid=%s lap=%s session_time=%.2f "
                           "bytes=%d limit_bytes=%d", self.session_uid,
                           self.summary.last_lap, header.session_time,
                           self.writer.size, self.writer.max_bytes)
            previous_name = self.summary.name
            seed = self.session_packet
            can_continue = (seed is not None and len(MAGIC) + 2 * RECORD.size
                            + len(seed) + len(data) <= self.writer.max_bytes)
            self.close("size_limit")
            if can_continue and seed is not None:
                # Each file stays within the replay limit. An original Session
                # packet at the start makes the next segment independently
                # decodable; the packet that crossed the boundary follows it.
                self.completed_uid = None
                seed_header = decode_packet(seed)
                seed_session = decode_payload(seed, seed_header)
                assert isinstance(seed_session, Session)
                self.observe(now, seed, seed_header, seed_session)
                if self.summary is not None:
                    self.summary.continued_from = previous_name
                    self.observe(now, data, header, session)
            else:
                LOGGER.error("recording event=segment_unavailable uid=%s packet_id=%d "
                             "packet_bytes=%d; current packet was not written",
                             header.session_uid, header.packet_id, len(data))
            return
        previous_network = self.summary.network_game
        self.summary.observe(now - self.started_monotonic, data, header)
        if valid_session and previous_network is not None and session.network_game != previous_network:
            LOGGER.info("recording event=network_mode_change uid=%s old=%s new=%s "
                        "lap=%s session_time=%.2f", self.session_uid, previous_network,
                        session.network_game, self.summary.last_lap, header.session_time)
        if self.last_player_index is not None and header.player_car_index != self.last_player_index:
            LOGGER.info("recording event=player_index_change uid=%s old_index=%d new_index=%d "
                        "lap=%s session_time=%.2f", self.session_uid, self.last_player_index,
                        header.player_car_index, self.summary.last_lap, header.session_time)
        self.last_player_index = header.player_car_index
        if header.packet_id == 4 and data[24] <= 22:
            active_cars = data[24]
            if self.last_active_cars is not None and active_cars != self.last_active_cars:
                LOGGER.info("recording event=participant_count_change uid=%s old_count=%d "
                            "new_count=%d lap=%s session_time=%.2f", self.session_uid,
                            self.last_active_cars, active_cars, self.summary.last_lap, header.session_time)
            self.last_active_cars = active_cars
        self.latest_game_time = max(self.latest_game_time, header.session_time)
        if header.packet_id == 1 and isinstance(session, Session):
            self.session_packet = data
        self.last_packet_at = now
        if header.packet_id == 3 and data[24:28] == b"SEND":
            self.ended_at = now
            LOGGER.info("recording event=session_ended uid=%s lap=%s session_time=%.2f "
                        "bytes=%d", self.session_uid, self.summary.last_lap,
                        header.session_time, self.writer.size)
        elif header.packet_id == 8:
            self.classified_at = now
            LOGGER.info("recording event=classification uid=%s lap=%s session_time=%.2f "
                        "bytes=%d", self.session_uid, self.summary.last_lap,
                        header.session_time, self.writer.size)

    def tick(self, now: float) -> None:
        quiet_since = self.last_packet_at
        if (self.classified_at is not None and quiet_since is not None
                and now - quiet_since >= CLASSIFICATION_GRACE_SECONDS):
            self.close("completed")
        elif (self.ended_at is not None and quiet_since is not None
              and now - quiet_since >= SESSION_END_GRACE_SECONDS):
            self.close("ended")

    def close(self, status: str = "stopped") -> None:
        if self.writer is None or self.summary is None:
            return
        name = self.summary.name
        actual_status = ("completed" if self.summary.classification_seen else
                         "ended" if self.summary.session_ended else
                         "continued" if status == "size_limit" else
                         "incomplete" if status in ("stopped", "session_changed", "write_error") else status)
        LOGGER.info("recording event=stop reason=%s status=%s name=%s uid=%s "
                    "session_type=%s lap=%s session_time=%.2f packets=%d rejected=%d bytes=%d",
                    status, actual_status, name, self.session_uid, self.summary.session_type,
                    self.summary.last_lap, self.latest_game_time, self.summary.packets,
                    self.rejected_packets,
                    self.writer.size)
        self.writer.close()
        try:
            data = save_summary(self.summary, actual_status, status)
            print(f"Saved {data['track']} {data['session_name']}: recordings\\{name} ({actual_status}).",
                  file=self.output, flush=True)
        except OSError as exc:
            LOGGER.warning("Could not save recording metadata for %s: %s", name, exc)
            print(f"Saved recording {name}; metadata could not be written: {exc}", file=self.output, flush=True)
        self.completed_uid = self.session_uid
        self.completed_game_time = self.latest_game_time
        self.completed_had_end = self.summary.classification_seen or self.summary.session_ended
        self.writer = None
        self.summary = None
        self.session_uid = None
        self.ended_at = None
        self.classified_at = None
        self.last_packet_at = None
        self.session_packet = None
        self.latest_game_time = 0.0
        self.last_player_index = None
        self.last_active_cars = None
