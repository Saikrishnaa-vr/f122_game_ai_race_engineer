# F1 22 race engineer companion

A separate, read-only Windows companion for F1 22's documented 2022 UDP feed. It receives local packets, tracks fresh player telemetry, provides deterministic radio answers, compares conditional pit plans, and optionally stores history, serves a dashboard, and enables offline voice. It never sends game commands, reads process memory, modifies game files, or drives the car. Game control remains with the player.

**Validation status:** a user-driven ten-lap run and a later completed 14-lap Grand Prix were captured on 2026-10-28. The full Grand Prix fixture has 140,578 packets, zero header/payload errors and two final-classification packets. A dashboard replay reaches 20 drivers/markers, a 181-point observed route and checkpoint gap estimates. See `reports/grand-prix-20261028-212717.md`. Browser smoothness, exact gap agreement with the game, practice preset comparison, SC/VSC guidance, voice and game frame pacing still need live validation. UDP packets alone cannot authenticate their sender.

Phase 1 connection verification is complete and user-accepted. See [project progress](PROJECT_PROGRESS.md) for phase status, evidence and the next validation plan.

Development uses `feature/*` and `fix/*` branches from `develop`; validated changes enter `develop` by pull request. The product owner promotes `develop` to `main` by release pull request. See [branches and releases](CONTRIBUTING.md) for the checks and GitHub protection settings.

## Compact race dashboard

The live and saved-race dashboards share a one-screen desktop layout: running order on the left, a smaller track map and pit rejoin view in the middle, car health beside them, and weather, chase, strategy and radio on the right. The circuit name appears once in the header; the Race card shows compact lap/position information. On narrower windows the cards reflow and scrolling returns. Display fit still needs a visual check at the actual screen resolution used while driving.

Running-order rows request 2022 team car photographs from Wikimedia Commons. Each thumbnail links to its source and license; [car photo credits](CAR_PHOTO_CREDITS.md) list the authors and terms. Without a network connection or when an image fails, the team-colour diamond is shown. Browsers may cache these remote images outside the D: project folder.

Four animated tyre rings show the game's wear percentages for front-left, front-right, rear-left and rear-right. The fuel card animates remaining fuel against the game's tank capacity and graphs up to eight comparable lap-start fuel-use readings. A low-confidence finish projection uses the engineer's comparable fuel-use rate and laps remaining. When it projects a shortfall, it shows a target kg/lap and an approximate saving amount. A map lift marker and text appear only after the companion has observed a heavy braking area on two clean laps; the cue is about 100 m before that area. It is an uncalibrated candidate, not a guaranteed fuel saving or an instruction to brake at the marker. Pit, safety-car and VSC phases suppress the lift cue. Replay rebuilds all these derived values from its saved raw packets, so the existing Grand Prix can be used to inspect the display without launching the game.

Race status now reads each car's result-status byte from F1 22's Lap packet. A game-reported DNF or retirement immediately labels the car **DNF** in running order and removes its map marker; a disqualification is labelled **DSQ** and also removed from the map. If the game still reports a rival as active, the dashboard infers DNF only when its shared lap distance and world position stay within 25 m while the player completes two full laps. This inferred label clears if the car moves again. Pit-lane and SC/VSC periods reset the stationary clock; missing or stale motion cannot create a new inferred DNF. A retired row remains visible if the game stops listing that car in the current running order. The stored Grand Prix did not prove a real retirement event, so check the label against the game if one occurs.

## Windows setup

Use PowerShell in this directory. Python 3.11+ is required. The baseline receiver uses the standard library. Optional extras need packages and an offline Vosk model downloaded separately. No administrator access is needed for normal operation.

```powershell
Set-Location 'D:\games\F1 22\f1-ai-race-engineer'
uv venv --python 3.12 .venv
uv pip install --cache-dir '.uv_cache' --python '.venv\Scripts\python.exe' -e '.[test]'
.\.venv\Scripts\python.exe -m pytest --basetemp '.pytest_sandbox_tmp'
```

The dashboard also has a local HTTP polling fallback that works with the baseline Python environment. For WebSocket delivery or voice, install only the parts you want:

```powershell
uv pip install --cache-dir '.uv_cache' --python '.venv\Scripts\python.exe' -e '.[dashboard,voice]'
```

To build a portable Windows app folder, install `.[packaging,dashboard,voice]` with `uv pip install` and run `scripts\build.ps1`. Keep the resulting `dist\F1RaceEngineer\` folder on D: and validate `dist\F1RaceEngineer\F1RaceEngineer.exe` on the target machine. This folder build avoids one-file runtime extraction into the Windows temp directory. Build temp and PyInstaller configuration are directed to the project's `data\` folder. PyInstaller and optional voice models are not bundled in source control.

## Start

From this directory, run **one command**:

```powershell
.\scripts\start.ps1
```

You can also double-click `Start Race Engineer.cmd`, or run `.\.venv\Scripts\python.exe -m app --drive`. This starts the **live** UDP receiver, diagnostic log, structured race history, and dashboard, then opens the dashboard in your default browser. Automatic recording defaults to valid race Session packets (types 10, 11, or 12), in Career, LAN or other modes that send 2022 UDP; menu time does not create an empty recording. Each session gets a unique `.f1cap` file in `recordings\` plus a `.json` file with its track, date, session/game mode labels, offline/online indicator, laps, last observed position, packet count, status and completion evidence. A brief UDP interruption or unrelated lobby packet does not close an active race. A recognized new driving Session closes the old capture; a new eligible session gets a separate file, including a same-UID restart after `SEND` and a game-clock reset. After final classification or `SEND`, recording closes only after the existing grace period has passed without further packets from that session. Ctrl+C retains an unfinished recording with `incomplete` status. At the 2 GiB per-file limit, automatic recording continues into a linked segment; the new segment begins with a copy of an original Session packet so it can be replayed independently. Manual `--record` captures retain the 256 MiB cap and stop at that limit. Opening `app\dashboard.html` directly cannot start the receiver or display live data because the page needs the local server.

The [EA F1 22 UDP specification](https://forums.ea.com/discussions/f1-games-franchise-discussion-en/f1-22-udp-specification/8418392) names Session types 1–4 practice, 5–9 qualifying, 10–12 race, and 13 Time Trial; 0 is unknown. These are session kinds, not Career/LAN mode IDs. The separate `m_gameMode` field can identify documented modes such as Grand Prix (3), Online Custom (7), Online League (8), Career '22 (19), or Career '22 Online (20); unknown IDs remain numeric. It does not explicitly identify a LAN host or client. `m_networkGame` is only 0 for offline or 1 for online. Neither field alone proves that the session used LAN transport. If you want raw recordings of practice and qualifying as well, run `--drive --auto-record-sessions weekend`; `--auto-record-sessions all-driving` also includes Time Trial. The default `races` policy captures only 10–12. Unknown and menu sessions are never automatically opened. A recognized transition outside the selected policy still closes the prior file as incomplete if no end signal was observed.

Recording events and 30-second health summaries are in `logs\race-engineer.log`. They include UID/session changes, accepted and rejected packet counts, last successful write age, byte counts, lap at the boundary, `SEND`, classification, connection state changes, and size-limit events. See [the LAN recording investigation](reports/lan-recording-investigation-20261029.md) for what the local captures establish and what still needs a game test.

To review saved sessions, double-click **`Replay Saved Races.cmd`**. It opens a separate local replay library at `http://127.0.0.1:8766/`, so it can run alongside the live dashboard on port 8765. Entries show circuit, date/time, driver, game mode, session name, offline/online indicator, observed lap numbers, last position, status, packet count and completion evidence. `classification_seen` confirms only that a Final Classification packet was received; `session_end_seen` means only `SEND` was seen; `no_end_signal` is a partial recording. Older sidecars without enough evidence show `unknown` instead of an invented confidence. Choose a recording to use Play, Jump to lap, Speed, or the timeline. The live shortcut shows **LIVE** and has no replay controls; the replay shortcut shows **REPLAY**. Older `.f1cap` files are indexed without changing their bytes. The viewer does not bind the game UDP port or write to race history. Leave its console open while browsing, and press Ctrl+C to stop it. The replay page serves the same dashboard HTML as live mode, and selecting a saved session rebuilds its snapshots from raw packets with the current telemetry and dashboard code. Restart the replay viewer and reselect after a code change to see the new version.

The repository includes **two compressed test races** in `fixtures/`: the ten-lap verification run and the completed Grand Prix. A fresh checkout can restore them to the ignored local `recordings/` folder before opening the replay library:

```powershell
.\.venv\Scripts\python.exe scripts\restore_replay_fixtures.py --verify-only
.\.venv\Scripts\python.exe scripts\restore_replay_fixtures.py
```

The restore command checks both raw SHA-256 digests and refuses to replace an existing recording with different bytes. The archives are 45.6 MB and 65.0 MB; no Git LFS account is required. Raw captures include participant names and are published solely as replay regression fixtures. Runtime recordings, local SQLite data, logs, control files, caches and test output remain ignored by Git.

For manual control, use `scripts\start.ps1 --dashboard` (dashboard without raw capture) or add `--record NAME.f1cap --open-dashboard`. The race dashboard is at `http://127.0.0.1:8765` and the separate practice/preset page is at `http://127.0.0.1:8765/practice`. With FastAPI and uvicorn installed it uses WebSockets; otherwise the same page polls the loopback server twice per second. The map has bundled approximate outlines for 26 full circuits; Bahrain uses a game-coordinate lap from the saved Grand Prix. Other circuits switch to observed game coordinates after a clean lap. The game-coordinate vertical axis is inverted relative to geographic north, and the dashboard uses separate transforms so these maps have the same north-up orientation. Numbered car markers and lap invalidation locations follow the active map; bundled-outline positions are approximate, and invalidation markers are not exact track limits. The grid shows running order, last/best laps, recent sectors and estimated gaps. Plain gaps use observed same-checkpoint crossing times; `≈` gaps use distance scaled by the player's last valid lap. Select a driver for the last five recorded laps. A message saying the UDP port is bound means only that the listener started. Enter a driving session and look for `Valid F1 22 packet received` and `Status: receiving`.

In the game's telemetry settings, use UDP On, Broadcast Off, IP `127.0.0.1`, port `20777`, 20 Hz, format 2022, Your Telemetry Restricted. The companion does not change these settings. Press Ctrl+C in its console to stop it. Stopping the companion does not stop the game.

For Career or LAN multiplayer **on the same PC**, keep the default listener `127.0.0.1:20777` and point the game's UDP telemetry at `127.0.0.1:20777`. For the companion **on another LAN PC**, explicitly run it with `--drive --bind-address <companion-LAN-IP> --sender-address <game-PC-IP>` (or bind `0.0.0.0` if necessary). Set the game's UDP destination IP to the companion PC and its port to 20777; allow that UDP port through the companion PC's local firewall only for the trusted LAN. `--sender-address` accepts one IPv4 literal and rejects/logs other sources before capture. Omitting it accepts any sender reachable at the chosen bind address. The dashboard and replay HTTP servers remain loopback-only on the companion PC. The game's **UDP Broadcast Mode** sends telemetry to LAN receivers; it is separate from **Safety Car Broadcast Experience**, which controls the presentation of an in-game safety car. Targeted UDP IP is preferable for one remote companion. These are manual game/network settings; the companion changes none of them.

The track map and running order use the Participant packet's race number and team ID with a three-letter driver code. The map keeps team colour on every car, adds a white ring around your car and an orange ring for a slow car, and exposes the full name/team in the marker tooltip and selected running-order row. Unknown identities use a neutral colour and `?` for an unavailable race number. Team colours are readable approximations, not colour values transmitted by F1 22. Lap and sector times appear as `m:ss.mmm` or `ss.mmm`, and the fastest game-reported best lap is purple. A green last lap means it matches that driver's game-reported best. The `Gap to you` column remains an **estimate** from shared telemetry, with `≈` for distance-derived values; it is not Formula 1's official gap or interval feed. This display follows the concepts in [official F1 Live Timing](https://www.formula1.com/en/latest/article/f1-live-timing-bigger-and-better-than-ever.3wTlvar6VFvyDbm8GWnyMM.3wTlvar6VFvyDbm8GWnyMM), its [gap and interval explanation](https://www.formula1.com/en/latest/article/f1-glossary-f-j.2g6grJVxT6dFCDpSmgoCQX), and the [F1 sector-colour glossary](https://www.formula1.com/en/latest/article/f1-glossary-p-t.GBhLQtpeu9b8LrK5VM0F1). The car identity fields come from EA's [F1 22 UDP specification](https://forums.ea.com/discussions/f1-games-franchise-discussion-en/f1-22-udp-specification/8418392).

To reduce report and dashboard overhead, run `--performance-mode`. It reports every ten seconds and does not start the dashboard. The receiver still processes incoming UDP data. Use `--no-history` to disable SQLite writes. In manual receive mode, raw datagram capture is **off** unless `--record NAME.f1cap` is supplied; those manual captures are capped at 256 MiB. Console-only packet replay uses `--replay NAME.f1cap`. For an unattended capture, `--stop-file NAME.stop` exits cleanly after `control\NAME.stop` is created.

The driving layout gives the map more width and fits its route to the available panel size. S2 and S3 markers use observed game sector transitions when captured; until then they use approximate thirds of the outline. Four compact cards over the map show the fastest observed S1/S2/S3 times and game-reported best lap, plus your available sector times. Purple identifies the fastest available driver time; it is not an official live-timing feed. A warning board appears one lap before the game's valid pit window, stays visible while it is open, and changes to a latest-lap warning at its end. A separate SC or VSC board appears only while the fresh Session packet reports that condition. Both boards disappear when the condition clears, restoring map space. Saved replay uses this same view and rebuilds these values from the capture; the completed Bahrain recording contains pit-window and sector evidence but no SC/VSC period.

## Strategy inputs

The model refuses a pit call until it has comparable wear observations and explicit pace assumptions. Example values below are **illustrative only**; replace them with measurements from your own sessions. Compound codes are F1 22 protocol values, not human labels.

```powershell
.\.venv\Scripts\python.exe -m app --dashboard `
  --pit-loss 23 --fresh-tyre-gain 0.5 --wear-pace-cost 0.04 `
  --next-compound 18 --traffic-loss 2 --strategy-profile balanced
```

`--second-compound CODE` enables two-stop comparison. `--safety-car-pit-factor 0.6` supplies a measured ratio for safety-car stops; without one, safety-car pit calls are withheld. The model compares stay-out, one-stop and optional two-stop plans with a configurable wear limit. It states assumptions, modelled advantage, uncertainty, alternatives and risks. It does not infer compound availability, regulations, rival tyre state, traffic at rejoin, or weather crossover pace. Those remain driver decisions. A strategy result is a conditional estimate, not a command.

Fuel margin uses one comparable lap-start fuel pair. Tyre wear trend uses one comparable pair; two rates give a range. These estimates are low confidence and reset on session, flashback, pit or tyre change. Alert thresholds are provisional: fuel at 2 kg, tyre wear at 70%, ERS at 0.4 MJ. Fuel, tyre, ERS, safety-car, weather and significant wing-damage alerts are checked as relevant packets arrive and also appear in the next report. Repeats have a 60-second cooldown by default (`--alert-cooldown SECONDS`). Alerts are advisory.

## Voice and questions

The dashboard accepts typed questions about tyres, fuel, ERS, weather, damage, lap, rival position, the fastest game-reported best lap, and pit strategy. The running-order page also shows the average relative pace needed to catch the car ahead before the finish. Early/late pit rejoin ranges require a measured `--pit-loss`; under SC/VSC, a measured `--safety-car-pit-factor` is also required. Rival planned stops, tyre condition, fuel and traffic are unavailable, so these are indicative positions, not a pit instruction. The practice page groups new practice laps by a fingerprint of the game's selected setup and lets you label it with the preset name you used. Its fastest-lap and median-lap candidates are descriptive proxies for qualifying pace and race consistency, not automatic setup recommendations. An optional LLM can classify a question's topic and follow-up. It cannot write race facts or recommendations.

Offline voice requires the `voice` extra and a local Vosk model directory. A [small Vosk model from the official model list](https://alphacephei.com/vosk/models) is a better starting point for play than a large server model because it uses much less memory:

```powershell
.\.venv\Scripts\python.exe -m app --voice-model 'C:\path\to\vosk-model'
```

Hold **F8** to speak, release to send. Recognition and synthesis run in worker threads. pyttsx3 provides local text-to-speech. Automatic threshold alerts enter a bounded radio queue. Use `--voice-input-device INDEX` and `--voice-volume 0.8` if needed; output follows the Windows default device. Voice errors are logged and telemetry reception continues. F8 is a global key poll while voice is enabled; choose another setup if the game uses F8 for a critical action. Response interruption and audio naturalness need a live user check.

An optional OpenAI-compatible chat-completions endpoint may be used for **intent classification only**:

```powershell
.\.venv\Scripts\python.exe -m app --llm-endpoint 'http://127.0.0.1:11434/v1/chat/completions' --llm-model 'YOUR_LOCAL_MODEL'
```

Plain HTTP is accepted only for loopback; remote endpoints require HTTPS. Set `RACE_ENGINEER_LLM_API_KEY` if the endpoint needs authentication. Question text, but no telemetry, is sent to that endpoint. Calls have a two-second timeout and fall back to deterministic keyword routing. A large local model can compete with game GPU resources; leave this off during performance-sensitive play.

## History and debrief

By default, structured session, lap, sector, event and alert records are stored locally at `data\races.sqlite3`. New practice laps also store the observed setup fingerprint; preset labels are local to the track. The schema adds a nullable fingerprint column on first run and preserves existing lap rows. Raw UDP is stored only when capture is explicitly enabled; microphone audio is never stored. Source runs write under the D: project folder; packaged runs write beside the packaged executable, so keep the entire app folder on D:. The live and replay launchers also set their process temp directory to `data\tmp` on D:. The database records only data available at sampling time. Menu session UID 0 and repeated button (`BUTN`) and all-car speed-trap (`SPTP`) notifications are excluded from session lists and debrief events; older database rows remain intact. A debrief reports observed best/mean lap, final observed position, median comparable fuel, wear and pace changes, and the sector with the largest observed variation when enough history exists. Sector validity flags are stored but not interpreted. It does not claim hypothetical strategic time gain.

These paths cover the race engineer. Codex, Windows, F1 22 and the default browser manage their own profiles and caches; the race engineer cannot relocate those files.

```powershell
.\.venv\Scripts\python.exe -m app --debrief SESSION_UID
.\.venv\Scripts\python.exe -m app --export-history SESSION_UID json debrief.json
.\.venv\Scripts\python.exe -m app --export-history SESSION_UID csv laps.csv
```

Review telemetry logs and exports before sharing. The rotating diagnostic log is `logs\race-engineer.log`.

## Protocol and limits

The packed 2022 header is 24-byte little-endian `<HBBBBQfIBB`. The receiver checks format/version, packet ID, exact datagram length, player indices, and finite session time. Player data comes from Motion (0), Session/forecast (1), Lap (2), Event (3), Participant (4), Car Telemetry (6), Car Status (7), Car Damage (10), and Session History (11). Shared Motion coordinates, Lap timing, Participant names and per-car Session History support the external map and grid. IDs 5, 8 and 9 are counted only. Four-wheel arrays are rear-left, rear-right, front-left, front-right. Rival analysis does not claim rival fuel, tyre condition or ERS. Restricted telemetry may withhold further opponent data.

Every decoded observation has a packet source and monotonic timestamp. Driving/lap/status expire after two seconds, session/damage after four, event/history after ten. Session/player transitions and `FLBK` clear state. If the flashback event is lost, two consecutive packets on a game clock more than two seconds behind the latest observation also clear state; a single delayed packet is ignored. Packet loss, pause and out-of-order UDP can still limit accuracy. A stale field is not reused as current data.

The [EA F1 22 UDP specification](https://forums.ea.com/discussions/f1-games-franchise-discussion-en/f1-22-udp-specification/8418392) is the primary protocol reference. Its linked v16 document has not yet been compared field by field in this workspace. A previous live run confirmed 40-byte Event datagrams; the receiver rejects other Event lengths.

## Gameplay acceptance check

After automated tests, check one practice and one race session while safely stopped: match speed, gear, lap, fuel, tyre wear, weather and event status to the HUD/MFD; test a pit, safety car if available, flashback and telemetry disconnect. Check the dashboard track outline and car markers against visible positions, select grid drivers to inspect recent lap logs, and ask who has the fastest lap. Check F8 voice in headphones and that a question about missing data says it is unavailable. Compare FPS and frame pacing with the companion closed, receiver only, dashboard, and voice separately. Keep the game unmodified. Record any mismatch with the companion log time and HUD value. Do not treat the new features as gameplay verified until those checks pass.

The completed Grand Prix capture is retained locally at `recordings/grand-prix-20261028-212717.f1cap`. After changing telemetry or dashboard code, run `python scripts/verify_grand_prix.py` from this directory. The script checks the SHA-256, fully replays the capture, verifies grid, map, gap and rejoin contracts, then opens it through the actual saved-race library path and checks all 20 map/running-order identities on lap 8. Its 22-second pit loss is a **test value only**. Use the replay shortcut for visual checks without starting F1 22. The existing Bahrain race can verify features represented in that capture; practice, SC/VSC and other absent events need their own recordings or controlled fixtures. See `reports/grand-prix-20261028-212717.md` for the fixture manifest.

To inspect that race directly without opening the picker, run `.\.venv\Scripts\python.exe scripts\replay_dashboard.py grand-prix-20261028-212717.f1cap --pit-loss 22` and open `http://127.0.0.1:8765`. The replay begins paused. The viewer processes the capture once, then serves read-only snapshots; it does not bind the game telemetry port, change the recording, or write race history. The 22-second pit loss is a test assumption shown in the viewer, not a measurement from this Grand Prix. Press Ctrl+C in its console to stop it.
