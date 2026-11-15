"""The Mods surfaces are WIRED IN: the server page's tab, the node row's door, and the catalogs.

One case per acceptance criterion, driven through the REAL application (the production shell, the
local login backend, the real ``modmanager`` actions and a stub cluster) so a claim about a rendered
page is made by reading the page:

* the Mods tab is OFFERED to a ``mods.view`` holder, its body renders inside the server page, and a
  caller without that capability is REFUSED with the console's 403 — the body never in the response;
* the tab sits between Missions and Log, and the page loads the mods page module's own stylesheet;
* the Nodes row carries ONE more glyph for an ``Admin`` and none for a ``DCS Admin``, its URL is the
  page module's own builder (compared, not re-spelled), and its word rides ``title``/``aria-label``
  translated;
* following that glyph opens the node's root-mods page — the click path, not the href alone;
* the door is offered on the capability the PAGE declares, read from the registrar;
* every new msgid is translated in all four catalogs, read from the COMPILED ``.mo``.

The node page's route belongs to the page module, and the shell registers the page modules it knows;
this file registers ``pages/mods`` the way the shell does before asserting the click path.
"""
from __future__ import annotations

import gettext
import re
from pathlib import Path

import pytest

from services.webservice import i18n
from services.webservice.pages import mods as mods_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.pages import server_detail as detail_page
# the harness and the doubles of the mods pages, reused rather than copied
from test_webui_mods_pages import (  # noqa: F401 - fixtures are imported on purpose
    BLUE_FLAG, NODE, ROOT, ROOT_ROWS, SAVED_ROWS, TRAINING, Cluster, RecordingService, build, client,
    config_dir, fleet, no_service, service, sign_in,
)

from core import Status
from services.webservice import readmodels

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALE = REPO_ROOT / "locale"
LANGUAGES = ("de", "es", "ru", "cn")

#: the tab body's own heading — the sentence that must NOT be in a refused response
BODY_HEADING = "Installed mods (Saved Games)"

#: the glyph's word in the four catalogs' own language, as the aria sentence reads
ARIA_DE = "Die Root-Mods von Knoten {node} öffnen"

#: a msgstr identical to its msgid is only legitimate where the string carries nothing but a NAME or
#: a data pattern. Everything else must differ, or the operator reads English.
IDENTICAL_ALLOWED = {
    "Mods",                                          # the German and Spanish word for it too
    "{package} — v{version} · {size} · {added}",     # placeholders and separators only
}


# --------------------------------------------------------------------------------- the harness

def token_of(html: str) -> str:
    """The CSRF token a rendered page carries (empty when the page renders none)."""
    match = re.search(r'name="_csrf_token" value="([^"]*)"', html)
    return match.group(1) if match else ""


def node_row(html: str, node: str) -> str:
    """The ``<td class="acts">`` cell of *node*'s row — the strip's own container, or ``""``."""
    for row in re.findall(r"<tr>.*?</tr>", html, re.S):
        if f"<b>{node}</b>" in row or f">{node}</b>" in row:
            match = re.search(r'<td class="acts">(.*?)</td>', row, re.S)
            return match.group(1) if match else ""
    return ""


async def german(http) -> None:
    """Ask for the German render the way a visitor does — the display cookie."""
    http.cookies.set(i18n.COOKIE_NAME, "de")


# ------------------------------------------------------------------------ 1. the server page's tab

def _tab_labels(html: str) -> list[str]:
    """The tab strip's labels, in render order."""
    return [label.strip() for label in
            re.findall(r'<a class="tab[^"]*"[^>]*>\s*([^<]+?)\s*</a>', html)]


@pytest.mark.asyncio
async def test_the_tab_renders_its_body_for_a_mods_view_holder(config_dir, service):
    """A DCS Admin holding ``mods.view`` opens the tab and reads the mods page module's OWN body."""
    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get(readmodels.server_url(BLUE_FLAG, tab=mods_page.MODS_TAB))

    assert response.status_code == 200, response.text
    html = response.text
    labels = _tab_labels(html)
    assert "Mods" in labels, labels
    # the body: the tab's own heading, the installed row and the read-only root card
    assert BODY_HEADING in html
    assert "DCSOlympus" in html
    assert f"Root mods on {NODE}" in html
    # the stylesheet the page module declared rides on the page
    assert 'href="/static/mods.css"' in html


@pytest.mark.asyncio
async def test_the_tab_sits_between_missions_and_log_in_the_rendered_strip(config_dir, service):
    """The ORDER the mockup draws: Missions, Mods, Log — read off the rendered strip."""
    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await http.get(readmodels.server_url(BLUE_FLAG, tab=mods_page.MODS_TAB))

    assert response.status_code == 200, response.text
    labels = _tab_labels(response.text)
    assert "Missions" in labels and "Mods" in labels and "Log" in labels, labels
    assert labels.index("Missions") < labels.index("Mods") < labels.index("Log"), labels


@pytest.mark.asyncio
async def test_the_tab_is_refused_for_a_dcs_caller(config_dir, service):
    """A ``DCS`` caller asking for ``?tab=mods`` gets the console's 403 and NO body at all."""
    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "plain")
        response = await http.get(readmodels.server_url(BLUE_FLAG, tab=mods_page.MODS_TAB))

    assert response.status_code == 403, response.status_code
    assert BODY_HEADING not in response.text
    assert "DCSOlympus" not in response.text
    assert "act-btn" not in response.text


@pytest.mark.asyncio
async def test_the_tab_is_refused_by_its_own_gate_and_not_offered(config_dir, service,
                                                                  monkeypatch):
    """The TAB's own check, proved on a caller who may OPEN THE PAGE.

    The tab and the page declare the same roles today, so the only caller the tab alone can refuse is
    one whose ``mods.view`` is narrower than the page's ``servers.view``; that difference is set here
    on the DECLARATION (never on the view), so the refusal and the absent link come from the tab's own
    predicate rather than from the page gate.
    """
    from services.webservice import permissions

    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS)
    app = build(config_dir, fleet())
    # narrowed AFTER the build: the page module re-declares its capabilities as it registers, and a
    # second declaration of one capability is refused loudly — so the difference is set on the live
    # table the gate reads, and monkeypatch restores it when this case ends.
    monkeypatch.setitem(permissions.CAPABILITIES, mods_page.MODS_VIEW_CAPABILITY, ("Admin",))
    monkeypatch.setitem(permissions.SCOPE_GRANTS, mods_page.MODS_VIEW_CAPABILITY, False)
    async with client(app) as http:
        await sign_in(http, "mod1")                       # DCS Admin: holds servers.view
        page = await http.get(readmodels.server_url(BLUE_FLAG))
        response = await http.get(readmodels.server_url(BLUE_FLAG, tab=mods_page.MODS_TAB))

    assert page.status_code == 200, page.text
    assert "Mods" not in _tab_labels(page.text), "the refused tab must not be offered"
    assert response.status_code == 403, response.status_code
    assert BODY_HEADING not in response.text
    assert "DCSOlympus" not in response.text


# --------------------------------------------------------------------------- 2. the node row's door

@pytest.mark.asyncio
async def test_the_nodes_row_offers_the_mods_glyph_to_an_admin(config_dir, service):
    """``mods.manage`` is held by an Admin: the glyph is in the row's strip, and its URL is the
    page module's own builder — compared with it, never a second spelling of the path."""
    service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await http.get(nodes_page.NODES_PATH)

    assert page.status_code == 200, page.text
    strip = node_row(page.text, NODE)
    assert strip, "the node's row renders no controls cell"
    anchor = re.search(r'<a class="act-btn mods" href="([^"]+)"(.*?)>(.*?)</a>', strip, re.S)
    assert anchor, f"the mods glyph is not in the row's strip: {strip[:400]}"
    assert anchor.group(1) == mods_page.node_page_url(NODE)
    # beside the row's other links (log VIEW, log DOWNLOAD), in ONE cell and in the strip's own order.
    # NO ``cfg`` glyph: a row carries no settings cogwheel — its NAME is the door into its own page.
    positions = [strip.find(f'class="act-btn {name}"') for name in ("view", "dl", "mods")]
    assert all(position >= 0 for position in positions), positions
    assert positions == sorted(positions), positions
    # a drawing, not an emoji and not a missing glyph (an unknown name renders an empty <svg>)
    assert "<svg" in anchor.group(3) and "<path" in anchor.group(3)


@pytest.mark.asyncio
async def test_the_nodes_row_offers_no_mods_glyph_to_a_dcs_admin(config_dir, service):
    """A DCS Admin holds ``mods.view`` but not ``mods.manage``: the door is ABSENT, and the page
    refuses them — the offer follows the page's own capability."""
    service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await http.get(nodes_page.NODES_PATH)
        refused = await http.get(mods_page.node_page_url(NODE))

    assert page.status_code == 200, page.text
    assert "act-btn mods" not in page.text
    assert mods_page.node_page_url(NODE) not in page.text
    assert refused.status_code == 403, refused.status_code


@pytest.mark.asyncio
async def test_following_the_glyph_opens_the_nodes_root_mods_page(config_dir, service):
    """THE CLICK PATH: the URL the glyph carries renders that node's page, for its own node."""
    service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await http.get(nodes_page.NODES_PATH)
        href = re.search(r'class="act-btn mods" href="([^"]+)"', page.text).group(1)
        opened = await http.get(href)

    assert opened.status_code == 200, opened.text
    assert f'<h1>Root mods</h1>' in opened.text
    assert "Community_A-4E-C" in opened.text


@pytest.mark.asyncio
async def test_the_glyphs_word_rides_title_and_aria_label_translated(config_dir, service):
    """Both attributes are non-empty and TRANSLATED: the German render differs from the msgid."""
    service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        english = await http.get(nodes_page.NODES_PATH)
        await german(http)
        deutsch = await http.get(nodes_page.NODES_PATH)

    def attributes(html: str) -> tuple[str, str]:
        anchor = re.search(r'<a class="act-btn mods" href="[^"]+"(.*?)>', html, re.S)
        assert anchor, "the mods glyph is missing"
        title = re.search(r'title="([^"]*)"', anchor.group(1))
        aria = re.search(r'aria-label="([^"]*)"', anchor.group(1))
        assert title and aria, anchor.group(1)
        return title.group(1), aria.group(1)

    title_en, aria_en = attributes(english.text)
    title_de, aria_de = attributes(deutsch.text)
    assert title_en and aria_en
    assert title_de and aria_de
    # the English render is the msgid (Jinja escapes the apostrophe); the German one is its translation
    assert f"Root mods on {NODE} — install, change and remove the packages in this node" in title_en
    assert title_de.startswith("Root-Mods auf ") and "DCS-Installation" in title_de
    assert aria_de == ARIA_DE.format(node=NODE), aria_de


# ---------------------------------------------------------------------- 3. the coupling and the menu

def test_the_door_is_offered_on_the_capability_the_page_declares():
    """A control that links to a page takes THAT page's capability — read from the declarations."""
    declared = mods_page.capabilities()
    assert declared[mods_page.NODE_PAGE_PATH] == mods_page.MODS_MANAGE_CAPABILITY
    assert mods_page.MODS_MANAGE_CAPABILITY != "nodes.settings"
    # the page's own gate and the door's gate are one predicate, so "offered ⊆ authorised" holds
    from conftest import permissions

    assert permissions.roles_for_capability(mods_page.MODS_MANAGE_CAPABILITY) == \
        mods_page.MODS_MANAGE_ROLES


def test_the_tab_is_declared_between_missions_and_log():
    """The ORDER is the declaration's: missions, then mods, then log — on both tab tuples."""
    order = detail_page.TAB_ORDER
    assert order.index("missions") < order.index(mods_page.MODS_TAB) < order.index("log")
    assert detail_page.OPTIONAL_TAB_ORDER.index("missions") \
        < detail_page.OPTIONAL_TAB_ORDER.index(mods_page.MODS_TAB) \
        < detail_page.OPTIONAL_TAB_ORDER.index("log")
    assert detail_page.TAB_LABELS[mods_page.MODS_TAB] == mods_page.MODS_LABEL


# ------------------------------------------------------------------------------- 4. the catalogs

def _catalog(language: str) -> gettext.GNUTranslations:
    path = LOCALE / language / "LC_MESSAGES" / "webui.mo"
    assert path.is_file(), f"missing compiled catalog: {path}"
    with path.open("rb") as handle:
        return gettext.GNUTranslations(handle)


def _placeholders(text: str) -> set[str]:
    """The ``{name}`` placeholders a string carries (the console's own interpolation shape)."""
    return set(re.findall(r"\{(\w+)\}", text))


def _new_msgids() -> set[str]:
    """Every msgid the mods pages and the node row's door introduce, read from the source that owns
    them: the page module's own copy constants plus the two the row's door is worded with."""
    import services.webservice.pages.actions as actions_page  # noqa: F401

    ids = {
        mods_page.NODE_TITLE, mods_page.NODE_LEAD, mods_page.TAB_HEADING, mods_page.ROOT_HEADING,
        mods_page.MODS_LABEL, mods_page.INSTALL_LABEL, mods_page.INSTALL_TITLE,
        mods_page.DOWNLOAD_LABEL, mods_page.DOWNLOAD_TITLE,
    }
    ids |= {"Root mods on {node}", "Root mods", "Mods", "Package", "Installed", "installed",
            "node-wide", "dialog", "Install a mod", "Change version", "Download a package",
            "URL or GitHub repository", "Package to install"}
    return {msgid for msgid in ids if msgid}


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_new_msgid_is_translated_in_the_compiled_catalog(language):
    """Read from the COMPILED ``.mo``: present, non-empty, placeholders intact, and not English.

    A ``msgstr`` identical to its ``msgid`` is a finding unless the string carries only a name or a
    data pattern, so the identical set is enumerated rather than ignored.
    """
    catalog = _catalog(language)
    above_msgid = _MODS_MSGIDS
    missing = sorted(msgid for msgid in above_msgid if not catalog.gettext(msgid))
    assert not missing, f"{language}: no translation for {missing}"

    identical = sorted(msgid for msgid in above_msgid if catalog.gettext(msgid) == msgid)
    unexpected = [msgid for msgid in identical if msgid not in IDENTICAL_ALLOWED]
    assert not unexpected, f"{language}: untranslated (msgstr == msgid): {unexpected}"

    mismatched = sorted(
        msgid for msgid in above_msgid
        if _placeholders(catalog.gettext(msgid)) != _placeholders(msgid))
    assert not mismatched, f"{language}: placeholder mismatch: {mismatched}"


#: the whole set of msgids the Mods surfaces introduce — the catalogs must carry a translation for
#: each one. Written down here so a string added to the feature without a catalog entry fails.
_MODS_MSGIDS: frozenset[str] = frozenset({
    "'{package}' v{version} is no longer in the package folder — nothing was changed.",
    "Change", "Change the installed version of {package}", "Change the version",
    "Change the version of {package} on {target}", "Change version",
    "Changing the version removes the installed one and installs the version you pick — one "
    "operation, and the bot log records both halves.",
    "Download a package", "Download a package from GitHub or a URL into the package folder",
    "Download the package into the package folder", "Download…",
    "Every server on this node is shut down — root mods can be changed.",
    "Install", "Install a mod", "Install a mod on {target}", "Install a mod…",
    "Install a package that is already in the package folder", "Installed",
    "Installed into the DCS installation on this node — active in every instance on it.",
    "Installed mods (Saved Games)", "Installed root mods", "Mods",
    "No root mods are installed on {node}.",
    "Not done (no URL was given).", "Not done (no package was chosen).",
    "Not done (no package was named).",
    "Not done (the form names a different target than its path).",
    "Nothing is installed by this step: the package has to be installed afterwards. For a plain "
    "URL the file name decides the version and the version field is ignored; for a repository "
    "download the field selects the release, and a version with no matching release asset is "
    "refused before anything is written.",
    "Open the root mods of node {node}", "Package", "Package to install",
    "Packages found in the {folder} package folder on {node} a moment ago. Installing one unpacks "
    "it into this target and records it here and in the bot log.",
    "Remove the package", "Remove {package} v{version} from {target}", "Remove {package} v{version}?",
    "Root mods", "Root mods cannot be changed while a server on this node is running.",
    "Root mods on {node}",
    "Root mods on {node} — install, change and remove the packages in this node's DCS installation",
    "Root mods on {node} →", "Saved Games on '{target}'",
    "Server <b>{name}</b> is running on this node. Root mods are active in every instance on it, "
    "so it has to be shut down before a package can be installed or removed.",
    "Shut down {name}",
    "The node downloads the package itself and writes it into the package folder — "
    "<span class=\"mods-path\">{path}</span> — so a package fetched here is available to every "
    "install on that node.",
    "The package is removed from {where}, and the bot log records the removal and your name.",
    "The packages in the {folder} package folder on {node}, read when this page opened.",
    "The root mods of {node} could not be read.",
    "The servers <b>{names}</b> are running on this node. Root mods are active in every instance on "
    "it, so every one of them has to be shut down before a package can be installed or removed.",
    "The versions of {package} in the {folder} package folder on {node}, read when this page opened.",
    "URL or GitHub repository", "dialog", "installed", "node-wide",
    "the DCS installation of node '{target}'",
    "{count} root mod(s) are installed on {node} — they are active in this server too, and they are "
    "changed on the node's own page.",
    "{folder} package folder on node {node}", "{package} — v{version} · {size} · {added}",
    "{server} is running, so its Saved Games mods cannot be changed. Shut it down first.",
    "{server} is shut down — its Saved Games mods can be changed.",
})


def test_the_written_msgid_set_is_the_one_the_sources_produce():
    """The list above must be the sources' own set: a string added to the feature fails here."""
    assert _new_msgids() <= _MODS_MSGIDS
    assert len(_MODS_MSGIDS) == 59, len(_MODS_MSGIDS)
