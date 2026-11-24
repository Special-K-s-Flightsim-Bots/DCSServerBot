"""The Mods pages are REGISTERED BY THE SHELL: their routes, their gate and their declarations.

``pages/mods`` owns the node's root-mods page and both owners' dialogs; the shell is the ONE place
that decides which page modules the reserved surface consists of, so a page module it does not name
has no route at all — a link to it is a 404 and every dialog under ``/mods/...`` is unreachable.
This file pins the registration itself, driven through the REAL application (production factory, the
shell, the local login backend, the real ``modmanager`` actions and a stub cluster):

* the node page renders THROUGH THE APP for an ``Admin`` — 200 and the page module's own headings;
* a caller without ``mods.manage`` is REFUSED with the console's 403, not bounced to the login door;
* every path the page module declares is a route the registrar OWNS and gates on the capability the
  module declared for it, and the node page rides the shell's own capability map;
* the main menu stays the pinned eight items — the Mods pages contribute no nav entry.

The module's doubles and its harness are imported rather than copied: one app shape, one fake fleet.
"""
from __future__ import annotations

import pytest

from services.webservice import permissions, shell as shell_module
from services.webservice.pages import mods as mods_page
# the harness and the doubles, reused rather than copied
from test_webui_mods_pages import (  # noqa: F401 - fixtures are imported on purpose
    BLUE_FLAG, NODE, ROOT_ROWS, SAVED_ROWS, Cluster, RecordingService, build, client, config_dir,
    fleet, no_service, service, sign_in,
)

#: the node page's own headings — the markers a rendered page carries and a 404 cannot
PAGE_TITLE = "<h1>Root mods</h1>"
PAGE_ROW = "Community_A-4E-C"

#: the nine items the main menu is pinned to (``tests/test_webui_nav.py``)
MAIN_MENU = 12


# ------------------------------------------------------------------- 1. the node page renders

@pytest.mark.asyncio
async def test_the_node_mods_page_renders_through_the_app_for_an_admin(config_dir, service):
    """The shell names the page module, so ``GET /nodes/<node>/mods`` renders its own page."""
    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await http.get(mods_page.node_page_url(NODE))

    assert response.status_code == 200, response.text
    html = response.text
    assert PAGE_TITLE in html
    assert PAGE_ROW in html
    # the path the shell registered is the page module's own builder, never a second spelling of it
    assert response.url.path == mods_page.NODE_PAGE_PATH.replace("{node}", NODE)


# ------------------------------------------------------------- 2. the gate on that same path

@pytest.mark.parametrize("username", ["mod1", "plain"])
@pytest.mark.asyncio
async def test_the_node_mods_page_is_refused_without_mods_manage(config_dir, service, username):
    """A caller without ``mods.manage`` gets the console's 403 — not a redirect to the login door."""
    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, username)
        response = await http.get(mods_page.node_page_url(NODE))

    assert response.status_code == 403, response.status_code
    assert "location" not in {name.lower() for name in response.headers}
    assert PAGE_TITLE not in response.text
    assert PAGE_ROW not in response.text


# ------------------------------------------------- 3. the registered routes and declarations

def test_every_declared_mods_path_is_a_gated_route_of_the_app(config_dir):
    """Each path the module declares is a route the registrar OWNS, gated on its declared capability.

    Read off the application the shell builds (never the module's dict alone): a path the module
    declares but the shell never registers has no route here, which is the regression this pins.
    """
    app = build(config_dir, fleet())
    registrar = app.state.webui_registrar
    declared = mods_page.capabilities()

    assert declared[mods_page.NODE_PAGE_PATH] == mods_page.MODS_MANAGE_CAPABILITY
    by_path = {getattr(route, "path", None): route for route in registrar.routes}
    missing = sorted(path for path in declared if path not in by_path)
    assert not missing, f"declared but not registered: {missing}"

    unguarded = sorted(path for path, capability in declared.items()
                       if registrar.capability_for(route=by_path[path]) != capability)
    assert not unguarded, f"registered but not gated on the declared capability: {unguarded}"


def test_both_mods_capabilities_are_declared_and_the_node_page_is_in_the_shells_map():
    """Both names are declared with the module's roles, and the node page rides the shell's map.

    An undeclared capability is refused at registration time, so the pin that matters for the
    registration itself is the node page's entry in ``_core_router()``'s returned dict: a page
    registered without its declaration would be an ungated route.
    """
    _router, capabilities, _nav = shell_module._core_router()

    assert capabilities[mods_page.NODE_PAGE_PATH] == mods_page.MODS_MANAGE_CAPABILITY
    assert permissions.roles_for_capability(mods_page.MODS_MANAGE_CAPABILITY) == \
        mods_page.MODS_MANAGE_ROLES
    assert permissions.roles_for_capability(mods_page.MODS_VIEW_CAPABILITY) == \
        mods_page.MODS_VIEW_ROLES
    assert permissions.scope_grants(mods_page.MODS_VIEW_CAPABILITY) is True
    assert permissions.scope_grants(mods_page.MODS_MANAGE_CAPABILITY) is False


# --------------------------------------------------------------------- 4. the menu is untouched

def test_the_mods_pages_contribute_no_nav_item():
    """The Mods pages are reached from a row and a tab: the pinned menu keeps its items."""
    _router, _capabilities, nav = shell_module._core_router()

    assert len(nav) == MAIN_MENU, [item.label for item in nav]
    assert all(item.url != mods_page.NODE_PAGE_PATH for item in nav)
    assert mods_page.nav_items() == ()
