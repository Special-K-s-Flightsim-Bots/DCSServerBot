"""A preset applied as a DOCUMENT is recorded by its NAME, never as temporary.

The launch record (``servers.launched_preset`` / ``launched_preset_temporary``) is derived from the
``preset`` VALUE: a name is a name, a list of names is their join, and anything else — a document read
out of a preset FILE — has no name in it and is recorded as temporary. A preset taken from a file does
have a name (its key in that file), so the caller states it beside the document under ``preset_name``
and the record takes it.

Contract pinned here, one section per state of the record:

* no preset — the record is CLEARED (name absent, not temporary);
* a NAME — recorded under that name, not temporary;
* a DOCUMENT with no ``preset_name`` — recorded as TEMPORARY (the pre-existing shape, unchanged);
* a DOCUMENT with ``preset_name`` — recorded under that name and NOT temporary (the RED case);

plus the seam: the keyword is optional and travels as an ABSENT RPC key when it is None, so a call
that states no name is byte-identical on the wire to the pre-change one.

The real ``ServerImpl.apply_mission_changes`` / ``ServerImpl.startup`` are driven over the harness of
``test_mission_load_clears_stale_preset`` (a recording pool, a real ``MizEdit`` over a temp preset file),
and the row the pool keeps is fed to the page's OWN read model, so the assertion is on the label.
"""
from __future__ import annotations

import inspect
import types

import pytest

from core import Status
from core.data.impl import serverimpl
from core.data.impl.serverimpl import ServerImpl
from core.data.proxy.serverproxy import ServerProxy
from core.data.server import Server
from services.webservice import readmodels

from test_mission_load_clears_stale_preset import (  # noqa: F401 - fixtures imported on purpose
    SERVER, RecordingPool, RowState, build_mizedit, build_server, label_of, mission, no_write,
    record_writes,
)

PRESETS = """\
Configured:
  modify:
    weather:
      clouds:
        base: 1000
Winter:
  modify:
    weather:
      clouds:
        base: 500
"""

#: a preset DOCUMENT as it is read out of a preset file — a change, and no name in it
DOCUMENT = {"modify": {"weather": {"clouds": {"base": 250}}}}
NAME = "Winter"


def presets_at(tmp_path) -> dict:
    """A real ``MizEdit`` over a temp preset file — built INSIDE the test, where a loop is running."""
    presets_file = tmp_path / "presets_winter.yaml"
    presets_file.write_text(PRESETS, encoding="utf-8")
    return {"MizEdit": build_mizedit(presets_file)}


def launch_double(mission_file, *, row=None, status=Status.RUNNING, extensions=None):
    """A server over the REAL launch path, with the status-change wait stubbed out."""
    pool = RecordingPool(row if row is not None else RowState())
    srv = build_server(mission_file, pool=pool, status=status, extensions=extensions)

    async def noop(*args, **kwargs):
        return None

    srv._assert_resource_available = noop
    srv._assert_port_free = noop
    srv._assert_ports_free = noop
    srv.init_extensions = noop
    srv.prepare_extensions = noop
    srv._cleanup_sqlite = lambda: None
    srv.do_startup = lambda: None
    srv.is_running = noop
    return srv, pool


@pytest.fixture(autouse=True)
def desanitized(monkeypatch):
    monkeypatch.setattr(serverimpl.utils, "is_desanitized", lambda node: True)


# --------------------------------------------------------- 1. no preset: the record is cleared

@pytest.mark.asyncio
async def test_a_load_with_no_preset_clears_the_record(mission, tmp_path, no_write):
    """The baseline the other three states are read against."""
    srv, pool = launch_double(mission, row=RowState("Summer", False), extensions=presets_at(tmp_path))

    await ServerImpl.apply_mission_changes(srv, mission, use_orig=False)

    assert record_writes(pool)[-1][1] == (None, False, SERVER), record_writes(pool)
    assert label_of(pool.row) is None, "a load that applied nothing must clear the label"


# --------------------------------------------------------- 2. a NAME: recorded as before

@pytest.mark.asyncio
async def test_a_named_preset_is_recorded_by_its_name(mission, tmp_path, no_write):
    srv, pool = launch_double(mission, row=RowState("Summer", False), extensions=presets_at(tmp_path))

    await ServerImpl.apply_mission_changes(srv, mission, use_orig=False, preset=NAME)

    assert record_writes(pool)[-1][1] == (NAME, False, SERVER), record_writes(pool)
    assert label_of(pool.row) is not None and pool.row.preset == NAME
    assert pool.row.temporary is False


# --------------------------------------------------------- 3. a DOCUMENT with no name: temporary

@pytest.mark.asyncio
async def test_a_document_without_a_name_is_recorded_as_temporary(mission, tmp_path, no_write):
    """The pre-existing document shape must keep its meaning with the new keyword unused."""
    srv, pool = launch_double(mission, row=RowState("Summer", False), extensions=presets_at(tmp_path))

    await ServerImpl.apply_mission_changes(srv, mission, use_orig=False, preset=DOCUMENT)

    assert record_writes(pool)[-1][1] == (None, True, SERVER), record_writes(pool)
    assert pool.row.preset is None and pool.row.temporary is True


# --------------------------------------------------------- 4. a DOCUMENT + preset_name: named

@pytest.mark.asyncio
async def test_a_document_with_a_name_is_recorded_by_that_name(mission, tmp_path, no_write):
    """RED on the unfixed tree: the document lands on the row as temporary, with no name."""
    srv, pool = launch_double(mission, row=RowState("Summer", False), extensions=presets_at(tmp_path))

    await ServerImpl.apply_mission_changes(srv, mission, use_orig=False, preset=DOCUMENT,
                                          preset_name=NAME)

    assert record_writes(pool)[-1][1] == (NAME, False, SERVER), record_writes(pool)
    assert pool.row.preset == NAME, "a preset taken from a file has a name: its key in that file"
    assert pool.row.temporary is False, "a preset from a file is not a temporary one"
    view = label_of(pool.row)
    assert view is not None and view.name == NAME, "the page names the preset the file carried"


@pytest.mark.asyncio
async def test_startup_forwards_the_name_through_apply_mission_changes(mission, tmp_path, no_write):
    """The startup hop: a launch that runs the extensions reaches the same record."""
    srv, pool = launch_double(mission, row=RowState("Summer", False), status=Status.SHUTDOWN,
                              extensions=presets_at(tmp_path))

    await ServerImpl.startup(srv, preset=DOCUMENT, preset_name=NAME)

    assert record_writes(pool)[-1][1] == (NAME, False, SERVER), record_writes(pool)
    assert pool.row.temporary is False


@pytest.mark.asyncio
async def test_startup_without_extensions_forwards_the_name_to_the_record(mission, tmp_path):
    """The ``modify_mission=False`` hop states an already-applied preset — name included."""
    srv, pool = launch_double(mission, row=RowState("Summer", False), status=Status.SHUTDOWN,
                              extensions=presets_at(tmp_path))

    await ServerImpl.startup(srv, modify_mission=False, use_orig=False, preset=DOCUMENT,
                             preset_name=NAME)

    assert record_writes(pool)[-1][1] == (NAME, False, SERVER), record_writes(pool)


@pytest.mark.asyncio
async def test_a_name_without_an_applied_preset_records_nothing(mission, tmp_path, no_write):
    """``preset_name`` labels a record; it never creates one on its own."""
    srv, pool = launch_double(mission, row=RowState("Summer", False), extensions=presets_at(tmp_path))

    await ServerImpl.apply_mission_changes(srv, mission, use_orig=False, preset_name=NAME)

    assert record_writes(pool)[-1][1] == (None, False, SERVER), record_writes(pool)
    assert label_of(pool.row) is None


# --------------------------------------------------------- the seam: signature and wire shape

def test_the_keyword_is_optional_and_keyword_only_on_every_hop():
    for fn in (Server.startup, ServerImpl.startup, ServerProxy.startup,
               Server.apply_mission_changes, ServerImpl.apply_mission_changes,
               ServerProxy.apply_mission_changes):
        params = inspect.signature(fn).parameters
        assert "preset_name" in params, fn.__qualname__
        assert params["preset_name"].kind is inspect.Parameter.KEYWORD_ONLY, fn.__qualname__
        assert params["preset_name"].default is None, fn.__qualname__


def test_the_load_path_carries_the_name_and_a_process_start_does_not():
    """``loadMission`` / ``restart`` carry the name beside the document (the Load route reaches the
    record through them); a PROCESS start states no weather at all and takes no name."""
    for fn in (ServerImpl.loadMission, ServerProxy.loadMission, Server.restart, ServerImpl.restart,
               ServerProxy.restart):
        assert "preset_name" in inspect.signature(fn).parameters, fn.__qualname__
    for fn in (ServerImpl.start, ServerProxy.start):
        assert "preset_name" not in inspect.signature(fn).parameters, fn.__qualname__


class Bus:
    def __init__(self):
        self.calls: list[dict] = []

    async def send_to_node_sync(self, message, **kwargs):
        self.calls.append(message)
        return None


def proxy_over(bus) -> ServerProxy:
    return types.SimpleNamespace(name=SERVER,
                                 node=types.SimpleNamespace(slow_system=False, name="node-1"),
                                 bus=bus)  # type: ignore[return-value] - a minimal proxy double


@pytest.mark.asyncio
async def test_the_proxy_carries_the_name_on_both_launch_rpcs():
    bus = Bus()
    proxy = proxy_over(bus)

    await ServerProxy.startup(proxy, preset=DOCUMENT, preset_name=NAME)  # type: ignore[arg-type]
    await ServerProxy.apply_mission_changes(proxy, "Training.miz", preset=DOCUMENT,
                                            preset_name=NAME)  # type: ignore[arg-type]

    assert [call["params"]["preset_name"] for call in bus.calls] == [NAME, NAME]
    assert [call["params"]["preset"] for call in bus.calls] == [DOCUMENT, DOCUMENT]


@pytest.mark.asyncio
async def test_the_proxy_omits_the_name_key_when_none():
    """A call that states no name is the pre-change wire format: no ``preset_name`` key at all."""
    bus = Bus()
    proxy = proxy_over(bus)

    await ServerProxy.startup(proxy)  # type: ignore[arg-type]
    await ServerProxy.apply_mission_changes(proxy, "Training.miz")  # type: ignore[arg-type]

    for call in bus.calls:
        assert "preset_name" not in call["params"], call
    assert bus.calls[0]["params"] == {"modify_mission": True, "use_orig": True}
    assert bus.calls[1]["params"] == {"filename": "Training.miz", "use_orig": True}
