# Telemetry reliability and offline latency check — 2026-10-30

## Scope and result

The full test suite passed (171 tests), the three inline JavaScript blocks passed `node --check`, and `scripts/verify_grand_prix.py` replayed the original 140,578-packet Bahrain capture. Its SHA-256 remains `91d7ca520d1a572610bbd37cfd0b7f22e1e3f16c0ccb442892b5d3c21a081888`. This is **offline validation**. No Career/LAN gameplay, FPS, browser paint latency, actual UDP delivery rate, or Safety Car Broadcast Experience was measured in this pass.

The first pytest attempt used `C:\Users\saikr\AppData\Local\Temp` and failed to create test directories there. It passed after using `--basetemp data\tmp\pytest-reliability-20261030-a` on D:. This was a test-environment permission failure, not a product regression.

## Pipeline investigation

The receiver calls `recvfrom`, validates the header and payload, applies state, writes raw captures, updates engineer lap observations and submits dashboard snapshots. SQLite operations already run on a bounded background worker. Before this change, `race_facts` and JSON/map construction ran on the UDP receive thread at each 0.5-second publication. The browser uses a WebSocket when the optional FastAPI stack is installed, otherwise 0.5-second HTTP polling. The 0.5-second publication cadence and fallback polling can each add visible waiting; the expensive portion measured in the old receiver path is fact construction and JSON/map publication. Browser rendering and game frame pacing remain unmeasured.

The receiver now copies bounded state at publication time and a one-slot worker builds dashboard facts and JSON. If it falls behind, it coalesces **display snapshots only**. UDP packet processing and raw capture are not queued through that worker. The WebSocket wakes when a new snapshot is published; the HTTP fallback still polls at 0.5 seconds. This reduces time spent on dashboard analysis in the receiver, but does not prove a lower end-to-end gameplay delay.

## Offline benchmark

Ran on the local Windows 11 machine using `scripts/benchmark_telemetry.py`, the unchanged 164,005,729-byte Bahrain `.f1cap`, 2,714 generated dashboard frames and 30 loopback HTTP requests for a representative 51,456-byte snapshot. Full machine specifications and competing load were not controlled. The full local JSON output is ignored by Git; the table below records the measurements needed for this review.

| Measurement | Copied snapshot model | Inline model |
| --- | ---: | ---: |
| Capture packets processed | 140,578 | 140,578 |
| Processing wall time | 21.262 s | 34.181 s |
| Process CPU time during capture processing | 20.609 s | 32.500 s |
| Throughput | 6,611.7 packets/s | 4,112.8 packets/s |
| Peak process resident RAM | 54.13 MiB | 53.80 MiB |
| Decode/state mean per packet | 0.0656 ms | 0.1053 ms |
| Snapshot copy mean / p95 per frame | 0.2379 / 0.4407 ms | n/a |
| Race facts mean per frame | 1.6974 ms | 3.1504 ms |
| Dashboard JSON/map mean per frame | 1.5909 ms | 2.4298 ms |
| HTTP loopback mean / p95 | 4.7281 / 20.1051 ms | 10.0845 / 16.6171 ms |

These are separate, unpaired runs; CPU scheduling and other machine load varied. The copied-snapshot benchmark executes the receiver and publisher stages sequentially and **models** their stage costs; it does not measure actual thread overlap, UDP socket buffering, capture disk writes, WebSocket transit, browser paint, or game FPS. Do not interpret the throughput difference as a proven in-game speedup. The measured per-frame facts and JSON cost motivated moving them off the UDP path. The process CPU percentage in the JSON is CPU time divided by wall time for this accelerated offline workload, not expected CPU use while playing.

Run again from the project root:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_telemetry.py
.\.venv\Scripts\python.exe scripts\benchmark_telemetry.py --inline
.\.venv\Scripts\python.exe -m pytest -q --basetemp data\tmp\pytest-reliability-local
.\.venv\Scripts\python.exe scripts\check_dashboard_js.py
.\.venv\Scripts\python.exe scripts\verify_grand_prix.py
```

## Reliability evidence and limits

The tests exercise Career completion, network-style race past lap 2, two successive races, partial stop, size rollover, replay discovery, unchanged raw bytes, malformed and out-of-order capture timestamps, temporary silence and resume, SC/VSC transitions, forward skipped intervals, flashback, DNF and DSQ. A real LAN host/client or in-game SC sequence is still needed. Frame identifier discontinuities are counted as observations, **not** asserted packet loss: packet types transmit at different rates. UDP source IP/port is observed but not authenticated. A missing datagram cannot be reconstructed; a replay of a truncated or unreadable capture now surfaces an error in the library.

The live log adds packet counts and rates by ID, header/payload rejects, frame reorder/duplicates/discontinuities, source changes, last decoded age, connection changes, receiver stage durations, dashboard copy/queue/build time and packet-to-publication age, history queue counts, and recording boundary/byte diagnostics. The latter recording diagnostics were added in prior work. Capture records preserve datagram bytes; timestamps must be finite, nonnegative and ordered. New captures use exclusive creation. Existing raw fixtures were not rewritten.

## Real-game acceptance measurements

1. On the target machine, record the game settings (resolution, graphics preset, FPS cap, UDP 2022 mode/rate/address, Safety Car Experience and session mode). Run a Career race and then LAN host and client races. Include more than two laps, a reconnect, a pit stop, a finish and, where possible, SC/VSC. Keep the raw capture, metadata and `logs/race-engineer.log` on D:.
2. Compare log packet-ID rates, invalid counts, source changes, age, recording boundaries and published snapshot delays with the capture/replay timeline. A short silence must keep the recording open and yield an unobserved interval on resume. Check that distinct races have distinct files and that the replay library reports honest completion evidence. Compare display changes against the game HUD and note approximate time offsets.
3. For performance, use the same track, session, weather, grid, graphics settings and capture tool in at least one game-only run and one run with the live companion/dashboard. Record game FPS, 1% low or frame-time p95/p99, and companion process CPU/RAM at fixed intervals using an in-game performance overlay or equivalent external tool. Repeat if run-to-run variation is large. Measure dashboard response and visual smoothness on the actual display. No FPS or frame-time values are claimed here.
4. If a packet interval looks missing, compare sender configuration, receiver validity/rates, source identity and raw capture before attributing it to UDP loss. The game may skip transmitted driving data during Broadcast Safety Car sequences.
