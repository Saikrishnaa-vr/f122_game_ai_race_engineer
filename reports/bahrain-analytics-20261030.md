# Bahrain offline analytics sample — 2026-10-30

Input: original `grand-prix-20261028-212717.f1cap`, 164,005,729 bytes, SHA-256 `91d7ca520d1a572610bbd37cfd0b7f22e1e3f16c0ccb442892b5d3c21a081888`. The extractor read **140,578 datagrams**, rejected **0**, and produced **42,536 sampled observations** in a separate D: SQLite file. The capture and its published fixture were not changed.

The dataset has one classified race segment (2022 Session type 10, Bahrain track code 3) and a short later epoch with no fresh Session context. The latter follows a game-clock reset around the terminal classification/history burst. It is retained as a separate epoch, **not claimed as a second race or confirmed player flashback**, and is excluded from comparable-lap analyses. Packet 8 yields **20 first-observed final result rows**, including the player at P1 after 14 laps; the original capture contains two classification packets. A classification packet proves a game result was transmitted, but does not fill in missing intermediate telemetry.

| Analysis | Observed sample | Interpretation |
| --- | --- | --- |
| Comparable player fuel | 8 adjacent valid green lap pairs; median **1.8025 kg/lap**, range **1.772–1.814** | Difference between fresh lap-boundary samples, not exact per-corner fuel flow. |
| Comparable max-wheel wear | 8 pairs; median **5.797 percentage points/lap**, range **3.627–6.971** | Does not establish tyre life for another track, compound, weather or setup. |
| Comparable lap-time change | 8 pairs; median **−138.5 ms**, range **−2,079 to +5,679 ms** | Descriptive change, not isolated tyre degradation: traffic, fuel and driver variation remain. |
| Pit-affected lap | Lap 7, **22,324 ms** above median of nearby earlier green laps | Single-lap excess is not a calibrated pit-lane loss; in/out laps, pace and traffic are mixed. |
| Public rival timing | 19 rivals had at least two common valid lap numbers | Uses Session History only. No rival fuel, wear, private setup or planned stop. |
| Repeated braking distance bins | 14 bins with samples on at least two player laps | 100 m distance bins, not named corners or a proven faster line. |
| SC/VSC observation changes | 28 Session context changes, all SC code 0 | This race contains **no observed SC or VSC period**. Counts are context updates, not seconds spent green. |
| Practice setup comparison | 0 | A race capture cannot validate practice presets. |
| Temporal backtest plumbing | 10 next-lap predictions, median absolute error **1,570 ms** | Median of three earlier observed valid laps. This is an offline illustration, not a trained or calibrated lap-time model. |

The source timestamps, packet IDs/frames, join freshness and quality flags are in `data/analytics/grand-prix-20261028-212717.sqlite3`. Rebuild with `--replace` when the decoder changes. The exact command and schema are in [ANALYTICS_DATASET.md](../ANALYTICS_DATASET.md). A new practice recording with multiple presets, multiple races per track/compound and real SC/VSC sessions are needed to estimate uncertainty and validate strategy features. Live dashboard behavior has not changed.
