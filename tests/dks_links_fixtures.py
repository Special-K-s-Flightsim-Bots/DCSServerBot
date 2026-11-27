"""Shared doubles for the scoped DKS link tests (``test_restapi_scoped_links``, ``test_dks_dcs_admin``).

The tokens are REAL ES256 JWTs, signed the way DKS signs them (``src/lib/crypto/integration-signing.ts``:
``iss=dks``, ``aud=dcssb``, a ``kid`` header, ``iat`` backdated), and verified by the product's own
``jwt.decode`` path; only the JWKS fetch is replaced (:class:`FakeJWKClient`), so no network is touched.

The members are ``discord.Member`` subclasses because :func:`core.utils.discord.check_roles` refuses
anything that is not one.
"""
from __future__ import annotations

import time
import types
from typing import Any

import discord
import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from core import Status

KID = "dks-test-kid"
DKS_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())

BOT_WIDE_KEY = "bot-wide-key-0123456789"
ALPHA_KEY = "alpha-customer-key-0123456789"
BRAVO_KEY = "bravo-customer-key-0123456789"

ADMIN_ID = 100
ALPHA_ID = 111          # DCS Admin + role "Alpha Wing"
BRAVO_ID = 222          # DCS Admin + role "Bravo Wing"
MEMBER_ID = 333         # no bot role at all
OWNER_ID = 999


class FakeJWKClient:
    """``jwt.PyJWKClient`` without the fetch: hands out DKS's public key for its kid."""

    def __init__(self, url: str, *args: Any, **kwargs: Any):
        self.url = url

    def get_signing_key(self, kid: str):
        if kid != KID:
            raise jwt.PyJWKClientError(f"Unable to find a signing key that matches: {kid}")
        return types.SimpleNamespace(key=DKS_PRIVATE_KEY.public_key(), algorithm_name="ES256")


def dks_token(*, key: str | None = None, otp: str | None = None, private_key=None, kid: str = KID,
              **claims: Any) -> str:
    """A token as DKS mints it (integration-signing.ts)."""
    now = int(time.time())
    payload: dict[str, Any] = {"iss": "dks", "aud": "dcssb", "iat": now - 60, "exp": now + 300}
    if key is not None:
        payload["key"] = key
    if otp is not None:
        payload["otp"] = otp
    payload.update(claims)
    return jwt.encode(payload, private_key or DKS_PRIVATE_KEY, algorithm="ES256", headers={"kid": kid})


class FakeRole:
    def __init__(self, role_id: int, name: str):
        self.id = role_id
        self.name = name


ROLE_ADMIN = FakeRole(1, "Admin")
ROLE_DCS_ADMIN = FakeRole(2, "DCS Admin")
ROLE_ALPHA = FakeRole(3, "Alpha Wing")
ROLE_BRAVO = FakeRole(4, "Bravo Wing")


class FakeMember(discord.Member):
    """A ``discord.Member`` that carries only what the role rules read: an id and roles."""

    def __init__(self, member_id: int, roles: list[FakeRole]):  # noqa: no super(): no gateway state
        self._fake_id = member_id
        self._fake_roles = list(roles)

    @property
    def id(self) -> int:  # type: ignore[override]
        return self._fake_id

    @property
    def roles(self) -> list[FakeRole]:  # type: ignore[override]
        return self._fake_roles

    @property
    def mention(self) -> str:  # type: ignore[override]
        return f"<@{self._fake_id}>"

    def __repr__(self) -> str:
        return f"<FakeMember {self._fake_id}>"


def members() -> dict[int, FakeMember]:
    return {
        ADMIN_ID: FakeMember(ADMIN_ID, [ROLE_ADMIN]),
        ALPHA_ID: FakeMember(ALPHA_ID, [ROLE_DCS_ADMIN, ROLE_ALPHA]),
        BRAVO_ID: FakeMember(BRAVO_ID, [ROLE_DCS_ADMIN, ROLE_BRAVO]),
        MEMBER_ID: FakeMember(MEMBER_ID, []),
        OWNER_ID: FakeMember(OWNER_ID, []),
    }


def fake_server(name: str, managed_by: list[str] | None, *, password: str = "") -> Any:
    """Enough of a ``Server`` for /servers, /airbases and the DKS install route."""
    return types.SimpleNamespace(
        name=name,
        display_name=name,
        locals={"managed_by": managed_by} if managed_by else {},
        settings={"password": password, "description": ""},
        status=Status.SHUTDOWN,
        current_mission=None,
        restart_time=None,
        maintenance=False,
        node=types.SimpleNamespace(public_ip="203.0.113.10", name="node1"),
        instance=types.SimpleNamespace(name=name.lower().replace(" ", "_")),
        get_active_players=lambda *args, **kwargs: [],
    )


def fake_servers() -> dict[str, Any]:
    """A hoster: two customers' servers, tagged, and the hoster's own untagged one."""
    return {
        "Alpha Training": fake_server("Alpha Training", ["Alpha Wing"], password="alpha-pw"),
        "Alpha Ops": fake_server("Alpha Ops", ["Alpha Wing"], password="alpha-ops-pw"),
        "Bravo Ops": fake_server("Bravo Ops", ["Bravo Wing"], password="bravo-pw"),
        "Public": fake_server("Public", None),
    }


def fake_bot(*, servers: dict[str, Any] | None = None, guild_members: dict[int, FakeMember] | None = None,
             restrict_owner: bool = False) -> Any:
    guild_members = members() if guild_members is None else guild_members

    async def get_member(user_id, fetch: bool = True):
        return guild_members.get(int(user_id))

    return types.SimpleNamespace(
        servers=fake_servers() if servers is None else servers,
        roles={"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["DCS"]},
        owner_id=OWNER_ID,
        get_member=get_member,
        guild_members=guild_members,
        node=types.SimpleNamespace(locals={"restrict_owner": restrict_owner}),
    )


def jwt_config(*, bot_wide: bool = True, links: list[dict] | None = None) -> dict:
    config: dict[str, Any] = {"jwks_url": "https://jwks.invalid/api/pubkey"}
    if bot_wide:
        config["key"] = BOT_WIDE_KEY
    config["links"] = [{"key": ALPHA_KEY, "owner": ALPHA_ID},
                       {"key": BRAVO_KEY, "owner": BRAVO_ID}] if links is None else links
    return config
