# Phase 2 and 3 offline development — 2026-10-28

## Source and scope

The [EA F1 22 UDP specification page](https://forums.ea.com/discussions/f1-games-franchise-discussion-en/f1-22-udp-specification/8418392) links the v16 Word document. The attachment could not be downloaded in this environment. This audit uses the [master brief's reference implementation](https://github.com/raweceek-temeletry/f1-22-udp), which reproduces the EA packet descriptions, and the local ten-lap capture. The field mapping is therefore an offline cross-check, with direct v16 attachment comparison still open before Phase 2 completion.

| Data | Implemented mapping cross-check | Capture evidence or limit |
| --- | --- | --- |
| Header | 24 packed little-endian bytes; format 2022, version 1, packet ID, session UID/time, frame and player indices. | 103,702 received datagrams passed validation. |
| Session | 632 bytes. Weather, track/session values, safety-car state, 56 forecast slots and game pit-window bytes at offsets 613–614 match the reproduced structure. | Pit window was ideal lap 5/latest lap 9 before the stop, then 0/0. Weather remained clear; forecast and safety-car changes were not exercised. |
| Lap | 972 bytes, with 43 bytes per car; position, lap number, pit status/stops and times align with the reproduced structure. Pit status 1 means pitting; 2 means in pit area. | Ten lap completions, pit entry in lap 5, pit area and exit in lap 6. The float lap distance exceeded the integer track length by up to 0.65 m at the line. |
| Car telemetry/status/damage | 1347/1058/948 bytes; player speed/gear, fuel, compound, ERS, wear and wing damage use the documented packed fields. Wheel arrays are rear-left, rear-right, front-left, front-right. | Soft visual code 16/actual code 18 changed to medium visual 17/actual 19; fuel and wear progressed across stints. Individual values still need HUD comparison. |
| Session history | 1155 bytes; player index, completed/partial laps, sectors and tyre stints use the documented fixed slots. | Ten complete lap/sector records persisted. |
| Event | Live datagrams are 40 bytes. The reproduced text labels this packet 36 bytes, while its largest speed-trap union member plus header and code requires 40. | 2,546 Event datagrams in this capture were 40 bytes and decoded without error. The 40-byte rule remains based on packed layout and live evidence. |

## Changes prepared for the next gameplay check

- **Phase 2 recovery:** when an `FLBK` event is absent, two consecutive packets more than two game-seconds behind the current session clock clear future observations and start a fresh state. One isolated delayed packet is ignored. Synthetic tests cover both paths; the ten-lap capture replayed with one expected session reset and no extra resets.
- **Phase 3 pit-window reminder:** the engineer now announces a valid game-provided ideal/latest window once when the current lap enters it, and announces the latest lap once if reached. It gives a reminder, not an uncalibrated box command. The original capture replay emitted the opening reminder at lap 5 from the recorded 5–9 window; the later 0/0 values produced no reminder.
- **Phase 3 finish-line boundary:** the conditional strategy model accepts the same two-metre track-length rounding margin already used by the fuel/wear estimator. This prevents brief loss of strategy availability immediately before crossing the line.
- **Phase 2 HUD comparison view:** the periodic console snapshot now shows DRS, surface and inner tyre temperatures, both sector times, pit status and stops, penalties, ERS deploy mode, actual and visual compound codes, wing damage, and game pit-window laps. Each displayed packet group includes its source and age; expired groups hide their values. A synthetic test checks the fresh and expired views.

The full automated suite passes 91 tests. The [final 103,702-packet CLI replay](replay-20261028-phase23-final.txt) finishes with zero header and payload errors, fuel/wear estimates available after lap 10 and one expected session reset. The game pit-window reminder appears once. Python compilation of `app` and `scripts` passes.

## Live verification still needed

Phase 2: compare decoded values to synchronized HUD/MFD readings, obtain the v16 attachment for direct field-by-field review, and exercise pause, disconnect/reconnect, session change and flashback. Phase 3: check pit-window reminder timing and audibility, low fuel/ERS/tyre/damage alerts and cooldowns during play, and measure performance with the receiver enabled. The run's pit loss and compound pace difference have not been calibrated, so the model must keep withholding an independent pit recommendation.
