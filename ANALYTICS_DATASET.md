# Offline race analytics dataset

Source: EA's [F1 22 UDP specification and v16 attachment](https://forums.ea.com/discussions/f1-games-franchise-discussion-en/f1-22-udp-specification/8418392), compared with the 2022 decoder and the original Bahrain capture on 2026-10-30. This is a field audit of what the companion **uses**; `recordings/*.f1cap` retains every received byte, including fields that are not decoded. The downloaded specification is a local, ignored reference under `data/tmp/`.

## Packet coverage and availability

| Packet | Decoded into application objects | Received, but not decoded or not retained in analytics | Limits |
| --- | --- | --- | --- |
| Header, all IDs | Format/version, ID, UID, game session time, frame, player index | Secondary player index and some game version detail are validated/available in the header but not analysis dimensions | UID is a session key, not a driver identity. Capture time is separate. |
| 0 Motion | Player 3D position, velocity, G force, orientation; all car world positions | Per-rival velocity/orientation, wheel forces, suspension, angular motion, local velocity | Analytics samples player trace at 5 Hz, not every raw packet. |
| 1 Session | Weather/temperature, session/game/network codes, track/laps, SC state, marshal zones, forecasts, pit window | Most assists, rules, pit lane speed, track geometry, brake temperature assist and other session settings | Forecast is game reported, not a rain or tyre model. Network bit does not identify LAN host/client. |
| 2 Lap | Player and rival position, lap/distance, lap/sector timings, pit status/stops, invalid flag, penalties, result status | Detailed warning/penalty counters, pit lane timer, grid positions and some per-car timing fields | Shared timing is game reported. Lap history is preferred for finalized sectors. |
| 3 Event | Four-character code | Event-specific payload unions such as penalty, speed trap, retirement details | An event code alone may not identify the affected driver. |
| 4 Participants | Active names, team IDs, numbers; player telemetry privacy setting | AI flag, nationality, online ID, platform and per-rival privacy setting | Online ID depends on the game's display/privacy settings. |
| 5 Car Setups | **Player** 49-byte setup hash and all v16 setup parameters: wings, diff, camber/toe, suspension, anti-roll bars, heights, brakes, tyre pressures, ballast, fuel load | Rival setup arrays | EA says other human player setups appear blank in multiplayer; no rival setup inference. Game preset label is not transmitted. |
| 6 Car Telemetry | Player speed, pedals, steering, gear, RPM, DRS, tyre temperatures and pressures | Rival telemetry, clutch, brake temperatures, engine temperature, surface type | Rival telemetry may be restricted. |
| 7 Car Status | Player fuel, capacity, game fuel-laps, compounds, tyre age, ERS, DRS allowed | Fuel mix, max RPM, traction, vehicle FIA flags and rival status fields | EA specifies restricted rival status fields can be zeroed. No rival private fuel/ERS claim. |
| 8 Final Classification | **All reported cars**: final position/laps/grid/points/stops/result, best lap, total time, penalties and stint arrays | None of the v16 final classification fields essential to this dataset | A received classification is evidence of a result; absence is not proof of a DNF. |
| 9 Lobby Info | Header/size only | Lobby player team/name/status arrays | Lobby is not a race result or analytics target. |
| 10 Car Damage | Player wear, tyre/brake and selected wing/gearbox/engine damage | Detailed engine component wear/faults, rivals | EA specifies restricted rival damage may be zeroed. |
| 11 Session History | Per-car completed lap and sector times, validity bit flags, best-lap index, tyre stint triples | Best-sector indices | `m_numLaps` includes the current partial lap. The extractor rejects zero/partial times as targets. Valid lap is bit 0x01. |

Fields listed as “received but not decoded” are potentially recoverable by reprocessing the original capture after decoder work. Fields absent because a game setting or a rival's privacy choice suppresses them are **unavailable** in that capture. Derived gaps, fuel burn, wear rate, pit loss and pace are **estimates**, not extra game fields. In particular, rival private fuel, wear, setup, ERS and future pit plans are unavailable to this analysis.

## Build and query

From the project root:

```powershell
.\.venv\Scripts\python.exe scripts\extract_analytics.py grand-prix-20261028-212717.f1cap
.\.venv\Scripts\python.exe scripts\extract_analytics.py grand-prix-20261028-212717.f1cap --replace --as-of 900
```

The extractor reads the canonical raw capture, checks each datagram, and writes `data/analytics/<capture>.sqlite3` on D:. It records the source SHA-256 and refuses to overwrite an existing dataset unless `--replace` is explicit. Replacement affects only this derived database. Re-run after a decoder or analysis change. No Parquet/DuckDB dependency is needed yet: SQLite gives indexed, portable, inspectable tables for the current capture size. Extraction is offline and never runs in the UDP receive loop. The existing `data/races.sqlite3` remains the lightweight live history.

| Table | Grain and origin |
| --- | --- |
| `metadata`, `packet_counts`, `rejected_packets` | Capture identity/hash, validated/decoded/rejected datagram counts. An invalid header's claimed packet ID is untrusted. |
| `segments` | UID, timeline epoch and player index. A flashback/confirmed clock rewind, new UID or player-index change starts a separate analysis segment. An epoch is not necessarily a new race. |
| `observations` | Sampled player driving/motion/fuel/wear, shared driver progress, participants, setup, weather/forecast, pit and race transitions, event codes. Every row has capture elapsed time, game time, packet ID/frame, source, quality and join freshness. |
| `driver_laps` | First seen completed Session History lap and three sectors per car, with raw validity flags and first-observed time. |
| `tyre_stints` | Append-only game-reported Session History stint revisions, per car. End lap 255 means current stint. |
| `lap_boundaries` | Player completed lap and fresh sampled fuel/wear/compound/setup at the first observed next-lap packet. `current_lap_invalid` refers to the new lap, so analyses use Session History validity for the completed lap. |
| `final_results` | First observed final classification per car; absent until packet 8 arrives. |
| `gaps` | Explicit unobserved intervals from the shared race timeline. No car state is interpolated through them. |

`game_reported` marks values directly decoded from valid packets; `valid`/`missing` describe joined lap-boundary inputs; `estimated` marks derived summaries; `unobserved` marks timeline gaps. A fresh join must meet the live `RaceState` time threshold; absent/expired joins are `null`, never a carried-forward measurement. Raw datagrams remain available for exact packet-level work. Normalized traces are intentionally sampled to limit database size; use raw capture when 20 Hz detail matters.

## Initial analyses and validation boundaries

`summarize_dataset(..., as_of_elapsed_s=t)` excludes observations first seen after `t`. Comparable fuel and wear differences require adjacent valid green laps, the same compound/stint, fresh boundary measurements, no pit-affected lap, no observed SC/VSC packet and no unobserved interval. They are between boundary samples, not an exact ECU fuel-flow measurement. Lap-time change uses the same filter. Pit-lap excess compares an observed stop lap with nearby earlier green laps; it is **not** a calibrated pit-lane loss. Rival relative pace uses common valid lap numbers and public Session History times. Braking bins group repeated player trace samples in 100 m distance bins; they are not a circuit corner definition. Practice setups group valid completed laps by observed hash, but fuel, traffic and weather still confound the comparison. SC/VSC output currently counts changes in Session weather/status observations; it is not a duration measurement.

The sample backtest predicts each player lap from the median of the last three **earlier observed** valid laps. It records both the prediction cutoff and target availability time. It is a temporal plumbing check, not a trained model or a claim of strategy accuracy. Predictions and targets must stay within one UID/epoch and use only information available before the target. Future models can use valid, observed-as-of lap/sector times, comparable lap-start fuel and wear, compound/stint, weather, track position/braking trace and public rival lap pace. Targets can be next-lap time, later wear/fuel, stop outcome or pace error only when the relevant result is eventually observed. Report error distributions and uncertainty by track, mode, compound and condition; obtain multiple representative races before fitting complex models. Keep training and backtesting offline. The live dashboard is unchanged by this dataset.

See the [Bahrain sample analysis](reports/bahrain-analytics-20261030.md) for actual output and limitations.
