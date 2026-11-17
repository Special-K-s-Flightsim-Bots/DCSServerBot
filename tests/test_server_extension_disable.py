"""``ServerImpl.disable_extension``: turning OFF an extension that is not loaded.

The reported flow: the extension was configured with ``enabled: true`` but was not among the loaded
extensions when the switch was pressed. It raised ("not found"), so nothing ran and no flag was
written — the switch could not be turned off at all.

The second half of that flow, reported after the first fix: turning it off WITHOUT running the
extension's own ``disable()`` leaves the PACKAGE it installed behind (Olympus uninstalls its mod
there), so the next enable answers "Mod already installed" and only a second disable removes it. A
configured extension that is not loaded is therefore BUILT from its configuration and asked.

The cases the method must keep apart:

* the name is CONFIGURED (instance block, node block, or one of the extensions that run with no
  block) — its class is built and its own ``disable()`` decides; the flag is written when it agrees;
* the name's CLASS is gone from the installation — there is nothing left to run, so the flag is
  written and the call succeeds (the runtime is already off);
* the name is configured NOWHERE — a typo must not write a block for an extension that does not
  exist, so that stays an ``InstallException``, word for word what it always said.

The loaded cases are controls: they must behave exactly as before.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.data.impl.serverimpl import DEFAULT_EXTENSIONS, ServerImpl
from core.extension import InstallException


class _Extension:
    """The bit of an extension ``disable_extension`` touches."""

    def __init__(self, accepts: bool = True, name: str = "Olympus") -> None:
        self.accepts = accepts
        self.name = name
        self.disabled = 0

    async def disable(self) -> bool:
        self.disabled += 1
        return self.accepts


def _server(*, instance_blocks: dict | None = None, node_blocks: dict | None = None,
            loaded: dict | None = None, builds: "_Extension | None" = None,
            ) -> tuple[ServerImpl, list[tuple], list[str]]:
    """A ``ServerImpl`` with only the attributes the method reads, plus what it asked for.

    Built with ``object.__new__``: the method reads ``extensions``, ``locals`` and ``node.locals``,
    and its two calls are the configuration write and the class build — recording both is the whole
    contract. *builds* is the extension ``load_extension`` answers (``None`` = the class is gone).
    """
    server = object.__new__(ServerImpl)
    server.extensions = dict(loaded or {})
    server.locals = {"extensions": dict(instance_blocks or {})}
    server.node = SimpleNamespace(locals={"extensions": dict(node_blocks or {})})
    # the real object's logger (``ServerImpl.__init__`` borrows the Server's), so a handler's own
    # logging line cannot be what a pin trips over
    import logging
    server.log = logging.getLogger("test.serverimpl")
    writes: list[tuple] = []
    built: list[str] = []

    async def config_extension(name: str, config: dict | None = None):
        writes.append((name, config))
        return {}

    def load_extension(name: str, config: dict | None = None):
        built.append(name)
        return builds

    server.config_extension = config_extension
    server.load_extension = load_extension
    return server, writes, built


@pytest.mark.asyncio
async def test_a_configured_extension_that_is_not_loaded_runs_its_own_disable():
    """The reported case: the extension is gone from the registry, its block still says ON.

    Its ``disable()`` is what uninstalls a package, so it must RUN — writing the flag alone leaves the
    mod installed and the next enable answers "Mod already installed".
    """
    extension = _Extension(accepts=True)
    server, writes, built = _server(instance_blocks={"Olympus": {"enabled": True, "backend": {}}},
                                   builds=extension)

    assert await server.disable_extension("Olympus") is True
    assert built == ["Olympus"], "the extension was never built, so nothing could uninstall"
    assert extension.disabled == 1, "the extension's own disable() never ran"
    assert writes == [("Olympus", {"Instance": {"enabled": False}})]
    assert server.extensions == {}, "a name that was not loaded was added to the registry"


@pytest.mark.asyncio
async def test_a_configured_extension_whose_class_is_gone_still_turns_off():
    """Nothing left to run: the runtime is already off, so the flag is written and the call succeeds."""
    server, writes, built = _server(instance_blocks={"Olympus": {"enabled": True}}, builds=None)

    assert await server.disable_extension("Olympus") is True
    assert built == ["Olympus"], "the class was not even looked up"
    assert writes == [("Olympus", {"Instance": {"enabled": False}})]


@pytest.mark.asyncio
async def test_a_configured_extension_that_declines_writes_nothing():
    """The extension's own refusal is respected even when it was not loaded (DKS while running)."""
    extension = _Extension(accepts=False, name="DKS")
    server, writes, _built = _server(instance_blocks={"DKS": {"enabled": True}}, builds=extension)

    assert await server.disable_extension("DKS") is False
    assert extension.disabled == 1
    assert writes == [], "a declining extension was switched off in the configuration anyway"


@pytest.mark.asyncio
async def test_an_extension_whose_class_cannot_be_built_still_turns_off():
    """A build that FAILS is not "not found": the operator asked for OFF and must get it.

    Its ``disable()`` cannot run, so nothing is uninstalled — the log says so — but the configuration
    is switched off rather than answering "not found" and leaving the switch ON for ever.
    """
    server, writes, built = _server(instance_blocks={"Olympus": {"enabled": True}})

    def refuses_to_build(name: str, config: dict | None = None):
        built.append(name)
        raise RuntimeError("the configuration cannot be used")

    server.load_extension = refuses_to_build

    assert await server.disable_extension("Olympus") is True
    assert built == ["Olympus"]
    assert writes == [("Olympus", {"Instance": {"enabled": False}})]


@pytest.mark.asyncio
async def test_a_node_level_block_is_enough_to_know_the_name():
    """The node block is the per-node default an instance inherits, so it knows the name too."""
    server, writes, built = _server(node_blocks={"Olympus": {"nodejs": "C:/nodejs"}},
                                   builds=_Extension())

    assert await server.disable_extension("Olympus") is True
    assert built == ["Olympus"]
    assert writes == [("Olympus", {"Instance": {"enabled": False}})]


@pytest.mark.asyncio
async def test_an_extension_that_runs_with_no_block_is_known_by_its_default_name():
    """``LogAnalyser`` and ``Cloud`` are loaded without a block: switching one off writes the block."""
    default = sorted(DEFAULT_EXTENSIONS)[0]
    server, writes, _built = _server(builds=_Extension(name=default))

    assert await server.disable_extension(default) is True
    assert writes == [(default, {"Instance": {"enabled": False}})]


@pytest.mark.asyncio
async def test_the_write_and_the_build_use_the_configured_spelling_of_the_name():
    """A caller may spell a name differently; a second block differing only by case must not appear."""
    server, writes, built = _server(instance_blocks={"Olympus": {"enabled": True}},
                                   builds=_Extension())

    assert await server.disable_extension("olympus") is True
    assert built == ["Olympus"], "the class was built under the caller's spelling"
    assert writes == [("Olympus", {"Instance": {"enabled": False}})]


@pytest.mark.asyncio
async def test_a_name_no_block_carries_is_still_a_refusal_and_writes_nothing():
    """The typo guard: a name the configuration does not know must never create a block."""
    server, writes, built = _server(instance_blocks={"SRS": {"enabled": True}}, builds=_Extension())

    with pytest.raises(InstallException) as caught:
        await server.disable_extension("Olympuss")
    assert "Extension 'Olympuss' not found" in str(caught.value)
    assert writes == [], "a refused name was written into the configuration"
    assert built == [], "a refused name was built"


@pytest.mark.asyncio
async def test_the_flag_lands_in_nodes_yaml_when_the_extension_is_not_loaded(tmp_path):
    """The write goes through the REAL ``config_extension``: the file must read ``enabled: false``.

    Recording the call proves the intent; this proves the operator's configuration changes, which is
    the half the switch reads back on the next render.
    """
    import yaml

    (tmp_path / "nodes.yaml").write_text(
        "dcs-a-01:\n"
        "  instances:\n"
        "    Syria:\n"
        "      extensions:\n"
        "        Olympus:\n"
        "          enabled: true\n"
        "          backend: {address: localhost, port: 4512}\n",
        encoding="utf-8")

    documented = yaml.safe_load((tmp_path / "nodes.yaml").read_text(encoding="utf-8"))

    def read_locals() -> dict:
        return yaml.safe_load((tmp_path / "nodes.yaml").read_text(encoding="utf-8"))["dcs-a-01"]

    instance = SimpleNamespace(name="Syria", locals={})
    node = SimpleNamespace(name="dcs-a-01", config_dir=str(tmp_path), locals=read_locals(),
                           read_locals=read_locals)
    extension = _Extension(accepts=True)
    server = object.__new__(ServerImpl)
    server.node = node
    server.extensions = {}
    # ``locals`` first, and the instance assigned through its own attribute: the ``instance``
    # property also prepares the server (DCS settings files), which this write never reads.
    server.locals = {"extensions": {"Olympus": {"enabled": True}}}
    server._instance = instance
    server.load_extension = lambda name, config=None: extension
    assert documented["dcs-a-01"]["instances"]["Syria"]["extensions"]["Olympus"]["enabled"] is True

    assert await ServerImpl.disable_extension(server, "Olympus") is True

    written = yaml.safe_load((tmp_path / "nodes.yaml").read_text(encoding="utf-8"))
    block = written["dcs-a-01"]["instances"]["Syria"]["extensions"]["Olympus"]
    assert block["enabled"] is False, "the switch would still read ON"
    assert block["backend"] == {"address": "localhost", "port": 4512}, "values were discarded"
    assert extension.disabled == 1, "the mod was not uninstalled"


@pytest.mark.asyncio
async def test_a_loaded_extension_runs_its_own_disable_and_leaves_the_registry():
    """CONTROL: the loaded path is unchanged — the extension decides, then the flag is written."""
    extension = _Extension(accepts=True, name="SRS")
    server, writes, built = _server(instance_blocks={"SRS": {"enabled": True}},
                                   loaded={"SRS": extension}, builds=None)

    assert await server.disable_extension("SRS") is True
    assert extension.disabled == 1
    assert writes == [("SRS", {"Instance": {"enabled": False}})]
    assert "SRS" not in server.extensions
    assert built == [], "a LOADED extension must not be built a second time"


@pytest.mark.asyncio
async def test_a_loaded_extension_that_declines_writes_nothing():
    """CONTROL: the extension's own refusal still leaves the configuration alone."""
    extension = _Extension(accepts=False, name="DKS")
    server, writes, _built = _server(instance_blocks={"DKS": {"enabled": True}},
                                    loaded={"DKS": extension}, builds=None)

    assert await server.disable_extension("DKS") is False
    assert extension.disabled == 1
    assert writes == []
    assert "DKS" in server.extensions, "a declining extension was dropped from the registry"
