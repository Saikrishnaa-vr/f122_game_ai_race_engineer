# Use the F1 22 Race Engineer

[← Project overview](../README.md) · [Technical reference](TECHNICAL_REFERENCE.md)

This guide is for the person playing F1 22. The companion runs **outside the game** and only reads its UDP telemetry.

**Jump to:** [install](#1-install-once) · [game settings](#2-set-f1-22-telemetry) · [dashboard](#read-the-dashboard) · [replay](#replay-a-race) · [recording](#recording-options) · [LAN](#companion-on-another-lan-pc) · [troubleshooting](#when-something-looks-wrong)

## 1. Install once

You need Windows, F1 22, Python **3.11 or newer**, and uv. Keep the project on a drive with room for race captures. From PowerShell in the repository folder:

~~~powershell
uv venv --python 3.12 .venv
uv pip install --cache-dir '.uv_cache' --python '.venv\Scripts\python.exe' -e '.[test]'
~~~

The base dashboard works without the optional FastAPI packages. To add WebSocket delivery or offline voice, install the extras you need:

~~~powershell
uv pip install --cache-dir '.uv_cache' --python '.venv\Scripts\python.exe' -e '.[dashboard,voice]'
~~~

The voice feature also needs a separately downloaded local [Vosk model](https://alphacephei.com/vosk/models). No administrator access is needed for ordinary use.

## 2. Set F1 22 telemetry

For F1 22 and the companion **on the same PC**, use:

| Game setting | Value |
| --- | --- |
| UDP Telemetry | On |
| UDP Format | 2022 |
| UDP IP Address | 127.0.0.1 |
| UDP Port | 20777 |
| UDP Send Rate | 20 Hz |
| UDP Broadcast Mode | Off |
| Your Telemetry | Restricted |

The companion does not change game settings. **UDP Broadcast Mode** controls where telemetry packets are sent on the network. **Safety Car Experience: Broadcast** controls the game's Safety Car presentation. They are separate settings.

## 3. Start and stop

Double-click **Start Race Engineer.cmd** or run:

~~~powershell
.\scripts\start.ps1
~~~

This starts the live UDP listener, dashboard, automatic race capture, diagnostic log, and local history. The dashboard opens at **http://127.0.0.1:8765**; the practice page is at **http://127.0.0.1:8765/practice**. Leave the console open while driving. Press **Ctrl+C** there when finished.

The receiver can be ready before the game sends data. Enter a driving session and look for **Valid F1 22 packet received** and **Status: receiving**. Opening the HTML file directly does not start the server or recorder.

## Read the dashboard

The desktop layout puts the **running order** on the left, **track map and pit rejoin** in the middle, and **car health, weather, chase, strategy, and radio** alongside. Narrow browser windows reflow the cards and may scroll.

| Indicator | Read it as |
| --- | --- |
| Driver code, number, team colour | Identity from Participant telemetry; unknown identities use a neutral marker. The running order includes team car photos when Wikimedia Commons images load, with a colour fallback offline. |
| White ring on map | Your car. An orange ring marks a car observed moving slowly. |
| Last lap, best lap, sector colour | Game-reported or observed timing. Purple marks the fastest available sector or best lap in the current data; it is not an official broadcast feed. |
| Gap to you with ≈ | A distance-based estimate. Plain gaps use observed same-checkpoint crossing times. Neither is official F1 timing. |
| Tyre rings and fuel flow | Game-reported wear/fuel, with derived trends when comparable laps exist. A projected shortfall is low confidence. |
| Pit-window board | Appears near or during the game's reported pit window. Rejoin and undercut/overcut ranges need measured pit loss and still depend on unknown rival plans. |
| GREEN, SC, VSC, UNKNOWN, STALE | A fresh Session packet supports the first three. UNKNOWN means no current supported status; STALE means race packets stopped arriving. |

The map uses bundled approximate outlines for 26 circuits. Bahrain has an observed game-coordinate route from the saved race; other circuits can switch to observed coordinates after a clean lap. Track-limit markers show observed lap invalidation locations, **not** precise track boundaries. The map and gap estimates need game-screen comparison before relying on them during a race.

During SC/VSC, a packet gap, or immediately after a restart, the dashboard withholds pace and fuel/tyre projections that are not comparable. A forward game-time jump is shown as an **unobserved interval**. The companion does not fill in speed, positions, fuel, or tyre wear for that interval.

A game-reported DNF or DSQ stays labelled in running order and leaves the map. An inferred stationary-car DNF requires two player laps of matching position and lap-distance evidence; it clears if the car moves. Pit-lane and SC/VSC periods cannot start that inference. The Bahrain fixture has no confirmed real retirement event.

## Replay a race

Double-click **Replay Saved Races.cmd**. Its library opens at **http://127.0.0.1:8766/** and can run beside the live dashboard. Choose a recording, then use **Play**, **Jump to lap**, **Speed**, and the timeline. The replay is read-only and does not bind the game UDP port.

A fresh checkout can restore the two included test races:

~~~powershell
.\.venv\Scripts\python.exe scripts\restore_replay_fixtures.py --verify-only
.\.venv\Scripts\python.exe scripts\restore_replay_fixtures.py
~~~

Restoration checks both raw SHA-256 digests and refuses to replace a different existing recording. The ten-lap run and completed Grand Prix are regression fixtures; raw captures contain participant names. A library entry shows track, date/time, driver, game and session labels, laps, last position, packet count, status, and completion evidence. **Classification seen** means a classification packet was received; **session end seen** means the SEND event was received. Neither invents missing race data. Older recordings remain readable.

Restart the replay viewer and reselect the recording after a dashboard code change. Leave its console open while browsing and press **Ctrl+C** to stop.

## Recording options

| Goal | Command |
| --- | --- |
| Races only (default) | Start normally |
| Practice, qualifying, and races | PowerShell: .\scripts\start.ps1 --drive --auto-record-sessions weekend |
| All recognized driving sessions, including Time Trial | PowerShell: .\scripts\start.ps1 --drive --auto-record-sessions all-driving |
| Dashboard without automatic raw capture | PowerShell: .\scripts\start.ps1 --dashboard |
| Named manual capture | PowerShell: .\scripts\start.ps1 --record NAME.f1cap --dashboard --open-dashboard |

Each automatic race gets a unique capture in **recordings/** and a JSON sidecar. Temporary silence does not close it. A real new race gets a separate file; an unexpected stop retains an **incomplete** recording. Automatic files segment at 2 GiB; manual named captures stop at 256 MiB. The replay library can show partial captures, but it cannot recover packets that the game never sent or the receiver never received.

## Companion on another LAN PC

Keep loopback reception as the default. For a remote companion, run it on the companion PC with its LAN address and the game PC's address:

~~~powershell
.\scripts\start.ps1 --drive --bind-address <companion-LAN-IP> --sender-address <game-PC-IP>
~~~

Set the game's UDP destination to the companion PC on port **20777** and allow that UDP port through the companion PC's firewall for the trusted LAN. The optional sender filter accepts one IPv4 address; omitting it accepts any sender reachable at the bind address. The dashboard HTTP servers remain loopback-only on the companion PC. The protocol's online/offline indicator does not prove whether a race is LAN or whether this PC is host or client.

## Optional voice and questions

Type questions on the dashboard about fuel, tyres, ERS, weather, damage, lap, rivals, or conditional pit plans. It answers from available data and says when a needed fact is missing. To enable offline voice:

~~~powershell
.\scripts\start.ps1 --drive --voice-model 'D:\path\to\vosk-model'
~~~

Hold **F8**, speak, and release. Recognition and speech synthesis run in workers; audio is not recorded. Voice requires the installed voice extra and a local model. Use **--voice-input-device INDEX** if necessary. F8 is polled globally while enabled, so check your game key bindings. An optional LLM endpoint can classify the *topic* of a question; it does not supply race facts.

## When something looks wrong

| Symptom | Check |
| --- | --- |
| Launcher says the Python environment is missing | Run the install commands above in this repository. |
| Dashboard opens but stays idle | Confirm the game UDP settings, enter a driving session, and look for the receiver's packet and receiving messages. |
| Port 20777 is already in use | Close another telemetry listener or the old companion instance. |
| Replay library is empty | Restore the included fixtures or check that captures are under **recordings/**. |
| Race status is UNKNOWN or STALE | Check whether fresh Session/race packets are arriving; a cinematic Safety Car interval may not transmit continuous telemetry. |
| Gap, pit, or fuel guidance looks uncertain | Check the displayed assumptions and freshness. The model withholds answers when comparable evidence is unavailable. |
| Remote LAN capture receives nothing | Check the game's destination IP, local firewall, bind address, sender filter, and whether another listener uses the port. |

Diagnostics are in **logs/race-engineer.log**. Review logs and raw captures before sharing because they can include driver names. For supported packets, data-quality rules, calibration inputs, and storage paths, see the [technical reference](TECHNICAL_REFERENCE.md).
