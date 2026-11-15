"""Dotted custom-entry keys must survive a save — the S1-includes review's silent-loss blocker.

A REGEX container (``regex;(.+)``) lets the OPERATOR invent the key of an entry. That key is free
text and may contain a ``.`` (``greet.user``), which collides with the page's own path separator: a
form field name is ``f.<dotted path>``, and the save path split it on ``.`` — so ``greet.user``
became two tokens, the field resolved to nothing, and the edit VANISHED while the save still
reported success. ``greet.user`` is the exact input the reviewer proved the loss with.

These tests pin the fix at BOTH layers and at the save's own guarantee:

* the shipped ``configgen.js`` ESCAPES a typed key into the row's field names, so a NEW dotted entry
  round-trips (the harness drives the REAL script);
* the save path DECODES every token, so a field whose path carries a dotted key resolves to the same
  document path the page rendered it from;
* a field the tree cannot place is REFUSED (never silently dropped) and the page writes nothing;
* the new refusal string is ``i18n``-marked and filled in every catalog.
"""
from __future__ import annotations

import html as _html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from babel.messages import pofile

from conftest import asgi_client
from services.webservice import configgen
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login

REPO_ROOT = Path(__file__).resolve().parents[1]

#: a minimal schema whose ``commands:`` block is an unbounded-key map (one regex entry per command).
DOTTED_SCHEMA = """\
type: map
nullable: false
mapping:
  commands:
    type: map
    nullable: false
    mapping:
      regex;(.+):
        type: map
        nullable: false
        mapping:
          name: {type: str, nullable: false, range: {min: 1}}
"""

#: an operator's own file carrying a DOTTED custom key under ``commands:``.
DOTTED_FIXTURE = """\
# main.yaml — an operator's own file.
commands:                # the shared command block
  greet.user:
    name: hello
"""


class Form:
    """A minimal posted-form double: ``multi_items`` like Starlette's, order preserved."""

    def __init__(self, items):
        self._items = list(items)

    def multi_items(self):
        return list(self._items)


def _schema(tmp_path) -> Path:
 # in a ``schemas/`` dir of its OWN, so ``schema_files_for`` does not treat the config file beside
 # it as a sibling schema (which pykwalify merges and would refuse every save).
    directory = tmp_path / "schemas"
    directory.mkdir(exist_ok=True)
    path = directory / "dotted_schema.yaml"
    path.write_text(DOTTED_SCHEMA, encoding="utf-8")
    return path


def _tree(tmp_path):
    return configgen.describe(configgen.load_schema(_schema(tmp_path)))


def _app(config_dir, roles=("Admin",)):
    return _build(config_dir, users=[{"username": "staff", "password_hash": _hash(),
                                      "roles": list(roles)}])[0]


def _config_dir(tmp_path, *, file_name, body):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / file_name).write_text(body, encoding="utf-8")
    return tmp_path


def _csrf_and_revision(page: str) -> tuple[str, str]:
    csrf = re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)
    revision = re.search(r'name="_revision" value="([^"]*)"', page).group(1)
    return csrf, revision


# ------------------------------------------------------- 1. the shipped JS escapes the typed key

CONFIGGEN_JS = REPO_ROOT / "services" / "webservice" / "static" / "configgen.js"
CONFIGGEN_HARNESS = Path(__file__).resolve().parent / "configgen_harness.js"

_requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                    reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def configgen_script() -> dict:
    done = subprocess.run(["node", str(CONFIGGEN_HARNESS), str(CONFIGGEN_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@_requires_node
def test_the_shipped_script_escapes_a_dotted_typed_key(configgen_script):
    """A NEW regex row's key is ESCAPED into the row's field names, and re-escaped off the current key."""
    dotted = configgen_script["regexTypedDotted"]
    assert dotted["afterFirst"] == ["f.commands.__key", "f.commands.greet%2Euser.name"]
    assert dotted["afterSecond"] == ["f.commands.__key", "f.commands.greet%2Eme.name"]
 # a key with a space and brackets carries no path-token specials: it passes through verbatim
    assert configgen_script["regexTypedSpecial"]["afterFirst"] == ["f.custom.__key", "f.custom.a b[c].v"]


# ------------------------------------------------------- 2. the save keeps a dotted key (THE test)

@_requires_node
@pytest.mark.asyncio
async def test_a_dotted_custom_entry_saved_through_the_page_keeps_its_value(
        tmp_path, monkeypatch, configgen_script):
    """THE failing test: the field a dotted NEW entry posts (as the shipped page + script produce it)
    round-trips through the real save route and keeps its value, without inventing a stray key.

    The posted name is read straight out of the shipped ``configgen.js`` (the harness's rename of the
    page's own ``__KEY__`` placeholder), so the test is fix-agnostic: before the fix the name is
    ``f.commands.greet.user.name`` and the save DROPS it (the file never changes); after the fix the
    name carries the escaped key and the edit lands on the ``greet.user`` entry.
    """
    field_name = configgen_script["regexTypedDotted"]["afterFirst"][1]
    config_dir = _config_dir(tmp_path, file_name="main.yaml", body=DOTTED_FIXTURE)
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(_schema(tmp_path)))
    monkeypatch.setattr(settings_page, "CONFIG_FILE_NAME", "main.yaml")

    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        csrf, revision = _csrf_and_revision(page)
        response = await client.post(settings_page.SETTINGS_SAVE_PATH,
                                     data={field_name: "changed", "_csrf_token": csrf,
                                           "_revision": revision})
        assert response.status_code == 303

    after = (config_dir / "main.yaml").read_text(encoding="utf-8")
    assert "name: changed" in after, f"the edit was dropped on save (field posted as {field_name})"
    assert "greet.user:" in after, "the dotted KEY itself was not preserved"
    assert "greet%2Euser" not in after, "the escaped key leaked into the file as a stray entry"


# ------------------------------------------------------- 3. the document round trip (the primitive)

def test_an_escaped_dotted_key_resolves_and_keeps_its_value(tmp_path):
    from core.utils.configwrite import dump_config, load_config_text
    document = load_config_text(DOTTED_FIXTURE)
    configgen.apply_form(document, Form([("f.commands.greet%2Euser.name", "changed")]),
                         _tree(tmp_path))
    after = dump_config(document)
    assert "name: changed" in after and "greet.user:" in after


def test_a_key_with_spaces_and_brackets_round_trips(tmp_path):
    from core.utils.configwrite import dump_config, load_config_text
    document = load_config_text("commands:\n  greet me[2]:\n    name: hello\n")
    configgen.apply_form(document, Form([("f.commands.greet me[2].name", "changed")]),
                         _tree(tmp_path))
    after = dump_config(document)
    assert "name: changed" in after and "greet me[2]:" in after


def test_the_escape_pair_is_a_bijection():
    for token in ("greet.user", "a b[c]", "100%", "a%.b", "plain", "..", "%2E", "x%252Ey"):
        assert configgen.decode_token(configgen.encode_token(token)) == token, token


def test_an_unplaceable_field_is_refused_not_dropped(tmp_path):
    """A posted field the tree cannot place RAISES — a save must never report success while dropping."""
    from core.utils.configwrite import load_config_text
    document = load_config_text(DOTTED_FIXTURE)
    with pytest.raises(configgen.UnknownFieldError):
        configgen.apply_form(document, Form([("f.commands.a.b.c.name", "x")]), _tree(tmp_path))


# ------------------------------------------------------- 4. the page refuses (operator-visible)

@pytest.mark.asyncio
async def test_the_page_refuses_an_unplaceable_field_and_writes_nothing(tmp_path, monkeypatch):
    config_dir = _config_dir(tmp_path, file_name="main.yaml", body=DOTTED_FIXTURE)
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(_schema(tmp_path)))
    monkeypatch.setattr(settings_page, "CONFIG_FILE_NAME", "main.yaml")

    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        csrf, revision = _csrf_and_revision((await client.get(settings_page.SETTINGS_MAIN_PATH)).text)
        response = await client.post(settings_page.SETTINGS_SAVE_PATH,
                                     data={"f.commands.a.b.c.name": "x", "_csrf_token": csrf,
                                           "_revision": revision})
        assert response.status_code == 303
        landed = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text

    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == DOTTED_FIXTURE
    banner = re.search(r'notice-msg">([^<]*)<', landed)
    assert banner is not None
    assert _html.unescape(banner.group(1)) == settings_page.SAVE_REFUSED_UNPLACEABLE


# ------------------------------------------------------- 5. both layers name the entry the same way

def test_custom_entries_carry_the_escaped_key(tmp_path):
    """The page model hands the template the ESCAPED key to name an entry's controls."""
    tree = _tree(tmp_path)
    field = next(f for f in tree.children if f.key == "commands").children[0]
    assert field.kind == configgen.KIND_REGEX
    entries = configgen.custom_entries(field, {"greet.user": {"name": "hello"}})
    assert entries[0]["key"] == "greet.user" and entries[0]["enc_key"] == "greet%2Euser"


def test_the_template_names_regex_entries_from_the_escaped_key():
    """A static pin: the template uses ``entry.enc_key`` for names, ``entry.key`` only as the value.

    The regex macro lives in the shared control partial (``_config_fields.html``).
    """
    template = (REPO_ROOT / "services" / "webservice" / "templates" / "_config_fields.html").read_text(
        encoding="utf-8")
    assert "entry.enc_key" in template
    assert "name=\"f.{{ name }}.{{ entry.key }}" not in template


# ------------------------------------------------------- 6. the new string is born wrapped

def test_the_refusal_string_is_shipped_in_every_catalog():
    for language in ("cn", "de", "es", "ru"):
        with open(REPO_ROOT / "locale" / language / "LC_MESSAGES" / "webui.po",
                  encoding="utf-8") as handle:
            catalog = pofile.read_po(handle)
        by_id = {message.id: message for message in catalog if message.id}
        text = settings_page.SAVE_REFUSED_UNPLACEABLE
        assert text in by_id, f"'{text}' is missing from the {language} catalog"
        assert not by_id[text].fuzzy, f"'{text}' is fuzzy in {language}"
        assert by_id[text].string, f"'{text}' is left untranslated in {language}"
