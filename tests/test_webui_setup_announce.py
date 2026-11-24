"""The announcement must reach a PIPED stdout while the console is still serving.

``announce()`` prints the entry URL and the one-time code — on a run with no terminal (systemd, a
container, ``nohup``, ``> file``) that print is the WHOLE interface, because ``browses_locally``
suppresses the browser there. Python block-buffers a non-TTY stdout, so an unflushed print would sit
in the process buffer for as long as the console serves: the operator would see nothing and could not
log in. These tests run the real ``announce()`` in a CHILD PROCESS whose stdout is a pipe, and assert
the code and URL are readable from that pipe BEFORE the child exits.

WHAT THEY DO NOT COVER: nothing here proves the announce is on stdout and never in the log (that is
``test_webui_setup_service``/the module's own reasoning), and nothing here proves a terminal run
shows the same text — only the piped, buffering case that the fix targets.
"""
from __future__ import annotations

import os
import subprocess
import threading
import sys
import time

import pytest

from conftest import REPO_ROOT

#: The code the child mints, and the dash-grouped form the operator is shown.
CODE = "ABCD2345"
DISPLAY_CODE = "ABCD-2345"
#: The entry URL the announce prints (the code is IN it, so the whole line matters).
URL_FRAGMENT = "http://127.0.0.1:9876/setup?code="

_CHILD = '''\
"""Build the real SetupService, announce, then stay alive (see the test module docstring)."""
import sys
import time

from services.webservice.setup.service import SetupService
from services.webservice.setup.window import SetupWindow

service = SetupService(SetupWindow(code={code!r}), config_dir=sys.argv[1], node="TestNode")
service.announce()
time.sleep(60)
'''


def _read_until(proc: subprocess.Popen, needle: str, timeout: float) -> tuple[str, str]:
    """Read the child's piped stdout until *needle* arrives or *timeout* elapses.

    Returns what was read (so a failure can show it) and the child's stderr. The pipe is drained on
    its own thread, which works on every platform: ``os.set_blocking`` is POSIX-only, so a
    non-blocking read is not available on Windows. A child that died early (an import error, most
    likely) ends the wait instead of hanging until the deadline.
    """
    assert proc.stdout is not None  # Popen was given PIPE, so this is always set
    collected: list[bytes] = []

    def drain():
        for line in iter(proc.stdout.readline, b""):
            collected.append(line)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle.encode("utf-8") in b"".join(collected):
            break
        if proc.poll() is not None and not reader.is_alive():
            break
        time.sleep(0.02)
    proc.kill()
    reader.join(timeout=10)
    proc.wait(timeout=10)
    err = proc.stderr.read() if proc.stderr is not None else b""
    return b"".join(collected).decode("utf-8", "replace"), err.decode("utf-8", "replace")


@pytest.fixture
def running_console(tmp_path):
    """A whole child process announcing onto a pipe, still running when the test reads it."""
    script = tmp_path / "announce_child.py"
    script.write_text(_CHILD.format(code=CODE), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.Popen([sys.executable, str(script), str(tmp_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=str(REPO_ROOT), env=env)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)


def test_the_code_reaches_a_piped_stdout_while_the_console_is_serving(running_console):
    seen, err = _read_until(running_console, DISPLAY_CODE, timeout=15.0)
    assert DISPLAY_CODE in seen, (
        f"the one-time code must be readable from a piped stdout BEFORE the process exits; "
        f"read {len(seen)} byte(s) while the console was up.\n--- stdout ---\n{seen}\n"
        f"--- stderr ---\n{err}")


def test_the_entry_url_reaches_a_piped_stdout_while_the_console_is_serving(running_console):
    seen, err = _read_until(running_console, URL_FRAGMENT, timeout=15.0)
    assert URL_FRAGMENT in seen, (
        f"the entry URL must be readable from a piped stdout BEFORE the process exits; "
        f"read {len(seen)} byte(s) while the console was up.\n--- stdout ---\n{seen}\n"
        f"--- stderr ---\n{err}")
