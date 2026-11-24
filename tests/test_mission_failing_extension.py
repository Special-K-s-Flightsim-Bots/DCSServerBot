"""An extension that fails on a mission load must not throw away what the others did.

The failure this pins: in ``ServerImpl.apply_mission_changes`` an exception from one extension's
``modify_mission`` left the loop for the outer except-fallback, which copies the bare ``.orig`` into
the writable mission. When RealWeather found no METAR for its ICAO, the mission therefore loaded
without the changes of EVERY extension, MizEdit presets included, not only without the weather.
The failing extension is now logged and skipped; the others still apply on the temporary copy.

The pins are the real shipped body over a stub ``self`` and a real temporary tree.
"""
from __future__ import annotations

import types
from unittest import mock

import pytest

from core.data.impl import serverimpl
from core.data.impl.serverimpl import ServerImpl
from core.extension import Extension


class _Log:
    def __init__(self):
        self.exceptions: list[str] = []

    def exception(self, msg, *a, **kw):
        self.exceptions.append(str(msg))

    def info(self, msg, *a, **kw):
        pass

    def warning(self, msg, *a, **kw):
        pass

    def debug(self, msg, *a, **kw):
        pass


class _Marker(Extension):
    """Appends its tag to the mission file, like an extension that rewrites the mission."""

    def __init__(self, tag: str):
        self.tag = tag

    @property
    def name(self) -> str:
        return self.tag

    async def modify_mission(self, filename: str, *, preset=None):
        with open(filename, "a", encoding="utf-8") as f:
            f.write(self.tag)
        return filename, True


class _Failing(Extension):
    """RealWeather without a METAR: raises before touching the file."""

    def __init__(self):
        pass

    @property
    def name(self) -> str:
        return "RealWeather"

    async def modify_mission(self, filename: str, *, preset=None):
        raise RuntimeError("no METAR")


async def _load(tmp_path, extensions) -> tuple[str, _Log]:
    mission = tmp_path / "m.miz"
    mission.write_text("old", encoding="utf-8")
    (tmp_path / ".dcssb").mkdir()
    (tmp_path / ".dcssb" / "m.miz.orig").write_text("new", encoding="utf-8")
    stub = types.SimpleNamespace(locals={}, extensions={e.name: e for e in extensions}, log=_Log())
    with mock.patch.object(serverimpl, "_record_launched_preset", mock.AsyncMock()):
        result = await ServerImpl.apply_mission_changes(stub, str(mission), use_orig=True)
    with open(result, encoding="utf-8") as f:
        return f.read(), stub.log


@pytest.mark.asyncio
async def test_a_failing_extension_is_skipped_and_the_others_apply(tmp_path):
    content, log = await _load(tmp_path, [_Marker("A"), _Failing(), _Marker("B")])
    assert content == "newAB", "the fresh .orig with the changes of both healthy extensions"
    assert log.exceptions, "the failure must be logged"


@pytest.mark.asyncio
async def test_without_a_failure_every_extension_applies(tmp_path):
    content, _ = await _load(tmp_path, [_Marker("A"), _Marker("B")])
    assert content == "newAB"


@pytest.mark.asyncio
async def test_a_lone_failing_extension_still_loads_the_fresh_orig(tmp_path):
    content, _ = await _load(tmp_path, [_Failing()])
    assert content == "new"
