"""Boot wiring for the setup console: WHICH boot may start it, and nothing else.

THE WINDOW IS DECIDED HERE, ONCE, AND FROM THE FILESYSTEM. Two states open it, and :func:`setup_mode`
is the one place that names them:

* ``INSTALL`` — ``main.yaml`` absent when the process starts: the first-run tour, exactly as before.
* ``JOIN`` — ``main.yaml`` PRESENT and this node has NO block in ``nodes.yaml``: the bot cannot boot
  (``core/data/impl/nodeimpl.py`` raises ``InstallationException``) and the setup console adds this
  node to the EXISTING ``nodes.yaml`` instead of writing a configuration.

Nothing re-checks those files later, and nothing can re-open the window: the factory is reached only
through :func:`start_setup_console`, which refuses outright when neither state holds, so a request, a
timer or an operator action has no code path to travel.

That refusal is checked TWICE on purpose — once by the launcher (:func:`should_run_setup`) so a
configured install never builds an application it does not serve, and once inside
:func:`start_setup_console` so a caller that skips the launcher is refused rather than trusted. The
second check is what makes "no code path builds the setup app when ``main.yaml`` exists" true of
this module and not merely of today's launcher.

THE JOINING RULE FAILS CLOSED, AND THAT IS ITS MOST IMPORTANT LINE. An unreadable, absent or
malformed ``nodes.yaml`` answers "no joining state" rather than "this node is missing": a YAML typo
must never be read as a licence to open an unauthenticated console on a configured, running install.
The node identity is the launcher's (``--node``, defaulting to ``platform.node()``), never re-derived
here, and it is the SAME key the write uses — so the window closes the moment the block lands.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only; the runtime import is inside the function
    from .service import SetupService

__all__ = ["MAIN_CONFIG_FILE", "NODES_CONFIG_FILE", "INSTALL", "JOIN", "SetupWindowClosed",
           "main_config_path", "nodes_config_path", "setup_mode", "should_run_setup",
           "web_setup_enabled", "start_setup_console", "run_setup_console"]

#: The file whose presence means "configured". The bot already treats it as that fact
#: (``core/data/node.py`` reads it, and its absence is what raises ``InstallationException``).
MAIN_CONFIG_FILE = "main.yaml"

#: The file that names every node of this installation. A configured install always has one
#: (``core/data/impl/nodeimpl.py`` reads it right after ``main.yaml``).
NODES_CONFIG_FILE = "nodes.yaml"

#: ``main.yaml`` absent: the first-run tour writes the installation's configuration.
INSTALL = "install"

#: ``main.yaml`` present and this node has no block in ``nodes.yaml``: the tour adds this node to the
#: existing configuration and writes nothing install-wide.
JOIN = "join"


class SetupWindowClosed(RuntimeError):
    """Raised when the setup console is asked to start on an install that is already configured."""


def main_config_path(config_dir) -> str:
    """The path of the file that closes the window."""
    return os.path.join(str(config_dir or "config"), MAIN_CONFIG_FILE)


def nodes_config_path(config_dir) -> str:
    """The path of the file naming every node of this installation."""
    return os.path.join(str(config_dir or "config"), NODES_CONFIG_FILE)


def setup_mode(config_dir, node: str = "") -> str | None:
    """``INSTALL``, ``JOIN`` or ``None`` (the window is shut) — decided from the filesystem.

    *node* is the launcher's own node name. An empty one means "the caller names no node", and the
    joining check is then inert: a caller that does not know which node is booting cannot tell a
    node missing from ``nodes.yaml`` from a configured install, and guessing would open the console
    on a running installation.

    The ``nodes.yaml`` read is LAZY (``ruamel.yaml`` is imported on the branch that needs it) and it FAILS
    CLOSED: anything but a mapping that carries *node* is ``None``. A file that could not be read is
    never evidence that this node is missing.
    """
    if not os.path.exists(main_config_path(config_dir)):
        return INSTALL
    if not node:
        return None
    from ruamel.yaml import YAML  # noqa: PLC0415 - loaded only on the branch that reads the file
    from ruamel.yaml.error import MarkedYAMLError  # noqa: PLC0415

    try:
        with open(nodes_config_path(config_dir), encoding="utf-8") as handle:
            data = YAML().load(handle)
    except (OSError, MarkedYAMLError):
        return None
    if not isinstance(data, dict) or node in data:
        return None
    return JOIN


def should_run_setup(config_dir, node: str = "") -> bool:
    """Whether THIS process may run the setup console: :func:`setup_mode` is not ``None``."""
    return setup_mode(config_dir, node) is not None


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
    """Build and start the setup console for the state :func:`setup_mode` names.

    Refuses BEFORE it mints a code or builds an application when the window is shut: a refusal that
    happened later would have already created the unauthenticated surface it is refusing.

    This module imports nothing but ``os`` at module level. The console's own modules are imported
    HERE, so a configured boot — which only ever calls :func:`should_run_setup` — loads neither
    uvicorn nor the admin shell's identity layer.
    """
    mode = setup_mode(config_dir, node)
    if mode is None:
        raise SetupWindowClosed(
            f"the setup console refuses to start: {main_config_path(config_dir)} exists, so this "
            f"installation is configured, and {nodes_config_path(config_dir)} carries a block for "
            f"node '{node}' (or could not be read) — so this node is already configured and the "
            f"setup window is closed for it. It is closed by those files as they are at process "
            f"start, and no request, timer or option can reopen it.")
    from .service import SETUP_PORT, SetupService
    from .window import SetupWindow

    window = SetupWindow()
    service = SetupService(window, config_dir=config_dir, node=node, mode=mode,
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
