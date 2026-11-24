"""A console marked HTTPS-only, opened over plain HTTP — the wizard and the page must not hide it.

Two halves of one defect:

* ``/webadmin setup`` left ``session.https_only`` unset, and the shipped derivation marks the cookie
  Secure on any non-loopback bind — so a wizard-made local-user install could not hold a sign-in at
  all over ``http://<lan-ip>``, while the same install worked on ``http://127.0.0.1`` (a browser
  treats loopback as a trustworthy origin).
* Nothing said so: the login page re-rendered silently, so a correct password looked like a dead
  form, and the language control (which needs the session) looked like broken JavaScript.

The wizard now WRITES the flag from the address the operator gave, and the page names the mismatch
when the cookie cannot survive the transport.
"""
from __future__ import annotations

import asyncio
import types

import httpx
import pytest

from conftest import create_app, install_shell
from plugins.admin.webadmin import (FAIL, OK, WARN, Facts, SetupPlan, mode_fields, mutate_for,
                                    status_rows)
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"


def _hash() -> str:
    return local_auth.hash_password(PASSWORD, iterations=local_auth.MIN_ITERATIONS)


def _config(*, https_only: bool | None) -> dict:
    """A webservice.yaml block for a local-user install, with or without an explicit flag."""
    session: dict = {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600}
    if https_only is not None:
        session["https_only"] = https_only
    return {"session": session,
            "auth": {"public_base_url": "http://192.168.1.50:9876",
                     "local": {"enabled": True,
                               "users": [{"username": "admin", "password_hash": _hash(),
                                          "roles": ["Admin"]}]}}}


def _build(config: dict):
    node = types.SimpleNamespace(master=True, config_dir="/tmp")
    app = create_app()
    install_shell(app, node=node, config=config)
    return app


def _get(app, scheme: str) -> str:
    async def run() -> str:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url=f"{scheme}://testserver") as client:
            response = await client.get(auth_routes.LOGIN_PATH)
        assert response.status_code == 200, response.text
        return response.text

    return asyncio.run(run())


# ------------------------------------------------------------------- the wizard writes the flag

@pytest.mark.parametrize("url,expected", [("https://bot.example.com", True),
                                          ("http://192.168.1.50:9876", False)])
def test_the_wizard_writes_https_only_from_the_public_address(url, expected):
    """The cookie's Secure flag is WRITTEN from the address, never left to the bind derivation.

    A wizard-made local-user install on ``http://<lan-ip>`` used to inherit the derived ``True``,
    which marks the cookie Secure on a non-loopback bind: the login POST succeeded and every
    following request was refused, with nothing on the page to say why.
    """
    document = {"DEFAULT": {"listen": "0.0.0.0", "port": 9876, "frontend": False}}
    mutate_for(SetupPlan(port=9876, public_base_url=url, listen="0.0.0.0"))(document)
    assert document["DEFAULT"]["session"]["https_only"] is expected


def test_the_wizard_keeps_an_existing_secret():
    """Only the flag is decided here — an operator's session key is never replaced."""
    document = {"DEFAULT": {"listen": "0.0.0.0", "port": 9876,
                            "session": {"secret": "0123456789abcdef0123456789abcdef"}}}
    mutate_for(SetupPlan(port=9876, public_base_url="https://bot.example.com"))(document)
    assert document["DEFAULT"]["session"]["secret"] == "0123456789abcdef0123456789abcdef"
    assert document["DEFAULT"]["session"]["https_only"] is True


# ------------------------------------------------------------------ the page names the mismatch

def test_the_login_page_names_the_mismatch_over_plain_http():
    """https_only + an http request = a sign-in that cannot stick, and the page says so."""
    html = _get(_build(_config(https_only=True)), "http")
    assert auth_routes.HTTPS_MISMATCH_MESSAGE in html
    assert "login-error" in html           # rendered through the page's own refusal slot


def test_the_login_page_is_silent_when_the_proxy_terminates_tls():
    """A correctly terminated install arrives as https and shows nothing — no false alarm."""
    html = _get(_build(_config(https_only=True)), "https")
    assert auth_routes.HTTPS_MISMATCH_MESSAGE not in html


def test_the_login_page_is_silent_when_the_cookie_is_not_secure():
    """Plain HTTP with https_only: false is a working LAN install, not a fault."""
    html = _get(_build(_config(https_only=False)), "http")
    assert auth_routes.HTTPS_MISMATCH_MESSAGE not in html


# ------------------------------------------------------------- the doctor names the mismatch

def _status(*, address: str, https_only=None, listen: str = "0.0.0.0"):
    """The ``/webadmin status`` rows for one address and one session block."""
    session = {"secret": SESSION_SECRET}
    if https_only is not None:
        session["https_only"] = https_only
    document = {"listen": listen, "port": 9876, "frontend": True,
                "auth": {"public_base_url": address},
                "session": session}
    return {row.label: row for row in status_rows(document, Facts(file_present=True))}


def test_the_doctor_fails_a_secure_cookie_on_a_plain_http_address():
    """The derived flag on a non-loopback bind plus an http:// address is the dead-login state."""
    row = _status(address="http://192.168.1.50:9876")["Sign-in cookie"]
    assert row.state == FAIL
    assert "https_only" in row.detail and "192.168.1.50" in row.detail


def test_the_doctor_names_an_explicit_flag_apart_from_the_derived_one():
    """A value the operator wrote is reported as written; silence about it is reported as derived."""
    written = _status(address="http://192.168.1.50:9876", https_only=True)["Sign-in cookie"]
    assert "session.https_only is set" in written.detail
    derived = _status(address="http://192.168.1.50:9876")["Sign-in cookie"]
    assert "non-loopback listen" in derived.detail


def test_the_doctor_accepts_a_plain_http_install_with_the_flag_off():
    """A LAN console on http:// with an explicit false holds its sign-in: nothing to report."""
    assert _status(address="http://192.168.1.50:9876",
                   https_only=False)["Sign-in cookie"].state == OK


def test_the_doctor_accepts_a_tls_install():
    """https:// and a Secure cookie agree — the ordinary reverse-proxy install."""
    assert _status(address="https://dcs.example.com", https_only=True)["Sign-in cookie"].state == OK


def test_the_doctor_warns_when_an_https_address_ships_a_non_secure_cookie():
    """The cookie travels without the Secure flag although users arrive over TLS."""
    assert _status(address="https://dcs.example.com", https_only=False)["Sign-in cookie"].state == WARN


def test_the_doctor_says_nothing_can_be_checked_without_an_address():
    """No usable address means no comparison, and the row says so instead of passing."""
    assert _status(address="")["Sign-in cookie"].state == WARN


# ------------------------------------------------------- the form says plain http is allowed

def test_the_address_field_says_plain_http_is_allowed_without_a_certificate():
    """The label carries the guidance because a modal takes no help text, and the placeholder is
    an http example — the old https one pushed people without a certificate into the dead state."""
    spec = mode_fields("local")["public_base_url"]
    assert "http://" in spec["label"]
    assert spec["placeholder"].startswith("http://")
    assert len(spec["label"]) <= 45, "a Discord modal label is capped at 45 characters"
