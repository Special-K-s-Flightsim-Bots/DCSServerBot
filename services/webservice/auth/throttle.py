"""Per-client failed-login throttle for the password door.

The password check is deliberately expensive (PBKDF2, 600 000 iterations), which makes every
attempt costly for the process and the door cheap to script. This module bounds it: a client
address may fail :data:`MAX_ATTEMPTS` times within :data:`WINDOW_SECONDS` and is then refused for
the same period, without the credentials being looked at. A successful sign-in clears the record.

State is in memory and per process: the console runs one worker, and a restart forgetting the
counters is the honest trade for a control whose value is being immediate.
"""
from __future__ import annotations

import math
import threading
import time

__all__ = ["MAX_ATTEMPTS", "WINDOW_SECONDS", "MAX_TRACKED_CLIENTS", "UNKNOWN_CLIENT",
           "LOGIN_THROTTLE_ATTR", "LoginThrottle"]

#: Refused attempts a client address may make before it is locked out.
MAX_ATTEMPTS = 5

#: Both the counting window and the lockout length, in seconds.
WINDOW_SECONDS = 5 * 60

#: Most distinct client addresses kept at once, so a spoofed-source flood cannot grow the two
#: dictionaries without bound. Past the cap the OLDEST-inserted address is dropped — losing a
#: lockout only lets that caller try again and be locked again at once.
MAX_TRACKED_CLIENTS = 1024

#: The key for a request that carries no client address. One shared bucket: fail closed, never
#: "unknown means unlimited".
UNKNOWN_CLIENT = "\x00unknown"

#: The app-state attribute holding the throttle. Per application, so a rebuilt app (a takeover, a
#: stop/start) starts from a clean count instead of inheriting another app's lockouts.
LOGIN_THROTTLE_ATTR = "webui_login_throttle"


class LoginThrottle:
    """Refused-attempt bookkeeping for one client address at a time."""

    def __init__(self, *, max_attempts: int = MAX_ATTEMPTS, window_seconds: float = WINDOW_SECONDS,
                 max_clients: int = MAX_TRACKED_CLIENTS, clock=time.monotonic) -> None:
        self._max_attempts = max(1, int(max_attempts))
        self._window = float(window_seconds)
        self._max_clients = max(1, int(max_clients))
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def client_key(client) -> str:
        """The bookkeeping key for *client* (a request's client address, or ``None``)."""
        text = str(client).strip() if client is not None else ""
        return text or UNKNOWN_CLIENT

    def is_locked(self, client) -> bool:
        return self.seconds_left(client) > 0

    def seconds_left(self, client) -> int:
        """Whole seconds the client must still wait; ``0`` when it may try again."""
        key = self.client_key(client)
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return 0
            remaining = until - self._clock()
            if remaining <= 0:
                self._locked_until.pop(key, None)
                return 0
            return max(1, math.ceil(remaining))

    def record_failure(self, client) -> int:
        """Record one refused attempt; return the attempts left (``0`` means now locked out)."""
        key = self.client_key(client)
        with self._lock:
            now = self._clock()
            attempts = [stamp for stamp in self._failures.get(key, ())
                        if now - stamp <= self._window]
            attempts.append(now)
            self._failures[key] = attempts
            if len(attempts) >= self._max_attempts:
                self._locked_until[key] = now + self._window
                left = 0
            else:
                left = self._max_attempts - len(attempts)
            self._prune(now)
            return left

    def reset(self, client) -> None:
        """Forget every refused attempt for *client* (a successful sign-in)."""
        key = self.client_key(client)
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def _prune(self, now: float) -> None:
        """Drop expired lockouts and keep the bookkeeping under :data:`MAX_TRACKED_CLIENTS`."""
        for key, until in [(key, until) for key, until in self._locked_until.items()
                           if until <= now]:
            del self._locked_until[key]
            # the failure stamps only go when the whole window has passed: a client that tried
            # again after its lockout keeps the attempt it just made
            if not any(now - stamp <= self._window for stamp in self._failures.get(key, ())):
                self._failures.pop(key, None)
        overflow = len(self._failures) + len(self._locked_until) - self._max_clients
        while overflow > 0 and (self._failures or self._locked_until):
            if self._failures:
                self._failures.pop(next(iter(self._failures)), None)
            else:
                self._locked_until.pop(next(iter(self._locked_until)), None)
            overflow -= 1
