"""Pins the uv-based dependency install / update path.

The Python entry points are exercised for real (``update.install_requirements`` and run.py's
missing-module fallback), because they can run on this host. The launchers and the shell
twins cannot be executed here, so they are pinned textually - see the report for what that
does and does not prove.
"""
from __future__ import annotations

import builtins
import importlib
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

LAUNCHERS = [
    'run.cmd', 'update.cmd', 'install.cmd', 'mizedit.cmd',
    'run.sh', 'update.sh', 'install.sh', 'mizedit.sh',
]

#: The uv binary and the environment directory, as the two launcher flavours spell them.
UVEXE = {'.cmd': '"%UVEXE%"', '.sh': '"$UVEXE"'}
VENV = {'.cmd': '"%VENV%"', '.sh': '"$VENV"'}


def _venv_create_line(text: str, uvexe: str, venv: str) -> str | None:
    """The line that creates the environment: it runs ``venv`` through ``uvexe`` and names ``venv``.

    The words between them are the launchers' own business (``--python``, ``--no-python-downloads``),
    so the tokens are read rather than one contiguous substring: the uv binary must come first, then
    the subcommand, then the directory. Returns ``None`` when no line carries all three in order.
    """
    for line in text.splitlines():
        tokens = line.split()
        if all(token in tokens for token in (uvexe, 'venv', venv)) \
                and tokens.index(uvexe) < tokens.index('venv') < tokens.index(venv):
            return line
    return None



def _capture_run(monkeypatch, recorder):
    def fake_run(cmd, *args, **kwargs):
        recorder.append(list(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=0)
    monkeypatch.setattr(subprocess, 'run', fake_run)


@pytest.fixture
def update_mod():
    return importlib.import_module('update')


def test_install_requirements_uses_uv(monkeypatch, update_mod):
    calls = []
    _capture_run(monkeypatch, calls)
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/uv')
    monkeypatch.setattr(os.path, 'exists', lambda p: False)

    rc = update_mod.install_requirements()

    assert rc.returncode == 0
    assert calls == [['uv', 'pip', 'sync', '--python', sys.executable, 'requirements.txt']]


def test_install_requirements_appends_local_file(monkeypatch, update_mod):
    calls = []
    _capture_run(monkeypatch, calls)
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/uv')
    monkeypatch.setattr(os.path, 'exists', lambda p: p == 'requirements.local')

    update_mod.install_requirements()

    assert calls[0][-1] == 'requirements.local'
    assert calls[0][0:3] == ['uv', 'pip', 'sync']


def test_install_requirements_reports_missing_uv(monkeypatch, update_mod):
    calls = []
    _capture_run(monkeypatch, calls)
    monkeypatch.setattr(shutil, 'which', lambda name: None)

    rc = update_mod.install_requirements()

    assert rc.returncode == -1, "a missing uv must surface as a failed install, not an exception"
    assert calls == [], "nothing may be executed when uv is not on PATH"


def test_run_py_fallback_uses_uv(monkeypatch):
    calls = []
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == 'rich' or name.startswith('rich.'):
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    _capture_run(monkeypatch, calls)
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/uv')

 # `rich` is imported inside run.py's guarded block, so breaking it must land in the
 # dependency-repair fallback instead of skipping the module quietly.
    with pytest.raises(SystemExit):
        runpy.run_path(str(REPO / 'run.py'), run_name='run_py_uv_probe')

    assert calls, "the missing-module fallback never ran"
    assert calls[0][:3] == ['uv', 'pip', 'sync']
    assert 'piptools' not in calls[0]


def test_run_py_fallback_reports_missing_uv(monkeypatch):
    calls = []
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == 'rich' or name.startswith('rich.'):
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    _capture_run(monkeypatch, calls)
    monkeypatch.setattr(shutil, 'which', lambda name: None)

    with pytest.raises(SystemExit):
        runpy.run_path(str(REPO / 'run.py'), run_name='run_py_uv_probe')

    assert calls == [], "must not shell out when uv is absent"


def test_launchers_create_and_sync_with_uv():
    for name in LAUNCHERS:
        text = (REPO / name).read_text(encoding='utf-8')
        suffix = Path(name).suffix
        assert _venv_create_line(text, UVEXE[suffix], VENV[suffix]), \
            f"{name} does not create {VENV[suffix]} with '{UVEXE[suffix]} venv'"
        assert 'pip sync --python' in text, f"{name} does not sync the environment"
        assert 'UV_CACHE_DIR' in text, f"{name} does not co-locate the cache"
        assert 'pip install' not in text, f"{name} still installs with pip"
        assert 'piptools' not in text, f"{name} still uses piptools"


def test_python_entry_points_are_free_of_pip_tools():
    for name in ('update.py', 'run.py'):
        text = (REPO / name).read_text(encoding='utf-8')
        assert 'piptools' not in text, f"{name} still references piptools"
        assert 'pip-tools' not in text, f"{name} still references pip-tools"


#: Every launcher that touches the environment, including the repair pair.
VENV_LAUNCHERS = LAUNCHERS + ['repair.cmd', 'repair.sh']

#: The argument token each launcher must forward AFTER it has walked the args for ``-n``.
ARGS_FORWARDING = {
    'update.cmd': '%ARGS%', 'install.cmd': '%ARGS%', 'mizedit.cmd': '%ARGS%',
    'update.sh': '${ARGS[@]}', 'install.sh': '${ARGS[@]}', 'mizedit.sh': '${ARGS[@]}',
}


def test_launchers_key_the_venv_on_the_node_name():
    for name in VENV_LAUNCHERS:
        text = (REPO / name).read_text(encoding='utf-8')
        if name.endswith('.cmd'):
            assert 'SET VENV=%USERPROFILE%\\.dcssb-%node_name%' in text, name
            assert 'SET "node_name=%computername%"' in text, f"{name} has no node-name default"
            assert '"%~1"=="-n"' in text, f"{name} does not honour -n"
        else:
            assert 'VENV="$HOME/.dcssb-$node_name"' in text, name
            assert 'node_name=$(hostname)' in text, f"{name} has no node-name default"
            assert '-n)' in text, f"{name} does not honour -n"


def test_launchers_forward_args_after_resolving_the_node_name():
    for name, token in ARGS_FORWARDING.items():
        text = (REPO / name).read_text(encoding='utf-8')
        assert token in text, f"{name} does not forward its arguments after -n was consumed"


def test_switch_branch_passes_its_arguments_through():
    assert 'update.cmd --install %*' in (REPO / 'switch-branch.cmd').read_text(encoding='utf-8')
    assert './update.sh --install "$@"' in (REPO / 'switch-branch.sh').read_text(encoding='utf-8')


#: The launchers that resolve PYTHON; repair only deletes, so it needs no interpreter.
PYTHON_LAUNCHERS_CMD = ['run.cmd', 'update.cmd', 'install.cmd', 'mizedit.cmd']
PYTHON_LAUNCHERS_SH = ['run.sh', 'update.sh', 'install.sh', 'mizedit.sh']


def test_launchers_prefer_the_environments_own_interpreter():
    for name in PYTHON_LAUNCHERS_CMD:
        text = (REPO / name).read_text(encoding='utf-8')
        assert 'SET "PYTHON=%VENV%\\Scripts\\python.exe"' in text, name
        assert 'if not exist "%PYTHON%" SET "PYTHON=python"' in text, f"{name} has no fallback"
        assert '"%PYTHON%" --version' in text, f"{name} still probes a bare python"
        assert '"%PYTHON%" -c "import sys;' in text, f"{name} still probes a bare python"

    for name in PYTHON_LAUNCHERS_SH:
        text = (REPO / name).read_text(encoding='utf-8')
        assert 'PYTHON="$VENV/bin/python"' in text, name
        assert '[ -x "$PYTHON" ] || PYTHON=python' in text, f"{name} has no fallback"
        assert 'python_version=$("$PYTHON" -c' in text, f"{name} still probes a bare python"


#: Launchers that create/sync an environment; repair only deletes, so it is not here.
LOCAL_REQ_LAUNCHERS_CMD = ['run.cmd', 'update.cmd', 'install.cmd', 'mizedit.cmd']
LOCAL_REQ_LAUNCHERS_SH = ['run.sh', 'update.sh', 'install.sh', 'mizedit.sh']


def test_launchers_pass_requirements_local_when_present():
    """`requirements.local` is the documented way to add plugin dependencies (plugins/README.md),
    so a freshly created environment must include it — otherwise the first boot fails on the
    missing module and only the fallback sync repairs it."""
    for name in LOCAL_REQ_LAUNCHERS_CMD:
        text = (REPO / name).read_text(encoding='utf-8')
        assert 'if exist requirements.local SET "REQ=requirements.txt requirements.local"' in text, name
        assert '"%UVEXE%" pip sync --python "%VENV%\\Scripts\\python.exe" %REQ%' in text, name

    for name in LOCAL_REQ_LAUNCHERS_SH:
        text = (REPO / name).read_text(encoding='utf-8')
        assert 'REQ=(requirements.txt)' in text, name
        assert 'REQ+=(requirements.local)' in text, name
        assert '"$UVEXE" pip sync --python "$VENV/bin/python" "${REQ[@]}"' in text, name


def test_launchers_resolve_uv_through_the_helper():
    """uv is allowed to be absent: the launchers call get_uv, which finds it, then installs it."""
    for name in LOCAL_REQ_LAUNCHERS_CMD:
        text = (REPO / name).read_text(encoding='utf-8')
        assert 'call "%~dp0get_uv.cmd"' in text, name
        assert 'if not defined UVEXE (' in text, name
        assert _venv_create_line(text, UVEXE['.cmd'], VENV['.cmd']), \
            f"{name} does not run '{UVEXE['.cmd']} venv' over {VENV['.cmd']}"
        assert '"%UVEXE%" pip sync' in text, name
        assert 'where uv >NUL 2>&1' not in text, f"{name} still probes for uv on PATH itself"

    for name in LOCAL_REQ_LAUNCHERS_SH:
        text = (REPO / name).read_text(encoding='utf-8')
        assert '/get_uv.sh"' in text, f"{name} does not source the uv helper"
        assert '[ -z "$UVEXE" ]' in text, name
        assert _venv_create_line(text, UVEXE['.sh'], VENV['.sh']), \
            f"{name} does not run '{UVEXE['.sh']} venv' over {VENV['.sh']}"
        assert 'command -v uv' not in text, f"{name} still probes for uv on PATH itself"


def _record_popen(monkeypatch, module, calls):
    class FakePopen:
        def __init__(self, cmd, **kwargs):
            calls.append((list(cmd), kwargs))
    monkeypatch.setattr(module.subprocess, 'Popen', FakePopen)


def test_updater_relaunches_the_launcher_itself(update_mod, monkeypatch):
    """After an update the updater starts a fresh launcher, because the one that started it has just
    been rewritten on disk and can no longer read its own remaining lines."""
    calls = []
    monkeypatch.chdir(REPO)
    _record_popen(monkeypatch, update_mod, calls)
    monkeypatch.setattr(update_mod.subprocess, 'CREATE_NEW_CONSOLE', 0x10, raising=False)
    monkeypatch.setattr(update_mod.sys, 'argv', ['update.py', '-n', 'TestNode'])
    monkeypatch.setattr(update_mod.sys, 'platform', 'win32')

    assert update_mod.relaunch_launcher() is True
    cmd, kwargs = calls[0]
    assert cmd[2:] == ['run.cmd', '-n', 'TestNode']
    assert kwargs['creationflags'] == 0x10


def test_updater_relaunch_uses_run_sh_off_windows(update_mod, monkeypatch):
    calls = []
    monkeypatch.chdir(REPO)
    _record_popen(monkeypatch, update_mod, calls)
    monkeypatch.setattr(update_mod.sys, 'argv', ['update.py', '--install', '-n', 'TestNode'])
    monkeypatch.setattr(update_mod.sys, 'platform', 'linux')

    assert update_mod.relaunch_launcher() is True
    cmd, kwargs = calls[0]
    assert cmd == ['./run.sh', '-n', 'TestNode'], '--install must not be passed on'
    assert kwargs['start_new_session'] is True


def test_updater_does_not_claim_success_when_there_is_no_launcher(update_mod, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(update_mod.sys, 'platform', 'win32')
    assert update_mod.relaunch_launcher() is False


def test_the_hand_over_and_its_exit_code_are_wired_into_the_launchers(update_mod):
    """The path itself cannot run here - it would pull the repository - so it is pinned by text."""
    assert update_mod.ALREADY_RESTARTED == -5
    text = (REPO / 'update.py').read_text(encoding='utf-8')
    assert 'relaunch_launcher()' in text
    assert 'exit(ALREADY_RESTARTED)' in text

    run_cmd = (REPO / 'run.cmd').read_text(encoding='utf-8')
    assert '%ERRORLEVEL% EQU -5 (' in run_cmd
    assert 'exit /B 0' in run_cmd

    run_sh = (REPO / 'run.sh').read_text(encoding='utf-8')
    assert 'EXIT_CODE -eq 251' in run_sh, '-5 as bash reports it'
    assert 'break' in run_sh
