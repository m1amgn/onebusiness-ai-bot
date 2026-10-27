"""Bound Opus work from packet framing, without trusting Ogg granule positions.

Packet durations follow RFC 6716 section 3.1; Ogg framing follows RFC 7845.
This inspects framing, not the compressed audio payload decoded by the provider.
"""

from collections.abc import Iterator

from taskbot.domain.errors import UnreadableAudioError


def _pages(data: bytes) -> Iterator[tuple[bytes, bytes, bytes]]:
    offset = 0
    while offset < len(data):
        header = data[offset : offset + 27]
        if len(header) != 27 or header[:5] != b"OggS\0":
            raise UnreadableAudioError("Invalid Ogg page")
        table_start = offset + 27
        body_start = table_start + header[26]
        lengths = data[table_start:body_start]
        end = body_start + sum(lengths)
        if len(lengths) != header[26] or end > len(data):
            raise UnreadableAudioError("Truncated Ogg page")
        yield header, lengths, data[body_start:end]
        offset = end


def _packets(data: bytes) -> Iterator[bytes]:
    pending = bytearray()
    serial = data[14:18]
    ended = False
    for sequence, (header, lengths, body) in enumerate(_pages(data)):
        if ended:
            raise UnreadableAudioError("Data after Ogg end")
        flags = header[5]
        if flags & ~7 or bool(flags & 1) != bool(pending):
            raise UnreadableAudioError("Invalid Ogg continuation")
        page_serial = header[14:18]
        if page_serial != serial or bool(flags & 2) != (sequence == 0):
            raise UnreadableAudioError("Multiple Ogg streams")
        if int.from_bytes(header[18:22], "little") != sequence:
            raise UnreadableAudioError("Missing Ogg page")
        cursor = 0
        for length in lengths:
            pending.extend(body[cursor : cursor + length])
            cursor += length
            if length < 255:
                yield bytes(pending)
                pending.clear()
        ended = bool(flags & 4)
    if pending or not ended:
        raise UnreadableAudioError("Truncated Ogg stream")


def _packet_milliseconds(packet: bytes) -> float:
    if not packet:
        raise UnreadableAudioError("Empty Opus packet")
    config = packet[0] >> 3
    code = packet[0] & 3
    if config >= 16:
        frame = 2.5 * (1 << (config & 3))
    elif config >= 12:
        frame = float(10 << (config & 1))
    else:
        frame = 60.0 if config & 3 == 3 else float(10 << (config & 3))
    if code == 3:
        if len(packet) < 2:
            raise UnreadableAudioError("Truncated Opus packet")
        count = packet[1] & 63
    else:
        count = 1 if code == 0 else 2
    duration = frame * count
    if count == 0 or duration > 120:
        raise UnreadableAudioError("Invalid Opus packet duration")
    return duration


def opus_duration_seconds(data: bytes) -> float:
    milliseconds = 0.0
    count = 0
    for index, packet in enumerate(_packets(data)):
        if index == 0:
            if len(packet) < 19 or packet[:8] != b"OpusHead" or packet[9] == 0:
                raise UnreadableAudioError("Missing Opus header")
        elif index == 1:
            if not packet.startswith(b"OpusTags"):
                raise UnreadableAudioError("Missing Opus tags")
        else:
            if packet.startswith((b"OpusHead", b"OpusTags")):
                raise UnreadableAudioError("Repeated Opus header")
            milliseconds += _packet_milliseconds(packet)
            count += 1
    if count == 0:
        raise UnreadableAudioError("Missing Opus audio")
    return milliseconds / 1000
