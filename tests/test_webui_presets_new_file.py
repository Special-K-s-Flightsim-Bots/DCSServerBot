"""A preset FILE from the wizard: the "New file…" choice, its one validation, and the save that creates it.

The card's acceptance criteria, one section each:

* the destination step offers, AFTER the offered files, one "New file…" choice that reveals exactly
  one text input, whose help states the shapes discovery reads;
* the validation is SERVER-SIDE and has ONE home (:func:`presets_page.preset_new_file`): a
  config-relative ``.yaml`` name at the config root (``presets*.yaml``) or under ``presets/``, with no
  ``..`` segment, no leading ``/``, no backslash, no drive letter, no control character and no NUL;
  a name that resolves outside the config directory is refused; a name that ALREADY EXISTS is refused
  so the file is SELECTED, never overwritten. Every refusal is its own plain sentence naming what is
  wrong with the typed text, printed verbatim;
* the file is created by the SAME save that writes the preset — one write, one revision — and the
  review step SHOWS the file before the Save button is pressed;
* the wizard says the honest half: a file no node's ``MizEdit.presets`` names is read by nothing, and
  the sentence says where it is added;
* afterwards the new file is a tab on the page and an offered destination, and the remembered parse
  from the document cache does not hide it (a new file is a new cache key);
* the permission is unchanged: ``presets.write``, no new capability.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), with the write suite's own harness reused so the app under test is the running one.
"""
from __future__ import annotations

import hashlib
import html as html_module
import re
from pathlib import Path

import pytest

from conftest import needs_symlinks, symlinks_available

from services.webservice.pages import presets as presets_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, fleet, sign_in,
)

#: a plain preset the install already carries, so the destination rule has something to offer.
SPRING = """\
Spring:
  date: '2016-04-21'
  temperature: 8
  qnh: 760
"""

#: ``nodes.yaml`` naming ``config/presets.yaml`` — the positive control for the "nobody reads it" line.
NODES_NAMING_ONE = """\
NODE1:
  instances:
    Instance1:
      extensions:
        MizEdit:
          presets: config/presets.yaml
"""

#: the name the wizard is asked to create, and the file that lands on disk.
NEW_FILE = "config/presets_night.yaml"

#: the EXACT document the offered-file flow writes, captured from the flow before the "New file…"
#: choice existed — the regression baseline.
UNCHANGED_DESTINATION_DOCUMENT = """\
Summer:
  temperature: 23
Fresh:
  date: '2016-04-21'
  temperature: 4
"""


def preset_file(config_dir: Path, text: str = SPRING, name: str = "presets.yaml") -> Path:
    path = config_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def nodes_file(config_dir: Path, text: str) -> Path:
    path = config_dir / "nodes.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hidden_fields(html: str) -> dict[str, str]:
    """Every ``<input type="hidden" name=… value=…>`` the page rendered, unescaped."""
    out: dict[str, str] = {}
    for match in re.finditer(r'<input type="hidden" name="([^"]*)" value="([^"]*)"', html):
        out[match.group(1)] = html_module.unescape(match.group(2))
    return out


async def page(http, path: str) -> str:
    response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.text


def wizard_url(name: str, *, step: str, new_file: str = "", file: str = "") -> str:
    """The new-preset wizard's review/anchor URL carrying the destination the step rendered."""
    url = f"{presets_page.PRESETS_NEW_PATH}?step={step}&name={name}"
    if file:
        url += f"&file={file}"
    if new_file:
        url += f"&{presets_page.PRESETS_NEW_FILE_FIELD}={new_file}"
    return url


async def review_form(http, name: str, *, new_file: str = "", file: str = "") -> dict[str, str]:
    """The form the wizard's review step rendered — the fields the route reads."""
    return hidden_fields(await page(http, wizard_url(name, step="review", new_file=new_file,
                                                     file=file)))


def text(html: str) -> str:
    """The page as READ — entities resolved, so a sentence's own apostrophes compare plainly."""
    return html_module.unescape(html)


async def post_new(http, name: str, *, new_file: str | None = None, file: str = "",
                   change: dict | None = None):
    """POST the ONE write route for a NEW preset, with the destination the test states.

    The review step is RENDERED with the same typed name the URL would carry (that is where its
    revision comes from), and the name is then posted explicitly — a name the step REFUSES is not
    re-rendered as a destination, so a form read off the page could not carry it.
    """
    render = new_file if new_file is not None else ""
    if any(ord(char) < 32 for char in render):
        render = ""            # a control character is not a URL; state it on the POST instead
    data = await review_form(http, name, new_file=render, file=file)
    data["_action"] = "save"
    if new_file is not None:
        data[presets_page.PRESETS_NEW_FILE_FIELD] = new_file
    if change:
        data.update(change)
    return await http.post(presets_page.PRESETS_SAVE_PATH, data=data)


# ------------------------------------------------------------- 1. the validation, as ONE home

@pytest.mark.parametrize("text, expected", [
    ("config/presets_night.yaml", Path("presets_night.yaml")),
    ("config/presets_*.yaml".replace("*", "x"), Path("presets_x.yaml")),
    ("config/presets/night.yaml", Path("presets") / "night.yaml"),
])
def test_the_accepted_shapes_are_the_forms_discovery_reads(tmp_path, text, expected):
    """The three shapes the help names are exactly what is accepted — and they land under config."""
    path, refusal, _params = presets_page.preset_new_file(tmp_path, text)
    assert refusal == "", refusal
    # the answer is the RESOLVED destination, so it is compared on the resolved pair
    assert path is not None and path.resolve() == (tmp_path / expected).resolve()


def test_the_help_states_the_shapes_the_validation_accepts():
    """Themselves stated once: every example the help prints is a name the validation accepts."""
    help_text = presets_page.WIZARD_DEST_NEW_HELP
    examples = re.findall(r"config/[A-Za-z0-9_./*]+", help_text)
    assert examples, f"the help names no accepted shape: {help_text!r}"
    for example in examples:
        name = example.replace("*", "rain") if example.endswith("*.yaml") else example
        if name.endswith("/") or "<" in name:
            continue
        _path, refusal, _params = presets_page.preset_new_file(Path("/tmp/config-probe"), name)
        assert refusal == "", f"the help offers {example!r}, which the validation refuses: {refusal}"


@pytest.mark.parametrize("text, msgid", [
    ("../evil.yaml", presets_page.PRESETS_REFUSED_NEW_FILE_TRAVERSAL),
    ("presets/../../x.yaml", presets_page.PRESETS_REFUSED_NEW_FILE_TRAVERSAL),
    ("/etc/presets.yaml", presets_page.PRESETS_REFUSED_NEW_FILE_ABSOLUTE),
    ("C:\\presets.yaml", presets_page.PRESETS_REFUSED_NEW_FILE_WINDOWS),
    ("presets.yaml\\0", presets_page.PRESETS_REFUSED_NEW_FILE_CONTROL),
    ("notes.txt", presets_page.PRESETS_REFUSED_NEW_FILE_SUFFIX),
    ("sub/presets.yaml", presets_page.PRESETS_REFUSED_NEW_FILE_SHAPE),
])
def test_every_refused_shape_answers_its_own_sentence(tmp_path, text, msgid):
    """Each defect is its own sentence and prints the operator's text verbatim."""
    text = text.replace("\\0", "\x00")
    path, refusal, params = presets_page.preset_new_file(tmp_path, text)
    assert path is None
    assert refusal == msgid, f"{text!r} answered {refusal!r}, expected {msgid!r}"
    assert params.get("file") == text.strip(), "the sentence must print the text as typed"


def test_the_refusal_sentences_are_distinct():
    """No two defects share a sentence: a generic one would not name what is wrong."""
    sentences = {
        getattr(presets_page, name) for name in (
            "PRESETS_REFUSED_NEW_FILE_TRAVERSAL",
            "PRESETS_REFUSED_NEW_FILE_ABSOLUTE", "PRESETS_REFUSED_NEW_FILE_WINDOWS",
            "PRESETS_REFUSED_NEW_FILE_CONTROL", "PRESETS_REFUSED_NEW_FILE_SUFFIX",
            "PRESETS_REFUSED_NEW_FILE_SHAPE", "PRESETS_REFUSED_NEW_FILE_ESCAPE",
            "PRESETS_REFUSED_NEW_FILE_EXISTS")
    }
    assert len(sentences) == 8, "two refusal shapes share one sentence"


def test_a_blank_name_is_not_a_request(tmp_path):
    """A name that is not typed is not "create this" — the route writes the offered destination."""
    path, refusal, _params = presets_page.preset_new_file(tmp_path, "   ")
    assert path is None and refusal, "a blank name is never a destination"


@needs_symlinks
def test_a_name_that_resolves_outside_the_config_dir_is_refused(tmp_path):
    """A ``presets/`` that is a symlink out of the config tree escapes the ``..`` check — still refused."""
    outside = tmp_path / "outside"
    outside.mkdir()
    config = tmp_path / "config-dir"
    config.mkdir()
    (config / "presets").symlink_to(outside, target_is_directory=True)
    path, refusal, params = presets_page.preset_new_file(config, "config/presets/night.yaml")
    assert path is None
    assert refusal == presets_page.PRESETS_REFUSED_NEW_FILE_ESCAPE
    assert params["file"] == "config/presets/night.yaml"
    assert not (outside / "night.yaml").exists()


def test_an_existing_file_is_never_a_new_name(tmp_path):
    """e) an existing file must be SELECTED: naming it as new is refused, not written over."""
    existing = preset_file(tmp_path)
    before = sha256(existing)
    path, refusal, params = presets_page.preset_new_file(tmp_path, "config/presets.yaml")
    assert path is None
    assert refusal == presets_page.PRESETS_REFUSED_NEW_FILE_EXISTS
    assert params["file"] == "config/presets.yaml"
    assert sha256(existing) == before


# ------------------------------------------------------- 2. the save that creates the file

@pytest.mark.asyncio
async def test_a_new_file_is_created_with_exactly_the_new_preset(config_dir, clean_registry):
    """The SAME save writes the preset and creates the file: one write, one revision."""
    from core.utils import configwrite

    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_new(http, "Night", new_file=NEW_FILE, change={"temperature": "3"})
        assert reply.status_code == 303, reply.text
        assert reply.headers["location"] == f"{presets_page.wizard_url('Night')}?step=review"
        created = config_dir / "presets_night.yaml"

    assert created.is_file(), "the save creates the file it was pointed at"
    document = configwrite.load_config_file(created)
    assert list(document) == ["Night"], "exactly the new preset, nothing else"
    assert document["Night"]["temperature"] == 3
    # the document is YAML the tree's own reader and the extension's own schema accept
    configwrite.validate_against_schema(configwrite.dump_config(document),
                                       presets_page.preset_schema_files(), path=created)
    # a file that did not exist has no previous generation to keep
    assert not (config_dir / "presets_night.yaml.bak").exists(), (
        "the primitive keeps a .bak only where there was a previous file")


@pytest.mark.asyncio
async def test_the_second_write_into_the_new_file_keeps_a_backup(config_dir, clean_registry):
    """The primitive's ``.bak`` behaviour is untouched: the next write into it keeps one."""
    from core.utils import configwrite

    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        await post_new(http, "Night", new_file=NEW_FILE, change={"temperature": "3"})
        # the file is now an ordinary preset file; the wizard OPENS the preset it carries and saves
        data = hidden_fields(await page(
            http, f"{presets_page.wizard_url('Night')}?step=review"))
        data["_action"] = "save"
        data["temperature"] = "5"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303, reply.text

    created = config_dir / "presets_night.yaml"
    backup = config_dir / "presets_night.yaml.bak"
    assert configwrite.load_config_file(created)["Night"]["temperature"] == 5
    assert backup.is_file(), "a real write keeps a backup"
    assert configwrite.load_config_file(backup)["Night"]["temperature"] == 3


@pytest.mark.asyncio
async def test_the_new_file_is_a_tab_on_the_page_afterwards(config_dir, clean_registry):
    """Afterwards the file is a tab: discovery reads it, and the remembered parse cannot hide it."""
    preset_file(config_dir)
    preset_file(config_dir, "Summer:\n  temperature: 23\n", name="presets2.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        # the page is rendered FIRST, so the document cache is warm for the files that exist now
        before = await page(http, presets_page.PRESETS_PATH)
        await post_new(http, "Night", new_file=NEW_FILE, change={"temperature": "3"})
        after = await page(http, presets_page.PRESETS_PATH)

    assert "config/presets_night.yaml" not in before
    assert "config/presets_night.yaml" in after, "the new file is a tab on the next render"


@pytest.mark.asyncio
async def test_a_new_file_is_a_new_cache_key(config_dir, clean_registry):
    """The parse cache is keyed by the file's own stat, so a file that did not exist is not hidden."""
    first = preset_file(config_dir)
    answer_before = presets_page.read_preset_file(first)
    assert "Spring" in answer_before.document
    # the file that does not exist yet is never served from the cache
    absent = config_dir / "presets_night.yaml"
    assert not presets_page.read_preset_file(absent).parsed
    absent.write_text("Night:\n  temperature: 3\n", encoding="utf-8")
    answer_after = presets_page.read_preset_file(absent)
    assert answer_after.parsed and "Night" in answer_after.document
    # and the sibling that was already parsed is untouched
    assert "Spring" in presets_page.read_preset_file(first).document


@pytest.mark.asyncio
async def test_the_new_file_is_an_offered_destination_on_the_next_visit(config_dir, clean_registry):
    """With no node naming a file, the offer is discovery — so the created file is offered next time."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        await post_new(http, "Night", new_file=NEW_FILE, change={"temperature": "3"})
        html = await page(http, presets_page.PRESETS_NEW_PATH)

    assert presets_page.WIZARD_DEST_MANY in html
    assert "config/presets_night.yaml" in html


# ------------------------------------------------------------- 3. the review names the file

@pytest.mark.asyncio
async def test_the_review_step_names_the_file_that_will_be_created(config_dir, clean_registry):
    """The operator sees the file they are about to write, BEFORE pressing Save — and nothing exists."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, wizard_url("Night", step="review", new_file=NEW_FILE))

    assert presets_page.WIZARD_DEST_NEW_ONE.replace("%(file)s", NEW_FILE) in html.replace(
        "&#39;", "'")
    assert not (config_dir / "presets_night.yaml").exists(), "the GET creates nothing"
    # and the form still carries the name, so the save validates it again
    fields = hidden_fields(html)
    assert fields.get(presets_page.PRESETS_NEW_FILE_FIELD) == NEW_FILE


@pytest.mark.asyncio
async def test_the_review_step_refuses_to_name_a_file_it_would_not_create(config_dir,
                                                                         clean_registry):
    """A typed name the validation refuses is NOT shown as the destination — the step says why."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, wizard_url("Night", step="review", new_file="../evil.yaml"))

    assert presets_page.WIZARD_DEST_NEW_ONE.replace("%(file)s", "../evil.yaml") not in html
    assert "carries a '..' segment" in text(html)


# ------------------------------------------------- 4. the honest half: nobody reads a new file

@pytest.mark.asyncio
async def test_the_wizard_says_a_new_file_is_read_by_nobody(config_dir, clean_registry):
    """A file no node names is read by nothing — the step says so and where to add it."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, wizard_url("Night", step="review", new_file=NEW_FILE))

    sentence = presets_page.WIZARD_DEST_UNREAD.replace("%(file)s", NEW_FILE)
    assert sentence in text(html), "a new file no node names must be named as unread"
    assert "MizEdit.presets" in sentence and "Settings" in sentence, (
        "the sentence must name the setting and where it is added")


@pytest.mark.asyncio
async def test_the_sentence_is_absent_for_a_destination_a_node_names(config_dir, clean_registry):
    """The positive control: a file a node's ``MizEdit.presets`` names carries no such line."""
    preset_file(config_dir)
    nodes_file(config_dir, NODES_NAMING_ONE)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, wizard_url("Night", step="review", file="config/presets.yaml"))

    assert "so nothing reads it" not in html
    assert presets_page.WIZARD_DEST_ONE.replace("%(file)s", "config/presets.yaml") in html


@pytest.mark.asyncio
async def test_the_default_file_carries_its_own_sentence(config_dir, clean_registry):
    """``config/presets.yaml`` is what an instance naming no preset files reads — so "nothing reads
    it" would be false, and that file gets the sentence that says exactly that."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = text(await page(http, presets_page.PRESETS_NEW_PATH))

    assert "so nothing reads it" not in html, "the default file IS read by an unnamed instance"
    assert presets_page.WIZARD_DEST_UNREAD_DEFAULT.replace(
        "%(file)s", "config/presets.yaml") in html


# --------------------------------------------------------------- 5. every refusal, end to end

@pytest.mark.parametrize("typed, msgid", [
    ("../evil.yaml", "PRESETS_REFUSED_NEW_FILE_TRAVERSAL"),
    ("/etc/presets.yaml", "PRESETS_REFUSED_NEW_FILE_ABSOLUTE"),
    ("presets/../../x.yaml", "PRESETS_REFUSED_NEW_FILE_TRAVERSAL"),
    ("C:\\presets.yaml", "PRESETS_REFUSED_NEW_FILE_WINDOWS"),
    ("presets.yaml\\0", "PRESETS_REFUSED_NEW_FILE_CONTROL"),
    ("notes.txt", "PRESETS_REFUSED_NEW_FILE_SUFFIX"),
    ("sub/presets.yaml", "PRESETS_REFUSED_NEW_FILE_SHAPE"),
])
@pytest.mark.asyncio
async def test_a_refused_name_writes_nothing(config_dir, clean_registry, typed, msgid):
    """Every refused shape: 303 back to the wizard, its own sentence, and no file anywhere."""
    typed = typed.replace("\\0", "\x00")
    path = preset_file(config_dir)
    before = sha256(path)
    listed_before = sorted(item.name for item in config_dir.iterdir())
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_new(http, "Night", new_file=typed, change={"temperature": "3"})
        assert reply.status_code == 303, reply.text
        html = text(await page(http, reply.headers["location"]))

    assert sha256(path) == before, "the offered file is untouched"
    assert sorted(item.name for item in config_dir.iterdir()) == listed_before, "no file is created"
    sentence = getattr(presets_page, msgid).replace("%(file)s", typed.strip())
    assert sentence in html, f"the refusal does not carry its own sentence:\n{sentence}"
    assert typed.strip() in html or not typed.strip(), (
        "the sentence prints the operator's own text")


@pytest.mark.asyncio
async def test_an_existing_file_named_as_new_is_refused_and_selected(config_dir, clean_registry):
    """(e) an existing file is SELECTED, never overwritten — and the page offers it selected."""
    from core.utils import configwrite

    path = preset_file(config_dir)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_new(http, "Night", new_file="config/presets.yaml",
                               change={"temperature": "3"})
        assert reply.status_code == 303, reply.text
        assert reply.headers["location"] == (
            f"{presets_page.PRESETS_NEW_PATH}?file=config%2Fpresets.yaml")
        html = await page(http, reply.headers["location"])

    assert sha256(path) == before, "the existing file is never overwritten"
    assert "already exists" in html and "config/presets.yaml" in html
    assert "Night" not in configwrite.load_config_file(path)


@pytest.mark.asyncio
async def test_the_expert_door_creates_the_file_too(config_dir, clean_registry):
    """The expert door shares the ONE destination rule, so it creates the new file the same way."""
    from core.utils import configwrite

    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Night", new_file=NEW_FILE)
        data.update({"_action": "save", "_expert": "1",
                     "_yaml": "Night:\n  temperature: 3\n"})
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303, reply.text

    created = config_dir / "presets_night.yaml"
    assert created.is_file(), "the expert door does not have a second write path"
    assert list(configwrite.load_config_file(created)) == ["Night"]


# ------------------------------------------------------- 6. the offered-file flow is unchanged

@pytest.mark.asyncio
async def test_picking_an_offered_file_writes_the_same_document_as_before(config_dir,
                                                                        clean_registry):
    """Regression: the existing destination flow produces a byte-identical document for the same input."""
    first = preset_file(config_dir)
    second = preset_file(config_dir, "Summer:\n  temperature: 23\n", name="presets2.yaml")
    first_before = sha256(first)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_new(http, "Fresh", file="config/presets2.yaml",
                               change={"temperature": "4", "date": "2016-04-21"})
        assert reply.status_code == 303, reply.text

    assert sha256(first) == first_before, "the other file is untouched"
    written = second.read_text(encoding="utf-8")
    assert written == UNCHANGED_DESTINATION_DOCUMENT, written


@pytest.mark.asyncio
async def test_the_new_file_field_is_ignored_for_an_existing_preset(config_dir, clean_registry):
    """An edit writes the file its own name was found in — a posted new-file name cannot move it."""
    from core.utils import configwrite

    path = preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = hidden_fields(await page(http, f"{presets_page.wizard_url('Spring')}?step=review"))
        data["_action"] = "save"
        data["temperature"] = "15"
        data[presets_page.PRESETS_NEW_FILE_FIELD] = NEW_FILE
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303, reply.text

    assert configwrite.load_config_file(path)["Spring"]["temperature"] == 15
    assert not (config_dir / "presets_night.yaml").exists()


# ------------------------------------------------------------------- 7. permission is unchanged

@pytest.mark.asyncio
async def test_the_new_file_path_is_behind_presets_write(config_dir, clean_registry):
    """No new capability: a caller without ``presets.write`` cannot create a file either."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "plain")
        reply = await http.post(presets_page.PRESETS_SAVE_PATH,
                                data={"name": "Night", "_action": "save",
                                      presets_page.PRESETS_NEW_FILE_FIELD: NEW_FILE})
        assert reply.status_code == 403, reply.text
    assert not (config_dir / "presets_night.yaml").exists()
