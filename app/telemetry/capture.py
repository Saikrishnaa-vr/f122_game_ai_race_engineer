"""Opt-in bounded packet capture and deterministic offline replay input."""

from __future__ import annotations

from pathlib import Path
import math
import re
import struct
from typing import BinaryIO, Iterator

from ..paths import data_root


CAPTURE_DIRECTORY = data_root() / "recordings"
MAGIC = b"F122CAP1"
RECORD = struct.Struct("<dH")
MAX_CAPTURE_BYTES = 256 * 1024 * 1024
MAX_AUTO_CAPTURE_BYTES = 2 * 1024 * 1024 * 1024


def capture_path(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.f1cap", name):
        raise ValueError("capture name must be a simple filename ending in .f1cap")
    path = CAPTURE_DIRECTORY / name
    if (
        CAPTURE_DIRECTORY.is_symlink()
        or CAPTURE_DIRECTORY.resolve().parent != CAPTURE_DIRECTORY.parent.resolve()
        or path.is_symlink()
        or path.resolve().parent != CAPTURE_DIRECTORY.resolve()
    ):
        raise ValueError("recording directory and capture file must not be symbolic links")
    return path


class CaptureWriter:
    def __init__(self, name: str, max_bytes: int | None = None) -> None:
        max_bytes = MAX_CAPTURE_BYTES if max_bytes is None else max_bytes
        if not len(MAGIC) < max_bytes <= MAX_AUTO_CAPTURE_BYTES:
            raise ValueError("recording size limit is invalid")
        path = capture_path(name)
        CAPTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        self.file: BinaryIO = path.open("xb", buffering=65536)
        self.file.write(MAGIC)
        self.size = len(MAGIC)
        self.max_bytes = max_bytes
        self.last_elapsed = -1.0

    def write(self, elapsed: float, data: bytes) -> bool:
        if not math.isfinite(elapsed) or elapsed < 0 or elapsed < self.last_elapsed:
            raise ValueError("capture timestamps must be finite and nondecreasing")
        if len(data) > 65535:
            raise ValueError("capture datagram exceeds the record length field")
        record = RECORD.pack(elapsed, len(data))
        if self.size + len(record) + len(data) > self.max_bytes:
            return False
        self.file.write(record)
        self.file.write(data)
        self.size += len(record) + len(data)
        self.last_elapsed = elapsed
        return True

    def close(self) -> None:
        self.file.close()


def read_capture(name: str) -> Iterator[tuple[float, bytes]]:
    path = capture_path(name)
    with path.open("rb") as file:
        if path.stat().st_size > MAX_AUTO_CAPTURE_BYTES:
            raise ValueError("capture exceeds the 2 GiB replay limit")
        if file.read(len(MAGIC)) != MAGIC:
            raise ValueError("invalid or unsupported capture header")
        previous = -1.0
        while True:
            record = file.read(RECORD.size)
            if not record:
                return
            if len(record) != RECORD.size:
                raise ValueError("truncated capture record header")
            elapsed, size = RECORD.unpack(record)
            if not (0 <= elapsed < float("inf")) or elapsed < previous:
                raise ValueError("capture timestamps must be finite and nondecreasing")
            data = file.read(size)
            if len(data) != size:
                raise ValueError("truncated capture datagram")
            previous = elapsed
            yield elapsed, data
