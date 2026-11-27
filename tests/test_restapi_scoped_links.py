"""Scoped JWT keys on the RestAPI (``plugins/restapi/links.py``): a DCS Admin's DKS link.

A hoster gives each customer ``DCS Admin`` plus a customer role, and tags the customer's servers with
``managed_by``. When such a customer runs ``/dks register``, the key DKS receives is stored as a LINK
owned by that member, and a token carrying it must reach that customer's servers and nothing else:

* ``/servers`` lists only the owner's servers — never another customer's (it carries the password);
* a per-server read answers for a server in reach, and 404 — the "no such server" answer — for one
  that is not, so a link cannot probe for other customers' servers;
* every endpoint outside the read set, and every write, is 403;
* the reach is the owner's CURRENT reach: removing their role, or the member leaving, closes it;
* the bot-wide key, an API key and a keyless loopback call behave exactly as before.

The requests go through the REAL legacy routes (``RestAPI.register_routes``) with real ES256 tokens.
"""
from __future__ import annotations

import logging
import types
from typing import Any, cast

import jwt
import pytest
from fastapi import FastAPI

from conftest import asgi_client
from core import DEFAULT_TAG
from dks_links_fixtures import (ALPHA_ID, ALPHA_KEY, BOT_WIDE_KEY, BRAVO_KEY, OTHER_PRIVATE_KEY, ROLE_ALPHA,
                                FakeJWKClient, FakeMember, dks_token, fake_bot, jwt_config,
                                members)
from plugins.restapi.commands import RestAPI
from plugins.restapi.links import Link, SCOPED_ENDPOINTS, match_key, member_reach, parse_links

ALPHA_SERVERS = {"Alpha Training", "Alpha Ops", "Public"}
ALL_SERVERS = {"Alpha Training", "Alpha Ops", "Bravo Ops", "Public"}


class _FakeLoop:
    def cancel(self) -> None:
        return None

    def get_task(self):
        return None


_LIVE: list = []


def _app(*, app: FastAPI | None = None, bot: Any = None, jwt_cfg: dict | None = None,
         security: dict | None = None, api_key: str | None = None) -> FastAPI:
    app = app or FastAPI()
    api = object.__new__(RestAPI)
    _LIVE[:] = [api]
    auth: dict[str, Any] = {"jwt": jwt_config() if jwt_cfg is None else jwt_cfg}
    if api_key:
        auth["api_key"] = api_key
    api.locals = {DEFAULT_TAG: {
        "auth": auth,
        # every network needs a JWT, so the in-process client (loopback) is checked like DKS is
        "security": {"default": security or {"local": "jwt", "trusted": "jwt", "remote": "jwt"}},
    }}
    api.log = logging.getLogger("tests.restapi.links")
    api.app = app
    api.router = None
    api._included = []
    api._included_v1 = []
    api.refresh_views = cast(Any, _FakeLoop())
    api.eventlistener = None
    api._config = {}
    api.__cog_name__ = "RestAPI"
    api.plugin_name = "restapi"
    api.bot = cast(Any, bot or fake_bot())
    api.bus = cast(Any, types.SimpleNamespace(node=types.SimpleNamespace(instances={})))
    api.node = cast(Any, types.SimpleNamespace(plugins=[], name="node1"))
    api.get_config = lambda *args, **kwargs: api.locals[DEFAULT_TAG]
    api.get_resolved_server = lambda name: (name, api.bot.servers.get(name))
    api.register_routes()
    return app


@pytest.fixture(autouse=True)
def offline_jwks(monkeypatch):
    monkeypatch.setattr(jwt, "PyJWKClient", FakeJWKClient)


def _bearer(key: str, **kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {dks_token(key=key, **kwargs)}"}


async def _get(app: FastAPI, path: str, headers: dict | None = None, **params):
    async with asgi_client(app) as client:
        return await client.get(path, headers=headers or {}, params=params or None)


# ------------------------------------------------------------------------------------ pure rules

def test_parse_links_keeps_the_bot_wide_key_first_and_skips_links_without_an_owner():
    links = parse_links({"key": "k0", "links": [
        {"key": "k1", "owner": 1}, {"key": "k2"}, {"key": "", "owner": 2}, {"key": "k3", "owner": "x"},
        {"key": "k4", "owner": True}, {"key": "k5", "owner": "5"},
    ]})
    assert links == [Link("k0"), Link("k1", 1), Link("k5", 5)]   # k2-k4 name no usable owner


def test_match_key_needs_the_exact_key():
    links = parse_links(jwt_config())
    assert match_key(links, BOT_WIDE_KEY) == Link(BOT_WIDE_KEY)
    assert match_key(links, ALPHA_KEY) == Link(ALPHA_KEY, ALPHA_ID)
    for wrong in (None, "", ALPHA_KEY[:-1], ALPHA_KEY + "x", 42):
        assert match_key(links, wrong) is None


def test_member_reach_is_the_dcs_admin_server_rule():
    bot = fake_bot()
    m = members()
    assert member_reach(bot, m[100]) is None                       # Admin: everything
    assert member_reach(bot, m[999]) is None                       # bot owner: everything
    assert member_reach(bot, m[ALPHA_ID]) == ALPHA_SERVERS          # own tagged + untagged
    assert member_reach(bot, m[222]) == {"Bravo Ops", "Public"}
    assert member_reach(bot, m[333]) == frozenset()                # no bot role
    assert member_reach(bot, None) == frozenset()                  # not in the guild
    # a customer role without DCS Admin is no way in: Discord's commands require the role
    assert member_reach(bot, FakeMember(5, [ROLE_ALPHA])) == frozenset()
    # restrict_owner: the owner is an ordinary member again
    assert member_reach(fake_bot(restrict_owner=True), m[999]) == frozenset()


def test_the_scoped_set_is_only_per_server_reads():
    assert "servers" in SCOPED_ENDPOINTS
    for forbidden in ("instance/stop", "instance/mission/load", "linkme", "getuser", "player_info", "stats",
                      "airbase/capture", "airbase/warehouse/item", "mission/upload", "node/restart"):
        assert forbidden not in SCOPED_ENDPOINTS


# --------------------------------------------------------------------------- /servers is filtered

@pytest.mark.asyncio
async def test_servers_lists_only_the_owners_servers_and_no_other_customers_password():
    response = await _get(_app(), "/servers", _bearer(ALPHA_KEY))
    assert response.status_code == 200, response.text
    assert {s["name"] for s in response.json()} == ALPHA_SERVERS
    assert "bravo-pw" not in response.text


@pytest.mark.asyncio
async def test_servers_with_the_bot_wide_key_lists_everything():
    response = await _get(_app(), "/servers", _bearer(BOT_WIDE_KEY))
    assert response.status_code == 200, response.text
    assert {s["name"] for s in response.json()} == ALL_SERVERS


@pytest.mark.asyncio
async def test_servers_naming_another_customers_server_is_not_found():
    response = await _get(_app(), "/servers", _bearer(ALPHA_KEY), server_name="Bravo Ops")
    assert response.status_code == 404
    ok = await _get(_app(), "/servers", _bearer(ALPHA_KEY), server_name="Alpha Ops")
    assert [s["name"] for s in ok.json()] == ["Alpha Ops"]


# ------------------------------------------------------------------------------ per-server reads

@pytest.mark.asyncio
async def test_a_read_on_a_server_in_reach_answers():
    response = await _get(_app(), "/airbases", _bearer(ALPHA_KEY), server_name="Alpha Training")
    assert response.status_code == 200, response.text
    assert response.json() == {"airbases": []}


@pytest.mark.asyncio
async def test_a_read_on_another_customers_server_answers_like_a_missing_server():
    app = _app()
    other = await _get(app, "/airbases", _bearer(ALPHA_KEY), server_name="Bravo Ops")
    missing = await _get(app, "/airbases", _bearer(BOT_WIDE_KEY), server_name="No Such Server")
    assert other.status_code == missing.status_code == 404
    assert other.json() == {"detail": "Server not found."}


@pytest.mark.asyncio
async def test_a_read_must_name_exactly_one_server():
    app = _app()
    async with asgi_client(app) as client:
        # the handler reads one value; a second one must not slip past the check
        twice = await client.get("/airbases?server_name=Alpha%20Ops&server_name=Bravo%20Ops",
                                 headers=_bearer(ALPHA_KEY))
        reversed_ = await client.get("/airbases?server_name=Bravo%20Ops&server_name=Alpha%20Ops",
                                     headers=_bearer(ALPHA_KEY))
    assert twice.status_code == reversed_.status_code == 404
    none = await _get(app, "/airbases", _bearer(ALPHA_KEY))
    assert none.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,params", [
    ("POST", "/instance/stop", {"server_name": "Alpha Ops"}),
    ("POST", "/instance/mission/restart", {"server_name": "Alpha Ops"}),
    ("GET", "/serverstats", {"server_name": "Alpha Ops"}),
    ("GET", "/squadrons", {}),
    ("POST", "/getuser", {}),
    ("POST", "/airbase/capture", {}),
])
async def test_everything_outside_the_read_set_is_refused(method, path, params):
    async with asgi_client(_app()) as client:
        response = await client.request(method, path, params=params, headers=_bearer(ALPHA_KEY))
    assert response.status_code == 403, (path, response.text)
    assert response.json() == {"detail": "This key is limited to its owner's servers."}


# ------------------------------------------------------------------------- the reach is live

@pytest.mark.asyncio
async def test_removing_the_owners_dcs_admin_role_closes_the_link():
    guild = members()
    app = _app(bot=fake_bot(guild_members=guild))
    assert (await _get(app, "/airbases", _bearer(ALPHA_KEY), server_name="Alpha Ops")).status_code == 200
    guild[ALPHA_ID]._fake_roles = [ROLE_ALPHA]          # DCS Admin taken away
    assert (await _get(app, "/airbases", _bearer(ALPHA_KEY), server_name="Alpha Ops")).status_code == 404
    listing = await _get(app, "/servers", _bearer(ALPHA_KEY))
    assert listing.status_code == 200 and listing.json() == []


@pytest.mark.asyncio
async def test_an_owner_who_left_the_guild_reaches_nothing():
    guild = members()
    del guild[ALPHA_ID]
    app = _app(bot=fake_bot(guild_members=guild))
    assert (await _get(app, "/servers", _bearer(ALPHA_KEY))).json() == []
    assert (await _get(app, "/airbases", _bearer(ALPHA_KEY), server_name="Public")).status_code == 404


@pytest.mark.asyncio
async def test_retagging_a_server_moves_it_between_customers():
    bot = fake_bot()
    app = _app(bot=bot)
    bot.servers["Bravo Ops"].locals["managed_by"] = ["Alpha Wing"]
    listing = await _get(app, "/servers", _bearer(ALPHA_KEY))
    assert {s["name"] for s in listing.json()} == ALL_SERVERS


# ------------------------------------------------------------------------ nothing else changed

@pytest.mark.asyncio
async def test_a_token_dks_did_not_sign_is_refused_even_with_a_valid_key():
    response = await _get(_app(), "/servers", _bearer(ALPHA_KEY, private_key=OTHER_PRIVATE_KEY))
    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid authentication"}


@pytest.mark.asyncio
async def test_an_unknown_key_is_refused():
    response = await _get(_app(), "/servers", _bearer("not-a-link"))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_bot_with_only_links_and_no_bot_wide_key_still_verifies():
    app = _app(jwt_cfg=jwt_config(bot_wide=False))
    assert {s["name"] for s in (await _get(app, "/servers", _bearer(BRAVO_KEY))).json()} == {"Bravo Ops",
                                                                                                 "Public"}
    assert (await _get(app, "/servers", _bearer(BOT_WIDE_KEY))).status_code == 403


@pytest.mark.asyncio
async def test_api_key_and_keyless_loopback_stay_unrestricted():
    app = _app(api_key="the-api-key", security={"local": "api_key", "trusted": "api_key", "remote": "jwt"})
    response = await _get(app, "/servers", {"X-API-Key": "the-api-key"})
    assert {s["name"] for s in response.json()} == ALL_SERVERS
    open_app = _app(security={"local": "none", "trusted": "api_key", "remote": "jwt"})
    response = await _get(open_app, "/instance/missions", server_name="No Such Server")
    assert response.status_code == 404          # the handler's own answer: the gate let it through


@pytest.mark.asyncio
async def test_the_v1_verifier_refuses_a_scoped_key():
    """``/api/v1`` verifies with ApiVerifier, which only knows the bot-wide key: a scoped key is
    refused there instead of being treated as unrestricted."""
    from services.webservice.api_auth import ApiAuthPolicy, ApiVerifier
    verifier = ApiVerifier(ApiAuthPolicy.from_plugin_config({"auth": {"jwt": jwt_config()}}))
    assert await verifier._verify_token(dks_token(key=ALPHA_KEY)) is None
    assert (await verifier._verify_token(dks_token(key=BOT_WIDE_KEY)))["key"] == BOT_WIDE_KEY


@pytest.mark.asyncio
async def test_a_scoped_key_stays_read_only_when_its_owner_becomes_admin():
    """Promotion widens WHICH servers the link reaches, never WHAT it may call."""
    from dks_links_fixtures import ROLE_ADMIN
    guild = members()
    guild[ALPHA_ID]._fake_roles = [ROLE_ADMIN]
    app = _app(bot=fake_bot(guild_members=guild))
    listing = await _get(app, "/servers", _bearer(ALPHA_KEY))
    assert {s["name"] for s in listing.json()} == ALL_SERVERS
    async with asgi_client(app) as client:
        write = await client.post("/airbase/capture", headers=_bearer(ALPHA_KEY))
    assert write.status_code == 403
    assert write.json() == {"detail": "This key is limited to its owner's servers."}


@pytest.mark.asyncio
async def test_a_new_key_works_after_the_restapi_reload_register_triggers():
    """/dks register writes the key, then reloads the RestAPI: the old routes (old keys) must go."""
    app = _app(jwt_cfg={"jwks_url": "x"})                 # running bot, not linked yet
    old = next(iter(_LIVE))
    await old.cog_unload()
    _app(app=app, jwt_cfg=jwt_config(links=[{"key": ALPHA_KEY, "owner": ALPHA_ID}]))
    response = await _get(app, "/servers", _bearer(ALPHA_KEY))
    assert response.status_code == 200, response.text
    assert {s["name"] for s in response.json()} == ALPHA_SERVERS
