"""Applying ``site.yaml`` rebuilds the public route table WITHOUT restarting the application.

WHAT IS PINNED, all through the real application object (the same app is used before and after every
apply — nothing is rebuilt, restarted or re-installed):

* disabling a site makes every address it served answer 404 — its pages, the search JSON, the
  preview, the theme sheet, the logo and the avatar routes — and enabling it serves them again;
* a changed ``slug`` serves at the new prefix and 404s at the old one;
* when the enabled set gains or loses the root site, ``/`` moves between the public site and the
  console: the console's mount is republished, the dashboard follows, and the "open the public site"
  link follows;
* the capability declarations follow the table, in both directions: a path that disappears is no
  longer ``public`` and a path that appears is declared BEFORE the table can answer it;
* two applies in a row leave exactly one route per path (the counts are quoted);
* a raise between the console's move, the site's swap and the mount's republish restores all three
  — the console's prefix and the published mount still agree, and `/` answers what it answered
  before the failed apply, in BOTH directions of the root handover;
* the CONTROL: a tree nobody applied to serves exactly as it did at install.

A live bot is never restarted and no browser is opened: every assertion drives the ASGI app.
"""
from __future__ import annotations

import types
from urllib.parse import urlparse

import pytest

from conftest import asgi_client, create_app, install_shell
from services.webservice import mount, permissions, site_pages
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import dashboard as dashboard_page
from test_webui_console_access import USERS, _config
from test_webui_dashboard import _login

HTML = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

#: ``servers.yaml`` declares the token the group site's ``group_role`` names.
SERVERS_YAML = """\
5th Guards:
  managed_by:
    - 5th Guards
"""

BOT_YAML = """\
roles:
  Admin:
    - admin
"""

ROOT = "/"
CONSOLE = mount.CONSOLE_PREFIX

#: site-only addresses: every one answers 404 when the site is switched off. ``/players`` is NOT
#: here — the console has its own Players page at that path, so it answers the console's gate
#: instead of a 404 when the site is gone.
SITE_ONLY_PATHS = ("/rankings", "/theme.css", "/logo.png", "/avatar.png", "/search.json",
                   "/chrome-preview", "/player/u-nobody")
#: the site-only addresses that answer 200 while the site is enabled (the logo/theme are declared
#: by the fixtures below; the avatar route is 404 without an uploaded mark, by design).
ENABLED_200_PATHS = ("/rankings", "/theme.css", "/logo.png", "/chrome-preview")


def root_site(*, enabled: bool = True) -> str:
    return (
        "sites:\n"
        "  root:\n"
        '    slug: ""\n'
        f"    enabled: {str(enabled).lower()}\n"
        "    branding:\n"
        "      title: Root Site\n"
        "      logo: logo.png\n"
        "    theme:\n"
        '      expert: "--x: 1;"\n'
        "    nav: [overview, leaderboard]\n"
    )


def group_site(slug: str = "group1", *, enabled: bool = True) -> str:
    return (
        "sites:\n"
        "  group1:\n"
        f"    slug: {slug}\n"
        f"    enabled: {str(enabled).lower()}\n"
        '    group_role: "5th Guards"\n'
        "    branding:\n"
        "      title: Group One\n"
        "      logo: logo.png\n"
        "    theme:\n"
        '      expert: "--x: 1;"\n'
        "    nav: [overview, leaderboard]\n"
    )


def both_sites(*, root_enabled: bool = True, slug: str = "group1") -> str:
    return (
        "sites:\n"
        "  root:\n"
        '    slug: ""\n'
        f"    enabled: {str(root_enabled).lower()}\n"
        "    branding:\n"
        "      title: Root Site\n"
        "    nav: [overview, leaderboard]\n"
        "  group1:\n"
        f"    slug: {slug}\n"
        "    branding:\n"
        "      title: Group One\n"
        "      logo: logo.png\n"
        "    theme:\n"
        '      expert: "--x: 1;"\n'
        '    group_role: "5th Guards"\n'
        "    nav: [overview, leaderboard]\n"
    )


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
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return tmp_path


def write_site(config_dir, text: str):
    (config_dir / "site.yaml").write_text(text, encoding="utf-8")


def build(config_dir):
    """The real shell, from the production factory, over the config directory's own site.yaml."""
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config=dict(_config(USERS)))
    return app


def rebuild(app):
    """The apply path's own call: the registrar rebuilds the site table in place."""
    registrar = app.state.webui_registrar
    return site_pages.rebuild(registrar, app)


async def get(client, path, *, _headers=HTML):
    return await client.get(path, headers=_headers)


# --------------------------------------------------------------- enable / disable, no restart

@pytest.mark.asyncio
async def test_disabling_a_site_404s_its_pages_and_re_enabling_serves_them(config_dir):
    write_site(config_dir, root_site())
    app = build(config_dir)
    assert mount.console_prefix(app) == CONSOLE
    async with asgi_client(app) as client:
        assert (await get(client, "/rankings")).status_code == 200
        assert (await get(client, ROOT)).status_code == 200

        write_site(config_dir, root_site(enabled=False))
        rebuild(app)
        assert mount.console_prefix(app) == "", "the console must take back the site root"
        for path in SITE_ONLY_PATHS:
            assert (await get(client, path)).status_code == 404, path
        landing = await get(client, ROOT)
        assert landing.status_code == 303, "GET / must leave the site for the console's door"

        write_site(config_dir, root_site())
        rebuild(app)
        assert mount.console_prefix(app) == CONSOLE
        for path in ENABLED_200_PATHS:
            assert (await get(client, path)).status_code == 200, path
        assert (await get(client, ROOT)).status_code == 200


@pytest.mark.asyncio
async def test_a_slug_change_serves_the_new_prefix_and_404s_the_old_one(config_dir):
    write_site(config_dir, group_site("group1"))
    app = build(config_dir)
    registrar = app.state.webui_registrar
    async with asgi_client(app) as client:
        assert (await get(client, "/group1/rankings")).status_code == 200

        write_site(config_dir, group_site("squad-x"))
        rebuild(app)
        assert (await get(client, "/group1/rankings")).status_code == 404
        assert (await get(client, "/squad-x/rankings")).status_code == 200
        assert (await get(client, "/squad-x/theme.css")).status_code == 200

    # the capability declarations follow the table in BOTH directions
    assert registrar.capability_for(path="/group1/rankings") is None, \
        "the old prefix kept a stale public declaration"
    assert registrar.capability_for(path="/squad-x/rankings") == permissions.PUBLIC
    assert registrar.capability_for(path="/group1/theme.css") is None
    assert registrar.capability_for(path="/squad-x/theme.css") == permissions.PUBLIC


# ------------------------------------------------------------------- the root moves, no restart

@pytest.mark.asyncio
async def test_the_root_toggle_moves_slash_between_the_site_and_the_console(config_dir):
    write_site(config_dir, both_sites())
    app = build(config_dir)
    async with asgi_client(app) as client:
        assert mount.console_prefix(app) == CONSOLE
        root = await get(client, ROOT)
        assert root.status_code == 200 and "Root Site" in root.text
        assert (await get(client, "/group1")).status_code == 200
        assert site_pages.public_site_href(app) == "/"

        write_site(config_dir, both_sites(root_enabled=False))
        rebuild(app)
        assert mount.console_prefix(app) == ""
        landing = await get(client, ROOT)
        assert landing.status_code == 303, "the root must fall back to the console's door"
        assert urlparse(landing.headers["location"]).netloc == ""
        assert landing.headers["location"] == f"{auth_routes.LOGIN_PATH}?next=/&surface=/"
        assert (await get(client, "/group1")).status_code == 200, "the group site kept its prefix"
        assert site_pages.public_site_href(app) == "/group1", \
            "the console's open-the-public-site link must follow the enabled set"
        # ...and the dashboard RENDERS it: the console is at "/" now, so the link is in the page.
        assert (await _login(client, "admin")).status_code == 303
        dashboard = await get(client, ROOT)
        assert dashboard.status_code == 200
        assert 'href="/group1"' in dashboard.text, \
            "the dashboard did not carry the link to the one enabled site"

        write_site(config_dir, both_sites())
        rebuild(app)
        assert mount.console_prefix(app) == CONSOLE
        root = await get(client, ROOT)
        assert root.status_code == 200 and "Root Site" in root.text
        console = await get(client, CONSOLE)
        assert console.status_code == 200, "the signed-in operator lands on the console's dashboard"
        assert dashboard_page.DASHBOARD_TITLE in console.text


# ------------------------------------------------------------------------ one route per path

def _duplicate_paths(registrar) -> list[str]:
    paths = [getattr(route, "path", "") or "" for route in registrar.routes
             if "GET" in (getattr(route, "methods", None) or ())]
    return sorted({path for path in paths if paths.count(path) > 1})


@pytest.mark.asyncio
async def test_two_applies_leave_exactly_one_route_per_path(config_dir):
    write_site(config_dir, both_sites())
    app = build(config_dir)
    registrar = app.state.webui_registrar
    async with asgi_client(app) as client:
        assert (await get(client, "/rankings")).status_code == 200
        assert (await get(client, "/group1/rankings")).status_code == 200
    routes = len(registrar.routes)
    capabilities = registrar.capability_count
    owners = registrar.owners
    page_paths = registrar.page_paths

    rebuild(app)
    rebuild(app)

    assert len(registrar.routes) == routes, "a second apply duplicated route declarations"
    assert registrar.route_count == routes
    assert registrar.capability_count == capabilities, "a stale capability survived the apply"
    assert registrar.owners == owners
    assert registrar.page_paths == page_paths
    assert _duplicate_paths(registrar) == [], "the same path is registered twice"


@pytest.mark.asyncio
async def test_disabling_every_site_retires_the_owner_and_its_declarations(config_dir):
    write_site(config_dir, root_site())
    app = build(config_dir)
    registrar = app.state.webui_registrar
    assert site_pages.OWNER in registrar.owners
    assert registrar.capability_for(path="/rankings") == permissions.PUBLIC

    write_site(config_dir, "sites: {}\n")
    rebuild(app)

    assert site_pages.OWNER not in registrar.owners
    assert registrar.capability_for(path="/rankings") is None
    assert registrar.serves("/rankings") is False
    assert mount.console_prefix(app) == ""


# ------------------------------------------------------------------------------- the control

@pytest.mark.asyncio
async def test_with_no_apply_the_tree_serves_exactly_as_at_install(config_dir):
    """CONTROL: a pin that rebuilt on every request (or applied on import) would fail this."""
    write_site(config_dir, both_sites())
    app = build(config_dir)
    registrar = app.state.webui_registrar
    routes = len(registrar.routes)
    capabilities = registrar.capability_count
    async with asgi_client(app) as client:
        assert (await get(client, ROOT)).status_code == 200
        assert (await get(client, "/rankings")).status_code == 200
        assert (await get(client, "/group1/rankings")).status_code == 200
        assert (await get(client, "/group1/theme.css")).status_code == 200
    assert mount.console_prefix(app) == CONSOLE
    assert len(registrar.routes) == routes, "a request rebuilt the route table"
    assert registrar.capability_count == capabilities
    assert _duplicate_paths(registrar) == []


# ------------------------------------------- a raise mid-apply leaves the installed tree serving

def _site_state(registrar, app) -> tuple[str, str]:
    """The two halves of "who answers `/`": the console's registrations and the published mount."""
    return registrar.console_prefix, mount.console_prefix(app)


@pytest.mark.asyncio
async def test_a_raise_after_the_console_moved_leaves_the_old_tree_serving(config_dir, monkeypatch):
    """A raise between the console's move and the site's swap must not leave `/` answering 404.

    The reviewer's exact case: a group site is installed, so the console owns `/`; the next file
    adds an enabled root site, so the console must move to ``/console`` first — and the swap then
    raises. Without the rollback ``registrar.console_prefix`` is ``/console`` while the published
    mount is ``""``: ``/`` is claimed by nobody and every console page 404s at its old address.
    """
    write_site(config_dir, group_site("group1"))
    app = build(config_dir)
    registrar = app.state.webui_registrar
    assert _site_state(registrar, app) == ("", ""), "the fixture must start with the console at /"
    routes = len(registrar.routes)

    async with asgi_client(app) as client:
        before = await get(client, ROOT)
        assert before.status_code == 303, "the console's own door answers / before the apply"

        real_replace = registrar.replace_owner
        calls = {"n": 0}

        def replace_then_raise_once(owner, router=None, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:            # the site's swap, AFTER move_console has already run
                raise RuntimeError("a peer registered /rankings since install")
            return real_replace(owner, router, **kwargs)

        monkeypatch.setattr(registrar, "replace_owner", replace_then_raise_once)
        write_site(config_dir, both_sites())
        with pytest.raises(RuntimeError):
            rebuild(app)
        monkeypatch.setattr(registrar, "replace_owner", real_replace)

        assert _site_state(registrar, app) == ("", ""), \
            "the console's prefix and the published mount disagree after a failed apply"
        after = await get(client, ROOT)
        assert (after.status_code, after.headers.get("location")) == \
               (before.status_code, before.headers.get("location")), \
            "/ must still answer what it answered before the failed apply"
        assert (await get(client, CONSOLE)).status_code == 404, \
            "/console must not serve the console tree when the console is at /"
        assert (await get(client, "/group1/rankings")).status_code == 200, \
            "the previously installed site table must keep serving"
        assert len(registrar.routes) == routes, "the rollback left the route table changed"
        assert _duplicate_paths(registrar) == []

        # nothing is frozen: the next successful apply takes the root
        rebuild(app)
        assert _site_state(registrar, app) == (CONSOLE, CONSOLE)
        assert mount.url("/dashboard", app=app) == CONSOLE + "/dashboard"
        root = await get(client, ROOT)
        assert root.status_code == 200 and "Root Site" in root.text
        assert (await get(client, "/group1")).status_code == 200


@pytest.mark.asyncio
async def test_a_raise_on_the_console_taking_back_the_root_leaves_the_old_tree_serving(
        config_dir, monkeypatch):
    """The mirror direction: the site leaves `/`, the swap lands, and the console's move back raises.

    ``/`` is claimed by nobody at that instant — the site dropped it, the console never took it. The
    rollback must put the root site's table back AND leave the console where it was, so `/` answers
    the site and ``/console`` still answers the console.
    """
    write_site(config_dir, both_sites())
    app = build(config_dir)
    registrar = app.state.webui_registrar
    assert _site_state(registrar, app) == (CONSOLE, CONSOLE)
    routes = len(registrar.routes)

    async with asgi_client(app) as client:
        before = await get(client, ROOT)
        assert before.status_code == 200 and "Root Site" in before.text

        real_move = registrar.move_console
        calls = {"n": 0}

        def move_then_raise_once(new_prefix):
            calls["n"] += 1
            if calls["n"] == 1:            # the console's move back to /, AFTER the swap
                raise RuntimeError("a peer claimed /players since install")
            return real_move(new_prefix)

        monkeypatch.setattr(registrar, "move_console", move_then_raise_once)
        write_site(config_dir, group_site("squad-x"))
        with pytest.raises(RuntimeError):
            rebuild(app)
        monkeypatch.setattr(registrar, "move_console", real_move)

        assert _site_state(registrar, app) == (CONSOLE, CONSOLE), \
            "the console's prefix and the published mount disagree after a failed apply"
        after = await get(client, ROOT)
        assert after.status_code == 200 and "Root Site" in after.text, \
            "/ must still answer the site it answered before the failed apply"
        assert (await get(client, "/rankings")).status_code == 200, \
            "the root site's own pages must be back"
        assert (await get(client, "/group1")).status_code == 200, \
            "the previously installed slug must keep serving"
        assert (await get(client, "/squad-x")).status_code == 404, \
            "the file that failed to apply must not be serving"
        assert len(registrar.routes) == routes, "the rollback left the route table changed"
        assert _duplicate_paths(registrar) == []

        # nothing is frozen: the next successful apply takes the root back from the site
        rebuild(app)
        assert _site_state(registrar, app) == ("", "")
        assert (await get(client, ROOT)).status_code == 303
        assert (await get(client, "/squad-x")).status_code == 200
        assert (await get(client, "/group1")).status_code == 404


@pytest.mark.asyncio
async def test_a_site_file_that_cannot_be_rebuilt_leaves_the_previous_table_serving(config_dir):
    """A failure while rebuilding must never leave a half-registered app: the old table keeps serving."""
    write_site(config_dir, root_site())
    app = build(config_dir)
    registrar = app.state.webui_registrar
    routes = len(registrar.routes)
    capabilities = registrar.capability_count
    async with asgi_client(app) as client:
        assert (await get(client, "/rankings")).status_code == 200

        write_site(config_dir, "sites:\n  root:\n    slug: 'not a slug!'\n")
        assert rebuild(app) is None

        assert mount.console_prefix(app) == CONSOLE, "the mount moved on a refused rebuild"
        assert len(registrar.routes) == routes
        assert registrar.capability_count == capabilities
        assert registrar.capability_for(path="/rankings") == permissions.PUBLIC
        assert (await get(client, "/rankings")).status_code == 200
        assert (await get(client, ROOT)).status_code == 200
