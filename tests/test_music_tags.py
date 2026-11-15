"""The Ogg tag reader on the bytes an upload can supply, and the one tag rule both surfaces use.

Every fixture here is built in code: the readers parse whatever a file holds, so a crafted container
is the threat model, not a corner case. Each pin asserts the RESULT of a read (an empty entry, the
same three fields through both doors) and never the intermediate steps.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import time

import pytest

from services.music.tags import get_tag, read_tags

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                     reason="ffmpeg is not installed: no audio file can be written")

SERIAL = 0x1234


def _page(payload: bytes, lacing: bytes | None = None, granule: int = 0, serial: int = SERIAL,
          header_type: int = 0, segment_count: int | None = None) -> bytes:
    """One Ogg page around *payload*.

    *lacing* overrides the segment table (so a table can lie about the bytes behind it), and
    *segment_count* overrides the count the header declares.
    """
    if lacing is None:
        lacing = bytes([len(payload)]) if payload else b""
    count = len(lacing) if segment_count is None else segment_count
    head = (b"OggS" + bytes([0, header_type]) + struct.pack("<q", granule)
            + struct.pack("<I", serial) + struct.pack("<I", 0) + b"\x00\x00\x00\x00"
            + bytes([count]))
    return head + lacing + payload


def _vorbis_head(rate: int = 44100, size: int = 44) -> bytes:
    """A Vorbis identification packet: magic, version, channels, the sample rate, zero padded."""
    head = b"\x01vorbis" + b"\x00\x00\x00\x00" + b"\x02" + struct.pack("<I", rate)
    return head.ljust(size, b"\x00")


def _comment(count: int, fields: tuple = (), vendor: bytes = b"", declared: int | None = None) -> bytes:
    """A Vorbis comment packet declaring *count* comments, carrying *fields*.

    *declared* overrides the length written for the LAST field, so a comment can claim more bytes
    than the packet holds.
    """
    body = b"\x03vorbis" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", count)
    for index, entry in enumerate(fields):
        length = len(entry) if declared is None or index < len(fields) - 1 else declared
        body += struct.pack("<I", length) + entry
    return body


def crafted_88() -> bytes:
    """88 bytes: a Vorbis stream whose comment header declares 0xFFFFFFFF comments and stops."""
    head = _vorbis_head()
    body = _comment(0xFFFFFFFF)
    data = _page(head + body, lacing=bytes([len(head), len(body)]), header_type=2)
    assert len(data) == 88, len(data)
    return data


def write(directory, name: str, data: bytes) -> str:
    target = directory / name
    target.write_bytes(data)
    return str(target)


def _audio(directory, name: str, codec: str, seconds: int = 7, metadata: tuple = ()) -> str:
    """One sine written by ffmpeg, with the given ``key=value`` metadata."""
    target = directory / name
    command = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
               "-i", f"sine=frequency=440:duration={seconds}"]
    for entry in metadata:
        command += ["-metadata", entry]
    command += ["-c:a", codec, str(target)]
    subprocess.run(command, check=True)
    return str(target)


# ------------------------------------------------------------------ 1. the declared count cannot spin

def test_a_crafted_ogg_that_declares_four_billion_comments_answers_empty_at_once(tmp_path):
    """A comment count the packet cannot back ends the parse at the packet's end, not at the count."""
    file = write(tmp_path, "crafted.ogg", crafted_88())
    assert len(crafted_88()) == 88

    started = time.perf_counter()
    found = read_tags(file)
    elapsed = time.perf_counter() - started

    assert found == {"title": "", "artist": "", "album": "", "duration": 0.0, "size": 88}
    assert elapsed < 1.0, f"the read took {elapsed:.1f}s on 88 bytes"


# ------------------------------------------------------------------ 2. broken pages answer, never raise

def test_broken_pages_answer_an_empty_entry_instead_of_raising(tmp_path):
    """A zero length, a length past the end and a huge segment count are all reads that answer {}."""
    head = _vorbis_head()

    # a page whose two packets are zero bytes long
    zero_length = write(tmp_path, "zero.ogg",
                        _page(head + _comment(0), lacing=bytes([len(head), 0]), header_type=2))

    # a page that claims 500 payload bytes and carries 10
    past_end = write(tmp_path, "past.ogg",
                     _page(head + b"\x00" * 10, lacing=bytes([len(head), 200]), header_type=2))

    # a page that declares 255 segments and holds a one-byte table
    huge_count = write(tmp_path, "count.ogg",
                       _page(head, lacing=bytes([len(head)]), segment_count=255, header_type=2))

    # a comment that claims 1000 bytes and holds 5
    field_past_end = write(tmp_path, "field.ogg",
                           _page(head + _comment(1, (b"TITLE=abcde",), declared=1000),
                                 lacing=bytes([len(head), 20]), header_type=2))

    # nothing at all
    empty = write(tmp_path, "empty.ogg", b"")

    for file in (zero_length, past_end, huge_count, field_past_end, empty):
        found = read_tags(file)
        assert found["title"] == "" and found["artist"] == "" and found["album"] == ""
        assert found["duration"] == 0.0
        assert get_tag(file).title is None


def test_a_comment_whose_length_runs_past_the_packet_keeps_the_fields_it_read(tmp_path):
    """One truncated comment ends the list; the comments before it are still reported."""
    packet = _comment(2, (b"TITLE=Read Me", b"ARTIST=never ends"), declared=999)
    from services.music.tags import _comment_fields

    assert _comment_fields(packet, len(b"\x03vorbis")) == {"title": "Read Me"}


# ------------------------------------------------------------------ 3. one tag rule for both surfaces

@requires_ffmpeg
def test_the_same_ogg_answers_the_same_fields_through_get_tag_and_read_tags(tmp_path):
    """The chat client and the website read one file the same way: title, artist and duration."""
    file = _audio(tmp_path, "tagged.ogg", "libvorbis",
                  metadata=("title=Ogg Title", "artist=Ogg Artist", "album=Ogg Album"))
    read = read_tags(file)
    tag = get_tag(file)

    assert tag.title == read["title"] == "Ogg Title"
    assert tag.artist == read["artist"] == "Ogg Artist"
    assert tag.album == read["album"] == "Ogg Album"
    assert getattr(tag, "duration") == read["duration"] == pytest.approx(7.0, abs=0.2)


def test_an_unreadable_ogg_still_answers_an_empty_tag(tmp_path):
    """``get_tag`` keeps its tolerance: a missing file answers an empty tag object, never a raise."""
    missing = str(tmp_path / "gone.ogg")
    assert get_tag(missing).title is None
    assert get_tag(missing).artist is None


# ------------------------------------------------------------------ 4. real files keep their length

@requires_ffmpeg
def test_a_real_vorbis_and_a_real_opus_file_still_report_seven_seconds(tmp_path):
    """The bounded read and the header parse agree with a file ffmpeg actually wrote."""
    from services.music import tags

    vorbis = _audio(tmp_path, "plain.ogg", "libvorbis")
    opus = _audio(tmp_path, "plain.opus.ogg", "libopus")

    assert read_tags(vorbis)["duration"] == pytest.approx(7.0, abs=0.2)
    assert read_tags(opus)["duration"] == pytest.approx(7.0, abs=0.2)


@requires_ffmpeg
def test_a_file_larger_than_both_windows_is_still_measured_exactly(tmp_path):
    """A tail-window answer equals the whole-file answer: the last page ends the window."""
    from services.music import tags

    file = _audio(tmp_path, "long.ogg", "libvorbis", seconds=60)
    assert (tmp_path / "long.ogg").stat().st_size > tags.HEAD_WINDOW

    windowed = tags._windowed_read(file)
    assert windowed is not None, "a plain ffmpeg file is answered from its head and its tail"
    with open(file, "rb") as handle:
        whole = tags._ogg_packets(handle.read())
    assert windowed[0] == whole[0] and windowed[1] == whole[1], "the windows answer what the whole file does"
    assert read_tags(file)["duration"] == pytest.approx(60.0, abs=0.5)
