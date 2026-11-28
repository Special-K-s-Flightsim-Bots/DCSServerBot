"""The Site settings page's Carrier switch: one row, one switch, one leaf.

Each pin is a promise the row makes:

* a site that stores nothing draws the switch OFF, and a site storing ``pages.carrier: true`` draws
  it ON — the state the public page would serve;
* the row is ONE switch in the PAGES block, beside the language leaf and the Squadrons layout row,
  posting ``pages.carrier`` through the card's own async save form, with the companion hidden field
  a switch states itself with;
* a switch pressed but not saved is drawn from the session's draft, and the file stays untouched;
* a save with the switch ON writes the leaf, and the page that comes back reads it from the FILE;
* a save that leaves it OFF drops the leaf (and the empty ``pages:`` map), so an unset site keeps
  following the shipped default — and an unset site saved off writes nothing at all;
* the row is drawn in exactly the cards the section switches are, and for no other audience.

Driven through the real application (the production factory, the shell, the local backend and a
stub cluster), with the page's own form: every CSRF token and revision is read out of a rendered
page.
"""
from __future__ import annotations

import re
from html import unescape

import pytest

from services.webservice import mount, site_pages, sites
from services.webservice.auth import local as local_auth
from services.webservice.pages import site_config as site_page
from test_webui_site_settings import (  # noqa: F401 - the page's harness, reused
    ADMIN, GROUP_ADMIN, cluster, notice_of, open_page, page_path, post, sign_in_as, token_of,
)
from test_webui_write_pause import PASSWORD, build, client

#: a signed-in identity the page does not admit: it holds no cluster role and no scope.
NO_CAPABILITY = "player"

BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS:
    - guards-admin
    - other-admin
    - player
"""

SERVERS_YAML = """\
SRS-1:
  managed_by:
    - 5th Guards
SRS-2:
  managed_by:
    - Other Token
"""

#: ``guards`` switches the Carrier page on, ``root`` and ``others`` store nothing — the shipped
#: answer, which is OFF.
SITE_YAML = """\
sites:
  root:
    slug: ""
    branding:
      title: Community
  guards:
    group_role: "5th Guards"
    slug: guards
    pages:
      carrier: true
    branding:
      title: Guards
  others:
    group_role: "Other Token"
    slug: others
"""


def _users() -> list[dict]:
    """The four identities these pins need: an Admin, two managers of one token each, a stranger."""
    password = local_auth.hash_password(PASSWORD, iterations=local_auth.MIN_ITERATIONS)
    return [{"username": ADMIN, "roles": ["Admin"], "password_hash": password},
            {"username": GROUP_ADMIN, "roles": ["DCS"], "scope": ["5th Guards"],
             "password_hash": password},
            {"username": "other-admin", "roles": ["DCS"], "scope": ["Other Token"],
             "password_hash": password},
            {"username": NO_CAPABILITY, "roles": ["DCS"], "password_hash": password}]


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (tmp_path / "site.yaml").write_text(SITE_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(config_dir):
    return build(config_dir, cluster(), users=_users())


@pytest.fixture
def site_file(config_dir):
    return config_dir / sites.SITE_FILE


@pytest.fixture(autouse=True)
def empty_store():
    """The draft store is PROCESS state: every pin starts from an empty one and leaves none behind."""
    site_pages.reset_drafts()
    try:
        yield
    finally:
        site_pages.reset_drafts()


def draft_path() -> str:
    return mount.url(site_page.SITE_CONFIG_DRAFT_PATH)


def pages_block(html: str, key: str) -> str:
    """*key*'s PAGES block — the one section that owns "which page, in what form"."""
    card = html[html.index(f'data-sc-card="{key}"'):]
    start = card.index('data-sc-block="pages"')
    return card[start:card.index("</section>", start)]


def carrier_row(html: str, key: str) -> str:
    """The Carrier row of *key*'s PAGES block: its own element, closed by its own tag."""
    block = pages_block(html, key)
    marker = block.index('data-sc-page="carrier"')
    start = block.rindex("<div", 0, marker)
    depth = 0
    for tag in re.finditer(r"<div\b|</div>", block[start:]):
        depth += 1 if tag.group(0) == "<div" else -1
        if depth == 0:
            return unescape(block[start:start + tag.end()])
    raise AssertionError(f"the Carrier row of {key} is not closed")


def carrier_switch(html: str, key: str) -> str:
    """The checkbox the Carrier row draws for *key* — its tag, verbatim."""
    pattern = re.compile(
        r'<input type="checkbox" role="switch" name="f\.sites\.' + re.escape(key)
        + r'\.pages\.carrier" value="1"[^>]*>')
    match = pattern.search(html)
    assert match is not None, f"the Carrier row draws no switch for {key}"
    return match.group(0)


def is_on(html: str, key: str) -> bool:
    return "checked" in carrier_switch(html, key)


# ======================================================== 1. the switch draws the served answer

@pytest.mark.asyncio
async def test_a_site_storing_nothing_draws_the_switch_off(app):
    """Absent means OFF in the model, so a site that declares no map draws the switch off."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    assert not is_on(html, "root"), "a site storing nothing drew the switch on"
    assert not is_on(html, "others"), "a site storing nothing drew the switch on"


@pytest.mark.asyncio
async def test_a_site_storing_the_leaf_true_draws_it_on(app):
    """The card draws what the public page would serve, read through the one resolution."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    assert is_on(html, "guards"), "a site storing pages.carrier: true drew the switch off"


# ======================================================== 2. the row is one switch, in its block

@pytest.mark.asyncio
async def test_the_row_is_one_switch_beside_the_language_leaf_in_the_pages_block(app):
    """ONE control, the page's shipped switch, in the block that owns "which page, in what form"."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    block = pages_block(html, "guards")
    row = carrier_row(html, "guards")
    assert 'name="f.sites.guards.pages.carrier"' in row, "the row left the PAGES block"
    assert 'name="f.sites.guards.lang"' in block, "the row is not beside the language leaf"
    assert 'name="f.sites.guards.layout.squadrons"' in block, "the row is not beside the layout row"
    assert row.count("<input") == 2, "the row is not the hidden companion plus one box"
    assert '<input type="hidden" name="f.sites.guards.pages.carrier" value="0">' in row, (
        "the switch does not state itself when it is off")
    assert 'class="sc-set"' in row, "the switch is outside the container its own style is declared in"
    assert 'class="sc-switch' in row, "the row does not use the page's shipped switch"
    assert "<select" not in row and 'type="radio"' not in row, "the row introduced a second control"
    # the row posts with the card's own form, which is the page's async save form
    form = re.search(r'<form class="sc-form" id="sc-save-guards"[^>]*>', html)
    assert form is not None, "the card's save form is missing"
    assert "data-async" in form.group(0), "the row's form is not the async save form"


@pytest.mark.asyncio
async def test_the_row_states_the_key_and_a_translated_label_and_hint(app):
    """The label, the hint and the key the row prints are the page's own translated chrome."""
    from services.webservice import i18n

    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    row = carrier_row(html, "guards")
    assert "pages.carrier" in row, "the row does not name the key it writes"
    for msgid in (site_page.SITE_PAGE_CARRIER, site_page.SITE_PAGE_CARRIER_HINT):
        assert i18n._(msgid) == msgid, "a row string is not a marked msgid"
        assert msgid in row, f"the row does not print {msgid!r}"


# ======================================================== 3. the draft, then the save

@pytest.mark.asyncio
async def test_a_pressed_switch_is_drawn_from_the_draft_and_a_save_writes_the_leaf(app, site_file):
    """A switch pressed but not saved survives the block's own write; Save writes it to the file."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        typed_reply = await http.post(draft_path(), data={
            "_csrf_token": token_of(html), site_page.SITE_KEY_FIELD: "root",
            "f.sites.root.pages.carrier": "1"})
        assert typed_reply.status_code == 200, typed_reply.text[:400]
        typed = await open_page(http)
        assert is_on(typed, "root"), "the page did not draw the switch the draft carries"
        assert "pages" not in (sites.load_site_block(site_file.parent) or {})["root"], (
            "a draft wrote the leaf into the file")

        reply = await post(http, typed, {site_page.SITE_KEY_FIELD: "root",
                                         "f.sites.root.pages.carrier": "1"})
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert site_pages.draft_count() == 0, "the save left the draft behind"
    block = sites.load_site_block(site_file.parent) or {}
    assert block["root"]["pages"]["carrier"] is True, block["root"]
    assert is_on(landed, "root"), "the page that came back does not read the saved leaf"


@pytest.mark.asyncio
async def test_a_save_that_switches_it_off_removes_the_leaf(app, site_file):
    """The shipped answer is OFF, so switching it off removes the decision — map and leaf both."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {site_page.SITE_KEY_FIELD: "guards",
                                        "f.sites.guards.pages.carrier": "0"})
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    block = sites.load_site_block(site_file.parent) or {}
    assert "pages" not in block["guards"], block["guards"]
    assert not is_on(landed, "guards")


@pytest.mark.asyncio
async def test_an_unset_site_saved_off_writes_nothing(app, site_file):
    """A site that stores nothing keeps following the shipped default, byte for byte."""
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {site_page.SITE_KEY_FIELD: "root",
                                        "f.sites.root.pages.carrier": "0"})
        assert reply.status_code == 303, reply.text[:400]
    assert site_file.read_bytes() == before, "a save of the default wrote the file"
    assert "pages" not in (sites.load_site_block(site_file.parent) or {})["root"]


# ======================================================== 4. the audience

@pytest.mark.asyncio
async def test_the_row_is_drawn_for_the_same_audience_as_the_section_switches(app):
    """The card's own gate (``site_in_scope`` under the page's ``site.view``) is the row's gate.

    The row carries no gate of its own: it is drawn wherever the section switches are drawn, and
    an identity the page does not admit gets neither the card nor the row.
    """
    async with client(app) as http:
        await sign_in_as(http, GROUP_ADMIN)
        own = await open_page(http)
    assert 'name="f.sites.guards.sections.' in own, "the fixture draws no section switch"
    assert 'name="f.sites.guards.pages.carrier"' in own, "the row is missing where the switches are"
    assert 'data-sc-card="others"' not in own, "the scope gate drew another site's card"

    async with client(app) as http:
        await sign_in_as(http, "other-admin")
        other = await open_page(http)
    assert 'data-sc-card="others"' in other
    assert 'name="f.sites.others.sections.' in other
    assert 'name="f.sites.others.pages.carrier"' in other
    assert 'data-sc-card="guards"' not in other
    assert "f.sites.guards.pages.carrier" not in other

    async with client(app) as http:
        await sign_in_as(http, NO_CAPABILITY)
        refused = await http.get(page_path())
    assert refused.status_code in (401, 403), refused.status_code
    assert "pages.carrier" not in refused.text, "a refused page still drew the row"


@pytest.mark.asyncio
async def test_the_save_needs_the_write_capability_the_sections_need(app, site_file, monkeypatch):
    """The leaf is written through the card's own save, behind the one write predicate.

    The card's save asks ``site_access(request).write`` before it reads a document, exactly as it
    does for every neighbouring control; a viewer is refused the same way whichever leaf the post
    names, and nothing is written.
    """
    before = site_file.read_bytes()
    viewer = site_page.SiteAccess(view=True, write=False, structure=False, expert=False,
                                  passwords=False)
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        monkeypatch.setattr(site_page, "site_access", lambda request: viewer)
        html = await open_page(http)
        refused = await post(http, html, {site_page.SITE_KEY_FIELD: "guards",
                                          "f.sites.guards.pages.carrier": "1"})
        landed = await open_page(http)
    assert refused.status_code == 303, refused.text[:400]
    assert site_page.SITE_REFUSED_SCOPE in unescape(notice_of(landed)), notice_of(landed)
    assert site_file.read_bytes() == before, "a viewer's save wrote the leaf"


# ================================================ the switch carries its own menu entry

@pytest.mark.asyncio
async def test_switching_the_page_on_adds_its_entry_to_a_stored_menu(config_dir, site_file):
    """ONE decision: a site that stores a menu without ``carrier`` gains the entry with the switch.

    Without this the operator turns the page on and the bar still has no entry for it — a page that
    exists and cannot be reached, with the reason hidden in a second control further up the card.
    """
    site_file.write_text("""sites:
  root:
    slug: ""
    nav: [servers, overview, leaderboard, players]
    branding:
      title: Community
""", encoding="utf-8")
    app = build(config_dir, cluster(), users=_users())
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {site_page.SITE_KEY_FIELD: "root",
                                        "f.sites.root.pages.carrier": "1"})
        assert reply.status_code == 303, reply.text[:400]

    block = (sites.load_site_block(config_dir) or {}).get("root") or {}
    assert block.get("pages", {}).get("carrier") is True, block
    assert block.get("nav") == ["servers", "overview", "leaderboard", "carrier", "players"], (
        f"the switch left the menu without its entry: {block.get('nav')!r}")

    site_pages.set_loaded_plugins(["os", "greenieboard"])
    try:
        # the public site serves the INSTALLED catalogue, so it is read through a fresh build — the
        # same rebuild a save triggers in the running service
        app = build(config_dir, cluster(), users=_users())
        async with client(app) as http:
            home = await http.get("/")
        assert f'href="{site_pages.CARRIER_PATH}"' in home.text, \
            "the saved site's bar carries no entry for the page it switched on"
    finally:
        site_pages.set_loaded_plugins(None)
