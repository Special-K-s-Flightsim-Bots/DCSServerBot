"""The site's own palette sheet: the upload, the removal, the parser's refusal and the route.

Each pin is a promise the ``theme.sheet`` source makes:

* a benign upload lands under the FIXED leaf ``theme.css`` in the site's asset folder, the site names
  it, and the theme route answers the file's bytes — and a SECOND upload moves the ``ETag``, because
  the tag follows the bytes;
* a caller without ``site.write``, a file that is not ``.css``, bytes that are not UTF-8 and a body
  over :data:`services.webservice.sites.MAX_SHEET_LENGTH` are each refused with their own sentence,
  and NOTHING is staged or written — the refusal never reaches the writer;
* a ``theme.sheet`` naming a file that is not there is refused at PARSE time, naming the key and the
  file, and the composer is never asked to read it;
* at most one of ``preset``/``sheet``/``expert`` may be set: an upload CLEARS the other two, and the
  parser refuses two sources naming both keys;
* the palette block draws the sheet's row with the MARK's anatomy — the same clipped native input,
  the same console label, the same chosen-file line, and the same inline confirm — and the markup is
  what ``static/site-config.js`` resolves: the selectors are derived from the rendered document here,
  not restated;
* a removal deletes the file and clears the key, and a second removal is still a success.

Driven through the real application (the production factory, the shell, the local backend and a stub
cluster), with the page's own CSRF token, so the doors are the ones the operator meets.
"""
from __future__ import annotations

import html as html_module
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice import mount, sites, theme_css
from services.webservice.auth import local as local_auth
from services.webservice.pages import site_config as site_page
from test_webui_write_pause import Cluster, build, client, instance, server, sign_in  # noqa: F401

ADMIN = "specialk"
GUARDS_ADMIN = "guards-admin"
PLAIN = "plain"

BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS:
    - guards-admin
    - plain
"""

SERVERS_YAML = """\
SRS-1:
  managed_by:
    - 5th Guards
SRS-2:
  managed_by:
    - Other Token
"""

SITE_YAML = """\
sites:
  root:
    slug: ""
    branding:
      title: Community
    theme:
      preset: midnight
  guards:
    group_role: "5th Guards"
    slug: guards
    branding:
      title: Guards
  others:
    group_role: "Other Token"
    slug: others
"""

#: the sheet a benign upload sends: component rules, an at-rule and a url(), so the pin proves the
#: file is stored and served as written rather than token-by-token.
BENIGN = """\
:root {
  --brass: #7a4bd1;
}
.hero h1 {
  letter-spacing: 0.02em;
}
@font-face {
  font-family: "Site";
  src: url("/static/site-font.woff2");
}
"""

NOT_CSS = "is not a .css sheet"
TOO_LARGE = "larger than the 256 KiB"
NOT_UTF8 = "not UTF-8"
UPLOADED = "The sheet was uploaded"
REMOVED = "The uploaded sheet was removed"
OTHER_SITE = "only for your own site"


def cluster():
    import types
    a = server("SRS-1", ["5th Guards"])
    b = server("SRS-2", ["Other Token"], node_name="dcs-b-01", instance="Host-B-Instance")
    master = types.SimpleNamespace(name="dcs-master-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-master-01": master}, node=master,
                   instances=[instance("Host-A-Instance", a)])


def _users() -> list[dict]:
    password = local_auth.hash_password("correct horse battery staple",
                                       iterations=local_auth.MIN_ITERATIONS)
    return [{"username": ADMIN, "roles": ["Admin"], "password_hash": password},
            {"username": GUARDS_ADMIN, "roles": ["DCS"], "scope": ["5th Guards"],
             "password_hash": password},
            {"username": PLAIN, "roles": ["DCS"], "password_hash": password}]


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (tmp_path / sites.SITE_FILE).write_text(SITE_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(config_dir):
    return build(config_dir, cluster(), users=_users())


@pytest.fixture
def assets(config_dir):
    """The folder a site's files live in — created by the upload itself, never by the fixture."""
    return config_dir / sites.ASSET_DIR


# ----------------------------------------------------------------------------- the harness
def page_path() -> str:
    return mount.url(site_page.SITE_CONFIG_PATH)


def upload_path() -> str:
    return mount.url(site_page.SITE_SHEET_UPLOAD_PATH)


def remove_path() -> str:
    return mount.url(site_page.SITE_SHEET_REMOVE_PATH)


async def open_page(http) -> str:
    response = await http.get(page_path())
    assert response.status_code == 200, f"the page answered {response.status_code}"
    return response.text


def token_of(html: str) -> str:
    match = re.search(r'name="_csrf_token" value="([^"]*)"', html)
    assert match, "the page rendered no CSRF field"
    return match.group(1)


def revision_of(html: str) -> str:
    match = re.search(r'name="_revision" value="([^"]*)"', html)
    assert match, "the page rendered no revision field"
    return match.group(1)


def notice_of(html: str) -> str:
    match = re.search(r'<span class="notice-msg">(.*?)</span>', html, re.S)
    return match.group(1) if match else ""


async def upload(http, html: str, key: str, payload: bytes, filename: str = "theme.css",
                 content_type: str = "text/css"):
    return await http.post(upload_path(),
                           data={"_csrf_token": token_of(html), "_site": key},
                           files={site_page.SITE_SHEET_FILE_FIELD: (filename, payload,
                                                                   content_type)})


async def remove(http, html: str, key: str):
    return await http.post(remove_path(), data={"_csrf_token": token_of(html), "_site": key})


def sheet_file(config_dir) -> Path:
    return config_dir / sites.ASSET_DIR / sites.SHEET_LEAF


def stored_theme(config_dir, key: str) -> dict:
    """The ``theme:`` block of ONE site as the file states it, ``{}`` when it states none."""
    block = sites.load_site_block(config_dir) or {}
    site_block = block.get(key) if isinstance(block, dict) else None
    theme = (site_block or {}).get("theme") if isinstance(site_block, dict) else None
    return theme if isinstance(theme, dict) else {}


def no_writes(monkeypatch) -> list:
    """Record every call to the writer, so \"nothing was written\" is not merely \"no file exists\"."""
    calls: list = []
    monkeypatch.setattr(site_page, "stage_sheet_file",
                        lambda payload, target: calls.append((target, len(payload))))
    return calls


async def theme_of(app, path: str = "/theme.css"):
    async with asgi_client(app) as http:
        return await http.get(path)


# ============================================ 1. the positive control: upload, serve, new ETag
def preview_address(html: str) -> str:
    """The address the card's frame loads, out of the rendered markup."""
    match = re.search(r'<iframe src="([^"]*)"[^>]*data-sc-preview', html)
    assert match, "the card renders no preview frame"
    return match.group(1).replace("&amp;", "&")


@pytest.mark.asyncio
async def test_a_benign_upload_lands_serves_its_bytes_and_the_etag_moves(app, config_dir):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        before_address = preview_address(html)
        reply = await upload(http, html, "root", BENIGN.encode("utf-8"))
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)

    assert preview_address(landed) != before_address, (
        "the preview keeps the address it had before the upload, so a cached document could answer "
        "with the old palette")
    #: read back as text: the sheet is a text upload, and a byte compare would fail on Windows only
    #: because the stored copy carries that platform's line ending
    assert sheet_file(config_dir).read_text(encoding="utf-8") == BENIGN, \
        "the sheet was not stored exactly as it was uploaded"
    stored = stored_theme(config_dir, "root")
    assert stored["sheet"] == sites.SHEET_LEAF, stored
    assert "preset" not in stored, "an upload must clear the preset it replaces"
    assert UPLOADED in notice_of(landed)

    first = await theme_of(app)
    assert first.status_code == 200
    assert first.headers["content-type"] == "text/css; charset=utf-8"
    assert first.headers["x-content-type-options"] == "nosniff"
    assert first.text == BENIGN, "the route did not answer the uploaded file verbatim"
    etag = first.headers["etag"]

    # a SECOND sheet of a different size and shape: the same address, a new representation
    changed = BENIGN + "\n.panel { border-radius: 2px; }\n"
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "root", changed.encode("utf-8"))
        assert reply.status_code == 303, reply.text[:400]

    second = await theme_of(app)
    assert second.text == changed, "a re-upload did not replace the sheet"
    assert second.headers["etag"] != etag, "the ETag did not follow the bytes"

    stale = await theme_of(app)
    again = None
    async with asgi_client(app) as http:
        again = await http.get("/theme.css", headers={"If-None-Match": second.headers["etag"]})
    assert stale.status_code == 200 and stale.headers["etag"] == second.headers["etag"]
    assert again.status_code == 304, "a matching tag must revalidate to a 304"


@pytest.mark.asyncio
async def test_the_composer_answers_the_sheet_and_falls_back_when_it_is_gone(app, config_dir):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        assert (await upload(http, html, "guards", BENIGN.encode("utf-8"))).status_code == 303

    theme = sites.Theme(sheet=sites.SHEET_LEAF)
    assert theme_css.compose(theme, config_dir) == BENIGN

    sheet_file(config_dir).unlink()
    assert theme_css.compose(theme, config_dir) == theme_css.preset_sheet(
        theme_css.DEFAULT_PRESET), "a sheet whose file is gone must fall back to the default palette"


# ================================================== 2. what the doors refuse, and nothing written
@pytest.mark.asyncio
async def test_a_caller_without_the_write_capability_gets_403_and_nothing_is_staged(
        app, config_dir, assets, monkeypatch):
    """The sheet's doors sit behind ``site.write``, and a refusal reaches no writer.

    Two facts, because neither alone is enough: the path's declared capability IS
    :data:`site_page.SITE_WRITE_CAPABILITY` (the map the router's gate reads), and a caller who does
    not hold it is refused by the door with a 403. A principal the gate refuses cannot load a page
    with a form either, so the CSRF predicate answers first on this post — which is the door's order,
    not a weaker check: nothing is staged on either refusal.
    """
    assert site_page.capabilities()[site_page.SITE_SHEET_UPLOAD_PATH] == \
        site_page.SITE_WRITE_CAPABILITY
    assert site_page.capabilities()[site_page.SITE_SHEET_REMOVE_PATH] == \
        site_page.SITE_WRITE_CAPABILITY
    calls = no_writes(monkeypatch)
    async with client(app) as http:
        await sign_in(http, PLAIN)
        reply = await http.post(upload_path(), data={"_site": "guards"},
                                files={site_page.SITE_SHEET_FILE_FIELD: ("theme.css",
                                                                         BENIGN.encode("utf-8"),
                                                                         "text/css")})
    assert reply.status_code == 403
    assert calls == [], f"a refused caller reached the writer: {calls}"
    assert not (assets / sites.SHEET_LEAF).exists()


@pytest.mark.asyncio
async def test_a_group_admin_may_not_upload_for_another_site(app, config_dir, assets, monkeypatch):
    calls = no_writes(monkeypatch)
    async with client(app) as http:
        await sign_in(http, GUARDS_ADMIN)
        reply = await upload(http, await open_page(http), "others", BENIGN.encode("utf-8"))
    assert reply.status_code == 403
    assert OTHER_SITE in reply.text
    assert calls == [], f"a site outside the caller's scope reached the writer: {calls}"
    assert not (assets / sites.SHEET_LEAF).exists()


@pytest.mark.asyncio
async def test_a_file_that_is_not_css_is_refused_and_nothing_is_staged(
        app, config_dir, assets, monkeypatch):
    calls = no_writes(monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await upload(http, await open_page(http), "guards", BENIGN.encode("utf-8"),
                             filename="theme.txt")
        landed = await open_page(http)
    assert reply.status_code == 303
    assert NOT_CSS in notice_of(landed)
    assert calls == [], f"a refused name reached the writer: {calls}"
    assert not (assets / sites.SHEET_LEAF).exists()


@pytest.mark.asyncio
async def test_bytes_that_are_not_utf8_are_refused_and_nothing_is_staged(
        app, config_dir, assets, monkeypatch):
    calls = no_writes(monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await upload(http, await open_page(http), "guards",
                             b":root { --brass: \xff\xfe\x00; }")
        landed = await open_page(http)
    assert reply.status_code == 303
    assert NOT_UTF8 in notice_of(landed)
    assert calls == [], f"a body that is not text reached the writer: {calls}"
    assert not (assets / sites.SHEET_LEAF).exists()


@pytest.mark.asyncio
async def test_a_body_over_the_ceiling_is_refused_and_nothing_is_staged(
        app, config_dir, assets, monkeypatch):
    calls = no_writes(monkeypatch)
    payload = b"/*" + b"x" * (sites.MAX_SHEET_LENGTH + 10) + b"*/\n"
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await upload(http, await open_page(http), "guards", payload)
        landed = await open_page(http)
    assert reply.status_code == 303
    assert TOO_LARGE in notice_of(landed), notice_of(landed)
    assert calls == [], f"an oversized body reached the writer: {calls}"
    assert not (assets / sites.SHEET_LEAF).exists()


@pytest.mark.asyncio
async def test_an_existing_sheet_that_cannot_be_read_is_refused_and_nothing_is_staged(
        app, config_dir, assets, monkeypatch):
    """An unreadable existing sheet answers the page's refusal, not a 500, and stages nothing.

    The previous bytes are what a failed config write puts back, so the read happens before the
    upload is staged: a sheet that exists but cannot be read is refused there, and the file on disk
    is left exactly as it was.
    """
    assets.mkdir(parents=True, exist_ok=True)
    sheet_file(config_dir).write_bytes(BENIGN.encode("utf-8"))
    calls = no_writes(monkeypatch)
    real_read = Path.read_bytes

    def unreadable(self):
        if self == sheet_file(config_dir):
            raise PermissionError(13, "Permission denied", str(self))
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await upload(http, await open_page(http), "guards", BENIGN.encode("utf-8"))
        landed = await open_page(http)
    assert reply.status_code == 303, reply.text[:400]
    assert "Nothing was written" in notice_of(landed), notice_of(landed)
    assert "Permission denied" in notice_of(landed), notice_of(landed)
    assert calls == [], f"an unreadable sheet reached the writer: {calls}"
    assert real_read(sheet_file(config_dir)) == BENIGN.encode("utf-8"), \
        "the refusal changed the sheet that was already there"


@pytest.mark.asyncio
async def test_a_sheet_naming_a_missing_file_is_refused_at_parse_and_never_read(monkeypatch,
                                                                               config_dir):
    """The parser refuses the name, and the composer is NEVER asked to read it."""
    (config_dir / sites.ASSET_DIR).mkdir(parents=True, exist_ok=True)
    read: list = []
    monkeypatch.setattr(theme_css, "sheet_text",
                        lambda theme, where: read.append((theme.sheet, where)) or "")

    with pytest.raises(sites.SiteConfigError) as refusal:
        sites.parse_sites({"root": {"theme": {"sheet": "gone.css"}}}, config_dir=config_dir)
    message = str(refusal.value)
    assert "sites.root.theme.sheet" in message, message
    assert "gone.css" in message and "does not exist" in message, message
    assert read == [], f"the composer was asked to read a refused name: {read}"

    # …and the same block, with the file present, parses and names the leaf
    (config_dir / sites.ASSET_DIR / "gone.css").write_text(BENIGN, encoding="utf-8")
    root = sites.parse_sites({"root": {"theme": {"sheet": "gone.css"}}},
                             config_dir=config_dir).root
    assert root is not None and root.theme.sheet == "gone.css"


# ========================================================= 3. one source, and the parse refuses two
@pytest.mark.asyncio
async def test_the_upload_clears_an_expert_sheet_and_the_parse_refuses_two_sources(app, config_dir):
    written = config_dir / sites.SITE_FILE
    written.write_text(
        "sites:\n"
        "  root:\n"
        "    slug: \"\"\n"
        "    theme:\n"
        "      expert: \"--brass: #e8b04b;\"\n",
        encoding="utf-8")
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        assert (await upload(http, html, "root", BENIGN.encode("utf-8"))).status_code == 303
    stored = stored_theme(config_dir, "root")
    assert stored == {"sheet": sites.SHEET_LEAF}, stored

    # the parser names both keys when a hand-edited block states them together
    with pytest.raises(sites.SiteConfigError) as refusal:
        sites.parse_sites({"root": {"theme": {"preset": "brass", "sheet": sites.SHEET_LEAF}}},
                          config_dir=config_dir)
    message = str(refusal.value)
    assert "sites.root.theme.preset" in message and "sites.root.theme.sheet" in message, message


# ============================================================== 4. the removal, and the second one
@pytest.mark.asyncio
async def test_a_removal_deletes_the_file_and_clears_the_key_and_a_second_one_succeeds(
        app, config_dir, assets):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        assert (await upload(http, html, "guards", BENIGN.encode("utf-8"))).status_code == 303
        assert (assets / sites.SHEET_LEAF).is_file()
        reply = await remove(http, await open_page(http), "guards")
        assert reply.status_code == 303
        landed = await open_page(http)
    assert not (assets / sites.SHEET_LEAF).exists()
    assert stored_theme(config_dir, "guards") == {}, \
        "the removal left a theme block behind"
    assert REMOVED in notice_of(landed)

    async with client(app) as http:
        await sign_in(http, ADMIN)
        again = await remove(http, await open_page(http), "guards")
        assert again.status_code == 303
        second = await open_page(http)
    assert REMOVED in notice_of(second), "a second removal is a success, not an error"


# ==================================================== 5. the palette block's third state, and the row
class _Doc(HTMLParser):
    """A flat element list with each node's ancestors, enough to derive the row's anatomy."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict] = []
        self.nodes: list[dict] = []

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parents": list(self.stack)}
        self.nodes.append(node)
        if tag not in ("input", "img", "br", "meta", "link"):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return


def _doc(html: str) -> _Doc:
    doc = _Doc()
    doc.feed(html)
    return doc


def _sheet_row(html: str) -> dict:
    """The FIRST card's sheet row, found by the attribute the template gives it.

    Every card draws one, so the count is checked against the palette blocks the page renders.
    """
    doc = _doc(html)
    rows = [node for node in doc.nodes if "data-sc-sheet" in node["attrs"]]
    blocks = [node for node in doc.nodes if node["attrs"].get("data-sc-block") == "palette"]
    assert blocks, "the page draws no palette block"
    assert len(rows) == len(blocks), f"{len(rows)} sheet rows for {len(blocks)} palette blocks"
    return rows[0]


def _within(doc: _Doc, row: dict, **attrs) -> list[dict]:
    """Every node under *row* carrying each of *attrs* (an attribute, or ``class=`` for a class)."""
    found = []
    for node in doc.nodes:
        if row not in node["parents"]:
            continue
        if all((key in node["attrs"] if not key.startswith("class:")
                else key.split(":", 1)[1] in node["attrs"].get("class", ""))
               for key in attrs):
            found.append(node)
    return found


@pytest.mark.asyncio
async def test_the_row_is_the_mark_s_anatomy_and_the_script_s_own_selectors(app, config_dir):
    """The rendered row IS what ``static/site-config.js`` resolves — derived, not restated."""
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)

    doc = _doc(html)
    row = _sheet_row(html)
    file_inputs = _within(doc, row, **{"class:sc-av-file": True})
    assert len(file_inputs) == 1, "the row draws no clipped native file input"
    field = file_inputs[0]
    assert field["tag"] == "input" and field["attrs"]["type"] == "file"
    assert field["attrs"]["name"] == site_page.SITE_SHEET_FILE_FIELD, field["attrs"]
    assert ".css" in field["attrs"]["accept"], field["attrs"]

    # the console's own label button is BOUND to that input, and the status line is the mark's own
    labels = _within(doc, row, **{"class:sc-av-pick": True})
    assert len(labels) == 1, "the row draws no console label for the picker"
    assert labels[0]["attrs"]["for"] == field["attrs"]["id"], labels[0]["attrs"]
    statuses = _within(doc, row, **{"data-sc-file-status": True})
    assert len(statuses) == 1, "the row draws no chosen-file line"

    # the remove is the mark's own door: the same attribute, the same inline confirm, the same form
    removes = _within(doc, row, **{"data-sc-mark-remove": True})
    assert len(removes) == 1, "the row draws no remove control"
    assert removes[0]["attrs"]["formaction"] == mount.url(site_page.SITE_SHEET_REMOVE_PATH)
    confirms = _within(doc, row, **{"data-sc-mark-confirm": True})
    assert len(confirms) == 1, "the remove opened no inline confirm"
    confirm_buttons = [node for node in _within(doc, row)
                       if node["tag"] == "button" and confirms[0] in node["parents"]
                       and node["attrs"].get("formaction") ==
                       mount.url(site_page.SITE_SHEET_REMOVE_PATH)]
    assert confirm_buttons, "the confirm carries no submit of the removal door"

    # the form the controls name is the sheet's own, and it posts where the route lives
    form_id = field["attrs"]["form"]
    forms = [node for node in doc.nodes if node["tag"] == "form"
             and node["attrs"].get("id") == form_id]
    assert forms, f"the row names form {form_id!r}, which the page does not render"
    assert forms[0]["attrs"]["action"] == mount.url(site_page.SITE_SHEET_UPLOAD_PATH)
    assert "multipart/form-data" in forms[0]["attrs"]["enctype"]
    assert "sc-sheet" in form_id

    # and the script binds THAT element, not a second one
    script = (Path(__file__).resolve().parent.parent
              / "services/webservice/static/site-config.js").read_text(encoding="utf-8")
    assert "[data-sc-mark], [data-sc-sheet]" in script, \
        "the script resolves only the mark's slot: the sheet's row would never be bound"
    assert "input.sc-av-file" in script
    assert "[data-sc-mark-confirm]" in script


@pytest.mark.asyncio
async def test_the_head_states_the_sheet_and_the_payload_carries_its_words(app, config_dir,
                                                                          assets):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        assert (await upload(http, html, "guards", BENIGN.encode("utf-8"))).status_code == 303
        landed = await open_page(http)

    assert 'data-sc-pal-key>theme.sheet<' in landed.replace(" ", ""), \
        "the head does not name the key in force"
    assert "An uploaded sheet" in landed, "the head does not state which palette the site names"
    payload = re.search(r'data-sc-pal="([^"]*)"', landed)
    assert payload, "the palette block carries no payload"
    table = json.loads(html_module.unescape(payload.group(1)))
    assert table["states"]["sheet"]["key"] == "theme.sheet", table
    assert table["states"]["sheet"]["words"], table


@pytest.mark.asyncio
async def test_the_row_states_that_the_sheet_is_served_as_written(app, config_dir):
    """The page says plainly what it does not do — the sheet is not checked."""
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
    window = html[html.index('data-sc-sheet="'):]
    assert "served exactly as you wrote it" in window, \
        "the row does not say the sheet is unchecked"
    assert "@font-face" in window, "the row does not name what an unchecked sheet can carry"
