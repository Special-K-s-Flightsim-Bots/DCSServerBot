"""One break-glass Admin credential, for the day the identity provider is the thing that broke.

SAME STORE, SAME FORMAT as the local backend: a PBKDF2-HMAC-SHA256 hash under
``auth.breakglass`` in ``config/services/webservice.yaml``, verified with the same constant-time
comparison (``services/webservice/auth/local.py``). One credential, role ``Admin``, nothing else:
a break-glass account that could be granted a narrower role would be an account somebody would
start granting narrower roles.

REFUSED UNLESS EXPLICITLY ENABLED. ``auth.breakglass.enabled`` must be ``true`` (the default is
absent/false), so an installation never grows a second admin credential by accident. When it IS
enabled the startup log says so at WARNING level, because a standing break-glass credential is an
operational state somebody should have decided on purpose -- the intended lifecycle is "enable it,
use it, disable it again".

Everything else about the account follows the local backend: it signs in through the same
``/auth/login`` form, gets the same signed session, and its session stores a reference
(``{"backend": "breakglass", "subject": <username>}``) rather than the role.
"""
from __future__ import annotations

import logging
from typing import Any

from . import AuthBackend, Identity, session_identity_ref
from .local import DEFAULT_ITERATIONS, SCHEME, parse_password_hash, verify_password
from ..scope import Scope

__all__ = ["ROLE", "BreakglassAuthBackend", "breakglass_backend"]

log = logging.getLogger(__name__)

#: the one role this credential grants
ROLE = "Admin"


class BreakglassAuthBackend(AuthBackend):
    """The single emergency Admin credential."""

    name = "breakglass"
    supports_password = True

    def __init__(self, username: str, password_hash: str):
        self._username = username
        self._password_hash = password_hash

    @property
    def username(self) -> str:
        return self._username

    def authenticate(self, request) -> Identity | None:
        ref = session_identity_ref(request)
        if ref is None or ref.get("backend") != self.name:
            return None
        if str(ref.get("subject") or "") != self._username:
            log.warning("auth: refusing %s %s - the session names break-glass user %r, but the "
                        "configuration names %r", request.method, request.url.path,
                        ref.get("subject"), self._username)
            return None
        return self._identity()

    def verify_credentials(self, request, username: str, password: str) -> Identity | None:
        if username != self._username:
            # no decoy hash here: the local backend already paid that cost for this username
            return None
        if not verify_password(password, self._password_hash):
            log.warning("auth: refusing a break-glass login - bad password")
            return None
        log.warning("auth: %r signed in with the BREAK-GLASS credential - disable "
                    "auth.breakglass.enabled again once it is no longer needed.", username)
        return self._identity()

    def _identity(self) -> Identity:
        """The break-glass identity: ``Admin`` and nothing else, and therefore UNSCOPED (spec §10.6).

        The scope is stated here explicitly rather than left to the ``Admin`` rule in
        :meth:`AuthManager.scope_for`, so a reader of this credential sees the decision where the
        credential is defined: it has no Discord member to compare a ``managed_by`` list against, and
        it exists for the day the operators are locked out — a scoped break-glass would defeat that.
        """
        return Identity(subject=self._username, backend=self.name, roles=frozenset({ROLE}),
                        display_name=self._username, scope=Scope.everything())


def breakglass_backend(breakglass_config: Any) -> BreakglassAuthBackend | None:
    """The backend for ``auth.breakglass``, or ``None`` when it is not explicitly enabled.

    Raises ``ValueError`` when it IS enabled and the credential is missing or malformed -- an
    enabled break-glass account that cannot be used is discovered in the emergency, which is
    exactly the wrong moment.
    """
    if not isinstance(breakglass_config, dict) or breakglass_config.get("enabled", False) is not True:
        log.info("Admin web UI auth: the break-glass credential is disabled "
                 "(auth.breakglass.enabled is not true).")
        return None

    username = str(breakglass_config.get("username") or "").strip()
    encoded = breakglass_config.get("password_hash")
    if not username:
        raise ValueError("auth.breakglass.enabled is true but auth.breakglass.username is missing")
    if not isinstance(encoded, str) or parse_password_hash(encoded.strip()) is None:
        raise ValueError(f"auth.breakglass.enabled is true but auth.breakglass.password_hash is "
                         f"not a valid {SCHEME} hash. Generate one with "
                         f"`python -m services.webservice.auth.local --hash`.")
    encoded = encoded.strip()
    iterations = parse_password_hash(encoded)
    if iterations is not None and iterations[0] < DEFAULT_ITERATIONS:
        log.warning("auth.breakglass: the hash was generated with %d iterations, below the current "
                    "default of %d. Regenerate it with "
                    "`python -m services.webservice.auth.local --hash`.", iterations[0],
                    DEFAULT_ITERATIONS)

    log.warning("Admin web UI auth: the BREAK-GLASS Admin credential is ENABLED for user %r. It "
                "grants the full Admin role to anyone holding %r's password. Disable it "
                "(auth.breakglass.enabled: false) when it is not needed.", username, username)
    return BreakglassAuthBackend(username, encoded)
