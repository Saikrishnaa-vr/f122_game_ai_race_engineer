# Dashboard track map and grid — offline development, 2026-10-28

## Implemented

- The local dashboard draws an approximate circuit outline from sampled player world coordinates during a clean lap. It shows current car markers only when both Motion and Lap packets are fresh. The route is bounded to 512 points and resets on session changes or flashbacks.
- The race grid lists cars with shared position and lap timing, relative distance along the race, last-lap comparison, current sectors and pit state. Names come from the Participants packet when available.
- Per-car Session History packets supply the game's best-lap number and the most recent five recorded laps and sectors. Choosing a grid row shows that log. An expired history clears best-lap and recent-lap details while current Lap data may remain available.
- The engineer can answer a fastest-lap question from fresh game-reported best laps. Rival fuel, tyre condition and ERS remain unknown.

## Offline evidence

- The 103,702-datagram verification capture passed the new dashboard data path with zero decode exceptions. Its snapshots reached 20 grid entries and 20 map markers. A clean observed lap produced a 97-point outline; up to 19 cars had a fresh game-reported best lap in one snapshot.
- Automated tests cover all-car Motion and Participants decoding, invalid rival slots, per-car history freshness and reset, full-grid comparisons, map completion, pit-lane trace rejection and stale markers, the fastest-lap answer, and the dependency-free dashboard's HTTP state/question routes. The full suite passes 98 tests. Python compilation, dashboard JavaScript syntax and a simulated DOM rendering check pass.
- No game files or controls were changed. The dashboard remains a separate loopback web page.

## Combined gameplay check still needed

The current Grand Prix run has started the dependency-free dashboard at `127.0.0.1:8765`; it responds to local HTTP requests while waiting for game packets. During play, compare the learned outline and car markers to observed positions, check the selectable logs and fastest-lap answer, and measure game FPS/frame pacing and companion CPU/RAM. The FastAPI/uvicorn WebSocket path and packaged Windows application have not been run here. The track outline is an observed approximation, and Session History validity flags are displayed as recorded timing without being interpreted as valid/invalid laps.
