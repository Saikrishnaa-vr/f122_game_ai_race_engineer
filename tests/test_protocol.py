from collections.abc import Callable
import math
import struct

import pytest

from app.telemetry.protocol import HEADER, PACKET_TYPES, PacketError, decode_packet


def test_event_size_matches_packed_speed_trap_union_and_live_observation(
    packet_factory: Callable[..., bytes],
) -> None:
    speed_trap_size = struct.calcsize("<BfBBBf")
    assert speed_trap_size == 12
    packet_size = HEADER.size + 4 + speed_trap_size
    assert packet_size == 40
    assert decode_packet(packet_factory(3, size=packet_size)).packet_name == "Event"
    with pytest.raises(PacketError) as error:
        decode_packet(packet_factory(3, size=36))
    assert error.value.reason == "length"


def test_header_layout_has_known_little_endian_bytes() -> None:
    header = bytes.fromhex("e6 07 01 12 01 03 08 07 06 05 04 03 02 01 00 00 48 41 44 33 22 11 02 ff")
    decoded = decode_packet(header + bytes(16))
    assert HEADER.size == 24
    assert decoded.packet_format == 2022
    assert (decoded.game_major_version, decoded.game_minor_version) == (1, 18)
    assert (decoded.packet_id, decoded.packet_name) == (3, "Event")
    assert decoded.session_uid == 0x0102030405060708
    assert decoded.session_time == 12.5
    assert decoded.frame_identifier == 0x11223344
    assert (decoded.player_car_index, decoded.secondary_player_car_index) == (2, 255)


@pytest.mark.parametrize(
    ("packet_id", "size"),
    [(0, 1464), (1, 632), (2, 972), (3, 40), (4, 1257), (5, 1102),
     (6, 1347), (7, 1058), (8, 1015), (9, 1191), (10, 948), (11, 1155)],
)
def test_all_documented_packet_types(packet_factory: Callable[..., bytes], packet_id: int, size: int) -> None:
    assert decode_packet(packet_factory(packet_id, size=size)).packet_id == packet_id
    assert PACKET_TYPES[packet_id][1] == size
    for bad_size in (size - 1, size + 1):
        with pytest.raises(PacketError) as error:
            decode_packet(packet_factory(packet_id, size=bad_size))
        assert error.value.reason == "length"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"size": 0}, "short_header"),
        ({"size": 23}, "short_header"),
        ({"packet_format": 2023}, "format"),
        ({"packet_version": 0}, "version"),
        ({"packet_version": 2}, "version"),
        ({"packet_id": 12}, "packet_id"),
        ({"player": 22}, "player_index"),
        ({"player": 255}, "player_index"),
        ({"secondary": 22}, "secondary_index"),
        ({"session_time": math.nan}, "session_time"),
        ({"session_time": math.inf}, "session_time"),
    ],
)
def test_invalid_packets(packet_factory: Callable[..., bytes], kwargs: dict[str, object], reason: str) -> None:
    with pytest.raises(PacketError) as error:
        decode_packet(packet_factory(**kwargs))
    assert error.value.reason == reason


def test_boundary_indices_and_session_identifiers(packet_factory: Callable[..., bytes]) -> None:
    header = decode_packet(packet_factory(player=21, secondary=21, session_uid=0, frame=0))
    assert (header.player_car_index, header.secondary_player_car_index) == (21, 21)
    assert (header.session_uid, header.frame_identifier) == (0, 0)
