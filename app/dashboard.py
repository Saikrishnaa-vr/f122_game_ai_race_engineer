"""Optional loopback dashboard. The receiver publishes immutable JSON snapshots."""

import asyncio
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Condition, Lock, Thread
import time
from urllib.parse import parse_qs, urlsplit

from .conversation import Conversation
from .history import RaceHistory
from .fuel_view import FuelView
from .race_status import RaceStatusTracker
from .track_map import ObservedTrackMap


HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")
PRACTICE_HTML = (Path(__file__).parent / "practice.html").read_text(encoding="utf-8")


class DashboardState:
    def __init__(self, conversation: Conversation | None = None,
                 history_path: str | None = None) -> None:
        self._lock = Lock()
        self._changed = Condition(self._lock)
        self._version = 0
        self._snapshot = "{}"
        self._facts: dict = {"fields": {}, "estimates": {}, "strategy": {}}
        self._messages: deque[str] = deque(maxlen=30)
        self._track_map = ObservedTrackMap()
        self._fuel_view = FuelView()
        self._race_status = RaceStatusTracker()
        self.conversation = conversation or Conversation()
        self.history_path = Path(history_path) if history_path else None

    def publish(self, facts: dict, connection: str, performance_mode: bool,
                voice_status: str = "off") -> None:
        with self._changed:
            facts["estimates"]["fuel_view"] = self._fuel_view.update(facts)
            self._race_status.update(facts)
            self._facts = facts
            payload = {"connection": connection, "performance_mode": performance_mode,
                       "voice_status": voice_status,
                       "facts": facts, "track_map": self._track_map.update(facts),
                       "messages": list(self._messages)}
            self._snapshot = json.dumps(payload, allow_nan=False)
            self._version += 1
            self._changed.notify_all()

    def message(self, value: str) -> None:
        with self._lock:
            self._messages.appendleft(value[:500])

    def snapshot(self) -> str:
        with self._lock:
            return self._snapshot

    def wait_for_newer(self, version: int, timeout: float = 1.0) -> tuple[int, str]:
        """Wake WebSocket clients on publication, without blocking UDP receive."""
        with self._changed:
            self._changed.wait_for(lambda: self._version > version, timeout)
            return self._version, self._snapshot

    def ask(self, question: str) -> str:
        with self._lock:
            facts = self._facts
        answer = (self.conversation.answer(question, facts) if facts.get("session_uid") is not None
                  else "Race data is not available yet.")
        self.message("Driver: " + question[:160])
        self.message("Engineer: " + answer)
        return answer

    def practice(self, track_id: int) -> list[dict]:
        if self.history_path is None:
            return []
        with RaceHistory(self.history_path) as history:
            return history.practice_summary(track_id)

    def label_setup(self, track_id: int, fingerprint: str, label: str) -> None:
        if self.history_path is None:
            raise ValueError("history is disabled")
        with RaceHistory(self.history_path) as history:
            history.label_setup(track_id, fingerprint, label)


def create_app(state: DashboardState):
    """Delay optional FastAPI import until the dashboard is requested."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
    from fastapi.responses import HTMLResponse, Response

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    async def home() -> HTMLResponse:
        return HTMLResponse(HTML, headers={"Content-Security-Policy":
                                         "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src https://commons.wikimedia.org https://upload.wikimedia.org https://thumb.wikimedia.org; connect-src 'self' ws://127.0.0.1:*"})

    @app.get("/practice")
    async def practice_home() -> HTMLResponse:
        return HTMLResponse(PRACTICE_HTML, headers={"Content-Security-Policy":
                       "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"})

    @app.get("/api/practice")
    async def practice_data(track_id: int) -> Response:
        try:
            rows = await asyncio.to_thread(state.practice, track_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return Response(json.dumps({"track_id": track_id, "setups": rows}), media_type="application/json")

    @app.post("/api/setup-label")
    async def setup_label(request: Request) -> Response:
        origin = request.headers.get("origin", "")
        if origin and origin not in ("http://127.0.0.1:8765", "http://localhost:8765"):
            raise HTTPException(403, "cross-origin request rejected")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "JSON required")
        raw = await request.body()
        if len(raw) > 2048:
            raise HTTPException(413, "request too large")
        try:
            body = json.loads(raw)
            await asyncio.to_thread(state.label_setup, body["track_id"], body["fingerprint"], body["label"])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return Response('{"saved":true}', media_type="application/json")

    @app.get("/api/state")
    async def api_state() -> Response:
        return Response(state.snapshot(), media_type="application/json", headers={"Cache-Control": "no-store"})

    @app.post("/api/ask")
    async def ask(request: Request) -> Response:
        origin = request.headers.get("origin", "")
        if origin and origin not in ("http://127.0.0.1:8765", "http://localhost:8765"):
            raise HTTPException(403, "cross-origin request rejected")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "JSON required")
        raw = await request.body()
        if len(raw) > 2048:
            raise HTTPException(413, "question too large")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "invalid JSON") from None
        question = body.get("question") if isinstance(body, dict) else None
        if not isinstance(question, str) or not 0 < len(question) <= 160:
            raise HTTPException(400, "question must be 1-160 characters")
        answer = await asyncio.to_thread(state.ask, question)
        return Response(json.dumps({"answer": answer}), media_type="application/json")

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        origin = ws.headers.get("origin", "")
        if origin and origin not in ("http://127.0.0.1:8765", "http://localhost:8765"):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            version = -1
            last_send = 0.0
            while True:
                current, snapshot = await asyncio.to_thread(state.wait_for_newer, version)
                if current != version or time.monotonic() - last_send >= 5:
                    await ws.send_text(snapshot)
                    version = current
                    last_send = time.monotonic()
        except (WebSocketDisconnect, RuntimeError, OSError):
            return

    return app


class PollingDashboardServer:
    """Match the receiver's shutdown interface for the dependency-free server."""

    def __init__(self, server: ThreadingHTTPServer) -> None:
        self.server = server

    @property
    def should_exit(self) -> bool:
        return False

    @should_exit.setter
    def should_exit(self, value: bool) -> None:
        if value:
            self.server.shutdown()
            self.server.server_close()


def start_polling_dashboard(state: DashboardState, port: int = 8765) -> tuple[Thread, PollingDashboardServer]:
    """Serve the same local page and JSON API when optional packages are absent."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def send_body(self, code: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if content_type.startswith("text/html"):
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                                 "script-src 'unsafe-inline'; img-src https://commons.wikimedia.org https://upload.wikimedia.org https://thumb.wikimedia.org; connect-src 'self' ws://127.0.0.1:*")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/":
                self.send_body(200, HTML, "text/html; charset=utf-8")
            elif path == "/practice":
                self.send_body(200, PRACTICE_HTML, "text/html; charset=utf-8")
            elif path == "/api/state":
                self.send_body(200, state.snapshot(), "application/json; charset=utf-8")
            elif path == "/api/practice":
                try:
                    track_id = int(parse_qs(parsed.query)["track_id"][0])
                    rows = state.practice(track_id)
                except (ValueError, KeyError, IndexError, OSError) as exc:
                    self.send_body(400, str(exc))
                    return
                self.send_body(200, json.dumps({"track_id": track_id, "setups": rows}),
                               "application/json; charset=utf-8")
            else:
                self.send_body(404, "Not found")

        def do_POST(self) -> None:
            if self.path not in ("/api/ask", "/api/setup-label"):
                self.send_body(404, "Not found")
                return
            origin = self.headers.get("Origin", "")
            expected = (f"http://127.0.0.1:{self.server.server_address[1]}",
                        f"http://localhost:{self.server.server_address[1]}")
            if origin and origin not in expected:
                # Drain a small request body before replying. On Windows, closing
                # with unread POST bytes can reset the socket instead of sending 403.
                try:
                    rejected_length = int(self.headers.get("Content-Length", "0"))
                    if 0 < rejected_length <= 2048:
                        self.connection.settimeout(0.5)
                        self.rfile.read(rejected_length)
                except (ValueError, OSError):
                    pass
                self.send_body(403, "Cross-origin request rejected")
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.send_body(415, "JSON required")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.send_body(411, "Content-Length required")
                return
            if length < 0 or length > 2048:
                self.send_body(413, "Question too large")
                return
            try:
                body = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                self.send_body(400, "Invalid JSON")
                return
            if self.path == "/api/setup-label":
                try:
                    state.label_setup(body["track_id"], body["fingerprint"], body["label"])
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    self.send_body(400, str(exc))
                    return
                self.send_body(200, '{"saved":true}', "application/json; charset=utf-8")
                return
            question = body.get("question") if isinstance(body, dict) else None
            if not isinstance(question, str) or not 0 < len(question) <= 160:
                self.send_body(400, "Question must be 1-160 characters")
                return
            self.send_body(200, json.dumps({"answer": state.ask(question)}),
                           "application/json; charset=utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, name="dashboard-polling", daemon=True)
    thread.start()
    return thread, PollingDashboardServer(server)


def start_dashboard(state: DashboardState, port: int = 8765) -> tuple[Thread, object]:
    try:
        import uvicorn
        app = create_app(state)
    except ImportError:
        return start_polling_dashboard(state, port)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning", access_log=False))
    thread = Thread(target=server.run, name="dashboard", daemon=True)
    thread.start()
    return thread, server
