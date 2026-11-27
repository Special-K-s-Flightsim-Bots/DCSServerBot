"""DKS for DCS Admins: a hoster's customer links DKS to the servers they manage, and only those.

Pinned here, through the plugin's REAL routes (``/register_dks``, ``/install_dks_exporter``) with real
ES256 tokens, and through the Discord command callbacks with interaction doubles:

* ``/dks register`` is open to ``Admin`` and ``DCS Admin``; an Admin's link is bot-wide, a DCS
  Admin's link is scoped to the servers they manage, and a DCS Admin who manages none is told so;
* the registration link is always ephemeral — an unclaimed link lets whoever opens it link those
  servers to their own squadron;
* one-time keys are per user: two customers registering at once do not cancel each other, a key
  works once, an expired key is dead, and a token DKS did not sign cannot use up a valid key;
* a claim stores the key where it belongs — the bot-wide ``auth.jwt.key`` or the owner's entry in
  ``auth.jwt.links`` — without touching the other half, and a re-registration replaces in place;
* ``/install_dks_exporter`` through a scoped link answers 404 for a server out of its reach;
* ``/dks install_permission`` lets a DCS Admin act on their own servers only;
* the per-command role override in ``dks.yaml`` still replaces the role check.
"""
from __future__ import annotations

import logging
import time
import types
from pathlib import Path
from typing import Any, cast

import discord
import jwt
import pytest
from discord import app_commands
from fastapi import FastAPI

from conftest import asgi_client
from core import DEFAULT_TAG, utils
from core.utils.yaml_helper import new_yaml
from dks_links_fixtures import (ADMIN_ID, ALPHA_ID, ALPHA_KEY, BOT_WIDE_KEY, BRAVO_ID, BRAVO_KEY, MEMBER_ID,
                                OTHER_PRIVATE_KEY, FakeJWKClient, dks_token, fake_bot, members)
from plugins.dks import commands as dks_commands
from plugins.dks.commands import DKS


# ------------------------------------------------------------------------------------ doubles

class _RestAPICog:
    """The RestAPI cog as the DKS plugin sees it: its config, read from disk like after a reload."""

    def __init__(self, path: Path):
        self.path = path

    def get_config(self, *args, **kwargs) -> dict:
        if not self.path.exists():
            return {}
        return (new_yaml().load(self.path.read_text(encoding="utf-8")) or {}).get(DEFAULT_TAG, {})


def _plugin(tmp_path: Path, *, restapi_yaml: dict | None = None, dks_yaml: dict | None = None,
            bot: Any = None) -> DKS:
    (tmp_path / "plugins").mkdir(exist_ok=True)
    restapi_path = tmp_path / "plugins" / "restapi.yaml"
    if restapi_yaml is not None:
        with restapi_path.open("w", encoding="utf-8") as f:
            new_yaml().dump(restapi_yaml, f)
    dks_path = tmp_path / "plugins" / "dks.yaml"
    if dks_yaml is not None:
        with dks_path.open("w", encoding="utf-8") as f:
            new_yaml().dump(dks_yaml, f)

    plugin = object.__new__(DKS)
    plugin.app = FastAPI()
    plugin.router = None
    plugin._included = []
    plugin._pending = {}
    plugin._config = {}
    plugin.eventlistener = None
    plugin.__cog_name__ = "DKS"
    plugin.plugin_name = "dks"
    plugin.log = logging.getLogger("tests.dks.dcsadmin")
    bot = bot or fake_bot()
    plugin.reloads = []

    async def reload_plugin(name: str) -> bool:
        plugin.reloads.append(name)
        return True

    bot.reload_plugin = reload_plugin
    bot.cogs = {"RestAPI": _RestAPICog(restapi_path)}
    plugin.bot = cast(Any, bot)
    plugin.node = cast(Any, types.SimpleNamespace(config_dir=str(tmp_path), guild_id=1, restart=None))

    def read_locals() -> dict:
        return (new_yaml().load(dks_path.read_text(encoding="utf-8")) or {}) if dks_path.exists() else {}

    plugin.read_locals = read_locals
    plugin.locals = read_locals()
    # the per-instance merge of the plugin config, as Plugin.get_config does for these two keys
    plugin.get_config = lambda server=None, **kw: (
        plugin.locals.get(server.instance.name, {}) if server else plugin.locals.get(DEFAULT_TAG, {}))
    return plugin


def _restapi_yaml(tmp_path: Path) -> dict:
    return new_yaml().load((tmp_path / "plugins" / "restapi.yaml").read_text(encoding="utf-8"))


def _jwt(tmp_path: Path) -> dict:
    return _restapi_yaml(tmp_path)[DEFAULT_TAG]["auth"].get("jwt") or {}


@pytest.fixture(autouse=True)
def offline_jwks(monkeypatch):
    monkeypatch.setattr(jwt, "PyJWKClient", FakeJWKClient)


async def _claim(plugin: DKS, otp: str, key: str, **kwargs):
    plugin.register_routes()
    async with asgi_client(plugin.app) as client:
        return await client.post("/register_dks", headers={"Authorization": f"Bearer {dks_token(key=key, otp=otp, **kwargs)}"},
                                 json={"jwks": {"keys": []}})


class _Response:
    def __init__(self, log: list):
        self.log = log
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, **kwargs):
        self._done = True
        self.log.append(("defer", {"ephemeral": ephemeral}))

    async def send_message(self, content=None, *, ephemeral: bool = False, **kwargs):
        self._done = True
        self.log.append(("send_message", {"content": content, "ephemeral": ephemeral, **kwargs}))


class _Followup:
    def __init__(self, log: list):
        self.log = log

    async def send(self, content=None, *, ephemeral: bool = False, **kwargs):
        self.log.append(("followup", {"content": content, "ephemeral": ephemeral, **kwargs}))
        return types.SimpleNamespace(edit=_noop)


async def _noop(*args, **kwargs):
    return None


def _interaction(plugin: DKS, user_id: int) -> Any:
    log: list = []
    return types.SimpleNamespace(
        user=plugin.bot.guild_members[user_id],
        client=plugin.bot,
        response=_Response(log),
        followup=_Followup(log),
        log=log,
    )


@pytest.fixture
def webservice(monkeypatch):
    service = types.SimpleNamespace(
        node=types.SimpleNamespace(public_ip="203.0.113.10"),
        get_ports=lambda: {"WebService": types.SimpleNamespace(port=9876)},
    )
    monkeypatch.setattr(dks_commands.ServiceRegistry, "get", staticmethod(lambda cls: service))
    return service


@pytest.fixture
def answer_yes(monkeypatch):
    asked: list = []

    async def yn_question(ctx, question, *, message=None, embed=None, ephemeral=True):
        asked.append({"question": question, "message": message, "ephemeral": ephemeral})
        return True

    monkeypatch.setattr(utils, "yn_question", yn_question)
    return asked


def _otp_of(interaction) -> str:
    sent = [entry for kind, entry in interaction.log if kind == "followup" and entry.get("view")]
    assert len(sent) == 1, interaction.log
    url = sent[0]["view"].children[0].url
    return url.split("otp=")[1].split("&")[0]


# ------------------------------------------------------------------------- who may register what

@pytest.mark.asyncio
async def test_a_dcs_admin_registers_a_link_scoped_to_their_servers(tmp_path, webservice):
    plugin = _plugin(tmp_path, restapi_yaml={DEFAULT_TAG: {}})
    interaction = _interaction(plugin, ALPHA_ID)

    await DKS.register.callback(plugin, interaction)

    otp = _otp_of(interaction)
    pending = plugin._pending[otp]
    assert (pending["user_id"], pending["owner"]) == (ALPHA_ID, ALPHA_ID)
    kind, sent = interaction.log[-1]
    assert sent["ephemeral"] is True, "the registration link must never be posted in the channel"
    reach = next(f.value for f in sent["embed"].fields if f.name == "Servers")
    assert reach == "Alpha Ops, Alpha Training, Public"


@pytest.mark.asyncio
async def test_an_admin_registers_the_bot_wide_link(tmp_path, webservice):
    plugin = _plugin(tmp_path, restapi_yaml={DEFAULT_TAG: {}})
    interaction = _interaction(plugin, ADMIN_ID)
    await DKS.register.callback(plugin, interaction)
    pending = plugin._pending[_otp_of(interaction)]
    assert pending["owner"] is None
    reach = next(f.value for f in interaction.log[-1][1]["embed"].fields if f.name == "Servers")
    assert reach == "All servers"


@pytest.mark.asyncio
async def test_a_dcs_admin_who_manages_no_server_is_told_so(tmp_path, webservice):
    bot = fake_bot(servers={})
    plugin = _plugin(tmp_path, restapi_yaml={DEFAULT_TAG: {}}, bot=bot)
    interaction = _interaction(plugin, ALPHA_ID)
    await DKS.register.callback(plugin, interaction)
    assert plugin._pending == {}
    assert interaction.log == [("send_message", {
        "content": "You don't manage any server on this bot.", "ephemeral": True})]


@pytest.mark.asyncio
async def test_re_registering_asks_first_and_stays_ephemeral(tmp_path, webservice, answer_yes):
    plugin = _plugin(tmp_path, restapi_yaml={DEFAULT_TAG: {"auth": {"jwt": {
        "jwks_url": "x", "links": [{"key": ALPHA_KEY, "owner": ALPHA_ID}]}}}})
    interaction = _interaction(plugin, ALPHA_ID)
    await DKS.register.callback(plugin, interaction)
    assert [(x["question"], x["message"]) for x in answer_yes] == [
        ("Do you want to overwrite it?", "You have linked your servers to DKS already.")]
    assert interaction.log[-1][1]["ephemeral"] is True
    # Bravo has no link yet: no question
    other = _interaction(plugin, BRAVO_ID)
    await DKS.register.callback(plugin, other)
    assert len(answer_yes) == 1


def test_the_role_checks_admit_admin_and_dcs_admin_only(tmp_path):
    plugin = _plugin(tmp_path)
    for command in (DKS.register, DKS.install_permission):
        check = command.checks[0]
        for user_id, allowed in ((ADMIN_ID, True), (ALPHA_ID, True), (MEMBER_ID, False)):
            interaction = types.SimpleNamespace(user=plugin.bot.guild_members[user_id], client=plugin.bot)
            assert check(interaction) is allowed, (command.name, user_id)


def test_the_role_override_in_dks_yaml_still_replaces_the_check(tmp_path):
    """``commands: dks: register: roles: [...]`` (core/plugin.py change_commands) keeps working."""
    helper = _plugin(tmp_path)
    # Cog.__new__ gives the instance its own copies of the commands, as a loaded plugin has
    cog = DKS.__new__(DKS)
    cog.log = helper.log
    cog.__cog_name__ = "DKS"
    cog.change_commands({"dks": {"register": {"roles": ["Admin"]}}}, {x.name: x for x in cog.get_app_commands()})
    group = next(cmd for cmd in cog.get_app_commands() if cmd.name == "dks")
    register = next(cmd for cmd in group.commands if cmd.name == "register")
    plugin = helper
    assert len(register.checks) == 1
    check = register.checks[0]
    alpha = types.SimpleNamespace(user=plugin.bot.guild_members[ALPHA_ID], client=plugin.bot)
    admin = types.SimpleNamespace(user=plugin.bot.guild_members[ADMIN_ID], client=plugin.bot)
    import asyncio
    assert asyncio.run(check(alpha)) is False
    assert asyncio.run(check(admin)) is True


# ---------------------------------------------------------------------------- one-time keys

def test_one_time_keys_are_per_user(tmp_path):
    plugin = _plugin(tmp_path)
    alpha = plugin.generate_otp(ALPHA_ID, ALPHA_ID)
    bravo = plugin.generate_otp(BRAVO_ID, BRAVO_ID)
    assert set(plugin._pending) == {alpha, bravo}, "a second customer does not cancel the first"
    alpha_again = plugin.generate_otp(ALPHA_ID, ALPHA_ID)
    assert set(plugin._pending) == {alpha_again, bravo}, "re-running replaces only one's own link"
    assert plugin.claim_otp(alpha) is None
    assert plugin.claim_otp(bravo)["owner"] == BRAVO_ID
    assert plugin.claim_otp(bravo) is None, "a one-time key works once"


def test_an_expired_one_time_key_is_dead(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    otp = plugin.generate_otp(ALPHA_ID, ALPHA_ID, ttl=300)
    now = time.monotonic()
    monkeypatch.setattr(dks_commands.time, "monotonic", lambda: now + 301)
    assert plugin.claim_otp(otp) is None


# ---------------------------------------------------------------------------------- the claim

@pytest.mark.asyncio
async def test_a_scoped_claim_adds_a_link_and_keeps_the_bot_wide_key(tmp_path):
    plugin = _plugin(tmp_path, restapi_yaml={DEFAULT_TAG: {"prefix": "", "auth": {
        "api_key": "keep-me", "jwt": {"jwks_url": "x", "key": BOT_WIDE_KEY}}}})
    otp = plugin.generate_otp(ALPHA_ID, ALPHA_ID)

    response = await _claim(plugin, otp, ALPHA_KEY)

    assert response.status_code == 200, response.text
    auth = _restapi_yaml(tmp_path)[DEFAULT_TAG]["auth"]
    assert auth["api_key"] == "keep-me"
    assert auth["jwt"]["key"] == BOT_WIDE_KEY
    assert auth["jwt"]["jwks_url"] == dks_commands.DKS_JWKS_URL
    assert [dict(x) for x in auth["jwt"]["links"]] == [{"key": ALPHA_KEY, "owner": ALPHA_ID}]
    assert plugin.reloads == ["RestAPI"], "the RestAPI reloads to pick the new key up"


@pytest.mark.asyncio
async def test_an_admin_claim_sets_the_bot_wide_key_and_keeps_the_links(tmp_path):
    plugin = _plugin(tmp_path, restapi_yaml={DEFAULT_TAG: {"auth": {"jwt": {
        "jwks_url": "x", "links": [{"key": ALPHA_KEY, "owner": ALPHA_ID}]}}}})
    otp = plugin.generate_otp(ADMIN_ID, None)
    assert (await _claim(plugin, otp, BOT_WIDE_KEY)).status_code == 200
    jwt_cfg = _jwt(tmp_path)
    assert jwt_cfg["key"] == BOT_WIDE_KEY
    assert [dict(x) for x in jwt_cfg["links"]] == [{"key": ALPHA_KEY, "owner": ALPHA_ID}]


@pytest.mark.asyncio
async def test_re_registering_replaces_only_ones_own_link(tmp_path):
    plugin = _plugin(tmp_path, restapi_yaml={DEFAULT_TAG: {"auth": {"jwt": {"jwks_url": "x", "links": [
        {"key": "old-alpha", "owner": ALPHA_ID}, {"key": BRAVO_KEY, "owner": BRAVO_ID}]}}}})
    otp = plugin.generate_otp(ALPHA_ID, ALPHA_ID)
    assert (await _claim(plugin, otp, ALPHA_KEY)).status_code == 200
    links = sorted((dict(x) for x in _jwt(tmp_path)["links"]), key=lambda x: x["owner"])
    assert links == [{"key": ALPHA_KEY, "owner": ALPHA_ID}, {"key": BRAVO_KEY, "owner": BRAVO_ID}]


@pytest.mark.asyncio
async def test_a_claim_with_a_wrong_or_used_one_time_key_is_refused(tmp_path):
    plugin = _plugin(tmp_path, restapi_yaml={DEFAULT_TAG: {}})
    otp = plugin.generate_otp(ALPHA_ID, ALPHA_ID)
    assert (await _claim(plugin, "not-the-otp", ALPHA_KEY)).status_code == 403
    assert (await _claim(plugin, otp, ALPHA_KEY)).status_code == 200
    assert (await _claim(plugin, otp, ALPHA_KEY)).status_code == 403


@pytest.mark.asyncio
async def test_a_token_dks_did_not_sign_cannot_burn_a_one_time_key(tmp_path):
    plugin = _plugin(tmp_path, restapi_yaml={DEFAULT_TAG: {}})
    otp = plugin.generate_otp(ALPHA_ID, ALPHA_ID)
    assert (await _claim(plugin, otp, "attacker-key", private_key=OTHER_PRIVATE_KEY)).status_code == 403
    assert otp in plugin._pending
    assert (await _claim(plugin, otp, ALPHA_KEY)).status_code == 200
    assert [dict(x) for x in _jwt(tmp_path)["links"]] == [{"key": ALPHA_KEY, "owner": ALPHA_ID}]


# -------------------------------------------------------------------------------- the install

async def _install(plugin: DKS, key: str, server: str):
    plugin.register_routes()
    token = dks_token(key=key, server=server, claimUrl="https://dks.invalid/claim", code="c0de")
    async with asgi_client(plugin.app) as client:
        return await client.post("/install_dks_exporter", headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_a_scoped_link_cannot_install_on_another_customers_server(tmp_path):
    plugin = _plugin(tmp_path, restapi_yaml={DEFAULT_TAG: {"auth": {"jwt": {"jwks_url": "x", "key": BOT_WIDE_KEY,
                     "links": [{"key": ALPHA_KEY, "owner": ALPHA_ID}]}}}},
                     dks_yaml={"bravo_ops": {"allow_install": True}})
    other = await _install(plugin, ALPHA_KEY, "Bravo Ops")
    assert other.status_code == 404, other.text
    # its own server answers the permission rule, not the reach rule
    own = await _install(plugin, ALPHA_KEY, "Alpha Ops")
    assert own.status_code == 409, own.text
    assert "DCS Admin" in own.json()["detail"]
    # the bot-wide key still reaches Bravo's server: past the reach check, into the install itself
    plugin.bot.servers["Bravo Ops"].config_extension = None
    wide = await _install(plugin, BOT_WIDE_KEY, "Bravo Ops")
    assert wide.status_code == 502, wide.text      # the claim URL does not resolve: proof it got that far


@pytest.mark.asyncio
async def test_an_unknown_key_cannot_install(tmp_path):
    plugin = _plugin(tmp_path, restapi_yaml={DEFAULT_TAG: {"auth": {"jwt": {"jwks_url": "x",
                     "links": [{"key": ALPHA_KEY, "owner": ALPHA_ID}]}}}})
    assert (await _install(plugin, BRAVO_KEY, "Bravo Ops")).status_code == 403


# --------------------------------------------------------------------- install_permission scope

def test_the_server_picker_narrows_install_permission_to_ones_own_servers(tmp_path):
    plugin = _plugin(tmp_path)
    transformer = utils.ServerTransformer()
    for user_id, server, ok in ((ALPHA_ID, "Alpha Ops", True), (ALPHA_ID, "Bravo Ops", False),
                                (ALPHA_ID, "Public", True), (ADMIN_ID, "Bravo Ops", True)):
        interaction = types.SimpleNamespace(user=plugin.bot.guild_members[user_id], client=plugin.bot,
                                            command=DKS.install_permission)
        assert transformer.is_admin(interaction) is True, "the picker treats the command as DCS-Admin-scoped"
        import asyncio
        if ok:
            assert asyncio.run(transformer.transform(interaction, server)).name == server
        else:
            with pytest.raises(app_commands.TransformerError):
                asyncio.run(transformer.transform(interaction, server))


@pytest.mark.asyncio
async def test_install_permission_writes_only_for_a_server_in_reach(tmp_path):
    plugin = _plugin(tmp_path, dks_yaml={"commands": {"dks": {"register": {"roles": ["DCS Admin"]}}}})
    interaction = _interaction(plugin, ALPHA_ID)
    await DKS.install_permission.callback(plugin, interaction, plugin.bot.servers["Bravo Ops"], True)
    assert interaction.log == [("send_message", {"content": "Server Bravo Ops not found.", "ephemeral": True})]
    interaction = _interaction(plugin, ALPHA_ID)
    await DKS.install_permission.callback(plugin, interaction, plugin.bot.servers["Alpha Ops"], True)
    data = new_yaml().load((tmp_path / "plugins" / "dks.yaml").read_text(encoding="utf-8"))
    assert data["alpha_ops"] == {"allow_install": True}
    assert "bravo_ops" not in data
    assert data["commands"] == {"dks": {"register": {"roles": ["DCS Admin"]}}}, "the overrides survive"

