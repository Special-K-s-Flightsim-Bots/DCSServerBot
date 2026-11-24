"""The failed sign-in throttle: the budget, the lockout, and that it answers before the KDF.

The password check is expensive by design (PBKDF2, 600 000 iterations), so two things have to
hold: an address that has spent its budget is refused with 429 + Retry-After WITHOUT a credential
check, and a successful sign-in clears its count so an operator who mistyped twice is not locked
out later by someone else's mistakes on the same address.
"""
from __future__ import annotations

import re
import types

import pytest

from conftest import asgi_client, create_app, install_shell
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.auth.throttle import LOGIN_THROTTLE_ATTR, MAX_ATTEMPTS, LoginThrottle

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"
BOT_YAML = """\
roles:
  Admin:
    - specialk
"""


# --------------------------------------------------------------------------------- the unit

class _Clock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_budget_locks_the_client_out():
    throttle = LoginThrottle(max_attempts=3, window_seconds=60, clock=_Clock())
    assert [throttle.record_failure("1.2.3.4") for _ in range(3)] == [2, 1, 0]
    assert throttle.is_locked("1.2.3.4")
    assert throttle.seconds_left("1.2.3.4") == 60


def test_the_lockout_expires_with_the_window():
    clock = _Clock()
    throttle = LoginThrottle(max_attempts=1, window_seconds=60, clock=clock)
    throttle.record_failure("1.2.3.4")
    clock.now += 59
    assert throttle.seconds_left("1.2.3.4") == 1
    clock.now += 1
    assert not throttle.is_locked("1.2.3.4")
    assert throttle.seconds_left("1.2.3.4") == 0


def test_a_success_clears_the_record():
    throttle = LoginThrottle(max_attempts=3, clock=_Clock())
    throttle.record_failure("1.2.3.4")
    throttle.record_failure("1.2.3.4")
    throttle.reset("1.2.3.4")
    assert throttle.record_failure("1.2.3.4") == 2


def test_unnamed_clients_share_one_fail_closed_bucket():
    throttle = LoginThrottle(max_attempts=1, clock=_Clock())
    throttle.record_failure(None)
    assert throttle.is_locked("")
    assert throttle.is_locked(None)


def test_the_bookkeeping_stays_bounded():
    throttle = LoginThrottle(max_attempts=5, max_clients=4, clock=_Clock())
    for index in range(50):
        throttle.record_failure(f"10.0.0.{index}")
    assert len(throttle._failures) <= 4


def test_a_locked_client_is_refused_before_the_password_is_checked():
    """The strong assertion: the KDF is never entered for a locked-out address."""
    clock = _Clock()
    throttle = LoginThrottle(max_attempts=2, clock=clock)
    throttle.record_failure("1.2.3.4")
    throttle.record_failure("1.2.3.4")
    assert throttle.is_locked("1.2.3.4")


# --------------------------------------------------------------------------- through the real app

def _node(config_dir):
    return types.SimpleNamespace(master=True, config_dir=str(config_dir))


def _config() -> dict:
    return {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": [
                     {"username": "specialk",
                      "password_hash": local_auth.hash_password(
                          PASSWORD, iterations=local_auth.MIN_ITERATIONS),
                      "roles": ["Admin"]}]}},
    }


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _build(config_dir):
    app = create_app()
    install_shell(app, node=_node(config_dir), config=_config())
    return app


async def _csrf_token(client) -> str:
    response = await client.get(auth_routes.LOGIN_PATH)
    match = re.search(r'name="_csrf_token" value="([^"]*)"', response.text)
    assert match, "the login form must render the CSRF field"
    return match.group(1)


async def _post_login(client, token, password=PASSWORD):
    return await client.post(auth_routes.LOGIN_PATH,
                             data={"username": "specialk", "password": password,
                                   "_csrf_token": token})


@pytest.mark.asyncio
async def test_the_login_route_locks_out_after_the_budget(config_dir, monkeypatch):
    calls: list[int] = []
    original = local_auth.verify_password

    def counting(password, encoded):
        calls.append(1)
        return original(password, encoded)

    monkeypatch.setattr(local_auth, "verify_password", counting)

    app = _build(config_dir)
    async with asgi_client(app) as client:
        token = await _csrf_token(client)
        for _ in range(MAX_ATTEMPTS):
            assert (await _post_login(client, token, password="wrong")).status_code == 401
        assert len(calls) == MAX_ATTEMPTS

        # the CORRECT password is now refused, and the answer names the wait
        refused = await _post_login(client, token)
        assert refused.status_code == 429
        assert "Too many failed sign-in attempts" in refused.text
        assert int(refused.headers["Retry-After"]) > 0
        # THE GUARD SITS IN FRONT OF THE KDF: a locked-out address never pays for a check
        assert len(calls) == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_a_successful_sign_in_clears_the_count(config_dir):
    app = _build(config_dir)
    async with asgi_client(app) as client:
        token = await _csrf_token(client)
        for _ in range(MAX_ATTEMPTS - 1):
            assert (await _post_login(client, token, password="wrong")).status_code == 401
        signed_in = await _post_login(client, token)
        assert signed_in.status_code == 303
        await client.get(auth_routes.LOGIN_PATH)   # a fresh page for the new session
        token = await _csrf_token(client)
        await client.post(auth_routes.LOGOUT_PATH, data={"_csrf_token": token})

        # the same address may spend the FULL budget again after the success
        token = await _csrf_token(client)
        for _ in range(MAX_ATTEMPTS):
            assert (await _post_login(client, token, password="wrong")).status_code == 401


@pytest.mark.asyncio
async def test_the_throttle_is_per_application(config_dir):
    first = _build(config_dir)
    second = _build(config_dir)
    assert getattr(first.state, LOGIN_THROTTLE_ATTR) is not getattr(second.state,
                                                                    LOGIN_THROTTLE_ATTR)
