"""Rebuild a local, read-only analytics copy from a canonical .f1cap file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics import extract_capture, summarize_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", help="simple .f1cap filename in recordings/")
    parser.add_argument("--replace", action="store_true", help="replace only the derived dataset")
    parser.add_argument("--as-of", type=float, metavar="SECONDS",
                        help="exclude observations first received after this capture time")
    arguments = parser.parse_args()
    result = extract_capture(arguments.capture, replace=arguments.replace)
    result["analysis"] = summarize_dataset(Path(result["dataset"]),
                                            as_of_elapsed_s=arguments.as_of)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
