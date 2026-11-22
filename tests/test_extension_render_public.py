"""A PUBLIC rendering beside the Discord one: fail-closed base, nine opt-ins, four credential-free builds.

``Extension.render_public`` answers the base dict (``_class``/``name``/``version``) plus ``withheld``
and never a ``value``, so an extension that overrides nothing publishes nothing but its name and its
version. The nine extensions whose ``render()`` carries no credential answer that ``render()`` dict
unchanged. The four that build credentials into it — gated on their own ``show_passwords`` setting,
which cannot tell a public page from Discord — rebuild their value without those lines and report how
many they left out. ``Server.render_extensions(public=True)`` is the ONE switch that picks between the
two renderings, and the proxy carries it on the wire so a remote server answers the public one too.
"""
from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

from core.data.impl.serverimpl import ServerImpl
from core.data.proxy.serverproxy import ServerProxy
from core.extension import Extension

#: Text that marks a credential line. A public value may contain none of it, in any spelling.
FORBIDDEN = ("Pass:", "Password", "passphrase", "GameMaster")

SERVER_NAME = "Test Server"


# --------------------------------------------------------------------------------------------------
# doubles
# --------------------------------------------------------------------------------------------------
class StandIn(Extension):
    """A stand-in whose ``render()`` publishes a value the base public render must not leak."""

    @property
    def version(self) -> str:
        return "9.9.9"

    async def render(self, param: dict | None = None) -> dict:
        return {
            "_class": self.__class__.__name__,
            "name": self.name,
            "version": self.version,
            "value": "the discord-side value"
        }


class RenderForbidden(Extension):
    """An extension whose ``render()`` raises if the public path ever calls it."""

    @property
    def version(self) -> str:
        return "1.0.0"

    async def render(self, param: dict | None = None) -> dict:
        raise AssertionError("the base render_public() must not call render()")


class Recorder:
    """A stub extension recording which of the two methods ran."""

    def __init__(self, name: str, *, raises: bool = False):
        self.name = name
        self.raises = raises
        self.calls: list[str] = []

    async def render(self, param: dict | None = None) -> dict:
        self.calls.append("render")
        return {"name": self.name, "value": "discord-side"}

    async def render_public(self, param: dict | None = None) -> dict:
        self.calls.append("render_public")
        if self.raises:
            raise NotImplementedError()
        return {"name": self.name, "withheld": 0}


class Bus:
    """The service bus double: records every wire payload and answers a fixed list."""

    def __init__(self, answer: list[dict] | None = None):
        self.answer = answer if answer is not None else []
        self.sent: list[dict] = []

    async def send_to_node_sync(self, message: dict, timeout: int | None = None, *, node=None):
        self.sent.append({"message": dict(message), "timeout": timeout, "node": node})
        return list(self.answer)


# --------------------------------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------------------------------
def make_extension(cls, *, name: str | None = None, config: dict | None = None):
    """A shipped-shaped extension built without its constructor: only the attributes a render reads."""
    ext = object.__new__(cls)
    ext._name = name or cls.__name__
    ext.config = dict(config or {})
    ext.server = SimpleNamespace(name=SERVER_NAME, instance=SimpleNamespace(home="/nonexistent"), options={})
    ext.node = SimpleNamespace(name="test-node", public_ip="203.0.113.10", slow_system=False)
    ext.locals = {}
    return ext


def extension_class(dotted: str, class_name: str) -> type:
    return getattr(importlib.import_module(dotted), class_name)


def pin_version(monkeypatch, cls: type, value: str = "1.2.3") -> None:
    """The ``version`` properties read files off disk; a fixture states its own."""
    monkeypatch.setattr(cls, "version", property(lambda self: value))


# --------------------------------------------------------------------------------------------------
# 1. the base render_public: base dict only, no value, and render() is never called
# --------------------------------------------------------------------------------------------------
def test_the_base_public_render_publishes_the_base_dict_and_never_a_value():
    ext = make_extension(StandIn)

    public = asyncio.run(ext.render_public())
    discord = asyncio.run(ext.render())

    assert discord["value"] == "the discord-side value"
    assert public == {
        "_class": "StandIn",
        "name": "StandIn",
        "version": "9.9.9",
        "withheld": 0
    }
    assert "value" not in public


def test_the_base_public_render_does_not_call_render():
    ext = make_extension(RenderForbidden)

    public = asyncio.run(ext.render_public())

    assert public["name"] == "RenderForbidden"
    assert "value" not in public


def test_a_hidden_extension_publishes_nothing_on_the_public_path():
    ext = make_extension(RenderForbidden, config={"hidden": True})

    with pytest.raises(NotImplementedError):
        asyncio.run(ext.render_public())


# --------------------------------------------------------------------------------------------------
# 2. the four credential-carrying extensions: their own render_public, and no credential text
# --------------------------------------------------------------------------------------------------
def lotatc_fixture(monkeypatch) -> tuple[type, object]:
    cls = extension_class("extensions.lotatc.extension", "LotAtc")
    pin_version(monkeypatch, cls)
    ext = make_extension(cls, config={"show_passwords": True, "host": "203.0.113.10"})
    ext.locals = {"port": 10310, "blue_password": "blue-secret", "red_password": "red-secret"}
    return cls, ext


def srs_fixture(monkeypatch) -> tuple[type, object]:
    cls = extension_class("extensions.srs.extension", "SRS")
    pin_version(monkeypatch, cls)
    ext = make_extension(cls, config={"show_passwords": True, "host": "203.0.113.10"})
    ext.locals = {
        "Server Settings": {"SERVER_PORT": 5002},
        "General Settings": {"EXTERNAL_AWACS_MODE": True},
        "External AWACS Mode Settings": {
            "EXTERNAL_AWACS_MODE_BLUE_PASSWORD": "blue-secret",
            "EXTERNAL_AWACS_MODE_RED_PASSWORD": "red-secret"
        }
    }
    return cls, ext


def tacview_fixture(monkeypatch) -> tuple[type, object]:
    cls = extension_class("extensions.tacview.extension", "Tacview")
    pin_version(monkeypatch, cls)
    ext = make_extension(cls, config={"show_passwords": True, "host": "203.0.113.10"})
    ext.locals = {
        "tacviewModuleEnabled": True,
        "tacviewRealTimeTelemetryEnabled": True,
        "tacviewRealTimeTelemetryPort": 42674,
        "tacviewRealTimeTelemetryPassword": "telemetry-secret",
        "tacviewPlaybackDelay": 0,
        "tacviewRemoteControlEnabled": True,
        "tacviewRemoteControlPort": 42675,
        "tacviewRemoteControlPassword": "admin-secret"
    }
    return cls, ext


def olympus_fixture(monkeypatch) -> tuple[type, object]:
    cls = extension_class("extensions.olympus.extension", "Olympus")
    pin_version(monkeypatch, cls)
    ext = make_extension(cls, config={
        "url": "http://203.0.113.10:3000",
        "show_passwords": True,
        "authentication": {
            "gameMasterPassword": "gm-secret",
            "blueCommanderPassword": "blue-secret",
            "redCommanderPassword": "red-secret"
        }
    })
    ext.frontend_tag = "frontend"
    return cls, ext


CREDENTIAL_CARRYING = {
    "LotAtc": (lotatc_fixture, 2),
    "SRS": (srs_fixture, 2),
    "Tacview": (tacview_fixture, 2),
    "Olympus": (olympus_fixture, 3),
}


@pytest.mark.parametrize("name", list(CREDENTIAL_CARRYING))
def test_each_credential_carrying_extension_builds_its_own_public_value(monkeypatch, name):
    fixture, expected_withheld = CREDENTIAL_CARRYING[name]
    cls, ext = fixture(monkeypatch)

    assert "render_public" in cls.__dict__, f"{name} must override render_public"
    assert cls.render_public is not Extension.render_public

    discord = asyncio.run(ext.render())
    public = asyncio.run(ext.render_public())

    # the fixture really exercises the credential branch of the discord render ...
    assert any(marker in discord["value"] for marker in FORBIDDEN), discord["value"]
    # ... and the public value carries none of it
    assert not any(marker in public["value"] for marker in FORBIDDEN), public["value"]
    assert public["withheld"] == expected_withheld
    assert "value" in public


def test_the_four_public_values_hold_only_the_address_and_status_lines(monkeypatch):
    lotatc = asyncio.run(lotatc_fixture(monkeypatch)[1].render_public())
    srs = asyncio.run(srs_fixture(monkeypatch)[1].render_public())
    tacview = asyncio.run(tacview_fixture(monkeypatch)[1].render_public())
    olympus = asyncio.run(olympus_fixture(monkeypatch)[1].render_public())

    assert lotatc["value"] == "203.0.113.10:10310"
    assert srs["value"] == "203.0.113.10:5002"
    assert tacview["value"] == "- 203.0.113.10:42674\n**Remote Ctrl [42675]**\n"
    assert olympus["value"] == "http://203.0.113.10:3000"


def test_a_public_value_reports_zero_withheld_when_it_has_no_credential_to_leave_out(monkeypatch):
    lotatc_cls, lotatc = lotatc_fixture(monkeypatch)
    lotatc.locals = {"port": 10310}
    srs_cls, srs = srs_fixture(monkeypatch)
    srs.locals = {"Server Settings": {"SERVER_PORT": 5002}, "General Settings": {}}
    tacview_cls, tacview = tacview_fixture(monkeypatch)
    tacview.locals = {"tacviewModuleEnabled": False}
    olympus_cls, olympus = olympus_fixture(monkeypatch)
    olympus.config = {"url": "http://203.0.113.10:3000"}

    assert asyncio.run(lotatc.render_public())["withheld"] == 0
    assert asyncio.run(srs.render_public())["withheld"] == 0
    assert asyncio.run(tacview.render_public())["withheld"] == 0
    assert asyncio.run(olympus.render_public())["withheld"] == 0


def test_an_extension_without_its_local_settings_publishes_nothing_publicly(monkeypatch):
    """LotAtc, SRS and Tacview raise on the discord path without their file; the public one matches."""
    for fixture in (lotatc_fixture, srs_fixture, tacview_fixture):
        _, ext = fixture(monkeypatch)
        ext.locals = {}
        with pytest.raises(NotImplementedError):
            asyncio.run(ext.render_public())


def test_olympus_answers_from_its_config_with_no_local_settings_file(monkeypatch):
    _, ext = olympus_fixture(monkeypatch)
    ext.locals = {}

    public = asyncio.run(ext.render_public())

    assert public["value"] == "http://203.0.113.10:3000"
    assert public["withheld"] == 3


# --------------------------------------------------------------------------------------------------
# 3. the nine opt-ins answer exactly what render() answers
# --------------------------------------------------------------------------------------------------
def build_opt_in(monkeypatch, name: str):
    if name == "DKS":
        cls = extension_class("extensions.dks.extension", "DKS")
        pin_version(monkeypatch, cls)
        monkeypatch.setattr(cls, "dks_url", property(lambda self: "https://dks.example.com"))
        monkeypatch.setattr(cls, "tacview_settings", lambda self: {})
        return cls, make_extension(cls, config={"enabled": True})
    if name == "DSMC":
        cls = extension_class("extensions.dsmc.extension", "DSMC")
        pin_version(monkeypatch, cls)
        return cls, make_extension(cls)
    if name == "Lardoon":
        cls = extension_class("extensions.lardoon.extension", "Lardoon")
        pin_version(monkeypatch, cls)
        return cls, make_extension(cls, config={"url": "https://lardoon.example.com"})
    if name == "ModManager":
        cls = extension_class("extensions.modmanager.extension", "ModManager")
        pin_version(monkeypatch, cls)
        ext = make_extension(cls)
        ext.modules = {SERVER_NAME: ["mod-one", "mod-two"]}
        return cls, ext
    if name == "Pretense":
        cls = extension_class("extensions.pretense.extension", "Pretense")
        pin_version(monkeypatch, cls)
        return cls, make_extension(cls)
    if name == "RealWeather":
        cls = extension_class("extensions.realweather.extension", "RealWeather")
        pin_version(monkeypatch, cls)
        ext = make_extension(cls)
        ext.metar = "METAR TEST 101250Z 27010KT CAVOK 18/09 Q1013"
        return cls, ext
    if name == "SkyEye":
        cls = extension_class("extensions.skyeye.extension", "SkyEye")
        pin_version(monkeypatch, cls)
        ext = make_extension(cls)
        ext.configs = [{"coalition": "blue", "callsign": "Focus", "srs-frequencies": "251.0AM,133.0AM"}]
        return cls, ext
    if name == "Sneaker":
        cls = extension_class("extensions.sneaker.extension", "Sneaker")
        pin_version(monkeypatch, cls)
        return cls, make_extension(cls, config={"url": "https://sneaker.example.com"})
    if name == "VoiceChat":
        cls = extension_class("extensions.voicechat.extension", "VoiceChat")
        pin_version(monkeypatch, cls)
        return cls, make_extension(cls, config={"enabled": True})
    raise AssertionError(f"no fixture for {name}")


OPT_IN = ["DKS", "DSMC", "Lardoon", "ModManager", "Pretense", "RealWeather", "SkyEye", "Sneaker", "VoiceChat"]


@pytest.mark.parametrize("name", OPT_IN)
def test_each_opt_in_answers_the_same_value_as_render(monkeypatch, name):
    cls, ext = build_opt_in(monkeypatch, name)

    assert "render_public" in cls.__dict__, f"{name} must opt in"
    assert cls.render_public is not Extension.render_public

    discord = asyncio.run(ext.render())
    public = asyncio.run(ext.render_public())

    assert public == discord, f"{name}: public tile must show what Discord shows"
    assert public["value"]


# --------------------------------------------------------------------------------------------------
# 4. the facade: one method, one switch
# --------------------------------------------------------------------------------------------------
def test_the_impl_calls_render_public_only_when_public_is_set():
    server = object.__new__(ServerImpl)
    ext = Recorder("SRS")
    server.extensions = {"SRS": ext}

    assert asyncio.run(ServerImpl.render_extensions(server, public=True)) == [
        {"name": "SRS", "withheld": 0}
    ]
    assert ext.calls == ["render_public"]

    ext.calls.clear()
    assert asyncio.run(ServerImpl.render_extensions(server)) == [
        {"name": "SRS", "value": "discord-side"}
    ]
    assert ext.calls == ["render"]


def test_the_impl_skips_an_extension_that_publishes_nothing_publicly():
    server = object.__new__(ServerImpl)
    withheld = Recorder("Withheld", raises=True)
    shown = Recorder("Shown")
    server.extensions = {"Withheld": withheld, "Shown": shown}

    assert asyncio.run(ServerImpl.render_extensions(server, public=True)) == [
        {"name": "Shown", "withheld": 0}
    ]
    assert withheld.calls == ["render_public"]


def test_the_local_path_is_the_impl_directly():
    """A server this process owns answers from ``ServerImpl`` — no proxy, no wire, no bus."""
    assert type(object.__new__(ServerImpl)).render_extensions is ServerImpl.render_extensions
    assert ServerProxy.render_extensions is not ServerImpl.render_extensions


# --------------------------------------------------------------------------------------------------
# 5. the proxy forwards the switch, and keeps the two answers apart
# --------------------------------------------------------------------------------------------------
def make_proxy(answer: list[dict] | None = None) -> tuple[ServerProxy, Bus]:
    proxy = object.__new__(ServerProxy)
    proxy.name = SERVER_NAME
    proxy.node = SimpleNamespace(name="agent-node", slow_system=False)
    proxy.bus = Bus(answer)
    proxy._extensions = None
    proxy._public_extensions = None
    return proxy, proxy.bus


def test_the_proxy_forwards_public_on_the_wire():
    proxy, bus = make_proxy([{"name": "SRS", "withheld": 2}])

    answer = asyncio.run(ServerProxy.render_extensions(proxy, public=True))

    assert answer == [{"name": "SRS", "withheld": 2}]
    payload = bus.sent[-1]["message"]
    assert payload["method"] == "render_extensions"
    assert payload["server_name"] == SERVER_NAME
    assert payload["params"] == {"public": True}
    assert bus.sent[-1]["node"] == "agent-node"


def test_the_proxy_asks_for_the_discord_render_when_public_is_not_set():
    proxy, bus = make_proxy([{"name": "SRS", "value": "203.0.113.10:5002"}])

    asyncio.run(ServerProxy.render_extensions(proxy))

    assert bus.sent[-1]["message"]["params"] == {"public": False}


def test_the_proxy_keeps_the_public_and_discord_answers_apart():
    proxy, bus = make_proxy()
    answers = {
        "public": [{"name": "SRS", "withheld": 2}],
        "discord": [{"name": "SRS", "value": "203.0.113.10:5002"}]
    }

    async def send_to_node_sync(message: dict, timeout: int | None = None, *, node=None):
        bus.sent.append({"message": dict(message), "timeout": timeout, "node": node})
        return answers["public" if message["params"]["public"] else "discord"]

    proxy.bus = SimpleNamespace(send_to_node_sync=send_to_node_sync)

    public = asyncio.run(ServerProxy.render_extensions(proxy, public=True))
    discord = asyncio.run(ServerProxy.render_extensions(proxy))
    again_public = asyncio.run(ServerProxy.render_extensions(proxy, public=True))

    assert public == [{"name": "SRS", "withheld": 2}]
    assert discord == [{"name": "SRS", "value": "203.0.113.10:5002"}]
    assert again_public == public
    assert len(bus.sent) == 2, "each rendering is fetched once and cached on its own"
