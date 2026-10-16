"""Boot wiring for the setup console: WHICH boot may start it, and nothing else.

THE WINDOW IS DECIDED HERE, ONCE, AND FROM THE FILESYSTEM. ``main.yaml`` absent when the process
starts is the whole rule. Nothing re-checks the file later, and nothing can re-open the window: the
factory is reached only through :func:`start_setup_console`, which refuses outright when the file is
there, so a request, a timer or an operator action has no code path to travel.

That refusal is checked TWICE on purpose — once by the launcher (:func:`should_run_setup`) so a
configured install never builds an application it does not serve, and once inside
:func:`start_setup_console` so a caller that skips the launcher is refused rather than trusted. The
second check is what makes "no code path builds the setup app when ``main.yaml`` exists" true of
this module and not merely of today's launcher.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only; the runtime import is inside the function
    from .service import SetupService

__all__ = ["MAIN_CONFIG_FILE", "SetupWindowClosed", "main_config_path", "should_run_setup",
           "web_setup_enabled", "start_setup_console", "run_setup_console"]

#: The file whose presence means "configured". The bot already treats it as that fact
#: (``core/data/node.py`` reads it, and its absence is what raises ``InstallationException``).
MAIN_CONFIG_FILE = "main.yaml"


class SetupWindowClosed(RuntimeError):
    """Raised when the setup console is asked to start on an install that is already configured."""


def main_config_path(config_dir) -> str:
    """The path of the file that closes the window."""
    return os.path.join(str(config_dir or "config"), MAIN_CONFIG_FILE)


def should_run_setup(config_dir) -> bool:
    """Whether THIS process may run the setup console: ``main.yaml`` absent at process start."""
    return not os.path.exists(main_config_path(config_dir))


def _interactive_stdout(stream=None) -> bool:
    """Whether there is a terminal to read a printed code from. A redirected stdout is not one."""
    stream = sys.stdout if stream is None else stream
    try:
        return bool(stream.isatty())
    except Exception:  # pragma: no cover - a stream with no isatty
        return False


def web_setup_enabled(args) -> bool:
    """Whether the setup console may run instead of the command line installer.

    ``--no-web-setup`` forces the installer; ``--setup`` forces the console; with neither, the
    console runs only on an interactive terminal, so a service, a container or a redirected stdout
    takes the installer path it has always taken. Given BOTH flags, ``--no-web-setup`` wins: it is
    the stronger instruction ("never"), and the operator who wants the console can drop it.
    """
    if getattr(args, "no_web_setup", False):
        return False
    if getattr(args, "setup", False):
        return True
    return _interactive_stdout()


async def start_setup_console(*, node: str = "", config_dir, open_browser: bool = True,
                              announce: bool = True, port: int | None = None) -> SetupService:
    """Build and start the setup console for an unconfigured install.

    Refuses BEFORE it mints a code or builds an application when ``main.yaml`` exists: a refusal
    that happened later would have already created the unauthenticated surface it is refusing.

    This module imports nothing but ``os`` at module level. The console's own modules are imported
    HERE, so a configured boot — which only ever calls :func:`should_run_setup` — loads neither
    uvicorn nor the admin shell's identity layer.
    """
    if not should_run_setup(config_dir):
        raise SetupWindowClosed(
            f"the setup console refuses to start: {main_config_path(config_dir)} exists, so this "
            f"installation is configured and the setup window is closed. It is closed by the file "
            f"being present at process start, and no request, timer or option can reopen it.")
    from .service import SETUP_PORT, SetupService
    from .window import SetupWindow

    window = SetupWindow()
    service = SetupService(window, config_dir=config_dir, node=node,
                           port=SETUP_PORT if port is None else port,
                           open_browser=open_browser, announce=announce)
    await service.start()
    return service


async def run_setup_console(*, node: str = "", config_dir, open_browser: bool = True,
                            announce: bool = True, port: int | None = None) -> int:
    """Run the console until setup asks to finish, then return 0.

    The caller decides what to do next; it must NOT assume the install became configured, because
    nothing in this phase writes configuration. ``run.py`` re-checks the file for that reason.
    """
    service = await start_setup_console(node=node, config_dir=config_dir, open_browser=open_browser,
                                        announce=announce, port=port)
    try:
        await service.run()
    finally:
        await service.stop()
    return 0
