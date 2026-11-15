"""Every watchdog observer must be stopped from threading's exit hooks, never from atexit.

``threading._shutdown()`` joins all NON-daemon threads *before* the interpreter runs its
``atexit`` handlers, and watchdog's observer, emitter and Windows reader threads are non-daemon.
A stopper registered with ``atexit`` is therefore reached only after the join that is waiting for
it: the process hangs at exit with the shutdown already complete — the last startup log line is
printed, the window never closes, and a launcher that waits for the exit code never restarts the
bot.

The pins below prove both halves: the interpreter-level ordering, in a subprocess because the
hang IS the point, and that the three observer owners register through the threading hook.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: A non-daemon thread the interpreter has to join at exit, a stopper for it, and the hook the
#: stopper is registered with. ``threading._register_atexit`` runs inside ``threading._shutdown``
#: before the join; ``atexit`` runs after it.
HARNESS = """
import atexit, threading

stop = threading.Event()
ran = threading.Event()

def watch():
    while not stop.is_set():
        ran.wait(0.05)
    ran.set()

def stopper():
    stop.set()

threading.Thread(target=watch, name="watcher", daemon=False).start()
{register}(stopper)
print("bye", flush=True)
"""

WATCHDOG_OWNERS = (
    "core/data/impl/serverimpl.py",
    "extensions/srs/extension.py",
    "extensions/lotatc/extension.py",
)


def _run(register: str, timeout: float) -> subprocess.CompletedProcess | None:
    """Run the harness with *register* as the hook, or None if it did not exit in *timeout*."""
    try:
        return subprocess.run([sys.executable, "-c", HARNESS.format(register=register)],
                              capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def test_threading_exit_hook_stops_the_thread_before_the_join():
    proc = _run("threading._register_atexit", timeout=30)
    assert proc is not None, "the process never exited"
    assert proc.returncode == 0, proc.stderr
    assert "bye" in proc.stdout


def test_plain_atexit_is_never_reached_before_the_join():
    """The control: this is the hang, and it is what the fix must not fall back to."""
    proc = _run("atexit.register", timeout=5)
    assert proc is None, ("a stopper registered with atexit is unreachable: threading._shutdown "
                          "joins the non-daemon thread first")


def _calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [ast.unparse(node) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]


def test_observer_owners_register_through_the_threading_exit_hook():
    for relative in WATCHDOG_OWNERS:
        calls = _calls(REPO / relative)
        assert "threading._register_atexit(self.stop_observer)" in calls, \
            f"{relative} does not stop its observer through threading's exit hook"
        assert "atexit.register(self.stop_observer)" not in calls, \
            f"{relative} still registers its observer stopper with atexit"
