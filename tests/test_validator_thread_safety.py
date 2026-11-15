"""The pykwalify parser is a shared, non-thread-safe global — every validation must be serialized.

``pykwalify.compat`` holds ONE module-global ruamel instance (``yml``). ``pykwalify.core`` loads every
source AND schema file through it (``core.py:82, 88, 100, 118``) and mutates its constructor on every
``Core(...)`` construction (``core.py:68-78``). A ruamel ``YAML`` carries per-parse state and
``YAML.load()`` resets it on that same object, so two validations in flight interleave their event
streams: a ``StreamStartEvent`` arrives where a node event is expected
(``AttributeError: 'StreamStartEvent' object has no attribute 'anchor'``) and one document's marks are
applied to another (faults at lines/columns that do not exist).

``core.utils.validators`` is the only place that builds a pykwalify ``Core`` and is reached from more
than one thread (the console's render warm-up plus config pages plus the bot). It now holds ONE
module-level ``threading.RLock`` across the ``Core(...)`` construction AND the ``.validate()`` call in
BOTH places a Core is built. The lock is REENTRANT because the pykwalify extension functions call
``_validate_schema`` from inside ``c.validate()``, i.e. while the outer lock is already held.

Run with the system pytest (``/usr/bin/python3``) from the repository root::

    /usr/bin/python3 -m pytest tests/test_validator_thread_safety.py -q
"""
from __future__ import annotations

import contextlib
import sys
import threading
import time
from pathlib import Path

import pytest

import pykwalify.compat
import pykwalify.core

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.utils import validators  # noqa: E402

#: A real sample+schema pair whose schema exercises the extension path (``include``/``any_of``), so the
#: validation drills from ``validate()`` back into ``_validate_schema()`` while the lock is held.
SOURCE = str(REPO_ROOT / "samples" / "main.yaml")
SCHEMAS = [str(REPO_ROOT / "schemas" / "main_schema.yaml")]

THREADS = 8
CALLS = 40


def _run_stress(fn) -> list[BaseException]:
    """Run ``fn`` THREADS x CALLS times concurrently and return every raised exception.

    ``fn`` takes no arguments; the shared-``vars`` cups are read by every thread, so any
    cross-thread coupling inside pykwalify surfaces here as a raised exception.
    """
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker() -> None:
        for _ in range(CALLS):
            try:
                fn()
            except BaseException as ex:  # noqa: BLE001 — any failure is the defect
                with guard:
                    errors.append(ex)

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def _validate_clean() -> None:
    validators.validate(SOURCE, SCHEMAS, raise_exception=True)


class _LoadOverlapDetector:
    """Records whether ``pykwalify.compat.yml.load`` is ever entered by two threads at once.

    Patches the SHARED instance's ``load`` (the object ``pykwalify.core`` imports as ``yml``), so it
    observes the exact call pykwalify makes. ``max_concurrency`` is the evidence: 1 means serialized.
    """

    def __init__(self) -> None:
        self.max_concurrency = 0
        self.calls = 0
        self._active = 0
        self._guard = threading.Lock()
        self._original = pykwalify.compat.yml.load

    def __enter__(self) -> "_LoadOverlapDetector":
        detector = self

        def load(stream, *args, **kwargs):
            with detector._guard:
                detector.calls += 1
                detector._active += 1
                detector.max_concurrency = max(detector.max_concurrency, detector._active)
            try:
                # widen the window so genuine overlap is observed, not merely possible
                time.sleep(0.001)
                return detector._original(stream, *args, **kwargs)
            finally:
                with detector._guard:
                    detector._active -= 1

        pykwalify.compat.yml.load = load
        return self

    def __exit__(self, *exc) -> None:
        pykwalify.compat.yml.load = self._original


class _NullLock:
    """A context manager that does NOT serialize — stands in for a lock-less (pre-fix) tree."""

    def __enter__(self) -> "_NullLock":
        return self

    def __exit__(self, *exc) -> None:
        return None


# ---------------------------------------------------------------------------------------------------
# 1. Stress: the serialized tree must never raise under concurrency.
# ---------------------------------------------------------------------------------------------------

def test_stress_validation_never_raises_under_concurrency():
    """8 threads x 40 calls over a real sample+schema pair — none may raise.

    This is the GREEN assertion. RED (pre-fix) is the same run with the module lock neutralised, and is
    pinned by ``test_detector_flags_unsynchronised_overlap`` below.
    """
    errors = _run_stress(_validate_clean)
    assert not errors, (
        f"{len(errors)}/{THREADS * CALLS} concurrent validations raised on the serialized tree; "
        f"first: {type(errors[0]).__name__}: {errors[0]}"
    )


# ---------------------------------------------------------------------------------------------------
# 2. Overlap detector + positive control.
# ---------------------------------------------------------------------------------------------------

def test_shared_parser_is_never_entered_concurrently():
    """The shared ``yml.load`` must never be driven by two threads at once on the shipped tree."""
    with _LoadOverlapDetector() as detector:
        errors = _run_stress(_validate_clean)
    assert detector.calls >= THREADS, f"detector saw only {detector.calls} loads — not exercising the path"
    assert not errors, f"serialized run raised {len(errors)} errors"
    assert detector.max_concurrency == 1, (
        f"shared parser entered by {detector.max_concurrency} threads at once; expected 1"
    )


def test_detector_flags_unsynchronised_overlap():
    """POSITIVE CONTROL: with the lock neutralised the detector MUST record overlap (and errors).

    A detector that only ever sees the fixed tree is not evidence. This run reproduces the pre-fix
    behaviour in memory (``_pykwalify_lock`` replaced by a no-op) and requires the detector to flag it.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(validators, "_pykwalify_lock", _NullLock())
        with _LoadOverlapDetector() as detector:
            errors = _run_stress(_validate_clean)
    assert detector.max_concurrency > 1, (
        "detector did not flag overlap even with the lock removed — it cannot detect the defect"
    )
    assert errors, "unsynchronised tree produced no errors — the stress harness does not exercise the race"


# ---------------------------------------------------------------------------------------------------
# 3. Reentrancy: the nesting path is real; the shipped RLock completes it.
# ---------------------------------------------------------------------------------------------------

def test_extension_nesting_is_real():
    """A single ``validate()`` re-enters pykwalify through the ``func:`` extensions.

    Proves the nesting is real, not hypothetical: ``samples/main.yaml`` vs ``schemas/main_schema.yaml``
    builds MANY ``Core`` objects from inside one outer ``c.validate()`` (the extension path calls
    ``_validate_schema``).
    """
    counts = {"n": 0}
    original = pykwalify.core.Core.__init__

    def spy(self, *a, **k):
        counts["n"] += 1
        return original(self, *a, **k)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pykwalify.core.Core, "__init__", spy)
        validators.validate(SOURCE, SCHEMAS, raise_exception=True)
    assert counts["n"] > 1, (
        f"only {counts['n']} Core construction(s) — the extension nesting path was not exercised"
    )


def test_reentrant_lock_completes_nested_schema_validation():
    """The shipped lock must complete the nested-schema validation and not hang.

    The schema drives ``validate()`` back into the extension path while the outer lock is held; an
    RLock is safe there. A bounded join makes a hang a result, not a stall.
    """
    result: list[object] = []

    def run() -> None:
        result.append(validators.validate(SOURCE, SCHEMAS, raise_exception=True))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive(), "validation hung — the shipped lock deadlocked on its own nesting path"
    assert result == [None], "validation did not complete cleanly on the nested schema"


def test_plain_lock_also_completes_because_pykwalify_reloads_the_extensions():
    """The nested ``_validate_schema`` runs in a RELOADED copy of this module, not the outer one.

    ``pykwalify.core.Core._load_extensions`` exec's each extension file with a fresh
    ``SourceFileLoader("", path).load_module()`` per ``Core(...)``, so the extension functions call the
    ``_validate_schema`` of a NEW module object — which carries its OWN lock, not the outer one. The
    outer lock is therefore not physically re-entered through pykwalify, and a plain ``threading.Lock``
    in the same place completes rather than deadlocking.

    This is a guard, not a guarantee: if pykwalify ever caches the extension module, the nested call
    re-enters THIS lock and a plain Lock deadlocks — at which point the shipped RLock becomes mandatory
    exactly as the docstring in ``core/utils/validators.py`` states.
    """
    done = threading.Event()

    def run() -> None:
        with contextlib.suppress(BaseException):
            validators.validate(SOURCE, SCHEMAS, raise_exception=True)
        done.set()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(validators, "_pykwalify_lock", threading.Lock())
        t = threading.Thread(target=run, daemon=True)
        t.start()
        finished = done.wait(timeout=10)

    assert finished, (
        "a plain Lock deadlocked — the nested call DOES re-enter the outer lock; the RLock is now "
        "load-bearing and this module's docstring must be updated"
    )
