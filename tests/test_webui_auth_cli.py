"""The documented password-hash CLI must run as documented: ``python -m ...``.

Importing the module executes ``services/webservice/__init__.py`` -> the bot's ``core`` ->
``core/commandline.py``, whose module-level ``parser.parse_args()`` runs on the REAL ``sys.argv``
and rejected ``--hash`` before ``main()`` was reached. The CLI therefore detaches its own argv above
its imports (see ``local.py``).

This file runs the real command in a real subprocess from the repo root with ``sys.executable`` --
so the entry point the operator types is what is verified -- and pins that merely importing the
package leaves the host process's argv alone (the bot does that from ``install_auth``)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from services.webservice.auth import local as local_auth

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = "services.webservice.auth.local"
PASSWORD = "correct horse battery staple"
TIMEOUT = 120


def run_cli(args: list[str], stdin: str) -> subprocess.CompletedProcess:
    """Run the documented entry point exactly as an operator would, from the repo root."""
    return subprocess.run([sys.executable, "-m", MODULE, *args],
                          cwd=REPO_ROOT, input=stdin, capture_output=True, text=True, encoding="utf-8",
                          timeout=TIMEOUT)


def printed_hash(stdout: str) -> str | None:
    """The hash line the CLI prints, or ``None`` when it printed none."""
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith(f"{local_auth.SCHEME}$"):
            return line
    return None


def _diagnosis(proc: subprocess.CompletedProcess) -> str:
    return (f"exit={proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")


# --------------------------------------------------------------------------- the real command

def test_hash_command_runs_and_prints_a_verifiable_hash():
    """(a) ``--hash`` exits 0 and prints a hash the module's own verifier accepts."""
    proc = run_cli(["--hash", "--iterations", "1000"], f"{PASSWORD}\n{PASSWORD}\n")

    assert proc.returncode == 0, _diagnosis(proc)
    encoded = printed_hash(proc.stdout)
    assert encoded is not None, _diagnosis(proc)
    assert local_auth.verify_password(PASSWORD, encoded), "the printed hash does not verify"
    assert not local_auth.verify_password("not the password", encoded), "the hash accepted a wrong password"


def test_hash_command_prints_the_ready_to_paste_yaml_block():
    """(b) ``--username``/``--roles`` also print the YAML block for ``auth.local.users``."""
    proc = run_cli(["--hash", "--iterations", "1000", "--username", "specialk",
                    "--roles", "Admin"], f"{PASSWORD}\n{PASSWORD}\n")

    assert proc.returncode == 0, _diagnosis(proc)
    encoded = printed_hash(proc.stdout)
    assert encoded is not None, _diagnosis(proc)
    assert "users:" in proc.stdout, _diagnosis(proc)
    assert "- username: specialk" in proc.stdout, _diagnosis(proc)
    assert f"password_hash: {encoded}" in proc.stdout, _diagnosis(proc)
    assert "roles: [Admin]" in proc.stdout, _diagnosis(proc)


def test_mismatched_passwords_exit_one_and_print_no_hash():
    """(c) A mismatched pair exits 1, says why, and leaks no hash."""
    proc = run_cli(["--hash", "--iterations", "1000"], "one\nsomething else\n")

    assert proc.returncode == 1, _diagnosis(proc)
    assert "do not match" in proc.stderr, _diagnosis(proc)
    assert printed_hash(proc.stdout) is None, _diagnosis(proc)


# --------------------------------------------------------------------------- the property underneath

#: Runs the documented entry point with the bot's REAL parser entering the CLI's import chain --
#: what the old "importing the package must not reach core" pin stood in for. ``core`` cannot be
#: IMPORTED by this suite's interpreter (no psycopg/discord), so the parser module is exec'd from
#: its own source instead: that is the same code, running its module-level ``parse_args`` at the
#: same moment, on the same ``sys.argv``.
#:
#: The hook fires on the FIRST import the ``__main__`` module makes after the argv detach
#: (``argparse``), i.e. the earliest point ``core`` could possibly enter from inside ``local.py``;
#: it deliberately ignores ``__future__`` (which the language requires ABOVE the detach) and the
#: detach's own ``import sys``.
_CORE_CHAIN_PROBE = """
import builtins
import runpy
import sys
import types

repo = sys.argv[1]
sys.argv = [repo + "/services/webservice/auth/local.py", "--hash", "--iterations", "1000",
            "--username", "specialk", "--roles", "Admin"]
source = open(repo + "/core/commandline.py", encoding="utf-8").read()

real_import = builtins.__import__
state = {"fired": False}


def hook(name, globals=None, locals=None, fromlist=(), level=0):
    if (not state["fired"] and name not in ("sys", "__future__") and globals is not None
            and globals.get("__name__") == "__main__"):
        state["fired"] = True
        module = types.ModuleType("core.commandline")
        exec(compile(source, "<core/commandline.py>", "exec"), module.__dict__)
        print("PROBE-CORE-RAN COMMAND_LINE_ARGS=%r" % (module.COMMAND_LINE_ARGS,),
              file=sys.stderr)
    return real_import(name, globals, locals, fromlist, level)


builtins.__import__ = hook
runpy.run_module("services.webservice.auth.local", run_name="__main__")
"""


def test_the_cli_still_works_once_core_enters_its_import_chain():
    """The documented command must survive the bot's import-time parser joining the chain.

    No flags are passed to a ``main()`` call here: the module runs as ``__main__`` with the process
    argv the operator would have, and the bot's ``core/commandline.py`` is executed at the CLI's
    first import. If the CLI let that parser see ``--hash``, argparse exited with exit code 2 and
    no hash was printed -- which is exactly how the documented command was broken before.
    """
    proc = subprocess.run([sys.executable, "-c", _CORE_CHAIN_PROBE, str(REPO_ROOT)],
                          cwd=REPO_ROOT, input=f"{PASSWORD}\n{PASSWORD}\n", capture_output=True,
                          text=True, encoding="utf-8", timeout=TIMEOUT)

    assert proc.returncode == 0, _diagnosis(proc)
 # anti-vacuity: the bot's parser really ran to completion (it would have exited 2 on an
 # un-detached argv and printed no marker at all, so the marker is the evidence the CLI's flags
 # were gone from sys.argv by then).
    assert "PROBE-CORE-RAN COMMAND_LINE_ARGS=Namespace(config=" in proc.stderr, _diagnosis(proc)
    encoded = printed_hash(proc.stdout)
    assert encoded is not None, _diagnosis(proc)
    assert local_auth.verify_password(PASSWORD, encoded), "the printed hash does not verify"


def test_importing_the_package_leaves_the_host_processes_argv_alone():
    """The detach is guarded by ``__main__``: merely importing must not truncate the host's argv.

    The bot imports this module from ``install_auth``, inside ``run.py``, whose OWN flags live in
    ``sys.argv``. An unguarded detach would eat them -- the mirror image of the bug it fixes.
    """
    probe = ("import sys; before = list(sys.argv); import services.webservice; "
             "import services.webservice.auth.local; "
             "assert sys.argv == before, "
             "'importing the package truncated sys.argv: %r -> %r' % (before, sys.argv); "
             "print('ARGV', sys.argv)")
    proc = subprocess.run([sys.executable, "-c", probe, "--hash", "--username", "specialk"],
                          cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT)

    assert proc.returncode == 0, _diagnosis(proc)
    assert "--hash" in proc.stdout and "--username" in proc.stdout, _diagnosis(proc)

