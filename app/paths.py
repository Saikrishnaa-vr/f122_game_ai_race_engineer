"""Writable companion data root beside the source tree or packaged executable."""

from __future__ import annotations

from pathlib import Path
import sys


def data_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]
