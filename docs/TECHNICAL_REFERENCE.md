# Technical reference

[← Project overview](../README.md) · [User guide](USER_GUIDE.md) · [Telemetry inventory](../TELEMETRY_DATA_INVENTORY.md)

The raw F1 22 UDP capture is the source of record. Dashboards, history, and analytics are derived from packets that arrived.

**Jump to:** [recording](#session-and-recording-policy) · [race status](#freshness-and-race-status) · [packet health](#packet-coverage-and-telemetry-health) · [strategy](#strategy-inputs) · [storage](#storage-and-offline-analysis) · [verification](#verification)

~~~text
F1 22 UDP → receiver → raw .f1cap capture
                     → fresh race state → dashboard / radio
                     → SQLite history
.f1cap replay → same race-state processing → saved dashboard / offline analytics
~~~

## Session and recording policy

The 2022 Session packet's session type describes the **kind of session**, not whether it is Career or LAN.

| Policy | Automatically records |
| --- | --- |
| **races** (default) | Recognized race types 10–12 |
| **weekend** | Practice 1–4, qualifying 5–9, and races 10–12 |
| **all-driving** | Weekend sessions plus Time Trial 13 |

Type 0 and unknown/menu sessions do not start automatic capture. The separate game-mode field can report Grand Prix (3), Online Custom (7), Online League (8), Career '22 (19), or Career '22 Online (20). The network-game field reports offline (0) or online (1). Neither proves LAN transport or identifies the host/client. Unknown codes remain numeric. See the [EA F1 22 UDP specification](https://forums.ea.com/discussions/f1-games-franchise-discussion-en/f1-22-udp-specification/8418392) and the [field audit](../ANALYTICS_DATASET.md).

### Capture lifecycle

1. A valid eligible Session packet opens a uniquely named capture and JSON sidecar under **recordings/**.
2. Valid packets for that race append their **original received bytes**. Brief silence or an unrelated lobby packet does not close it.
3. A recognized new driving Session closes the old file. A same-UID restart after SEND and a game-clock reset also gets a separate recording.
4. Final Classification or SEND starts a grace period. The file closes after that period passes without more packets from its session.
5. Ctrl+C or an unexpected end retains an **incomplete** file. Automatic captures continue in linked segments at the 2 GiB per-file limit; a new segment begins with a copy of an original Session packet. Named manual captures stop at 256 MiB.

The sidecar identifies track, date/time, driver, game/session type, online/offline indicator, laps observed, last position, packet count, status, and completion evidence. **classification_seen** means packet 8 arrived; **session_end_seen** means SEND arrived; **no_end_signal** identifies a partial capture. Legacy sidecars without enough evidence show **unknown**. Older captures remain readable without changing their bytes.

The two archives in **fixtures/** restore the ten-lap run and completed Bahrain Grand Prix. Their sizes are about 45.6 MB and 65.0 MB; no Git LFS account is needed. The restore script checks raw SHA-256 digests and refuses to overwrite different bytes.

## Freshness and race status

| Observation | Treatment |
| --- | --- |
| Fresh Session status 0, 1, 2 | Displays **GREEN**, **SC**, or **VSC**. Formation lap (3) and unsupported codes show **UNKNOWN** here. |
| Session status ages out while other packets continue | Displays **UNKNOWN** instead of an old SC/VSC claim. |
| No accepted race packet for five seconds | Displays **STALE** and hides old car values. |
| Forward time/lap jump | Marks an **unobserved interval**; never invents intermediate telemetry or calls it a flashback. |
| FLBK event or confirmed clock regression | Preserves a flashback boundary and clears incompatible state. A single delayed packet is ignored. |
| SC/VSC, gap, or restart | Withholds normal-race pace, distance-scaled gaps, lift/coast cues, and fuel/tyre projections until comparable data returns. |

The raw-derived timeline retains the last confirmed status but does not display it as current after expiry. Session status, pit entry/exit from Lap pit status, FLBK, SEND, and observed gaps preserve capture-relative and game session time. A pit stop inside an unobserved interval cannot be reconstructed. The game does not transmit the chosen **Safety Car Experience** setting or why UDP stopped. The Bahrain fixture has no SC/VSC observations; synthetic tests cover those paths, and a [Career/LAN gameplay check](../reports/sc-vsc-broadcast-acceptance.md) remains.

Driving, lap, and car-status observations expire after two seconds; Session and damage after four; event and history after ten. Every decoded observation carries packet source and monotonic arrival time. Session/player transitions and flashbacks clear incompatible state. After a lost FLBK event, two consecutive packets more than two game seconds behind the latest observation also clear state.

## Packet coverage and telemetry health

The receiver checks the packed 24-byte little-endian 2022 header, format/version, packet ID, exact datagram length, player indices, and finite session time. It uses Motion (0), Session (1), Lap (2), Event (3), Participant (4), Car Setups (5), Car Telemetry (6), Car Status (7), Final Classification (8), Car Damage (10), and Session History (11). Lobby Info (9) is counted only. Player setup and final classification are decoded for offline analysis. Four-wheel arrays are **rear-left, rear-right, front-left, front-right**.

The 30-second health summary and recording events in **logs/race-engineer.log** include accepted/rejected counts by packet ID, arrival/decode rates, source changes, last packet/write age, UID/session transitions, frame gaps or reordering, lap/session-time boundaries, byte counts, size-limit segments, classification, SEND, connection changes, and close reasons. A frame discontinuity is **not proof of packet loss**.

Dashboard facts and JSON are built in a bounded worker away from the UDP-critical path. Old display snapshots may be coalesced if it falls behind; raw capture is retained. Optional FastAPI/WebSocket clients wake on a new frame. The standard-library HTTP fallback polls every 0.5 seconds. The [offline benchmark](../reports/telemetry-reliability-benchmark-20261030.md) reports measured CPU, RAM, throughput, and publication age; game FPS/frame-time impact still needs target-PC measurement.

UDP alone cannot authenticate a sender. The default listener is **127.0.0.1:20777**. A remote receiver needs an explicit IPv4 bind address; **--sender-address** accepts one IPv4 source and rejects others before capture. Dashboard and replay HTTP stay loopback-only. **UDP Broadcast Mode** controls network delivery; **Safety Car Broadcast Experience** controls in-game presentation.

## Dashboard numbers and their limits

| Display | Source or condition |
| --- | --- |
| Driver and number | Participant packet. Team colours are approximations; car photos are from Wikimedia Commons with an offline fallback. See [credits](../CAR_PHOTO_CREDITS.md). |
| Laps and sectors | Available timing. Purple is fastest among available values, not an official live-timing feed. |
| Gap | Plain gaps use observed same-checkpoint crossings; **≈** gaps scale distance by the player's last valid lap. |
| Map | Approximate outlines for 26 circuits. Bahrain has an observed route; other circuits can switch after a clean lap. Invalidation locations are not exact track limits. |
| Fuel and tyre trend | Comparable lap-start pairs. Early rates are low confidence and reset on session, flashback, pit, or tyre change. |
| Inferred DNF | Shared lap distance and world position stay within 25 m over two player laps. Pit/SC/VSC reset the clock; movement clears the label. Game-reported DNF/DSQ takes precedence. |
| Practice comparison | Observed setup fingerprint plus player-entered preset label. Fastest and median laps are descriptive proxies. |

Rival fuel, tyre condition, ERS, planned stops, and private setup are not available under current shared telemetry assumptions. Restricted telemetry can withhold more opponent data. The companion cannot promise an exact undercut, rejoin place, or track-limit ruling.

## Strategy inputs

The pit model compares stay-out, one-stop, and optionally two-stop plans only with comparable wear and explicit pace assumptions. This command uses **illustrative values**, not Bahrain calibrations:

~~~powershell
.\scripts\start.ps1 --drive --pit-loss 23 --fresh-tyre-gain 0.5 --wear-pace-cost 0.04 --next-compound 18 --traffic-loss 2 --strategy-profile balanced
~~~

Compound numbers are protocol codes. **--second-compound CODE** enables two-stop comparison. **--safety-car-pit-factor** and **--vsc-pit-factor** need separately measured ratios of neutralized to normal pit loss; without one, the model withholds a neutralized pit recommendation. It does not infer rival plans, compound availability, regulations, or weather crossover pace.

Fuel margin starts with one comparable lap-start pair. Provisional alert thresholds are **2 kg fuel**, **70% tyre wear**, and **0.4 MJ ERS**. Alerts repeat after a default **60-second cooldown**. A heavy-braking lift marker appears only after two clean observed laps and is an uncalibrated candidate about 100 m before the zone, not a braking instruction. All strategy output is advisory.

## Storage and offline analysis

| Project path | Content |
| --- | --- |
| **recordings/** | Raw .f1cap files and JSON sidecars |
| **data/races.sqlite3** | Structured session, lap, sector, event, alert, and practice history |
| **data/analytics/** | Derived SQLite datasets from raw captures |
| **data/tmp/** | Launcher temp files |
| **logs/race-engineer.log** | Rotating diagnostics |
| **fixtures/** | Two published compressed regression captures |

Source runs write their runtime data under this D: project. Packaged runs write beside the executable, so keep the packaged folder on D:. Codex, Windows, F1 22, and the browser manage their own profiles and caches. Microphone audio is not stored.

**scripts/extract_analytics.py** rebuilds a separate dataset without changing the raw capture. It extracts setup, per-car lap/stint history, final classification, driving/fuel/wear samples, gaps and flashback epochs, and cautious fuel, wear, pace, pit, neutralized-lap, and practice summaries. **--as-of SECONDS** limits summaries to observations known by that time; **--replace** rebuilds only an existing *derived* database. See the [schema and field audit](../ANALYTICS_DATASET.md) and [Bahrain sample](../reports/bahrain-analytics-20261030.md). The historical layer is not a trained ML model.

~~~powershell
.\.venv\Scripts\python.exe scripts\extract_analytics.py grand-prix-20261028-212717.f1cap
.\.venv\Scripts\python.exe -m app --debrief SESSION_UID
.\.venv\Scripts\python.exe -m app --export-history SESSION_UID json debrief.json
~~~

History excludes menu UID 0 and repeated button/speed-trap notifications from session lists. Debriefs report observed timing, position, fuel/wear/pace changes, and sector variation when enough data exists; they do not claim hypothetical strategy gains. Review logs and exports before sharing.

## Useful commands

| Task | Command or option |
| --- | --- |
| Live dashboard and automatic capture | .\scripts\start.ps1 |
| Dashboard without automatic capture | .\scripts\start.ps1 --dashboard |
| Named manual capture with dashboard | .\scripts\start.ps1 --record NAME.f1cap --dashboard --open-dashboard |
| Console-only capture replay | .\.venv\Scripts\python.exe -m app --replay NAME.f1cap |
| Direct dashboard replay | .\.venv\Scripts\python.exe scripts\replay_dashboard.py NAME.f1cap |
| Lower-overhead receiver | --performance-mode: no dashboard, ten-second reports |
| Disable SQLite history in manual receive mode | --no-history |
| Stop by control file | --stop-file NAME.stop, then create **control/NAME.stop** |
| Optional question-topic classifier | --llm-endpoint URL and --llm-model NAME together |

For an LLM endpoint, plain HTTP is accepted only on loopback; remote URLs require HTTPS. Set **RACE_ENGINEER_LLM_API_KEY** only if needed. Only question text, not telemetry, is sent. Calls time out after two seconds and fall back to deterministic routing. A large local model may compete with game resources.

To build a portable Windows folder, install the **packaging,dashboard,voice** extras and run **scripts/build.ps1**. Validate **dist/F1RaceEngineer/F1RaceEngineer.exe** on the target PC. The folder build avoids one-file extraction into Windows temp; PyInstaller and voice models are not bundled in source control.

## Verification

~~~powershell
.\.venv\Scripts\python.exe -m pytest --basetemp '.pytest_sandbox_tmp'
.\.venv\Scripts\python.exe scripts\check_dashboard_js.py
.\.venv\Scripts\python.exe scripts\restore_replay_fixtures.py --verify-only
.\.venv\Scripts\python.exe scripts\verify_grand_prix.py
~~~

The Bahrain verifier checks capture SHA-256, raw replay, grid/map/gap/rejoin contracts, and the saved-race library on lap 8. Its **22-second pit loss is a test input**, not a measurement. It only verifies features represented in the capture; practice, SC/VSC, live voice, and game frame pacing require separate gameplay checks. See the [fixture report](../reports/grand-prix-20261028-212717.md) and [project progress](../PROJECT_PROGRESS.md).
