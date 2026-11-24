"""NAV IS DATA, and it is filtered by the SAME requirement its route declares.

Prevents a sidebar entry whose route answers 403 for the person offered it. Exactly one predicate --
:func:`services.webservice.permissions.allows` -- is used by both the access gate and the sidebar.
The assertions are made OVER THE SETS (per role set, over every nav item), not per item: a per-item
test passes while the pair at the next item is already broken. The role sets are driven THROUGH THE
REAL ASGI APP, so the answer comes from the gate, never a second reading of the capability table."""
from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest

from conftest import stub_identity, asgi_client, create_app, install_shell, permissions, simple_router
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import instances as instances_page
from services.webservice.pages import logs as logs_page
from services.webservice.readmodels import views as view_layer

PERMISSIONS_PY = (Path(__file__).resolve().parent.parent
                  / "services" / "webservice" / "permissions.py")
DASHBOARD_PY = (Path(__file__).resolve().parent.parent
                / "services" / "webservice" / "pages" / "dashboard.py")

#: every role set the "offered ⊆ authorised" sweep runs — including an empty one and one that
#: holds only a role no page names, so the sweep cannot pass by offering everything to everyone
ROLE_SETS: tuple[frozenset, ...] = (
    frozenset(),
    frozenset({"GameMaster"}),
    frozenset({"Alert"}),
    frozenset({"DCS"}),
    frozenset({"DCS Admin"}),
    frozenset({"Admin"}),
    frozenset({"Admin", "GameMaster"}),
    frozenset({"DCS", "Alert"}),
)


def a_second_page(registrar):
    """A probe contributor: two pages with two DIFFERENT capabilities, so the sweep has a set to
    compare instead of one item that is either shown or not."""
    router = simple_router("/probe/download", "download")
    capabilities = {"/probe/download": "probe.download", "/probe/settings": "probe.settings"}

    @router.get("/probe/settings")
    async def settings():
        return {"page": "settings"}

    from services.webservice.registry import NavItem
    registrar.register_pages(
        "probe", router, capabilities=capabilities,
        nav=[NavItem(label="Downloads", url="/probe/download", capability="probe.download",
                     group="Data", glyph="sliders"),
             NavItem(label="Settings", url="/probe/settings", capability="probe.settings",
                     group="Administration", glyph="cog")])


@pytest.fixture
def shell_with_pages():
    """A shell whose nav has THREE items (the dashboard plus the probe's two) and whose routes are
    registered — the floor the sweep needs to be meaningful."""
    permissions.declare_capability("probe.download", ("DCS", "DCS Admin", "Admin"))
    permissions.declare_capability("probe.settings", ("Admin",))
    app = create_app()
    registrar = install_shell(app, node=None, config={})
    a_second_page(registrar)
    return app, registrar


def _nav_is_registered(registrar) -> list:
    """The nav the registrar holds, with a floor: an empty nav would make every sweep vacuous."""
    items = registrar.nav
    assert len(items) >= 3, f"floor: expected the dashboard + the probe's two pages, got {items}"
    return items


# ------------------------------------------------------- one requirement, declared once

def test_every_nav_item_declares_the_requirement_of_the_route_it_points_at(shell_with_pages):
    """The nav item and the route read the SAME declaration: the route's capability is the item's,
    and the item's url is a path the registrar actually owns."""
    _app, registrar = shell_with_pages
    registered = {getattr(route, "path", None) for route in registrar.routes}
    for item in _nav_is_registered(registrar):
        assert item.url in registered, f"nav offers '{item.label}' at {item.url}, which is no route"
        declared = registrar.capability_for(path=item.url)
        assert declared == item.capability, (
            f"nav item '{item.label}' ({item.url}) declares '{item.capability}' but its route "
            f"declares '{declared}' - the pair has diverged")


def test_the_nav_predicate_is_the_gates_predicate(shell_with_pages):
    """PIN: the access gate calls ``permissions.allows``, the one predicate the sidebar uses.

    Structural because the alternative — the gate keeping its own inline intersection test while
    the nav calls ``allows`` — is exactly the two-spellings drift this design forbids, and it would
    still pass every behavioural test on the day both happen to agree.
    """
    tree = ast.parse(PERMISSIONS_PY.read_text(encoding="utf-8"))
    gate = next(node for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "capability_gate")
    called = {node.func.id for node in ast.walk(gate)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "allows" in called, \
        "capability_gate must decide through permissions.allows, not with its own intersection"


def test_the_nav_predicate_can_also_say_no(shell_with_pages):
    """Positive control: the predicate is not a constant. Were ``allows`` to return True for
    everything, the sweep below would pass while the sidebar advertised every page to everyone."""
    assert permissions.allows(dashboard_page.DASHBOARD_CAPABILITY,
                              dashboard_page.DASHBOARD_ROLES) is True
    assert permissions.allows(dashboard_page.DASHBOARD_CAPABILITY, {"GameMaster"}) is False
    assert permissions.allows("nothing.declared.this", {"Admin"}) is False


# ------------------------------------------- accepted 5: offered is a subset of authorised

@pytest.mark.asyncio
@pytest.mark.parametrize("roles", ROLE_SETS, ids=lambda roles: "+".join(sorted(roles)) or "anonymous")
async def test_offered_is_a_subset_of_authorised_for_every_role_set(shell_with_pages, roles):
    """Over the sets, through the real gate: whatever the nav OFFERS must answer 200, and whatever
    it withholds must answer 403. Both directions, every role set, every item."""
    app, registrar = shell_with_pages
    stub_identity(roles)
    items = _nav_is_registered(registrar)
    offered = permissions.visible_nav(items, roles)
    authorised = [item for item in items if permissions.allows(item.capability, roles)]

    assert offered == authorised, "the sidebar and the capability table disagree"
    assert set(offered) <= set(authorised)          # the offered ⊆ authorised assertion

    async with asgi_client(app) as client:
        for item in items:
            response = await client.get(item.url)
            label = f"'{item.label}' ({item.url}) for roles {sorted(roles) or ['<none>']}"
            if item in offered:
                assert response.status_code == 200, f"the nav offers {label} but the route says " \
                                                    f"{response.status_code}"
            else:
                assert response.status_code == 403, f"the nav withholds {label} but the route " \
                                                    f"answered {response.status_code}"


@pytest.mark.asyncio
async def test_a_role_without_the_capability_sees_no_item_and_its_route_refuses(shell_with_pages):
    """The single case, spelled out: GameMaster is a real role this bot defines and no page names
    it, so it is offered nothing and every page refuses it - including the dashboard at the root."""
    app, registrar = shell_with_pages
    roles = frozenset({"GameMaster"})
    stub_identity(roles)

    assert permissions.visible_nav(registrar.nav, roles) == []
    assert dashboard_page.nav_groups(registrar, roles, current="/") == [], \
        "the sidebar rendered a group for a viewer who may be offered nothing"

    async with asgi_client(app) as client:
        assert (await client.get("/")).status_code == 403

    # the other direction, so the assertions above are not the only thing proved. The console's OWN
    # pages now come between the root and the probe's pages: the main-menu items only
    # (Dashboard, Servers, Nodes, Statistics, Players, Music, Bans, Audit, Presets, Settings), in the
    # order the shell registers them.
    admin = frozenset({"Admin"})
    stub_identity(admin)
    shown = permissions.visible_nav(registrar.nav, admin)
    assert [item.url for item in shown] == ["/", "/fleet", "/nodes", "/statistics", "/players",
                                            "/music", "/bans", "/audit", "/presets", "/access",
                                            "/settings", "/settings/site",
                                            "/probe/download", "/probe/settings"]
    async with asgi_client(app) as client:
        assert (await client.get("/")).status_code == 200


def test_the_sweep_compares_different_sets_for_different_roles(shell_with_pages):
    """Anti-vacuity for the sweep: if every role set produced the same offer, the parametrised test
    would be one test repeated. A DCS-only viewer must not be offered the Admin-only page."""
    _app, registrar = shell_with_pages
    dcs = {item.url for item in permissions.visible_nav(registrar.nav, {"DCS"})}
    admin = {item.url for item in permissions.visible_nav(registrar.nav, {"Admin"})}
    assert dcs and dcs < admin, f"the offered sets do not discriminate: DCS {dcs}, Admin {admin}"
    assert "/probe/settings" in admin - dcs


def test_the_dashboard_never_enumerates_the_app_route_table():
    """Requirement 7: ``app.routes`` is blind to included routers and to ``_frontend_routes`` (the
    pinned FastAPI keeps them inside wrappers), so a page that walked it would silently examine
    nothing. The nav comes from the registrar; nothing in the page touches ``app.routes``."""
    source = DASHBOARD_PY.read_text(encoding="utf-8")
    assert ".routes" not in source, "the dashboard must not enumerate a route table - read the registrar"
    assert "app.routes" not in source


# ------------------------------------------- a menu entry reads apart from its neighbours

#: every language that ships a ``webui`` catalog.
_CATALOG_LANGUAGES: tuple[str, ...] = ("en", "de", "es", "ru", "cn")


def test_no_two_visible_nav_labels_are_equal_in_any_language():
    """Observed behaviour: the German menu showed "Übersicht" TWICE.

    A page whose nav item resolved another page's ``msgstr`` (the standalone server-overview page once
    reused the ``Overview`` msgid) made the menu show one word for two entries. That page is gone now,
    but the rule stands: every visible nav label must be DISTINCT in EVERY shipped language, which a
    reused msgid cannot be — so this test is the guard against a future page reusing another's label.

    The labels are read through ``gettext`` on the REAL ``.mo`` catalogs (never the ``.po``: a fuzzy or
    dropped entry renders the English msgid and would hide a collision), over the console's OWN nav —
    the sidebar the registrar builds, with no probe contributor in the set.
    """
    import gettext

    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True), config={})
    items = list(registrar.nav)
    assert len(items) >= 4, f"floor: expected the console's own pages, got {[i.label for i in items]}"

    locale_dir = Path(__file__).resolve().parent.parent / "locale"
    for language in _CATALOG_LANGUAGES:
        translations = gettext.translation("webui", localedir=str(locale_dir),
                                           languages=[language], fallback=True)
        labels = [translations.gettext(item.label) for item in items]
        duplicates = sorted({label for label in labels if labels.count(label) > 1})
        assert not duplicates, (
            f"{language}: the menu shows the same label for two entries: {duplicates} "
            f"(labels: {labels})")


# ------------------------------------------ the settled main menu, entry by entry

#: THE main menu, in the order the shell registers it: name -> url.
MAIN_MENU: tuple[tuple[str, str], ...] = (
    ("Dashboard", "/"),
    ("Servers", "/fleet"),
    ("Nodes", "/nodes"),
    ("Statistics", "/statistics"),
    ("Players", "/players"),
    ("Music", "/music"),
    ("Bans", "/bans"),
    ("Audit", "/audit"),
    ("Presets", "/presets"),
    ("Access", "/access"),
    ("Settings", "/settings"),
    ("Public site", "/settings/site"),
)

#: The pages whose content the Dashboard already carries. They are still PAGES (their routes
#: stay), but they must have NO nav entry anywhere and must not be advertised as console pages.
REMOVED_FROM_MENU: tuple[tuple[str, str], ...] = (
    ("Logs", logs_page.LOGS_PATH),
    ("Instances", instances_page.INSTANCES_PATH),
)

#: The menu labels TRANSLATED, per shipped language, read from the real .mo catalogs. Pinned
#: exhaustively so a dropped or fuzzy entry (which renders the English msgid) fails here.
MENU_LABELS_BY_LANGUAGE: dict[str, tuple[str, ...]] = {
    "en": ("Dashboard", "Servers", "Nodes", "Statistics", "Players", "Music", "Bans", "Audit",
           "Presets", "Access", "Settings", "Public site"),
    "de": ("Übersicht", "Server", "Knoten", "Statistik", "Spieler", "Musik", "Sperren", "Audit-Log",
           "Missions-Presets", "Zugriff", "Einstellungen", "Öffentliche Site"),
    "es": ("Panel", "Servidores", "Nodos", "Estadísticas", "Jugadores", "Música", "Prohibiciones",
           "Auditoría", "Preajustes de misión", "Acceso", "Ajustes", "Sitio público"),
    "ru": ("Обзор", "Серверы", "Узлы", "Статистика", "Игроки", "Музыка", "Баны", "Аудит",
           "Пресеты миссий", "Доступ", "Настройки", "Публичный сайт"),
    "cn": ("仪表盘", "服务器", "节点", "统计", "玩家", "音乐", "封禁", "审计", "任务预设", "访问权限", "设置",
           "公开站点"),
}


def _console_registrar():
    """The CONSOLE's own shell (no probe contributor): the main menu and nothing else."""
    app = create_app()
    return install_shell(app, node=types.SimpleNamespace(master=True), config={})


def test_the_main_menu_is_exactly_dashboard_servers_nodes_players_bans_presets_settings():
    """The main menu is Dashboard, Servers, Nodes, Statistics, Players, Music, Bans, Presets, Access,
    Settings — in that order, nothing else. The order is the shell's REGISTRATION order; the rail
    re-groups it (`test_webui_nav_groups.py` pins the headings)."""
    registrar = _console_registrar()
    assert [(item.label, item.url) for item in registrar.nav] == list(MAIN_MENU), \
        ("the main menu is not exactly Dashboard, Servers, Nodes, Players, Bans, Presets, "
         "Settings, in order")


@pytest.mark.parametrize("label, path", REMOVED_FROM_MENU,
                         ids=[path for _label, path in REMOVED_FROM_MENU])
def test_a_removed_page_has_no_nav_entry_anywhere(label, path):
    """Instances and Logs are off the menu: no nav item by url OR by label, and they are
    not console PAGES either (``page_paths`` reads the nav items), so no write can target them."""
    registrar = _console_registrar()
    assert path not in [item.url for item in registrar.nav], f"{path} is still a nav item"
    assert label not in [item.label for item in registrar.nav], f"'{label}' is still a nav label"
    assert path not in registrar.page_paths, f"{path} is still advertised as a console page"


@pytest.mark.parametrize("language", sorted(MENU_LABELS_BY_LANGUAGE))
def test_the_four_menu_labels_in_every_shipped_language(language):
    """One language per case: the menu reads the SAME items in the same order in every catalog,
    through the real .mo (never the .po)."""
    import gettext

    registrar = _console_registrar()
    locale_dir = Path(__file__).resolve().parent.parent / "locale"
    translations = gettext.translation("webui", localedir=str(locale_dir),
                                       languages=[language], fallback=True)
    labels = tuple(translations.gettext(item.label) for item in registrar.nav)
    assert labels == MENU_LABELS_BY_LANGUAGE[language], labels


def test_the_menu_labels_match_the_dashboard_tab_labels_where_they_name_the_same_resource():
    """NAV_LABEL (the sidebar) and the dashboard's TABLE_LABELS are two separate lists; where they
    name the SAME resource (Servers, Nodes) they must say the SAME word, in EVERY language."""
    import gettext

    registrar = _console_registrar()
    by_url = {item.url: item.label for item in registrar.nav}
    locale_dir = Path(__file__).resolve().parent.parent / "locale"
    for url, table_key in (("/fleet", "servers"), ("/nodes", "nodes")):
        assert by_url[url] == view_layer.TABLE_LABELS[table_key], \
            f"{url}: the menu label and the dashboard tab label have drifted"
        for language in MENU_LABELS_BY_LANGUAGE:
            translations = gettext.translation("webui", localedir=str(locale_dir),
                                               languages=[language], fallback=True)
            assert (translations.gettext(by_url[url])
                    == translations.gettext(view_layer.TABLE_LABELS[table_key])), \
                f"{url}: '{language}' renders the two lists differently"
