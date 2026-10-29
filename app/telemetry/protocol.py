"""F1 22 UDP 2022 header and datagram validation."""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct


HEADER = struct.Struct("<HBBBBQfIBB")
PACKET_FORMAT = 2022
PACKET_VERSION = 1
CAR_COUNT = 22
NO_SECONDARY_CAR = 255

# Fixed packed datagram sizes, including the header. See README for the
# reference's conflicting Event size annotation and structure.
PACKET_TYPES: dict[int, tuple[str, int]] = {
    0: ("Motion", 1464),
    1: ("Session", 632),
    2: ("Lap Data", 972),
    3: ("Event", 40),
    4: ("Participants", 1257),
    5: ("Car Setups", 1102),
    6: ("Car Telemetry", 1347),
    7: ("Car Status", 1058),
    8: ("Final Classification", 1015),
    9: ("Lobby Info", 1191),
    10: ("Car Damage", 948),
    11: ("Session History", 1155),
}


class PacketError(ValueError):
    """A datagram does not match the supported F1 22 protocol."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PacketHeader:
    packet_format: int
    game_major_version: int
    game_minor_version: int
    packet_version: int
    packet_id: int
    session_uid: int
    session_time: float
    frame_identifier: int
    player_car_index: int
    secondary_player_car_index: int

    @property
    def packet_name(self) -> str:
        return PACKET_TYPES[self.packet_id][0]


def decode_packet(data: bytes) -> PacketHeader:
    """Validate a complete datagram and return its header, not its payload."""
    if len(data) < HEADER.size:
        raise PacketError("short_header", f"header needs {HEADER.size} bytes; got {len(data)}")

    header = PacketHeader(*HEADER.unpack_from(data))
    if header.packet_format != PACKET_FORMAT:
        raise PacketError("format", f"expected format {PACKET_FORMAT}; got {header.packet_format}")
    if header.packet_id not in PACKET_TYPES:
        raise PacketError("packet_id", f"unsupported packet ID {header.packet_id}")
    if header.packet_version != PACKET_VERSION:
        raise PacketError("version", f"unsupported packet version {header.packet_version}")
    expected_size = PACKET_TYPES[header.packet_id][1]
    if len(data) != expected_size:
        raise PacketError(
            "length",
            f"{header.packet_name} requires {expected_size} bytes; got {len(data)}",
        )
    if header.player_car_index >= CAR_COUNT:
        raise PacketError("player_index", f"invalid player car index {header.player_car_index}")
    if header.secondary_player_car_index >= CAR_COUNT and (
        header.secondary_player_car_index != NO_SECONDARY_CAR
    ):
        raise PacketError(
            "secondary_index",
            f"invalid secondary player car index {header.secondary_player_car_index}",
        )
    if not math.isfinite(header.session_time):
        raise PacketError("session_time", "session time is not finite")
    return header
