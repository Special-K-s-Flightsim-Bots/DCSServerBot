"""The Site settings page: one card per site, the one save, the capabilities and the CSS check.

Each pin is a promise the page makes:

* the card renders the CODE's vocabulary — every section id, every character, every offered metric
  and every shipped preset — and a section added to the constant appears without touching a template;
* a save writes only the keys it posted, so every other line of ``site.yaml`` (comments included)
  comes back byte-identical (T3);
* a candidate the loader's own parser refuses is never written, and the file is byte-identical
  afterwards (T4);
* a content-only save is live and offers nothing to apply, while a save that moved a structural key
  APPLIES the file itself: the console re-reads ``site.yaml`` before the answer, so the site serves
  with no further press;
* a group admin may edit their own site's CONTENT and is refused its address, its role, its switch,
  the expert sheet and every other site (T9);
* the four capabilities are declared as the card states, and the nav entry names ``site.view``.

Driven through the real application (the production factory, the shell, the local backend and a stub
cluster), with the page's own forms: every CSRF token and revision is read out of a rendered page.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import types
from html.parser import HTMLParser
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from services.webservice import mount, site_pages, sites, theme_css
from services.webservice.auth import local as local_auth
from services.webservice.pages import site_config as site_page
from services.webservice.pages import site_leaderboard
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, instance, server, sign_in,
)

ADMIN = "specialk"
GROUP_ADMIN = "guards-admin"

BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
  DCS:
    - guards-admin
    - other-admin
"""

#: two servers, so a group admin is a MANAGER of the cluster and their scope names one token.
SERVERS_YAML = """\
SRS-1:
  managed_by:
    - 5th Guards
SRS-2:
  managed_by:
    - Other Token
"""

#: the site file the page owns. The comments are part of the pin: a save must not lose them.
SITE_YAML = """\
# the public site's settings — comments and key order are part of the file
sites:
  root:
    slug: ""
    branding:
      title: Community
  guards:
    group_role: "5th Guards"
    slug: guards
    character: pve
    branding:
      title: Guards
  others:
    group_role: "Other Token"
    slug: others
"""


def cluster():
    a = server("SRS-1", ["5th Guards"])
    b = server("SRS-2", ["Other Token"], node_name="dcs-b-01", instance="Host-B-Instance")
    master = types.SimpleNamespace(name="dcs-master-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-master-01": master}, node=master,
                   instances=[instance("Host-A-Instance", a)])


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (tmp_path / "site.yaml").write_text(SITE_YAML, encoding="utf-8")
    return tmp_path


def _hash() -> str:
    return local_auth.hash_password("correct horse battery staple",
                                    iterations=local_auth.MIN_ITERATIONS)


def _users() -> list[dict]:
    """The three identities these pins need: the Admin, and two managers of ONE token each."""
    password = _hash()
    return [{"username": ADMIN, "roles": ["Admin"], "password_hash": password},
            {"username": GROUP_ADMIN, "roles": ["DCS"], "scope": ["5th Guards"],
             "password_hash": password},
            {"username": "other-admin", "roles": ["DCS"], "scope": ["Other Token"],
             "password_hash": password}]


@pytest.fixture
def app(config_dir):
    return build(config_dir, cluster(), users=_users())


@pytest.fixture
def site_file(config_dir):
    return config_dir / sites.SITE_FILE


def page_path() -> str:
    """The page's address under the console's mount (a configured site owns ``/``)."""
    from services.webservice import mount

    return mount.url(site_page.SITE_CONFIG_PATH)


def save_path() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_CONFIG_SAVE_PATH)


def validate_path() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_CSS_VALIDATE_PATH)


async def open_page(http) -> str:
    response = await http.get(page_path())
    assert response.status_code == 200, f"the page answered {response.status_code}: {response.text[:400]}"
    return response.text


def token_of(html: str) -> str:
    match = re.search(r'name="_csrf_token" value="([^"]*)"', html)
    assert match, "the page rendered no CSRF field"
    return match.group(1)


def revision_of(html: str) -> str:
    match = re.search(r'name="_revision" value="([^"]*)"', html)
    assert match, "the page rendered no revision field"
    return match.group(1)


async def sign_in_as(http, username: str) -> None:
    await sign_in(http, username)


async def post(http, html: str, data: dict):
    """POST one card's form: the session's token and the page's revision, then the test's own fields."""
    body = {"_csrf_token": token_of(html), "_revision": revision_of(html), **data}
    return await http.post(save_path(), data=body)


def notice_of(html: str) -> str:
    match = re.search(r'<span class="notice-msg">(.*?)</span>', html, re.S)
    return match.group(1) if match else ""


# ============================================================ the card renders the vocabulary

@pytest.mark.asyncio
async def test_the_page_lists_every_site_and_the_whole_vocabulary(app):
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    for key in ("root", "guards", "others"):
        assert f'value="{key}"' in html, f"the {key} card is missing"
    for ident in sites.SECTION_IDS:
        assert f"f.sites.root.sections.{ident}" in html, f"no switch for section {ident}"
    for ident in sites.CHARACTERS:
        assert f'name="f.sites.root.character" value="{ident}"' in html, f"no option for {ident}"
    for ident in sites.LEADERBOARD_METRICS:
        assert f'name="f.sites.root.metrics" value="{ident}"' in html, f"no metric {ident}"
    for preset in theme_css.PRESETS:
        assert f'name="f.sites.root.theme.preset" value="{preset.id}"' in html


@pytest.mark.asyncio
async def test_a_section_added_to_the_constant_appears_without_a_template_change(app, monkeypatch):
    """The card is drawn from the model's tuple — there is no second list in the template."""
    monkeypatch.setattr(sites, "SECTION_IDS", sites.SECTION_IDS + ("brand_new",))
    monkeypatch.setattr(site_page, "SECTION_LABELS",
                        dict(site_page.SECTION_LABELS, brand_new="Brand new"))
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    assert "f.sites.root.sections.brand_new" in html


# ============================================= the preset gallery: the press, the write, the sheet

def gallery(html: str, key: str) -> list[dict]:
    """Each tile of ONE card's gallery: its stored state, the radio it carries, and its badge.

    Parsed off the rendered markup, so the pin is the tiles the operator presses and not the loop
    that drew them.
    """
    start = html.index(f'name="f.sites.{key}.theme.preset"')
    opening = html.rindex("<label", 0, start)
    closing = html.index("</div>", html.index('value="', start))
    chunk = html[opening:closing]
    tiles: list[dict] = []
    for match in re.finditer(r'<label class="([^"]*)" data-sc-preset-tile>(.*?)</label>', chunk,
                             re.S):
        body = match.group(2)
        radio = re.search(r'<input type="radio"[^>]*value="([^"]*)"([^>]*)>', body)
        badge = re.search(r'<span class="st-badge[^"]*" data-sc-preset-badge([^>]*)>', body)
        tiles.append({
            "on": "on" in match.group(1).split(),
            "value": radio.group(1),
            "checked": "checked" in radio.group(2),
            "badge": badge is not None,
            "badge_shown": badge is not None and "hidden" not in badge.group(1),
        })
    return tiles


@pytest.mark.asyncio
async def test_the_gallery_states_the_palette_in_use_on_every_tile(app):
    """Every tile carries the badge and only the stored palette draws it — the shipped one too."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    tiles = gallery(html, "root")
    assert [tile["value"] for tile in tiles] == [""] + [preset.id for preset in theme_css.PRESETS]
    assert all(tile["badge"] for tile in tiles), "a tile carries no badge to move on a press"
    assert [tile["value"] for tile in tiles if tile["badge_shown"]] == [""], (
        f"the gallery states the palette in use on {[t['value'] for t in tiles if t['badge_shown']]}")
    assert tiles[0]["on"] is True, "the shipped palette is not drawn as the stored one"


@pytest.mark.asyncio
async def test_a_save_that_posts_a_preset_writes_it_and_the_site_serves_that_sheet(app, site_file,
                                                                                 config_dir):
    """PINS (b) and (c): the post reaches ``theme.preset``, the page reads it back, ``/theme.css``
    is that preset's sheet — and the shipped palette is a choice again, not a one-way door."""
    chosen = "slate"
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        assert [tile["value"] for tile in gallery(html, "root") if tile["checked"]] == [""]
        before = await http.get("/theme.css")
        assert before.text.strip() == theme_css.compose(None).strip(), (
            "a site that names no palette is not served the shipped one")

        reply = await post(http, html, {"_site": "root", "f.sites.root.theme.preset": chosen})
        assert reply.status_code == 303, reply.text[:400]
        block = sites.load_site_block(config_dir) or {}
        assert (block.get("root") or {}).get("theme", {}).get("preset") == chosen, (
            f"the save left preset={(block.get('root') or {}).get('theme')!r}")

        landed = await open_page(http)
        tiles = gallery(landed, "root")
        assert [tile["value"] for tile in tiles if tile["checked"]] == [chosen], (
            "the page does not read back the preset it just wrote")
        assert [tile["value"] for tile in tiles if tile["badge_shown"]] == [chosen], (
            "the 'in use' badge did not move to the palette the file now stores")
        assert [tile["value"] for tile in tiles if tile["on"]] == [chosen]

        sheet = await http.get("/theme.css")
        assert sheet.status_code == 200
        assert sheet.text.strip() == theme_css.preset_sheet(chosen).strip(), (
            "the site is not served the sheet of the palette its file names")

        reply = await post(http, landed, {"_site": "root", "f.sites.root.theme.preset": ""})
        assert reply.status_code == 303, reply.text[:400]
        cleared = await http.get("/theme.css")
    assert (sites.load_site_block(config_dir)["root"].get("theme") or {}).get("preset") in ("", None)
    assert cleared.text.strip() == theme_css.compose(None).strip(), (
        "choosing the shipped palette back did not put the shipped sheet in force")


def test_every_section_and_character_has_a_label():
    """A label table is not a second vocabulary: an id the model carries must have words here."""
    assert set(site_page.SECTION_LABELS) == set(sites.SECTION_IDS)
    assert set(site_page.CHARACTER_LABELS) == set(sites.CHARACTERS)
    assert set(site_leaderboard.METRIC_MSGIDS) == set(sites.DEFAULT_METRICS)


# ============================================================== the content save (T3: only its keys)

@pytest.mark.asyncio
async def test_a_content_save_changes_only_its_own_keys(app, site_file):
    """A save that edits ONE branding key leaves every other line, comment and key byte-identical."""
    before = site_file.read_text(encoding="utf-8")
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "guards",
                                        "f.sites.guards.branding.title": "Guards Wing"})
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    after = site_file.read_text(encoding="utf-8")
    changed = [(old, new) for old, new in zip(before.splitlines(), after.splitlines())
               if old != new]
    assert changed == [("      title: Guards", "      title: Guards Wing")], changed
    assert len(before.splitlines()) == len(after.splitlines()), "the save changed the file's shape"
    assert "Guards Wing" in landed


@pytest.mark.asyncio
async def test_a_content_save_is_live_and_offers_nothing_to_apply(app):
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        await post(http, html, {"_site": "guards", "f.sites.guards.branding.title": "Live"})
        landed = await open_page(http)
    assert "nothing is reloaded or restarted" in notice_of(landed)
    assert "Re-read the site settings now?" not in landed
    assert "notice-apply" not in landed


@pytest.mark.asyncio
async def test_an_unchecked_section_is_written_false(app, site_file):
    """A section switched OFF on a site that states no ``sections:`` map is a genuine change."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        fields = {"_site": "guards"}
        for ident in sites.SECTION_IDS:
            value = "0" if ident == "season" else "1"
            fields[f"f.sites.guards.sections.{ident}"] = value
        await post(http, html, fields)
    block = sites.load_site_block(site_file.parent)
    assert block["guards"]["sections"]["season"] is False


# ==================================================== a structural save applies its own file

@pytest.mark.asyncio
async def test_a_structural_change_applies_before_the_answer(app, site_file):
    """ONE POST moves the running console: no apply control, no confirmation, no second press."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "guards", "f.sites.guards.slug": "guards-wing"})
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
        # THE RUNNING TABLE MOVED: the new address serves, the old one no longer does
        assert (await http.get("/guards-wing")).status_code == 200, (
            "the save did not apply the site's new address")
        assert (await http.get("/guards")).status_code == 404, (
            "the site still answers at the address its file no longer names")
    told = notice_of(landed)
    assert "re-read the site settings file" in told, told
    assert "live now" in told, told
    assert "notice-apply" not in landed, "a structural save still offers a second press"
    assert "Re-read the site settings now?" not in landed
    assert sites.load_site_block(site_file.parent)["guards"]["slug"] == "guards-wing"


@pytest.mark.asyncio
async def test_switching_a_site_off_is_structural_and_applies(app, site_file):
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        await post(http, html, {"_site": "guards", "f.sites.guards.enabled": "0"})
        landed = await open_page(http)
        assert (await http.get("/guards")).status_code == 404, (
            "the switched-off site still answers: the save did not apply")
    assert "notice-apply" not in landed
    assert sites.load_site_block(site_file.parent)["guards"]["enabled"] is False


@pytest.mark.asyncio
async def test_a_structural_save_leaves_no_offer_in_the_session(app):
    """The apply the notice would have carried is not recorded either: nothing is left to press."""
    from services.webservice import config_apply, mount

    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        await post(http, html, {"_site": "guards", "f.sites.guards.slug": "guards-wing"})
        landed = await open_page(http)
        assert "notice-apply" not in landed
        # the door finds no offer for the file: pressing it by hand answers the spent-offer refusal
        asked = await http.post(mount.url(config_apply.APPLY_ASK_PATH),
                               data={"_csrf_token": token_of(landed),
                                     config_apply.APPLY_KIND_FIELD: "main",
                                     config_apply.APPLY_NAME_FIELD: config_apply.SITE_FILE})
        assert asked.status_code == 303, asked.text[:400]
        told = notice_of(await open_page(http))
    assert "no longer valid" in told, told


@pytest.mark.asyncio
async def test_a_save_answers_the_card_whose_save_was_pressed(app):
    """The answer NAMES the card, and the page opens it.

    A page of several cards always opened the FIRST one, so pressing Save on the third card landed
    the operator at the top with the first site's switch on screen — which reads as "it saved the
    wrong site". The destination is the page plus the key whose Save was pressed.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "guards",
                                        "f.sites.guards.branding.title": "Guards Wing"})
        assert reply.status_code == 303, reply.text[:400]
        assert reply.headers["location"] == f"{page_path()}?site=guards", reply.headers["location"]
        landed = await http.get(reply.headers["location"])
    assert landed.status_code == 200, landed.text[:400]
    cards = _cards(landed.text)
    assert "open" in cards["guards"].attrs, "the card whose Save was pressed came back closed"
    assert "open" not in cards["others"].attrs, "an unrelated card was opened"


@pytest.mark.asyncio
async def test_a_save_states_its_destination_to_the_background_submit(app):
    """The console's own submit cannot read a 303, so the route hands it the URL instead.

    ``submit.js`` posts with ``fetch(redirect: "manual")``, which makes the ``Location`` opaque. A
    route that only 303s therefore leaves the script navigating to the page it is already on — the
    top of the list, first card open. The JSON face carries the same target as the 303.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await http.post(save_path(),
                                data={"_csrf_token": token_of(html),
                                      "_revision": revision_of(html),
                                      "_site": "others",
                                      "f.sites.others.branding.title": "Others Two"},
                                headers={"accept": "application/json"})
    assert reply.status_code == 200, reply.text[:400]
    assert reply.json().get("redirect") == f"{page_path()}?site=others", reply.text[:400]


# ====================================================== a refused candidate is never written (T4)

@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,expected", (
    ("f.sites.guards.slug", "others", "already the slug of site"),
    ("f.sites.guards.slug", "console", "collides with a path"),
    ("f.sites.guards.group_role", "No Such Role", "matches no managed_by value"),
    # the retired theme key is no longer a field of the schema, so the post is refused as unplaceable
    ("f.sites.guards.theme.stylesheet", "site/missing.css",
     "the form carried a field this page cannot place in the config"),
))
async def test_a_candidate_the_parser_refuses_is_never_written(app, site_file, field, value,
                                                             expected):
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "guards", field: value})
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert expected in notice_of(landed), notice_of(landed)
    assert site_file.read_bytes() == before, "the refused candidate was written"


@pytest.mark.asyncio
async def test_a_refused_expert_sheet_names_its_line(app, site_file):
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        await post(http, html, {"_site": "guards",
                                "f.sites.guards.theme.expert": "a.component { color: red; }"})
        landed = await open_page(http)
    assert "Line 1" in notice_of(landed)
    assert site_file.read_bytes() == before


# ==================================================== the group admin: content only, own site (T9)

@pytest.mark.asyncio
async def test_a_group_admin_edits_their_own_site_s_content(app, site_file):
    async with client(app) as http:
        await sign_in_as(http, GROUP_ADMIN)
        html = await open_page(http)
        assert 'value="guards"' in html, "the card a group admin owns is not on their page"
        assert 'value="root"' not in html, "a group admin was shown a site they do not own"
        assert 'value="others"' not in html
        reply = await post(http, html, {"_site": "guards",
                                        "f.sites.guards.branding.title": "Their Wing"})
        assert reply.status_code == 303
    assert sites.load_site_block(site_file.parent)["guards"]["branding"]["title"] == "Their Wing"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", (
    ("f.sites.guards.slug", "guards-wing"),
    ("f.sites.guards.group_role", "Other Token"),
    ("f.sites.guards.enabled", "0"),
    ("f.sites.guards.theme.expert", "--accent: #fff;"),
))
async def test_a_group_admin_is_refused_what_is_not_content(app, site_file, field, value):
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, GROUP_ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "guards", field: value})
        assert reply.status_code == 303
        landed = await open_page(http)
    assert "so nothing was written" in notice_of(landed), notice_of(landed)
    assert site_file.read_bytes() == before


@pytest.mark.asyncio
async def test_a_group_admin_posting_another_site_is_refused(app, site_file):
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, GROUP_ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "others",
                                        "f.sites.others.branding.title": "Taken"})
        assert reply.status_code == 303
        landed = await open_page(http)
    assert "only your own site" in notice_of(landed)
    assert site_file.read_bytes() == before


@pytest.mark.asyncio
async def test_a_post_naming_another_site_is_refused(app, site_file):
    """The card's own site is stated by its hidden field; a field naming a second site is refused."""
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "root",
                                        "f.sites.guards.branding.title": "Smuggled"})
        assert reply.status_code == 303
        landed = await open_page(http)
    assert "names another site" in notice_of(landed)
    assert site_file.read_bytes() == before


@pytest.mark.asyncio
async def test_a_group_admin_of_another_token_sees_only_their_own_site(app):
    """``site.view`` is what admits the page: an identity with no scope manages nothing."""
    async with client(app) as http:
        await sign_in_as(http, "other-admin")
        response = await http.get(page_path())
    assert response.status_code == 200, "the other token's manager sees their own site"
    assert 'value="others"' in response.text


# ============================================================= the CSS validate route

@pytest.mark.asyncio
async def test_the_css_validate_route_refuses_and_measures(app, site_file):
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        csrf = token_of(html)
        bad = await http.post(validate_path(),
                              data={"_csrf_token": csrf,
                                    site_page.SITE_SHEET_FIELD: "a.component { color: red; }"})
        good = await http.post(validate_path(),
                               data={"_csrf_token": csrf,
                                     site_page.SITE_SHEET_FIELD: "--accent: #e8b04b;"})
    assert bad.status_code == 200 and bad.json()["ok"] is False
    assert bad.json()["line"] == 1 and bad.json()["message"]
    assert good.status_code == 200 and good.json()["ok"] is True
    assert good.json()["bytes"] == len(theme_css.expert_rule("--accent: #e8b04b;").encode("utf-8"))
    assert site_file.read_bytes() == before, "the validate route writes nothing"


# ======================================================== §D · the expert sheet's transcription
#: the shipped sheet the page transcribes, read from the presets directory (never restated).
BRASS_FILE = theme_css.PRESET_DIR / theme_css.PRESET_BY_ID[theme_css.DEFAULT_PRESET].filename


def test_the_transcription_is_the_design_s_and_is_the_one_sheet_that_passes():
    """The prefill is a TRANSCRIPTION, not the file: 30 declarations, 35 lines, 932 characters.

    The raw preset file is refused by the validator (`:root {` is a selector and the opening comment
    is a comment), so pasting it would make the operator's first Check fail at line 1. The
    transcription it loads instead is accepted — that difference is the whole point of the copy.
    """
    sheet = site_page.expert_sheet()
    assert (sheet.declarations, sheet.lines, sheet.characters) == (30, 35, 932), (
        f"the transcription counts {sheet.declarations} declarations, {sheet.lines} lines and "
        f"{sheet.characters} characters")
    assert theme_css.validate(sheet.text) is None, "the transcribed sheet is refused"
    raw = theme_css.validate(BRASS_FILE.read_text(encoding="utf-8"))
    assert raw is not None and raw.line == 1, (
        "the raw preset file is accepted, so the transcription is not the difference it exists to be")
    # every declaration of the file is in the transcription, in the file's own order
    declared = [line.strip().split(":")[0] for line in BRASS_FILE.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("--")]
    transcribed = [line.split(":")[0].strip() for line in sheet.text.splitlines() if line.strip()]
    assert transcribed == declared, "the transcription drops or reorders a declaration"


@pytest.mark.asyncio
async def test_the_sheet_assets_state_the_current_asset_version(app):
    """A cached sheet is a FIXED page: an updated rule would not reach the browser that holds it.

    The version is derived from the shipped directory here, never re-declared — a template version
    that drifts from the files would be the staleness it exists to prevent.
    """
    from services.webservice import templating

    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    newest = max(entry.stat().st_mtime_ns for entry in templating.SHELL_STATIC_DIR.iterdir()
                 if entry.is_file())
    for name in ("shell.css", "settings-scope.css", "site-config.js",
                 "vendor/codemirror/codemirror.min.css",
                 "vendor/codemirror/codemirror.min.js",
                 "vendor/codemirror/mode/css/css.min.js"):
        match = re.search(r'(?:href|src)="/static/' + re.escape(name) + r'\?v=([^"]*)"', html)
        assert match, f"the page loads {name} with no asset version"
        assert match.group(1) == str(newest), f"{name} states a stale asset version"


@pytest.mark.asyncio
async def test_a_saved_sheet_round_trips_and_comes_back_edited(app, site_file):
    """The sheet is operator work: Save writes it, and the card says it is no longer the copy."""
    sheet = site_page.expert_sheet()
    stored = sheet.text + "\n--extra: #fff;"
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "root",
                                        "f.sites.root.theme.expert": stored})
        assert reply.status_code == 303
        landed = await open_page(http)
    assert "Saved" in notice_of(landed), notice_of(landed)
    block = sites.load_site_block(site_file.parent) or {}
    assert block["root"]["theme"]["expert"] == stored
    states = re.findall(r"data-sc-x-badge>([^<]*)<.*?data-sc-x-load([^>]*)>", landed, re.S)
    assert states, "the page drew no expert sheet at all"
    edited = [attrs for badge, attrs in states if badge.strip() == "edited"]
    copies = [attrs for badge, attrs in states if badge.strip() != "edited"]
    assert len(edited) == 1, f"the reloaded card's badge says {[b for b, _ in states]}"
    assert "disabled" not in edited[0], (
        "the restore control is disabled on a sheet that no longer is the copy")
    for attrs in copies:
        assert "disabled" in attrs, "a copy's restore control is live"


# ============================================================= the capabilities and the nav entry
def test_the_four_capabilities_are_declared_as_stated():
    from services.webservice import permissions

    assert permissions.roles_for_capability(site_page.SITE_VIEW_CAPABILITY) == ("Admin",)
    assert permissions.scope_grants(site_page.SITE_VIEW_CAPABILITY) is True
    assert permissions.roles_for_capability(site_page.SITE_WRITE_CAPABILITY) == ("Admin",)
    assert permissions.scope_grants(site_page.SITE_WRITE_CAPABILITY) is True
    assert permissions.roles_for_capability(site_page.SITE_STRUCTURE_CAPABILITY) == ("Admin",)
    assert permissions.scope_grants(site_page.SITE_STRUCTURE_CAPABILITY) is False
    assert permissions.roles_for_capability(site_page.SITE_EXPERT_CAPABILITY) == ("Admin",)
    assert permissions.scope_grants(site_page.SITE_EXPERT_CAPABILITY) is False


def test_the_route_and_the_nav_entry_name_site_view():
    declared = site_page.capabilities()
    assert declared[site_page.SITE_CONFIG_PATH] == "site.view"
    assert declared[site_page.SITE_CONFIG_SAVE_PATH] == "site.write"
    assert declared[site_page.SITE_CSS_VALIDATE_PATH] == "settings.write"
    # the mark's three paths carry their declared capability too: the two write doors are ``site.write``
    # and the read-only preview is ``site.view``. A later flip to ``site.view`` or ``PUBLIC`` fails here.
    assert declared[site_page.SITE_AVATAR_UPLOAD_PATH] == "site.write"
    assert declared[site_page.SITE_AVATAR_REMOVE_PATH] == "site.write"
    assert declared[site_page.SITE_MARK_PATH] == "site.view"
    item = site_page.nav_items()[0]
    assert (item.url, item.capability, item.group) == (site_page.SITE_CONFIG_PATH,
                                                      "site.view", "Administration")


# =========================================== §A · the branding card and the site's own chrome in a box

#: the branding leaves the design's A2 table names, by their dotted schema path. ``avatar`` is not
#: here on purpose: it is the upload's own switch (§E), not a leaf this card renders.
A2_LEAVES = ("branding.title", "branding.community_name", "branding.welcome",
             "branding.footer_links", "branding.logo")

#: elements that never close, so the parser must not push them.
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                   "param", "source", "track", "wbr", "circle", "line", "path", "rect", "text",
                   "polyline", "polygon", "use", "stop"})


class _Node:
    """One parsed element: its tag, its attributes and its place in the tree."""

    def __init__(self, tag: str, attrs: "dict | None" = None, parent: "_Node | None" = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.parent = parent
        self.children: "list[_Node]" = []
        #: the character data this element itself holds (not its children's)
        self.text: "list[str]" = []

    def walk(self):
        for child in self.children:
            yield child
            yield from child.walk()

    def classes(self) -> "list[str]":
        return str(self.attrs.get("class") or "").split()


class _Tree(HTMLParser):
    """A minimal HTML tree builder — enough to resolve a descendant selector over RENDERED markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, dict(attrs), self.stack[-1]))

    def handle_data(self, data):
        self.stack[-1].text.append(data)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return


def _parse(html: str) -> _Node:
    tree = _Tree()
    tree.feed(html)
    return tree.root


def _matches(node: _Node, part: str) -> bool:
    if part.startswith("."):
        return part[1:] in node.classes()
    return node.tag == part


def _matches_chain(node: _Node, parts: list[str], index: int) -> bool:
    """Whether *node* matches ``parts[index]`` with every earlier part on an ancestor."""
    if not _matches(node, parts[index]):
        return False
    if index == 0:
        return True
    parent = node.parent
    while parent is not None:
        if _matches_chain(parent, parts, index - 1):
            return True
        parent = parent.parent
    return False


def _resolve(document: _Node, selector: str) -> list[_Node]:
    """Every element a descendant selector of class/tag compounds resolves to, in document order."""
    parts = [part for part in selector.split() if part]
    if not parts:
        return []
    return [node for node in document.walk() if _matches_chain(node, parts, len(parts) - 1)]


def _driven_rows(html: str) -> dict[str, dict[str, str]]:
    """The card's branding rows as the markup carries them: leaf -> its driving attributes."""
    rows = {}
    for node in _parse(html).walk():
        leaf = node.attrs.get("data-sc-leaf")
        if leaf:
            rows[leaf] = {"selector": node.attrs.get("data-sc-drives", ""),
                          "kind": node.attrs.get("data-sc-kind", "")}
    return rows


def _media_block(css: str, query: str) -> str:
    """The body of the ``@media (<query>)`` block, braces balanced."""
    start = css.find("@media (" + query + ")")
    assert start != -1, f"no @media ({query}) block in the sheet"
    open_at = css.find("{", start)
    depth = 0
    for index in range(open_at, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[open_at + 1:index]
    raise AssertionError(f"the @media ({query}) block is not closed")


def _rule(body: str, selector: str) -> str:
    """The declaration block of one rule inside *body* (the first match)."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", body)
    assert match is not None, f"no {selector} rule in the block"
    return match.group(1)


def preview_url(site_key: str) -> str:
    """``<site prefix>/chrome-preview`` for *site_key* — the route's own address."""
    prefix = "" if site_key == "root" else "/" + site_key
    return (prefix or "") + site_pages.CHROME_PREVIEW_PATH


# ---------------------------------------------------------------- the route (pin 1)

@pytest.mark.asyncio
async def test_the_chrome_preview_answers_for_every_live_site_with_its_own_title(app):
    """PIN 1: the preview is the site's own document — one address per LIVE site, its own brand."""
    async with client(app) as http:
        for key, title in (("root", "Community"), ("guards", "Guards"), ("others", "DCSServerBot")):
            reply = await http.get(preview_url(key))
            assert reply.status_code == 200, f"{preview_url(key)} answered {reply.status_code}"
            assert f"<title>{title}</title>" in reply.text, (
                f"{preview_url(key)} does not carry the site's own brand title")
            assert "/static/site.css" in reply.text, "the preview does not serve the site's sheet"


@pytest.mark.asyncio
async def test_the_preview_is_the_hero_only(app):
    """The box shows the bar, the hero and the footer — the overview's own bands are not in it."""
    async with client(app) as http:
        reply = await http.get(preview_url("root"))
    assert reply.status_code == 200
    assert '<section class="hero">' in reply.text
    assert 'class="liveband"' not in reply.text, "the preview drew the overview's live band"
    assert 'class="giant"' not in reply.text
    assert "<main class=\"in\">" not in reply.text


@pytest.mark.asyncio
async def test_a_disabled_site_serves_no_preview(config_dir):
    """PIN 1: the gate is the SITE's own — a switched-off site registers no route, preview included."""
    (config_dir / "site.yaml").write_text(
        SITE_YAML.replace('    slug: guards\n', '    slug: guards\n    enabled: false\n'),
        encoding="utf-8")
    app = build(config_dir, cluster(), users=_users())
    async with client(app) as http:
        assert (await http.get(preview_url("guards"))).status_code == 404
        assert (await http.get(preview_url("root"))).status_code == 200


@pytest.mark.asyncio
async def test_the_preview_serves_the_site_s_own_palette(config_dir):
    """The preview is the site's document: the palette its theme resolves to is the one it loads."""
    (config_dir / "site.yaml").write_text(
        SITE_YAML.replace("      title: Community\n",
                          "      title: Community\n    theme:\n      preset: brass\n"),
        encoding="utf-8")
    app = build(config_dir, cluster(), users=_users())
    async with client(app) as http:
        reply = await http.get(preview_url("root"))
        assert reply.status_code == 200
        assert 'href="/theme.css"' in reply.text, "the preview loads no palette of its own"
        sheet = await http.get("/theme.css")
    assert sheet.status_code == 200 and "--brass" in sheet.text


# ---------------------------------------------------------------- the field→element mapping (pin 3)

def test_every_branding_leaf_the_design_names_has_a_row_line():
    """PIN 3: the mapping is the design's A2 table — every leaf it names has an entry there."""
    mapped = {".".join(row.path) for row in site_page.BRANDING_ROWS}
    missing = [leaf for leaf in A2_LEAVES if leaf not in mapped]
    assert not missing, f"the design names these leaves and the mapping has no entry: {missing}"


def test_the_mapping_is_the_set_of_leaves_the_card_renders():
    """PIN 3: no row without a line, no line without a row — one table, and it is the render list."""
    assert tuple(row.path for row in site_page.BRANDING_ROWS) == site_page.BRANDING_PATHS
    assert len(set(site_page.BRANDING_PATHS)) == len(site_page.BRANDING_PATHS), "a leaf is listed twice"
    for row in site_page.BRANDING_ROWS:
        assert row.line, f"the {row.path} row prints no line"


@pytest.mark.asyncio
async def test_the_card_renders_one_row_and_one_line_per_mapped_leaf(app):
    """PIN 3, read from the RENDERED markup: every mapped leaf has a row, a control and a line.

    The footer links leaf is the one row the page draws OUTSIDE the Branding block: it has its own
    block after Menu & Tiles, and it carries its own `data-sc-leaf`/`data-sc-drives` there — so the
    lines are counted per block and the two together must cover the whole table.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    rows = _driven_rows(html)
    mapped = {".".join(row.path) for row in site_page.BRANDING_ROWS}
    assert set(rows) == mapped, (
        f"rows without a line: {sorted(set(rows) - mapped)}; "
        f"lines without a row: {sorted(mapped - set(rows))}")
    document = _parse(html)
    sections = [node for node in document.walk() if node.attrs.get("data-sc-block") == "branding"]
    assert sections, "the card draws no Branding block"
    for section in sections:
        lines = [node for node in section.walk() if "sc-drives" in node.classes()]
        assert len(lines) == len(mapped) - 1, (
            f"the {len(mapped) - 1} branded row(s) of the block print {len(lines)} drive line(s)")
        assert len([node for node in section.walk() if "sc-brand-row" in node.classes()]) == len(mapped) - 1
        assert not [node for node in section.walk() if "sc-pv-box" in node.classes()], (
            "the Branding block draws a box of its own — the page draws the one preview")
    for leaf in mapped:
        assert f"f.sites.root.{leaf}" in html, f"the {leaf} row renders no control"


def test_the_welcome_row_names_the_hero_paragraph_and_is_not_flagged():
    """``branding.welcome`` is drawn now: the row names the hero's paragraph, with no stale flag."""
    welcome = next(row for row in site_page.BRANDING_ROWS if row.path == ("branding", "welcome"))
    assert welcome.selector == "", "the welcome is server-rendered, so nothing may be bound"
    assert not welcome.flag, "the welcome is drawn, so it carries no 'not drawn' flag"
    assert "hero" in welcome.line.lower() and "paragraph" in welcome.line.lower()
    assert "%(community)s" in welcome.line


def test_the_card_draws_the_placement_row_with_the_stored_side(app):
    """The Branding card carries the two-pill placement row, the site's own value preselected."""
    import asyncio

    async def run():
        async with client(app) as http:
            await sign_in_as(http, ADMIN)
            return await open_page(http)

    html = asyncio.run(run())
    assert 'class="sc-markside"' in html, "the card draws no placement row"
    assert 'data-sc-mark-side="right"' in html, "the stored side is not on the row"
    for ident in sites.MARK_SIDES:
        assert f'name="f.sites.root.branding.mark_side" value="{ident}"' in html, ident
    checked = re.search(r'name="f\.sites\.root\.branding\.mark_side" value="([^"]*)"[^>]*checked',
                        html)
    assert checked and checked.group(1) == "right", "the default side is not preselected"


@pytest.mark.asyncio
async def test_a_save_writes_the_mark_side(app, site_file):
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "root",
                                        "f.sites.root.branding.mark_side": "left"})
        assert reply.status_code == 303, reply.text[:400]
    assert "mark_side: left" in site_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_the_preview_draws_the_mark_on_the_stored_side(config_dir):
    """The box's own document carries the placement class, so a re-render shows the chosen side."""
    (config_dir / sites.SITE_FILE).write_text(
        SITE_YAML.replace("      title: Community",
                          "      title: Community\n      logo: config/site/crest.png\n"
                          "      mark_side: left"), encoding="utf-8")
    app = build(config_dir, cluster(), users=_users())
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        preview = await http.get(site_pages.site_url(
            types.SimpleNamespace(prefix=""), site_pages.CHROME_PREVIEW_PATH))
    assert preview.status_code == 200, preview.text[:400]
    assert 'class="in has-mark mark-left"' in preview.text
    assert '<div class="hero-mark"><img src="/logo.png" alt=""></div>' in preview.text


# ---------------------------------------------------------------- the binding against the markup (pin 2)

@pytest.mark.asyncio
async def test_the_binding_resolves_the_selectors_against_the_rendered_preview(app):
    """PIN 2: parse BOTH sides — the selector the script resolves and the element the template draws.

    The console page's markup is the selector's source (the script reads ``data-sc-drives`` off the
    row) and the site's own served preview document is the element's source. A harness that built its
    own DOM would repair either side of this and prove nothing.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        console = await open_page(http)
        previews = {key: await http.get(preview_url(key)) for key in ("root", "guards", "others")}
    rows = _driven_rows(console)
    bound = {leaf: row["selector"] for leaf, row in rows.items() if row["selector"]}
    assert set(bound) == {"branding.title", "branding.community_name",
                          "branding.footer_links"}, f"the bound rows are {sorted(bound)}"
    for key, reply in previews.items():
        assert reply.status_code == 200
        document = _parse(reply.text)
        for leaf, selector in bound.items():
            found = _resolve(document, selector)
            assert len(found) == 1, (
                f"{leaf}: the row drives '{selector}' and the {key} preview document carries "
                f"{len(found)} matching element(s)")


def test_the_binding_script_reads_its_selectors_from_the_markup():
    """PIN 2: the script resolves no selector of its own — the row's attribute is the one source."""
    script = (REPO_ROOT / "services/webservice/static" / "site-config.js").read_text("utf-8")
    assert "data-sc-drives" in script, "the binding keeps a second copy of the selectors"
    assert "data-sc-preview" in script, "the binding does not find the card's preview box"
    assert "contentDocument" in script, "the binding does not reach the preview's own document"


SITE_CONFIG_JS = REPO_ROOT / "services" / "webservice" / "static" / "site-config.js"
SITE_DRAFT_HARNESS = Path(__file__).resolve().parent / "site_draft_harness.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot run")


@requires_node
def test_a_mark_side_press_posts_its_draft_without_reloading_the_preview():
    """The placement pills paint the hero's class themselves, so the pane is not reloaded for them.

    Pinned beside its control: an edit the script CANNOT paint (a brand text) still reloads the pane,
    so a later tidy-up cannot silence every reload instead of only the painted one.
    """
    done = subprocess.run(["node", str(SITE_DRAFT_HARNESS), str(SITE_CONFIG_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert seen["mark_side"] == {"posts": 1, "reloads": 0, "src_changed": False}, (
        "a mark-placement press reloaded a pane it had already painted")
    assert seen["palette_press"] == {"posts": 1, "reloads": 0, "src_changed": False}, (
        "a palette press re-rendered the pane, blanking the box and flickering its branding images")
    assert seen["text_field"] == {"posts": 1, "reloads": 1, "src_changed": True}, (
        "an edit the script cannot paint no longer reloads the preview")
    assert seen["server_drawn_row_typing"] == {"posts": 1, "reloads": 1, "src_changed": True}, (
        "typing a sentence the server draws (branding.welcome) does not reach the box until blur")
    assert seen["server_drawn_row_blur"] == {"posts": 1, "reloads": 1, "src_changed": True}, (
        "leaving a field the box already follows redraws it a second time")
    assert seen["section_toggle"] == {"posts": 1, "reloads": 1, "src_changed": True}, (
        "switching a section off no longer repaints the box")


@pytest.mark.asyncio
async def test_the_preview_hero_is_the_overview_s_own_hero(app):
    """No second owner of the hero: the preview's hero markup is the overview's, character for character."""
    async with client(app) as http:
        overview = await http.get(site_pages.NAV_PATHS["overview"] or "/")
        preview = await http.get(preview_url("root"))
    assert overview.status_code == 200 and preview.status_code == 200
    pattern = re.compile(r'<section class="hero">.*?</section>', re.S)
    assert pattern.search(overview.text).group(0) == pattern.search(preview.text).group(0)


# ---------------------------------------------------- the footer's fixed attribution (no control)

ATTRIBUTION = "Powered by DCSServerBot"


def footer_text_of(markup: str) -> str:
    """Every character the document's footer prints, tags removed."""
    start = markup.index("<footer")
    chunk = markup[start:markup.index("</footer>", start)]
    return re.sub(r"<[^>]+>", "", chunk)


@pytest.mark.asyncio
async def test_the_card_offers_no_copyright_control(app):
    """The retired control is gone from the markup: no field name, no leaf, no ``.foot-text`` driver."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    assert "branding.copyright" not in html, "the retired field is still rendered"
    assert 'data-sc-leaf="branding.copyright"' not in html
    assert "foot-text" not in html, "a row still drives the footer's fixed line"
    assert "copyright" not in {".".join(row.path) for row in site_page.BRANDING_ROWS}


@pytest.mark.asyncio
async def test_the_demo_pane_renders_the_fixed_attribution(config_dir):
    """The settings card's own preview carries the attribution, byte for byte, and nothing else."""
    app = build(config_dir, cluster(), users=_users())
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        preview = await http.get(preview_url("root"))
    assert preview.status_code == 200, preview.text[:400]
    assert ATTRIBUTION in footer_text_of(preview.text)
    assert "©" not in footer_text_of(preview.text)


@pytest.mark.asyncio
async def test_a_configured_copyright_renders_nowhere_on_the_site_or_its_preview(config_dir):
    """A site file that still sets ``branding.copyright`` loads, serves, and the value is in no byte
    of either document."""
    (config_dir / sites.SITE_FILE).write_text(
        SITE_YAML.replace("    branding:\n      title: Community\n",
                          "    branding:\n      title: Community\n      copyright: ACME Corp\n"),
        encoding="utf-8")
    app = build(config_dir, cluster(), users=_users())
    async with client(app) as http:
        site_page_reply = await http.get("/")
        preview = await http.get(preview_url("root"))
    assert site_page_reply.status_code == 200, site_page_reply.text[:400]
    assert site_page_reply.status_code == 200 and preview.status_code == 200
    assert "ACME Corp" not in site_page_reply.text, "the site's copyright reached the page"
    assert "ACME Corp" not in preview.text, "the site's copyright reached the preview"
    assert ATTRIBUTION in footer_text_of(site_page_reply.text)
    assert ATTRIBUTION in footer_text_of(preview.text)


@pytest.mark.asyncio
async def test_a_save_round_trip_writes_no_copyright_key(app, site_file):
    """Saving the card as it renders lands the field it posted and writes no ``copyright`` key."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "root",
                                        "f.sites.root.branding.title": "Community Two"})
        assert reply.status_code == 303, reply.text[:400]
    written = site_file.read_text(encoding="utf-8")
    assert "Community Two" in written, "the posted title did not land"
    assert "copyright" not in written, "a save wrote the retired key"


# ---------------------------------------------------------------- the page's widths (§11.3)

def test_the_page_goes_above_the_blocks_at_1130_as_a_flex_column():
    """§11.3: under 1 131 px the box goes FIRST, and the page becomes a FLEX column, not a grid.

    A sticky child of a one-column grid is confined to its own row and cannot travel, so the row
    stack is a flex column — and every block carries the strip's height as its scroll margin, or a
    jump to a block lands it behind the pinned strip.
    """
    css = (REPO_ROOT / "services/webservice/static" / "settings-scope.css").read_text("utf-8")
    body = _media_block(css, "max-width: 1130px")
    assert re.search(r"display:\s*flex", _rule(body, ".sc-page")), (
        ".sc-page is still a grid at 1 130 — a sticky child cannot travel in a grid row")
    assert re.search(r"flex-direction:\s*column", _rule(body, ".sc-page"))
    assert re.search(r"order:\s*-1", _rule(body, ".sc-site")), (
        "the narrow page does not put the box above the blocks")
    assert "367px" in body, "no scroll margin for the 359 px strip"
    for target in (".sc-expert", ".sc-x", ".sc-wiz"):
        assert target in body, f"the strip covers a jump to {target}"
    phone = _media_block(css, "max-width: 620px")
    assert "272px" in phone, "no scroll margin for the 264 px strip"


def test_the_wide_page_is_the_design_s_two_columns_and_640_measures():
    """The design's own measures: the editing column beside a 640 × 400 box."""
    css = (REPO_ROOT / "services/webservice/static" / "settings-scope.css").read_text("utf-8")
    page = _rule(css, ".sc-page")
    assert re.search(r"minmax\(0,\s*1fr\)\s+640px", page), f".sc-page is '{page.strip()}'"
    assert re.search(r"max-width:\s*420px", css), "no 420 px control measure in the column"
    assert re.search(r"\.sc-pv-box\s*\{[^}]*height:\s*400px", css), "the box is not 400 px tall"
    narrow = _media_block(css, "max-width: 1359px")
    assert re.search(r"minmax\(0,\s*1fr\)\s+440px", _rule(narrow, ".sc-page")), (
        "the box does not give 200 px back to the column below 1 360")


# ================================================ §B · the character card and the two lists it writes

#: the design's B1 table, section by section, in ``sites.SECTION_IDS`` order.
B1_SECTIONS: dict[str, tuple[str, ...]] = {
    "pvp": ("community", "standings", "aircraft", "when_we_fly", "servers"),
    "pve": ("community", "season", "aircraft", "when_we_fly", "squadrons", "servers"),
    "pvpve": ("community", "season", "standings", "aircraft", "when_we_fly", "squadrons", "servers"),
}

#: the design's B1 table, metric by metric, in the order the table states them.
B1_METRICS: dict[str, tuple[str, ...]] = {
    "pvp": ("playtime", "air_targets", "kd_ratio", "pvp_kd_ratio"),
    "pve": ("playtime", "air_targets", "ships", "air_defence", "ground_targets", "kd_ratio"),
    "pvpve": ("playtime", "air_targets", "ships", "air_defence", "ground_targets", "kd_ratio",
              "pvp_kd_ratio"),
}

#: the metrics the design's table WITHDRAWS per character — the complement of ``B1_METRICS``.
B1_WITHDRAWN: dict[str, tuple[str, ...]] = {
    "pvp": ("ships", "air_defence", "ground_targets"),
    "pve": ("pvp_kd_ratio",),
    "pvpve": (),
}


def test_the_character_table_is_the_design_s_b1_table():
    """PIN 3: the code-side table IS the design's B1 table — three characters, seven and seven."""
    assert set(site_page.CHARACTER_SETS) == set(sites.CHARACTERS), (
        f"the table is keyed by {sorted(site_page.CHARACTER_SETS)}, the model names "
        f"{sorted(sites.CHARACTERS)}")
    assert len(sites.SECTION_IDS) == 7 and len(sites.LEADERBOARD_METRICS) == 7
    for ident in sites.CHARACTERS:
        spec = site_page.CHARACTER_SETS[ident]
        assert tuple(spec.sections) == B1_SECTIONS[ident], (
            f"{ident}: the table's sections are {spec.sections}, the design says {B1_SECTIONS[ident]}")
        assert tuple(spec.metrics) == B1_METRICS[ident], (
            f"{ident}: the table's metrics are {spec.metrics}, the design says {B1_METRICS[ident]}")
        assert tuple(m for m in sites.LEADERBOARD_METRICS if m not in spec.metrics) == \
            B1_WITHDRAWN[ident], f"{ident}: the withdrawn metrics are not the design's"
        assert set(spec.sections) <= set(sites.SECTION_IDS), (
            f"{ident}: {sorted(set(spec.sections) - set(sites.SECTION_IDS))} is not a section id")
        assert set(spec.metrics) <= set(sites.LEADERBOARD_METRICS), (
            f"{ident}: {sorted(set(spec.metrics) - set(sites.LEADERBOARD_METRICS))} is not a metric")
        assert spec.what, f"{ident} states no sentence of its own"
        if ident == sites.DEFAULT_CHARACTER:
            assert not spec.why_sections and not spec.why_metrics, (
                "the default character withdraws nothing, so it prints no 'off' note")
        else:
            assert spec.why_sections and spec.why_metrics, (
                f"{ident} withdraws something and states no reason for it")


def test_the_character_table_reads_the_player_page_s_own_symbols():
    """PIN 3: the readout order and the combat layout come from ``site_player``, never retyped.

    Identity (``is``) is the pin: a table that copied the tuples would carry equal values, and a
    copy drifts. The player page is the only owner of what it draws.
    """
    from services.webservice.pages import site_player

    assert site_page.READOUT_ORDER is site_player.READOUT_ORDER
    assert site_page.COMBAT_LAYOUT is site_player.COMBAT_LAYOUT
    for ident in sites.CHARACTERS:
        spec = site_page.CHARACTER_SETS[ident]
        assert spec.readout is site_player.READOUT_ORDER[ident], (
            f"{ident}: the card states a readout order the player page does not have")
        assert spec.combat is site_player.COMBAT_LAYOUT[ident], (
            f"{ident}: the card states a combat layout the player page does not have")


@pytest.mark.asyncio
async def test_the_card_renders_the_stored_character_s_sentence_and_no_other(app):
    """The one B4 sentence on the page is the stored character's — the other two are not rendered.

    The script's payload carries all three (a press must not reload the page), so this reads the
    RENDERED element, not the page source.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    default = sites.DEFAULT_CHARACTER
    assert site_page.CHARACTER_SETS[default].what in html
    match = re.search(r'<p class="sc-charwhat"[^>]*>(.*?)</p>', html, re.S)
    assert match, "the character card renders no sentence of its own"
    assert match.group(1).strip() == site_page.CHARACTER_SETS[default].what
    for ident in sites.CHARACTERS:
        if ident == default:
            continue
        assert site_page.CHARACTER_SETS[ident].what not in match.group(1)


@pytest.mark.asyncio
async def test_without_scripts_the_card_saves_the_character_and_keeps_the_stored_lists(config_dir):
    """PIN 4: with scripts off the pills still post the character, and the lists keep the file's values.

    The markup is PARSED: a substring pin would match the page's own CSS class names.
    """
    from test_webui_write_pause import build, client, sign_in

    (config_dir / "site.yaml").write_text(
        SITE_YAML.replace('    slug: ""\n',
                          '    slug: ""\n'
                          '    sections:\n      season: false\n'
                          '    metrics:\n      - playtime\n      - kd_ratio\n'),
        encoding="utf-8")
    app = build(config_dir, cluster(), users=_users())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
    document = _parse(html)
    forms = [node for node in document.walk() if node.tag == "form"]
    root_form = next(
        form for form in forms
        if any(child.attrs.get("name") == site_page.SITE_KEY_FIELD
               and child.attrs.get("value") == "root" for child in form.walk()))

    radios = [node for node in root_form.walk()
              if node.tag == "input" and node.attrs.get("type") == "radio"
              and node.attrs.get("name") == "f.sites.root.character"]
    assert [node.attrs.get("value") for node in radios] == list(sites.CHARACTERS), (
        "the character pills are not radios inside the card's form")
    checked = [node.attrs.get("value") for node in radios if "checked" in node.attrs]
    assert checked == [sites.DEFAULT_CHARACTER], f"the stored character is not the selected pill: {checked}"

    sections = [node for node in root_form.walk() if node.attrs.get("data-sc-section")]
    assert len(sections) == len(sites.SECTION_IDS)
    assert [node.attrs.get("data-sc-section") for node in sections if "checked" in node.attrs] == \
        [ident for ident in sites.SECTION_IDS if ident != "season"], (
        "the rendered switches do not carry the stored section values")
    metrics = [node for node in root_form.walk() if node.attrs.get("data-sc-metric")]
    assert len(metrics) == len(sites.LEADERBOARD_METRICS)
    assert [node.attrs.get("data-sc-metric") for node in metrics if "checked" in node.attrs] == \
        ["playtime", "kd_ratio"], "the rendered switches do not carry the stored metric values"

    note = re.search(r'<p class="sc-noscript"[^>]*>(.*?)</p>', html, re.S)
    assert note, "the card does not state what a press does with scripts off"
    assert "stored" in note.group(1), (
        f"the scripts-off sentence does not say the lists keep their stored values: {note.group(1)!r}")
    assert "JavaScript" in note.group(1) or "scripts" in note.group(1)


# ============================================ §C · the menu and the tiles — a closed set, ordered by drag

#: the two closed sets, by the leaf they post and the vocabulary they must always list in full.
CLOSED_SETS = (("nav", sites.NAV_PAGE_IDS), ("tiles", sites.TILE_IDS))


def _blocks(html: str) -> dict[str, "_Node"]:
    """The ROOT site's list blocks, keyed by the leaf they draw.

    The page draws one card per site and every card carries the same three blocks, so the first
    match is the root site's — the card the save tests write.
    """
    out: dict[str, _Node] = {}
    for node in _parse(html).walk():
        name = node.attrs.get("data-sc-set") or node.attrs.get("data-sc-leaf")
        if name in ("nav", "tiles", "branding.footer_links"):
            out.setdefault(name, node)
    return out


def _rows_of(block: "_Node") -> list["_Node"]:
    """The block's own rows, in DOM order."""
    return [node for node in block.walk() if "data-reorder-row" in node.attrs]


def _row_ids(block: "_Node") -> list[str]:
    return [node.attrs.get("data-reorder-id") or "" for node in _rows_of(block)]


def _dump(node: "_Node") -> str:
    """*node* and its descendants as tag names and attribute values — no CSS, no comments."""
    return "".join(f"<{child.tag} {child.attrs}>" + "".join(child.text)
                   for child in [node, *node.walk()])


def _root_form(html: str) -> "_Node":
    """The root site's own card form."""
    return next(node for node in _parse(html).walk() if node.tag == "form"
                and any(child.attrs.get("name") == "_site"
                        and child.attrs.get("value") == "root" for child in node.walk()))


@pytest.mark.asyncio
async def test_the_two_closed_sets_list_every_id_and_offer_no_add_and_no_remove(app):
    """§C: every id of the vocabulary is always drawn, so neither closed set has an Add or a ✕."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    blocks = _blocks(html)
    form = _root_form(html)
    for leaf, vocabulary in CLOSED_SETS:
        block = blocks[leaf]
        assert _row_ids(block) == list(vocabulary), (
            f"the {leaf} block draws {_row_ids(block)}, the vocabulary is {list(vocabulary)}")
        assert not [node for node in block.walk() if "data-cfg-add" in node.attrs], (
            f"the {leaf} block draws an Add control")
        assert not [node for node in block.walk() if "data-cfg-remove" in node.attrs], (
            f"the {leaf} block draws a remove control")
        assert not [node for node in block.walk() if node.attrs.get("data-cfg-seq")], (
            f"the {leaf} block still renders the generated sequence")
        # ONE checkbox per row under the block's own name, and one empty value stating the block
        values = [node.attrs.get("value") for node in form.walk()
                  if node.tag == "input" and node.attrs.get("name") == f"f.sites.root.{leaf}"]
        assert values and values[0] == "", (
            f"the {leaf} block posts no empty value to state itself: {values}")
        assert values[1:] == list(vocabulary), (
            f"the {leaf} block posts {values[1:]}, the vocabulary is {list(vocabulary)}")


@pytest.mark.asyncio
async def test_the_footer_links_block_keeps_its_entries_and_its_add(app):
    """§C: the one block that takes entries keeps the generated row, the Add and the ✕."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    block = _blocks(html)["branding.footer_links"]
    assert [node for node in block.walk() if "data-cfg-add" in node.attrs], (
        "the footer links block draws no Add")
    assert [node for node in block.walk() if "data-cfg-remove" in node.attrs], (
        "the footer links block draws no remove control")
    seq = [node for node in block.walk() if node.attrs.get("data-cfg-seq")]
    assert seq and seq[0].attrs["data-cfg-seq"] == "sites.root.branding.footer_links", (
        "the footer links rows left the generated sequence's own contract")
    assert [node for node in block.walk() if "sc-empty" in node.classes()], (
        "an empty footer links block draws no empty state")
    assert [node for node in block.walk() if "data-cfg-row" in node.attrs], (
        "the footer links block draws no row")
    assert "No footer links yet." in _dump(block), (
        "the empty footer links state prints no sentence")


@pytest.mark.asyncio
async def test_the_closed_sets_draw_no_arrow_control(app):
    """PIN 3: the order control is the grip — no ↑/↓ and no move-pair column in the block.

    Parsed, never substring-matched: the sheet's own CSS and the page's comments carry those glyphs.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    for leaf, _vocabulary in CLOSED_SETS:
        markup = _dump(_blocks(html)[leaf])
        for glyph in ("\u2191", "\u2193", "\u25b2", "\u25bc"):
            assert glyph not in markup, f"the {leaf} block renders a {glyph!r} control"
        assert "move" not in markup.lower(), f"the {leaf} block renders a move control"


def test_the_order_number_is_a_css_counter_that_renumbers_with_the_rows():
    """PIN 4: the number is the counter's, so a drop renumbers the rows with no script."""
    css = (REPO_ROOT / "services/webservice/static" / "settings-scope.css").read_text("utf-8")
    assert re.search(r"\.sc-list\s*\{[^}]*counter-reset:\s*sc-item", css), (
        "the row list resets no counter")
    assert re.search(r"\.sc-row\s*\{[^}]*counter-increment:\s*sc-item", css), (
        "the row does not increment the counter")
    assert re.search(r"\.sc-row\s+\.ord::before\s*\{[^}]*content:\s*counter\(sc-item\)", css), (
        "the order number is not drawn by the counter")
    assert not re.search(r"\.ord[^{]*\{[^}]*content:\s*['\"]", css), (
        "the order number is a fixed string, not the counter")


def test_every_tile_id_has_a_label_and_the_nav_labels_are_the_bar_s_own():
    """A label table is not a second vocabulary: the ids are the model's, the words are the bar's."""
    assert set(site_page.TILE_LABELS) == set(sites.TILE_IDS)
    assert site_page.NAV_LABEL_SOURCE is site_pages.NAV_LABELS, (
        "the card keeps a second copy of the menu's own labels")
    for ident in sites.NAV_PAGE_IDS:
        assert ident in site_pages.NAV_LABELS


def test_a_hidden_id_is_drawn_after_the_shown_ones_in_vocabulary_order():
    """PIN 5: the sequence holds the SHOWN ids, so a hidden one has no stored position."""
    rows = site_page.list_rows(("players", "overview"), sites.NAV_PAGE_IDS, {})
    assert [row["id"] for row in rows] == ["players", "overview", "leaderboard", "squadrons"]
    assert [row["on"] for row in rows] == [True, True, False, False]
    duplicates = site_page.list_rows(("players", "players"), sites.NAV_PAGE_IDS, {})
    assert [row["id"] for row in duplicates].count("players") == 1, (
        "a repeated id is drawn twice")


#: the site file a switched-off row is pinned against: nav names two ids in its own order, tiles
#: names two of five, so each block has hidden rows to place.
OFF_SITE_YAML = """\
sites:
  root:
    slug: ""
    nav:
      - players
      - overview
    tiles:
      - online_now
      - active_week
    branding:
      title: Community
  guards:
    group_role: "5th Guards"
    slug: guards
  others:
    group_role: "Other Token"
    slug: others
"""


def _two_site_app(config_dir, site_yaml: str):
    """The page served from a site file that states partial nav/tiles lists."""
    from test_webui_write_pause import build

    (config_dir / sites.SITE_FILE).write_text(site_yaml, encoding="utf-8")
    return build(config_dir, cluster(), users=_users())


@pytest.mark.asyncio
async def test_a_switched_off_row_stays_drawn_after_the_shown_ones_and_is_struck(config_dir):
    """PIN 5: a hidden id is drawn last, dimmed and struck — and the block still lists every id."""
    app = _two_site_app(config_dir, OFF_SITE_YAML)
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    blocks = _blocks(html)
    for leaf, vocabulary, first in (("nav", sites.NAV_PAGE_IDS, ["players", "overview"]),
                                    ("tiles", sites.TILE_IDS, ["online_now", "active_week"])):
        assert _row_ids(blocks[leaf]) == first + [i for i in vocabulary if i not in first], (
            f"the {leaf} block draws {_row_ids(blocks[leaf])}")
        rows = _rows_of(blocks[leaf])
        parked = [row.attrs.get("data-reorder-id") for row in rows if "parked" in row.classes()]
        assert parked == [i for i in vocabulary if i not in first], (
            f"the {leaf} block strikes {parked}")
        for row in rows:
            if row.attrs.get("data-reorder-id") in first:
                assert "parked" not in row.classes(), (
                    f"the shown {leaf} row {row.attrs.get('data-reorder-id')} is struck")
            checked = [node for node in row.walk()
                       if node.tag == "input" and "checked" in node.attrs]
            assert bool(checked) == (row.attrs.get("data-reorder-id") in first), (
                f"the {leaf} row {row.attrs.get('data-reorder-id')} carries the other state")


@pytest.mark.asyncio
async def test_the_all_off_state_still_renders_every_row_and_the_sentence(config_dir):
    """PIN 7: every id switched off still draws all its rows, and the block's all-off sentence."""
    app = _two_site_app(config_dir, OFF_SITE_YAML)
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "root",
                                        "f.sites.root.nav": "", "f.sites.root.tiles": ""})
        assert reply.status_code == 303, reply.text[:300]
        html = await open_page(http)

    blocks = _blocks(html)
    for leaf, vocabulary in CLOSED_SETS:
        assert _row_ids(blocks[leaf]) == list(vocabulary), (
            f"an all-off {leaf} block draws {_row_ids(blocks[leaf])}")
        assert all("parked" in row.classes() for row in _rows_of(blocks[leaf])), (
            f"an all-off {leaf} block strikes none of its rows")
        assert "switched off" in _dump(blocks[leaf]).lower(), (
            f"the {leaf} block prints no all-off sentence")
    block = sites.load_site_block(config_dir) or {}
    root = block.get("root") or {}
    assert root.get("nav") == [], f"the file carries nav: {root.get('nav')!r}"
    assert root.get("tiles") == [], f"the file carries tiles: {root.get('tiles')!r}"


@pytest.mark.asyncio
async def test_a_reordered_list_saves_in_the_posted_order(app, site_file):
    """THE SAVE-SHAPE PROOF: the repeated same-name values ARE the sequence, in DOM order.

    The metric idiom, applied to `nav`: one checkbox per id under one name, so the order of the
    posted values is the order of the list. Nothing is renumbered and no index is posted.
    """
    wanted = ["squadrons", "players", "overview", "leaderboard"]
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "root", "f.sites.root.nav": wanted})
        assert reply.status_code == 303, reply.text[:300]
        html = await open_page(http)

    catalog = sites.load_sites({"sites": sites.load_site_block(site_file.parent)},
                               config_dir=site_file.parent)
    root = next(site for site in catalog.sites if site.key == "root")
    assert list(root.nav) == wanted, f"{sites.SITE_FILE} carries {list(root.nav)}"
    assert _row_ids(_blocks(html)["nav"]) == wanted, (
        "the page draws an order the file no longer carries")
    assert "notice bad" not in html, notice_of(html)


@pytest.mark.asyncio
async def test_switching_the_whole_set_off_writes_an_empty_list_on_a_site_that_never_declared_one(
        app, site_file):
    """PIN 7's other half: the all-off post is honoured even where the key is ABSENT.

    The shared form path never CREATES a key because its control was shown, which would silently
    undo an all-off on a site that declares neither list — the post says the list is empty, so it is
    written as one.
    """
    block = sites.load_site_block(site_file.parent) or {}
    assert "nav" not in block["root"] and "tiles" not in block["root"]
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "root", "f.sites.root.nav": "",
                                        "f.sites.root.tiles": ""})
        assert reply.status_code == 303, reply.text[:300]
    block = sites.load_site_block(site_file.parent) or {}
    assert block["root"]["nav"] == [], f"the file carries nav: {block['root'].get('nav')!r}"
    assert block["root"]["tiles"] == [], f"the file carries tiles: {block['root'].get('tiles')!r}"


@pytest.mark.asyncio
async def test_the_blocks_run_the_shared_drag_in_its_local_mode(app):
    """§C3: the list opts into the shared script WITHOUT a URL — a drop moves the row, Save writes it."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    blocks = _blocks(html)
    for leaf, _vocabulary in CLOSED_SETS:
        lists = [node for node in blocks[leaf].walk() if "data-reorder" in node.attrs]
        assert len(lists) == 1, f"the {leaf} block draws {len(lists)} reorderable containers"
        container = lists[0]
        assert "data-reorder-url" not in container.attrs, (
            f"the {leaf} list carries a write URL, so a drop would post")
        assert "data-reorder-body" in container.attrs, (
            f"the {leaf} list does not mark its row holder")
    assert any("reorder.js" in (src or "") for src in
               re.findall(r'<script src="([^"]*)"', html)), (
        "the page renders the rows' grips without the script that moves them")


# ================================================================= §E · the mark slot on the card
#
# The slot is the branding card's first row: ONE of four states drawn at a time, each with its own
# sentence and its own controls. The uploaded state draws the three files the upload wrote — at their
# REAL pixels, from their own addresses — because the point is that the operator meets the 32 px file
# he will see in a chip.

#: the site file these pins use for the "branding.logo is set" state: the same three sites, one of
#: them declaring a raster logo.
LOGO_SITE_YAML = SITE_YAML.replace(
    "    branding:\n      title: Guards\n",
    "    branding:\n      title: Guards\n      logo: brand/guards.png\n")

#: the sentences the four states are told apart by — read off the rendered page, never off a constant.
SHIPPED_SENTENCE = "branding.logo is not set"
LOGO_SENTENCE = "branding.logo names the file the site draws today"
ORDER_SENTENCE = "the shipped mark"
CONFIRM_SENTENCE = "Remove the uploaded mark? The site falls back to branding.logo."


def _text(node: "_Node") -> str:
    """Every character this element holds, its descendants' included, whitespace collapsed."""
    parts = list(node.text)
    for child in node.walk():
        parts.extend(child.text)
    return " ".join(" ".join(parts).split())


def _cards(html: str) -> "dict[str, _Node]":
    """The page's cards by the site key each one's own form posts."""
    cards: "dict[str, _Node]" = {}
    for node in _parse(html).walk():
        if "sc-card" not in node.classes():
            continue
        key = next((child.attrs.get("value", "") for child in node.walk()
                    if child.tag == "input" and child.attrs.get("name") == "_site"), "")
        if key:
            cards[key] = node
    return cards


def _slot(card: "_Node") -> "_Node":
    """The card's mark slot — the one element carrying ``data-sc-mark``."""
    slots = [node for node in card.walk() if "data-sc-mark" in node.attrs]
    assert len(slots) == 1, f"the card draws {len(slots)} mark slots"
    return slots[0]


def _slot_state(html: str, key: str) -> str:
    return _slot(_cards(html)[key]).attrs.get("data-sc-mark", "")


def _in_slot(slot: "_Node", predicate) -> "list[_Node]":
    return [node for node in slot.walk() if predicate(node)]


def mount_url(path: str, **params) -> str:
    from services.webservice import mount

    return mount.url(path.format(**params))


def mark_upload_path() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_AVATAR_UPLOAD_PATH)


def mark_remove_path() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_AVATAR_REMOVE_PATH)


def site_assets(config_dir) -> Path:
    return config_dir / sites.ASSET_DIR


async def upload_mark(http, html: str, key: str, payload: bytes) -> None:
    """Upload through the page's own token and the card's key, then land on the page again."""
    reply = await http.post(mark_upload_path(),
                            data={"_csrf_token": token_of(html), "_site": key},
                            files={site_page.SITE_AVATAR_FIELD: ("mark.png", payload, "image/png")})
    assert reply.status_code == 303, f"the upload answered {reply.status_code}: {reply.text[:300]}"


def mark_png(size: int = 400) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (size, size), (200, 30, 40, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_the_slot_is_the_branding_card_s_first_row_and_names_the_resolution_order(app):
    """STATE 1 — nothing uploaded and no ``branding.logo``: the shipped mark, and the true order."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    card = _cards(html)["guards"]
    slot = _slot(card)
    assert slot.attrs["data-sc-mark"] == "shipped"
    sections = [node for node in card.walk() if node.attrs.get("data-sc-block") == "branding"]
    assert len(sections) == 1, f"the card draws {len(sections)} branding blocks"
    assert slot in list(sections[0].walk()), "the slot is not inside the Branding block"
    assert [node for node in card.walk() if "sc-pv-box" in node.classes()], (
        "the page draws no preview box beside the blocks")
    assert not [node for node in sections[0].walk() if "sc-pv-box" in node.classes()], (
        "the Branding block draws its own box: the page's one preview is the page's")
    heading = next(node for node in sections[0].children if "sc-sechd" in node.classes())
    assert heading is sections[0].children[0], "the block does not open with its heading row"
    content = [node for node in sections[0].children if "sc-sechd" not in node.classes()]
    first_row = next(node for node in content if node.tag == "div")
    assert first_row is slot, f"the slot is not the block's first row but {first_row.tag}"
    images = [node for node in slot.walk() if node.tag == "img"]
    assert images[0].attrs.get("src") == "/static/logo-mark.png", (
        f"the shipped state draws '{images[0].attrs.get('src')}'")
    text = _text(slot)
    assert SHIPPED_SENTENCE in text, text
    assert "uploaded mark" in text and "branding.logo" in text and ORDER_SENTENCE in text, text
    assert "until the resolver" not in text, "the retired §E1 caveat is still on the card"
    buttons = [node for node in slot.walk() if node.tag == "button"]
    upload = [b for b in buttons if "Upload an image" in _text(b)]
    remove = [b for b in buttons if "disabled" in b.attrs]
    assert len(upload) == 1, f"the shipped state offers {len(upload)} upload controls"
    assert len(remove) == 1 and "Remove" in _text(remove[0]), (
        "the shipped state does not draw a disabled Remove")


@pytest.mark.asyncio
async def test_a_declared_branding_logo_is_drawn_dashed_and_captioned(config_dir):
    """STATE 2 — ``branding.logo`` set, nothing uploaded: that file, dashed, and it is replaced."""
    app = _two_site_app(config_dir, LOGO_SITE_YAML)
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    slot = _slot(_cards(html)["guards"])
    assert slot.attrs["data-sc-mark"] == "logo"
    images = [node for node in slot.walk() if node.tag == "img"]
    assert images[0].attrs.get("src") == "/guards/logo.png", (
        f"the logo state draws '{images[0].attrs.get('src')}'")
    dashed = [node for node in slot.walk() if "sc-av-dashed" in node.classes()]
    assert len(dashed) == 1, "the logo state's slot carries no dashed edge"
    text = _text(slot)
    assert LOGO_SENTENCE in text, text
    assert "from branding.logo" in text, "the slot does not caption the file it draws"
    assert ORDER_SENTENCE in text
    remove = [node for node in slot.walk() if node.tag == "button" and "disabled" in node.attrs]
    assert len(remove) == 1 and "Remove" in _text(remove[0])


@pytest.mark.asyncio
async def test_the_uploaded_state_draws_the_four_files_at_their_own_sizes(app, config_dir):
    """STATE 3 — the four previews are the ACTUAL files at 32, 64, 128 and 256, never scaled."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        await upload_mark(http, html, "guards", mark_png())
        html = await open_page(http)
        slot = _slot(_cards(html)["guards"])
        assert slot.attrs["data-sc-mark"] == "uploaded"
        previews = [node for node in slot.walk() if "figure" == node.tag]
        assert len(previews) == 4, f"the uploaded state draws {len(previews)} previews"
        captions = [_text(next(n for n in figure.walk() if n.tag == "figcaption"))
                    for figure in previews]
        assert captions == ["32", "64", "128", "256"], captions
        addresses = []
        for figure, size in zip(previews, sites.AVATAR_SIZES):
            image = next(node for node in figure.walk() if node.tag == "img")
            address = image.attrs.get("src", "")
            assert f"/{size}.png" in address, f"the {size} preview points at '{address}'"
            assert image.attrs.get("width") == str(size) and image.attrs.get("height") == str(size)
            addresses.append(address)
            served = await http.get(address)
            assert served.status_code == 200, f"{address} answered {served.status_code}"
            assert served.headers["content-type"] == "image/png"
            assert served.content == (site_assets(config_dir) /
                                      f"avatar-guards-{size}.png").read_bytes(), (
                f"{address} does not serve the {size} file")
        from PIL import Image
        import io

        for address, size in zip(addresses, sites.AVATAR_SIZES):
            served = await http.get(address)
            with Image.open(io.BytesIO(served.content)) as image:
                assert image.size == (size, size), f"{address} is {image.size}"
    text = _text(slot)
    assert ORDER_SENTENCE in text, text
    for size in sites.AVATAR_SIZES:
        assert f"avatar-guards-{size}.png" in text, f"the caption does not name the {size} file"
    images = [node for node in slot.walk() if node.tag == "img"]
    assert images[0].attrs.get("src", "").endswith("256.png") or "/256.png?" in images[0].attrs.get(
        "src", ""), f"the slot draws '{images[0].attrs.get('src')}' instead of the 256 file"
    buttons = {_text(node) for node in slot.walk() if node.tag == "button"}
    assert any("Replace" in label for label in buttons), buttons
    assert any(label.startswith("Remove") for label in buttons), buttons
    assert not [node for node in slot.walk() if node.tag == "button" and "disabled" in node.attrs], (
        "the uploaded state draws a disabled control")


@pytest.mark.asyncio
async def test_the_removal_asks_once_inline_and_the_plain_remove_posts_without_scripts(
        app, config_dir):
    """STATE 4 — the inline confirm, and its no-JS path: the plain Remove submit."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        await upload_mark(http, html, "guards", mark_png())
        html = await open_page(http)
        slot = _slot(_cards(html)["guards"])
        confirms = [node for node in slot.walk() if "data-sc-mark-confirm" in node.attrs]
        assert len(confirms) == 1, "the uploaded state carries no inline confirm"
        confirm = confirms[0]
        assert "hidden" in confirm.attrs, "the inline confirm is drawn before it is asked"
        assert CONFIRM_SENTENCE in _text(confirm), _text(confirm)
        confirm_post = [node for node in confirm.walk()
                        if node.tag == "button" and node.attrs.get("formaction")]
        assert len(confirm_post) == 1, "the inline confirm offers no removal"
        plain = [node for node in slot.walk()
                 if node.tag == "button" and node.attrs.get("formaction") == mark_remove_path()]
        assert len(plain) == 2, (
            "the plain Remove (the no-script path) does not post to the removal route")
        reply = await http.post(mark_remove_path(),
                                data={"_csrf_token": token_of(html), "_site": "guards"})
        assert reply.status_code == 303
        html = await open_page(http)
    assert _slot_state(html, "guards") == "shipped", "a removal does not return to the fallback state"
    assert not site_assets(config_dir).exists() or not list(site_assets(config_dir).iterdir())


@pytest.mark.asyncio
async def test_the_mark_form_carries_the_csrf_and_revision_fields(app, config_dir):
    """PIN 4 — the upload posts the session's token and the page's revision, and a render follows it."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        forms = [node for node in _cards(html)["guards"].walk()
                 if node.tag == "form" and node.attrs.get("action") == mark_upload_path()]
        assert len(forms) == 1, f"the guards card renders {len(forms)} mark forms"
        form = forms[0]
        assert form.attrs.get("method", "").lower() == "post"
        assert form.attrs.get("enctype") == "multipart/form-data", (
            "the mark form cannot carry a file")
        fields = {child.attrs.get("name"): child.attrs.get("value")
                  for child in form.walk() if child.tag == "input"}
        assert fields.get("_csrf_token") == token_of(html), "the mark form carries no CSRF token"
        assert fields.get("_revision") == revision_of(html), "the mark form carries no revision"
        assert fields.get("_site") in {"root", "guards", "others"}, fields
        # every control of the slot belongs to THAT form, never to the card's save form
        slot = _slot(_cards(html)["guards"])
        controls = [node for node in slot.walk() if node.tag in {"input", "button"}
                    and (node.attrs.get("name") or node.attrs.get("form"))]
        assert controls, "the slot draws no control bound to its own form"
        for control in controls:
            assert control.attrs.get("form") == form.attrs.get("id"), (
                f"a slot control ({control.tag} {control.attrs.get('name')}) does not name the mark "
                "form")
        file_input = [node for node in slot.walk()
                      if node.tag == "input" and node.attrs.get("type") == "file"]
        assert len(file_input) == 1 and file_input[0].attrs.get("name") == site_page.SITE_AVATAR_FIELD
        accept = file_input[0].attrs.get("accept", "")
        for kind in ("image/png", "image/jpeg", "image/webp"):
            assert kind in accept, f"the file control does not accept {kind}"
        await upload_mark(http, html, "guards", mark_png())
        html = await open_page(http)
    assert _slot_state(html, "guards") == "uploaded", (
        "the render that follows the upload does not show the uploaded state")


def test_the_slot_s_css_is_the_design_s_measures():
    """The slot's own geometry: 96 / 72, contained on a well, and previews that are never scaled."""
    css = (REPO_ROOT / "services/webservice/static" / "settings-scope.css").read_text("utf-8")
    slot = _rule(css, ".sc-av-slot")
    assert re.search(r"width:\s*96px", slot) and re.search(r"height:\s*96px", slot), slot
    assert re.search(r"border:\s*1px solid var\(--line-2\)", slot), slot
    assert re.search(r"background:\s*var\(--ground-2\)", slot), slot
    image = _rule(css, ".sc-av-slot img")
    assert re.search(r"object-fit:\s*contain", image), image
    narrow = _media_block(css, "max-width: 820px")
    assert re.search(r"\.sc-av-slot\s*\{[^}]*width:\s*72px", narrow), (
        "the slot is not 72 px at ≤820")
    for size in (32, 64, 128, 256):
        box = _rule(css, f".sc-av-pre .s{size}")
        assert re.search(rf"width:\s*{size}px", box) and re.search(rf"height:\s*{size}px", box), (
            f"the {size} preview box is not {size} px: {box.strip()}")


def test_the_inline_confirm_is_a_script_and_the_remove_posts_without_one():
    """The confirm is progressive enhancement: the markup posts, the script asks first."""
    script = (REPO_ROOT / "services/webservice/static" / "site-config.js").read_text("utf-8")
    assert "data-sc-mark-remove" in script, "the script does not intercept the Remove"
    assert "data-sc-mark-confirm" in script, "the script does not reveal the inline confirm"
    assert "data-sc-mark-cancel" in script, "the inline confirm cannot be dismissed"


@pytest.mark.asyncio
async def test_a_mark_preview_address_is_narrowed_to_the_caller_s_scope(app, config_dir):
    """The three previews are served by the console, so they obey the page's own scope rule.

    A token this build does not serve answers 404, a size outside ``AVATAR_SIZES`` answers 404, and a
    group admin may read their OWN site's files and no other site's.
    """
    address = site_page.SITE_MARK_PATH
    async with client(app) as http:
        await sign_in_as(http, GROUP_ADMIN)
        html = await open_page(http)
        await upload_mark(http, html, "guards", mark_png())
        own = await http.get(mount_url(address, token="guards", size=32))
        foreign = await http.get(mount_url(address, token="others", size=32))
        unknown = await http.get(mount_url(address, token="nosuchsite", size=32))
        bad_size = await http.get(mount_url(address, token="guards", size=48))
        html = await open_page(http)
    assert own.status_code == 200, own.text[:200]
    assert own.headers["content-type"] == "image/png"
    assert foreign.status_code == 404 and unknown.status_code == 404
    assert bad_size.status_code == 404
    assert _slot_state(html, "guards") == "uploaded"


# ================================== §1–§5 · THE MERGED PALETTE BLOCK (one block, three panels)

#: the sheet the transcription carries — the same text the page's own expert block prefills
SHEET_TEXT = site_page.expert_sheet().text
SHEET_DECLARATIONS = len([line for line in SHEET_TEXT.splitlines() if line.strip()])


def _sheet_yaml(indent: str = "        ") -> str:
    """The transcription as a YAML block scalar, indented for a ``theme.expert`` key."""
    return "".join(f"{indent}{line}\n" for line in SHEET_TEXT.splitlines())


#: three sites, one per palette state a site may be in — the two-way truth plus the shipped default,
#: side by side
PALETTE_SITES_YAML = f"""\
sites:
  shipped:
    slug: shipped
    group_role: "5th Guards"
  preset:
    slug: preset
    group_role: "5th Guards"
    theme:
      preset: slate
  sheet:
    slug: sheet
    group_role: "5th Guards"
    theme:
      expert: |
{_sheet_yaml()}
"""


def _palette_app(config_dir):
    return _two_site_app(config_dir, PALETTE_SITES_YAML)


def _palette_block(card: "_Node") -> "_Node":
    """The card's ONE palette block."""
    found = [node for node in card.walk() if node.attrs.get("data-sc-block") == "palette"]
    assert len(found) == 1, f"the card draws {len(found)} palette blocks"
    return found[0]


def _head_truth(card: "_Node") -> dict:
    """What the block's head states: the words, the key in force and its value."""
    block = _palette_block(card)
    head = next(node for node in block.children if "sc-sechd" in node.classes())
    return {
        "words": _text(next(node for node in head.walk() if "data-sc-pal-words" in node.attrs)),
        "key": _text(next(node for node in head.walk() if "data-sc-pal-key" in node.attrs)),
        "value": _text(next(node for node in head.walk() if "data-sc-pal-value" in node.attrs)),
    }


def _marked_tiles(card: "_Node") -> list[str]:
    """The preset tiles that are marked in use, with the value of the radio they carry."""
    block = _palette_block(card)
    marked = []
    for tile in [node for node in block.walk() if "data-sc-preset-tile" in node.attrs]:
        if "on" not in tile.classes():
            continue
        radio = next(node for node in tile.walk() if node.tag == "input")
        marked.append(radio.attrs.get("value", ""))
    return marked


def _badges_shown(card: "_Node") -> list[str]:
    """The "In use" badges actually drawn — the words, so a wrong badge is as loud as a missing one."""
    block = _palette_block(card)
    return [_text(badge)
            for tile in [node for node in block.walk() if "data-sc-preset-tile" in node.attrs]
            for badge in tile.walk()
            if "data-sc-preset-badge" in badge.attrs and "hidden" not in badge.attrs]


def _checked_preset(card: "_Node") -> list[str]:
    block = _palette_block(card)
    return [node.attrs.get("value", "") for node in block.walk()
            if node.tag == "input" and node.attrs.get("name", "").endswith(".theme.preset")
            and "checked" in node.attrs]


@pytest.mark.asyncio
async def test_the_head_names_the_one_of_two_this_site_uses(config_dir):
    """PIN 3: the words, the key in force and its value — one statement per site, and only a preset
    marks a tile.

    The three cards are the two stored states plus the shipped default, and the pin is that each one
    names itself and no other: a site wearing an expert sheet marks NO preset, because a name belongs
    to the tile that carries it and none of them is in force.
    """
    app = _palette_app(config_dir)
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    cards = _cards(html)

    shipped = cards["shipped"]
    assert _head_truth(shipped) == {"words": "A shipped preset", "key": "theme.preset",
                                    "value": ""}, _head_truth(shipped)
    # a site with no preset and no sheet wears the shipped palette: the EMPTY tile is the one marked
    assert _marked_tiles(shipped) == [""], _marked_tiles(shipped)
    assert _checked_preset(shipped) == [""], _checked_preset(shipped)
    assert _badges_shown(shipped) == ["In use"], _badges_shown(shipped)

    preset = cards["preset"]
    assert _head_truth(preset) == {"words": "A shipped preset", "key": "theme.preset", "value": ""}, \
        _head_truth(preset)
    assert _marked_tiles(preset) == ["slate"], _marked_tiles(preset)
    assert _checked_preset(preset) == ["slate"], _checked_preset(preset)
    assert _badges_shown(preset) == ["In use"], _badges_shown(preset)
    # the preset's NAME is the gallery's: it is not repeated in the head
    assert "Slate" not in _head_truth(preset)["words"] + _head_truth(preset)["value"]

    sheet = cards["sheet"]
    assert _head_truth(sheet) == {"words": "An expert sheet", "key": "theme.expert",
                                  "value": f"{SHEET_DECLARATIONS} declarations"}, _head_truth(sheet)
    assert _marked_tiles(sheet) == [], f"a site wearing a sheet marks {_marked_tiles(sheet)}"
    assert _badges_shown(sheet) == [], "a preset claims to be in use while the sheet is in force"
    assert _checked_preset(sheet) == [], "a preset is checked while the sheet is in force"


@pytest.mark.asyncio
async def test_the_palette_is_one_block_and_the_wizard_is_two_of_its_panels(app):
    """PIN 4: one block per card, the wizard's two steps INSIDE it, and one preview on the page.

    The wizard used to be a block of its own with a frame of its own; the pin is the parsed tree —
    one ``data-sc-block="palette"``, the wizard and both of its bodies descend from it, and no block
    draws a frame.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    document = _parse(html)
    for key, card in _cards(html).items():
        block = _palette_block(card)
        rest = [node for node in block.walk() if node.attrs.get("data-sc-pal-panel") == "rest"]
        assert len(rest) == 1, f"{key}: {len(rest)} galleries in the block"
        wizards = [node for node in block.walk() if "data-sc-wizard" in node.attrs]
        assert len(wizards) == 1, f"{key}: {len(wizards)} wizards in the palette block"
        bodies = [node.attrs.get("data-sc-wiz-body")
                  for node in wizards[0].walk() if "data-sc-wiz-body" in node.attrs]
        assert bodies == ["1", "2"], f"{key}: the wizard's bodies are {bodies}"
        # the steps are PANELS: exactly one of the three is drawn at rest
        assert "hidden" not in rest[0].attrs, f"{key}: the gallery is hidden at rest"
        assert "hidden" in wizards[0].attrs, f"{key}: the wizard is drawn at rest"
        # no block-in-block frame: the palette block holds no preview of its own
        assert not [node for node in block.walk() if node.tag == "iframe"], (
            f"{key}: the palette block draws a preview of its own")
    frames = [node for node in document.walk() if node.tag == "iframe"]
    cards = [node for node in document.walk() if "sc-form" in node.classes()]
    assert len(frames) == len(cards), f"the page draws {len(frames)} frames for {len(cards)} cards"


@pytest.mark.asyncio
async def test_the_one_rule_is_stated_once_under_the_gallery(app):
    """§5: the block's one rule sits under the gallery, always visible, and says all three names."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    card = _cards(html)["root"]
    block = _palette_block(card)
    rules = [node for node in block.walk() if "sc-palrule" in node.classes()]
    assert len(rules) == 1, f"the block states its rule {len(rules)} times"
    text = _text(rules[0])
    assert "One palette, one choice." in text, text
    for word in ("A shipped preset", "expert sheet"):
        assert word in text, f"the rule never names {word}: {text}"
    assert "at most one" in text, text
    # the rule states what the SHIPPED save does with two names: the parser refuses it
    assert "is refused, and nothing is written" in text, text


def _expert_block(card: "_Node") -> "_Node":
    """The card's ONE expert block — the section that draws the sheet."""
    found = [node for node in card.walk() if node.attrs.get("data-sc-block") == "expert"]
    assert len(found) == 1, f"the card draws {len(found)} expert blocks"
    return found[0]


@pytest.mark.asyncio
async def test_expert_mode_is_a_view_control_with_no_name(app):
    """PIN 2, in the markup: the console's own ``.st-expert`` box on the SHEET's own bar, no ``name``.

    A control with a ``name`` posts, and the sheet is the only thing that may carry the palette — so
    the box must be nameless, and the textarea it reveals must be the only posted control for
    ``theme.expert``. The box sits on the bar of the block that draws that sheet, so the control is
    beside its effect; the palette block's head carries none.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    for key, card in _cards(html).items():
        block = _expert_block(card)
        head = next(node for node in block.children if "sc-sechd" in node.classes())
        toggles = [node for node in head.walk() if "data-sc-expert-toggle" in node.attrs]
        assert len(toggles) == 1, f"{key}: {len(toggles)} Expert Mode boxes on the sheet's bar"
        box = toggles[0]
        assert box.tag == "input" and box.attrs.get("type") == "checkbox", box.attrs
        assert box.attrs.get("role") == "switch", f"{key}: the box is not the console's switch"
        assert "name" not in box.attrs, f"{key}: the box posts a field: {box.attrs.get('name')}"
        label = box.parent
        assert label.tag == "label" and "st-expert" in label.classes(), label.attrs
        assert "Expert Mode" in _text(label), _text(label)
        # …and the site that stores no sheet starts UNTICKED
        assert "checked" not in box.attrs or key == "sheet", key
        # the palette block's head carries NO control: the switch is not where the palette is named
        palette_head = next(node for node in _palette_block(card).children
                            if "sc-sechd" in node.classes())
        assert not [node for node in palette_head.walk()
                    if "data-sc-expert-toggle" in node.attrs], (
            f"{key}: the palette block's head still carries the Expert Mode box")
    # the sheet's own control is the textarea, and it exists whether the box is ticked or not
    for key, card in _cards(html).items():
        posted = [node for node in card.walk() if node.tag == "textarea"
                  and node.attrs.get("name", "").endswith(".theme.expert")]
        assert len(posted) == 1, f"{key}: {len(posted)} expert textareas in the form"


@pytest.mark.asyncio
async def test_the_palette_block_carries_no_stylesheet_control(app):
    """The palette has two sources: the block draws no field for a file of the operator's own, and
    the block names no removed key."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    assert "theme.stylesheet" not in html, "the retired key is still rendered"
    assert "data-sc-pal-own" not in html, "the operator's-own-file row is still drawn"
    assert "data-sc-wiz-strip" not in html, "the wizard still carries the clipboard sentence"
    block = _palette_block(_cards(html)["root"])
    assert not [node for node in block.walk()
                if node.attrs.get("name", "").endswith(".theme.stylesheet")], \
        "the palette block still posts a theme.stylesheet field"


@pytest.mark.asyncio
async def test_a_site_that_names_no_palette_starts_the_wizard_on_the_shipped_preset(config_dir):
    """The starting point is the stored sheet, else the shipped preset — there is no third source."""
    app = _palette_app(config_dir)
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    cards = _cards(html)
    for key in ("shipped", "preset"):
        wizard = next(node for node in cards[key].walk() if "data-sc-wizard" in node.attrs)
        payload = json.loads(wizard.attrs["data-sc-wizard"])
        assert payload["start"] == "preset", (key, payload["start"])
        assert [tile["id"] for tile in payload["tiles"] if tile["on"]] == ["preset", "one"], key
    stored = next(node for node in cards["sheet"].walk() if "data-sc-wizard" in node.attrs)
    assert json.loads(stored.attrs["data-sc-wizard"])["start"] == "stored"


@pytest.mark.asyncio
async def test_a_site_with_a_sheet_is_pointed_at_the_control_at_the_foot(config_dir):
    """PIN 2, in the markup: the head states the palette and POINTS at the switch, never carries it.

    A site whose palette IS an expert sheet ends its say-line with a plain anchor onto the block that
    draws that sheet; a site with no sheet gets no anchor, because there is no sheet to show.
    """
    app = _palette_app(config_dir)
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    cards = _cards(html)
    assert set(cards) == {"shipped", "preset", "sheet"}, set(cards)
    sheet_block = _expert_block(cards["sheet"])
    target = sheet_block.attrs.get("id", "")
    assert target, "the sheet's block carries no id for the anchor to reach"
    for key, card in cards.items():
        head = next(node for node in _palette_block(card).children
                    if "sc-sechd" in node.classes())
        anchors = [node for node in head.walk() if "sc-palfoot" in node.classes()]
        if key != "sheet":
            assert not anchors, f"{key}: a site with no sheet is pointed at the switch"
            continue
        assert len(anchors) == 1, f"{key}: {len(anchors)} anchors onto the sheet's block"
        anchor = anchors[0]
        assert anchor.tag == "a", anchor.attrs
        assert anchor.attrs.get("href") == f"#{target}", anchor.attrs
        assert "at the foot of the page" in _text(anchor), _text(anchor)


@pytest.mark.asyncio
async def test_the_character_block_states_its_title_once(app):
    """§3: the block's own title is drawn ONCE, and its row's name is the block's QUESTION.

    Two copies of "Character" one under the other read as a stutter; the inner header answers the
    title instead of repeating it. Checked for every block: no block repeats its own `<h3>` in the
    body it opens.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    card = _cards(html)["guards"]
    block = next(node for node in card.walk() if node.attrs.get("data-sc-block") == "character")
    titles = [node for node in block.walk() if node.tag == "h3"]
    assert len(titles) == 1, f"the character block carries {len(titles)} h3 titles"
    title = _text(titles[0])
    assert title == "Character", title
    inner = [_text(node) for node in block.walk()
             if node.tag == "div" and "nm" in node.classes()]
    assert inner == [site_page.SITE_CHARACTER_Q], (
        f"the block repeats its title in its own row: {inner}")
    assert sum(1 for node in block.walk() if title in _text(node) and node.tag == "h3") == 1
    # NO OTHER BLOCK repeats its own title in its own body
    for key, card in _cards(html).items():
        for section in [node for node in card.walk() if "data-sc-block" in node.attrs]:
            heads = [_text(node) for node in section.walk() if node.tag == "h3"]
            if len(heads) != 1:
                continue
            names = [_text(node) for node in section.walk()
                     if node.tag in ("div", "p", "span") and "nm" in node.classes()]
            assert heads[0] not in names, f"{key}: the {heads[0]} block repeats its own title"


@pytest.mark.asyncio
async def test_a_group_admin_gets_no_box_no_sheet_and_no_door(app):
    """PIN 6: `site.write` without `site.expert` — presets and the preview, and none of the three.

    ``theme.expert`` is the installation admin's, and the wizard is a UI over that sheet, so a group
    admin is offered neither: no checkbox, no textarea in the form, and no door onto nothing.
    """
    async with client(app) as http:
        await sign_in_as(http, GROUP_ADMIN)
        html = await open_page(http)
    card = _cards(html)["guards"]
    block = _palette_block(card)
    assert not [node for node in card.walk() if "data-sc-expert-toggle" in node.attrs], (
        "a group admin is drawn an Expert Mode box")
    assert not [node for node in card.walk() if node.tag == "textarea"
                and node.attrs.get("name", "").endswith(".theme.expert")], (
        "a group admin's form carries the expert sheet")
    assert not [node for node in block.walk() if "data-pal-start" in node.attrs], (
        "a group admin is drawn a door onto a wizard they cannot open")
    assert not [node for node in block.walk() if "data-sc-wizard" in node.attrs], (
        "a group admin is drawn the palette wizard")
    assert not [node for node in card.walk() if "data-sc-expert" in node.attrs], (
        "a group admin is drawn the sheet's editor")
    # …and the presets and the page's one preview are theirs, as before
    assert len([node for node in block.walk() if "data-sc-preset-tile" in node.attrs]) == \
        1 + len(theme_css.PRESETS), "the gallery is not whole for a group admin"
    assert [node for node in card.walk() if node.tag == "iframe"], "a group admin has no preview"
    assert _marked_tiles(card) == [""], _marked_tiles(card)
    assert _head_truth(card) == {"words": "A shipped preset", "key": "theme.preset", "value": ""}


@pytest.mark.asyncio
async def test_switching_the_root_site_off_moves_the_console_before_the_redirect(app):
    """The apply lands BEFORE the answer, so the redirect follows the mount the save just moved."""
    from services.webservice import mount

    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)              # the console is under /console while root is served
        reply = await post(http, html, {"_site": "root", "f.sites.root.enabled": "0"})
        assert reply.status_code == 303, reply.text[:400]
        target = reply.headers["location"]
        assert target == mount.url(site_page.SITE_CONFIG_PATH) + "?site=root"
        assert target == "/settings/site?site=root", target
        landed = await http.get(target)
        assert landed.status_code == 200, landed.text[:400]
        assert "re-read the site settings file" in notice_of(landed.text)
        assert (await http.get("/")).status_code == 200, "the console no longer owns /"


# ================================================= the row states the site's ADDRESS, and once (T10)

def _address_chips(html: str) -> "list[str]":
    """Every address chip on the page, in document order, as the string it prints."""
    return [_text(node).replace(" ", "") for node in _parse(html).walk()
            if "sc-addr" in node.classes()]


@pytest.mark.asyncio
async def test_every_card_leads_with_the_address_a_visitor_types(app):
    """The row is read by the address; the config key is the config's own name, beside it."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    assert _address_chips(html) == ["/", "/guards", "/others"], _address_chips(html)


@pytest.mark.asyncio
async def test_the_site_at_the_root_prints_its_slash_as_a_value(app):
    """One glyph in a pill reads as an empty value, so the root chip is given a real width."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    roots = [node for node in _parse(html).walk() if "sc-addr-root" in node.classes()]
    assert len(roots) == 1, f"the page draws {len(roots)} root address chips"
    assert _text(roots[0]).strip() == "/"


@pytest.mark.asyncio
async def test_the_row_states_the_address_once_and_keeps_the_editable_slug(app):
    """The chip replaces the two read-only copies the row used to print, and adds no field.

    The Admin's editable slug — the value the NEXT save writes — is a different fact from the address
    being SERVED, so it stays exactly where it was, and the root site keeps its sentence.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    assert "sc-idro" not in html, "the row still prints a second, read-only copy of the address"
    assert 'name="f.sites.guards.slug"' in html, "the Admin lost the field that edits the slug"
    assert "site root" in _text(_cards(html)["root"]), "the root card lost its sentence"


@pytest.mark.asyncio
async def test_a_group_admin_sees_their_own_address_and_no_field(app):
    async with client(app) as http:
        await sign_in_as(http, GROUP_ADMIN)
        html = await open_page(http)
    assert _address_chips(html) == ["/guards"], _address_chips(html)
    assert 'name="f.sites.guards.slug"' not in html, "a group admin was given the address field"


def test_the_narrow_row_keeps_the_address_chip():
    """Every other chip folds below 1050 px; the identity must not go with them."""
    css = (REPO_ROOT / "services/webservice/static/settings-scope.css").read_text(encoding="utf-8")
    assert ("@media (max-width: 1050px) { .sc-sum .st-chip:not(.sc-addr) { display: none; } }"
            in css)
    assert ".sc-idro" not in css, "the stylesheet still carries the rule its two spans took away"


@pytest.mark.asyncio
async def test_a_dead_link_offers_the_site_at_the_root_not_the_console(app):
    """With a public site at ``/``, that site IS the site root: a 404 must not send anyone to /console.

    The console mounts under a prefix of its own exactly while a site owns ``/``, so the page's one
    way out has to be the root, and it must name what the visitor will find there.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        response = await http.get("/no-such-page", headers={"accept": "text/html"})
    assert response.status_code == 404
    assert 'href="/"' in response.text, response.text[-400:]
    assert "Open the public site" in response.text
    assert "Return to the dashboard" not in response.text, (
        "a dead link offered the admin dashboard while a public site owns the root")
    assert 'href="/console' not in response.text, (
        "the way out of a 404 leads into the console's own mount")


# ============================================ the add block and the file's own editor (Admin only)

def add_url() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_ADD_PATH)


def yaml_url() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_YAML_PATH)


def yaml_validate_url() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_YAML_VALIDATE_PATH)


async def post_add(http, html: str, *, key: str = "", role: str = "", slug: str = "",
                   revision: str | None = None):
    """POST the add block's own form: the session's token, the page's revision and three fields."""
    body = {"_csrf_token": token_of(html),
            "_revision": revision_of(html) if revision is None else revision,
            "_key": key, "_role": role, "_slug": slug}
    return await http.post(add_url(), data=body)


async def post_yaml(http, html: str, text: str, *, revision: str | None = None):
    """POST the YAML panel's own form: the carriers the Expert Mode script would post."""
    body = {"_csrf_token": token_of(html),
            "_revision": revision_of(html) if revision is None else revision,
            "_expert": "1", "_yaml": text}
    return await http.post(yaml_url(), data=body)


@pytest.mark.asyncio
async def test_the_add_block_is_open_when_the_page_has_no_site(app, site_file):
    """No site at all: the empty notice still states it, and the add form is the open block."""
    site_file.unlink()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    blocks = [node for node in _parse(html).walk() if "sc-add" in node.classes()]
    assert len(blocks) == 1, "the add block is missing from the page with no site"
    assert "open" in blocks[0].attrs, "the page's only action is folded away"
    assert site_page.SITE_EMPTY in html, "the empty state's sentence is gone"


@pytest.mark.asyncio
async def test_the_add_block_offers_every_role_servers_yaml_names(app, config_dir):
    """The select is the ONE thing the operator cannot read off ``site.yaml``: the managed_by set."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    roles = sorted(sites.known_managed_by(sites.load_servers_config(config_dir)))
    assert roles, "the fixture declares no managed_by token"
    for role in roles:
        assert f'<option value="{role}">' in html, f"the add form does not offer the role {role}"
    # every id is offered exactly once, and the root is the empty value with its own words
    assert html.count('name="_role"') == 1, "the add form posts its role twice"
    assert f'<option value="">{site_page.SITE_ADD_ROOT}</option>' in html


@pytest.mark.asyncio
async def test_adding_a_site_writes_it_and_opens_its_card(app, site_file):
    """The new entry lands in the file, the comments and the key order survive, and its card opens."""
    before = site_file.read_text(encoding="utf-8")
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post_add(http, html, key="newbies", role="Other Token", slug="newbies")
        assert reply.status_code == 303, reply.text[:400]
        assert reply.headers["location"] == f"{page_path()}?site=newbies", reply.headers["location"]
        landed = await http.get(reply.headers["location"])
    assert landed.status_code == 200, landed.text[:400]
    after = site_file.read_text(encoding="utf-8")
    assert "# the public site's settings" in after, "the file's comment was lost"
    assert after.startswith("# the public site's settings — comments and key order are part of "
                            "the file\nsites:\n"), after[:200]
    assert list(sites.load_site_block(site_file.parent)) == ["root", "guards", "others", "newbies"], (
        "the new site did not land at the end of the block, or a key moved")
    block = sites.load_site_block(site_file.parent)["newbies"]
    assert block["group_role"] == "Other Token" and block["slug"] == "newbies"
    # EVERY LINE THAT WAS THERE IS STILL THERE, in order: the write appends, it does not reformat.
    kept = [line for line in before.splitlines() if line.strip()]
    surviving = iter(after.splitlines())
    for line in kept:
        assert line in surviving, f"the write lost or moved this line: {line!r}"
    cards = _cards(landed.text)
    assert "open" in cards["newbies"].attrs, "the card of the site just added came back closed"


@pytest.mark.asyncio
async def test_adding_the_root_site_states_only_its_name(app, site_file):
    """The site root takes no role and no slug: the fewest keys that state the site are written."""
    text = site_file.read_text(encoding="utf-8")
    body = text.split("  guards:", 1)
    site_file.write_text("sites:\n  guards:" + body[1], encoding="utf-8")
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post_add(http, html, key="root")
        assert reply.status_code == 303, reply.text[:400]
    block = sites.load_site_block(site_file.parent)["root"]
    assert block == {}, f"the root site was written with keys it does not need: {dict(block)}"


@pytest.mark.asyncio
async def test_adding_a_key_that_exists_is_refused(app, site_file):
    """A duplicate name is refused and the file is byte-identical — nothing is overwritten."""
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post_add(http, html, key="guards", role="5th Guards", slug="guards-2")
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert "already in this file" in notice_of(landed), notice_of(landed)
    assert site_file.read_bytes() == before, "the refused site was written"


@pytest.mark.asyncio
async def test_adding_a_role_no_server_names_is_refused_with_the_parser_s_sentence(app, site_file):
    """The parser's own words, not a second rule set: an unmatched group_role writes nothing."""
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        await post_add(http, html, key="lonely", role="No Such Role", slug="lonely")
        landed = await open_page(http)
    refused = notice_of(landed)
    assert "matches no managed_by value" in refused, refused
    assert "No Such Role" in refused, refused
    assert site_file.read_bytes() == before, "the refused site was written"


@pytest.mark.asyncio
@pytest.mark.parametrize("key,slug,expected", (
    ("Not A Key", "not-a-key", "is not a site name"),
    ("guards2", "", "needs the segment it answers on"),
))
async def test_an_add_the_page_can_refuse_alone_writes_nothing(app, site_file, key, slug, expected):
    """A name that is not a name, and a site with no address: both refused before any write."""
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        await post_add(http, html, key=key, role="5th Guards", slug=slug)
        landed = await open_page(http)
    assert expected in notice_of(landed), notice_of(landed)
    assert site_file.read_bytes() == before


@pytest.mark.asyncio
async def test_a_group_admin_sees_neither_block_and_both_doors_refuse(app, site_file):
    """A hidden block is not a guard: the two routes answer 403 for a request without the capability."""
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, GROUP_ADMIN)
        html = await open_page(http)
        assert "data-sc-add" not in html, "a group admin was given the add block"
        assert "data-cfg-yaml" not in html, "a group admin was given the file editor"
        csrf = token_of(html)
        added = await http.post(add_url(), data={"_csrf_token": csrf, "_key": "theirs",
                                                 "_role": "5th Guards", "_slug": "theirs"})
        written = await http.post(yaml_url(), data={"_csrf_token": csrf, "_expert": "1",
                                                    "_yaml": "sites: {}\n"})
    assert added.status_code == 403, added.text[:400]
    assert written.status_code == 403, written.text[:400]
    assert site_file.read_bytes() == before, "a refused door wrote the file"


@pytest.mark.asyncio
async def test_the_yaml_panel_renders_the_file_s_own_text(app, site_file):
    """The panel's read-only view is the file the save writes — comments and key order included."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    assert 'data-cfg-yaml data-validate-url="' in html, "the Expert Mode panel is not on the page"
    assert f'data-validate-url="{yaml_validate_url()}"' in html
    assert "comments and key order are part of the file" in html, (
        "the panel does not render the file's comment")
    assert "character: pve" in html, "the panel does not render the file's own text"
    assert 'name="_yaml"' in html and 'name="_expert" value="0"' in html, (
        "the panel's carriers are not inside the page's own form")


@pytest.mark.asyncio
async def test_a_yaml_save_writes_the_document_and_keeps_every_comment(app, site_file):
    """The edited document is written through the one primitive; every comment survives it."""
    before = site_file.read_text(encoding="utf-8")
    edited = before.replace("title: Guards", "title: Guards Wing")
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post_yaml(http, html, edited)
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    after = site_file.read_text(encoding="utf-8")
    assert "title: Guards Wing" in after, after
    assert "# the public site's settings — comments and key order are part of the file" in after
    assert sites.load_site_block(site_file.parent)["guards"]["branding"]["title"] == "Guards Wing"
    assert "Saved" in notice_of(landed), notice_of(landed)


@pytest.mark.asyncio
@pytest.mark.parametrize("text,expected", (
    # YAML that does not parse at all
    ("sites:\n  guards: [unclosed\n", "not valid YAML"),
    # YAML that parses and a sites block the loader refuses
    ("sites:\n  guards:\n    group_role: \"No Such Role\"\n    slug: guards\n",
     "matches no managed_by value"),
    # an empty document would be written as `null`
    ("", "the document is empty"),
))
async def test_a_document_the_page_refuses_is_never_written(app, site_file, text, expected):
    """A refused document leaves the file sha-identical, whatever the reason."""
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post_yaml(http, html, text)
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert expected in notice_of(landed), notice_of(landed)
    assert site_file.read_bytes() == before, "the refused document was written"


@pytest.mark.asyncio
@pytest.mark.parametrize("text,ok", (
    (SITE_YAML, True),
    ("sites:\n  guards:\n    group_role: \"No Such Role\"\n    slug: guards\n", False),
    ("sites: [\n", False),
))
async def test_the_live_check_answers_the_verdict_and_writes_nothing(app, site_file, text, ok):
    """The panel's data-validate-url: a JSON verdict from the same reader the save uses."""
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await http.post(yaml_validate_url(),
                                data={"_csrf_token": token_of(html), "_yaml": text},
                                headers={"accept": "application/json"})
    assert reply.status_code == 200, reply.text[:400]
    verdict = reply.json()
    assert verdict["ok"] is ok, verdict
    if not ok:
        assert verdict["message"], "a refusal carried no sentence"
    assert site_file.read_bytes() == before, "the live check wrote the file"


@pytest.mark.asyncio
async def test_a_stale_revision_is_refused_for_both_doors(app, site_file):
    """A file that moved since the page was rendered is refused, and nothing is written."""
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        assert (await post_add(http, html, key="newbies", role="Other Token", slug="newbies",
                               revision="0" * 64)).status_code == 303
        told = notice_of(await open_page(http))
        assert "stale" in told, told
        assert (await post_yaml(http, html, SITE_YAML, revision="0" * 64)).status_code == 303
        told = notice_of(await open_page(http))
    assert "stale" in told, told
    assert site_file.read_bytes() == before, "a stale post wrote the file"


@pytest.mark.asyncio
async def test_adding_the_first_site_creates_the_file(app, site_file):
    """A console with no ``site.yaml`` gets one: the block is created, not appended to nothing."""
    site_file.unlink()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post_add(http, html, key="root")
        assert reply.status_code == 303, reply.text[:400]
        landed = await http.get(reply.headers["location"])
    assert landed.status_code == 200, landed.text[:400]
    assert site_file.is_file(), "the first site did not create the file"
    assert list(sites.load_site_block(site_file.parent)) == ["root"]
    assert "open" in _cards(landed.text)["root"].attrs


@pytest.mark.asyncio
async def test_the_two_blocks_sit_at_the_foot_of_the_page_in_order(app):
    """The add form first, the file second, both AFTER every card — the page's own reading order."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    cards = html.rfind('data-sc-card="')
    add = html.find("data-sc-add")
    yaml_panel = html.find("data-cfg-yaml data-validate-url")
    assert cards > 0 and add > cards, "the add block is not below the cards"
    assert yaml_panel > add, "the YAML panel is not the last block"


@pytest.mark.asyncio
async def test_a_yaml_save_from_a_form_writes_no_stray_carriage_return(app, site_file):
    """A form posts CRLF whatever the file holds: the save must not stamp a CR onto every comment."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        posted = site_file.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
        reply = await post_yaml(http, html, posted)
        assert reply.status_code == 303, reply.text[:400]
    with open(site_file, "r", encoding="utf-8", newline="") as handle:
        written = handle.read()
    assert "\r" not in written, f"the save left CR in the file: {written[:120]!r}"


#: the stub-DOM harness for the "Add a site" form's name field (see the file's own header)
SITE_ADD_HARNESS = Path(__file__).resolve().parent / "site_add_harness.js"


@requires_node
def test_the_name_field_follows_the_role_and_never_overwrites_a_typed_name():
    """One run of the shipped script against a stub add form, once per case.

    The name a site gets in the file is a token and the operator's words are not one, so the field is
    FILLED from the role — and only while it holds nothing, the last proposal, or nothing of its own:
    a name already in the field (typed, or printed by a re-render) is never taken away.
    """
    done = subprocess.run(["node", str(SITE_ADD_HARNESS), str(SITE_CONFIG_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert seen["root_at_load"] == "root", seen
    assert seen["root_picked"] == "root", seen
    assert seen["group_at_load"] == "5th-guards", seen
    assert seen["group_switched"] == "other-token", (
        f"picking another role left the old proposal in the field: {seen['group_switched']!r}")
    assert seen["accents_and_punctuation"] == "elite-co", (
        f"a role is not reduced to a name a key can carry: {seen['accents_and_punctuation']!r}")
    assert seen["typed_name_survives"] == "my-own", (
        "a name the operator typed was overwritten by a role change")
    assert seen["rendered_name_survives"] == "custom", (
        "a name already in the field was overwritten on load")
    assert seen["proposal_then_switch"] == "other-token", (
        "a field still holding the proposal stopped following the role")


@pytest.mark.asyncio
async def test_a_site_the_process_has_not_registered_is_drawn_from_the_file(app, site_file):
    """Routes are installed at startup, so a site the file just gained has no address to answer.

    Its box must still show what the operator made: the frame is the CONSOLE's demo document for that
    key, drawn from the file, and the sentence under it says the site has no address of its own yet.
    A frame at the site's own address would put the shell's 404 page inside the pane.
    """
    text = site_file.read_text(encoding="utf-8")
    site_file.write_text(text.rstrip("\n")
                         + "\n  fresh:\n    group_role: 5th Guards\n    slug: fresh\n",
                         encoding="utf-8")
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    fresh = _cards(html)["fresh"]
    frames = [node for node in fresh.walk() if node.tag == "iframe"]
    assert len(frames) == 1, "the unregistered site's box shows nothing to its operator"
    source = frames[0].attrs.get("src", "")
    assert "/settings/site/mock" in source and "site=fresh" in source, (
        f"the box does not point at the console's demo document: {source!r}")
    assert "/chrome-preview" not in source, (
        "the box points at an address this process never registered")
    note = [node for node in fresh.walk() if node.attrs.get("data-sc-pv-mock") is not None]
    assert len(note) == 1, "the box does not say the site has no address of its own yet"
    assert "restarts" in "".join(note[0].text), (
        f"the sentence does not say when the address answers: {note[0].text!r}")
    assert not [node for node in fresh.walk() if node.tag == "a"
                and node.attrs.get("href", "").endswith("/chrome-preview")], (
        "the card still offers a door onto an address nothing answers")
    # the sites this process DID register keep their own document
    guards = _cards(html)["guards"]
    sources = [node.attrs.get("src", "") for node in guards.walk() if node.tag == "iframe"]
    assert sources and all("/chrome-preview" in src for src in sources), (
        f"the registered site lost its own preview: {sources!r}")
    assert not [node for node in guards.walk() if node.attrs.get("data-sc-pv-mock") is not None]


@pytest.mark.asyncio
async def test_a_site_whose_address_changed_is_drawn_from_the_file(app, site_file):
    """The address is part of what is installed: a renamed slug points where nothing answers."""
    text = site_file.read_text(encoding="utf-8")
    site_file.write_text(text.replace("slug: guards", "slug: guards-moved"), encoding="utf-8")
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    moved = _cards(html)["guards"]
    sources = [node.attrs.get("src", "") for node in moved.walk() if node.tag == "iframe"]
    assert sources, "a renamed site's card shows its operator nothing"
    assert all("/settings/site/mock" in src for src in sources), (
        f"the card still frames the address the site no longer answers on: {sources!r}")
    assert [node for node in moved.walk() if node.attrs.get("data-sc-pv-mock") is not None], (
        "an address changed in the file is drawn as if the site were live")


@pytest.mark.asyncio
async def test_the_demo_document_draws_the_file_site_with_everything_inside_it(app, site_file):
    """What the box shows for a site with no address: the look, and nothing to fetch from it.

    Every address in the demo is inert or inline — the palette and the marks ride in the document —
    so the pane cannot land on a 404 page while it is showing the operator their own site.
    """
    text = site_file.read_text(encoding="utf-8")
    site_file.write_text(text.rstrip("\n")
                         + "\n  fresh:\n    group_role: 5th Guards\n    slug: fresh\n",
                         encoding="utf-8")
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        source = next(node.attrs["src"] for node in _cards(html)["fresh"].walk()
                      if node.tag == "iframe")
        demo = await http.get(source.replace("&amp;", "&"))
    assert demo.status_code == 200, f"the box's document does not answer: {demo.status_code}"
    assert demo.headers.get("cache-control") == "no-store", (
        "the demo document may be cached, and it follows a draft")
    body = demo.text
    assert "data:text/css;base64," in body, "the demo asks for a palette at an address that is not served"
    assert "/chrome-preview" not in body
    assert "site.css" in body, "the demo drops the site's own sheet"
    # nothing in the demo navigates and nothing it names lives on the site's own address: the only
    # addresses left are the installation's fixed /static surface, which answers under every mount
    hrefs = [chunk.split('"', 1)[0] for chunk in body.split('href="')[1:]]
    assert hrefs, "the demo draws no shell at all"
    offenders = [href for href in hrefs
                 if href not in {"#", ""} and not href.startswith(("data:", "/static/"))]
    assert not offenders, f"the demo points at addresses that are not served yet: {offenders!r}"


@pytest.mark.asyncio
async def test_the_demo_follows_the_form_before_the_save(app, site_file):
    """The box is a DRAFT for a site with no address too: a value in the form reaches the drawing.

    The card posts its fields to the draft door, the demo document renders the file's site with that
    draft over it, and the file itself is untouched until Save — so an operator building a site sees
    what they are building, before it exists.
    """
    text = site_file.read_text(encoding="utf-8")
    site_file.write_text(text.rstrip("\n")
                         + "\n  fresh:\n    group_role: 5th Guards\n    slug: fresh\n",
                         encoding="utf-8")
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        source = next(node.attrs["src"] for node in _cards(html)["fresh"].walk()
                      if node.tag == "iframe")
        posted = await http.post(mount.url(site_page.SITE_CONFIG_DRAFT_PATH),
                                 data={"_csrf_token": token_of(html),
                                       site_page.SITE_KEY_FIELD: "fresh",
                                       "f.sites.fresh.branding.title": "Built Before Save"})
        assert posted.status_code == 200, posted.text[:300]
        assert posted.json().get("stored") is True, posted.text[:300]
        demo = await http.get(source.replace("&amp;", "&"))
    assert demo.status_code == 200, demo.text[:300]
    assert "Built Before Save" in demo.text, (
        "the demo does not follow the values in the form — the operator cannot see what they build")
    assert "Built Before Save" not in site_file.read_text(encoding="utf-8"), (
        "a draft reached the site file before Save")


@pytest.mark.asyncio
async def test_a_served_site_preview_draws_the_palette_of_the_draft(app):
    """The card's preview of a SERVED site follows the draft's palette, not the saved sheet.

    A site with an address draws its own document in the box, and without a draft that document names
    the site's ``/theme.css`` — the saved palette, which is what visitors get. WITH a draft the palette
    is composed from the draft and carried in the document instead: the route answers the saved sheet,
    so a preset pressed in the form used to paint and then be replaced by the file's colours on the
    reload that follows the press.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        source = next(node.attrs["src"] for node in _cards(html)["root"].walk()
                      if node.tag == "iframe").replace("&amp;", "&")
        fresh = await http.get(source)
        assert fresh.status_code == 200, fresh.text[:300]
        assert "data:text/css;base64," not in fresh.text, (
            "a preview without a draft carries a palette that belongs to no draft")
        posted = await http.post(mount.url(site_page.SITE_CONFIG_DRAFT_PATH),
                                 data={"_csrf_token": token_of(html),
                                       site_page.SITE_KEY_FIELD: "root",
                                       "f.sites.root.theme.preset": "parchment"})
        assert posted.status_code == 200, posted.text[:300]
        demo = await http.get(source)
    assert demo.status_code == 200, demo.text[:300]
    body = demo.text
    assert "data:text/css;base64," in body, "the preview still fetches the SAVED palette"
    assert "/theme.css" not in body, "the preview names the saved sheet while a draft is in force"
    sheet = base64.b64decode(body.split("data:text/css;base64,", 1)[1].split('"', 1)[0]).decode("utf-8")
    assert "#efe6d2" in sheet, "the palette carried in the preview is not the draft's preset"


# ==================================================== the site FILE's own schema (Expert Mode check)

def _site_schema() -> dict:
    import yaml

    return yaml.safe_load(site_page.SITE_SCHEMA_PATH.read_text(encoding="utf-8"))


def _web_service_sites_block() -> dict:
    """The LEGACY home of the same description, inside the web service's own schema."""
    import yaml

    schema = yaml.safe_load(site_page._SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema["mapping"]["DEFAULT"]["mapping"]["sites"]


def test_the_site_file_describes_itself_at_the_document_root():
    """`config/site.yaml` is a `sites:` document, so its schema's root holds exactly that key."""
    schema = _site_schema()
    assert schema["type"] == "map"
    assert list(schema["mapping"]) == ["sites"], (
        "the site file's schema describes something other than the file's own root")


def test_the_two_copies_of_the_block_cannot_drift():
    """One authority, two addresses: the legacy nest in the web service's schema and the site file's."""
    fresh = _site_schema()["mapping"]["sites"]
    legacy = _web_service_sites_block()
    assert fresh == legacy, (
        "config/site.yaml's schema and the legacy sites: block inside webservice_schema.yaml "
        "describe different blocks")


def test_the_schema_names_every_key_the_loader_reads():
    """A key the loader accepts and the schema omits is refused by the editor as a typo."""
    inner = _site_schema()["mapping"]["sites"]["mapping"]["regex;(.+)"]["mapping"]
    assert set(inner) == set(sites.SITE_KEYS)
    assert set(inner["branding"]["mapping"]) == set(sites.BRANDING_KEYS) | set(
        sites.IGNORED_BRANDING_KEYS)
    assert set(inner["theme"]["mapping"]) == set(sites.THEME_KEYS)


@pytest.mark.asyncio
async def test_the_expert_check_refuses_a_key_the_schema_does_not_know(app, site_file):
    """A typo is named by the schema, not discovered later by the loader's own parser."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await http.post(yaml_validate_url(),
                                data={"_csrf_token": token_of(html),
                                      "_expert": "1",
                                      "_yaml": "sites:\n  root:\n    branding:\n      titel: Home\n"})
    verdict = reply.json()
    assert reply.status_code == 200, reply.text[:300]
    assert verdict["ok"] is False, verdict
    # the SCHEMA's sentence, not the loader's: `sites.root.branding.titel: unknown key` would mean
    # the check never reached the schema file and the pin passes for the wrong reason
    assert verdict["message"].startswith("site.yaml: Schema validation failed"), verdict
    assert "'titel'" in verdict["message"], verdict
    assert site_file.read_text(encoding="utf-8") == SITE_YAML, "the check wrote to the file"


@pytest.mark.asyncio
async def test_the_expert_check_still_accepts_a_legacy_copyright_key(app, site_file):
    """`branding.copyright` is accepted and ignored — a schema that refused it would refuse old files."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await http.post(yaml_validate_url(),
                                data={"_csrf_token": token_of(html),
                                      "_expert": "1",
                                      "_yaml": "sites:\n  root:\n    branding:\n"
                                               "      copyright: Someone\n"})
    assert reply.status_code == 200, reply.text[:300]
    assert reply.json()["ok"] is True, reply.json()


@pytest.mark.asyncio
async def test_a_wrong_type_is_refused_on_save_and_the_file_is_untouched(app, site_file):
    """The save runs the same reader, so the panel's green check and the write cannot disagree."""
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        reply = await post_yaml(http, html, "sites:\n  root:\n    enabled: \"false\"\n")
        assert reply.status_code == 303, reply.text[:400]
        landed = await http.get(reply.headers["location"])
    assert landed.status_code == 200
    assert "Schema validation failed" in landed.text, (
        "the save was refused by something other than the schema")
    assert site_file.read_bytes() == before, "a refused document reached the file"
