"""The Site settings card's demo pane renders the UNSAVED form, in memory, writing nothing.

WHAT IS PINNED, all through the real application (the production factory, the shell, the local
backend and a stub cluster) and the page's own forms — every token read out of a rendered page:

* the operator's own case: a site that is SWITCHED OFF, a draft posted with ``enabled`` and a changed
  field, and ``<prefix>/chrome-preview`` answering 200 with that value for THAT session — while the
  same address without the cookie answers exactly what it answered before this change;
* a draft writes nothing: ``site.yaml`` is byte-identical afterwards, its hash quoted, and no file
  appears under the config directory;
* a save clears the draft, and the box then shows the SAVED value;
* every refusal — an unknown field, an oversized body, no CSRF token, no capability — leaves the
  store's entry count unchanged;
* an expired draft is gone, and the cap refuses a new entry instead of growing;
* the address the form types is NOT applied: the demo is always the preview address's own site;
* the card says the box is a draft, and the sentence is translated in all nine catalogs.

No browser is opened: the debounce and the repaint timing are the client's own and are stated in the
report as what a browser would still have to confirm.
"""
from __future__ import annotations

import gettext
import hashlib
import io
import re
from urllib.parse import urlencode

import httpx
import pytest

from conftest import REPO_ROOT
from services.webservice import site_pages, sites
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import site_config as site_page
from test_webui_site_settings import (  # noqa: F401 - fixtures and helpers imported on purpose
    _parse, cluster, notice_of, open_page, page_path, revision_of, save_path, token_of,
)
from test_webui_write_pause import PASSWORD, build, client, sign_in  # noqa: F401 - reused

ADMIN = "specialk"
GROUP_ADMIN = "guards-admin"
NO_CAPABILITY = "player"

BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS:
    - guards-admin
    - player
"""

SERVERS_YAML = """\
SRS-1:
  managed_by:
    - 5th Guards
"""

#: ``guards`` is the site these pins switch; the root site stays enabled so the console keeps its
#: mount and the page's own addresses are the ones the pins post to.
SITE_YAML = """\
sites:
  root:
    slug: ""
    branding:
      title: Community
  guards:
    group_role: "5th Guards"
    slug: guards
    nav: [overview, leaderboard]
    enabled: false
    branding:
      title: Guards
"""

SITE_FILE_TEXT = SITE_YAML


def _users() -> list[dict]:
    """The three identities these pins need: the Admin, a manager, and one with no site capability."""
    password = local_auth.hash_password(PASSWORD, iterations=local_auth.MIN_ITERATIONS)
    return [{"username": ADMIN, "roles": ["Admin"], "password_hash": password},
            {"username": GROUP_ADMIN, "roles": ["DCS"], "scope": ["5th Guards"],
             "password_hash": password},
            {"username": NO_CAPABILITY, "roles": ["DCS"], "password_hash": password}]


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (tmp_path / "site.yaml").write_text(SITE_FILE_TEXT, encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(config_dir):
    return build(config_dir, cluster(), users=_users())


@pytest.fixture(autouse=True)
def empty_store():
    """The draft store is PROCESS state: every pin starts from an empty one and leaves none behind."""
    site_pages.reset_drafts()
    try:
        yield
    finally:
        site_pages.reset_drafts()


def draft_path() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_CONFIG_DRAFT_PATH)


def preview_url(key: str) -> str:
    """``<site prefix>/chrome-preview`` — the address the card's box renders."""
    prefix = "" if key == "root" else "/" + key
    return (prefix or "") + site_pages.CHROME_PREVIEW_PATH


def tree_digest(folder) -> dict:
    """Every file under *folder* with its size and digest — what a write would move."""
    import os

    out = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            with open(path, "rb") as handle:
                payload = handle.read()
            out[path] = (len(payload), hashlib.sha256(payload).hexdigest())
    return out


async def draft(http, html: str, data: dict | list, *, token: str | None = None,
                csrf: bool = True) -> httpx.Response:
    """POST one card's draft: the page's own token in the body, then the test's own fields.

    The body is url-encoded HERE because a repeated name (the switch's hidden ``0`` beside its ``1``)
    is a list of pairs, and this client encodes only a mapping. The content type is set explicitly:
    the route reads a form, and nothing else.
    """
    body: list = list(data.items()) if isinstance(data, dict) else list(data)
    if csrf:
        body.append(("_csrf_token", token if token is not None else token_of(html)))
    payload = urlencode(body)
    return await http.post(draft_path(), content=payload,
                           headers={"content-type": "application/x-www-form-urlencoded"})


async def switch_on_draft(http, key: str, html: str, **fields) -> httpx.Response:
    """The switch flipped ON plus the fields the card holds: the hidden ``0`` then the box's ``1``."""
    token = site_page.SITE_KEY_FIELD
    name = f"f.sites.{key}.enabled"
    body = [(token, key), (name, "0"), (name, "1")]
    body += [(f"f.sites.{key}.{leaf}", value) for leaf, value in fields.items()]
    return await draft(http, html, body)


# =========================================== 1. the operator's own case, end to end

@pytest.mark.asyncio
async def test_switching_a_switched_off_site_on_shows_it_in_the_box(app, config_dir):
    """The reported case: a site that is OFF, flipped ON in the form, READ WITHOUT a save."""
    before = (config_dir / "site.yaml").read_bytes()
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        # the address answers nothing to anybody yet — the answer this change must preserve
        assert (await http.get(preview_url("guards"))).status_code == 404

        posted = await switch_on_draft(http, "guards", page, **{"branding.title": "Guards Draft"})
        assert posted.status_code == 200, posted.text[:400]
        assert posted.json() == {"ok": True, "stored": True}

        served = await http.get(preview_url("guards"))
        assert served.status_code == 200, served.text[:400]
        assert "Guards Draft" in served.text, "the box does not carry the value the form holds"
        assert "<title>Guards Draft</title>" in served.text, "the draft's brand did not reach the bar"
        # the palette the draft's site resolves to is served too, or the demo is unpainted
        assert (await http.get("/guards" + site_pages.THEME_SHEET_PATH)).status_code == 200
        assert (await http.get("/guards/rankings")).status_code == 404, (
            "a draft made the site's own pages answer")

    # NOTHING WAS WRITTEN: the file is byte-identical and no file appeared under the config dir
    assert (config_dir / "site.yaml").read_bytes() == before
    print(f"site.yaml sha256={hashlib.sha256(before).hexdigest()} (before == after)")


@pytest.mark.asyncio
async def test_another_session_gets_exactly_the_answer_it_got_before(app):
    """The owning session sees the draft; everybody else gets the unregistered address's answer."""
    async with client(app) as owner:
        await sign_in(owner, ADMIN)
        page = await open_page(owner)
        assert (await switch_on_draft(owner, "guards", page,
                                      **{"branding.title": "Guards Draft"})).status_code == 200
        assert (await owner.get(preview_url("guards"))).status_code == 200

    async with client(app) as anonymous:
        draft_answer = await anonymous.get(preview_url("guards"))
        # a path under the same prefix that no route ever answered: the pre-change shape
        never = await anonymous.get("/guards/no-such-address")
    assert draft_answer.status_code == 404
    assert (draft_answer.status_code, draft_answer.content) == (never.status_code, never.content), (
        "a switched-off site's preview answers something other than the unregistered address did")


@pytest.mark.asyncio
async def test_a_disabled_sites_assets_answer_their_own_draft_only(app):
    """The palette, the logo and the mark follow the same rule as the preview."""
    assets = ("/guards" + site_pages.THEME_SHEET_PATH, "/guards" + site_pages.LOGO_PATH,
              "/guards" + site_pages.AVATAR_PATH)
    async with client(app) as owner:
        await sign_in(owner, ADMIN)
        page = await open_page(owner)
        for path in assets:
            assert (await owner.get(path)).status_code == 404, f"{path} answered before the draft"
        assert (await switch_on_draft(owner, "guards", page,
                                      **{"branding.title": "Guards Draft"})).status_code == 200
    async with client(app) as anonymous:
        for path in assets:
            answer = await anonymous.get(path)
            never = await anonymous.get("/guards/no-such-address")
            assert (answer.status_code, answer.content) == (never.status_code, never.content), path


# =========================================== 2. nothing is written

@pytest.mark.asyncio
async def test_a_draft_writes_nothing_at_all(app, config_dir):
    """The file's bytes and the whole config tree are unmoved by a draft, quoted by hash."""
    site_file = config_dir / "site.yaml"
    before = site_file.read_bytes()
    tree_before = tree_digest(config_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        assert (await switch_on_draft(http, "guards", page,
                                      **{"branding.title": "Guards Draft"})).status_code == 200
        for leaf, value in (("character", "pvp"), ("lang", "de"), ("nav", "overview")):
            assert (await draft(http, page, {
                site_page.SITE_KEY_FIELD: "guards",
                f"f.sites.guards.{leaf}": value})).status_code == 200, leaf
        assert (await http.get(preview_url("guards"))).status_code == 200
    assert site_file.read_bytes() == before, "a draft moved the site file"
    assert tree_digest(config_dir) == tree_before, "a draft created or changed a file"
    print(f"site.yaml sha256={hashlib.sha256(before).hexdigest()} — unchanged, no new file")


@pytest.mark.asyncio
async def test_signing_out_ends_reach_of_the_draft(app):
    """A cleared session presents no draft: the sign-out the console runs IS the drop.

    The store's only reference to an entry lives in the session (its own CSRF token), and sign-out
    runs ``session.clear()`` — so the browser that signed out carries nothing that matches, and the
    box answers what it answers to a stranger. The entry itself is memory, reaped by the idle TTL.
    """
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        assert (await switch_on_draft(http, "guards", page,
                                      **{"branding.title": "Guards Draft"})).status_code == 200
        assert (await http.get(preview_url("guards"))).status_code == 200

        left = await http.post(auth_routes.LOGOUT_PATH, data={"_csrf_token": token_of(page)})
        assert left.status_code == 303, left.text[:400]
        after = await http.get(preview_url("guards"))
        unknown = await http.get("/guards/no-such-address")
    assert (after.status_code, after.content) == (unknown.status_code, unknown.content), (
        "the signed-out session still reached the draft")


# =========================================== 3. save clears the draft

@pytest.mark.asyncio
async def test_a_save_clears_the_draft_and_the_box_shows_the_saved_value(app, config_dir):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        assert (await switch_on_draft(http, "guards", page,
                                      **{"branding.title": "Draft Title"})).status_code == 200
        assert "Draft Title" in (await http.get(preview_url("guards"))).text
        assert site_pages.draft_count() == 1

        saved = await http.post(save_path(), data={
            "_csrf_token": token_of(page), "_revision": revision_of(page),
            "_site": "guards",
            "f.sites.guards.branding.title": "Saved Title",
            "f.sites.guards.enabled": "1"})
        assert saved.status_code == 303, saved.text[:400]
        assert site_pages.draft_count() == 0, "the save left the draft in the store"

        served = await http.get(preview_url("guards"))
    assert served.status_code == 200
    assert "Saved Title" in served.text, "the box does not follow the saved file after a save"
    assert "Draft Title" not in served.text, "the box still renders the cleared draft"
    assert "Saved Title" in (config_dir / "site.yaml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_the_cards_own_discard_drops_the_draft(app):
    """What leaving the card sends: the same door with the discard control set."""
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        assert (await switch_on_draft(http, "guards", page,
                                      **{"branding.title": "Guards Draft"})).status_code == 200
        assert site_pages.draft_count() == 1
        cleared = await draft(http, page, {site_page.SITE_KEY_FIELD: "guards",
                                           site_page.SITE_DRAFT_CLEAR_FIELD: "1"})
        assert cleared.status_code == 200, cleared.text[:400]
        assert cleared.json() == {"ok": True, "stored": False}
        assert site_pages.draft_count() == 0
        assert (await http.get(preview_url("guards"))).status_code == 404


# =========================================== 4. the refusals

@pytest.mark.asyncio
async def test_an_unknown_field_is_refused_and_the_store_is_untouched(app):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        count = site_pages.draft_count()
        reply = await draft(http, page, {site_page.SITE_KEY_FIELD: "root",
                                         "f.sites.root.branding.nonsense": "x"})
        assert reply.status_code == 400, reply.text[:400]
        assert site_pages.draft_count() == count


@pytest.mark.asyncio
async def test_another_sites_field_is_refused(app):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        reply = await draft(http, page, {site_page.SITE_KEY_FIELD: "root",
                                         "f.sites.guards.branding.title": "Foreign"})
        assert reply.status_code == 400, reply.text[:400]
        assert site_pages.draft_count() == 0


@pytest.mark.asyncio
async def test_an_oversized_body_is_refused_before_it_is_parsed(app):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        filler = "x" * (site_page.SITE_DRAFT_BODY_BYTES + 1024)
        reply = await draft(http, page, {site_page.SITE_KEY_FIELD: "root",
                                         "f.sites.root.branding.title": filler})
        assert reply.status_code == 413, reply.status_code
        assert site_pages.draft_count() == 0


@pytest.mark.asyncio
async def test_a_body_without_the_token_is_refused(app):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        reply = await draft(http, page, {site_page.SITE_KEY_FIELD: "root",
                                         "f.sites.root.branding.title": "No Token"}, csrf=False)
        assert reply.status_code == 403, reply.status_code
        assert site_pages.draft_count() == 0


@pytest.mark.asyncio
async def test_a_caller_without_the_capability_is_refused(app):
    async with client(app) as http:
        await sign_in(http, NO_CAPABILITY)
        # a caller that cannot open the page has no token to mint one from: build the body by hand
        reply = await http.post(draft_path(), data={site_page.SITE_KEY_FIELD: "root",
                                                    "_csrf_token": "not-a-session-token",
                                                    "f.sites.root.branding.title": "Denied"})
        assert reply.status_code in (401, 403), reply.status_code
        assert site_pages.draft_count() == 0


@pytest.mark.asyncio
async def test_a_site_outside_the_callers_scope_is_refused(app):
    """A manager of another token cannot draft a site they cannot see (the save's own rule)."""
    async with client(app) as http:
        await sign_in(http, GROUP_ADMIN)
        page = await open_page(http)
        reply = await draft(http, page, {site_page.SITE_KEY_FIELD: "root",
                                         "f.sites.root.branding.title": "Not Mine"})
        assert reply.status_code == 404, reply.status_code
        assert site_pages.draft_count() == 0


# =========================================== 5. the TTL and the cap

@pytest.mark.asyncio
async def test_an_expired_draft_is_gone(app, monkeypatch):
    """Thirty idle minutes and the entry is reaped; the box goes back to the saved site."""
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        assert (await switch_on_draft(http, "guards", page,
                                      **{"branding.title": "Guards Draft"})).status_code == 200
        assert site_pages.draft_count() == 1

        later = site_pages._clock() + site_pages.DRAFT_TTL_SECONDS + 1
        monkeypatch.setattr(site_pages, "_clock", lambda: later)
        assert (await http.get(preview_url("guards"))).status_code == 404
        assert site_pages.draft_count() == 0, "the expired entry is still held"


@pytest.mark.asyncio
async def test_the_cap_refuses_rather_than_growing(app, monkeypatch):
    monkeypatch.setattr(site_pages, "DRAFT_MAX_ENTRIES", 1)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        first = await draft(http, page, {site_page.SITE_KEY_FIELD: "root",
                                         "f.sites.root.branding.title": "One"})
        assert first.status_code == 200, first.text[:400]
        second = await switch_on_draft(http, "guards", page, **{"branding.title": "Two"})
        assert second.status_code == 429, second.status_code
        assert site_pages.draft_count() == 1, "the store grew past its cap"


# =========================================== 6. an enabled site, and the address the form types

@pytest.mark.asyncio
async def test_an_enabled_sites_box_follows_the_form_before_save(app):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        assert "Community" in (await http.get(preview_url("root"))).text
        reply = await draft(http, page, {site_page.SITE_KEY_FIELD: "root",
                                         "f.sites.root.character": "pvp",
                                         "f.sites.root.branding.title": "Draft Community"})
        assert reply.status_code == 200, reply.text[:400]
        served = await http.get(preview_url("root"))
    assert served.status_code == 200
    assert "Draft Community" in served.text


@pytest.mark.asyncio
async def test_the_address_the_form_types_is_not_applied_in_the_demo(app):
    """The demo always renders at the preview address, whatever the slug box says."""
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http)
        reply = await switch_on_draft(http, "guards", page,
                                      **{"slug": "renamed", "branding.title": "Guards Draft"})
        assert reply.status_code == 200, reply.text[:400]
        served = await http.get(preview_url("guards"))
        assert served.status_code == 200
        assert "/renamed" not in served.text, "the draft moved the address the demo renders at"
        assert "/guards/rankings" in served.text, "the demo lost the site's own prefix"


# =========================================== 7. the card and the catalogs

@pytest.mark.asyncio
async def test_the_card_says_the_box_is_a_draft_and_carries_the_doors(app):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
    panels = [node for node in _parse(html).walk() if "sc-site" in node.classes()]
    assert len(panels) == 2, f"{len(panels)} panels on a page with two sites"
    for panel in panels:
        assert panel.attrs.get("data-sc-draft-url") == draft_path()
        assert panel.attrs.get("data-sc-draft-clear") == site_page.SITE_DRAFT_CLEAR_FIELD
        assert panel.attrs.get("data-sc-preview-url"), "the panel carries no preview address"
        drafts = [node for node in panel.walk() if "sc-pv-draft" in node.classes()]
        assert len(drafts) == 1, "the panel draws no sentence saying the box is a draft"
        assert "".join(drafts[0].text).strip() == site_page.SITE_PANEL_DRAFT


def test_the_draft_sentence_is_translated_in_all_nine_catalogs():
    """The one new msgid: nine ``.po`` files carry a translation and nine ``.mo`` files serve it."""
    languages = sorted(path.parent.parent.name for path in
                       (REPO_ROOT / "locale").glob("*/LC_MESSAGES/webui.po"))
    assert len(languages) == 9, f"the catalogs found are {languages}"
    for language in languages:
        folder = REPO_ROOT / "locale" / language / "LC_MESSAGES"
        source = (folder / "webui.po").read_text(encoding="utf-8")
        assert site_page.SITE_PANEL_DRAFT in source, f"{language} does not carry the msgid"
        with open(folder / "webui.mo", "rb") as handle:
            catalog = gettext.GNUTranslations(io.BytesIO(handle.read()))
        translated = catalog.gettext(site_page.SITE_PANEL_DRAFT)
        assert translated and translated != site_page.SITE_PANEL_DRAFT, (
            f"{language} renders the English sentence")


def test_the_draft_door_is_declared_in_the_cards_own_route_table():
    declared = site_page.capabilities()
    assert declared[site_page.SITE_CONFIG_DRAFT_PATH] == site_page.SITE_WRITE_CAPABILITY


def test_the_binding_script_carries_the_draft_half():
    """The client half exists in the shipped bytes: debounce, mount, unmount, discard."""
    script = (REPO_ROOT / "services" / "webservice" / "static" / "site-config.js").read_text(
        encoding="utf-8")
    assert "data-sc-site" in script and "data-sc-draft-url" in script
    assert re.search(r"DEBOUNCE_MS\s*=\s*400", script), "no ~0.4 s debounce"
    assert "sc-preview-mounted" in script, "a frame mounted after load is never bound"
    assert "sendBeacon" in script, "leaving the card posts no discard"
    assert "data-sc-pv-off" in script, "the off-state cannot be restored"