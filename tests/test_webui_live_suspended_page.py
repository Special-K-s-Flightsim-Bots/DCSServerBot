"""The live stream must not hold a connection for a page nobody is looking at.

A browser may PARK a page in its back/forward cache instead of destroying it when the reader
navigates away, and an open ``EventSource`` rides into the cache with it. The browser allows six
connections per origin, so parked consoles alone can fill that pool — and then every later request to
the console (a click's navigation, an asset, the next fragment poll) waits for a connection that
never frees, which is a page that ignores clicks and a navigation that hangs. The shipped script
therefore CLOSES its stream on ``pagehide`` and reopens it on ``pageshow``.

Driven by a browser-less harness that loads the REAL shipped file (``tests/shell_live_harness.js``),
because "is the source closed and reopened" cannot be read off the rendered markup.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import run_node_harness

HARNESS = Path(__file__).resolve().parent / "shell_live_harness.js"
REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL_JS = REPO_ROOT / "services" / "webservice" / "static" / "shell.js"

#: the one statement the pagehide behaviour hangs off — pinned by text so the control below can
#: remove exactly it and nothing else.
SUSPEND_LISTENER = 'window.addEventListener("pagehide", suspend);'


def _run(script: Path) -> dict:
    done = run_node_harness(["node", str(HARNESS), str(script)])
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_live_script_closes_its_stream_when_the_page_is_parked():
    """The stream exists while the page is looked at — and is closed the moment it is parked."""
    report = _run(SHELL_JS)
    assert report["openedOnLoad"] == 1, report
    assert report["openAfterLoad"] == 1, "the page must open its stream while it is shown"
    assert report["closedOnPagehide"] is True, \
        ("the stream must be closed on pagehide: an open EventSource rides into the back/forward "
         "cache and holds one of the browser's six connections per origin")
    assert report["openAfterPagehide"] == 0, \
        "no stream may outlive the page that opened it"
    assert report["pollLeftBehindByPagehide"] == 0, \
        "the polling fallback must be stopped on pagehide too, or it keeps a tab that is not shown " \
        "asking for fragments"


def test_a_restored_page_opens_a_new_stream_exactly_once():
    """Coming back must reopen it — once, at the URL the page was rendered with."""
    report = _run(SHELL_JS)
    assert report["createdAfterPageshow"] == 2, \
        "a restored page must reopen its stream (the one it closed on pagehide)"
    assert report["openAfterPageshow"] == 1, "exactly one stream may be open after the restore"
    assert report["reopenedUrl"] == "/api/dashboard/stream?level=info", \
        "the reopened stream must carry the same URL the page rendered the level into"
    assert report["createdAfterIdlePageshow"] == 2, \
        "a pageshow with nothing suspended must NOT open a second stream"
    assert report["openAfterSecondPagehide"] == 0, "the cycle must hold on every park"
    assert report["openAfterSecondPageshow"] == 1, "the cycle must hold on every restore"


def test_the_harness_reports_the_leak_for_a_script_that_does_not_suspend(tmp_path):
    """POSITIVE CONTROL: the same harness against the SAME file with only the pagehide listener
    removed must report the held connection. A comparison that cannot fail proves nothing."""
    original = SHELL_JS.read_text(encoding="utf-8")
    assert SUSPEND_LISTENER in original, "the listener this control removes is not in the shipped file"
    target = tmp_path / "shell.js"
    target.write_text(original.replace(SUSPEND_LISTENER, ""), encoding="utf-8")

    report = _run(target)
    assert report["closedOnPagehide"] is False, report
    assert report["openAfterPagehide"] == 1, report
    assert report["openedOnLoad"] == 1, "the control must still be the live script, just without the close"


#: the gate that makes the close a PARK-only close, and the guard that makes the restore reconnect the
#: STREAM whatever armed the fallback while the page was parked. Pinned by text so each control below
#: can remove exactly one of them and nothing else.
PARK_GATE = "    if (event && event.persisted !== true) { return; }\n"
RESTORE_GUARD = ('  window.addEventListener("pageshow", function () {\n'
                 '    if (source) { return; }\n'
                 '    if (poll) { clearInterval(poll); poll = null; }\n'
                 '    connect();\n'
                 '  });')
OLD_GUARD = ('  window.addEventListener("pageshow", function () {\n'
             '    if (!source && !poll) { connect(); }\n'
             '  });')


def test_a_pagehide_that_is_not_a_park_does_not_close_the_stream():
    """``event.persisted`` is the browser's own statement that this document STAYS ALIVE. With it false
    the page is being DESTROYED, and the connection goes with the document — so the close frees nothing,
    while a navigation that does not complete (a cancelled or refused one, a same-document restore)
    would leave a SURVIVING page with no stream and no ``pageshow`` to reopen it."""
    report = _run(SHELL_JS)
    assert report["closedOnNonParkPagehide"] is False, \
        ("a pagehide that is not a back/forward-cache park must not close the stream: that document is "
         "being destroyed, so the close frees no connection, and a navigation that does not complete "
         "would leave the page with no stream and no pageshow to reopen it")
    assert report["openAfterNonParkPagehide"] == 1, \
        "the stream of a page that is not parked must stay open"
    # the PARK is still closed — the leak fix is untouched (see the two tests above)
    assert report["closedOnPagehide"] is True and report["openAfterPagehide"] == 0, report


def test_a_page_restored_with_the_polling_fallback_armed_reconnects_the_stream():
    """A stream aborted by a navigation fires ``error``, and the shipped ``onerror`` arms the polling
    fallback. A page restored with that fallback armed must still get its STREAM back: the fallback is
    a different transport, and it can stop for good (an expired session answers 401/403 and
    ``stopPolling`` never restarts it), which leaves the page with no live transport at all."""
    report = _run(SHELL_JS)
    assert report["pollArmedByAbort"] == 1, \
        "the abort error must arm the fallback — otherwise this sequence proves nothing"
    assert report["createdAfterRestoreWithPoll"] == 4, \
        ("a restored page must reconnect the STREAM whatever ran while it was parked; the fallback "
         "must not suppress the reconnect")
    assert report["openAfterRestoreWithPoll"] == 1, "exactly one stream must be open after the restore"
    assert report["pollAfterRestore"] == 0, \
        "the fallback must be dropped when the stream comes back, so the two never both run"
    assert report["reopenedAfterRestoreWithPoll"] == "/api/dashboard/stream?level=info", \
        "the reopened stream must carry the same URL the page rendered the level into"


def test_the_harness_reports_the_dead_page_for_a_script_that_closes_on_every_pagehide(tmp_path):
    """POSITIVE CONTROL for the two tests above: the SAME file with only the park gate removed must
    report the closed stream (and, with the guard left as it was, no reconnect)."""
    original = SHELL_JS.read_text(encoding="utf-8")
    assert PARK_GATE in original, "the park gate this control removes is not in the shipped file"
    target = tmp_path / "shell.js"
    target.write_text(original.replace(PARK_GATE, ""), encoding="utf-8")

    report = _run(target)
    assert report["closedOnNonParkPagehide"] is True, report
    assert report["openAfterNonParkPagehide"] == 0, report
    assert report["openedOnLoad"] == 1, "the control must still be the live script, just without the gate"


def test_the_harness_reports_the_skipped_reconnect_for_the_old_guard(tmp_path):
    """POSITIVE CONTROL for the restore test: the SAME file with only the reopen guard reverted must
    report that the restore did NOT bring the stream back (and that the fallback is still running)."""
    original = SHELL_JS.read_text(encoding="utf-8")
    assert RESTORE_GUARD in original, "the guard this control reverts is not in the shipped file"
    target = tmp_path / "shell.js"
    target.write_text(original.replace(RESTORE_GUARD, OLD_GUARD), encoding="utf-8")

    report = _run(target)
    assert report["createdAfterRestoreWithPoll"] == 3, \
        "the old guard must be shown to skip the reconnect (the comparison has to be able to fail)"
    assert report["openAfterRestoreWithPoll"] == 0, report
    assert report["pollAfterRestore"] == 1, report
    assert report["openedOnLoad"] == 1, "the control must still be the live script, just with the old guard"
