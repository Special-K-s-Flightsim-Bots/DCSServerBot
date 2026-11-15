"""The per-load weather ``preset`` plug-in point: ``apply_mission_changes`` -> ``modify_mission``.

Contract pinned here:

* a ``dict`` ``preset`` (and a ``list`` of preset DOCUMENTs) REPLACES the instance's configured
  MizEdit presets for that load — ``apply_presets`` receives the passed document(s) and the
  configured names never reach it;
* a ``list`` of preset NAMEs is a combination: every NAME resolves against the same FRESH read as
  the singular ``str`` case, and the resolved documents reach ``apply_presets`` in the given order;
* a ``str`` ``preset`` is a preset NAME resolved by MizEdit against a FRESH read of the node's preset
  file, so a preset that appears after the extension was built still applies on the next load;
* a name the node does not carry — alone or inside a list — is logged and ignored exactly as before:
  no exception, and ``modify_mission`` returns its honest ``(filename, False)``;
* ``None`` keeps today's behaviour (the configured presets resolve as they always did);
* on the wire, ``preset`` is an ABSENT key when it is ``None``, so a no-preset RPC is byte-identical
  to the pre-change format and a node that predates the parameter still accepts it;
* ``preset=None`` is passed to an extension as NO keyword, so a third-party extension still declaring
  ``modify_mission(self, filename)`` keeps applying;
* ``loadNextMission`` takes NO ``preset`` — that omission is the rotation guarantee.

The extension is driven through the REAL node-side sink, ``ServerImpl.apply_mission_changes``, with only
``MizEdit.apply_presets`` (the file writer) replaced by a recorder — the seam, not the logic.
"""
from __future__ import annotations

import inspect
import logging
import os
import types

import pytest

try:
    from core import Status, Extension
    from core.data.impl.serverimpl import ServerImpl
    from core.data.proxy.serverproxy import ServerProxy
    from extensions.mizedit.extension import MizEdit
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)


# --------------------------------------------------------------------------------- preset fixtures

#: A node's preset file BEFORE the "Fresh" preset exists.
BEFORE = """\
Configured:
  modify:
    weather:
      clouds:
        base: 1000
"""

#: The same file AFTER "Fresh" was saved on the node.
AFTER = BEFORE + """\
Fresh:
  modify:
    weather:
      clouds:
        base: 500
"""

CONFIGURED_MODIFICATIONS = [{"modify": {"weather": {"clouds": {"base": 1000}}}}]
FRESH_MODIFICATIONS = [{"modify": {"weather": {"clouds": {"base": 500}}}}]


# -------------------------------------------------------------------------------------- harness

class Recorder:
    """Stands in for ``MizEdit.apply_presets`` — the file writer — and keeps what it was handed."""

    def __init__(self):
        self.calls: list[types.SimpleNamespace] = []

    async def __call__(self, server, filename, preset, debug=False):
        self.calls.append(types.SimpleNamespace(
            server=server, filename=filename, preset=preset, debug=debug))


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(MizEdit, "apply_presets", staticmethod(rec))
    return rec


def node_sink(mizedit):
    """The node-side ``self`` ``ServerImpl.apply_mission_changes`` needs: its extensions and its log."""
    return types.SimpleNamespace(
        locals={}, log=logging.getLogger("test.launch.preset"),
        extensions={"MizEdit": mizedit},
    )


def build_mizedit(presets_file: str, *, settings=None):
    """A REAL ``MizEdit`` bound to a node whose preset file is ``presets_file``.

    ``validation`` is switched off so the fixture file is loaded verbatim, without the schema coupling.
    Returns the node-side sink ``ServerImpl.apply_mission_changes`` is driven through, and the extension.
    """
    node = types.SimpleNamespace(config_dir=os.path.dirname(presets_file),
                                 config={"validation": "off"}, pool=None)
    config: dict = {"presets": presets_file}
    if settings is not None:
        config["settings"] = settings
    server = types.SimpleNamespace(status=Status.RUNNING, name="SRS-1", node=node)
    miz = MizEdit(server, config)  # type: ignore[arg-type] - a deliberately minimal Server double
    return node_sink(miz), miz


@pytest.fixture
def mission(tmp_path):
    f = tmp_path / "Training.miz"
    f.write_bytes(b"MIZ")
    return str(f)


def write(path, text):
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------- 1. a dict/list REPLACES config

@pytest.mark.asyncio
async def test_a_dict_preset_replaces_the_configured_presets(tmp_path, recorder, mission):
    """The passed document IS what ``apply_presets`` gets; the instance's configured names do not."""
    presets = tmp_path / "presets.yaml"
    write(presets, AFTER)
    sink, miz = build_mizedit(str(presets), settings="Configured")
    doc = {"modify": {"weather": {"clouds": {"base": 250}}}}

    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset=doc)

    assert len(recorder.calls) == 1, "one preset document, one apply"
    assert recorder.calls[0].preset is doc, "the passed dict reaches apply_presets unchanged"
    assert recorder.calls[0].preset != CONFIGURED_MODIFICATIONS, "the configured preset was NOT used"


@pytest.mark.asyncio
async def test_a_list_combination_is_passed_through_unchanged(tmp_path, recorder, mission):
    """A ``list`` is a combination: it reaches ``apply_presets`` as the same list, not re-resolved."""
    presets = tmp_path / "presets.yaml"
    write(presets, AFTER)
    sink, _miz = build_mizedit(str(presets), settings="Configured")
    combo = [{"modify": {"weather": {"clouds": {"base": 300}}}},
             {"modify": {"weather": {"wind": {"atGround": {"speed": 4}}}}}]

    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset=combo)

    assert recorder.calls[0].preset is combo


# -------------------------------------------------- 2. a str name resolves against a FRESH node read

@pytest.mark.asyncio
async def test_a_str_name_resolves_against_a_fresh_read(tmp_path, recorder, mission):
    """A name saved AFTER the extension was built still applies on the next load (no restart)."""
    presets = tmp_path / "presets.yaml"
    write(presets, BEFORE)
    sink, miz = build_mizedit(str(presets), settings="Configured")
    assert "Fresh" not in miz.presets, "the extension was built before Fresh existed"

    write(presets, AFTER)  # the preset is saved on the node

    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset="Fresh")

    assert recorder.calls[-1].preset == FRESH_MODIFICATIONS, "the fresh file was read for the name"
    assert recorder.calls[-1].preset != CONFIGURED_MODIFICATIONS


@pytest.mark.asyncio
async def test_an_unknown_name_is_logged_and_ignored(tmp_path, recorder, mission, caplog):
    """A name the node does not carry: no exception, nothing applied, the honest ``False`` verdict."""
    presets = tmp_path / "presets.yaml"
    write(presets, AFTER)
    sink, miz = build_mizedit(str(presets), settings="Configured")

    with caplog.at_level(logging.ERROR, logger="extensions.mizedit.extension"):
        out = await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset="Nope")

    assert out == mission, "the mission loads unchanged"
    assert recorder.calls == [], "nothing was applied"
    assert any("Preset Nope not found, ignored." in r.getMessage() for r in caplog.records)
    # and the directory call reports the same honest pair on its own
    assert await miz.modify_mission(mission, preset="Nope") == (mission, False)


# ------------------------------------------------------ 3. None keeps today's behaviour byte for byte

@pytest.mark.asyncio
async def test_none_resolves_the_configured_presets(tmp_path, recorder, mission):
    """The default applies the instance's configured preset, exactly as before the parameter existed."""
    presets = tmp_path / "presets.yaml"
    write(presets, AFTER)
    sink, _miz = build_mizedit(str(presets), settings="Configured")

    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False)

    assert recorder.calls[-1].preset == CONFIGURED_MODIFICATIONS


@pytest.mark.asyncio
async def test_none_with_no_configured_presets_logs_and_leaves_the_mission(tmp_path, recorder, mission,
                                                                         caplog):
    """An instance with no configured presets behaves as it always did: logged, nothing written."""
    presets = tmp_path / "presets.yaml"
    write(presets, AFTER)
    sink, _miz = build_mizedit(str(presets))       # no ``settings`` -> nothing configured

    with caplog.at_level(logging.ERROR, logger="extensions.mizedit.extension"):
        out = await ServerImpl.apply_mission_changes(sink, mission, use_orig=False)

    assert out is None or out == mission
    assert recorder.calls == []
    assert any("MizEdit: No presets defined!" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------- 4. the contract additions on every signature

def _params(fn):
    return inspect.signature(fn).parameters


def test_loadNextMission_takes_no_preset():
    """The scheduler's load cannot carry a preset — the omission IS the rotation guarantee."""
    for fn in (ServerImpl.loadNextMission, ServerProxy.loadNextMission):
        params = _params(fn)
        assert "preset" not in params
        assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()), \
            "no **kwargs could smuggle one in either"


def test_the_load_path_accepts_a_keyword_only_preset():
    """``preset`` is keyword-only on every hop, so no positional caller can shift it."""
    hops = [ServerImpl.startup, ServerImpl.loadMission, ServerImpl.apply_mission_changes,
            ServerProxy.startup, ServerProxy.loadMission, ServerProxy.apply_mission_changes]
    for fn in hops:
        params = _params(fn)
        assert "preset" in params, fn.__qualname__
        assert params["preset"].kind is inspect.Parameter.KEYWORD_ONLY, fn.__qualname__
        assert params["preset"].default is None, fn.__qualname__


def test_every_modify_mission_override_accepts_preset():
    """The base and all four overrides carry the keyword — no TypeError on a load."""
    from extensions.github.extension import GitHub
    from extensions.persistence.extension import Persistence
    from extensions.realweather.extension import RealWeather

    from core.extension import Extension
    for cls in (Extension, MizEdit, RealWeather, Persistence, GitHub):
        params = _params(cls.modify_mission)
        assert "preset" in params, cls.__name__
        assert params["preset"].kind is inspect.Parameter.KEYWORD_ONLY, cls.__name__


class Bus:
    def __init__(self):
        self.calls: list[dict] = []

    async def send_to_node_sync(self, message, **kwargs):
        self.calls.append(message)
        return None


@pytest.mark.asyncio
async def test_the_proxy_carries_preset_on_every_load_rpc():
    """Agent-node/cluster servers must receive the field — a missing stub silently drops the choice."""
    bus = Bus()
    proxy = types.SimpleNamespace(name="SRS-1",
                                  node=types.SimpleNamespace(slow_system=False, name="node-1"),
                                  bus=bus)

    doc = {"modify": {"weather": {"clouds": {"base": 250}}}}
    await ServerProxy.startup(proxy, preset=doc)  # type: ignore[arg-type] - a minimal proxy double
    await ServerProxy.loadMission(proxy, "Training.miz", preset=[{"a": 1}])  # type: ignore[arg-type]
    await ServerProxy.apply_mission_changes(proxy, "Training.miz", preset="Fresh")  # type: ignore[arg-type]

    assert [c["params"]["preset"] for c in bus.calls] == [doc, [{"a": 1}], "Fresh"]


@pytest.mark.asyncio
async def test_the_proxy_omits_the_preset_key_when_none():
    """A no-preset call is byte-identical to the pre-change wire format: no ``preset`` key at all.

    The node validates the params against its OWN method signature, so a null ``preset`` key makes a
    node that predates the parameter reject the whole RPC — including calls that carry no preset.
    """
    bus = Bus()
    proxy = types.SimpleNamespace(name="SRS-1",
                                  node=types.SimpleNamespace(slow_system=False, name="node-1"),
                                  bus=bus)

    await ServerProxy.startup(proxy)  # type: ignore[arg-type] - a minimal proxy double
    await ServerProxy.loadMission(proxy, "Training.miz")  # type: ignore[arg-type]
    await ServerProxy.apply_mission_changes(proxy, "Training.miz")  # type: ignore[arg-type]

    for call in bus.calls:
        assert "preset" not in call["params"], call
    assert bus.calls[0]["params"] == {"modify_mission": True, "use_orig": True}
    assert bus.calls[1]["params"] == {"mission": "Training.miz", "modify_mission": True,
                                      "use_orig": True, "no_reload": False}
    assert bus.calls[2]["params"] == {"filename": "Training.miz", "use_orig": True}


# ------------------------------------------- 5. an extension on the OLD standalone signature survives

class OldSignatureExtension(Extension):
    """A third-party extension still declaring ``modify_mission(self, filename)``."""

    def __init__(self, server, config):
        super().__init__(server, config)
        self.calls = 0

    async def modify_mission(self, filename):  # type: ignore[override]
        """Deliberately the pre-parameter shape."""
        self.calls += 1
        return filename, False


@pytest.mark.asyncio
async def test_an_old_signature_extension_still_applies(mission):
    """``preset=None`` passes no keyword, so a stale override is called, not a swallowed ``TypeError``."""
    node = types.SimpleNamespace(config_dir=os.path.dirname(mission),
                                 config={"validation": "off"}, pool=None)
    ext = OldSignatureExtension(
        types.SimpleNamespace(status=Status.RUNNING, name="S", node=node), {})  # type: ignore[arg-type]
    sink = types.SimpleNamespace(locals={}, log=logging.getLogger("test.launch.preset.old"),
                                 extensions={"Old": ext})

    out = await ServerImpl.apply_mission_changes(sink, mission, use_orig=False)

    assert ext.calls == 1, "the old-signature extension was reached under the shipped path"
    assert out == mission


@pytest.mark.asyncio
async def test_a_preset_value_still_reaches_an_updated_override_on_every_load(tmp_path, recorder, mission):
    """The keyword IS passed when a preset is given, so an updated override receives it."""
    presets = tmp_path / "presets.yaml"
    write(presets, AFTER)
    sink, _miz = build_mizedit(str(presets), settings="Configured")
    doc = {"modify": {"weather": {"clouds": {"base": 111}}}}

    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset=doc)

    assert recorder.calls[-1].preset is doc


# ------------------------------------------------------------- 6. a list of preset NAMES is a combination

@pytest.mark.asyncio
async def test_a_list_of_names_resolves_each_name_in_order(tmp_path, recorder, mission):
    """A ``list`` of NAMEs combines: each NAME resolves, and they reach ``apply_presets`` in order."""
    presets = tmp_path / "presets.yaml"
    write(presets, AFTER)
    sink, _miz = build_mizedit(str(presets), settings="Configured")

    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False,
                                           preset=["Configured", "Fresh"])

    assert recorder.calls[-1].preset == CONFIGURED_MODIFICATIONS + FRESH_MODIFICATIONS


@pytest.mark.asyncio
async def test_a_list_with_only_an_absent_name_is_logged_and_ignored(tmp_path, recorder, mission, caplog):
    """An absent NAME inside a list behaves exactly as the singular case: logged, ignored, no raise."""
    presets = tmp_path / "presets.yaml"
    write(presets, AFTER)
    sink, _miz = build_mizedit(str(presets), settings="Configured")

    with caplog.at_level(logging.ERROR, logger="extensions.mizedit.extension"):
        out = await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset=["Nope"])

    assert out == mission, "the mission loads unchanged"
    assert recorder.calls == [], "nothing was applied"
    assert any("Preset Nope not found, ignored." in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_list_of_a_known_and_an_absent_name_applies_the_known_one(tmp_path, recorder, mission,
                                                                         caplog):
    """The known NAME still applies; the absent one is skipped and the rest keeps its order."""
    presets = tmp_path / "presets.yaml"
    write(presets, AFTER)
    sink, _miz = build_mizedit(str(presets), settings="Configured")

    with caplog.at_level(logging.ERROR, logger="extensions.mizedit.extension"):
        await ServerImpl.apply_mission_changes(sink, mission, use_orig=False,
                                               preset=["Nope", "Fresh"])

    assert recorder.calls[-1].preset == FRESH_MODIFICATIONS
    assert any("Preset Nope not found, ignored." in r.getMessage() for r in caplog.records)
