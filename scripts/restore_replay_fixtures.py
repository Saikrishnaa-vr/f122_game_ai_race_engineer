"""Restore the two published replay fixtures into the local recordings folder."""

from __future__ import annotations

import argparse
import gzip
from hashlib import sha256
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = {
    "verification-20261028-193443.f1cap": "3d63c29c507150d0a969a8e948317d679a219f2279c14cff668aaeb8e2ac07a4",
    "grand-prix-20261028-212717.f1cap": "91d7ca520d1a572610bbd37cfd0b7f22e1e3f16c0ccb442892b5d3c21a081888",
}


def digest(path: Path) -> str:
    result = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def restore(output_dir: Path, verify_only: bool = False) -> None:
    if not verify_only:
        output_dir.mkdir(parents=True, exist_ok=True)
    for name, expected in FIXTURES.items():
        archive = ROOT / "fixtures" / f"{name}.gz"
        if not archive.is_file():
            raise FileNotFoundError(f"Missing published fixture: {archive}")
        if verify_only:
            result = sha256()
            with gzip.open(archive, "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    result.update(chunk)
            if result.hexdigest() != expected:
                raise ValueError(f"Fixture digest mismatch: {archive}")
            print(f"Verified {archive}")
            continue
        destination = output_dir / name
        if destination.exists() and digest(destination) == expected:
            print(f"Verified existing {destination}")
            continue
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite a different recording: {destination}")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=output_dir, prefix="fixture-",
                                             suffix=".f1cap", delete=False) as temporary:
                temporary_path = Path(temporary.name)
                with gzip.open(archive, "rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        temporary.write(chunk)
            if digest(temporary_path) != expected:
                raise ValueError(f"Fixture digest mismatch: {archive}")
            os.replace(temporary_path, destination)
            temporary_path = None
            print(f"Restored {destination}")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="check archives without installing recordings")
    args = parser.parse_args()
    restore(ROOT / "recordings", verify_only=args.verify_only)
