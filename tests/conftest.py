"""Synthetic 2022 packets for protocol and local socket tests."""

from collections.abc import Callable
import struct

import pytest


@pytest.fixture
def packet_factory() -> Callable[..., bytes]:
    def make_packet(
        packet_id: int = 3,
        *,
        size: int = 40,
        packet_format: int = 2022,
        packet_version: int = 1,
        session_uid: int = 0x0102030405060708,
        session_time: float = 12.5,
        frame: int = 0x11223344,
        player: int = 2,
        secondary: int = 255,
    ) -> bytes:
        header = struct.pack(
            "<HBBBBQfIBB",
            packet_format,
            1,
            18,
            packet_version,
            packet_id,
            session_uid,
            session_time,
            frame,
            player,
            secondary,
        )
        return (header + bytes(max(0, size - len(header))))[:size]

    return make_packet
