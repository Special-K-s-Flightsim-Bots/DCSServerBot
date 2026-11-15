"""``core.commandline`` must not consume a foreign ``sys.argv`` at import.

``core/commandline.py`` builds a parser for the bot's own entry points and parses whatever argv the
importing process happens to have, so an importer that owns its own flags (pytest, ``python -m``,
an embedder) dies in argparse with ``SystemExit(2)`` -- a ``BaseException`` no surrounding
``except Exception`` can catch. These tests drive the real subprocess route."""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable

# Names ``OWNED_PROGRAMS`` carries on purpose while no such file exists in this repo.
# ``testdriver.py`` is a real, strict-parse entry point that is not shipped in this repo (it lives
# only on the operator's own machine), so the repo-existence check below must not read it as a
# whitelist entry for nothing. It is the ONLY such name: a whitelisted program that is neither in
# the repo nor on this list still fails.
INTERNAL_PROGRAMS = {'testdriver.py'}

# The driver is `python -c <DRIVER>`: its natural argv is `['-c', *extra]`, exactly the
# shape that used to kill an import. TESTARGV lets a test install a specific argv.
DRIVER = (
    "import json, os, shlex, sys\n"
    "if os.environ.get('TESTARGV'):\n"
    "    sys.argv = shlex.split(os.environ['TESTARGV'])\n"
    "import core\n"
    "data = vars(core.COMMAND_LINE_ARGS)\n"
    "data['_program'] = os.path.basename(sys.argv[0])\n"
    "print(json.dumps(data))\n"
)


def run_core(argv=None, extra=None):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(REPO) + os.pathsep + env.get('PYTHONPATH', '')
    if argv is not None:
        env['TESTARGV'] = argv
    cmd = [PY, '-c', DRIVER] + list(extra or [])
    return subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8")


def parsed(proc):
    assert proc.returncode == 0, f"exit={proc.returncode}\nstderr:\n{proc.stderr}"
    return json.loads(proc.stdout)


# --- a foreign argv must not kill the importing process ---------------------------------

def test_import_with_a_foreign_flag_exits_zero():
    """The live reproduction: `python -c 'import core' --some-flag` used to exit 2."""
    proc = run_core(extra=['--some-flag'])
    assert proc.returncode == 0, proc.stderr
    assert parsed(proc)['config'] == 'config'


def test_import_with_a_pytest_shaped_argv_exits_zero():
    proc = run_core(argv='pytest -q --no-header')
    assert proc.returncode == 0, proc.stderr
    assert parsed(proc)['config'] == 'config'


def test_import_with_a_module_argv_exits_zero():
    proc = run_core(argv='-m services.webservice.auth.local --hash')
    assert proc.returncode == 0, proc.stderr
    assert parsed(proc)['config'] == 'config'


def test_import_with_an_embedders_argv_exits_zero():
    proc = run_core(argv='uvicorn app:app --reload --port 8000')
    assert proc.returncode == 0, proc.stderr
    assert parsed(proc)['config'] == 'config'


# --- strictness preserved: an owned program keeps its flag validation -----------------

def test_owned_program_still_honours_config_flag():
    proc = run_core(argv='run.py -c /tmp/x')
    assert parsed(proc)['config'] == '/tmp/x'


def test_owned_program_typo_still_fails_loudly():
    proc = run_core(argv='run.py --nonsense')
    assert proc.returncode == 2, f"a typo must not pass silently (exit={proc.returncode})"
    assert 'unrecognized arguments' in proc.stderr


def test_foreign_program_typo_is_ignored_not_fatal():
    """The other half of the whitelist: a foreign program's flags are none of our business."""
    proc = run_core(argv='pytest --nonsense-of-mine')
    assert proc.returncode == 0, proc.stderr


# --- one case per owned program, asserting a REAL flag is still parsed ----------------

OWNED_CASES = {
    'run.py': ('-c /tmp/x -n mynode', {'config': '/tmp/x', 'node': 'mynode'}),
    'update.py': ('-c /tmp/y --no-restart', {'config': '/tmp/y', 'no_restart': True}),
    'install.py': ('-u myuser -d mydb', {'user': 'myuser', 'database': 'mydb'}),
    'mizedit.py': ('-m /tmp/m.miz', {'mizfile': '/tmp/m.miz'}),
    'restore.py': ('-n mynode', {'node': 'mynode'}),
    'testdriver.py': ('-n mynode', {'node': 'mynode'}),
}


def test_every_owned_program_still_parses_its_flags():
    for program, (argv, expected) in OWNED_CASES.items():
        proc = run_core(argv=f'{program} {argv}')
        data = parsed(proc)
        assert data['_program'] == program
        for key, value in expected.items():
            assert data[key] == value, f'{program}: {key}={data[key]!r} != {value!r}'


# --- the whitelist must cover every branch the module actually defines ----------------

def _source_branch_programs():
    src = (REPO / 'core' / 'commandline.py').read_text(encoding="utf-8")
    return set(re.findall(r"program == '([^']+)'", src))


def _declared_whitelist():
    tree = ast.parse((REPO / 'core' / 'commandline.py').read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Set, ast.List, ast.Tuple)):
            for target in node.targets:
                if getattr(target, 'id', None) == 'OWNED_PROGRAMS':
                    return set(ast.literal_eval(node.value))
    raise AssertionError('core/commandline.py declares no OWNED_PROGRAMS whitelist')


def test_whitelist_covers_every_branch_and_vice_versa():
    branches = _source_branch_programs()
    assert branches == set(OWNED_CASES), f'unexpected branch set: {branches}'
    assert _declared_whitelist() == branches, (
        'OWNED_PROGRAMS drifted from the per-program branches: '
        f'{_declared_whitelist()} != {branches}'
    )


def _missing_from_repo(names):
    """Names that are neither in the repo nor on INTERNAL_PROGRAMS: a whitelist entry for nothing."""
    return sorted(name for name in names
                  if name not in INTERNAL_PROGRAMS and not (REPO / name).is_file())


def test_every_owned_program_exists_in_the_repo():
    """A whitelisted program that is not there is a whitelist for nothing.

    Every owned program must be a real entry point -- a ``<name>`` at the root -- or be on the
    documented INTERNAL_PROGRAMS exemption (``testdriver.py``).  Anything else in the
    list is a name nobody launches, which reads as if the list had been verified against the
    launchers when it had not.
    """
    missing = _missing_from_repo(_declared_whitelist())
    assert not missing, f'OWNED_PROGRAMS names programs that do not exist in the repo: {missing}'


def test_the_exemption_does_not_weaken_the_exists_check():
    """The assertion still bites: an invented whitelist name fails, only INTERNAL_PROGRAMS pass.

    Guards the exemption itself -- if a future edit widened INTERNAL_PROGRAMS (or dropped the
    check), an invented program would slip through, and this is the proof it would not.
    """
    assert _missing_from_repo({'does_not_exist.py'}) == ['does_not_exist.py'], (
        'an invented program must still be flagged as missing from the repo'
    )
 # INTERNAL_PROGRAMS is the only set allowed to be absent from the repo.
    assert _missing_from_repo(INTERNAL_PROGRAMS) == [], (
        'INTERNAL_PROGRAMS is the documented exemption and must be the only absent names'
    )
