"""A bot started with NO site.yaml: the first Add and the first Save must land.

Each pin here is a bug an operator met on a fresh install — the site file did not exist when the
process started, and the console had already decided everything it decides at startup:

* the first save of a card written by "Add a site" must reach the file (it used to need a second
  press, because the page's revision was the one a file-less page renders);
* once a site owns ``/``, the public site must serve the root and the console must move under its
  own prefix, with no restart in between.
"""
from __future__ import annotations

import re

import pytest

from services.webservice import mount, sites
from services.webservice.pages import site_config as site_page
from test_webui_site_settings import (  # noqa: F401 - fixtures imported on purpose
    ADMIN, BOT_YAML, SERVERS_YAML, cluster, _users, page_path, save_path, token_of,
)
from test_webui_write_pause import build, client, sign_in  # noqa: F401 - fixtures on purpose


@pytest.fixture
def fresh_dir(tmp_path):
    """A config directory WITHOUT site.yaml — the state a fresh install is in."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def fresh_app(fresh_dir):
    return build(fresh_dir, cluster(), users=_users())


async def _open(http) -> str:
    response = await http.get(page_path())
    assert response.status_code == 200, response.text[:400]
    return response.text


def _revision(html: str) -> str:
    match = re.search(r'name="_revision" value="([^"]*)"', html)
    assert match, "the page rendered no revision"
    return match.group(1)


@pytest.mark.asyncio
async def test_the_first_save_of_a_site_added_to_a_missing_file_reaches_it(fresh_app, fresh_dir):
    """Add a site where no file existed, then save once: the file must carry the edit."""
    site_file = fresh_dir / sites.SITE_FILE
    async with client(fresh_app) as http:
        await sign_in(http, ADMIN)
        html = await _open(http)
        token = token_of(html)
        added = await http.post(mount.url(site_page.SITE_ADD_PATH),
                               data={"_csrf_token": token, "_key": "guards",
                                     "_role": "5th Guards", "_slug": "guards",
                                     "_revision": _revision(html)})
        assert added.status_code in (200, 303), added.text[:300]
        assert site_file.is_file(), "Add wrote no site.yaml"
        after_add = await _open(http)
        save = await http.post(save_path(),
                              data={"_csrf_token": token_of(after_add),
                                    site_page.SITE_KEY_FIELD: "guards",
                                    "_revision": _revision(after_add),
                                    "f.sites.guards.branding.title": "Saved Once"})
    assert save.status_code in (200, 303), save.text[:400]
    assert "Saved Once" in site_file.read_text(encoding="utf-8"), (
        f"the first save did not reach the file; it holds:\n{site_file.read_text(encoding='utf-8')}")


@pytest.mark.asyncio
async def test_a_site_added_as_the_root_is_served_without_a_restart(fresh_dir):
    """Adding a site applies the file: the root becomes the site and the console moves to /console.

    The file existed at startup with ONE group site; adding the site keyed ``root`` is a structural
    edit like any other, so the add applies its own file before it answers — the very next request is
    served by the public site at ``/`` and the console answers under its own prefix.
    """
    site_file = fresh_dir / sites.SITE_FILE
    site_file.write_text("sites:\n  guards:\n    group_role: \"5th Guards\"\n    slug: guards\n",
                         encoding="utf-8")
    app = build(fresh_dir, cluster(), users=_users())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await _open(http)
        added = await http.post(mount.url(site_page.SITE_ADD_PATH),
                               data={"_csrf_token": token_of(html), "_key": "root", "_role": "",
                                     "_slug": "", "_revision": _revision(html)},
                               follow_redirects=False)
        assert added.status_code in (200, 303), added.text[:300]
        assert "root" in site_file.read_text(encoding="utf-8"), "the add wrote no root site"
        root = await http.get("/")
        console = await http.get("/console" + site_page.SITE_CONFIG_PATH)
        dashboard = await http.get("/console")
    assert mount.console_prefix(app) == mount.CONSOLE_PREFIX, (
        f"the console was not moved off the root: {mount.console_prefix(app)!r}")
    assert root.status_code == 200, f"the root answered {root.status_code}"
    assert 'class="top"' in root.text, "the root is not the public site's document"
    assert console.status_code == 200, f"the console did not move to /console: {console.status_code}"
    assert dashboard.status_code == 200, f"the console dashboard answered {dashboard.status_code}"
    assert 'href="/"' in dashboard.text and "public site" in dashboard.text.lower(), (
        "the dashboard carries no door to the site that now owns the root")
