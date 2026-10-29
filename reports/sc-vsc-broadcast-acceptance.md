# SC/VSC Broadcast Experience: real-game acceptance test

Status: **not yet run**. The Bahrain Grand Prix capture has no SC/VSC evidence. This plan checks the new behavior in the game without changing game files, reading memory or sending commands.

## Preparation

1. Use the current `develop` build after this feature is merged. Set F1 22 UDP telemetry **On**, format **2022**, destination `127.0.0.1`, port `20777`, and a known send rate. Leave UDP Broadcast Mode **Off** when the companion runs on the same PC. This network setting is separate from Safety Car Experience.
2. In game Rules and Flags, enable Safety Car/VSC and select **Broadcast** Safety Car Experience. Record the exact race length, track, date/time, game mode, and whether this is Career or LAN. A Short or shorter session can disable Safety Car in game settings; select a length that permits deployment.
3. Run `Start Race Engineer.cmd` or `python -m app --drive`. Keep the diagnostic console/log and dashboard open. Do not alter the game settings from the companion.

## Career Mode run

1. Start a Career race, confirm the dashboard reads **GREEN** from a fresh Session packet, and note the Session UID and lap.
2. When an SC or VSC occurs, note the in-game flag, the dashboard **SC/VSC** badge and banner, and the time/lap. If the game enters a cinematic sequence, note whether UDP packets pause or the game session time jumps. The recording must stay open.
3. If safe in gameplay, enter and leave the pit lane during neutralization. Confirm a pit entry/exit event only if the saved Lap packets report it. Check that fuel/tyre rates, lift cue, chase pace and inferred DNF are withheld during the neutralization.
4. At the restart, check for a fresh Session code 0, **GREEN** status, no invented intermediate telemetry, and pace guidance only after comparable green-flag data returns. Complete the race and confirm classification/completion status if the game sends it.
5. Open the saved replay. Seek through SC/VSC, packet silence and restart. The replay must show the same observed transitions and an explicit stale/unobserved interval where packets were absent. Compare the dashboard against the in-game footage or notes, not against assumptions about missing packets.

## LAN multiplayer run

1. Repeat in a LAN race with the companion on the **same PC** and the game UDP destination still `127.0.0.1:20777`. Record whether this computer is host or client; the 2022 UDP fields do not prove that role.
2. Play beyond lap 2. Check that a Broadcast SC/VSC or packet gap does not close the active capture or split one valid race. Note any `SEND`, classification, UID or player-index changes from the diagnostic log.
3. Finish or stop cleanly, open the saved replay, and verify the same status/gap/pit behavior. If a second race starts, confirm a separate recording. If no SC/VSC occurs, mark this run as inconclusive for the neutralization transition and repeat when one occurs.

Optional remote-companion check: bind explicitly to the companion LAN IP and filter to the game PC IP as documented in the README; configure the game's UDP destination manually. The dashboard HTTP page remains on the companion's loopback interface.

## Evidence to retain

Keep the `.f1cap` and `.json` sidecar, `logs/race-engineer.log`, a short list of in-game times/laps/statuses, and screenshots or video if available. Compare packet arrival gaps, Session safety code, Lap pit status, `FLBK`/`SEND` events, frame IDs and game session times. Do not publish captures without checking participant names.

## Protocol limits

The 2022 Session `m_safetyCarStatus` codes are 0 none, 1 full SC, 2 VSC, 3 formation lap. Lap `m_pitStatus` is 0 none, 1 pitting, 2 in pit area. The documented Event list includes `FLBK` and `SEND`, but no standalone SC deployment event. If a code transition, pit state, or intermediate position was not transmitted, the companion cannot reconstruct it. Packet silence and a forward game-time jump do not prove that Broadcast Experience caused them. Sources: [EA F1 22 UDP specification](https://forums.ea.com/discussions/f1-games-franchise-discussion-en/f1-22-udp-specification/8418392), [EA F1 22 telemetry settings](https://www.frostbite.com/able/resources/f1-22/pc/telemetry), [EA F1 22 broadcast features](https://www.ea.com/ea-studios/ea-sports/news/f1-2022-features).
