"""Bounded, receive-only UDP diagnostics for F1 22."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import logging
import re
import socket
from ipaddress import IPv4Address, AddressValueError
from threading import Event
import time
from typing import TextIO
from urllib.request import urlopen
import webbrowser

from ..engineer import AlertConfig, AlertMonitor, Engineer
from ..history import HistoryWorker
from ..intelligence import race_facts
from ..logging_setup import LOGGER
from ..paths import data_root
from ..strategy import StrategyAssumptions
from .capture import CaptureWriter, read_capture
from .payloads import Session
from .protocol import PACKET_TYPES, PacketError, PacketHeader, decode_packet
from .state import RaceState


HOST = "127.0.0.1"
PORT = 20777
SOCKET_TIMEOUT_SECONDS = 0.5
REPORT_INTERVAL_SECONDS = 5.0
STALE_AFTER_SECONDS = 5.0
MAX_DATAGRAM_SIZE = 65535
CONTROL_DIRECTORY = data_root() / "control"
DASHBOARD_URL = "http://127.0.0.1:8765/"


def open_dashboard_browser(output: TextIO) -> None:
    """Launch the local page only after this receiver's dashboard responds."""
    for _attempt in range(30):
        try:
            with urlopen(DASHBOARD_URL + "api/state", timeout=0.3) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    else:
        message = f"Dashboard did not become ready; open {DASHBOARD_URL} after checking the console."
        LOGGER.warning(message)
        print(message, file=output, flush=True)
        return
    try:
        if webbrowser.open(DASHBOARD_URL, new=2):
            print("Dashboard opened in your default browser.", file=output, flush=True)
        else:
            print(f"Could not open a browser automatically. Open {DASHBOARD_URL}", file=output, flush=True)
    except OSError as exc:
        LOGGER.warning("Could not open dashboard browser: %s", exc)
        print(f"Could not open a browser automatically: {exc}. Open {DASHBOARD_URL}", file=output, flush=True)


@dataclass(slots=True)
class ReceptionStats:
    started_at: float
    last_report_at: float
    last_valid_at: float | None = None
    last_header: PacketHeader | None = None
    datagrams: int = 0
    valid: int = 0
    invalid: int = 0
    payload_invalid: int = 0
    valid_at_last_report: int = 0
    invalid_at_last_report: int = 0
    payload_invalid_at_last_report: int = 0
    counts: Counter[int] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)
    last_error: str | None = None

    def record(self, data: bytes, now: float) -> PacketHeader | None:
        self.datagrams += 1
        try:
            header = decode_packet(data)
        except PacketError as exc:
            self.invalid += 1
            self.errors[exc.reason] += 1
            self.last_error = str(exc)
            return None
        self.valid += 1
        self.counts[header.packet_id] += 1
        self.last_valid_at = now
        self.last_header = header
        return header

    def status(self, now: float) -> str:
        if self.last_valid_at is None:
            return "waiting"
        if now - self.last_valid_at >= STALE_AFTER_SECONDS:
            return "stale"
        return "receiving"

    def report(self, now: float) -> str:
        elapsed = now - self.last_report_at
        rate = (self.valid - self.valid_at_last_report) / elapsed if elapsed > 0 else 0.0
        self.last_report_at = now
        self.valid_at_last_report = self.valid
        self.invalid_at_last_report = self.invalid
        self.payload_invalid_at_last_report = self.payload_invalid
        packets = ", ".join(
            f"{packet_id}:{PACKET_TYPES[packet_id][0]}={count}"
            for packet_id, count in sorted(self.counts.items())
        ) or "none"
        errors = ", ".join(f"{name}={count}" for name, count in sorted(self.errors.items())) or "none"
        last_error = f"; last invalid: {self.last_error}" if self.last_error else ""
        status = self.status(now)
        if status == "waiting":
            advice = (
                " No valid telemetry yet: start a driving session; check UDP On, "
                "127.0.0.1:20777, format 2022, and port conflicts."
            )
        elif status == "stale":
            advice = " Valid telemetry stopped; check the session and UDP settings."
        else:
            advice = ""
        header = self.last_header
        latest = (
            f"; latest session={header.session_uid}, frame={header.frame_identifier}, "
            f"player={header.player_car_index}"
            if header is not None
            else ""
        )
        return (
            f"Status: {status}; datagrams={self.datagrams}, valid={self.valid}, "
            f"invalid={self.invalid}, payload errors={self.payload_invalid}, valid rate={rate:.1f} packets/s "
            f"(last {elapsed:.1f}s); IDs: {packets}; errors: {errors}"
            f"{last_error}{latest}.{advice}"
        )


def log_report(stats: ReceptionStats, now: float, *, final: bool = False) -> str:
    level = (
        logging.WARNING
        if stats.status(now) != "receiving" or stats.invalid > stats.invalid_at_last_report
        or stats.payload_invalid > stats.payload_invalid_at_last_report
        else logging.INFO
    )
    report = stats.report(now)
    LOGGER.log(level, "%s%s", "Final " if final else "", report)
    return report


def process_packet(stats: ReceptionStats, state: RaceState, data: bytes, now: float) -> PacketHeader | None:
    header = stats.record(data, now)
    if header is not None:
        previous_uid, previous_player = state.session_uid, state.player_index
        try:
            state.apply(data, header, now)
        except PacketError as exc:
            stats.payload_invalid += 1
            stats.errors[f"payload_{exc.reason}"] += 1
            stats.last_error = str(exc)
        else:
            if previous_uid is not None and (previous_uid != state.session_uid
                                            or previous_player != state.player_index):
                LOGGER.info("telemetry event=state_reset old_uid=%s new_uid=%s "
                            "old_player=%s new_player=%s packet_id=%d session_time=%.2f",
                            previous_uid, state.session_uid, previous_player,
                            state.player_index, header.packet_id, header.session_time)
    return header


def _ipv4_address(value: str, *, bind: bool = False) -> str:
    try:
        address = IPv4Address(value)
    except AddressValueError as exc:
        raise ValueError("UDP address must be an IPv4 literal") from exc
    if address.is_multicast or address == IPv4Address("255.255.255.255"):
        raise ValueError("UDP address must be unicast or 0.0.0.0 for binding")
    if not bind and address.is_unspecified:
        raise ValueError("sender address cannot be 0.0.0.0")
    return str(address)


def open_receiver(port: int = PORT, bind_address: str = HOST) -> socket.socket:
    """Bind UDP exclusively where supported; loopback remains the default."""
    bind_address = _ipv4_address(bind_address, bind=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((bind_address, port))
        sock.settimeout(SOCKET_TIMEOUT_SECONDS)
        return sock
    except OSError:
        sock.close()
        raise


def run_receiver(
    output: TextIO, port: int = PORT, stop: Event | None = None, record_name: str | None = None,
    alert_config: AlertConfig | None = None,
    strategy_assumptions: StrategyAssumptions | None = None, history_path: str | None = None,
    dashboard: bool = False, open_dashboard: bool = False, performance_mode: bool = False,
    voice_model: str | None = None,
    voice_input_device: int | None = None, voice_volume: float = 0.8,
    llm_endpoint: str | None = None, llm_model: str | None = None,
    stop_name: str | None = None,
    auto_record: bool = False,
    bind_address: str = HOST, sender_address: str | None = None,
    recording_policy: str = "races",
) -> None:
    """Receive until interrupted; never send a datagram or touch the game."""
    if stop is None:
        stop = Event()
    bind_address = _ipv4_address(bind_address, bind=True)
    if sender_address is not None:
        sender_address = _ipv4_address(sender_address)
    if open_dashboard and (not dashboard or performance_mode):
        raise ValueError("opening the dashboard requires an enabled dashboard")
    if stop_name is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.stop", stop_name):
        raise ValueError("stop file must be a simple filename ending in .stop")
    stop_path = CONTROL_DIRECTORY / stop_name if stop_name is not None else None
    if stop_path is not None:
        CONTROL_DIRECTORY.mkdir(parents=True, exist_ok=True)
        if stop_path.exists():
            raise ValueError(f"stop file already exists: {stop_path}")
    with open_receiver(port, bind_address) as sock:
        actual_port = sock.getsockname()[1]
        message = f"Bound UDP {bind_address}:{actual_port}. Binding alone does not confirm telemetry."
        print(message, file=output, flush=True)
        LOGGER.info(message)
        if stop_path is not None:
            print(f"Stop signal file: {stop_path}", file=output, flush=True)
        started = time.monotonic()
        last_facts_at = started - 1.0
        last_strategy_key: tuple[int | None, str] | None = None
        real_time_alert_messages: set[str] = set()
        stats = ReceptionStats(started_at=started, last_report_at=started)
        state = RaceState()
        engineer = Engineer(alerts=AlertMonitor(alert_config or AlertConfig()),
                            strategy_assumptions=strategy_assumptions or StrategyAssumptions())
        from ..conversation import Conversation, IntentModel
        from ..dashboard import DashboardState, start_dashboard

        if open_dashboard:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.3)
                if probe.connect_ex((HOST, 8765)) == 0:
                    raise OSError("dashboard port 8765 is already in use; close the other dashboard first")
        model = IntentModel(llm_endpoint, llm_model) if llm_endpoint and llm_model else None
        dashboard_state = DashboardState(Conversation(model), history_path)
        from ..recordings import AutoRaceRecorder, RecordingPolicy, RecordingSummary, save_summary
        capture = CaptureWriter(record_name) if record_name is not None else None
        capture_summary = (RecordingSummary(record_name) if record_name is not None else None)
        auto_recorder = AutoRaceRecorder(output, RecordingPolicy(recording_policy)) if auto_record else None
        sender_rejected = 0
        connection_status = stats.status(started)
        next_recording_diagnostic_at = started + 30.0
        history = None
        if history_path is not None:
            try:
                history = HistoryWorker(history_path)
            except (OSError, RuntimeError) as exc:
                LOGGER.warning("History unavailable: %s", exc)
        dashboard_server = None
        dashboard_thread = None
        if dashboard and not performance_mode:
            try:
                dashboard_thread, dashboard_server = start_dashboard(dashboard_state)
                print("Dashboard: http://127.0.0.1:8765", file=output, flush=True)
            except Exception as exc:
                LOGGER.warning("Dashboard unavailable: %s", exc)
                print(f"Dashboard unavailable: {exc}", file=output, flush=True)
                if open_dashboard:
                    if capture is not None:
                        capture.close()
                    if history is not None:
                        history.close()
                    raise OSError(f"dashboard could not start: {exc}") from exc
        if open_dashboard and dashboard_server is not None:
            open_dashboard_browser(output)
        radio = None
        if voice_model is not None:
            try:
                from ..voice import VoiceRadio
                radio = VoiceRadio(dashboard_state.ask, voice_model, volume=voice_volume,
                                   input_device=voice_input_device)
                radio.start()
                print("Voice starting: hold F8 to ask; release to send.", file=output, flush=True)
            except Exception as exc:
                LOGGER.warning("Voice unavailable: %s", exc)
                print(f"Voice unavailable: {exc}", file=output, flush=True)
                radio = None
        if record_name is not None:
            print(f"Recording raw UDP datagrams to recordings\\{record_name} (maximum 256 MiB).", file=output, flush=True)
            LOGGER.info("Opt-in recording started: %s", record_name)
        try:
            while not stop.is_set() and (stop_path is None or not stop_path.exists()):
                try:
                    data, address = sock.recvfrom(MAX_DATAGRAM_SIZE)
                except socket.timeout:
                    data = None
                else:
                    if sender_address is not None and address[0] != sender_address:
                        sender_rejected += 1
                        if sender_rejected == 1 or sender_rejected % 100 == 0:
                            LOGGER.warning("telemetry event=sender_rejected source=%s expected=%s count=%d",
                                           address[0], sender_address, sender_rejected)
                        data = None
                if data is not None:
                    now = time.monotonic()
                    if capture is not None:
                        try:
                            has_room = capture.write(now - started, data)
                        except OSError as exc:
                            LOGGER.exception("recording event=manual_write_error name=%s bytes=%d error=%s",
                                             record_name, capture.size, exc)
                            capture.close()
                            capture = None
                            if capture_summary is not None:
                                try:
                                    save_summary(capture_summary, "incomplete", "write_error")
                                except OSError as metadata_exc:
                                    LOGGER.warning("Could not save recording metadata: %s", metadata_exc)
                                capture_summary = None
                            print("Recording stopped after a write error; telemetry reception continues.",
                                  file=output, flush=True)
                            has_room = True
                        if not has_room:
                            LOGGER.warning("recording event=manual_size_limit name=%s bytes=%d "
                                           "limit_bytes=%d", record_name, capture.size, capture.max_bytes)
                            capture.close()
                            capture = None
                            if capture_summary is not None:
                                try:
                                    save_summary(capture_summary, "size_limit", "size_limit")
                                except OSError as exc:
                                    LOGGER.warning("Could not save recording metadata: %s", exc)
                                capture_summary = None
                            message = "Recording stopped; telemetry reception continues."
                            print(message, file=output, flush=True)
                            LOGGER.warning(message)
                    previous_status = stats.status(now)
                    header = process_packet(stats, state, data, now)
                    if header is None and auto_recorder is not None:
                        auto_recorder.reject(stats.last_error or "invalid_packet")
                    if header is not None and capture_summary is not None:
                        capture_summary.observe(now - started, data, header)
                    if header is not None and auto_recorder is not None:
                        session_observation = state.get_fresh(1, now) if header.packet_id == 1 else None
                        # Only this datagram's successfully decoded Session may
                        # start or switch a capture. A malformed Session header
                        # can otherwise inherit the previous cached Session.
                        session = (session_observation.value if session_observation is not None
                                   and session_observation.observed_at == now
                                   and session_observation.header == header
                                   and isinstance(session_observation.value, Session) else None)
                        auto_recorder.observe(now, data, header, session)
                    latest = state.get_fresh(header.packet_id, now) if header is not None and header.packet_id in (1, 7, 10) else None
                    if (header is not None and latest is not None
                            and latest.header.frame_identifier == header.frame_identifier):
                        for alert in engineer.on_packet(state, now):
                            priority = "critical" if alert.priority < 0 else "high" if alert.priority == 0 else "normal"
                            print(f"Radio [{priority}]: {alert.message}", file=output, flush=True)
                            dashboard_state.message(alert.message)
                            real_time_alert_messages.add(alert.message)
                            if radio is not None:
                                radio.say(alert.message, 0 if alert.priority < 0 else 1 if alert.priority == 0 else 2)
                            if history is not None:
                                lap_obs = state.get_fresh(2, now)
                                lap_number = lap_obs.value.lap_number if lap_obs and hasattr(lap_obs.value, "lap_number") else None
                                history.record_advice(state.session_uid, lap_number, alert.message)
                    if history is not None and header is not None and header.packet_id == 3:
                        history.observe(state, now)
                    if header is not None and previous_status != "receiving":
                        message = (
                            f"Valid F1 22 packet received: ID {header.packet_id} "
                            f"({header.packet_name}), session={header.session_uid}, "
                            f"frame={header.frame_identifier}, player={header.player_car_index}. "
                            "Sender identity and actual gameplay are not verified by UDP alone."
                        )
                        print(message, file=output, flush=True)
                        LOGGER.info(message)

                now = time.monotonic()
                current_status = stats.status(now)
                if current_status != connection_status:
                    LOGGER.info("telemetry event=connection_%s previous=%s uid=%s "
                                "last_valid_age_s=%s valid=%d invalid=%d",
                                current_status, connection_status,
                                stats.last_header.session_uid if stats.last_header else None,
                                round(now - stats.last_valid_at, 1) if stats.last_valid_at is not None else None,
                                stats.valid, stats.invalid)
                    connection_status = current_status
                if auto_recorder is not None:
                    auto_recorder.tick(now)
                    if now >= next_recording_diagnostic_at:
                        auto_recorder.diagnostics(now)
                        next_recording_diagnostic_at = now + 30.0
                if (dashboard and not performance_mode or radio is not None) and now - last_facts_at >= 0.5:
                    try:
                        dashboard_state.publish(race_facts(state, engineer, now),
                                                stats.status(now), performance_mode,
                                                radio.status if radio is not None else "off")
                    except (TypeError, ValueError) as exc:
                        LOGGER.warning("Could not publish race facts: %s", exc)
                    last_facts_at = now
                report_interval = 10.0 if performance_mode else REPORT_INTERVAL_SECONDS
                if now - stats.last_report_at >= report_interval:
                    if history is not None:
                        history.observe(state, now)
                    print(log_report(stats, now), file=output, flush=True)
                    snapshot = state.format_snapshot(now)
                    print(snapshot, file=output, flush=True)
                    LOGGER.info("%s", snapshot)
                    engineer_report = engineer.report(state, now)
                    print(engineer_report, file=output, flush=True)
                    LOGGER.info("%s", engineer_report)
                    for line in engineer_report.splitlines():
                        if "Alert [" in line:
                            message = line.strip().split(": ", 1)[-1]
                            if message in real_time_alert_messages:
                                continue
                            dashboard_state.message(message)
                            if radio is not None:
                                radio.say(message, 0 if "[critical]" in line else 1 if "[high]" in line else 2)
                            if history is not None:
                                lap_obs = state.get_fresh(2, now)
                                lap_number = lap_obs.value.lap_number if lap_obs and hasattr(lap_obs.value, "lap_number") else None
                                history.record_advice(state.session_uid, lap_number, message)
                        elif line.strip().startswith("Pit strategy: "):
                            message = line.strip()
                            strategy_key = (state.session_uid, message)
                            if (strategy_key != last_strategy_key and "unavailable" not in message
                                    and "no decisive" not in message):
                                dashboard_state.message(message)
                                if radio is not None:
                                    radio.say(message, 1)
                                if history is not None:
                                    lap_obs = state.get_fresh(2, now)
                                    lap_number = lap_obs.value.lap_number if lap_obs and hasattr(lap_obs.value, "lap_number") else None
                                    history.record_advice(state.session_uid, lap_number, message)
                            last_strategy_key = strategy_key
                    real_time_alert_messages.clear()
        finally:
            if capture is not None:
                capture.close()
                if capture_summary is not None:
                    try:
                        save_summary(capture_summary)
                    except OSError as exc:
                        LOGGER.warning("Could not save recording metadata: %s", exc)
            if auto_recorder is not None:
                auto_recorder.close()
            if radio is not None:
                radio.close()
            if dashboard_server is not None:
                dashboard_server.should_exit = True
                if dashboard_thread is not None:
                    dashboard_thread.join(timeout=2)
            if history is not None:
                history.close()
            log_report(stats, time.monotonic(), final=True)
            if sender_rejected:
                LOGGER.info("telemetry event=sender_filter_summary rejected=%d expected=%s",
                            sender_rejected, sender_address)


def replay_capture(output: TextIO, name: str, alert_config: AlertConfig | None = None,
                   strategy_assumptions: StrategyAssumptions | None = None) -> None:
    stats = ReceptionStats(started_at=0.0, last_report_at=0.0)
    state = RaceState()
    engineer = Engineer(alerts=AlertMonitor(alert_config or AlertConfig()),
                        strategy_assumptions=strategy_assumptions or StrategyAssumptions())
    last_time = 0.0
    count = 0
    for now, data in read_capture(name):
        count += 1
        last_time = now
        header = process_packet(stats, state, data, now)
        latest = state.get_fresh(header.packet_id, now) if header is not None and header.packet_id in (1, 7, 10) else None
        if (header is not None and latest is not None
                and latest.header.frame_identifier == header.frame_identifier):
            engineer.on_packet(state, now)
        if now - stats.last_report_at >= REPORT_INTERVAL_SECONDS:
            print(stats.report(now), file=output)
            print(state.format_snapshot(now), file=output)
            print(engineer.report(state, now), file=output)
    print(f"Replay finished: {count} datagrams; {stats.report(last_time)}", file=output)
    print(state.format_snapshot(last_time), file=output)
    print(engineer.report(state, last_time), file=output)
