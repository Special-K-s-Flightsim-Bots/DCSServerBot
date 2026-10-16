"""The setup window: the boot-time permission to run setup, and the one-time code that gates it.

TWO FACTS, KEPT APART ON PURPOSE.

* The WINDOW is file-derived and decided ONCE, at process start. ``main.yaml`` absent then means the
  setup console may run; a boot where that file exists never builds the console at all, so the
  window cannot be re-opened by a request, a timer or an operator action. This module deliberately
  does NOT re-read the file: a per-request re-check would turn the window into a runtime toggle,
  which is the one design it must not be.
* The CODE is the sole authenticator of a remote client. It lives in this object only — process
  memory, never on disk, never in the log — is good for :data:`CODE_TTL_SECONDS`, and is SPENT on
  the first successful exchange, atomically, so two simultaneous submissions cannot both win.

A WRONG ENTRY NEVER INVALIDATES THE CODE. The accepted cost is that a scripted flood can occupy the
window; the refused alternative is a burn-on-flood rule, which would let any network neighbour deny
the operator their own setup. Wrong entries are counted PER CLIENT ADDRESS: a locked-out caller is
answered :data:`LOCKED` while the code stays valid for everyone else in the window.

ONE BUCKET PER ADDRESS — AND ON THE LOOPBACK CONSOLE THAT IS ONE SHARED BUCKET. The console binds
loopback only (see :mod:`services.webservice.setup.app`), so every local process presents the same
peer address (``127.0.0.1``) and shares a single attempt/lockout bucket with the operator. A flood
from any local process can therefore lock the OPERATOR out for :data:`LOCKOUT_SECONDS`; the code is
never burned, and a local process is already inside the trust boundary, so this is accepted rather
than fixed. A remote mode would need to key the limiter on something finer than the peer address and
would face a second, separate problem — a spoofed source flooding the bookkeeping itself (see
:data:`MAX_TRACKED_CLIENTS`).

The entropy, not the limiter, is the brute-force defence: 32**8 = 2**40 codes, so a limiter exists
to keep a flood cheap to refuse and the log readable.

WHAT THIS MODULE DOES NOT COVER: it says nothing about the transport (a cleartext bind crosses the
network in the clear), nothing about what a tour may read or write, and nothing about the normal
console's authentication — those belong to the caller and the identity layer.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import math
import secrets
import time

__all__ = [
    "CODE_ALPHABET",
    "CODE_LENGTH",
    "CODE_TTL_SECONDS",
    "MAX_CODE_ATTEMPTS",
    "LOCKOUT_SECONDS",
    "WRONG_CODE_DELAY_SECONDS",
    "MAX_TRACKED_CLIENTS",
    "ACCEPTED",
    "WRONG",
    "EXPIRED",
    "SPENT",
    "LOCKED",
    "generate_code",
    "normalise_code",
    "format_code",
    "SetupWindow",
]

log = logging.getLogger(__name__)

#: Crockford base32: ``0-9`` plus ``A-Z`` with ``I``, ``L``, ``O`` and ``U`` removed, so no symbol
#: can be confused with another when the code is read aloud or typed off a screenshot.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: 8 symbols = 32**8 = 2**40 codes. Short enough to transcribe, long enough that a 30-minute
#: window's worth of guessing is hopeless without a limiter carrying the security.
CODE_LENGTH = 8

#: How long a minted code stays acceptable. Fixed: the console lives for a single process lifetime
#: anyway, so a configurable value would be a knob nobody tunes.
CODE_TTL_SECONDS = 30 * 60

#: Wrong entries one client address may make before it is locked out. The code itself is untouched.
MAX_CODE_ATTEMPTS = 5

#: How long a locked-out client address waits before it may try again.
LOCKOUT_SECONDS = 5 * 60

#: The delay a wrong entry pays. Keeps a scripted flood cheap to refuse; never a hint to the caller.
WRONG_CODE_DELAY_SECONDS = 0.25

#: The most distinct client addresses the limiter keeps bookkeeping for at once. The loopback-only
#: console sees one address, so this is never reached in supported use; it exists so an eventual
#: remote mode cannot grow the two limiter dicts without bound under a spoofed-source flood. When the
#: cap is reached the OLDEST-inserted clients are dropped — losing a lockout only lets that caller
#: try again (and be re-locked at once), while the real brute-force defence is the code's entropy.
MAX_TRACKED_CLIENTS = 1024

#: The outcome of an exchange attempt. Strings, so a caller can log or compare them directly.
ACCEPTED = "accepted"
WRONG = "wrong"
EXPIRED = "expired"
SPENT = "spent"
LOCKED = "locked"


def generate_code(length: int = CODE_LENGTH) -> str:
    """Mint a new code over :data:`CODE_ALPHABET` with the OS CSPRNG."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def normalise_code(value) -> str:
    """The comparable form of a code: separators gone, case folded.

    Only the separators a person types or a link carries are removed (``-`` and whitespace). The
    alphabet excludes ``I``/``L``/``O``/``U`` so a transcription cannot need a repair, and a repair
    that guessed wrong would be a lockout for a correct code — so there is none.
    """
    text = str(value or "")
    for character in ("-", " ", "\t", "\n", "\r"):
        text = text.replace(character, "")
    return text.strip().upper()


def format_code(code) -> str:
    """The dash-grouped display form (``XXXX-XXXX``). Presentation only — see :func:`normalise_code`."""
    text = normalise_code(code)
    return "-".join(text[index:index + 4] for index in range(0, len(text), 4))


class SetupWindow:
    """One setup console's code and its single-use / expiry / per-client bookkeeping.

    Constructed once per boot of an unconfigured install. Every state change an exchange can make
    goes through :meth:`exchange`, whose critical section is synchronous, so the check and the spend
    are one atomic step and no two callers can both be accepted.
    """

    def __init__(self, *, code: str | None = None, ttl: float = CODE_TTL_SECONDS,
                 max_attempts: int = MAX_CODE_ATTEMPTS, lockout_seconds: float = LOCKOUT_SECONDS,
                 wrong_delay: float = WRONG_CODE_DELAY_SECONDS, clock=time.monotonic,
                 sleep=asyncio.sleep) -> None:
        normalised = normalise_code(code) if code else generate_code()
        if not normalised:
            raise ValueError("a setup code must not be empty")
        self._code = normalised
        self._ttl = float(ttl)
        self._max_attempts = int(max_attempts)
        self._lockout_seconds = float(lockout_seconds)
        self._wrong_delay = float(wrong_delay)
        self._clock = clock
        self._sleep = sleep
        self._created = self._clock()
        self._spent = False
        #: a random marker for THIS window object. An accepted session stores it, so a session that
        #: was accepted by an earlier process (a configured session secret survives a restart) can
        #: never satisfy this one's guard: acceptance belongs to a boot, not to a cookie.
        self._fingerprint = secrets.token_urlsafe(16)
        self._lock = asyncio.Lock()
        #: client address -> monotonic timestamps of its wrong entries
        self._attempts: dict[str, list[float]] = {}
        #: client address -> monotonic time its lockout ends
        self._lockouts: dict[str, float] = {}

    # ------------------------------------------------------------------------------ the code

    @property
    def code(self) -> str:
        """The comparable form of the code (never logged, never written down)."""
        return self._code

    @property
    def display_code(self) -> str:
        """The code as it is shown to the operator (dash-grouped)."""
        return format_code(self._code)

    @property
    def spent(self) -> bool:
        return self._spent

    @property
    def ttl(self) -> float:
        return self._ttl

    @property
    def fingerprint(self) -> str:
        """This window object's random marker; see the constructor."""
        return self._fingerprint

    def seconds_left(self) -> float:
        """Seconds this code remains acceptable (never negative)."""
        return max(0.0, self._ttl - (self._clock() - self._created))

    @property
    def expired(self) -> bool:
        return self.seconds_left() <= 0.0

    # ------------------------------------------------------------------------ the limiter

    def is_locked(self, client: str | None) -> bool:
        """Whether *client* is currently locked out for wrong entries."""
        client = self._client_key(client)
        return self._lockouts.get(client, 0.0) > self._clock()

    def lock_seconds_left(self, client: str | None) -> int:
        """Whole seconds *client* must wait, rounded up, or 0 when it is not locked out."""
        client = self._client_key(client)
        return max(0, math.ceil(self._lockouts.get(client, 0.0) - self._clock()))

    def attempts_left(self, client: str | None) -> int:
        """Wrong entries *client* may still make before it is locked out (never negative).

        A locked-out client reports 0 whether or not its earlier entries are still inside the
        window: the lockout is the answer, not the count that produced it.
        """
        client = self._client_key(client)
        if self.is_locked(client):
            return 0
        return max(0, self._max_attempts - len(self._recent_attempts(client)))

    @staticmethod
    def _client_key(client: str | None) -> str:
        """The key a caller is booked under. ONE bucket, so a caller a transport cannot name cannot
        escape the limiter by staying nameless.

        On the loopback-only console this is also the ADDRESS the operator and every other local
        process share (``127.0.0.1``): one bucket by design here, which is why a local flood can lock
        the operator out — see the module docstring.
        """
        return str(client) if client else "?"

    def _prune(self) -> None:
        """Drop bookkeeping that no longer constrains anyone, and keep the two dicts under
        :data:`MAX_TRACKED_CLIENTS`.

        BOUND: an address is remembered only while it is locked out or has a wrong entry inside the
        lockout window, and at most :data:`MAX_TRACKED_CLIENTS` addresses are kept at once. Called as
        a new address is booked, so both dicts stay bounded without a timer.
        """
        now = self._clock()
        for client in [key for key, until in self._lockouts.items() if until <= now]:
            del self._lockouts[client]
        for client in [key for key in list(self._attempts) if not self._recent_attempts(key)]:
            del self._attempts[client]
        overflow = len(self._lockouts) + len(self._attempts) - MAX_TRACKED_CLIENTS
        while overflow > 0 and self._lockouts:
            self._lockouts.pop(next(iter(self._lockouts)), None)
            overflow -= 1
        while overflow > 0 and self._attempts:
            self._attempts.pop(next(iter(self._attempts)), None)
            overflow -= 1

    def _recent_attempts(self, client: str) -> list[float]:
        now = self._clock()
        kept = [stamp for stamp in self._attempts.get(client, ())
                if now - stamp <= self._lockout_seconds]
        if kept:
            self._attempts[client] = kept
        else:
            self._attempts.pop(client, None)
        return kept

    def _count_wrong(self, client: str) -> None:
        """Record one wrong entry and lock the client out once it has had :data:`MAX_CODE_ATTEMPTS`.

        The bookkeeping is pruned first, so a stream of distinct addresses cannot grow the two dicts
        without bound: an expired lockout or a stale attempt leaves no trace once the next address is
        seen (see :meth:`_prune`).
        """
        self._prune()
        recent = self._recent_attempts(client)
        recent.append(self._clock())
        self._attempts[client] = recent
        if len(recent) >= self._max_attempts:
            self._lockouts[client] = self._clock() + self._lockout_seconds
            self._attempts.pop(client, None)
            log.warning("Setup console: locking out %s for %d second(s) after %d wrong setup "
                        "codes. The code stays valid for everyone else.",
                        client, int(self._lockout_seconds), len(recent))

    # ---------------------------------------------------------------------------- the exchange

    async def exchange(self, submitted, client: str | None = None) -> str:
        """Try to spend the code for *client*. Returns one of the outcome constants.

        The check and the spend happen in ONE synchronous critical section, so of two simultaneous
        submissions of the correct code exactly one is :data:`ACCEPTED` and the other is
        :data:`SPENT`. The delay a wrong entry pays is applied OUTSIDE the lock: a flood must not be
        able to make the operator's correct entry wait behind it.
        """
        async with self._lock:
            outcome = self._evaluate(submitted, self._client_key(client))
        if outcome == WRONG:
            await self._sleep(self._wrong_delay)
        return outcome

    def _evaluate(self, submitted, client: str) -> str:
        """The atomic decision. Synchronous on purpose: no await may separate check from spend."""
        if self._spent:
            return SPENT
        if self.expired:
            return EXPIRED
        if self._lockouts.get(client, 0.0) > self._clock():
            return LOCKED
        if self._matches(submitted):
            self._spent = True
            log.info("Setup console: the setup code was accepted and is now spent; a second client "
                     "can no longer enter the tour.")
            return ACCEPTED
        # A wrong entry: counted against the caller, never against the code. The submitted value is
        # NOT logged, and the refusal carries no hint (no length oracle, no "first character
        # correct"), so a flood cannot learn anything from the answer or from the log.
        self._count_wrong(client)
        log.warning("Setup console: rejecting an incorrect setup code from %s.", client)
        return WRONG

    def _matches(self, submitted) -> bool:
        """Constant-time comparison of the normalised forms (the house idiom, ``hmac.compare_digest``).

        Compared as BYTES: ``compare_digest`` refuses a ``str`` holding a non-ASCII character, and a
        submitted value is attacker-controlled, so a stray byte must answer "wrong" rather than
        raise on the refusal path.
        """
        return hmac.compare_digest(normalise_code(submitted).encode("utf-8"),
                                   self._code.encode("utf-8"))
