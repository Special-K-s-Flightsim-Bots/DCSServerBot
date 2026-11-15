"""THE NODE GUARD on EVERY door into a scoped config file.

The last card closed the node-guard hole for the Expert Mode door only; the review then PROVED two
data-integrity holes in the scoped-save path, both reproduced here against the REAL app:

* BLOCKER 1 — the PLAIN FORM door had NO node guard. ``node_slice_keys`` was consulted only when
  ``_expert=1``, so a form POST scoped to one node could name ANOTHER node's block
  (``f.dcs-b-01.Nevada.music_dir=…``) and REWRITE it on disk while reporting "Saved". The guard now
  applies to the form door too: a posted key naming a node that is not the selected one is REFUSED —
  nothing written, the file byte-identical — never silently dropped (a drop is a lie about what saved).

* BLOCKER 2 — the EXPERT door fell back to WHOLE-FILE replacement. With a bogus node (``?node=dcs-NOPE``)
  or NO node at all, ``_save`` took the whole-file door, so the posted SLICE (all the editor holds)
  became the ENTIRE file and every other node's block was silently DELETED (schema validation passes
  because the top-level ``regex;(.+)`` is unbounded). The expert save now only ever REPLACES the keys
  it was given (merge semantics); if the page does not name a node the file carries, the save is REFUSED
  — never promoted to a whole-file write.

Also pinned: a hand-written other-node key inside an expert slice is REFUSED (not dropped), and the
validate route with a bogus node refuses to guess rather than validating the slice as the whole file.

The no-op byte-identity the review verified for every file shape must STILL hold — do not regress it.
"""
from __future__ import annotations

import tempfile

import html
import re
import types
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import _build, _hash, _login

#: a REAL directory: ``music_dir`` carries ``func: dir_exists``, so the schema refuses a value
#: that does not exist here — ``/tmp`` exists on POSIX only, and the save is then a refusal
NEW_DIR = tempfile.gettempdir()

BOT_YAML = """\
roles:
  Admin:
    - specialk
"""

#: THREE nodes, two instances each — so a service file can carry TWO node blocks and the node guard's
#: "selected node vs. every other node" is well defined.
NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
    Caucasus:
      home: /home/a-caucasus
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  instances:
    Nevada:
      home: /home/b-nevada
    PersianGulf:
      home: /home/b-persiangulf
dcs-c-01:
  listen_address: 127.0.0.1
  listen_port: 10044
  instances:
    SinaiMap:
      home: /home/c-sinai
    Kola:
      home: /home/c-kola
"""

SERVERS_YAML = """\
SRS-1:
  node: dcs-a-01
"""

#: A music service file with a DEFAULT block AND TWO node blocks (``dcs-a-01`` and ``dcs-b-01``) — the
#: multi-node shape the node guard partitions. The directory is ``NEW_DIR`` — a REAL one: the schema
#: checks ``dir_exists``, and ``/etc`` exists on POSIX hosts only, so the slice is refused on Windows.
MUSIC_YAML = f"""\
DEFAULT:
  music_dir: {NEW_DIR}
  pause_without_players: true
dcs-a-01:
  Syria:
    music_dir: {NEW_DIR}
dcs-b-01:
  Nevada:
    music_dir: {NEW_DIR}
"""


@pytest.fixture
def guard_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "services" / "music.yaml").write_text(MUSIC_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def node_data(guard_dir, monkeypatch):
    """Bind the bot's own validator to THIS install's cluster (the sibling suites' fixture)."""
    import core.commandline as commandline
    from core.utils import validators

    saved_argv = getattr(commandline, "COMMAND_LINE_ARGS", None)
    saved_instance = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(guard_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved_instance
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", saved_argv, raising=False)


MUSIC_PAGE = settings_page.SETTINGS_FILE_PATH.format(kind=settings_page.KIND_SERVICE, name="music")
MUSIC_SAVE = settings_page.SETTINGS_FILE_SAVE_PATH.format(kind=settings_page.KIND_SERVICE, name="music")
MUSIC_VALIDATE = settings_page.SETTINGS_VALIDATE_FILE_PATH.format(
    kind=settings_page.KIND_SERVICE, name="music")


def _app(guard_dir):
    return _build(guard_dir, users=[{"username": "staff", "password_hash": _hash(),
                                     "roles": ["Admin"]}])[0]


def _music_path(guard_dir) -> Path:
    return guard_dir / "services" / "music.yaml"


def _csrf(page: str) -> str:
    return re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)


def _revision(page: str) -> str:
    return re.search(r'name="_revision" value="([^"]*)"', page).group(1)


def _panel(page: str) -> str:
    found = re.search(r'<pre class="st-yaml"[^>]*>(.*?)</pre>', page, re.DOTALL)
    assert found is not None, "the YAML panel was not rendered"
    return html.unescape(found.group(1))


def _notice(page: str) -> str:
    found = re.search(r'notice-msg">([^<]*)<', page)
    assert found is not None, "no notice was rendered"
    return html.unescape(found.group(1))


def _block(text: str, node: str) -> str:
    """The raw text of one node's top-level block (its ``node:`` line and indented body)."""
    found = re.search(rf"(?m)^{re.escape(node)}:\n(?:[ \t].*\n|\n)*", text)
    assert found is not None, f"no block for {node}"
    return found.group(0)


async def _open(client, url: str) -> str:
    response = await client.get(url)
    assert response.status_code == 200, (url, response.status_code, response.text[:300])
    return response.text




# ============================================================= BLOCKER 1 — the FORM door's node guard

@pytest.mark.asyncio
async def test_a_form_save_naming_another_node_is_refused_and_writes_nothing(guard_dir, node_data):
    """The reviewer's own body: ``?node=dcs-a-01`` with ``_expert=0`` and a ``dcs-b-01`` field.

    It must be REFUSED — nothing written, the file byte-identical — and NOT silently dropped (a drop
    would show "Saved" while the posted key vanished). Before the fix this REWROTE dcs-b-01's block.
    """
    path = _music_path(guard_dir)
    before = path.read_bytes()
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        reply = await client.post(
            f"{MUSIC_SAVE}?node=dcs-a-01",
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "0",
                  "f.dcs-b-01.Nevada.music_dir": NEW_DIR})
        landed = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "a foreign-node form field rewrote dcs-b-01's block"
    assert _notice(landed) == settings_page.SAVE_REFUSED_FOREIGN_NODE, _notice(landed)


@pytest.mark.asyncio
async def test_a_form_save_of_the_selected_node_still_lands(guard_dir, node_data):
    """The guard is not a blanket refusal: the SELECTED node's own field is written, and every other
    node's block stays byte-for-byte what it was."""
    path = _music_path(guard_dir)
    before = path.read_text(encoding="utf-8")
    target = guard_dir / "music2"
    target.mkdir(parents=True, exist_ok=True)   # a REAL directory the schema's dir_exists accepts
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        reply = await client.post(
            f"{MUSIC_SAVE}?node=dcs-a-01",
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "0",
                  "f.dcs-a-01.Syria.music_dir": str(target)})
        landed = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert str(target) in after, "the selected node's field was not written"
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01"), "dcs-b-01's block was rewritten"
    assert _notice(landed) == settings_page.SAVED_RESTART, _notice(landed)


@pytest.mark.asyncio
async def test_a_form_save_without_a_node_may_not_name_any_node(guard_dir, node_data):
    """With NO ``?node=`` on a multi-node file, the file is not the operator's unit — the same refusal
    the Expert door gives (the page did not name a node the file carries), so a form cannot rewrite a
    block the operator did not open. The FORM and Expert doors now say the SAME thing."""
    path = _music_path(guard_dir)
    before = path.read_bytes()
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, MUSIC_PAGE)
        reply = await client.post(
            MUSIC_SAVE,
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "0",
                  "f.dcs-a-01.Syria.music_dir": NEW_DIR})
        landed = await _open(client, MUSIC_PAGE)
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "a node-less form save rewrote a node's block"
    assert _notice(landed) == settings_page.SAVE_REFUSED_UNKNOWN_NODE, _notice(landed)


# =============================================== BLOCKER 2 — the EXPERT door's node guard (no shrink)

@pytest.mark.asyncio
async def test_an_expert_save_with_a_bogus_node_cannot_shrink_the_file(guard_dir, node_data):
    """``?node=dcs-NOPE``: the posted slice is NOT promoted to a whole-file write. Before the fix it
    replaced the file, deleting every other node's block. Now: refused, file byte-identical."""
    path = _music_path(guard_dir)
    before = path.read_bytes()
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        slice_text = _panel(page)                       # the slice the editor actually holds
        reply = await client.post(
            f"{MUSIC_SAVE}?node=dcs-NOPE",
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "1",
                  "_yaml": slice_text})
        landed = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "a bogus-node expert save shrank the file"
    assert _notice(landed) == settings_page.SAVE_REFUSED_UNKNOWN_NODE, _notice(landed)


@pytest.mark.asyncio
async def test_an_expert_save_with_no_node_cannot_shrink_the_file(guard_dir, node_data):
    """No ``?node=`` at all on a multi-node file: the same refusal — the slice never becomes the file."""
    path = _music_path(guard_dir)
    before = path.read_bytes()
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        slice_text = _panel(page)
        reply = await client.post(
            MUSIC_SAVE,
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "1",
                  "_yaml": slice_text})
        landed = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "a node-less expert save shrank the file"
    assert _notice(landed) == settings_page.SAVE_REFUSED_UNKNOWN_NODE, _notice(landed)


@pytest.mark.asyncio
async def test_an_expert_slice_save_of_the_selected_node_still_lands(guard_dir, node_data):
    """The control: a VALID slice save writes only that node and leaves the others byte-identical."""
    path = _music_path(guard_dir)
    before = path.read_text(encoding="utf-8")
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        edited = _panel(page).replace("/etc", NEW_DIR, 1)   # only dcs-a-01's own music_dir
        reply = await client.post(
            f"{MUSIC_SAVE}?node=dcs-a-01",
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "1",
                  "_yaml": edited})
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert NEW_DIR in after and "dcs-a-01" in after
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01"), "dcs-b-01's block was rewritten"


@pytest.mark.asyncio
async def test_an_untouched_expert_slice_is_byte_identical(guard_dir, node_data):
    """The no-op re-proof: posting back exactly the slice the editor holds changes nothing on disk."""
    path = _music_path(guard_dir)
    before = path.read_bytes()
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        reply = await client.post(
            f"{MUSIC_SAVE}?node=dcs-a-01",
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "1",
                  "_yaml": _panel(page)})
        landed = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "an unchanged expert slice rewrote the file"
    assert _notice(landed) == settings_page.SAVED_NO_CHANGE, _notice(landed)


# =================================================== ALSO — a hand-written foreign node in a slice

@pytest.mark.asyncio
async def test_an_expert_slice_carrying_a_hand_written_foreign_node_is_refused(guard_dir, node_data):
    """A slice may carry ONLY the selected node's keys. A hand-written ``dcs-b-01`` block is REFUSED,
    not silently dropped while "Saved" shows."""
    path = _music_path(guard_dir)
    before = path.read_bytes()
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        smuggled = _panel(page) + "\ndcs-b-01:\n  Nevada:\n    music_dir: /tmp\n"
        reply = await client.post(
            f"{MUSIC_SAVE}?node=dcs-a-01",
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "1",
                  "_yaml": smuggled})
        landed = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "a hand-written foreign node in a slice wrote the file"
    assert _notice(landed) == settings_page.SAVE_REFUSED_FOREIGN_NODE, _notice(landed)


# =================================================== ALSO — the validate route with a bogus node

@pytest.mark.asyncio
async def test_the_validate_route_with_a_bogus_node_refuses_to_guess(guard_dir, node_data):
    """The slice cannot be validated as if it were the whole file: with a node the file does not carry
    the route answers ``ok=False`` instead of a misleading verdict."""
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        verdict = await client.post(
            f"{MUSIC_VALIDATE}?node=dcs-NOPE",
            data={"_csrf_token": _csrf(page), "_yaml": _panel(page)})
    assert verdict.status_code == 200, verdict.text
    body = verdict.json()
    assert body["ok"] is False, body
    assert body["message"] == settings_page.VALIDATE_UNKNOWN_NODE, body


@pytest.mark.asyncio
async def test_the_validate_route_still_accepts_a_valid_slice(guard_dir, node_data):
    """The control: a genuine slice of the selected node is still validated (merged, then checked)."""
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        verdict = await client.post(
            f"{MUSIC_VALIDATE}?node=dcs-a-01",
            data={"_csrf_token": _csrf(page), "_yaml": _panel(page)})
    assert verdict.status_code == 200, verdict.text
    assert verdict.json()["ok"] is True, verdict.json()


# ================================ BLOCKER 3 — the FORM door's ALLOW-list (instance / DEFAULT keys)

#: A music file that ALSO carries a top-level INSTANCE block (``Nevada``, an instance of ``dcs-b-01``)
#: that the scoped view for ``dcs-a-01`` never renders. A node-only deny-list did not know the name
#: ``Nevada``, so a crafted ``f.Nevada.music_dir`` field could OVERWRITE this block on disk.
MUSIC_YAML_WITH_FOREIGN_INSTANCE = f"""\
DEFAULT:
  music_dir: {NEW_DIR}
  pause_without_players: true
dcs-a-01:
  Syria:
    music_dir: {NEW_DIR}
dcs-b-01:
  Nevada:
    music_dir: {NEW_DIR}
Nevada:
  music_dir: {NEW_DIR}
"""


@pytest.mark.asyncio
async def test_a_form_save_naming_a_foreign_instance_is_refused_and_writes_nothing(
        guard_dir, node_data):
    """A field's first token is not always a NODE: ``Nevada`` is an INSTANCE of ``dcs-b-01``. Posting
    ``f.Nevada.music_dir`` from ``dcs-a-01``'s page wrote a top-level ``Nevada:`` block the scoped view
    never renders. The slice's ALLOW-list (its own keys only) now REFUSES it — byte-identical."""
    path = _music_path(guard_dir)
    before = path.read_bytes()
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        reply = await client.post(
            f"{MUSIC_SAVE}?node=dcs-a-01",
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "0",
                  "f.Nevada.music_dir": NEW_DIR})
        landed = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "a foreign-INSTANCE form field rewrote the file"
    assert _notice(landed) == settings_page.SAVE_REFUSED_FOREIGN_NODE, _notice(landed)


@pytest.mark.asyncio
async def test_a_form_save_cannot_overwrite_an_existing_foreign_instance_block(guard_dir, node_data):
    """The same crafted field, but the top-level ``Nevada`` block ALREADY exists: the allow-list must
    stop it being OVERWRITTEN from another node's page (silent data loss)."""
    path = _music_path(guard_dir)
    path.write_text(MUSIC_YAML_WITH_FOREIGN_INSTANCE, encoding="utf-8")
    before = path.read_bytes()
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        reply = await client.post(
            f"{MUSIC_SAVE}?node=dcs-a-01",
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "0",
                  "f.Nevada.music_dir": NEW_DIR})
        landed = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "an existing foreign-INSTANCE block was overwritten"
    assert _notice(landed) == settings_page.SAVE_REFUSED_FOREIGN_NODE, _notice(landed)


@pytest.mark.asyncio
async def test_a_form_save_of_the_shared_default_on_the_selected_node_still_lands(
        guard_dir, node_data):
    """The allow-list is not a blanket refusal: the shared ``DEFAULT`` block IS part of the selected
    node's slice, so its field still saves — and every other node's block stays byte-for-byte."""
    path = _music_path(guard_dir)
    before = path.read_text(encoding="utf-8")
    target = guard_dir / "music2"
    target.mkdir(parents=True, exist_ok=True)   # a REAL directory the schema's dir_exists accepts
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        reply = await client.post(
            f"{MUSIC_SAVE}?node=dcs-a-01",
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "0",
                  "f.DEFAULT.music_dir": str(target)})
        landed = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert str(target) in after, "the shared DEFAULT field was not written"
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01"), "dcs-b-01's block was rewritten"
    assert _notice(landed) == settings_page.SAVED_RESTART, _notice(landed)


# ============================= MINOR 1 — the FORM door must consult require_node (no DEFAULT write)

@pytest.mark.asyncio
async def test_a_bogus_node_form_save_cannot_write_the_shared_default(guard_dir, node_data):
    """``?node=dcs-NOPE`` (a node the file does not carry) — the FORM door did not consult
    ``require_node``, so the shared ``DEFAULT`` was writable from a bogus-node page. Now: the SAME
    refusal the Expert door gives — nothing written, byte-identical."""
    path = _music_path(guard_dir)
    before = path.read_bytes()
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-NOPE")
        reply = await client.post(
            f"{MUSIC_SAVE}?node=dcs-NOPE",
            data={"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "0",
                  "f.DEFAULT.music_dir": NEW_DIR})
        landed = await _open(client, f"{MUSIC_PAGE}?node=dcs-NOPE")
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "a bogus-node form save wrote the shared DEFAULT block"
    assert _notice(landed) == settings_page.SAVE_REFUSED_UNKNOWN_NODE, _notice(landed)


# ============================= MINOR 2 — the VALIDATE door must agree with the SAVE door

@pytest.mark.asyncio
async def test_the_validate_route_refuses_the_slice_the_save_refuses(guard_dir, node_data):
    """A slice smuggling a foreign node was BLESSED by the validator (``ok=True``) while the SAVE
    refused the very same body — the editor lied to the user. The validator now applies the SAME rule:
    ``ok=False`` with the save's own refusal, so the two doors always agree."""
    app = _app(guard_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, f"{MUSIC_PAGE}?node=dcs-a-01")
        smuggled = _panel(page) + "\ndcs-b-01:\n  Nevada:\n    music_dir: /tmp\n"
        verdict = await client.post(
            f"{MUSIC_VALIDATE}?node=dcs-a-01",
            data={"_csrf_token": _csrf(page), "_yaml": smuggled})
    assert verdict.status_code == 200, verdict.text
    body = verdict.json()
    assert body["ok"] is False, body
    assert body["message"] == settings_page.SAVE_REFUSED_FOREIGN_NODE, body
