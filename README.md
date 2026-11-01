# F1 22 AI Race Engineer

An external, read-only race companion for **F1 22 on Windows**. It receives the game's 2022 UDP telemetry, shows a live dashboard, answers race questions, and saves races for replay and offline analysis. You remain in control of the car.

> **Start here:** [Set up and use the companion](docs/USER_GUIDE.md) · [Explore the dashboard](docs/USER_GUIDE.md#read-the-dashboard) · [Replay a saved race](docs/USER_GUIDE.md#replay-a-race)

## Get running

1. Install Python **3.11+** and uv, then open PowerShell in this repository on **D:**.
2. Create the local environment:

   ~~~powershell
   uv venv --python 3.12 .venv
   uv pip install --cache-dir '.uv_cache' --python '.venv\Scripts\python.exe' -e '.[test]'
   ~~~

3. In F1 22, set **UDP Telemetry: On**, **UDP Format: 2022**, **IP: 127.0.0.1**, **Port: 20777**, **20 Hz**, and **Your Telemetry: Restricted**. Keep **UDP Broadcast Mode: Off** for a companion on the same PC.
4. Start the live companion:

   ~~~powershell
   .\scripts\start.ps1
   ~~~

The browser opens the dashboard at **http://127.0.0.1:8765**. You can also double-click **Start Race Engineer.cmd**. Enter a driving session to receive data; opening the dashboard HTML file alone cannot start the receiver. Press **Ctrl+C** in the companion console to stop.

## What you can see

| View | What it helps with |
| --- | --- |
| **Running order and map** | Driver position, team colour, car number, observed route, sector times, and estimated gaps. |
| **Your car** | Fuel, four tyre-wear readings, damage, weather, and session status. |
| **Strategy** | Conditional pit comparisons, rejoin ranges, pit-window alerts, and pace needed to catch the car ahead. |
| **Practice** | Compare laps by the setup preset you label, on the live server's practice page. |
| **Replay** | Inspect saved races with play, jump-to-lap, and speed controls, without starting the game. |
| **Questions and voice** | Type a race question; optional offline F8 push-to-talk requires a local voice model. |

**Green, SC, VSC, unknown, and stale** are distinct states. The dashboard only presents a condition as current when fresh packets support it. It marks unobserved intervals and withholds estimates that are unreliable during Safety Car, VSC, or a telemetry gap. Gaps and pit outcomes are estimates from available UDP, not official F1 timing.

## Live or replay?

| I want to… | Open |
| --- | --- |
| Drive with the live dashboard and automatic race recording | **Start Race Engineer.cmd** or the PowerShell start command above |
| Review a saved race without F1 22 | **Replay Saved Races.cmd** at **http://127.0.0.1:8766/** |
| Restore the two included test races to the replay library | Run the restore command in the [user guide](docs/USER_GUIDE.md#replay-a-race) |
| Collect practice and qualifying too | See [recording options](docs/USER_GUIDE.md#recording-options) |

Automatic recording defaults to recognized race sessions. A valid race keeps recording through brief UDP silence and saves an incomplete capture if it ends unexpectedly. Raw captures and local data stay under the project folder.

## What has been verified

- A ten-lap run and a completed 14-lap Bahrain Grand Prix were captured on 2026-10-28. The Grand Prix replay fixture contains **140,578 packets** and passed the saved-race regression check.
- Automated tests and synthetic fixtures cover recording, replay, race-state changes, and telemetry gaps. [Project progress](PROJECT_PROGRESS.md) separates these checks from live-game acceptance.
- **Still needs live checks:** Career and LAN recording across different sessions; Safety Car/VSC Broadcast Experience; dashboard visual fit, exact game-to-dashboard gaps, practice preset comparisons, voice, and game FPS/frame pacing.

This companion does not send commands to F1 22, read game memory, modify game files, or control the car. Its UDP feed cannot authenticate a sender; use the default loopback listener unless you deliberately configure a trusted LAN connection.

## Find the right guide

| Need | Read |
| --- | --- |
| Installation, launchers, dashboard, replay, LAN, voice, troubleshooting | [User guide](docs/USER_GUIDE.md) |
| Recording rules, packet quality, strategy assumptions, CLI and storage | [Technical reference](docs/TECHNICAL_REFERENCE.md) |
| Data available for analysis and its quality limits | [Telemetry inventory](TELEMETRY_DATA_INVENTORY.md) and [analytics dataset](ANALYTICS_DATASET.md) |
| Current development status and gameplay checks | [Project progress](PROJECT_PROGRESS.md) |
| Contributing and release branches | [Contributing](CONTRIBUTING.md) |

Development flows through feature or fix branches → develop → owner-reviewed main. GitHub's PR and Validation rulesets are active; the [branch-ruleset handoff](BRANCH_RULESET_HANDOFF.md) records the remaining enforcement checks.
