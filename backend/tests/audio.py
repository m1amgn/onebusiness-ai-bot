"""Synthetic Ogg/Opus packets for duration checks; no recorded speech."""

import struct


def ogg_page(packets: list[bytes], sequence: int, *, flags: int = 0, granule: int = 0) -> bytes:
    lengths = bytes(len(packet) for packet in packets)
    header = (
        b"OggS\0" + bytes([flags]) + struct.pack("<QIIIB", granule, 1, sequence, 0, len(lengths))
    )
    return header + lengths + b"".join(packets)


def synthetic_opus(seconds: float, *, granule: int = 0) -> bytes:
    head = b"OpusHead" + bytes([1, 1]) + struct.pack("<HIhB", 312, 48000, 0, 0)
    result = ogg_page([head], 0, flags=2) + ogg_page([b"OpusTags" + bytes(8)], 1)
    packets = [b"\xf8\xff\xfe"] * round(seconds * 50)
    for offset in range(0, len(packets), 200):
        last = offset + 200 >= len(packets)
        result += ogg_page(
            packets[offset : offset + 200],
            offset // 200 + 2,
            flags=4 if last else 0,
            granule=granule,
        )
    return result
