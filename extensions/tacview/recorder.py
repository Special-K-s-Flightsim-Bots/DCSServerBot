#!/usr/bin/env python3
"""
Tacview RTT manual recorder (handshake‑safe) – asyncio version
- Discards the host handshake so recordings start with a valid ACMI header.
- Waits until 'FileType=text/acmi/tacview' is seen before buffering/recording.
- Start/stop interactive control. Optional password (CRC‑64/ECMA of UTF‑16LE).

Usage:
  python recorder.py <host> <port> [--password "pw"] [--out "clip_{ts}.acmi"]

Commands at prompt: start | stop | toggle | name <basename> | quit
"""

import argparse
import asyncio
import logging
import os

from io import TextIOWrapper
from pathlib import Path

# --------------------------------------------------------------------------- #
# CRC‑64 / ECMA helpers (exactly the same as in the original script)

CRC64_POLY = 0x42F0E1EBA9EA3693
CRC64_INIT = 0xFFFFFFFFFFFFFFFF
CRC64_XOROUT = 0xFFFFFFFFFFFFFFFF

logger = logging.getLogger(__name__)


def crc64_ecma(data: bytes) -> int:
    crc = CRC64_INIT
    for b in data:
        crc ^= (b << 56) & 0xFFFFFFFFFFFFFFFF
        for _ in range(8):
            if crc & (1 << 63):
                crc = ((crc << 1) ^ CRC64_POLY) & 0xFFFFFFFFFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFFFFFFFFFF
    return crc ^ CRC64_XOROUT


def tacview_password_hash(pw: str) -> str:
    """Return the 16‑digit hex representation expected by Tacview."""
    return f"{crc64_ecma(pw.encode('utf-16le')):016X}"


# --------------------------------------------------------------------------- #
# Async recorder

class TacviewRecorder:

    def __init__(
        self,
        host: str,
        port: int,
        out_pattern: str = "recording_{ts}.acmi",
        client_name: str = "ExternalRecorder",
        password: str | None = None,
        connect_timeout: float = 10.0,
        buffer_bytes: int = 8 * 1024 * 1024,
    ):
        self.host = host
        self.port = port
        self.out_pattern = out_pattern
        self.client_name = client_name
        self.password = password
        self.connect_timeout = connect_timeout
        self.buffer_bytes = buffer_bytes

        # These will be created in connect()
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

        self._stop_evt = asyncio.Event()
        self._reader_task: asyncio.Task | None = None

        self._buf = bytearray()
        self._buf_ready = False  # true once ACMI header has been seen

        self._lock = asyncio.Lock()

        self._recording = False
        self._f: TextIOWrapper | None = None

        self.log = logger

    # --------------------------------------------------------------------- #
    # Connection handling

    async def connect(self) -> None:
        """Establish TCP connection and perform the Tacview handshake."""
        # 1) Connect
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.connect_timeout
            )
        except Exception as exc:
            raise RuntimeError(f"Could not connect to {self.host}:{self.port}") from exc

        # 2) Read & discard host handshake until terminal '\0'
        host_handshake = await self._read_until_terminator(b"\x00", max_bytes=4096)
        if host_handshake is None:
            raise RuntimeError("Did not receive host handshake.")

        # 3) Send client handshake
        pw_hash = tacview_password_hash(self.password) if self.password else "0"
        client_hs = (
            "XtraLib.Stream.0\n"
            "Tacview.RealTimeTelemetry.0\n"
            f"{self.client_name}\n"
            f"{pw_hash}\0"
        ).encode("utf-8")
        self.writer.write(client_hs)
        await self.writer.drain()

        # 4) Start the reader task
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _read_until_terminator(
        self, term: bytes, max_bytes: int = 65536
    ) -> bytes | None:
        """Read from the socket until the given terminator is found."""
        data = bytearray()
        while len(data) < max_bytes:
            chunk = await self.reader.read(1024)
            if not chunk:
                break
            data += chunk
            idx = data.find(term)
            if idx != -1:
                remainder = data[idx + len(term) :]
                if remainder:
                    # Anything after the terminator may already be ACMI data
                    await self._ingest(remainder)
                return bytes(data[: idx + len(term)])
        return None

    # --------------------------------------------------------------------- #
    # Reader task – runs until the recorder is closed

    async def _reader_loop(self) -> None:
        try:
            while not self._stop_evt.is_set():
                try:
                    chunk = await self.reader.read(65536)
                except asyncio.IncompleteReadError:
                    # Remote closed connection
                    self.log.debug("Tacview connection closed by remote.")
                    break
                if not chunk:
                    self.log.debug("Tacview connection closed by remote.")
                    break
                await self._ingest(chunk)
        finally:
            await self._close_file()

    # --------------------------------------------------------------------- #
    # Data ingestion (buffering / writing)

    async def _ingest(self, data: bytes) -> None:
        async with self._lock:
            if not self._buf_ready:
                # Looking for the ACMI header
                self._buf.extend(data)
                text = self._buf.decode("utf-8", errors="ignore")
                header_pos = text.find("FileType=text/acmi/tacview\n")
                if header_pos != -1:
                    # Drop everything before the header
                    self._buf = bytearray(text[header_pos:].encode("utf-8"))
                    self._buf_ready = True
                else:
                    # Keep at most 8 KiB while searching
                    if len(self._buf) > 8192:
                        self._buf = self._buf[-8192:]
                return

            # From here on we have a valid ACMI header at the start of _buf
            self._buf.extend(data)
            if 0 < self.buffer_bytes < len(self._buf):
                # Keep the most recent part only
                drop = len(self._buf) - self.buffer_bytes
                del self._buf[:drop]

            if self._recording and self._f:
                try:
                    self._f.write(data)
                except Exception as e:
                    self.log.error(f"Write error: {e}")
                    self._recording = False
                    await self._close_file()

    # --------------------------------------------------------------------- #
    # Record control

    async def start(self) -> bool:
        while not self._buf_ready:
            await asyncio.sleep(0.1)

        async with self._lock:
            if self._recording:
                self.log.debug("Already recording.")
                return False

            p = Path(self.out_pattern).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)

            self._f = open(p, "wb")
            # Write the backlog that starts exactly at the header
            if self._buf:
                self._f.write(self._buf)
                self._f.flush()
                os.fsync(self._f.fileno())
            self._recording = True
            self.log.debug(f"Recording {p}")
            return True

    async def stop(self) -> None:
        async with self._lock:
            if not self._recording:
                self.log.debug("Not recording.")
                return
            self._recording = False
            await self.close()
            self.log.debug("Recording stopped.")

    async def _close_file(self) -> None:
        if self._f:
            try:
                self._f.flush()
                os.fsync(self._f.fileno())
            except Exception:
                pass
            try:
                self._f.close()
            except Exception:
                pass
            self._f = None

    def set_name(self, name: str) -> None:
        # Name change can be called from the command loop – no lock needed
        self.out_pattern = name
        self.log.debug(f"Next file name: {name}")

    # --------------------------------------------------------------------- #
    # Cleanup

    async def close(self) -> None:
        self._stop_evt.set()
        if self._reader_task:
            self._reader_task.cancel()
            try:
                self.reader.feed_eof()
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        await self._close_file()


# --------------------------------------------------------------------------- #
# Async command loop

async def command_loop(rec: TacviewRecorder) -> None:
    """Run the interactive prompt in a background task."""
    while True:
        # Use a separate thread for the blocking input()
        try:
            line = await asyncio.to_thread(input, "> ")
        except EOFError:
            line = "quit"

        line = line.strip()
        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()

        if cmd == "start":
            await rec.start()
        elif cmd == "stop":
            await rec.stop()
        elif cmd == "toggle":
            async with rec._lock:
                if rec._recording:
                    await rec.stop()
                else:
                    await rec.start()
        elif cmd == "name":
            if len(parts) < 2:
                logger.info("Usage: name <basename>")
            else:
                rec.set_name(" ".join(parts[1:]))
        elif cmd in ("quit", "exit"):
            await rec.stop()
            logger.info("[i] Bye.")
            break
        else:
            logger.warning("Unknown command.")


# --------------------------------------------------------------------------- #
# Main entry point

async def main() -> None:
    ap = argparse.ArgumentParser(description="Tacview RTT recorder (valid ACMI output)")
    ap.add_argument("host", default="127.0.0.1", help="Tacview host")
    ap.add_argument("port", type=int, default=42674, help="Tacview realtime port")
    ap.add_argument("--password", default=None)
    ap.add_argument(
        "--out",
        default="recording_{ts}.acmi",
        dest="out_pattern",
        help="Filename pattern; placeholders: {ts}, {base}",
    )
    ap.add_argument("--client-name", default="ExternalRecorder")
    ap.add_argument(
        "--buffer-mb",
        type=int,
        default=16,
        help="Backlog to keep before 'start' (MiB)",
    )
    args = ap.parse_args()

    rec = TacviewRecorder(
        args.host,
        args.port,
        out_pattern=args.out_pattern,
        client_name=args.client_name,
        password=args.password,
        buffer_bytes=max(1, args.buffer_mb) * 1024 * 1024,
    )

    logger.info(f"[i] Connecting to {args.host}:{args.port} …")
    await rec.connect()
    logger.info("Commands: start | stop | toggle | name <basename> | quit")

    await command_loop(rec)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
