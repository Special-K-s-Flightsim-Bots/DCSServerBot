"""``SetupService`` — the standalone uvicorn front end an unconfigured install serves.

The service is deliberately NOT a :class:`~services.webservice.service.WebService`: that one is a
registered service which needs the database, the node and the service bus, and it does not exist
before an installation is configured. This one owns its own application
(:func:`~services.webservice.setup.app.create_setup_app`), binds the loopback interface only, and
runs until the tour asks it to stop.

WHAT IT PRINTS, AND WHAT IT NEVER WRITES DOWN. The one-time code and the URL that carries it are
printed to stdout — once, at start-up, before the browser is opened. They are NOT logged: the bot's
root logger writes to ``logs/dcssb-<node>.log``, and a code in a rotated file log is a code on
disk, which is exactly what an in-memory-only code is meant to avoid. For the same reason the
uvicorn access log stays off (``log_level=WARNING``, no access log), so a request's query string
cannot deposit the code either.

An eventual remote mode (not part of this phase) would bind an interface the launcher chooses
explicitly, print the bind address rather than the loopback one, and pass TLS material to uvicorn —
see the note in :mod:`services.webservice.setup.app`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import webbrowser

from contextlib import suppress

import uvicorn

from .app import SETUP_LISTEN, SETUP_PATH, create_setup_app, require_loopback_bind
from .boot import INSTALL
from .window import SetupWindow

__all__ = ["SETUP_PORT", "SetupService", "browses_locally"]

log = logging.getLogger(__name__)

#: The port this console listens on. The same stable port the admin console uses, so the documented
#: URL keeps working once setup is done.
SETUP_PORT = 9876


def browses_locally() -> bool:
    """Whether launching a browser on THIS machine is likely to show the operator anything.

    False for a service, a container or any run with a redirected stdout (no terminal to be sitting
    at) and for a Linux session with no display — where ``webbrowser.open`` would either do nothing
    or fail. In every one of those cases the printed URL is the whole interface.
    """
    if sys.platform.startswith("linux"):
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # pragma: no cover - a stdout object with no isatty
        return False


class SetupService:
    """The setup console: its window (the code), its application, and its uvicorn server."""

    def __init__(self, window: SetupWindow, *, config_dir, node: str = "",
                 listen: str = SETUP_LISTEN, port: int = SETUP_PORT, mode: str = INSTALL,
                 open_browser: bool = True, announce: bool = True,
                 ready_timeout: float = 10.0) -> None:
        self.window = window
        self.node = node
        #: which state this console serves (``boot.INSTALL`` / ``boot.JOIN``) — the rail, the steps
        #: and the write plan all read it from the application
        self.mode = mode
        # A HARD refusal before anything is built: see app.require_loopback_bind.
        self.listen = require_loopback_bind(listen)
        self.port = int(port)
        self.config_dir = str(config_dir)
        self.app = create_setup_app(window, config_dir=self.config_dir, listen=self.listen,
                                    node=node, mode=mode)
        # The port the console serves on, and the way to end it: both are what the Review step needs
        # to hand over (the written console config uses this port; finishing closes the window and
        # lets the launcher continue into the normal boot).
        self.app.state.webui_setup_port = self.port
        self.app.state.setup_finish = self.finish
        self.config = uvicorn.Config(
            app=self.app,
            host=self.listen,
            port=self.port,
            workers=1,
            log_level=logging.WARNING,
            log_config=None,
            use_colors=False,
            lifespan="off",
        )
        self.server = uvicorn.Server(config=self.config)
        self._open_browser = bool(open_browser)
        self._announce = bool(announce)
        self._ready_timeout = float(ready_timeout)
        self._finished = asyncio.Event()
        self._task = None

    # ------------------------------------------------------------------------------- the surface

    @property
    def url(self) -> str:
        """The one URL that enters the tour: the code is IN it, so nothing has to be typed."""
        return f"http://{self.listen}:{self.port}{SETUP_PATH}?code={self.window.display_code}"

    @property
    def bound(self) -> bool:
        """Whether the server has actually bound its port and is answering."""
        return bool(getattr(self.server, "started", False))

    def finish(self) -> None:
        """Signal that setup is complete. The console stops and the launcher continues its boot."""
        self._finished.set()

    async def wait_closed(self) -> None:
        """Wait until the tour finishes OR the server stops on its own.

        The server stops on its own for two reasons that matter — a signal (uvicorn installs the
        SIGINT/SIGTERM handlers and exits gracefully, so no ``KeyboardInterrupt`` ever reaches the
        launcher) and a bind failure. Waiting only on the finish event would leave the process alive
        with nothing serving it, which is the failure this method exists to prevent.
        """
        finished = asyncio.ensure_future(self._finished.wait())
        waiters: list = [finished]
        if self._task is not None:
            waiters.append(self._task)
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            finished.cancel()
            with suppress(asyncio.CancelledError):
                await finished

    # -------------------------------------------------------------------------------- lifecycle

    async def start(self) -> bool:
        """Start serving and announce the URL. Returns whether the port was confirmed bound.

        Idempotent: a second call does not start a second server and does not print the code again —
        a code shown twice is not shown once, and the second print would be a second chance for it to
        be copied somewhere.
        """
        first_call = self._task is None
        if first_call:
            self._task = asyncio.create_task(self._serve())
        ready = await self._await_ready()
        if first_call:
            if self._announce:
                self.announce()
            if ready and self._open_browser:
                self._open()
        return ready

    async def _serve(self) -> None:
        """Run uvicorn, converting its exit-by-exception into a log line.

        uvicorn answers a bind failure (a taken port, most often) with ``sys.exit``, and asyncio
        RE-RAISES ``SystemExit``/``KeyboardInterrupt`` out of a task into the event loop — killing
        the whole process rather than just this server, which would take the launcher's own error
        reporting with it. Catching it HERE, inside the coroutine, is what keeps the process alive
        to say what happened and exit on its own terms.
        """
        try:
            await self.server.serve()
        except (SystemExit, KeyboardInterrupt) as ex:
            log.error("Setup console: the server could not serve %s:%s (%s). The port is probably "
                      "in use; the setup console is not available.", self.listen, self.port, ex)
        except asyncio.CancelledError:
            raise
        except Exception as ex:  # noqa: BLE001 - a crashing server must not take the launcher down
            log.exception("Setup console: the server crashed: %s", ex)

    async def _await_ready(self) -> bool:
        """Wait for uvicorn to report the port bound, or for the server to die trying.

        Polled rather than hooked: the server object exposes no completion callback, and a fixed
        readiness check is one fewer moving part than a signal whose timing would have to be proven.
        The browser is opened only after this returns true, so it cannot reach a port nothing
        is listening on yet.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._ready_timeout
        task = self._task
        while not self.bound:
            if task is None or task.done():
                # The server stopped before it bound: the port is taken, most often. ``_serve``
                # already turned uvicorn's ``sys.exit`` into a log line; this is the second look,
                # so a failure that somehow still carries an exception is reported rather than
                # raised out of the boot path.
                if task is not None:
                    try:
                        task.result()
                    except BaseException as ex:  # noqa: BLE001 - a bind failure is not fatal here
                        log.error("Setup console: the server stopped before it bound %s:%s (%s).",
                                  self.listen, self.port, ex)
                return False
            if loop.time() >= deadline:
                log.warning("Setup console: the server did not report its port bound within %ss; "
                            "the URL above is printed anyway.", self._ready_timeout)
                return False
            await asyncio.sleep(0.05)
        return True

    def announce(self) -> None:
        """Print the entry URL and the bare code. STDOUT ONLY — never the logger (see the module
        docstring: the bot's root logger also writes a file).

        EVERY line is flushed explicitly. ``print`` block-buffers when stdout is not a terminal — a
        pipe (systemd, docker logs, ``nohup``, ``> file``) — so an unflushed announcement would sit
        in the process buffer for the whole time the console is serving, hiding the code and leaving
        the operator no way in. ``browses_locally`` suppresses the browser for exactly those runs, so
        this print IS the interface there and must land immediately.
        """
        print("", flush=True)
        print("DCSServerBot is not configured yet - starting the setup console.", flush=True)
        if self.node:
            print(f"  Node: {self.node}", flush=True)
        print("  Setup is available ONLY on this machine, at:", flush=True)
        print(f"      {self.url}", flush=True)
        print(f"  Setup code (single use): {self.window.display_code}", flush=True)
        print(f"  It expires {int(self.window.ttl // 60)} minutes after this console started.",
              flush=True)
        print("", flush=True)

    def _open(self) -> None:
        """Open the entry URL in a local browser, falling back to the printed URL."""
        if not browses_locally():
            log.info("Setup console: no local browser to open (no terminal or no display); "
                     "use the printed URL.")
            return
        try:
            if webbrowser.open(self.url):
                log.info("Setup console: opened the setup URL in the default browser.")
        except Exception as ex:  # noqa: BLE001 - a browser that will not open must not stop setup
            log.warning("Setup console: could not open a browser (%s); use the printed URL.",
                        type(ex).__name__)

    async def stop(self, *, timeout: float = 5.0) -> None:
        """Stop serving. Safe to call when the server was never started."""
        task, self._task = self._task, None
        if task is None:
            return
        self.server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("Setup console: the server did not stop within %ss, cancelling it.", timeout)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        except asyncio.CancelledError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise
        except BaseException as ex:  # noqa: BLE001 - the server already failed and said why
            # A server that died before it bound (a taken port, most often) leaves a failed task
            # behind. Stopping it is still the caller's answer, and the failure itself was reported
            # where it happened (:meth:`_await_ready`), so it is not raised a second time here.
            log.debug("Setup console: the server task had already ended with %s.", type(ex).__name__)

    async def run(self) -> None:
        """Serve until :meth:`finish` is called or the server stops, then stop."""
        await self.start()
        try:
            await self.wait_closed()
        finally:
            await self.stop()
