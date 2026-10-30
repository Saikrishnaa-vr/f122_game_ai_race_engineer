"""Offline receiver/dashboard benchmark using an existing raw F1 22 capture.

Run from the project root: python scripts/benchmark_telemetry.py
This never modifies the capture. Results describe this machine and this workload,
not in-game FPS, UDP delivery, browser rendering, or packet loss.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import ctypes
import json
import os
from pathlib import Path
import statistics
import sys
import time
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dashboard import DashboardState, start_polling_dashboard
from app.dashboard_worker import copy_for_publication
from app.engineer import Engineer
from app.intelligence import race_facts
from app.telemetry.capture import capture_path, read_capture
from app.telemetry.receiver import ReceptionStats, process_packet
from app.telemetry.state import RaceState


DEFAULT_CAPTURE = "grand-prix-20261028-212717.f1cap"


def memory_mib() -> tuple[float | None, float | None]:
    """Current and peak process resident memory where the OS exposes them."""
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        info = Counters()
        info.cb = ctypes.sizeof(info)
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = ctypes.c_void_p
        get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory.argtypes = (ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong)
        if get_memory(get_process(), ctypes.byref(info), info.cb):
            return round(info.WorkingSetSize / 1048576, 2), round(info.PeakWorkingSetSize / 1048576, 2)
    elif Path("/proc/self/status").is_file():
        lines = Path("/proc/self/status").read_text(encoding="utf-8").splitlines()
        values = {parts[0].rstrip(":"): int(parts[1]) / 1024 for line in lines
                  if (parts := line.split()) and parts[0] in ("VmRSS:", "VmHWM:")}
        return round(values.get("VmRSS", 0), 2), round(values.get("VmHWM", 0), 2)
    return None, None


def describe(samples: list[float]) -> dict:
    if not samples:
        return {"samples": 0}
    ordered = sorted(samples)
    return {"samples": len(samples), "mean_ms": round(statistics.fmean(samples), 4),
            "p50_ms": round(ordered[(len(ordered) - 1) // 2], 4),
            "p95_ms": round(ordered[int((len(ordered) - 1) * 0.95)], 4),
            "max_ms": round(ordered[-1], 4), "total_s": round(sum(samples) / 1000, 3)}


def benchmark(name: str, max_packets: int = 0, http_requests: int = 30,
              snapshot_copy: bool = True) -> dict:
    path = capture_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"Restore the fixture first: {path}")
    stats = ReceptionStats(0.0, 0.0)
    state = RaceState()
    engineer = Engineer()
    dashboard = DashboardState()
    timings: dict[str, list[float]] = defaultdict(list)
    count = frames = 0
    snapshot_bytes_total = 0
    snapshot_max_bytes = 0
    representative_snapshot = ""
    last_publish = -0.5
    cpu_start, wall_start = time.process_time(), time.perf_counter()
    source = iter(read_capture(name))
    while max_packets <= 0 or count < max_packets:
        started = time.perf_counter_ns()
        try:
            now, packet = next(source)
        except StopIteration:
            break
        read_done = time.perf_counter_ns()
        header = process_packet(stats, state, packet, now)
        processed = time.perf_counter_ns()
        timings["capture_read"].append((read_done - started) / 1e6)
        timings["packet_decode_state"].append((processed - read_done) / 1e6)
        count += 1
        if header is None:
            continue
        latest = state.get_fresh(header.packet_id, now) if header.packet_id in (1, 7, 10) else None
        if latest is not None and latest.header.frame_identifier == header.frame_identifier:
            for _alert in engineer.on_packet(state, now):
                pass
        engineered = time.perf_counter_ns()
        timings["engineer"].append((engineered - processed) / 1e6)
        if now - last_publish < 0.5:
            continue
        last_publish = now
        view, analysis = (copy_for_publication(state, engineer)
                          if snapshot_copy else (state, engineer))
        copied = time.perf_counter_ns()
        if snapshot_copy:
            timings["receiver_snapshot_copy"].append((copied - engineered) / 1e6)
        facts = race_facts(view, analysis, now)
        factored = time.perf_counter_ns()
        dashboard.publish(facts, stats.status(now), False)
        published = time.perf_counter_ns()
        payload = dashboard.snapshot()
        size = len(payload.encode("utf-8"))
        snapshot_bytes_total += size
        if size > snapshot_max_bytes:
            snapshot_max_bytes = size
            representative_snapshot = payload
        timings["race_facts"].append((factored - copied) / 1e6)
        timings["dashboard_publish_json"].append((published - factored) / 1e6)
        frames += 1
    wall_seconds = time.perf_counter() - wall_start
    cpu_seconds = time.process_time() - cpu_start
    current_ram, peak_ram = memory_mib()

    http_times: list[float] = []
    if http_requests and frames:
        class RepresentativeState:
            def snapshot(self) -> str:
                return representative_snapshot

        thread, server = start_polling_dashboard(RepresentativeState(), port=0)
        try:
            port = server.server.server_address[1]
            for _ in range(http_requests):
                start = time.perf_counter_ns()
                with urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as response:
                    payload = response.read()
                http_times.append((time.perf_counter_ns() - start) / 1e6)
                if response.status != 200 or not payload:
                    raise RuntimeError("dashboard HTTP response was empty or failed")
        finally:
            server.should_exit = True
            thread.join(timeout=3)

    return {"capture": name, "capture_bytes": path.stat().st_size,
            "analysis_mode": "copied snapshot (models receiver/worker split)" if snapshot_copy else "inline baseline",
            "packets": count, "valid_headers": stats.valid, "invalid_headers": stats.invalid,
            "invalid_payloads": stats.payload_invalid, "dashboard_frames": frames,
            "wall_seconds": round(wall_seconds, 3), "cpu_seconds": round(cpu_seconds, 3),
            "cpu_percent_of_one_core": round(cpu_seconds / wall_seconds * 100, 1),
            "packets_per_wall_second": round(count / wall_seconds, 1),
            "ram_current_mib": current_ram, "ram_peak_mib": peak_ram,
            "snapshot_mean_bytes": round(snapshot_bytes_total / frames) if frames else 0,
            "snapshot_representative_bytes": snapshot_max_bytes,
            "stages": {key: describe(value) for key, value in timings.items()},
            "http_loopback": describe(http_times),
            "scope": "offline capture read, receiver decode/state, engineer, dashboard JSON, local HTTP; no game, UDP ingress, browser render or FPS"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", default=DEFAULT_CAPTURE)
    parser.add_argument("--max-packets", type=int, default=0,
                        help="first N packets only; zero means the full capture")
    parser.add_argument("--http-requests", type=int, default=30)
    parser.add_argument("--inline", action="store_true",
                        help="measure the former inline analysis path without copying")
    options = parser.parse_args()
    if options.max_packets < 0 or options.http_requests < 0:
        parser.error("counts must be non-negative")
    print(json.dumps(benchmark(options.capture, options.max_packets, options.http_requests,
                               snapshot_copy=not options.inline), indent=2))
