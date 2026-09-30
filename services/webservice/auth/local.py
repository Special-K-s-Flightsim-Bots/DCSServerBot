"""The local (username + password) identity backend, and the password-hash helper.

WHERE THE CREDENTIALS LIVE. In ``config/services/webservice.yaml`` (``auth.local.users``), not in
the pickle-based ``config/.secret/`` store: that is the file the service already reads and it is
schema-validated (``services/webservice/schemas/webservice_schema.yaml``), which keeps the pickle
store out of the login path. Only a HASH is stored -- PBKDF2-HMAC-SHA256 via ``cryptography``
(already pinned; no new dependency) -- and it is compared with ``hmac.compare_digest``.

Generate one with::

    python -m services.webservice.auth.local --hash
    python -m services.webservice.auth.local --hash --username specialk --roles Admin

The first form prints the hash; the second also prints the ready-to-paste YAML block. The password
is read with ``getpass`` and never appears on the command line (shell history) or in the log.

WHERE THE CLI RUNS. In the bot's OWN environment (the same venv), so it is allowed to reach the
bot's ``core`` — and does: ``..scope`` (imported below) consumes the shared ``managed_by`` rule from
``core/utils/discord.py``. The argv detach below is therefore NOT about dependencies: it exists so
that the bot's import-time parser (``core/commandline.py``) cannot eat the CLI's flags.

HOW A USER'S ROLES ARE DECIDED. By ``auth.local.users[].roles`` -- role NAMES, validated at
startup against the role names the bot defines (``bot.yaml -> roles:`` keys, read through
``node.config_dir``, plus the bot's built-in names). The phase-1 review (§D.2) notes that
``bot.yaml -> roles:`` has two meanings in this codebase: on the Discord path it maps a bot role
name to the Discord roles that grant it, and on the headless path it maps a role name to MEMBER
names. This backend deliberately does NOT derive a web user's roles from the headless meaning:

* the card's config declares the roles explicitly, and an explicit declaration is the only one that
  also works for a web-only principal who is not listed in ``bot.yaml`` at all;
* deriving them would put a second, independent source of truth next to the declared list -- the
  exact divergence §D.2/F3 warns about, where one path grants Admin and the other silently does
  not;
* ``DummyGuild`` reads ``config/services/bot.yaml`` off a *relative* path, which §D.3 forbids in
  the web layer.

So ``bot.yaml`` is read for exactly one purpose here: to answer "is this a role name this bot
defines?", loudly refusing a name it does not. A typo therefore fails at startup instead of
granting nothing (or, after a careless merge, too much).
"""
from __future__ import annotations

# --------------------------------------------------------------------------- the CLI's own argv
# The ORDER of this snippet is load-bearing: runpy runs this module as ``__main__`` for the
# documented ``python -m services.webservice.auth.local --hash``, and the bot's
# ``core/commandline.py`` parses the REAL ``sys.argv`` the moment anything imports it -- so the
# CLI's flags must be taken out of ``sys.argv`` HERE, above every import below (any of which may
# reach ``core``: the web layer is allowed to import it), or the bot's parser sees ``--hash`` and
# exits with "unrecognized arguments" before ``main()`` ever runs.
if __name__ == "__main__":
    import sys

    _CLI_ARGV: list[str] | None = list(sys.argv[1:])
    sys.argv = sys.argv[:1]
else:
    # imported (the bot's own ``install_auth`` does): never touch the host process's argv.
    _CLI_ARGV = None

import argparse
import getpass
import hmac
import logging
import secrets
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from ruamel.yaml import YAML

from . import AuthBackend, Identity, session_identity_ref
# the shared rule LIVES in ``core/utils/discord.py`` and this module CONSUMES it (through ``..scope``),
# so this import reaches the bot's ``core`` — deliberate: the console runs in the bot's own venv.
# Direction pinned by ``tests/test_layering_direction.py``.
from ..scope import Scope

__all__ = [
    "SCHEME", "DEFAULT_ITERATIONS", "MIN_ITERATIONS", "MAX_ITERATIONS", "MAX_PASSWORD_LENGTH",
    "LocalUser", "LocalAuthBackend", "local_backend", "parse_users",
    "hash_password", "verify_password", "is_password_hash", "parse_password_hash",
    "known_role_names", "read_bot_roles", "BUILTIN_ROLE_NAMES", "main",
]

log = logging.getLogger(__name__)

yaml = YAML(typ="safe")

#: hash scheme marker, the first field of a stored hash
SCHEME = "pbkdf2_sha256"

#: OWASP's current floor for PBKDF2-HMAC-SHA256
DEFAULT_ITERATIONS = 600_000

#: accepted iteration bounds. The lower bound keeps a weakened hash from being accepted silently
#: by accident (tests generate at the floor); the upper bound is a decompression-bomb guard on a
#: configuration value that an attacker who can edit the config would use to hang a login.
MIN_ITERATIONS = 1_000
MAX_ITERATIONS = 5_000_000

KEY_LENGTH = 32
SALT_BYTES = 16

#: the longest password that will be accepted. The password is posted by whoever can reach the
#: login form and every verification runs PBKDF2-HMAC-SHA256 at ``iterations`` (600k by default),
#: so without a cap one request makes the process derive megabytes of key material. 1024 is far
#: above any passphrase a human types and is applied BEFORE the KDF -- never as a TRUNCATION, which
#: would silently verify a secret the person did not type. Refusals name the limit; see
#: :meth:`LocalAuthBackend.verify_credentials` for the logged reason.
MAX_PASSWORD_LENGTH = 1024

#: the role names every install has, whichever bot implementation runs: ``DCSServerBot`` always
#: defines the first four, ``DummyBot`` defines all but ``DCS``, and both synthesise
#: ``GameMaster``/``Alert`` from ``DCS Admin`` when the config does not name them.
BUILTIN_ROLE_NAMES = frozenset({"Admin", "DCS Admin", "DCS", "GameMaster", "Alert"})


# --------------------------------------------------------------------------- password hashing

def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=KEY_LENGTH, salt=salt,
                     iterations=iterations)
    return kdf.derive(password.encode("utf-8"))


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    """``pbkdf2_sha256$<iterations>$<salt-hex>$<digest-hex>``.

    A fresh random salt per call, so two hashes of one password differ.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("a password must be a non-empty string")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"a password must be at most {MAX_PASSWORD_LENGTH} characters, "
                         f"got {len(password)}")
    if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise ValueError(f"iterations must be between {MIN_ITERATIONS} and {MAX_ITERATIONS}, "
                         f"got {iterations}")
    salt = secrets.token_bytes(SALT_BYTES)
    return f"{SCHEME}${iterations}${salt.hex()}${_derive(password, salt, iterations).hex()}"


def parse_password_hash(encoded: Any) -> tuple[int, bytes, bytes] | None:
    """``(iterations, salt, digest)`` for a well-formed hash, ``None`` for anything else.

    Never raises: a malformed stored hash is a refusal, not a 500 on the login page.
    """
    if not isinstance(encoded, str):
        return None
    parts = encoded.strip().split("$")
    if len(parts) != 4 or parts[0] != SCHEME:
        return None
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        digest = bytes.fromhex(parts[3])
    except ValueError:
        return None
    if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        return None
    if len(salt) < 8 or len(digest) != KEY_LENGTH:
        return None
    return iterations, salt, digest


def is_password_hash(encoded: Any) -> bool:
    return parse_password_hash(encoded) is not None


def verify_password(password: str, encoded: Any) -> bool:
    """Constant-time check. ``False`` for a wrong password AND for a malformed stored hash.

    A password longer than :data:`MAX_PASSWORD_LENGTH` is refused HERE, before the KDF runs: the
    length is checked first so no attacker-chosen input can make this function do the work.
    """
    if isinstance(password, str) and len(password) > MAX_PASSWORD_LENGTH:
        return False
    parsed = parse_password_hash(encoded)
    if parsed is None:
        return False
    iterations, salt, expected = parsed
    try:
        actual = _derive(password, salt, iterations)
    except (TypeError, ValueError, UnicodeError):  # pragma: no cover - defensive
        return False
    return hmac.compare_digest(actual, expected)


@lru_cache(maxsize=1)
def _decoy_hash() -> str:
    """A real hash of a throwaway password, verified against when the USERNAME is unknown.

    Without it an unknown username answers measurably faster than a known one, which turns the
    login page into a user-enumeration oracle.
    """
    return hash_password(secrets.token_urlsafe(32))


# --------------------------------------------------------------------------- role names

def read_bot_roles(config_dir: str | Path) -> dict[str, tuple[str, ...]]:
    """``bot.yaml -> roles:`` exactly as declared, read through the GIVEN config dir.

    Deliberately not ``DummyGuild``'s relative ``config/services/bot.yaml`` (§D.3): a request must
    never be able to read a different ``bot.yaml`` than the running process.
    """
    path = Path(config_dir) / "services" / "bot.yaml"
    if not path.is_file():
        log.debug("auth: no bot.yaml at '%s' - only the bot's built-in role names are known.", path)
        return {}
    try:
        data = yaml.load(path.read_text(encoding="utf-8")) or {}
    except Exception as ex:
        log.warning("auth: could not read '%s' (%s) - only the bot's built-in role names are "
                    "known.", path, ex)
        return {}
    roles = data.get("roles") or {}
    return {str(name): tuple(str(member) for member in (members or []))
            for name, members in roles.items()}


def known_role_names(config_dir: str | Path) -> frozenset[str]:
    """Every role name this installation defines: bot.yaml's keys plus the bot's own defaults."""
    return frozenset(BUILTIN_ROLE_NAMES) | frozenset(read_bot_roles(config_dir))


# --------------------------------------------------------------------------- the backend

@dataclass(frozen=True, slots=True)
class LocalUser:
    username: str
    password_hash: str
    roles: tuple[str, ...]
    #: the managed_by values this account is scoped to. EMPTY = unrestricted (no declaration).
    scope: tuple[str, ...] = ()


class LocalAuthBackend(AuthBackend):
    """Username + password, roles declared in ``services/webservice.yaml``."""

    name = "local"
    supports_password = True

    def __init__(self, users: Iterable[LocalUser], *, config_dir: str | Path = "config"):
        self._users: dict[str, LocalUser] = {user.username: user for user in users}
        self.config_dir = Path(config_dir)

    @property
    def users(self) -> dict[str, LocalUser]:
        return dict(self._users)

    def declared_scopes(self) -> dict[str, tuple[str, ...]]:
        """``{username: declared scope}`` for the accounts that DECLARE one.

        Read by the startup audit (``auth._audit_scope_declarations``): a declared value that
        matches no server's ``managed_by`` is a silent loss of access, so it is warned about once.
        """
        return {name: user.scope for name, user in self._users.items() if user.scope}

    def authenticate(self, request) -> Identity | None:
        """Resolve the signed-in session against the LIVE user list.

        A user removed from the configuration loses every capability on their next request, on the
        same cookie: the session stores a reference, never the roles.
        """
        ref = session_identity_ref(request)
        if ref is None or ref.get("backend") != self.name:
            return None
        username = str(ref.get("subject") or "")
        user = self._users.get(username)
        if user is None:
            log.warning("auth: refusing %s %s - the session names local user %r, who is no longer "
                        "configured", request.method, request.url.path, username)
            return None
        return self._identity(user)

    def verify_credentials(self, request, username: str, password: str) -> Identity | None:
        # The length is refused FIRST, before the username lookup and before the decoy hash: the
        # password comes off the login form, and both branches below run PBKDF2 (600k iterations).
        # The reason is logged in the same style as the other refusals -- named, never echoed to
        # the page, and the password itself never appears in the line.
        if isinstance(password, str) and len(password) > MAX_PASSWORD_LENGTH:
            log.warning("auth: refusing a local login for %r - the posted password is %d "
                        "characters, longer than the accepted maximum of %d",
                        username, len(password), MAX_PASSWORD_LENGTH)
            return None
        user = self._users.get(username)
        if user is None:
            verify_password(password, _decoy_hash())
            log.warning("auth: refusing a local login - unknown user %r", username)
            return None
        if not verify_password(password, user.password_hash):
            log.warning("auth: refusing a local login for %r - bad password", username)
            return None
        log.info("auth: %r signed in with the local backend", username)
        return self._identity(user)

    def _identity(self, user: LocalUser) -> Identity:
        """The local identity: the username is both the subject AND the name the console shows.

        A password backend has no avatar, so ``avatar_url`` keeps its empty default and the shell
        falls back to the initials chip.

        The SCOPE is the account's DECLARED one (spec §10.2), because a web-only principal has no
        Discord member to compare a ``managed_by`` list against: absent a declaration the account is
        unrestricted, exactly as a web user with no per-server restriction always was.

        A declared scope is marked ``declared=True``: it is what makes the account a MANAGER of the
        console (``scope.manages_any_server``) even before a server with that ``managed_by`` exists,
        because for this backend the declaration IS the fact — a local account has no Discord roles
        to walk, so "the tokens match a server" could never be established from an identity alone.
        """
        scope = (Scope.everything() if not user.scope
                 else Scope.restricted(tuple(user.scope), declared=True))
        return Identity(subject=user.username, backend=self.name, roles=frozenset(user.roles),
                        display_name=user.username, scope=scope)


# --------------------------------------------------------------------------- configuration

def parse_users(local_config: dict, *, config_dir: str | Path) -> tuple[LocalUser, ...]:
    """Validate ``auth.local.users`` and turn every role name into a checked one.

    Raises ``ValueError`` naming the offending user and role -- never a silent grant.
    """
    known = known_role_names(config_dir)
    configured = local_config.get("users") or []
    if not isinstance(configured, list):
        raise ValueError("auth.local.users must be a list of "
                         "{username, password_hash, roles} entries")
    users: list[LocalUser] = []
    seen: set[str] = set()
    for index, entry in enumerate(configured, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"auth.local.users[{index}] must be a mapping")
        username = str(entry.get("username") or "").strip()
        if not username:
            raise ValueError(f"auth.local.users[{index}] has no username")
        if username in seen:
            raise ValueError(f"auth.local.users names '{username}' more than once")
        seen.add(username)

        encoded = entry.get("password_hash")
        if not isinstance(encoded, str) or not encoded.strip():
            raise ValueError(f"auth.local.users '{username}' has no password_hash. Generate one "
                             f"with `python -m services.webservice.auth.local --hash`.")
        encoded = encoded.strip()
        parsed = parse_password_hash(encoded)
        if parsed is None:
            raise ValueError(f"auth.local.users '{username}': password_hash is not a valid "
                             f"{SCHEME} hash - never store a plaintext password. Generate one "
                             f"with `python -m services.webservice.auth.local --hash`.")
        iterations = parsed[0]
        if iterations < DEFAULT_ITERATIONS:
            log.warning("auth.local.users '%s': the hash was generated with %d iterations, below "
                        "the current default of %d. Regenerate it with "
                        "`python -m services.webservice.auth.local --hash`.", username, iterations,
                        DEFAULT_ITERATIONS)

        roles = entry.get("roles")
        if not isinstance(roles, (list, tuple)):
            raise ValueError(f"auth.local.users '{username}' has no roles list (use `roles: []` "
                             f"for a user whose access comes from a declared scope, or `roles: []` "
                             f"with no scope for one who may sign in and see nothing)")
        role_names = tuple(str(role).strip() for role in roles if str(role).strip())
        unknown = sorted(set(role_names) - known)
        if unknown:
            raise ValueError(f"auth.local.users '{username}' declares role(s) {unknown}, which "
                             f"this bot does not define. Known role names: {sorted(known)} "
                             f"(bot.yaml 'roles:' plus the bot's own defaults). A name nothing "
                             f"defines would grant nothing at best and, merged wrongly, too much.")

        scope_values = _parse_scope(entry.get("scope"), username=username)
        # A roleless account is not necessarily a dead one: the console's third kind of identity is
        # a MANAGER, admitted through a declared scope rather than a role (``scope_grants``). Only
        # an account with NEITHER can reach nothing, so that — not "no role" — is what warns.
        if not role_names and not scope_values:
            log.warning("auth.local.users '%s' holds no role and declares no scope: the account "
                        "can sign in and reach no page.", username)

        users.append(LocalUser(username=username, password_hash=encoded, roles=role_names,
                               scope=scope_values))
    return tuple(users)


def _parse_scope(declared: Any, *, username: str) -> tuple[str, ...]:
    """Validate ``auth.local.users[].scope`` — the account's declared ``managed_by`` values.

    REFUSED LOUDLY at startup, like an unknown role name: a scope that is not a list of non-empty
    strings cannot be compared against a server's ``managed_by``, and accepting it silently would
    either grant too much (an entry ignored) or nothing at all. The only feasible shape check is
    this one, because ``managed_by`` values are FREE TEXT in the server configuration — whether a
    declared value matches a server is not decidable here and is warned about at startup instead
    (``scope.audit_local_scopes``). Absent/empty = unrestricted (see :class:`LocalUser`).
    """
    if declared is None:
        return ()
    if not isinstance(declared, (list, tuple)):
        raise ValueError(f"auth.local.users '{username}'.scope must be a list of managed_by values "
                         f"(the servers this account may manage), got {declared!r}")
    values: list[str] = []
    for value in declared:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"auth.local.users '{username}' declares scope {value!r}, which is not "
                             f"a non-empty string. managed_by values are free text and must match a "
                             f"server's managed_by entry exactly.")
        values.append(value.strip())
    return tuple(dict.fromkeys(values))


def local_backend(local_config: Any, *, config_dir: str | Path) -> LocalAuthBackend | None:
    """The backend for ``auth.local``, or ``None`` when it is absent or not enabled.

    Nothing is enabled by default: ``enabled`` must be ``true``. A block listing users while
    ``enabled`` is not true is a warning, not a silent no-op -- it is the shape of "I configured my
    account and the form refuses me".
    """
    if not isinstance(local_config, dict):
        log.info("Admin web UI auth: auth.local is not configured - no local login.")
        return None
    if local_config.get("enabled", False) is not True:
        if local_config.get("users"):
            log.warning("Admin web UI auth: auth.local.enabled is not true, so the %d configured "
                        "local user(s) cannot sign in. Set auth.local.enabled: true to enable the "
                        "local login.", len(local_config.get("users") or []))
        else:
            log.info("Admin web UI auth: the local backend is disabled (auth.local.enabled is "
                     "not true).")
        return None
    users = parse_users(local_config, config_dir=config_dir)
    if not users:
        log.warning("Admin web UI auth: the local backend is enabled but auth.local.users is "
                    "empty - nobody can sign in. Generate a hash with "
                    "`python -m services.webservice.auth.local --hash`.")
    return LocalAuthBackend(users, config_dir=config_dir)


# --------------------------------------------------------------------------- the CLI

def main(argv: list[str] | None = None) -> int:
    """``python -m services.webservice.auth.local --hash``.

    Reads the password with ``getpass`` (twice) and prints the hash plus, optionally, the YAML
    block for ``auth.local.users``. The password never reaches the command line or the log.
    """
    parser = argparse.ArgumentParser(
        prog="python -m services.webservice.auth.local",
        description="Generate a password hash for auth.local.users / auth.breakglass in "
                    "config/services/webservice.yaml.")
    parser.add_argument("--hash", action="store_true",
                        help="read a password (twice) and print its hash")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS,
                        help=f"PBKDF2 iterations (default {DEFAULT_ITERATIONS})")
    parser.add_argument("--username", default=None,
                        help="also print the ready-to-paste YAML block for this username")
    parser.add_argument("--roles", default="Admin",
                        help="comma-separated role NAMES for the printed YAML block "
                             "(default: Admin)")
    args = parser.parse_args(argv)
    if not args.hash:
        parser.print_help()
        return 2
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Repeat: ")
    if first != second:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    try:
        encoded = hash_password(first, iterations=args.iterations)
    except ValueError as ex:
        print(f"{ex}", file=sys.stderr)
        return 1
    print(encoded)
    if args.username:
        roles = [role.strip() for role in (args.roles or "Admin").split(",") if role.strip()]
        print()
        print("      users:")
        print(f"        - username: {args.username}")
        print(f"          password_hash: {encoded}")
        print(f"          roles: [{', '.join(roles)}]")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main() and the CLI subprocess
    raise SystemExit(main(_CLI_ARGV))
