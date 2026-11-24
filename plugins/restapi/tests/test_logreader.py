"""
Unit tests for the RestAPI log reader (plugins/restapi/logreader.py).

The reader only talks to a node through ``read_file_window`` and ``list_files``; FakeNode implements
both over an in-memory file system with the same semantics as ``NodeImpl`` (window clamping, the size
and identity at read time), so rotations can be staged exactly, including NTFS file system tunneling
(a replacement file that keeps the identity of the file it replaces).
"""
import asyncio
import fnmatch
import posixpath
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.restapi.logreader import (BOT_FORMAT, DCS_FORMAT, Cursor, LogFilter, LogNotFound, LogReader,
                                       LogRequestError, LogSource, MAX_LINE_BYTES, build_filter, parse_time)


class FakeNode:
    def __init__(self):
        self.files: dict[str, list] = {}     # path -> [bytearray, identity, mtime]
        self.clock = 0
        self.reads = 0

    def write(self, path: str, data: str | bytes, identity=None):
        self.clock += 1
        data = data.encode() if isinstance(data, str) else data
        self.files[path] = [bytearray(data), identity if identity is not None else self.clock, float(self.clock)]

    def append(self, path: str, data: str):
        self.clock += 1
        self.files[path][0] += data.encode()
        self.files[path][2] = float(self.clock)

    def rename(self, old: str, new: str):
        self.files[new] = self.files.pop(old)

    async def read_file_window(self, path, *, length, offset=None, behind=None):
        self.reads += 1
        if path not in self.files:
            raise FileNotFoundError(path)
        data, identity, _ = self.files[path]
        size = len(data)
        length = min(length, 1024 * 1024)
        if behind is not None:
            end = max(0, min(behind, size))
            start = max(0, end - length)
        else:
            start = min(offset, size)
            end = min(size, start + length)
        return bytes(data[start:end]), size, identity

    async def list_files(self, path, *, pattern="*"):
        patterns = [pattern] if isinstance(pattern, str) else pattern
        found = [(p, len(d), m) for p, (d, _, m) in self.files.items()
                 if posixpath.dirname(p) == path and any(fnmatch.fnmatch(posixpath.basename(p), pat) for pat in patterns)]
        return sorted(found, key=lambda entry: entry[2], reverse=True)


BOT_SOURCE = LogSource(kind="bot", key="node1", directory="logs", live="dcssb-node1.log",
                       patterns=("dcssb-node1.log", "dcssb-node1.log.*"), format=BOT_FORMAT)
DCS_SOURCE = LogSource(kind="dcs", key="My Server", directory="home/Logs", live="dcs.log",
                       patterns=("dcs*.log*",), format=DCS_FORMAT)
BOT = "logs/dcssb-node1.log"
DCS = "home/Logs/dcs.log"


def bot_line(n: int, level: str = "INFO", minute: int = 0) -> str:
    return f"2026-10-18 10:{minute:02d}:{n % 60:02d}.{n % 1000:03d} {level}\tmessage {n}\n"


def dcs_line(n: int, level: str = "INFO") -> str:
    return f"2026-10-18 10:00:{n % 60:02d}.{n % 1000:03d} {level:<8}APP (Main): event {n}\n"


def run(coro):
    return asyncio.run(coro)


def texts(result):
    return [entry.text for entry in result.entries]


def reader(node, source=BOT_SOURCE, **kwargs):
    return LogReader(source, node.read_file_window, node.list_files, **kwargs)


# ============================================================================= parsing and filters

class TestParsing:
    def test_bot_and_dcs_lines(self):
        node = FakeNode()
        node.write(BOT, bot_line(1, "WARNING"))
        node.write(DCS, "=== Log opened UTC 2026-10-18 10:00:00\n" + dcs_line(2, "ERROR"))
        bot = run(reader(node).read())
        assert [(e.level, e.text) for e in bot.entries] == [("WARNING", "message 1")]
        dcs = run(reader(node, DCS_SOURCE).read())
        assert [(e.level, e.text) for e in dcs.entries] == [("", "=== Log opened UTC 2026-10-18 10:00:00"),
                                                           ("ERROR", "APP (Main): event 2")]

    def test_traceback_stays_with_its_entry(self):
        node = FakeNode()
        node.write(BOT, bot_line(1) + bot_line(2, "ERROR") + "Traceback (most recent call last):\n"
                   "  File \"x.py\", line 1\nValueError: boom\n" + bot_line(3, "DEBUG"))
        result = run(reader(node).read(flt=build_filter(level="error")))
        assert len(result.entries) == 1
        assert result.entries[0].text.endswith("ValueError: boom")

    def test_long_line_is_cut(self):
        node = FakeNode()
        node.write(BOT, bot_line(1).replace("message 1", "x" * (MAX_LINE_BYTES * 2)))
        result = run(reader(node).read())
        assert result.entries[0].text.endswith("[...truncated]")
        assert len(result.entries[0].text) < MAX_LINE_BYTES + 100

    def test_contains_and_level(self):
        node = FakeNode()
        node.write(BOT, "".join(bot_line(i, "ERROR" if i % 10 == 0 else "INFO") for i in range(1, 50)))
        assert texts(run(reader(node).read(flt=build_filter(contains="MESSAGE 4")))) == \
            ["message 4"] + [f"message {i}" for i in range(40, 50)]
        assert texts(run(reader(node).read(flt=build_filter(contains=["message 4", "error"])))) == ["message 40"]
        assert texts(run(reader(node).read(flt=build_filter(level="error")))) == \
            [f"message {i}" for i in (10, 20, 30, 40)]

    def test_bad_filters_are_refused(self):
        with pytest.raises(LogRequestError):
            build_filter(contains=[str(i) for i in range(11)])
        with pytest.raises(LogRequestError):
            build_filter(level="verbose")
        with pytest.raises(LogRequestError):
            build_filter(since="yesterday")
        with pytest.raises(LogRequestError):
            build_filter(since="999999d")

    def test_level_filter_on_a_new_file_keeps_its_headless_first_line_out(self):
        node = FakeNode()
        node.write(DCS, "")
        first = run(reader(node, DCS_SOURCE).read(flt=build_filter(level="error")))
        node.append(DCS, "=== Log opened UTC 2026-10-18 10:00:00\n" + dcs_line(2, "ERROR"))
        follow = run(reader(node, DCS_SOURCE).read(flt=build_filter(level="error"), cursor=first.cursor))
        fresh = run(reader(node, DCS_SOURCE).read(flt=build_filter(level="error")))
        assert texts(follow) == texts(fresh) == ["APP (Main): event 2"]

    def test_parse_time(self):
        now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
        assert parse_time("30m", now=now) == datetime(2026, 10, 18, 11, 30, tzinfo=timezone.utc)
        assert parse_time("2026-10-18T10:00:00", now=now) == datetime(2026, 10, 18, 10, tzinfo=timezone.utc)
        assert parse_time("2026-10-18T12:00:00+02:00") == datetime(2026, 10, 18, 10, tzinfo=timezone.utc)
        assert parse_time(None) is None


# ============================================================================= tail and forward

class TestTail:
    def test_last_lines_across_windows(self):
        node = FakeNode()
        node.write(BOT, "".join(bot_line(i) for i in range(1000)))
        result = run(reader(node, window=100).read(lines=25))
        assert texts(result) == [f"message {i}" for i in range(975, 1000)]
        assert result.more is True

    def test_filter_does_not_shrink_the_tail(self):
        node = FakeNode()
        lines = [bot_line(i, "ERROR" if i < 3 else "DEBUG") for i in range(500)]
        node.write(BOT, "".join(lines))
        result = run(reader(node, window=128).read(flt=build_filter(level="error"), lines=10))
        assert texts(result) == ["message 0", "message 1", "message 2"]
        assert result.more is False

    def test_since_stops_the_walk_early(self):
        node = FakeNode()
        data = "".join(bot_line(i, minute=i // 60) for i in range(3000))
        node.write(BOT, data)
        flt = build_filter(since="2026-10-18T10:49:00")
        result = run(reader(node, window=1024).read(flt=flt, lines=2000))
        assert texts(result)[0] == "message 2940"
        assert len(result.entries) == 60
        assert result.scanned_bytes < len(data) / 10

    def test_until_skips_newer_entries(self):
        node = FakeNode()
        node.write(BOT, "".join(bot_line(i, minute=i) for i in range(10)))
        result = run(reader(node).read(flt=build_filter(until="2026-10-18T10:04:30"), lines=2))
        assert texts(result) == ["message 3", "message 4"]

    def test_partial_last_line_is_held_back(self):
        node = FakeNode()
        node.write(BOT, bot_line(1) + "2026-10-18 10:00:02.000 INFO\tstill writ")
        result = run(reader(node).read())
        assert texts(result) == ["message 1"]
        node.append(BOT, "ing\n")
        follow = run(reader(node).read(cursor=result.cursor))
        assert texts(follow) == ["still writing"]

    def test_scan_budget(self):
        node = FakeNode()
        node.write(BOT, "".join(bot_line(i, "DEBUG") for i in range(5000)))
        result = run(reader(node, window=1000, max_scan=4000).read(flt=build_filter(level="error")))
        assert result.entries == []
        assert result.more is True
        assert result.scanned_bytes <= 4000

    def test_forward_from_start(self):
        node = FakeNode()
        node.write(BOT, "".join(bot_line(i) for i in range(100)))
        result = run(reader(node, window=200).read(tail=False, lines=5))
        assert texts(result) == [f"message {i}" for i in range(5)]
        assert result.more is True
        rest = run(reader(node, window=200).read(cursor=result.cursor, lines=1000))
        assert texts(rest) == [f"message {i}" for i in range(5, 100)]
        assert rest.more is False


# ============================================================================= cursor and rotation

class TestCursor:
    def test_follow_returns_only_new_entries(self):
        node = FakeNode()
        node.write(BOT, "".join(bot_line(i) for i in range(10)))
        first = run(reader(node).read(lines=3))
        assert run(reader(node).read(cursor=first.cursor)).entries == []
        node.append(BOT, bot_line(10) + bot_line(11))
        second = run(reader(node).read(cursor=first.cursor))
        assert texts(second) == ["message 10", "message 11"]
        assert second.rotated is False
        assert run(reader(node).read(cursor=second.cursor)).entries == []

    def test_follow_with_a_filter_keeps_continuation_lines_of_kept_entries(self):
        node = FakeNode()
        node.write(BOT, bot_line(1, "ERROR") + "first line of traceback\n")
        first = run(reader(node).read(flt=build_filter(level="error")))
        node.append(BOT, "second line of traceback\n" + bot_line(2, "INFO"))
        second = run(reader(node).read(flt=build_filter(level="error"), cursor=first.cursor))
        assert texts(second) == ["second line of traceback"]

    def test_rotation_by_rename(self):
        node = FakeNode()
        node.write(BOT, "".join(bot_line(i) for i in range(5)))
        first = run(reader(node).read())
        node.append(BOT, bot_line(5))
        node.rename(BOT, BOT + ".1")
        node.write(BOT, bot_line(100))
        result = run(reader(node).read(cursor=first.cursor))
        assert texts(result) == ["message 5", "message 100"]
        assert (result.rotated, result.gap, result.new_file_at) == (True, False, 1)
        node.append(BOT, bot_line(101))
        assert texts(run(reader(node).read(cursor=result.cursor))) == ["message 101"]

    def test_rotation_with_tunneling_and_a_bigger_new_file(self):
        """dcs.log renamed to dcs.log.old, a new dcs.log that keeps the old identity and is already longer."""
        node = FakeNode()
        node.write(DCS, "".join(dcs_line(i) for i in range(3)), identity=42)
        first = run(reader(node, DCS_SOURCE).read())
        node.append(DCS, dcs_line(3))
        node.rename(DCS, "home/Logs/dcs.log.old")
        node.write(DCS, "=== Log opened UTC 2026-10-18 11:00:00\n" + "".join(dcs_line(i + 50) for i in range(10)),
                   identity=42)
        result = run(reader(node, DCS_SOURCE).read(cursor=first.cursor, lines=100))
        assert texts(result)[0] == "APP (Main): event 3"
        assert texts(result)[1] == "=== Log opened UTC 2026-10-18 11:00:00"
        assert len(result.entries) == 12
        assert (result.rotated, result.gap) == (True, False)

    def test_bot_copy_at_shutdown_then_dcs_rename(self):
        node = FakeNode()
        node.write(DCS, dcs_line(1))
        first = run(reader(node, DCS_SOURCE).read())
        node.append(DCS, dcs_line(2))
        node.write("home/Logs/dcs-20261018-100500.log", bytes(node.files[DCS][0]))   # the bot's copy
        node.append(DCS, dcs_line(3))                                                 # written after the copy
        node.rename(DCS, "home/Logs/dcs.log.old")
        node.write(DCS, dcs_line(100))
        node.files["home/Logs/dcs-20261018-100500.log"][2] = 999.0                   # listed first
        result = run(reader(node, DCS_SOURCE).read(cursor=first.cursor))
        assert texts(result) == ["APP (Main): event 2", "APP (Main): event 3", "APP (Main): event 100"]

    def test_rotation_without_predecessor_is_a_gap(self):
        node = FakeNode()
        node.write(BOT, bot_line(1))
        first = run(reader(node).read())
        node.write(BOT, bot_line(2))
        result = run(reader(node).read(cursor=first.cursor))
        assert texts(result) == ["message 2"]
        assert (result.rotated, result.gap, result.new_file_at) == (True, True, 0)

    def test_truncated_file_with_same_start_is_a_rotation(self):
        node = FakeNode()
        node.write(BOT, "".join(bot_line(i) for i in range(10)))
        first = run(reader(node).read())
        node.files[BOT][0] = bytearray(bot_line(0).encode())
        result = run(reader(node).read(cursor=first.cursor))
        assert result.rotated is True

    def test_limit_on_the_old_file_keeps_the_cursor_there(self):
        node = FakeNode()
        node.write(BOT, bot_line(0))
        first = run(reader(node).read())
        node.append(BOT, "".join(bot_line(i) for i in range(1, 6)))
        node.rename(BOT, BOT + ".1")
        node.write(BOT, bot_line(100))
        part = run(reader(node).read(cursor=first.cursor, lines=3))
        assert texts(part) == ["message 1", "message 2", "message 3"]
        assert (part.more, part.new_file_at) == (True, None)
        rest = run(reader(node).read(cursor=part.cursor, lines=10))
        assert texts(rest) == ["message 4", "message 5", "message 100"]
        assert rest.new_file_at == 2

    def test_file_replaced_during_the_read(self):
        node = FakeNode()
        node.write(BOT, "".join(bot_line(i) for i in range(50)))
        first = run(reader(node).read(lines=1))
        node.append(BOT, "".join(bot_line(i) for i in range(50, 100)))
        original = node.read_file_window
        calls = []

        async def flaky(path, **kwargs):
            data, size, identity = await original(path, **kwargs)
            calls.append(1)
            return data, size, identity if len(calls) < 3 else "replaced"

        node.read_file_window = flaky
        result = run(LogReader(BOT_SOURCE, flaky, node.list_files, window=300).read(cursor=first.cursor))
        assert result.more is True
        node.read_file_window = original
        rest = run(reader(node).read(cursor=result.cursor, lines=1000))
        assert texts(result) + texts(rest) == [f"message {i}" for i in range(50, 100)]

    def test_bad_cursors(self):
        node = FakeNode()
        node.write(BOT, bot_line(1))
        node.write(DCS, dcs_line(1))
        for token in ("garbage", "", "eyJ2IjoyfQ"):
            with pytest.raises(LogRequestError):
                Cursor.decode(token)
        dcs = run(reader(node, DCS_SOURCE).read())
        with pytest.raises(LogRequestError):
            run(reader(node).read(cursor=dcs.cursor))
        forged = Cursor(kind="bot", key="node1", offset=-5, head_length=0, head_hash="0" * 16).encode()
        with pytest.raises(LogRequestError):
            run(reader(node).read(cursor=forged))


# ============================================================================= files

class TestFiles:
    def test_listing_and_selection(self):
        node = FakeNode()
        node.write(BOT + ".1", bot_line(1))
        node.write(BOT, bot_line(2))
        node.write("logs/other.log", bot_line(3))
        files = run(reader(node).list_files())
        assert [(f.name, f.live) for f in files] == [("dcssb-node1.log", True), ("dcssb-node1.log.1", False)]
        assert texts(run(reader(node).read("dcssb-node1.log.1"))) == ["message 1"]

    def test_names_outside_the_listing_are_refused(self):
        node = FakeNode()
        node.write(BOT, bot_line(1))
        node.write("logs/other.log", bot_line(3))
        for name in ("other.log", "../main.yaml", "logs/dcssb-node1.log", "C:\\Windows\\win.ini"):
            with pytest.raises(LogNotFound):
                run(reader(node).read(name))

    def test_no_live_file_yet(self):
        with pytest.raises(LogNotFound):
            run(reader(FakeNode()).read())
