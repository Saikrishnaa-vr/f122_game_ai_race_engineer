# LAN recording investigation — 2026-10-29

## Evidence from local captures and logs

The two local Mexico race files were read without modification. Both contain valid race Session packets with type 10 and end with a `SEND` event. Neither reached a size limit.

| Local recording start | UID | Packets | Last lap | Bytes | Prior sidecar status | Last captured event |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 19:48:01 | 1652477051737178769 | 15,025 | 2 | 17,403,175 | `session_changed` | `SEND` |
| 19:56:33 | 7788338931237168579 | 14,437 | 1 | 16,832,770 | `session_changed` | `SEND` |

The receiver log reports zero invalid packets and zero payload errors while these races were flowing. For the second capture, the sidecar's start time plus duration places its last packet around 19:58:56 local. Receiver snapshots at 19:58:58, 19:59:03, and later still show traffic from UID `7788338931237168579`. The frame counter reset from roughly 3,100 to 39, and the game clock restarted near zero. Thus the companion continued receiving UDP after the recorder stopped. The old recorder closed as soon as **any** different UID appeared and then suppressed the closed UID through `completed_uid`. This is a confirmed mechanism for losing later packets in that test.

The first capture's `SEND` is followed by a quiet period and then UID 0 menu traffic in the receiver log. Available evidence does not prove that its race continued. The application did not log individual transition reasons at the time, so the exact first packet that triggered each old `session_changed` close is unavailable. The source of `SEND`, the reason the game clock reset, and whether LAN host/client roles affected these events cannot be established from the stored packets alone. A `SEND` packet is evidence of the game's session-ended signal; it is not proof that a scheduled 71-lap race finished.

## Recorder changes

- Start only on a valid race Session packet. Types 10, 11 and 12 are treated as race sessions; the same types are visible and replayable in the library. These are the F1 22 R/R2/R3 race codes discussed in the [EA F1 22 UDP specification thread](https://forums.ea.com/discussions/f1-games-franchise-discussion-en/f1-22-udp-specification/8418392). The observed LAN packets used type 10.
- Do not close on a packet gap, a lobby packet, or one packet from another UID. A new race Session packet confirms a different-UID race boundary.
- If `SEND` was seen and a later race Session packet with the same UID rewinds the game clock by more than 30 seconds, close the old file and start a separate one, even if the old file has already closed after its grace period. Other same-UID packets continue in the current file.
- Use packet quiet time for the classification and `SEND` grace periods. Continued valid traffic from the active UID postpones the close.
- When an automatic file reaches 2 GiB, close it as `continued` and start a linked segment with an original Session packet followed by the packet that crossed the limit. Each file remains independently readable under the replay cap. Manual `--record` still stops at 256 MiB.
- Persist status separately from stop reason. A file without `SEND` or classification is `incomplete`; one with `SEND` is `ended`; one with classification is `completed`. The sidecar also records `continued_from` for segments.
- Log structured `recording event=...` and `telemetry event=...` lines for boundaries, counts, bytes, write errors, last write age, state resets, interruptions, and recovery. Invalid packet logs are rate limited; periodic health lines are every 30 seconds.
- Pass a Session observation to the recorder only when it was decoded from the current Session datagram. A header-valid but payload-invalid packet cannot borrow the previous cached Session and trigger a false race switch.

## Automated verification

`python -m pytest -q --basetemp .pytest_sandbox_tmp`: **140 passed**. New cases cover race types 10/11/12 through lap 5, a temporary gap and lobby packet, a new UID race, same-UID restart/type boundaries both before and after closing, exact raw packet bytes, incomplete status, an automatic size rollover, diagnostic counters, and a real local UDP receiver run that records lap 4 after a socket timeout, an unrelated UID packet, and a payload-invalid Session packet. Existing Grand Prix behavior remains covered.

`python scripts/verify_grand_prix.py`: **passed**. The unchanged Bahrain capture has SHA-256 `91d7ca520d1a572610bbd37cfd0b7f22e1e3f16c0ccb442892b5d3c21a081888`, 140,578 packets, zero decode errors, and lap 14. The replay library produced 2,683 frames and checked all 20 map/grid identities on lap 8.

## Next live LAN validation

Start the companion with `Start Race Engineer.cmd` before the host and client enter a race. Record which machine is sending the local UDP feed. Drive beyond lap 2 and watch the live dashboard and `logs/race-engineer.log` for `recording event=health` (accepted packets and increasing bytes), `telemetry event=connection_stale` / `connection_receiving`, and any `recording event=session_restart` or `session_change`. After stopping, open `Replay Saved Races.cmd`; verify the lap reached, status, stop reason, and that playback reaches the same point. If the game sends `SEND` or resets the clock unexpectedly, save the exact local log time and both machines' in-game state. A single host/client test with a deliberate short UDP interruption will check recovery; a later legitimate new race should create a separate library entry.

The published fixtures and the two local raw captures were not edited.
