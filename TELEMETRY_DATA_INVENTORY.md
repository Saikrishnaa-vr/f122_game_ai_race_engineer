# F1 22 telemetry data inventory for race analysis

**Audience:** a collaborator designing useful, evidence-based information for the driver. This describes what the companion currently receives, decodes, saves, and derives. It is an implementation inventory, not a claim that every game field has been validated against the HUD.

## Data flow and where to find it

F1 22 sends its **2022 UDP format** to `127.0.0.1:20777` by default. An explicitly configured LAN listener may bind another IPv4 address and filter by sender IP. The receiver validates each packet's header and exact size, decodes selected fields, keeps a freshness-aware in-memory race state, and publishes dashboard facts about every 0.5 seconds. The default `--drive` launcher also writes:

| Artifact | Location under this project | Contents |
| --- | --- | --- |
| Raw race capture | `recordings/*.f1cap` | Original UDP datagrams with elapsed monotonic time per packet. Replaying these runs current decoders and dashboard code against the old race. This is the richest source for new analysis. |
| Recording metadata | `recordings/*.json`, when a sidecar has been saved | Track, local start date/time, player name, session type/name, game mode ID/name, online/offline flag when observed, scheduled/last lap, count of distinct lap numbers observed, last observed position, duration, packet count, status, completion evidence, stop reason, participant count/player indices, and prior segment when a 2 GiB file boundary is crossed. `incomplete` means no `SEND` or classification was captured. Older captures may have no sidecar; the replay library derives metadata by scanning the capture. |
| Structured history | `data/races.sqlite3` | Sampled player laps, game-reported sectors, selected event codes, issued advice, session details, and practice setup labels. It is much smaller than a raw capture. |
| Diagnostic log | `logs/race-engineer.log` | Receiver status, packet counts, errors, snapshots, and engineer reports. Useful for debugging, not the canonical analysis dataset. |
| Dashboard facts | `app/intelligence.py` via the local dashboard | Fresh decoded fields, full running order, map data, and estimates. These are generated in memory; the dashboard snapshot is not a separate historical database. |

All these race-engineer paths are under this D: project for source runs. The launchers also direct their process temporary files to `data/tmp` on D:. The repository's `.gitignore` excludes `data/`, `logs/`, and `recordings/`. Two deliberately published compressed captures are in `fixtures/`; run `scripts/restore_replay_fixtures.py` in a fresh checkout to reconstruct the raw races. Raw captures include player and participant names; review them before sharing.

The raw capture retains packet bytes even when the app does not currently decode their payload. The structured database stores only selected, sampled values. The raw capture uses a custom `F122CAP1` header followed by repeated little-endian `<double elapsed_seconds, uint16 packet_size, packet_bytes>` records; use `app/telemetry/capture.py:read_capture()` instead of parsing it from scratch.

Automatic capture defaults to race Session codes 10, 11 and 12. `--auto-record-sessions weekend` includes practice 1–4 and qualifying 5–9; `all-driving` additionally includes Time Trial 13. The 2022 Session packet has a separate `m_gameMode` ID that can identify Grand Prix, Career '22, Online Custom and other documented modes; there is no dedicated LAN host/client indicator. `m_networkGame` says only offline (0) or online (1), not UDP broadcast state. Recordings from a network session can include changing player indices and participant counts; those observations do not prove who joined or left without additional evidence. A gap in packet arrival does not end a recording. The replay library labels old sidecars with no end evidence as unknown confidence rather than rewriting the raw data.

## Decoded data available today

`app/telemetry/payloads.py` is the field-level source of truth. Four-wheel arrays throughout are **rear-left, rear-right, front-left, front-right**. Times ending in `_ms` are milliseconds; `_m` is metres; `_c` is Celsius. Most game enum values remain numeric codes.

| Game packet (ID) | Current decoded fields | Scope and use |
| --- | --- | --- |
| Header (every packet) | Session UID, game session time, frame identifier, packet type, player car index, game/version fields | Join packets within a session; detect a new session, reordered frames, or a flashback. Capture elapsed time is separate from game session time. |
| Motion (0) | Player world position, 3D velocity, G force, orientation; world positions for up to 22 car slots | Track trace, car markers, movement/slow-car inference. The map uses the horizontal X/Z coordinates. Other cars' velocity/G force are **not** decoded. |
| Session (1) | Session type, track ID and length, total laps, time left, current weather, track/air temperature, safety-car state, weather forecast samples, marshal-zone fractions and flags, game's ideal/latest pit laps | Race context, SC/VSC and flag boards, pit-window reminder, forecast context. Forecast samples include future offset, weather, track/air temperature, and rain percentage. |
| Lap Data (2) | Player and other valid cars' running position, lap number, lap distance, last lap time, current S1/S2 times, pit status, stop count, invalid-lap flag, penalties, result status | Running order, gaps, relative pace, pit transitions, lap invalidation and game-reported DNF/retirement/DSQ. Player also has current-lap time and current sector. These are shared game values, **not** an official timing feed. |
| Event (3) | Four-character event code only | Event/flashback detection. Event-specific detail bytes are not decoded. Repeated `BUTN` and `SPTP` codes are excluded from new structured event history. |
| Participants (4) | Player driver/team/race number/name/telemetry setting; names, team IDs and race numbers for active car slots | Driver labels and team-colour mapping. Colours and three-letter codes are derived locally; the game does not transmit those display colours. |
| Car Setups (5) | A short hash of the player's 49-byte setup payload | Groups practice laps by observed setup. **No individual wing, suspension, gearing, or preset parameters are decoded**, and the preset name is a user-entered label. |
| Car Telemetry (6) | Player speed, throttle, steering, brake, gear, RPM, DRS, four tyre surface/inner temperatures, four pressures | Driving state and tyre-temperature context. Other cars' pedal and tyre telemetry are **not** decoded. |
| Car Status (7) | Player fuel/capacity, game's fuel-remaining-laps field, actual/visual tyre compound codes, tyre age, ERS energy/deploy mode/harvest/deployed totals, DRS allowed | Fuel/ERS monitoring and stint context. The current application does **not** decode usable rival fuel, tyres, or ERS from this packet. |
| Car Damage (10) | Player four-wheel wear, tyre and brake damage, left/right front and rear wing, gearbox and engine damage | Wear and damage alerts. No rival damage is decoded. |
| Session History (11) | Per-car recorded lap times, S1/S2/S3 times, validity flags, best-lap number, tyre-stint tuples | Fresh grid best laps, sector leaders, recent five laps per driver. Tyre-stint tuples are decoded in memory but are **not** exposed as reliable live rival tyre state in current dashboard logic or stored in SQLite. |

Final Classification (8) and Lobby Info (9) pass packet validation and are counted. Their payloads are **not decoded**. The auto-recorder uses the presence of packet 8 to close a completed race; the recording's finish position comes from the last observed Lap packet, not a decoded classification. Packet sizes and checks are in `app/telemetry/protocol.py`.

## What the structured SQLite history actually keeps

`app/history.py` creates these tables:

| Table | Stored fields / interpretation |
| --- | --- |
| `sessions` | Session UID, first locally recorded UTC time, track ID, session type, scheduled laps, **last observed** position. |
| `laps` | One player row when a new lap is first observed: completed lap number and last-lap time, sampled position, fuel kg, maximum of four tyre-wear percentages, actual compound code, ERS MJ, pit-stop count, weather code, recording UTC, optional setup fingerprint. Fuel and other sampled values are taken at that observation; do not treat them as exact finish-line measurements or as per-corner data. |
| `sectors` | Player Session History lap time, three sector times, and raw validity flags for each available lap. The flags are stored but not yet interpreted in the debrief. |
| `events` | Event code, sampled lap, UTC. New writes omit `BUTN`/`SPTP`; older rows may remain. |
| `advice` | Alerts/strategy messages emitted by the engineer, sampled lap, UTC. This records what was said, not a proven outcome. |
| `setup_labels` | Track ID + setup fingerprint + user-entered preset label. |

The history worker has a bounded queue and can drop a record if overloaded; a packet capture is the better source for reconstructing a detailed timeline. `--no-history` disables SQLite writes. Manual receive without `--record` does not save raw packets; `--drive` automatically captures recognized race Session types 10/11/12. Practice/qualifying raw capture requires `--auto-record-sessions weekend`; `all-driving` also includes Time Trial. Practice laps can populate SQLite independently.

## Calculations already produced by the companion

These are **derived estimates or presentation**, not extra game telemetry:

| Output | Inputs and method | Confidence boundary |
| --- | --- | --- |
| Running order and car map | Shared Lap positions + Motion car coordinates + Participants | The game provides the ordering; bundled non-Bahrain outlines can give approximate display positions until a clean observed lap builds the game-coordinate route. |
| Gap to another car | Shared 25 m checkpoint crossing times when both cars cross the same point within 15 s; otherwise relative lap distance scaled by the player's last valid lap | An observed/estimated gap, not the game's official gap. Fallback and stale gaps must be labelled. |
| Pace needed to pass | Gap ahead plus a one-second passing allowance, divided by estimated laps left | Simple average required gain per lap; no traffic, tyre evolution, or overtake probability. |
| Fuel use/margin and finish projection | Comparable consecutive lap-start fuel readings; current fuel minus estimated kg/lap times laps remaining | Low confidence; assumes a constant future rate. The dashboard also graphs up to eight observed comparable lap burns and shows the target kg/lap if projected fuel is short. |
| Lift/coast candidate | Player speed/brake transitions and lap distance on at least two clean laps, plus a projected fuel shortfall | Approximate point 100 m before an observed heavy-braking zone; it is not a calibrated fuel-saving amount. Held under pit/SC/VSC conditions; absent if observations or fuel projection are missing. |
| Tyre wear trend/life | Comparable lap-start wear by wheel; linear extrapolation | Low confidence; 100% wear is a data bound, not a safe tyre-life target. |
| ERS trend | Two comparable lap-start energy observations | Energy trend only, not a deployment recommendation. |
| Pit/undercut-like projection | Shared gaps, latest valid lap pace, and a **user-supplied measured pit loss**; full SC and VSC each need their own calibrated factor | Indicative rejoin position/range; rival pit plans, traffic, changing pace, and exact pit timing are unknown. The strategy comparison also needs explicit fresh-tyre gain, wear pace cost, next compound, and related assumptions. Normal pace projections wait for a comparable green-flag lap after a gap or restart. |
| Pit, weather, SC/VSC, tyre, fuel, ERS, damage alerts | Fresh Session/Status/Damage values and provisional thresholds | Alerts tell the driver to reassess; they are not automatic pit instructions. |
| Practice preset comparison | Setup hash, user label, recorded practice lap times | Best and median lap are descriptive; fuel, traffic, weather, validity, and driving variation can confound comparisons. |
| Track limits markers | Transition of the player's lap-invalid flag, placed at the contemporaneous world position | Approximate **invalidation location**, not the exact track boundary or steward reason. |
| Stationary-car DNF fallback | Rival Lap distance plus Motion position compared while the player travels two full laps | Inferred only for a car still reported active and not in the pits or under SC/VSC or a telemetry gap. A transition or gap resets the clock; movement clears the inference. It is distinct from the game's result status. |

Dashboard questions use these facts and fixed answer logic. The optional LLM classifies a question's topic only; it does not generate telemetry or free-form recommendations. See `app/intelligence.py`, `app/gaps.py`, `app/rivals.py`, `app/tactics.py`, and `app/strategy.py`.

## Freshness, missing data, and analysis rules

- Driving, Motion, Lap, and Car Status observations expire after **2 s**; Session and Damage after **4 s**; Event and Session History after **10 s**. Participants and setup fields have a 15 s fresh view. The grid deliberately retains the last valid participant roster within the same session for stable names, while the raw field still reports freshness.
- Session UID/player changes and `FLBK` clear live state. Two consistent packets from a game clock more than 2 s behind also trigger a reset if the flashback event was missed. Do not join a pre-flashback estimate to post-flashback driving.
- UDP can be lost, delayed, or out of order. Zero, absent, stale, and invalid are different states. A displayed `null` may mean no packet yet, an expired observation, or insufficient model inputs.
- Rival lap/position, motion positions and game-reported history are available; **current decoded rival tyre wear, fuel, ERS, setup, damage, and future stops are not**. Avoid claiming an undercut is certain or inferring an opponent's compound from team colour or lap time alone.
- Session marshal-zone flags indicate flagged sections; a slow-car marker is inferred from movement. The completed saved Bahrain race did not contain SC/VSC or a player's invalid-lap transition, so those particular displays have only controlled test coverage so far.
- The game-provided weather forecast is an input, not a calibrated rain-to-dry tyre crossover model. Game-reported fuel laps and pit window are hints, not measured strategy outcomes.
- `app/race_timeline.py` keeps bounded, raw-derived transition evidence for the dashboard and replay: capture-relative observation time, game session time, packet ID, lap, SC/VSC/green status changes, pit entry/exit, `FLBK`, `SEND`, and packet/time/lap gaps. Session safety code 3 means formation lap; it is not labelled green. If the Session packet is older than 4 s while other packets arrive the current race status is **UNKNOWN**; after 5 s without an accepted race packet it is **STALE**. The last confirmed status remains available as historical evidence only.
- A forward jump and missing packets identify an **unobserved interval**, not the Safety Car Experience setting, a confirmed flashback, a DNF, or a completed race. The raw capture retains only packets actually transmitted; replay inserts a stale display frame but no intermediate car values. `FLBK` is explicit flashback evidence; a confirmed backward game-clock sequence is separately labelled `clock_rewind` because it could also be a same-UID restart.
- During neutralization and until new comparable green-flag laps are available, distance-derived gaps, chase pace, fuel/tyre rate projections, lift cues and inferred stationary DNF are withheld or reset. Shared same-checkpoint gaps can still be displayed when newly observed. SC and VSC pit-loss factors are separate optional measurements. The game's Safety Car Broadcast Experience is not transmitted as a decoded UDP setting; UDP Broadcast Mode concerns LAN packet delivery only.

## Local sample data and a productive first analysis

The main regression fixture is `recordings/grand-prix-20261028-212717.f1cap`: **140,578 datagrams**, 14-lap Bahrain Grand Prix (track code 3), 20-car grid in replay, one observed stop, two Final Classification packets, zero header/payload decode errors in the recorded verification. It is 164,005,729 bytes with SHA-256 `91d7ca520d1a572610bbd37cfd0b7f22e1e3f16c0ccb442892b5d3c21a081888`. Its last observed position was P1; the final-classification position is not decoded. See `reports/grand-prix-20261028-212717.md` for what was and was not verified. `recordings/verification-20261028-193443.f1cap` is a separate ten-completed-lap capture with a soft-to-medium stop. A tiny eight-byte capture, if present, contains only the file header and is **not** a useful race dataset.

For a first analysis, reconstruct per-lap player fuel, wear, compound, lap/sector pace, position, and pit status; annotate comparable versus pit/traffic/flag-affected laps; then compare the advice that would have been shown with what happened later in the replay. Report which statements are measured, estimated, or unavailable. To evaluate undercut predictions, first measure actual pit-lane time loss from this and later races, then back-test projected versus observed rejoin positions without using information from after the decision point.

Run the built-in capture check from the project folder with `.\.venv\Scripts\python.exe scripts\verify_grand_prix.py`. For structured summaries, `.\.venv\Scripts\python.exe -m app --debrief SESSION_UID` and `.\.venv\Scripts\python.exe -m app --export-history SESSION_UID csv laps.csv` use the local SQLite data. Replace `SESSION_UID` with a value from `sessions.uid` in `data/races.sqlite3`. The replay viewer can load the saved race without launching F1 22. Sharing the source repo alone will not share these recordings; use a deliberate, private transfer if your friend needs raw examples.
