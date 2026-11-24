"""Session cookie policy and the CSRF helpers.

The cookie policy is explicit configuration -- ``same_site`` defaults to ``lax`` (load-bearing for the
OAuth callback) -- and a CSRF token is accepted rather than spent."""
from __future__ import annotations

import logging

import pytest
from fastapi import APIRouter, Depends, Request

from conftest import asgi_client, create_app, install_shell, permissions, session

SECRET = "s" * 40


def _session_router() -> APIRouter:
    router = APIRouter()

    @router.get("/probe/token")
    async def probe_token(request: Request):
        request.session["seen"] = request.session.get("seen", 0) + 1
        return {"token": session.get_csrf_token(request)}

    @router.post("/probe/write", dependencies=[Depends(session.csrf_protect)])
    async def probe_write():
        return {"ok": True}

    return router


def _app_with_session_router(config: dict | None = None):
    app = create_app()
    registrar = install_shell(app, node=None, config=config)
    registrar.register_pages("probe", _session_router(),
                             capabilities={"/probe/token": permissions.PUBLIC,
                                           "/probe/write": permissions.PUBLIC})
    return app


# --------------------------------------------------------------------- cookie policy

@pytest.mark.asyncio
async def test_cookie_attributes_come_from_explicit_config():
    app = _app_with_session_router({"session": {
        "secret": SECRET, "cookie_name": "dcssb_admin", "max_age": 600, "https_only": True,
        "same_site": "lax"}})
    async with asgi_client(app) as client:
        response = await client.get("/probe/token")
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("dcssb_admin=")
    assert "Max-Age=600" in cookie
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert "secure" in cookie.lower(), "https_only: true must reach the cookie"


@pytest.mark.asyncio
async def test_the_default_cookie_is_secure_and_the_ephemeral_secret_warns(caplog):
    """The default is derived from the bind address, not a fixed `False`: with no `https_only` and
    no `listen` (the service default is `0.0.0.0`, see service.py:44) the console may be reachable
    beyond loopback, so the cookie is Secure unless an operator says otherwise explicitly."""
    app = _app_with_session_router({})
    with caplog.at_level(logging.WARNING):
        async with asgi_client(app) as client:
            response = await client.get("/probe/token")
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{session.SESSION_COOKIE}=")
    assert "samesite=lax" in cookie.lower()
    assert "secure" in cookie.lower(), \
        "an unset https_only on a possibly network-reachable console must not mean 'off'"
    assert any("ephemeral key" in record.getMessage() for record in caplog.records), \
        "an unset session.secret must be a warning, not a silent one"


def test_ephemeral_secrets_differ_between_installs():
    assert session.session_settings({})["secret"] != session.session_settings({})["secret"]


def test_configured_secret_wins():
    assert session.session_settings({"session": {"secret": SECRET}})["secret"] == SECRET


def test_same_site_none_requires_https_only():
    with pytest.raises(ValueError) as excinfo:
        session.session_settings({"listen": "127.0.0.1", "session": {"same_site": "none"}})
    assert "https_only" in str(excinfo.value)
    assert session.session_settings(
        {"session": {"same_site": "none", "https_only": True}})["same_site"] == "none"


def test_an_unknown_same_site_value_is_refused():
    with pytest.raises(ValueError):
        session.session_settings({"session": {"same_site": "sometimes"}})


def test_leaving_lax_is_warned_about(caplog):
    with caplog.at_level(logging.WARNING):
        session.session_settings({"session": {"same_site": "strict", "secret": SECRET}})
    assert any("lax" in record.getMessage() for record in caplog.records)


def test_a_non_positive_max_age_is_refused():
    with pytest.raises(ValueError):
        session.session_settings({"session": {"max_age": 0}})


# https_only: unset is never "off"

def test_an_unset_https_only_is_derived_from_the_bind_address():
    """`listen` decides: only a loopback-only console may send the cookie without Secure (a Secure
    cookie cannot be returned over plain HTTP, so forcing it there breaks the login for the one
    deployment that cannot leak it). An absent `listen` is the service's own default (0.0.0.0)."""
    assert session.session_settings({})["https_only"] is True
    assert session.session_settings({"listen": "0.0.0.0"})["https_only"] is True
    assert session.session_settings({"listen": "192.168.178.22"})["https_only"] is True
    assert session.session_settings({"listen": "127.0.0.1"})["https_only"] is False
    assert session.session_settings({"listen": "localhost"})["https_only"] is False
    assert session.session_settings({"listen": "::1"})["https_only"] is False


@pytest.mark.parametrize("listen,expected", [
    ("127.0.0.1", False),
    ("127.0.0.1:9876", False),
    ("localhost", False),
    ("localhost:9876", False),
    ("[::1]", False),
    ("[::1]:9876", False),
    ("::1", False),
    ("0.0.0.0", True),
    ("0.0.0.0:9876", True),
    ("::", True),
    ("192.168.178.22", True),
    ("192.168.178.22:9876", True),
], ids=["v4-loopback", "v4-loopback-port", "localhost", "localhost-port", "v6-loopback",
        "v6-loopback-port", "v6-loopback-bare", "wildcard", "wildcard-port", "v6-any",
        "real-address", "real-address-port"])
def test_the_bind_address_is_parsed_in_every_form_the_config_allows(listen, expected):
    """`listen` is a bind address, so it may carry a port (`host:port`), an IPv6 literal may be
    bracketed (`[::1]` / `[::1]:9876`), and it may be a name rather than an address. Every loopback
    spelling derived `Secure: False`; every network-reachable one derived `Secure: True`. The bug
    this pins: `ip_address("127.0.0.1:9941")` raises, the old code read that as "reachable", and a
    plain-HTTP loopback console got a Secure cookie no browser would send back.
    """
    assert session.session_settings({"listen": listen})["https_only"] is expected


@pytest.mark.parametrize("listen", [None, "", "   "])
def test_an_unset_or_empty_listen_derives_from_the_service_default(listen):
    """No bind configured means the service's own default (0.0.0.0) — which is reachable beyond
    loopback, so the cookie is Secure. An empty value is *unset*, never "off"."""
    assert session.session_settings({"listen": listen})["https_only"] is True


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_an_empty_https_only_value_is_unset_never_off(empty):
    """The convention the cookie policy was asked for: an empty value means *unset*, so it falls
    back to the derived default instead of silently disabling Secure."""
    assert session.session_settings(
        {"listen": "0.0.0.0", "session": {"https_only": empty}})["https_only"] is True
    assert session.session_settings(
        {"listen": "127.0.0.1", "session": {"https_only": empty}})["https_only"] is False


def test_a_string_https_only_value_is_parsed_not_truthiness_tested():
    """`bool("0")` is `True` and `bool("false")` is `True`: a config value spelled as a string has
    to be parsed, or "false" quietly turns Secure ON — and "0" would too."""
    assert session.session_settings({"session": {"https_only": "false"}})["https_only"] is False
    assert session.session_settings({"session": {"https_only": "0"}})["https_only"] is False
    assert session.session_settings({"session": {"https_only": "off"}})["https_only"] is False
    assert session.session_settings({"session": {"https_only": "yes"}})["https_only"] is True
    assert session.session_settings({"session": {"https_only": True}})["https_only"] is True
    with pytest.raises(ValueError) as excinfo:
        session.session_settings({"session": {"https_only": "maybe"}})
    assert "https_only" in str(excinfo.value)


def test_an_explicitly_insecure_cookie_on_a_reachable_bind_is_warned_about(caplog):
    """The insecure value is still reachable — deliberately, by writing it down — but never
    quietly: the operator is told what it exposes."""
    with caplog.at_level(logging.WARNING):
        settings = session.session_settings(
            {"listen": "0.0.0.0", "session": {"secret": SECRET, "https_only": False}})
    assert settings["https_only"] is False
    assert any("https_only" in record.getMessage() for record in caplog.records), \
        "an unencrypted session cookie on a reachable interface must be warned about"


def test_the_same_explicit_choice_on_loopback_is_not_warned_about(caplog):
    """The warning has to stay meaningful: on a loopback-only bind the cookie cannot leave the
    machine, and a warning nobody can act on becomes wallpaper."""
    with caplog.at_level(logging.WARNING):
        session.session_settings({"listen": "127.0.0.1",
                                  "session": {"secret": SECRET, "https_only": False}})
    assert not any("https_only" in record.getMessage() for record in caplog.records)


# ------------------------------------- the silent lockout on the default bind (review IMPORTANT-1)

def _lockout_warnings(caplog) -> list[str]:
    """The startup warnings about a Secure cookie that a plain-HTTP browser cannot send back.

    Matched on the phrase that is unique to this situation ("over plain HTTP"), not on the
    "https_only"/"TLS" tokens — the *other* session warning uses those for the opposite situation
    (an explicitly insecure cookie on a reachable bind).
    """
    return [record.getMessage() for record in caplog.records
            if "plain HTTP" in record.getMessage()]


@pytest.mark.parametrize("config", [{}, {"listen": "0.0.0.0"}, {"listen": "192.168.178.22"}],
                         ids=["unset-listen", "wildcard", "a-real-address"])
def test_the_default_bind_without_tls_warns_about_the_silent_lockout(config, caplog):
    """The unreachable default: `listen` defaults to 0.0.0.0, no TLS is configured, so the cookie
    policy derives Secure — but this service never terminates TLS itself, and a browser does not
    send a Secure cookie back over plain HTTP. The login POST therefore appears to succeed and the
    next request is refused, with nothing in the log saying why. One warning, naming the risk and
    both ways out."""
    with caplog.at_level(logging.WARNING):
        settings = session.session_settings({**config, "session": {"secret": SECRET}})
    assert settings["https_only"] is True, "the derivation must still be the secure one"
    warnings = _lockout_warnings(caplog)
    assert len(warnings) == 1, f"exactly one warning, got {warnings}"
    message = warnings[0]
    assert str(config.get("listen") or session.DEFAULT_LISTEN) in message, \
        "the warning must name the bind it is about"
    assert "127.0.0.1" in message, "way out 1: bind to loopback"
    assert "https_only" in message and "TLS" in message, "way out 2: terminate TLS and declare it"


@pytest.mark.parametrize("config", [{"listen": "127.0.0.1"}, {"listen": "localhost"},
                                    {"listen": "::1"}],
                         ids=["loopback", "localhost", "ipv6-loopback"])
def test_a_loopback_bind_never_warns_about_the_lockout(config, caplog):
    """On loopback there is no lockout: the browser reaches the console exactly as it is, so a
    warning here would be wallpaper nobody can act on."""
    with caplog.at_level(logging.WARNING):
        settings = session.session_settings({**config, "session": {"secret": SECRET}})
    assert settings["https_only"] is False
    assert _lockout_warnings(caplog) == []


def test_an_explicit_https_only_is_the_operator_declaring_tls(caplog):
    """The other way out: once `https_only: true` is written down, the operator has declared that
    TLS is terminated in front of the console — the question is answered, so it is not asked."""
    with caplog.at_level(logging.WARNING):
        session.session_settings({"listen": "0.0.0.0",
                                  "session": {"secret": SECRET, "https_only": True}})
    assert _lockout_warnings(caplog) == []


def test_the_explicitly_insecure_cookie_warns_about_the_other_risk_not_this_one(caplog):
    """The two situations are opposites and must not share a sentence: an explicitly insecure cookie
    is warned about by `_warn_about_an_insecure_cookie`, and the lockout warning (which is about a
    Secure cookie that will never come back) must not also fire."""
    with caplog.at_level(logging.WARNING):
        settings = session.session_settings(
            {"listen": "0.0.0.0", "session": {"secret": SECRET, "https_only": False}})
    assert settings["https_only"] is False
    assert any("https_only" in record.getMessage() for record in caplog.records)
    assert _lockout_warnings(caplog) == []


# --------------------------------------------------------------------- CSRF

@pytest.mark.asyncio
async def test_a_loopback_bind_with_a_port_issues_a_cookie_a_browser_can_return():
    """End-to-end through the real cookie path: ``listen: 127.0.0.1:9941`` with ``https_only`` unset
    derives Secure OFF, so the ``Set-Cookie`` a browser receives carries no ``secure``. Before the fix
    it derived TRUE and the cookie could never come back over http."""
    app = _app_with_session_router({"listen": "127.0.0.1:9941", "session": {"secret": SECRET}})
    async with asgi_client(app) as client:
        response = await client.get("/probe/token")
    assert response.status_code == 200
    assert "secure" not in response.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_an_explicit_https_only_still_reaches_the_cookie_on_a_loopback_bind():
    """Precedence is unchanged: an explicit `https_only: true` wins over the loopback derivation
    and still reaches the issued cookie."""
    app = _app_with_session_router({"listen": "127.0.0.1:9941",
                                    "session": {"secret": SECRET, "https_only": True}})
    async with asgi_client(app) as client:
        response = await client.get("/probe/token")
    assert "secure" in response.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_a_write_needs_a_token_and_the_token_is_not_spent():
    app = _app_with_session_router({"session": {"secret": SECRET}})
    async with asgi_client(app) as client:
        token = (await client.get("/probe/token")).json()["token"]
        assert (await client.post("/probe/write", data={session.CSRF_FIELD: token})).status_code == 200
 # a second action on the same page posts the same token: it must still be accepted
        assert (await client.post("/probe/write", data={session.CSRF_FIELD: token})).status_code == 200
        assert (await client.post("/probe/write", data={session.CSRF_FIELD: "nope"})).status_code == 403


@pytest.mark.asyncio
async def test_a_write_without_a_token_is_refused():
    app = _app_with_session_router({"session": {"secret": SECRET}})
    async with asgi_client(app) as client:
        assert (await client.post("/probe/write", data={"x": "1"})).status_code == 403


@pytest.mark.asyncio
async def test_the_token_can_arrive_in_the_header():
    app = _app_with_session_router({"session": {"secret": SECRET}})
    async with asgi_client(app) as client:
        token = (await client.get("/probe/token")).json()["token"]
        response = await client.post("/probe/write", headers={session.CSRF_HEADER: token})
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_second_token_does_not_invalidate_the_first():
    """A page renders one token and every action on it posts that token, so it is accepted, never
    spent: the first page's form keeps working after the same session renders another one."""
    app = _app_with_session_router({"session": {"secret": SECRET}})
    async with asgi_client(app) as client:
        first = (await client.get("/probe/token")).json()["token"]
        token_response = await client.get("/probe/token")
        assert (await client.post("/probe/write",
                                 data={session.CSRF_FIELD: first})).status_code == 200
        assert token_response.status_code == 200


def test_the_session_holds_exactly_one_token_and_never_rotates_it():
    """``_MAX_TOKENS`` was dead (the ring was never appended to). The ceiling is gone and the behaviour is
    pinned here: one token per session, minted on first need, returned forever after."""
    class _Request:
        def __init__(self):
            self.session = {}

    request = _Request()
    minted = session.get_csrf_token(request)
    assert minted
    assert request.session[session._SESSION_KEY] == [minted]

    assert session.get_csrf_token(request) == minted, "a second call must not mint a second token"
    assert len(request.session[session._SESSION_KEY]) == 1, "there is no ring and nothing grows one"
    assert not hasattr(session, "_MAX_TOKENS"), \
        "a token ceiling nothing can reach is a claim nobody can keep"


# ------------------------------------------------------- the signing key has a minimum entropy

def test_a_short_session_secret_is_refused_at_startup():
    """The cookie is signed, not encrypted: its integrity is exactly the key's entropy."""
    with pytest.raises(ValueError) as raised:
        session.session_settings({"session": {"secret": "x"}})
    assert "32" in str(raised.value)


def test_the_placeholder_from_older_samples_is_refused():
    with pytest.raises(ValueError):
        session.session_settings({"session": {"secret": "SECRET_SESSION_KEY"}})


def test_a_secret_at_the_minimum_is_accepted():
    secret = "s" * session.MIN_SECRET_LENGTH
    assert session.session_settings({"session": {"secret": secret}})["secret"] == secret


def test_an_unset_secret_still_falls_back_to_an_ephemeral_key():
    settings = session.session_settings({})
    assert len(settings["secret"]) >= session.MIN_SECRET_LENGTH
    assert session.session_settings({})["secret"] != settings["secret"]
