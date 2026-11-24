"""The site root always lands somewhere: with no public site served there, an anonymous visitor
reaches the console's login page instead of a bare 404.

THE DEFECT THIS PINS. The console's mount was decided from "is a ``sites:`` block configured"
(``bool(catalog)``) while the site's routes are registered per ENABLED site. A catalog whose root
site is switched off is non-empty, so the console moved to ``/console`` — and ``/`` was registered
by nobody at all: the site skipped it (disabled) and the console was no longer there. An anonymous
visitor got FastAPI's ``{"detail":"Not Found"}`` with no guard to run and no door to bounce into.
The same happened on an installation that declares only a GROUP site, whose pages live at
``/group1``: nothing owned ``/``.

WHAT IS PINNED, all through the real application:

* the root answers a 303 into the login door — exactly one hop, a PATH (never an absolute URL with a
  host), and the hop renders the console's own login form — for each declaration that serves no
  site at the root: no catalog, a switched-off root site, and a catalog of group sites only;
* the rule itself: :func:`services.webservice.mount.site_owns_root` counts SERVED sites only;
* THE CONTROLS, so a test that redirects unconditionally cannot pass: an enabled root site still
  answers ``/`` with the public site (200, its own document), and a SIGNED-IN operator with no site
  at all still gets the dashboard at ``/`` (200, no redirect).
"""
from __future__ import annotations

import types
from urllib.parse import urlparse

import pytest

from conftest import asgi_client, create_app, install_shell, password_fields
from services.webservice import mount, site_pages, sites
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import dashboard as dashboard_page
from test_webui_console_access import USERS, _config
from test_webui_dashboard import BOT_YAML, _login

HTML = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

#: the group token ``servers.yaml`` declares, so a group site parses.
GROUP = "5th Guards"
SERVERS_YAML = f"""\
5th Guards:
  managed_by:
    - {GROUP}
"""

#: every declaration that serves NO site at the root, by name. Each one leaves ``/`` to the console.
NO_ROOT_SITE = {
    "no-catalog": None,
    "root-switched-off": {"root": {"slug": "", "enabled": False,
                                   "branding": {"title": "Root"}}},
    "group-site-only": {"group1": {"slug": "group1", "group_role": GROUP}},
}

#: the control: an enabled root site owns ``/``.
ROOT_SITE = {"root": {"slug": "", "branding": {"title": "Root Site"}, "nav": ["overview"]}}

#: the root page's address
ROOT = "/"


@pytest.fixture(autouse=True)
def restore_console_mount():
    """The resolved mount is process state (``mount._PREFIX``): restore it around every test."""
    saved = mount._PREFIX
    try:
        yield
    finally:
        mount._PREFIX = saved


@pytest.fixture
def config_dir(tmp_path):
    """A config directory whose ``servers.yaml`` declares the group site's token."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


def build(config_dir, sites_block=None):
    """The real shell from the production factory, with *sites_block* as the ``sites:`` key."""
    app = create_app()
    config = dict(_config(USERS))
    if sites_block is not None:
        config["sites"] = sites_block
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config=config)
    return app


# ------------------------------------------------------------------- the rule, on its own

def test_the_mount_rule_counts_served_sites_only():
    """``site_owns_root`` reads the ``enabled`` flag the site's routes are registered by.

    Declared directly, so the one predicate the mount and the site's registration share is pinned
    without an application: a disabled root site owns no address, whatever the block declares.
    """
    def catalog(block):
        return sites.parse_sites(block, known_roles={GROUP})

    assert mount.site_owns_root(catalog(None)) is False, "no catalog serves no root"
    assert mount.site_owns_root(catalog({"root": {"slug": "", "enabled": False}})) is False, \
        "a switched-off root site registers no route, so it owns no address"
    assert mount.site_owns_root(catalog({"group1": {"slug": "group1",
                                                   "group_role": GROUP}})) is False, \
        "a group site is served at its own prefix, never at the site root"
    assert mount.site_owns_root(catalog({"root": {"slug": "", "enabled": False},
                                         "group1": {"slug": "group1",
                                                    "group_role": GROUP}})) is False, \
        "a disabled root site beside an enabled group site still leaves / to the console"
    assert mount.site_owns_root(catalog({"root": {"slug": ""}})) is True, \
        "an enabled root site owns the site root"


# ------------------------------------------------------- the root, for an anonymous visitor

@pytest.mark.parametrize("declaration", sorted(NO_ROOT_SITE), ids=sorted(NO_ROOT_SITE))
@pytest.mark.asyncio
async def test_an_anonymous_root_lands_on_the_login_page(config_dir, declaration):
    """One hop, a path, and the console's own login form at the end of it.

    The console keeps ``/`` whenever no site is served there, so the dashboard's own capability gate
    refuses the anonymous visitor and the shell bounces it into the door — the same answer an
    installation with no site at all has always given. Asserted for every declaration that serves no
    site at the root, because the defect was that these disagreed with the empty-catalog case.
    """
    app = build(config_dir, NO_ROOT_SITE[declaration])
    assert mount.console_prefix(app) == "", \
        f"{declaration}: the console must keep the site root when no site is served there"
    async with asgi_client(app) as client:
        response = await client.get(ROOT, headers=HTML)
        assert response.status_code == 303, \
            f"{declaration}: GET / answered {response.status_code}: {response.text[:200]!r}"
        location = response.headers["location"]
        # the target is a PATH: no scheme, no host, so an install behind a proxy prefix is not sent
        # to an address it does not serve
        assert location.startswith("/") and not location.startswith("//"), \
            f"{declaration}: the door must be a rooted path, not {location!r}"
        assert urlparse(location).netloc == "", \
            f"{declaration}: the door must not name a host: {location!r}"
        assert location == f"{auth_routes.LOGIN_PATH}?next=/&surface=/", \
            f"{declaration}: the door must carry the root as its target and its surface"
        # ONE hop: following it renders the login form and redirects no further
        page = await client.get(location, headers=HTML)
    assert page.status_code == 200, f"{declaration}: the door answered {page.status_code}"
    assert "location" not in page.headers, f"{declaration}: the door redirected again — a loop"
    assert f'action="{auth_routes.LOGIN_PATH}"' in page.text, \
        f"{declaration}: the door is not the console's login form"
    assert password_fields(page.text), f"{declaration}: the login form carries no password field"


@pytest.mark.asyncio
async def test_the_group_site_keeps_its_own_prefix_while_the_console_keeps_the_root(config_dir):
    """The console taking ``/`` must not take the group site's address with it."""
    app = build(config_dir, NO_ROOT_SITE["group-site-only"])
    assert site_pages.public_site_href(app) == "/group1"
    async with asgi_client(app) as client:
        page = await client.get("/group1", headers=HTML)
    assert page.status_code == 200, f"/group1 answered {page.status_code}: {page.text[:200]!r}"
    assert "does not have access" not in page.text


# ----------------------------------------------------------------------------- the controls

@pytest.mark.asyncio
async def test_an_enabled_root_site_still_serves_the_public_site_at_the_root(config_dir):
    """CONTROL: the public site owns ``/`` — a fix that redirects the root unconditionally fails."""
    app = build(config_dir, ROOT_SITE)
    assert mount.console_prefix(app) == mount.CONSOLE_PREFIX
    async with asgi_client(app) as client:
        response = await client.get(ROOT, headers=HTML)
        assert response.status_code == 200, f"the public site answered {response.status_code}"
        assert "Root Site" in response.text, "GET / did not serve the root site's own document"
        assert password_fields(response.text) == [], "GET / served the login form, not the site"
        # the console moved: its own root is the door's target, not the site's address
        console = await client.get(mount.CONSOLE_PREFIX, headers=HTML)
    assert console.status_code == 303
    assert console.headers["location"] == (
        f"{auth_routes.LOGIN_PATH}?next=/console&surface=/console")


@pytest.mark.asyncio
async def test_a_signed_in_operator_gets_the_dashboard_at_the_root_with_no_site(config_dir):
    """CONTROL: the root is the console's own page for a signed-in operator — no redirect at all."""
    app = build(config_dir, NO_ROOT_SITE["no-catalog"])
    async with asgi_client(app) as client:
        assert (await _login(client, "admin")).status_code == 303
        response = await client.get(ROOT, headers=HTML)
    assert response.status_code == 200, f"GET / answered {response.status_code}: " \
                                       f"{response.text[:200]!r}"
    assert "location" not in response.headers, "a signed-in operator was redirected away from /"
    assert dashboard_page.DASHBOARD_TITLE in response.text, \
        "GET / did not render the dashboard for a signed-in operator"
