"""Browse saved Grand Prix recordings or open one directly.

Run from the project root:
    python scripts/replay_dashboard.py
    python scripts/replay_dashboard.py grand-prix-20261028-212717.f1cap --pit-loss 22
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from threading import Thread
import time
from urllib.request import urlopen
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.replay_dashboard import ReplayController, ReplayLibrary, load_frames, serve_replay
from app.recordings import recording_info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture", nargs="?", help="Optional simple .f1cap filename in recordings/")
    parser.add_argument("--pit-loss", type=float, default=None,
                        help="Test-only assumed green-flag pit loss in seconds")
    parser.add_argument("--port", type=int, help="Dashboard port (library: 8766; direct replay: 8765)")
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--stop-file", type=Path)
    args = parser.parse_args()
    if args.pit_loss is not None and not 0 < args.pit_loss < 120:
        parser.error("--pit-loss must be between 0 and 120 seconds")
    if args.stop_file is not None and args.stop_file.exists():
        parser.error("stop file already exists")
    port = args.port or (8765 if args.capture else 8766)
    if not 1 <= port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.capture:
        print("Building seekable dashboard frames from the recording...", flush=True)
        frames = load_frames(args.capture, args.pit_loss)
        controller = ReplayController(frames, args.pit_loss, recording_info(args.capture))
        print(f"Replay ready: {len(frames)} frames, laps {frames[0].lap}-{frames[-1].lap}.", flush=True)
    else:
        controller = ReplayLibrary(args.pit_loss)
        print("Saved-race library ready. Choose a recording in your browser.", flush=True)
    url = f"http://127.0.0.1:{port}/"
    print(f"Dashboard: {url}", flush=True)
    if args.open_browser:
        def open_when_ready() -> None:
            for _ in range(30):
                try:
                    with urlopen(url, timeout=0.3) as response:
                        if response.status == 200:
                            webbrowser.open(url, new=2)
                            return
                except OSError:
                    time.sleep(0.1)
            print(f"Could not open browser automatically. Open {url}", flush=True)
        Thread(target=open_when_ready, daemon=True).start()
    serve_replay(controller, port, args.stop_file)


if __name__ == "__main__":
    main()
