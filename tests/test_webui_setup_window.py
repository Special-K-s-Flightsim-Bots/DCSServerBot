"""The setup window: the one-time code, its single use, its expiry and its per-address limiter.

These are the properties the console's security rests on, exercised against the object alone (no
HTTP): the code is minted over the documented alphabet, it is spent exactly once, it stops being
accepted after its TTL, a wrong entry never invalidates it, and an address that keeps guessing is
locked out while everybody else's code still works.

WHAT THEY DO NOT COVER: nothing here mounts the application, so nothing here proves the gate page
is what serves them, that the cookie is set only on acceptance, or that a route is reachable — that
is ``test_webui_setup_app``. Nothing here proves the code cannot be read off disk: it is never
written, and the check for that is that no code path in this module writes a file at all.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from services.webservice.setup.window import (
    ACCEPTED, CODE_ALPHABET, CODE_LENGTH, EXPIRED, LOCKED, SPENT, WRONG,
    MAX_TRACKED_CLIENTS, SetupWindow, format_code, generate_code, normalise_code,
)

CODE = "ABCD2345"


async def _no_sleep(_seconds: float) -> None:
    """The wrong-entry delay, removed: the delay is real behaviour, the waiting is not the subject."""
    return None


class Clock:
    """A monotonic clock a test can move."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def window(code: str = CODE, *, clock=None, ttl: float = 1800.0, max_attempts: int = 5,
           lockout_seconds: float = 300.0) -> SetupWindow:
    return SetupWindow(code=code, ttl=ttl, max_attempts=max_attempts,
                       lockout_seconds=lockout_seconds, wrong_delay=0.0,
                       clock=clock or Clock(), sleep=_no_sleep)


# ------------------------------------------------------------------------------------ the code

def test_the_minted_code_uses_the_unambiguous_alphabet_and_the_documented_length():
    for _ in range(200):
        code = generate_code()
        assert len(code) == CODE_LENGTH
        assert set(code) <= set(CODE_ALPHABET)
        # the four symbols Crockford base32 drops, so no code can be misread as another
        assert not set(code) & set("ILOU")


def test_minted_codes_are_not_repeated():
    assert len({generate_code() for _ in range(500)}) == 500


def test_the_separators_a_person_types_are_normalised_away():
    assert normalise_code("abcd-2345") == "ABCD2345"
    assert normalise_code(" ABCD 2345 ") == "ABCD2345"
    assert normalise_code("ABCD\t2345\n") == "ABCD2345"
    assert normalise_code(None) == ""


def test_the_display_form_groups_by_four_and_does_not_change_the_comparison():
    assert format_code("ABCD2345") == "ABCD-2345"
    assert format_code("abcd2345") == "ABCD-2345"
    # a displayed and re-typed code is still the code
    assert normalise_code(format_code(CODE)) == CODE


def test_a_code_is_generated_when_none_is_given_and_refused_when_empty():
    assert len(SetupWindow().code) == CODE_LENGTH
    try:
        SetupWindow(code="---")
    except ValueError as ex:
        assert "must not be empty" in str(ex)
    else:  # pragma: no cover - the refusal is the point
        raise AssertionError("an empty code must be refused")


# ------------------------------------------------------------------------- single use and expiry

@pytest.mark.asyncio
async def test_the_correct_code_is_accepted_once_and_never_again():
    subject = window()
    assert await subject.exchange(CODE) == ACCEPTED
    assert subject.spent is True
    # the same client, another client, a differently spelled code: all spent
    assert await subject.exchange(CODE, "other") == SPENT
    assert await subject.exchange("abcd-2345", "other") == SPENT


@pytest.mark.asyncio
async def test_two_simultaneous_submissions_of_the_code_accept_exactly_one():
    subject = window()
    results = await asyncio.gather(*[subject.exchange(CODE, f"client-{index}")
                                     for index in range(20)])
    assert results.count(ACCEPTED) == 1
    assert results.count(SPENT) == len(results) - 1


@pytest.mark.asyncio
async def test_the_code_stops_being_accepted_when_it_expires():
    clock = Clock()
    subject = window(clock=clock)
    assert subject.expired is False
    clock.advance(1800.0)
    assert subject.expired is True
    assert subject.seconds_left() == 0.0
    assert await subject.exchange(CODE) == EXPIRED
    assert subject.spent is False, "an expired entry must not spend the code"


@pytest.mark.asyncio
async def test_a_code_one_second_inside_its_ttl_is_still_accepted():
    clock = Clock()
    subject = window(clock=clock, ttl=30.0)
    clock.advance(29.0)
    assert await subject.exchange(CODE) == ACCEPTED


# ------------------------------------------------------------------------------- the limiter

@pytest.mark.asyncio
async def test_a_wrong_entry_never_invalidates_the_code():
    subject = window(max_attempts=5)
    for _ in range(4):
        assert await subject.exchange("ZZZZ9999", "1.2.3.4") == WRONG
    assert subject.spent is False
    # a different address is not affected by those failures
    assert await subject.exchange(CODE, "5.6.7.8") == ACCEPTED


@pytest.mark.asyncio
async def test_a_flood_locks_the_address_out_while_the_code_stays_valid_elsewhere():
    subject = window(max_attempts=3, lockout_seconds=300.0)
    assert await subject.exchange("ZZZZ9999", "1.2.3.4") == WRONG
    assert await subject.exchange("ZZZZ9999", "1.2.3.4") == WRONG
    # the entry that reaches the limit is still answered as WRONG; the lockout starts after it
    assert await subject.exchange("ZZZZ9999", "1.2.3.4") == WRONG
    assert subject.is_locked("1.2.3.4") is True
    assert await subject.exchange("ZZZZ9999", "1.2.3.4") == LOCKED
    assert subject.attempts_left("1.2.3.4") == 0
    assert subject.spent is False
    # the operator, somewhere else, still gets in
    assert await subject.exchange(CODE, "5.6.7.8") == ACCEPTED


@pytest.mark.asyncio
async def test_the_lockout_expires_by_itself():
    clock = Clock()
    subject = window(clock=clock, max_attempts=2, lockout_seconds=60.0)
    await subject.exchange("ZZZZ9999", "1.2.3.4")
    await subject.exchange("ZZZZ9999", "1.2.3.4")
    assert subject.is_locked("1.2.3.4") is True
    assert subject.lock_seconds_left("1.2.3.4") > 0
    clock.advance(60.0)
    assert subject.is_locked("1.2.3.4") is False
    assert subject.lock_seconds_left("1.2.3.4") == 0
    assert subject.attempts_left("1.2.3.4") == 2
    assert await subject.exchange(CODE, "1.2.3.4") == ACCEPTED


@pytest.mark.asyncio
async def test_an_unnamed_caller_cannot_escape_the_limiter():
    """Every request without a peer address is booked in ONE bucket, so staying nameless is no way
    around the limiter."""
    subject = window(max_attempts=2)
    assert await subject.exchange("ZZZZ9999") == WRONG
    assert await subject.exchange("ZZZZ9999") == WRONG
    assert subject.is_locked(None) is True
    assert await subject.exchange(CODE) == LOCKED


@pytest.mark.asyncio
async def test_a_non_ascii_submission_is_answered_wrong_rather_than_raising():
    subject = window()
    assert await subject.exchange("ÜNICODE-9", "1.2.3.4") == WRONG
    assert await subject.exchange(CODE, "1.2.3.4") == ACCEPTED


@pytest.mark.asyncio
async def test_the_submitted_value_is_never_logged(caplog):
    subject = window()
    with caplog.at_level(logging.WARNING):
        await subject.exchange("SECRET-GUESS", "9.9.9.9")
    assert "SECRET-GUESS" not in caplog.text
    assert "9.9.9.9" in caplog.text, "the refusal names the address it is counted against"


@pytest.mark.asyncio
async def test_an_expired_lockout_is_pruned_when_the_next_address_arrives():
    """The limiter bookkeeping is not kept forever: an address whose lockout has run out leaves no
    trace once another address is seen."""
    clock = Clock()
    subject = window(clock=clock, max_attempts=1, lockout_seconds=60.0)
    for octet in range(5):
        await subject.exchange("ZZZZ9999", f"10.0.0.{octet}")
    assert len(subject._lockouts) == 5, "each wrong entry locked its own address out"
    assert subject.is_locked("10.0.0.0") is True

    clock.advance(61.0)
    # a brand-new address's wrong entry triggers the prune
    await subject.exchange("ZZZZ9999", "10.0.0.99")

    assert "10.0.0.0" not in subject._lockouts, "an expired lockout must not linger"
    assert len(subject._lockouts) == 1, "only the address booked just now remains"
    assert subject._attempts == {}


@pytest.mark.asyncio
async def test_the_limiter_bookkeeping_is_bounded(monkeypatch):
    """A stream of distinct addresses cannot grow the limiter dicts without bound: the cap holds."""
    from services.webservice.setup import window as window_module

    assert MAX_TRACKED_CLIENTS >= 1
    monkeypatch.setattr(window_module, "MAX_TRACKED_CLIENTS", 8)
    subject = window(max_attempts=1, lockout_seconds=300.0)
    for index in range(50):
        await subject.exchange("ZZZZ9999", f"10.0.{index // 256}.{index % 256}")
    tracked = len(subject._lockouts) + len(subject._attempts)
    assert tracked <= 9, f"bookkeeping must stay at the cap (plus the one just booked), was {tracked}"


# ----------------------------------------------------------------------------------- the window

def test_the_window_fingerprint_is_per_object_and_unpredictable():
    first, second = SetupWindow(), SetupWindow()
    assert first.fingerprint != second.fingerprint
    assert len(first.fingerprint) >= 20
