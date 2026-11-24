"""``detect_dcs_installation`` finds DCS World installs (Windows registry) without prompts.

On non-Windows there is no discovery source, so it returns nothing; the installer asks for a path.
The Windows branch is exercised with a fake ``winreg``.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.utils import dcs  # noqa: E402


class _Key:
    def __init__(self):
        self.closed = False

    def Close(self):
        self.closed = True


class _FakeWinreg(types.ModuleType):
    HKEY_CURRENT_USER = object()

    def __init__(self, installs):
        super().__init__('winreg')
        self._installs = installs  # {name: path}
        self._current = None
        self.opened = []

    def OpenKey(self, root, path, _reserved):
        self.opened.append(path)
        if path == r"Software\Eagle Dynamics":
            return _Key()
        return _Key()

    def QueryInfoKey(self, key):
        return (len(self._installs), None, None)

    def EnumKey(self, key, index):
        return list(self._installs)[index]

    def QueryValueEx(self, key, name):
        # the registry walk asks each subkey's own name via EnumKey, then reads its 'Path'
        return (self._installs[self._current],)


@pytest.fixture
def fake_winreg(monkeypatch):
    def _install(installs):
        fake = _FakeWinreg(installs)
        # the walk names the subkey from EnumKey; answer QueryValueEx from that name
        enum = fake.EnumKey

        def enum_and_remember(key, index):
            name = enum(key, index)
            fake._current = name
            return name

        fake.EnumKey = enum_and_remember
        monkeypatch.setitem(sys.modules, 'winreg', fake)
        monkeypatch.setattr(dcs.sys, 'platform', 'win32')
        return fake

    return _install


def test_non_windows_returns_no_candidates(monkeypatch):
    monkeypatch.setattr(dcs.sys, 'platform', 'linux')
    assert dcs.detect_dcs_installation() == []


def test_windows_lists_existing_installs(fake_winreg, tmp_path):
    real = tmp_path / 'DCS World'
    real.mkdir()
    fake_winreg({'DCS World': str(real), 'DCS World OpenBeta': str(tmp_path / 'missing')})
    assert dcs.detect_dcs_installation() == [('DCS World', str(real))]


def test_windows_empty_registry_returns_nothing(fake_winreg):
    fake_winreg({})
    assert dcs.detect_dcs_installation() == []


def test_windows_missing_registry_returns_nothing(fake_winreg):
    fake = fake_winreg({})

    def boom(root, path, reserved):
        raise FileNotFoundError(path)

    fake.OpenKey = boom
    assert dcs.detect_dcs_installation() == []
