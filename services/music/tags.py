"""Tag and duration reads for the audio files of a node's music directory.

The service owns these reads: the library scan and the playlist reads both need the same fields, and
a playlist's length is only knowable from the files, so the reader lives beside the service that
walks the folder rather than in a plugin module.

TWO CONTAINERS, TWO READERS. ``.mp3`` is read with ``eyed3``, which parses ID3 and the MPEG frame
headers behind the duration. ``.ogg`` is read HERE, from the container's own headers: ``eyed3``
answers nothing for Ogg at all (no tag, ``duration`` 0.0), and a Vorbis/Opus header parse needs no
decoder and no dependency the installation does not already carry. Both shapes are the same dict,
so a caller never learns which container answered.
"""
from __future__ import annotations

import logging
import os

__all__ = ["AUDIO_SUFFIXES", "get_tag", "read_tags"]

log = logging.getLogger(__name__)

#: the file suffixes the library offers — the same pair the Discord upload handler accepts
AUDIO_SUFFIXES: tuple[str, ...] = (".mp3", ".ogg")

#: the fields one library entry carries, in the order a page renders them
TAG_FIELDS: tuple[str, ...] = ("title", "artist", "album")

#: the comment names an Ogg stream carries the field above under, upper-cased
OGG_COMMENT_NAMES: dict[str, str] = {"TITLE": "title", "ARTIST": "artist", "ALBUM": "album"}

#: the sample rate every Opus stream decodes at, whatever rate it was encoded from
OPUS_RATE = 48000

#: the suffix :func:`_read_ogg` answers; every other suffix is ``eyed3``'s
CONTAINER_SUFFIX = ".ogg"

#: the bytes read from each end of a file for the bounded read; one Ogg page is at most 65 307
HEAD_WINDOW = 65536
TAIL_WINDOW = 65536

#: the leading bytes of an Ogg page and of the two identification packets this reader understands
OGG_CAPTURE = b"OggS"
VORBIS_HEADER = b"\x01vorbis"
VORBIS_COMMENT = b"\x03vorbis"
OPUS_HEADER = b"OpusHead"
OPUS_COMMENT = b"OpusTags"


def get_tag(file: str):
    """The tag of *file* as an ``eyed3`` tag object, empty when the file carries no readable tag.

    A file that cannot be opened answers an empty tag rather than raising — a broken upload must
    not take a radio down. ``.ogg`` has no ``eyed3`` tag reader at all, so that suffix is answered
    by :func:`read_tags` and its fields are carried on the same empty tag object; ``duration``
    rides along as an extra attribute a caller may ignore.
    """
    from eyed3.id3 import Tag

    if os.path.splitext(file)[1].lower() == CONTAINER_SUFFIX:
        tag = Tag()
        found = read_tags(file)
        for field in TAG_FIELDS:
            if found.get(field):
                setattr(tag, field, found[field])
        setattr(tag, "duration", float(found.get("duration") or 0.0))
        return tag

    import eyed3

    try:
        audio = eyed3.load(file)
    except Exception:
        log.warning(f"- Music: the tags of {file} could not be read.", exc_info=True)
        return Tag()
    if not audio or not audio.tag:
        return Tag()
    return audio.tag


def _page(data: bytes, offset: int) -> tuple[int, int, int, bytes] | None:
    """``(page_end, serial, granule, lacing)`` of the Ogg page at *offset*, or ``None``.

    An Ogg page is a 27-byte header, a lacing table of ``segment_count`` byte counts and the bytes
    they describe. A page whose header or payload runs past the end of *data*, or whose capture
    pattern is missing, answers ``None``.
    """
    if offset + 27 > len(data) or data[offset:offset + 4] != OGG_CAPTURE:
        return None
    segment_count = data[offset + 26]
    table_end = offset + 27 + segment_count
    if table_end > len(data):
        return None
    lacing = data[offset + 27:table_end]
    page_end = table_end + sum(lacing)
    if page_end > len(data):
        return None
    return (page_end,
            int.from_bytes(data[offset + 14:offset + 18], "little"),
            int.from_bytes(data[offset + 6:offset + 14], "little", signed=True),
            lacing)


def _ogg_packets(data: bytes) -> tuple[list[bytes], int]:
    """The first two packets of an Ogg stream and the granule position of its last page.

    A packet continues while a lacing value is 255. Only the FIRST logical stream is read (a
    chained file's later streams describe other tracks), and only its first two packets are
    collected: the identification header and the comment header. The granule is the stream's
    sample position after a page, so the last page's non-negative value is the stream's total
    length.

    A truncated or foreign file answers whatever it did yield; nothing here raises.
    """
    packets: list[bytes] = []
    pending = bytearray()
    granule = 0
    serial: int | None = None
    offset = 0
    while True:
        page = _page(data, offset)
        if page is None:
            break
        page_end, page_serial, page_granule, lacing = page
        if serial is None:
            serial = page_serial
        if page_serial == serial:
            if page_granule >= 0:
                granule = page_granule
            if len(packets) < 2:
                start = offset + 27 + len(lacing)
                for value in lacing:
                    pending += data[start:start + value]
                    start += value
                    if value < 255:
                        packets.append(bytes(pending))
                        pending = bytearray()
        offset = page_end
    return packets, granule


def _uint32(data: bytes, offset: int) -> int:
    """The little-endian 32-bit value at *offset*; ``IndexError`` when the bytes are not there.

    Every length and count this reader trusts is read through here, so a truncated packet fails at
    the field that is missing instead of being answered with ``0`` from an empty slice.
    """
    if offset < 0 or offset + 4 > len(data):
        raise IndexError(f"a 32-bit field at {offset} runs past {len(data)} bytes")
    return int.from_bytes(data[offset:offset + 4], "little")


def _comment_fields(packet: bytes, prefix: int) -> dict:
    """The TITLE/ARTIST/ALBUM fields of a Vorbis or Opus comment packet.

    Both carry the same layout after their own magic: a 4-byte vendor length and its bytes, a 4-byte
    comment count, then per comment a 4-byte length and ``NAME=value``. Only ``OGG_COMMENT_NAMES`` is
    lifted; every other comment (and the pictures) is skipped. Names are compared upper-cased, as the
    formats require them to be case-insensitive.

    The packet is the whole budget: a count or a length that is not backed by four bytes still to
    read ends the parse (:func:`_uint32`), and a comment that runs past the packet ends it too. A
    declared count can therefore never outlive the bytes, whatever value it declares.
    """
    entry: dict = {}
    try:
        vendor = _uint32(packet, prefix)
        offset = prefix + 4 + vendor
        count = _uint32(packet, offset)
        offset += 4
        for _ in range(count):
            length = _uint32(packet, offset)
            offset += 4
            if offset + length > len(packet):
                break
            raw = packet[offset:offset + length]
            offset += length
            name, _, value = raw.decode("utf-8", "replace").partition("=")
            field = OGG_COMMENT_NAMES.get(name.strip().upper())
            if field and value and not entry.get(field):
                entry[field] = value
    except (IndexError, ValueError):
        log.debug("- Music: a comment header ended early.", exc_info=True)
    return entry


def _tail_granule(window: bytes, serial: int) -> int | None:
    """The granule of the page ending *window* for *serial*, or ``None`` when no page does.

    Only a page whose payload ends exactly at the window's end is accepted: a page is at most
    :data:`HEAD_WINDOW` bytes, so a stream's last page always starts inside the tail window, and a
    capture pattern inside a packet's payload cannot also end the window.
    """
    offset = window.rfind(OGG_CAPTURE)
    while offset >= 0:
        page = _page(window, offset)
        if page and page[0] == len(window) and page[1] == serial:
            return page[2]
        offset = window.rfind(OGG_CAPTURE, 0, offset)
    return None


def _windowed_read(file: str) -> tuple[list[bytes], int] | None:
    """The first two packets and the last granule of *file*, from its head and its tail only.

    ``None`` when those two windows cannot answer — a packet runs past the head window, or the page
    ending the file belongs to another stream. A file that fits in the head window is read whole
    from it. The caller reads the whole file when this answers ``None``, so a bounded read never
    trades an answer away.
    """
    try:
        with open(file, "rb") as handle:
            head = handle.read(HEAD_WINDOW)
            size = handle.seek(0, os.SEEK_END)
            if size <= len(head):
                return _ogg_packets(head)
            handle.seek(max(0, size - TAIL_WINDOW))
            tail = handle.read()
    except OSError:
        return None
    packets, _ = _ogg_packets(head)
    first = _page(head, 0)
    if len(packets) < 2 or first is None:
        return None
    granule = _tail_granule(tail, first[1])
    if granule is None:
        return None
    return packets, granule


def _read_ogg(file: str) -> dict:
    """The tag fields and the duration in seconds of ONE Ogg file, or an empty mapping.

    The first packet names the codec: a Vorbis identification header carries the sample rate the
    granule is counted in, an Opus one is always counted at :data:`OPUS_RATE` and carries a pre-skip
    the first samples were delayed by. Anything else in an Ogg container (a video stream, a codec
    this reader does not know) answers nothing rather than a wrong duration.

    :func:`_windowed_read` answers from the file's head and tail; only when its two windows cannot
    do so is the whole file read, so a folder scan pays two small reads per file.
    """
    found = _windowed_read(file)
    if found is None:
        try:
            with open(file, "rb") as handle:
                found = _ogg_packets(handle.read())
        except OSError:
            log.warning(f"- Music: {file} could not be read as audio.", exc_info=True)
            return {}
    packets, granule = found
    if not packets:
        return {}
    head = packets[0]
    if head.startswith(VORBIS_HEADER):
        rate = int.from_bytes(head[12:16], "little")
        skip = 0
        prefix = len(VORBIS_COMMENT)
    elif head.startswith(OPUS_HEADER):
        rate = OPUS_RATE
        skip = int.from_bytes(head[10:12], "little")
        prefix = len(OPUS_COMMENT)
    else:
        return {}
    entry = _comment_fields(packets[1], prefix) if len(packets) > 1 else {}
    if rate > 0:
        entry["duration"] = max(0.0, (granule - skip) / float(rate))
    return entry


def read_tags(file: str) -> dict:
    """The tag fields, the duration in seconds and the size in bytes of ONE audio file.

    Reads the file once for its tag and its info stream. A file that cannot be read answers the
    empty shape with its size (or ``0``) — never an exception: one unreadable file must not fail a
    whole library scan.

    ``.ogg`` is parsed here (:func:`_read_ogg`); every other suffix goes to ``eyed3``.
    """
    entry: dict = {field: "" for field in TAG_FIELDS}
    entry["duration"] = 0.0
    entry["size"] = 0
    try:
        entry["size"] = os.path.getsize(file)
    except OSError:
        return entry
    if os.path.splitext(file)[1].lower() == CONTAINER_SUFFIX:
        found = _read_ogg(file)
        for field in TAG_FIELDS:
            entry[field] = str(found.get(field) or "")
        try:
            entry["duration"] = float(found.get("duration") or 0.0)
        except (TypeError, ValueError):
            entry["duration"] = 0.0
        return entry
    try:
        import eyed3

        audio = eyed3.load(file)
    except Exception:
        log.warning(f"- Music: {file} could not be read as audio.", exc_info=True)
        return entry
    tag = getattr(audio, "tag", None)
    if tag is not None:
        for field in TAG_FIELDS:
            entry[field] = str(getattr(tag, field, "") or "")
    info = getattr(audio, "info", None)
    try:
        entry["duration"] = float(getattr(info, "time_secs", 0.0) or 0.0)
    except (TypeError, ValueError):
        entry["duration"] = 0.0
    return entry
