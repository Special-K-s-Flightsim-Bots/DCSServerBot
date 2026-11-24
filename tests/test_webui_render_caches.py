"""THE TWO RENDER CACHES — a page must not re-parse schemas or re-validate a file it just validated.

Measured on the shipped tree: a node page paid ~380 ms parsing the node schema set, ~285 ms for the
extension set and ~270 ms re-validating ``nodes.yaml`` for its notice — on EVERY click, for files that
had not changed. Both are now kept (``configgen.load_schema``'s parse and
``services/webservice/validation.validate_cached``), keyed by the FILES THEMSELVES.

What this module pins, in order:
1. the schema parse is served from memory the second time, and a repeat call does NOT re-read the file;
2. an EDIT to the schema (or to a sibling file its includes resolve in) is picked up on the next call —
   the cache can never serve a stale schema;
3. the render's validation is memoised, and a save still validates freshly;
4. an EDIT to the config invalidates the validation verdict — a broken file is reported at once, and a
   repaired one is reported clean again.
"""
from __future__ import annotations


from pathlib import Path

import pytest

from core.utils import configwrite
from services.webservice import configgen, validation

SCHEMA = """\
type: map
nullable: false
mapping:
  DEFAULT:
    type: map
    nullable: false
    mapping:
      name: {type: str, nullable: false}
      port: {type: int, nullable: false}
"""

GOOD = "DEFAULT:\n  name: x\n  port: 1\n"


@pytest.fixture
def schema_file(tmp_path) -> Path:
    path = tmp_path / "s.yaml"
    path.write_text(SCHEMA, encoding="utf-8")
    return path


@pytest.fixture
def config_file(tmp_path) -> Path:
    path = tmp_path / "main.yaml"
    path.write_text(GOOD, encoding="utf-8")
    return path


# ---------------------------------------------------------------- 1. the schema parse is kept

def test_a_second_load_of_the_same_schema_does_not_re_read_it(schema_file, monkeypatch):
    """The parse is served from memory: the file is READ once for two loads of unchanged content."""
    reads = []
    original = configgen._read_partials

    def counted(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(configgen, "_read_partials", counted)
    first = configgen.load_schema(str(schema_file))
    second = configgen.load_schema(str(schema_file))
    assert first is second, "the second load parsed the schema again"
    assert len(reads) == 1, f"the schema file was read {len(reads)} times for two loads"


def test_an_edited_schema_is_picked_up_on_the_next_load(schema_file):
    """No staleness: touching the file (a real edit) means the next load parses it again."""
    before = configgen.load_schema(str(schema_file))
    edited = SCHEMA.replace("port: {type: int, nullable: false}",
                            "port: {type: int, nullable: false}\n      extra: {type: str}")
    schema_file.write_text(edited, encoding="utf-8")
    after = configgen.load_schema(str(schema_file))
    assert after is not before
    names = list(after.root["mapping"]["DEFAULT"]["mapping"])       # the raw rule dict's keys
    assert "extra" in names, "the edit to the schema was not seen"


def test_an_edited_sibling_schema_is_picked_up_too(tmp_path):
    """The key carries EVERY file the parse reads — a sibling's change invalidates it as well."""
    primary = tmp_path / "primary_schema.yaml"
    sibling = tmp_path / "sibling.yaml"
    primary.write_text("type: map\nnullable: false\nmapping:\n  a: {type: str}\n", encoding="utf-8")
    sibling.write_text("schema;block:\n  type: map\n  mapping:\n    b: {type: str}\n", encoding="utf-8")
    files = [str(primary), str(sibling)]
    first = configgen.load_schema(str(primary), schema_files=files)
    assert "b" in first.partials["block"]["mapping"]
    sibling.write_text("schema;block:\n  type: map\n  mapping:\n    c: {type: str}\n", encoding="utf-8")
    second = configgen.load_schema(str(primary), schema_files=files)
    assert "c" in second.partials["block"]["mapping"], "a sibling's edit was not seen"


# ------------------------------------------- 2. the render's notice check is memoised, a save is not

def test_the_render_check_is_memoised_and_a_save_still_validates_freshly(config_file, schema_file,
                                                                        monkeypatch):
    calls = []
    original = configwrite.validate_against_schema

    def counted(text, schemas, *, path=None):
        calls.append(str(path))
        return original(text, schemas, path=path)

    monkeypatch.setattr(configwrite, "validate_against_schema", counted)
    schemas = [str(schema_file)]
    validation.validate_cached(config_file, schemas)
    validation.validate_cached(config_file, schemas)
    assert len(calls) == 1, f"an unchanged file was validated {len(calls)} times"

    # ... while a SAVE runs the primitive's own check every time (never the memo): this is what
    # refuses a candidate before any file is touched.
    with pytest.raises(configwrite.ConfigValidationError):
        configwrite.validate_against_schema(GOOD.replace("port: 1", "port: nope"), schemas,
                                            path=config_file)
    assert len(calls) == 2, "the save path must not go through the memo"


def test_an_edited_config_invalidates_the_verdict(config_file, schema_file):
    schemas = [str(schema_file)]
    validation.validate_cached(config_file, schemas)                     # clean
    config_file.write_text(GOOD.replace("port: 1", "port: nope"), encoding="utf-8")
    with pytest.raises(configwrite.ConfigValidationError) as caught:
        validation.validate_cached(config_file, schemas)
    assert caught.value.error_path.endswith("/port") or "port" in caught.value.errors
    config_file.write_text(GOOD, encoding="utf-8")
    validation.validate_cached(config_file, schemas)                     # repaired: clean again
    assert validation._CACHE, "the repaired verdict was not kept"


def test_the_cached_refusal_carries_the_same_detail_as_the_primitive(config_file, schema_file):
    """A memoised refusal is INDISTINGUISHABLE from a fresh one — same errors, same error_path."""
    schemas = [str(schema_file)]
    broken = GOOD.replace("port: 1", "port: nope")
    config_file.write_text(broken, encoding="utf-8")
    try:
        configwrite.validate_against_schema(broken, schemas, path=config_file)
        fresh = None
    except configwrite.ConfigValidationError as ex:
        fresh = (str(ex), ex.errors, ex.error_path)
    try:
        validation.validate_cached(config_file, schemas)
        cached = None
    except configwrite.ConfigValidationError as ex:
        cached = (str(ex), ex.errors, ex.error_path)
    assert cached == fresh and fresh is not None


def test_a_page_render_keeps_the_notice_but_skips_the_second_validation(config_file, schema_file,
                                                                       monkeypatch):
    """The end the cache exists for: two renders in a row cost ONE validation and the notice is drawn."""
    calls = []
    original = configwrite.validate_against_schema

    def counted(text, schemas, *, path=None):
        calls.append(str(path))
        return original(text, schemas, path=path)

    monkeypatch.setattr(configwrite, "validate_against_schema", counted)
    schemas = [str(schema_file)]
    config_file.write_text(GOOD.replace("port: 1", "port: nope"), encoding="utf-8")
    for _ in range(3):
        try:
            validation.validate_cached(config_file, schemas)
        except configwrite.ConfigValidationError:
            pass
    assert len(calls) == 1, f"the notice's check ran {len(calls)} times for three renders"
