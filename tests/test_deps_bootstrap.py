"""Pins the environment reconcile that runs at every boot (``deps.py``).

Nothing here needs a real virtual environment or uv: the fingerprint, the stamp and the uv call are
plain functions of the installation directory, ``sys.prefix`` and PATH, so they are exercised for
real with a temporary directory standing in for the install and for the environment.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import deps  # noqa: E402


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A fake installation directory, plus a fake environment root as this process's ``sys.prefix``."""
    install_dir = tmp_path / 'install'
    install_dir.mkdir()
    (install_dir / 'requirements.txt').write_text('packaging==24.2\n', encoding='utf-8')
    env_dir = tmp_path / 'env'
    env_dir.mkdir()
    monkeypatch.chdir(install_dir)
    monkeypatch.setattr(sys, 'prefix', str(env_dir))
    monkeypatch.setattr(sys, 'base_prefix', str(tmp_path))
    return install_dir


def _capture(monkeypatch, returncode=0):
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=returncode)

    monkeypatch.setattr(deps.subprocess, 'run', fake_run)
    monkeypatch.setattr(deps.shutil, 'which', lambda name: '/usr/bin/uv')
    return calls


def test_fingerprint_tracks_content_and_file_names(install):
    before = deps.fingerprint()
    (install / 'requirements.txt').write_text('packaging==24.3\n', encoding='utf-8')
    assert deps.fingerprint() != before, "a changed pin must change the fingerprint"

    after_content_change = deps.fingerprint()
    (install / 'requirements.local').write_text('rich==13.9.4\n', encoding='utf-8')
    assert deps.fingerprint() != after_content_change, \
        "requirements.local is part of what the environment must match"
    assert deps.requirement_files() == ['requirements.txt', 'requirements.local']


def test_an_environment_without_a_stamp_is_out_of_date(install):
    assert deps.needs_sync() is True


def test_a_matching_stamp_needs_no_sync_and_runs_nothing(install, monkeypatch):
    calls = _capture(monkeypatch)
    assert deps.ensure() == 0
    assert len(calls) == 1
    assert deps.needs_sync() is False

    assert deps.ensure() == 0
    assert len(calls) == 1, "an unchanged environment must not shell out to uv again"


def test_a_changed_requirement_file_triggers_the_next_boot(install, monkeypatch):
    calls = _capture(monkeypatch)
    deps.ensure()
    (install / 'requirements.txt').write_text('packaging==24.3\nrich==13.9.4\n', encoding='utf-8')
    assert deps.needs_sync() is True
    assert deps.ensure() == 0
    assert len(calls) == 2


def test_a_failed_sync_is_not_recorded_so_the_next_boot_retries(install, monkeypatch):
    calls = _capture(monkeypatch, returncode=2)
    assert deps.ensure() == 2
    assert len(calls) == 1
    assert deps.needs_sync() is True, "a failed synchronisation must not look like a succeeded one"


def test_sync_passes_the_interpreter_and_requirements_local(install, monkeypatch):
    calls = _capture(monkeypatch)
    (install / 'requirements.local').write_text('rich==13.9.4\n', encoding='utf-8')
    deps.ensure()
    assert calls == [[
        'uv', 'pip', 'sync', '--python', sys.executable, 'requirements.txt', 'requirements.local'
    ]]


def test_sync_refuses_without_uv(install, monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(deps.shutil, 'which', lambda name: None)
    assert deps.sync() == -1
    assert calls == [], "nothing may be executed when uv is not on PATH"


def test_the_stamp_belongs_to_the_environment_not_the_installation(install, monkeypatch):
    """Two nodes share one installation directory and have separate environments, so the stamp must
    follow ``sys.prefix``: one node's synchronisation may never make the other look up to date."""
    # the prefixes are strings, and the stamp joins them with the HOST's separator: `pathlib` would
    # normalise a POSIX-looking prefix on Windows ('/envs' -> '\\envs') and pin the wrong spelling
    node_a, node_b = '/envs/.dcssb-nodeA', '/envs/.dcssb-nodeB'
    monkeypatch.setattr(sys, 'prefix', node_a)
    first = deps.stamp_path()
    monkeypatch.setattr(sys, 'prefix', node_b)
    second = deps.stamp_path()
    assert first == os.path.join(node_a, deps.STAMP_NAME)
    assert second == os.path.join(node_b, deps.STAMP_NAME)


def test_a_bare_interpreter_carries_no_stamp(install, monkeypatch):
    """Without a virtual environment there is nothing per node to record, and a stamp written next to
    a system interpreter would claim every installation matches this one's requirement files."""
    monkeypatch.setattr(sys, 'prefix', sys.base_prefix)
    assert deps.stamp_path() is None
    assert deps.needs_sync() is False
    deps.write_stamp()  # a no-op, not a crash