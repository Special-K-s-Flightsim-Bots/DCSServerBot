"""The sidebar: five named groups, a glyph per entry, and a heading that is a bare label.

Five facts, and none of them is proved by the others:

* the GROUP ORDER and the set are the shell's ONE constant, and the rail renders them in that order;
* a group whose visible items are all filtered away renders NO heading — an empty heading advertises
  a door that opens on nothing;
* a page naming a group the console does not have is REFUSED when it builds its nav item, and a page
  that names none is refused too;
* a glyph name the sprite does not carry renders an EMPTY span and never raises — a page added before
  its drawing exists still renders, it just draws nothing;
* every drawn glyph is ``aria-hidden``, because the entry is read by its label.

The group heading carries NO glyph and no icon column: it is a bare label on the rail's own gutter,
closed above by a hairline (``shell.css``, ``.nav .grp``), so nothing in a heading line stands in a
link's column.
"""
from __future__ import annotations

import gettext
import re
import types
from dataclasses import fields
from pathlib import Path

import pytest

from conftest import create_app, install_shell

from services.webservice import i18n, permissions
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.registry import NAV_GROUPS, NavGroup, NavItem

WEBUI = Path(dashboard_page.__file__).parent.parent
TEMPLATES = WEBUI / "templates"
SHELL_CSS = WEBUI / "static" / "shell.css"
LOCALE = Path(dashboard_page.__file__).resolve().parent.parent.parent.parent / "locale"

#: The groups, in the order the rail renders them. ONE list, read off the shell's constant below.
GROUP_ORDER: tuple[str, ...] = ("Operations", "Media", "Cluster", "Data", "Administration")

#: The console's own entries, group by group, as the Admin's rail shows them. Enumerated rather
#: than counted: a rail that quietly LOST an entry would pass a length check.
GROUPS_FOR_ADMIN: dict[str, tuple[str, ...]] = {
    "Operations": ("/", "/fleet", "/players", "/bans"),
    "Media": ("/music",),
    "Cluster": ("/nodes", "/audit"),
    "Data": ("/statistics", "/presets"),
    "Administration": ("/access", "/settings", "/settings/site"),
}

#: The group headings in EVERY shipped language, read from the real ``.mo`` catalogs — never the
#: ``.po``: a fuzzy or dropped entry renders the English msgid and would hide a missing translation.
GROUP_LABELS_BY_LANGUAGE: dict[str, tuple[str, ...]] = {
    "de": ("Betrieb", "Medien", "Cluster", "Daten", "Verwaltung"),
    "es": ("Operaciones", "Multimedia", "Clúster", "Datos", "Administración"),
    "ru": ("Операции", "Медиа", "Кластер", "Данные", "Администрирование"),
    "cn": ("运维", "媒体", "集群", "数据", "管理"),
}

RAIL = re.compile(r'<aside class="side">.*?</aside>', re.S)
ANCHOR = r'<a [^>]*href="{url}"[^>]*>(.*?)</a>'

#: ``shell.css`` with its comments removed — the rules, not the prose that explains them.
SHEET = re.sub(r"/\*.*?\*/", "",
               SHELL_CSS.read_text(encoding="utf-8"), flags=re.S)

#: The entry row's declarations, verbatim. A link label's own rule is NOT part of the heading's
#: change, so it is pinned as a fixed string: an edit that "fixes" a heading cannot quietly move a
#: link's padding, gap or column with it.
NAV_LINK_DECLARATIONS = ("display: flex; align-items: center; gap: 10px; padding: 8px 10px; "
                         "border-radius: var(--radius-s); color: var(--text-2);")


def _rule(selector: str) -> str:
    """The declarations of the FIRST rule whose selector is exactly ``selector``, normalised.

    Whitespace is collapsed so a re-wrap of the block cannot fail the pin; the declaration ORDER and
    every value are still compared verbatim.
    """
    match = re.search(r"(?m)^[ \t]*" + re.escape(selector) + r"[ \t]*\{([^}]*)\}", SHEET)
    assert match, f"shell.css declares no rule for {selector!r}"
    return " ".join(match.group(1).split())


def _rules(selector: str) -> list[str]:
    """The declarations of EVERY rule whose selector is exactly ``selector``, in document order.

    Same normalisation as :func:`_rule`: whitespace collapsed, declaration order and values verbatim.
    """
    bodies = re.findall(r"(?m)^[ \t]*" + re.escape(selector) + r"[ \t]*\{([^}]*)\}", SHEET)
    assert bodies, f"shell.css declares no rule for {selector!r}"
    return [" ".join(body.split()) for body in bodies]


@pytest.fixture(autouse=True)
def music_read_action_registered(monkeypatch):
    """The console under test runs as an installation with the music plugin.

    The Media entry is offered only while the music read action is registered; this suite imports no
    plugin module, so the registration is stubbed at the same seam the entry reads.
    """
    from core import actions as actions_module

    monkeypatch.setitem(actions_module._ACTIONS, "get_music_radios", lambda *args, **kwargs: None)


@pytest.fixture
def console():
    """The console's own shell: the registrar with its nine nav items."""
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True), config={})
    return app, registrar


def _request(app, *, language: str | None = None):
    request = types.SimpleNamespace()
    request.app = app
    request.cookies = {i18n.COOKIE_NAME: language} if language else {}
    request.headers = {}
    request.url = types.SimpleNamespace(path="/", query="")
    return request


def _render(app, groups, *, language: str | None = None) -> str:
    """``base.html`` rendered with these groups and nothing else — the chrome, no page."""
    return i18n.render(_request(app, language=language), "base.html", nav_groups=groups)


def _rail(html: str) -> str:
    match = RAIL.search(html)
    assert match, "base.html rendered no sidebar"
    return match.group(0)


def _anchor(rail: str, url: str) -> str:
    match = re.search(ANCHOR.format(url=re.escape(url)), rail, re.S)
    assert match, f"the rail renders no entry for {url}"
    return match.group(1)


def _headings(rail: str) -> list[str]:
    """The group headings of a rendered rail, in document order, with their markup stripped."""
    return [re.sub(r"<[^>]+>", "", body).strip()
            for body in re.findall(r'<div class="grp"[^>]*>(.*?)</div>', rail, re.S)]


# ------------------------------------------------------------------- the groups and their order

def test_the_group_set_and_its_order_are_one_constant():
    """The shell declares the groups once, in the order the rail renders them."""
    assert [group.name for group in NAV_GROUPS] == list(GROUP_ORDER)


def test_an_admin_gets_the_five_headings_in_order_with_their_own_entries(console):
    """Every entry appears under its own heading, and the headings are the five, in order."""
    app, registrar = console
    groups = dashboard_page.nav_groups(registrar, {"Admin"}, current="/")
    assert [(group["label"], tuple(link["url"] for link in group["links"])) for group in groups] == \
        list(GROUPS_FOR_ADMIN.items())
    assert _headings(_rail(_render(app, groups))) == list(GROUP_ORDER)


def test_the_groups_partition_the_registry_with_no_entry_in_two_places(console):
    """Grouping loses nothing and duplicates nothing: the entries are a partition of the nav."""
    _app, registrar = console
    groups = dashboard_page.nav_groups(registrar, {"Admin"}, current="/")
    seen = [link["url"] for group in groups for link in group["links"]]
    assert sorted(seen) == sorted(item.url for item in registrar.nav)
    assert len(seen) == len(set(seen)), f"an entry is offered under two headings: {seen}"


# --------------------------------------------------------- an empty group renders no heading

def test_a_group_with_nothing_visible_left_is_not_rendered(console):
    """Drive the capability filter until a whole group is empty: its heading must be ABSENT.

    Settings is Admin-only, so a DCS Admin is offered nothing under Administration — the heading has
    to go with its last entry rather than head an empty list. The generic invariant is asserted over
    BOTH identities and over a MANAGER (a scope, no role) too: the headings rendered are exactly the
    groups the visible entries name, never one more.
    """
    app, registrar = console
    for roles, manager in (({"Admin"}, False), ({"DCS Admin"}, False), (set(), True)):
        groups = dashboard_page.nav_groups(registrar, roles, current="/", manager=manager)
        offered = permissions.visible_nav(registrar.nav, roles, manager=manager)
        assert [group["label"] for group in groups] == \
            [name for name in GROUP_ORDER if any(item.group == name for item in offered)], \
            f"roles {sorted(roles) or '<none>'} (manager={manager}): the headings are not the " \
            f"groups the offered entries name"
        assert all(group["links"] for group in groups), "a rendered heading has no entry under it"

    filtered = dashboard_page.nav_groups(registrar, {"DCS Admin"}, current="/")
    assert "Administration" not in [group["label"] for group in filtered], \
        "Settings is Admin-only, so a DCS Admin must lose the Administration heading"
    rail = _rail(_render(app, filtered))
    assert _headings(rail) == [group["label"] for group in filtered]
    assert ">Administration<" not in rail


def test_no_viewer_at_all_renders_no_heading(console):
    """The floor: a viewer offered nothing gets an empty rail, not five headings over nothing."""
    app, registrar = console
    assert dashboard_page.nav_groups(registrar, {"GameMaster"}, current="/") == []
    assert _headings(_rail(_render(app, []))) == []


# ---------------------------------------------------------------- a group is declared, or refused

def test_an_unknown_group_name_is_refused_loudly():
    """A page naming a group outside the constant must not silently create a sixth heading."""
    with pytest.raises(ValueError) as refused:
        NavItem(label="Ghost", url="/ghost", capability=permissions.PUBLIC,
                group="Extras", glyph="grid")
    message = str(refused.value)
    assert "Extras" in message, "the refusal does not name the group it refused"
    assert "Administration" in message, "the refusal does not name the groups that exist"


def test_a_nav_item_without_a_glyph_is_refused():
    """Every entry has a glyph: a page cannot ship an entry that draws nothing at all."""
    with pytest.raises(ValueError):
        NavItem(label="Ghost", url="/ghost", capability=permissions.PUBLIC,
                group="Data", glyph="  ")


def test_every_declared_nav_item_has_a_known_group_and_a_glyph(console):
    """The ratchet over the registry: a new page cannot ship an ungrouped, unglyphed entry."""
    _app, registrar = console
    known = {group.name for group in NAV_GROUPS}
    offenders = [f"{item.label} ({item.url}) group={item.group!r} glyph={item.glyph!r}"
                 for item in registrar.nav
                 if item.group not in known or not str(item.glyph or "").strip()]
    assert offenders == [], f"nav items with no group or no glyph: {offenders}"


# ------------------------------------------------------------- an unknown glyph renders nothing

def test_an_unknown_glyph_renders_an_empty_span_without_raising(console):
    """A name the sprite does not carry draws NOTHING — no broken image, no tofu box, no exception.

    A heading's ``glyph`` key is a dead input now: the heading carries no span at all, so a name
    there can never reach the document.
    """
    app, _registrar = console
    groups = [{"label": "Data", "glyph": "no_such_group_drawing",
               "links": [{"label": "Ghost", "url": "/ghost", "active": False,
                          "glyph": "no_such_drawing"}]}]
    rail = _rail(_render(app, groups))

    entry = _anchor(rail, "/ghost")
    assert entry.startswith('<span class="ic">'), "the entry lost its fixed icon column"
    assert "<svg" not in entry and "<use" not in entry, "an unknown glyph drew something"
    assert "no_such_drawing" not in rail, "the unknown name leaked into the document"

    heading = re.search(r'<div class="grp"[^>]*>(.*?)</div>', rail, re.S).group(1)
    assert "<span" not in heading and "<svg" not in heading, "a group heading drew something"
    assert "no_such_group_drawing" not in rail, "the heading's glyph name reached the document"
    assert "Ghost" in rail, "the entry itself must still render"


def test_every_shipped_entry_draws_its_glyph(console):
    """The nine entries the console ships all name a drawing the sprite really carries."""
    app, registrar = console
    rail = _rail(_render(app, dashboard_page.nav_groups(registrar, {"Admin"}, current="/")))
    for urls in GROUPS_FOR_ADMIN.values():
        for url in urls:
            entry = _anchor(rail, url)
            assert '<use href="#i-' in entry, f"{url} draws no glyph"
    symbols = set(re.findall(r'<symbol id="i-([a-z_]+)"', _render(
        app, dashboard_page.nav_groups(registrar, {"Admin"}, current="/"))))
    assert len(symbols) == 12, f"the sprite is not the twelve symbols the design fixes: {symbols}"


# --------------------------------------------------------------------- decoration, never a name

def test_every_icon_is_aria_hidden(console):
    """Every glyph — entry and sprite symbol — is decoration; the label is the name."""
    app, registrar = console
    rail = _rail(_render(app, dashboard_page.nav_groups(registrar, {"Admin"}, current="/")))
    svgs = re.findall(r"<svg[^>]*>", rail)
    assert len(svgs) >= len(registrar.nav), \
        f"expected a glyph per entry, found {len(svgs)} svg(s) for {len(registrar.nav)} entries"
    assert [tag for tag in svgs if 'aria-hidden="true"' not in tag] == [], \
        "a glyph is exposed to a screen reader instead of being aria-hidden"


def test_the_active_entry_keeps_its_icon_and_wears_the_accent(console):
    """The row's icon survives the active state and takes the accent with the row."""
    app, registrar = console
    rail = _rail(_render(app, dashboard_page.nav_groups(registrar, {"Admin"}, current="/")))
    active = re.search(r'<a class="on" href="/">(.*?)</a>', rail, re.S)
    assert active, "the rail marks no active entry"
    assert '<span class="ic">' in active.group(1) and "<use" in active.group(1), \
        "the active entry lost its glyph"

    css = SHELL_CSS.read_text(encoding="utf-8")
    accent = re.search(r"\.nav a\.on \.ic \{[^}]*\}", css)
    assert accent, "the active row's icon is not restyled with the row"
    assert "var(--accent)" in accent.group(0), "the active row's icon does not wear the accent"


def test_the_group_heading_has_no_glyph_and_no_icon_column(console):
    """A heading is a bare label: the sheet names no glyph span for it, and the entries keep ONE
    fixed 18px column, so a link label starts on the same x whichever group it is in.

    Two halves, and neither implies the other — the markup emits no glyph span in a heading (pinned
    on a rendered rail below), and the sheet carries no selector that could draw one.
    """
    assert ".grp .g" not in SHEET, \
        "shell.css still carries a selector for a heading's glyph span"

    column, opacity = _rules(".nav a .ic")
    assert column == ("width: 18px; height: 18px; display: grid; place-items: center; "
                      "flex: 0 0 auto;"), column
    assert opacity == "opacity: .9;", opacity


def test_a_group_declares_its_name_and_nothing_else():
    """The heading's declaration is its msgid alone: no glyph field is left for a template to read."""
    assert [field.name for field in fields(NavGroup)] == ["name"]
    assert [group.name for group in NAV_GROUPS] == list(GROUP_ORDER)


# ------------------------------------------- the heading: the rail's gutter, a hairline, no column

def test_the_group_heading_is_a_label_on_the_gutter_closed_by_a_hairline():
    """The heading is a block of its own type on the rail's gutter, closed above by a 1px rule.

    The numbers are the design's; the point is that no declaration of the old row (flex, centred,
    a 10px gap to an icon) survives, because each of those is what made the label read as an entry.
    """
    rule = _rule(".nav .grp")
    assert "display: block" in rule, rule
    assert "display: flex" not in rule, rule
    assert "align-items" not in rule, rule
    assert "gap:" not in rule, rule
    assert "color: var(--text-3)" in rule, rule
    assert "font-size: 10px" in rule, rule
    assert "font-weight: 600" in rule, rule
    assert "text-transform: uppercase" in rule, rule
    assert "letter-spacing: .14em" in rule, rule
    assert "padding: 17px 2px 5px" in rule, rule
    assert "border-top: 1px solid var(--line)" in rule, rule


def test_the_first_group_heading_loses_the_hairline_under_the_brands_border(console):
    """The rail opens under the brand's own border, so its first heading keeps the air and no rule.

    The pin holds in two places: the sheet's rule, and the markup that makes its selector TRUE — the
    first ``.grp`` must be the ``.nav``'s own first element child, or ``:first-child`` would silently
    leave a hairline under the brand.
    """
    rule = _rule(".nav .grp:first-child")
    assert "padding-top: 4px" in rule, rule
    assert "border-top: 0" in rule, rule

    app, registrar = console
    rail = _rail(_render(app, dashboard_page.nav_groups(registrar, {"Admin"}, current="/")))
    nav = re.search(r'<nav class="nav">(.*?)</nav>', rail, re.S)
    assert nav, "base.html rendered no nav"
    assert re.match(r'\s*<div class="grp"', nav.group(1)), \
        "an element precedes the first group heading; `.nav .grp:first-child` would not match it"


def test_the_first_group_heading_carries_no_icon_span_and_an_entry_still_does(console):
    """The heading's line is the label alone; the entries below it keep the shared icon column.

    Read off a rail rendered from the REAL ``nav_groups``, so it holds with or without a glyph field
    on the heading: every heading is its label and nothing else, every entry opens on its span.
    """
    app, registrar = console
    rail = _rail(_render(app, dashboard_page.nav_groups(registrar, {"Admin"}, current="/")))

    headings = re.findall(r'<div class="grp"[^>]*>(.*?)</div>', rail, re.S)
    assert headings, "the rail renders no group heading"
    assert headings[0].strip() == "Operations", headings[0]
    assert [body for body in headings if "<span" in body or "<svg" in body] == [], \
        "a heading wears an icon span"

    entry = _anchor(rail, "/fleet")
    assert entry.startswith('<span class="ic">'), "the entry lost its icon span"
    assert '<use href="#i-' in entry, "the entry draws no glyph"


def test_the_heading_keeps_the_rails_air_above_the_next_hairline():
    """The last entry of a group keeps the rail's breathing room: 6px between it and the heading."""
    assert "margin-top: 6px" in _rule(".nav a + .grp")


def test_the_entry_row_rule_is_unchanged():
    """The links are NOT part of this change: their own rule is compared declaration for declaration.

    Every link label starts on x = 46px today (8px rail padding, 10px row padding, the 18px column,
    the 10px gap), so a heading fix that touched padding or gap would move every label in the rail.
    """
    assert _rule(".nav a") == NAV_LINK_DECLARATIONS


# ------------------------------------------------------------------------------- the chrome words

@pytest.mark.parametrize("language", sorted(GROUP_LABELS_BY_LANGUAGE))
def test_the_group_names_are_translated_in_every_catalog(language):
    """One language per case: the headings read from the compiled ``.mo``, exhaustively pinned."""
    assert LOCALE.is_dir(), f"no locale directory at {LOCALE}"
    translations = gettext.translation("webui", localedir=str(LOCALE), languages=[language],
                                       fallback=True)
    assert tuple(translations.gettext(name) for name in GROUP_ORDER) == \
        GROUP_LABELS_BY_LANGUAGE[language]


def test_a_german_rail_reads_the_german_headings(console):
    """The rail translates the group names at render — the catalog is what the reader sees."""
    app, registrar = console
    groups = dashboard_page.nav_groups(registrar, {"Admin"}, current="/")
    assert _headings(_rail(_render(app, groups, language="de"))) == \
        list(GROUP_LABELS_BY_LANGUAGE["de"])
