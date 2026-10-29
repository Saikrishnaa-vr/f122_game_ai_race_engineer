"""Read-only, seekable dashboard playback from a bounded local capture."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock
import time
from urllib.parse import parse_qs, urlsplit
import zlib

from .conversation import Conversation
from .dashboard import DashboardState, HTML, PRACTICE_HTML
from .engineer import Engineer
from .intelligence import race_facts
from .recordings import RACE_SESSION_TYPES, list_recordings, recording_info
from .strategy import StrategyAssumptions
from .telemetry.capture import read_capture
from .telemetry.receiver import ReceptionStats, process_packet
from .telemetry.state import RaceState


LIBRARY_HTML = (Path(__file__).parent / "replay_library.html").read_text(encoding="utf-8")


@dataclass(frozen=True)
class ReplayFrame:
    elapsed: float
    lap: int
    payload: bytes


def load_frames(name: str, pit_loss_seconds: float | None = None,
                interval_seconds: float = 0.5) -> list[ReplayFrame]:
    """Run the real telemetry and dashboard pipeline once; preserve its snapshots."""
    if interval_seconds <= 0:
        raise ValueError("interval must be positive")
    stats = ReceptionStats(0.0, 0.0)
    state = RaceState()
    engineer = Engineer(strategy_assumptions=StrategyAssumptions(pit_loss_seconds=pit_loss_seconds))
    dashboard = DashboardState()
    frames: list[ReplayFrame] = []
    last_publish = -interval_seconds
    for now, datagram in read_capture(name):
        header = process_packet(stats, state, datagram, now)
        if header is None:
            continue
        latest = state.get_fresh(header.packet_id, now) if header.packet_id in (1, 7, 10) else None
        if latest is not None and latest.header.frame_identifier == header.frame_identifier:
            for alert in engineer.on_packet(state, now):
                dashboard.message(alert.message)
        if now - last_publish < interval_seconds:
            continue
        last_publish = now
        facts = race_facts(state, engineer, now)
        session = facts["fields"].get("session")
        lap = facts["fields"].get("lap")
        if not session or not lap or session["value"]["session_type"] not in RACE_SESSION_TYPES:
            continue
        dashboard.publish(facts, stats.status(now), False)
        frames.append(ReplayFrame(now, lap["value"]["lap_number"],
                                  zlib.compress(dashboard.snapshot().encode("utf-8"), level=3)))
    if not frames:
        raise ValueError("capture has no Grand Prix dashboard frames")
    return frames


class ReplayController:
    def __init__(self, frames: list[ReplayFrame], pit_loss_seconds: float | None = None,
                 details: dict | None = None, library_mode: bool = False) -> None:
        if not frames:
            raise ValueError("replay requires at least one frame")
        self.frames = frames
        self.pit_loss_seconds = pit_loss_seconds
        self.details = details
        self.library_mode = library_mode
        self._times = [frame.elapsed for frame in frames]
        self._laps: dict[int, int] = {}
        for index, frame in enumerate(frames):
            self._laps.setdefault(frame.lap, index)
        self._lock = Lock()
        self._index = 0
        self._playing = False
        self._speed = 1.0
        self._anchor = time.monotonic()
        self._anchor_elapsed = frames[0].elapsed
        self._conversation = Conversation()

    def _current_index(self) -> int:
        if self._playing:
            elapsed = self._anchor_elapsed + (time.monotonic() - self._anchor) * self._speed
            self._index = min(len(self.frames) - 1, bisect_right(self._times, elapsed) - 1)
            if self._index == len(self.frames) - 1:
                self._playing = False
        return self._index

    def snapshot(self) -> dict:
        with self._lock:
            index = self._current_index()
            frame = self.frames[index]
            payload = json.loads(zlib.decompress(frame.payload))
            payload["replay"] = {
                "active": True, "index": index, "total": len(self.frames),
                "elapsed": round(frame.elapsed - self.frames[0].elapsed, 1),
                "duration": round(self.frames[-1].elapsed - self.frames[0].elapsed, 1),
                "lap": frame.lap, "playing": self._playing, "speed": self._speed,
                "laps": self._laps, "pit_loss_seconds": self.pit_loss_seconds,
                "details": self.details,
                "library_mode": self.library_mode,
            }
            return payload

    def control(self, command: dict) -> dict:
        if not isinstance(command, dict):
            raise ValueError("control must be a JSON object")
        action = command.get("action")
        with self._lock:
            index = self._current_index()
            if action == "play":
                self._playing = True
            elif action == "pause":
                self._playing = False
            elif action == "seek":
                wanted = command.get("index")
                if type(wanted) is not int or not 0 <= wanted < len(self.frames):
                    raise ValueError("index is outside the replay")
                index = wanted
            elif action == "lap":
                wanted = command.get("lap")
                if type(wanted) is not int or wanted not in self._laps:
                    raise ValueError("lap is unavailable")
                index = self._laps[wanted]
            elif action == "speed":
                wanted = command.get("speed")
                if type(wanted) not in (int, float) or wanted not in (1, 2, 4, 8):
                    raise ValueError("speed must be 1, 2, 4 or 8")
                self._speed = float(wanted)
            else:
                raise ValueError("unknown replay action")
            self._index = index
            self._anchor = time.monotonic()
            self._anchor_elapsed = self.frames[index].elapsed
        return self.snapshot()

    def ask(self, question: str) -> str:
        return self._conversation.answer(question, self.snapshot()["facts"])


class ReplayLibrary:
    def __init__(self, pit_loss_seconds: float | None = None) -> None:
        self.pit_loss_seconds = pit_loss_seconds
        self._lock = Lock()
        self._controller: ReplayController | None = None
        self._loading = Lock()

    def recordings(self) -> list[dict]:
        return list_recordings()

    def controller(self) -> ReplayController | None:
        with self._lock:
            return self._controller

    def select(self, name: str) -> dict:
        if not isinstance(name, str) or name not in {item["name"] for item in self.recordings()}:
            raise ValueError("recording is unavailable")
        with self._loading:
            frames = load_frames(name, self.pit_loss_seconds)
            details = recording_info(name)
            controller = ReplayController(frames, self.pit_loss_seconds, details, library_mode=True)
            with self._lock:
                self._controller = controller
        return {"name": name, "frames": len(frames), "details": details}


def create_replay_server(controller: ReplayController | ReplayLibrary, port: int = 8765) -> ThreadingHTTPServer:
    library = isinstance(controller, ReplayLibrary)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def send_body(self, code: int, body: str, content_type: str) -> None:
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if content_type.startswith("text/html"):
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                                 "script-src 'unsafe-inline'; img-src https://commons.wikimedia.org https://upload.wikimedia.org https://thumb.wikimedia.org; connect-src 'self'")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/" or path == "/replays":
                self.send_body(200, LIBRARY_HTML if library else HTML, "text/html; charset=utf-8")
            elif path == "/race" and library:
                self.send_body(200, HTML if controller.controller() is not None else LIBRARY_HTML,
                               "text/html; charset=utf-8")
            elif path == "/api/replays" and library:
                self.send_body(200, json.dumps(controller.recordings()), "application/json; charset=utf-8")
            elif path == "/practice":
                self.send_body(200, PRACTICE_HTML, "text/html; charset=utf-8")
            elif path == "/api/practice":
                try:
                    track_id = int(parse_qs(parsed.query)["track_id"][0])
                except (ValueError, KeyError, IndexError):
                    self.send_body(400, "track_id is required", "text/plain; charset=utf-8")
                    return
                self.send_body(200, json.dumps({"track_id": track_id, "setups": []}),
                               "application/json; charset=utf-8")
            elif path == "/api/state":
                selected = controller.controller() if library else controller
                if selected is None:
                    self.send_body(409, "Select a recording first", "text/plain; charset=utf-8")
                else:
                    self.send_body(200, json.dumps(selected.snapshot()), "application/json; charset=utf-8")
            else:
                self.send_body(404, "Not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            if self.path not in (("/api/replay", "/api/ask", "/api/select") if library
                                 else ("/api/replay", "/api/ask")):
                self.send_body(404, "Not found", "text/plain; charset=utf-8")
                return
            origin = self.headers.get("Origin", "")
            actual_port = self.server.server_address[1]
            allowed = (f"http://127.0.0.1:{actual_port}", f"http://localhost:{actual_port}")
            if origin and origin not in allowed:
                self.send_body(403, "Cross-origin request rejected", "text/plain; charset=utf-8")
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.send_body(415, "JSON required", "text/plain; charset=utf-8")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.send_body(411, "Content-Length required", "text/plain; charset=utf-8")
                return
            if not 0 <= length <= 2048:
                self.send_body(413, "Request too large", "text/plain; charset=utf-8")
                return
            try:
                body = json.loads(self.rfile.read(length))
                if self.path == "/api/select" and library:
                    name = body.get("name") if isinstance(body, dict) else None
                    result = controller.select(name)
                else:
                    selected = controller.controller() if library else controller
                    if selected is None:
                        self.send_body(409, "Select a recording first", "text/plain; charset=utf-8")
                        return
                    if self.path == "/api/replay":
                        result = selected.control(body)
                    else:
                        question = body.get("question") if isinstance(body, dict) else None
                        if not isinstance(question, str) or not 0 < len(question) <= 160:
                            raise ValueError("question must be 1-160 characters")
                        result = {"answer": selected.ask(question)}
            except (ValueError, UnicodeDecodeError) as exc:
                self.send_body(400, str(exc), "text/plain; charset=utf-8")
                return
            self.send_body(200, json.dumps(result), "application/json; charset=utf-8")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_replay(controller: ReplayController | ReplayLibrary, port: int = 8765,
                 stop_file: Path | None = None) -> None:
    server = create_replay_server(controller, port)
    try:
        if stop_file is None:
            server.serve_forever(poll_interval=0.5)
        else:
            server.timeout = 0.5
            while not stop_file.exists():
                server.handle_request()
    finally:
        server.server_close()
