"""The config-write primitive: round-trip fidelity, validation, atomicity, `.bak`, honesty.

``core/utils/configwrite`` is the ONE path every console config write will take. These tests prove the
four properties the card names, against the real primitive and the bot's OWN shipped validator
(``core.utils.validators.validate`` — pykwalify, not a second implementation):

* **round-trip fidelity** — comments, blank lines, ordering, quoting and block scalars survive; a file
  re-saved with no change comes back **byte-identical**, and a file with one intended change differs
  from the original ONLY on that change's line;
* **validation refuses a bad edit** — the target on disk is untouched and no ``.bak`` is made;
* **atomicity** — a change writes through a sibling temp file + ``os.replace``; no temp file is left
  behind and a ``.bak`` of the previous generation exists;
* **the refusal says what happened** — the exception names the file and the reason;
* **the apply story is honest** — the result defaults to ``APPLIES_ON_RESTART``.

WHAT THESE TESTS DO NOT COVER, stated plainly:

* **A real bot restart applying the change.** The primitive only writes the file; that the running bot
  then picks it up is not exercised here (it cannot be — the bot reads its config once at process
  start, ``Node.__init__`` / ``NodeData`` / ``Server.locals``). The tests prove the FILE contract,
  and the console's honest "restart applies" message is the caller's, not this module's.
* **The schema's own correctness.** Validation is delegated to the shipped schemas; a test here proves
  the primitive CALLS the validator and refuses on its verdict, not that a given schema is right.
* **Crash-durability of the directory entry.** ``os.replace`` is atomic, but the parent directory is
  not fsync'd, so a whole-machine power loss in the microsecond after the rename is out of scope.
* **Concurrent writers on different processes.** The lock is per-process; two OS processes writing the
  same file are not serialised (the bot and its console are one process, which is the case in hand).
"""
from __future__ import annotations

import difflib
import hashlib
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from core.utils import configwrite
from core.utils.configwrite import (
    APPLIES_NOW,
    APPLIES_ON_RESTART,
    ConfigParseError,
    ConfigValidationError,
    StaleConfigRevision,
    config_revision,
    dump_config,
    load_config_file,
    load_config_text,
    patch_config_file,
    save_config_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVERS_SCHEMA = str(REPO_ROOT / "schemas" / "servers_schema.yaml")

#: A config file in the project's CANONICAL style (``mapping=2, sequence=4, offset=2``) carrying
#: everything a hand-maintained file has: a leading comment, an aligned trailing comment, a blank
#: line, a block scalar, a flow sequence with MIXED quoting, and a real sequence. ruamel's round trip
#: reproduces it byte-for-byte (asserted below), which is what makes the "only the intended change"
#: assertion meaningful.
CANONICAL = """\
# servers.yaml \u2014 an operator's own file.
DEFAULT:
  message_timeout: 10           # seconds
  profanity_filter: true        # default: false

My Server:  # the display name
  server_user: Admin            # technical user
  channels:
    status: 1122334455667788    # the status embed channel
    chat: -1                    # disabled
  rules: |
    Line one
    Line two
  aliases: ['a', "b"]           # quoting must survive
  discord:
    - '@everyone'
    - DCS Admin
"""

#: A file in a NON-canonical style (sequence items at the parent's indent). ruamel's emitter would
#: straighten that indentation if it wrote — which is exactly why the no-op rule must not write.
MESSY = """\
# a hand file, indented the hand's way
root:
  items:
  - one   # first
  - two
"""

#: A minimal valid ``servers.yaml`` for the schema test. ``message_timeout`` has ``range: min 5, max 60``
#: in the shipped schema, so 999 is a refusal the SCHEMA makes, not this test.
SERVERS_TINY = """\
DEFAULT:
  message_timeout: 10
"""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def load_yaml(path: Path) -> Any:
    return load_config_file(path)


# --------------------------------------------------------------- 1. round-trip fidelity

def test_the_serializer_reproduces_a_canonical_file_byte_for_byte():
    """The strongest fidelity claim: the round-trip loader, given the file, emits the SAME bytes."""
    assert dump_config(load_config_text(CANONICAL)) == CANONICAL


def test_a_no_op_save_leaves_a_hand_formatted_file_untouched(tmp_path):
    """A file that is NOT in the canonical style is not rewritten when nothing is edited: the
    no-op rule leaves the bytes exactly as the operator wrote them (no re-indent, no re-wrap)."""
    path = tmp_path / "servers.yaml"
    write(path, MESSY)

    result = save_config_file(path, load_yaml(path))

    assert result.changed is False
    assert path.read_text(encoding="utf-8") == MESSY, "a no-op must not reformat the file"
    assert not (tmp_path / "servers.yaml.bak").exists(), "a no-op makes no backup"
    assert result.backup is None
    assert result.revision == config_revision(path) == sha(MESSY)


def test_a_real_change_edits_only_the_intended_line(tmp_path):
    """A single change to a canonical file changes exactly one line and leaves comments, blank lines,
    ordering and quoting intact — the property ``safe_load`` -> ``dump`` destroys."""
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)

    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 15

    result = save_config_file(path, document)

    assert result.changed is True
    after = path.read_text(encoding="utf-8")
    diff = list(difflib.unified_diff(CANONICAL.splitlines(), after.splitlines(),
                                     lineterm="", n=0))
    added = [line for line in diff if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in diff if line.startswith("-") and not line.startswith("---")]
    assert added == ["+  message_timeout: 15           # seconds"], f"unexpected diff: {diff}"
    assert removed == ["-  message_timeout: 10           # seconds"], f"unexpected diff: {diff}"

 # the file's own shape is intact
    assert "# servers.yaml" in after                              # leading comment
    assert "# the display name" in after                          # trailing comment on a key
    assert "\n\nMy Server:" in after                              # the blank line
    assert "aliases: ['a', \"b\"]" in after                       # mixed quoting survived
    assert "rules: |\n    Line one\n    Line two\n" in after      # the block scalar survived


def test_a_second_identical_save_is_a_no_op(tmp_path):
    """Writing the same document twice never rewrites: the second call reports ``changed=False``."""
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)
    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 15
    save_config_file(path, document)

    again = save_config_file(path, load_yaml(path))

    assert again.changed is False
    assert again.backup is None


# --------------------------------------------------------------- 2. validation refuses a bad edit

def test_a_bad_edit_is_refused_and_the_file_is_untouched(tmp_path):
    """The shipped ``servers_schema.yaml`` refuses ``message_timeout: 999`` (range max 60): nothing is
    written, no backup is made, and no temp file is left behind."""
    path = tmp_path / "servers.yaml"
    write(path, SERVERS_TINY)

    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 999

    with pytest.raises(ConfigValidationError) as caught:
        save_config_file(path, document, schemas=[SERVERS_SCHEMA])

    assert path.read_text(encoding="utf-8") == SERVERS_TINY, "the target must be untouched"
    assert not (tmp_path / "servers.yaml.bak").exists(), "a refused write makes no backup"
    assert not [p for p in tmp_path.iterdir() if ".tmp" in p.name], "no temp file may be left behind"
    assert [p.name for p in tmp_path.iterdir()] == ["servers.yaml"]


def test_the_refusal_names_the_file_and_the_reason(tmp_path):
    """The refusal is a sentence a caller can render: it names the file and what was wrong."""
    path = tmp_path / "servers.yaml"
    write(path, SERVERS_TINY)
    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 999

    with pytest.raises(ConfigValidationError) as caught:
        save_config_file(path, document, schemas=[SERVERS_SCHEMA])

    message = str(caught.value)
    assert "servers.yaml" in message
    assert "not valid against" in message
    assert caught.value.path == path
    assert caught.value.schemas == [SERVERS_SCHEMA]
    assert caught.value.errors, "the validator's own detail must ride along"
 # the refusal is the SCHEMA's verdict, not a parse/read failure: the range is what refused 999
    assert "max limit '60'" in caught.value.errors


def test_a_valid_edit_against_the_shipped_schema_is_accepted(tmp_path):
    """The same schema that refuses 999 accepts a value inside its range — the validator is really
    the deciding factor, not the primitive."""
    path = tmp_path / "servers.yaml"
    write(path, SERVERS_TINY)
    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 20

    result = save_config_file(path, document, schemas=[SERVERS_SCHEMA])

    assert result.changed is True
    assert load_yaml(path)["DEFAULT"]["message_timeout"] == 20


# --------------------------------------------------------------- 3. atomicity and the `.bak`

def test_a_change_leaves_no_temp_file_and_one_backup(tmp_path):
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)
    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 15

    result = save_config_file(path, document)

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["servers.yaml", "servers.yaml.bak"], f"unexpected files: {names}"
    assert result.backup == tmp_path / "servers.yaml.bak"
    assert result.backup.read_text(encoding="utf-8") == CANONICAL, "the .bak is the previous generation"


def test_the_backup_always_holds_the_previous_generation(tmp_path):
    """Two successive writes: each ``.bak`` holds exactly the content that was there before it."""
    path = tmp_path / "servers.yaml"
    write(path, SERVERS_TINY)

    first = load_yaml(path)
    first["DEFAULT"]["message_timeout"] = 20
    save_config_file(path, first)
    generation_one = path.read_text(encoding="utf-8")

    second = load_yaml(path)
    second["DEFAULT"]["message_timeout"] = 30
    save_config_file(path, second)

    assert (tmp_path / "servers.yaml.bak").read_text(encoding="utf-8") == generation_one
    assert load_yaml(path)["DEFAULT"]["message_timeout"] == 30


def test_a_brand_new_file_has_no_backup(tmp_path):
    path = tmp_path / "servers.yaml"

    result = save_config_file(path, load_config_text(SERVERS_TINY))

    assert result.changed is True
    assert result.backup is None
    assert not (tmp_path / "servers.yaml.bak").exists()
    assert load_yaml(path)["DEFAULT"]["message_timeout"] == 10


# --------------------------------------------------------------- 4. the revision guard

def test_the_returned_revision_identifies_the_written_generation(tmp_path):
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)
    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 15

    result = save_config_file(path, document)

    assert result.revision == config_revision(path) == sha(path.read_text(encoding="utf-8"))


def test_a_stale_revision_is_refused_and_nothing_is_written(tmp_path):
    """A caller that rendered its form from a revision refuses to write once the file has moved."""
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)
    stale = config_revision(path)

 # somebody else moves the file on
    edited = load_yaml(path)
    edited["DEFAULT"]["message_timeout"] = 20
    save_config_file(path, edited)
    moved = path.read_text(encoding="utf-8")

    candidate = load_yaml(path)
    candidate["DEFAULT"]["profanity_filter"] = False
    with pytest.raises(StaleConfigRevision) as caught:
        save_config_file(path, candidate, expect_revision=stale)

    assert path.read_text(encoding="utf-8") == moved, "a stale write must not land"
    assert caught.value.expected == stale
    assert caught.value.current == config_revision(path)


# --------------------------------------------------------------- 5. patch: load, mutate, save

def test_patch_applies_a_mutation_and_keeps_the_rest(tmp_path):
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)

    def widen(document):
        document["DEFAULT"]["message_timeout"] = 30

    result = patch_config_file(path, widen)

    assert result.changed is True
    after = path.read_text(encoding="utf-8")
    assert "  message_timeout: 30           # seconds" in after
    assert "# servers.yaml" in after and "rules: |" in after


def test_patch_of_a_missing_file_starts_from_an_empty_document(tmp_path):
    path = tmp_path / "servers.yaml"

    result = patch_config_file(path, lambda document: document.update({"DEFAULT": {}}))

    assert result.changed is True
    assert load_yaml(path) == {"DEFAULT": {}}


# --------------------------------------------------------------- 6. parse failures and honesty

def test_a_malformed_file_is_refused_with_a_parse_error():
    with pytest.raises(ConfigParseError):
        load_config_text("root: [unclosed\n", path="servers.yaml")


def test_the_result_carries_the_honest_apply_story(tmp_path):
    """By default a write is NOT live — it applies on the next bot restart — and a caller may say
    otherwise only when its write genuinely refreshed memory."""
    path = tmp_path / "servers.yaml"

    default = save_config_file(path, load_config_text(SERVERS_TINY))
    assert default.applies == APPLIES_ON_RESTART
    assert default.applies_on_restart is True

    now = save_config_file(path, load_config_text("DEFAULT:\n  message_timeout: 20\n"),
                           applies=APPLIES_NOW)
    assert now.applies == APPLIES_NOW
    assert now.applies_on_restart is False


def test_a_missing_directory_is_a_clear_refusal(tmp_path):
    path = tmp_path / "nope" / "servers.yaml"

    with pytest.raises(configwrite.ConfigWriteError) as caught:
        save_config_file(path, load_config_text(SERVERS_TINY))

    assert "does not exist" in str(caught.value)


# --------------------------------------------------------------- 7. integration with a real file

def test_a_real_shipped_config_round_trips_and_validates(tmp_path):
    """``samples/servers.yaml`` — a real, comment-heavy config — validates against the shipped schema,
    re-saves as a no-op byte-for-byte, and takes a real change the schema allows."""
    source = (REPO_ROOT / "samples" / "servers.yaml").read_text(encoding="utf-8")
    path = tmp_path / "servers.yaml"
    write(path, source)

    noop = save_config_file(path, load_yaml(path), schemas=[SERVERS_SCHEMA])
    assert noop.changed is False, "a real file the operator has not touched must not be rewritten"
    assert path.read_text(encoding="utf-8") == source

    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 20
    changed = save_config_file(path, document, schemas=[SERVERS_SCHEMA])
    assert changed.changed is True
    assert load_yaml(path)["DEFAULT"]["message_timeout"] == 20


# --------------------------------------------------------------- 8. the target's identity survives

#: Windows has no POSIX mode bits: ``os.chmod`` there only toggles the read-only attribute, so
#: ``stat`` reports 0666 whatever was asked for. The mode-preservation contract is POSIX-only.
posix_modes = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows reports no POSIX mode, only the read-only bit: chmod 0640 cannot be observed")


@posix_modes
def test_a_save_preserves_the_target_mode_and_the_backup_mode(tmp_path):
    """A 0640 config stays 0640 after a save, and its ``.bak`` is 0640 too.

    This is the blocker the review proved: ``mkstemp`` writes 0600 and ``os.replace`` promotes it with
    no ``chmod``, so before this fix a save silently rewrote the config — and its backup — to 0600.
    """
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)
    os.chmod(path, 0o640)
    before = stat.S_IMODE(path.stat().st_mode)

    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 15
    save_config_file(path, document)

    after = stat.S_IMODE(path.stat().st_mode)
    backup_mode = stat.S_IMODE((tmp_path / "servers.yaml.bak").stat().st_mode)
    print(f"mode before={before:04o} mode after={after:04o} .bak mode={backup_mode:04o}")

 # the exact assertion the card asks to be quoted: 0640 in, 0640 out, 0640 in the backup
    assert before == 0o640
    assert after == 0o640, f"target mode changed: {before:04o} -> {after:04o}"
    assert backup_mode == 0o640, f".bak mode is {backup_mode:04o}, not the original {before:04o}"


@posix_modes
def test_a_no_op_save_never_touches_the_mode(tmp_path):
    """A no-op writes nothing, so even a 0600 file is left at 0600 — no surprise chmod on a re-save."""
    path = tmp_path / "servers.yaml"
    write(path, MESSY)
    os.chmod(path, 0o600)

    result = save_config_file(path, load_yaml(path))

    assert result.changed is False
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@posix_modes
def test_a_brand_new_file_gets_the_new_file_mode_not_0600(tmp_path):
    """A file created from scratch gets the project's ordinary new-file mode, not mkstemp's 0600."""
    path = tmp_path / "servers.yaml"

    save_config_file(path, load_config_text(SERVERS_TINY))

    assert stat.S_IMODE(path.stat().st_mode) == configwrite._new_file_mode()
    assert stat.S_IMODE(path.stat().st_mode) != 0o600


@posix_modes
def test_a_save_survives_a_platform_without_os_chown(tmp_path, monkeypatch):
    """The Windows situation: ``os.chown`` does not exist, so the save must not touch it.

    This is the reported crash: ``_apply_identity`` called ``os.chown`` (POSIX-only) wrapped in
    ``suppress(OSError)``, but deleting the attribute on Windows raises **AttributeError**, which
    ``suppress(OSError)`` does not catch — so every save of an EXISTING file crashed. With the
    capability guard the fix-up is skipped and the file is still written correctly.
    """
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)
    os.chmod(path, 0o640)
    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 15

 # Simulate Windows: the attribute is simply not there.
    monkeypatch.delattr(os, "chown", raising=False)
    assert not hasattr(os, "chown")

    result = save_config_file(path, document)

    assert result.changed is True
    assert load_yaml(path)["DEFAULT"]["message_timeout"] == 15
 # the write is complete and atomic: no temp file left, one backup
    assert sorted(p.name for p in tmp_path.iterdir()) == ["servers.yaml", "servers.yaml.bak"]
 # the mode fix-up still happened (chmod is available everywhere) — 0640 in, 0640 out
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert stat.S_IMODE((tmp_path / "servers.yaml.bak").stat().st_mode) == 0o640


def test_a_save_survives_a_chown_that_raises_attributeerror(tmp_path, monkeypatch):
    """The other half of the Windows situation, and any platform with a broken ``chown`` shim: the
    attribute exists but raises AttributeError. The save must degrade, not crash."""
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)
    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 15

    def broken_chown(*_args, **_kwargs):
        raise AttributeError("module 'os' has no attribute 'chown'")

    monkeypatch.setattr(os, "chown", broken_chown, raising=False)

    result = save_config_file(path, document)

    assert result.changed is True
    assert load_yaml(path)["DEFAULT"]["message_timeout"] == 15
 # the.bak was written through the same guarded path and also survived
    assert (tmp_path / "servers.yaml.bak").read_text(encoding="utf-8") == CANONICAL


@pytest.mark.skipif(not hasattr(os, "chown"),
                    reason="os.chown is POSIX-only: the Windows half is the test above")
def test_the_ownership_fix_up_is_still_attempted_when_chown_is_available(tmp_path, monkeypatch):
    """The guard must skip only when the capability is missing — never silently drop a real chown.

    On a POSIX host with ``os.chown`` present, the fix-up is still called for an existing file.
    """
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)
    document = load_yaml(path)
    document["DEFAULT"]["message_timeout"] = 15

    calls = []
    real_chown = os.chown

    def recording_chown(p, uid, gid):
        calls.append((str(p), uid, gid))
        return real_chown(p, uid, gid)

    monkeypatch.setattr(os, "chown", recording_chown, raising=False)

    save_config_file(path, document)

 # the temp file (and the.bak) are chown'd BEFORE the swap, so at least one call must have run
    assert calls, "chown must still be called where it exists"
 # and it was called with the target's own uid/gid (a no-op ownershsip hand-back)
    uid, gid = os.stat(path).st_uid, os.stat(path).st_gid
    assert all(call[1] == uid and call[2] == gid for call in calls)


# --------------------------------------------------------------- 9. the no-op rule, both directions

def test_a_comment_only_edit_is_a_real_change(tmp_path):
    """A comment-only edit must NOT be swallowed. ruamel document equality ignores comments, so the
    no-op compares the SERIALISED bytes: an added eol comment changes them and the write lands."""
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)

    document = load_yaml(path)
    document["DEFAULT"].yaml_add_eol_comment("tuned by ops", key="message_timeout")

    result = save_config_file(path, document)

    assert result.changed is True, "a comment-only edit must not be a silent no-op"
    assert result.backup is not None
    assert "tuned by ops" in path.read_text(encoding="utf-8")


def test_an_unchanged_document_is_still_a_byte_identical_no_op(tmp_path):
    """The other direction: a genuine no-op is byte-identical — nothing is written, no ``.bak``."""
    path = tmp_path / "servers.yaml"
    write(path, CANONICAL)

    result = save_config_file(path, load_yaml(path))

    assert result.changed is False
    assert result.backup is None
    assert path.read_text(encoding="utf-8") == CANONICAL
    assert not (tmp_path / "servers.yaml.bak").exists()
