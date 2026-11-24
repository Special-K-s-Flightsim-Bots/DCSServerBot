"""A saved ``site.yaml`` reaches the public pages on the next request, without a reload.

WHAT THIS PINS, and why it is not the same pin as ``tests/test_webui_sites.py``'s live-catalog
section: those four tests prove ``sites.live_catalog`` overlays a file correctly. They say nothing
about whether ANY public route ASKS it — and for a while none did, so a save wrote the file, the
overlay would have served it, and every page rendered from the catalogue installed at startup. The
operator's symptom was exactly that: refresh shows the old site, restart shows the new one.

So every test here drives the REAL application (the shell, the site's own route tree) and reads a
PUBLIC page:

* a content-only edit made while the app is up is on the very next ``GET /`` — no reload, no
  restart, no second install;
* a structural edit (a site that does not exist in the installed route table) keeps serving the
  installed structure AND says, once, that a reload is owed;
* an unchanged file is parsed ONCE across many requests (the cache marker still answers).
"""
from __future__ import annotations

import logging

import pytest

from conftest import asgi_client
from services.webservice import sites
from test_webui_site_pages import build, config_dir  # noqa: F401 - fixtures imported on purpose

#: the installed site file. Two sites: the root one and one group site, so a structural edit has
#: somewhere to add a third.
SITE_YAML = """\
sites:
  root:
    slug: ""
    branding:
      title: Root Site
  group1:
    slug: group1
    group_role: 5th Guards
    branding:
      title: 5th Guards
"""

#: the same file after a CONTENT-only edit: one title, nothing structural.
CONTENT_EDIT = SITE_YAML.replace("title: Root Site", "title: Renamed Site")

#: the same file after a STRUCTURAL edit: a site the installed route table has no route for.
STRUCTURAL_EDIT = SITE_YAML + """\
  group2:
    slug: group2
    group_role: 5th Guards
    branding:
      title: Second Group
"""


@pytest.fixture
def app_with_sites(config_dir):
    """The console installed from a ``site.yaml`` on disk: the file is the only source."""
    (config_dir / sites.SITE_FILE).write_text(SITE_YAML, encoding="utf-8")
    app, _registrar = build(config_dir, config={"session": {"secret": "s" * 40}})
    assert sites.sites_for(app).root is not None, "the fixture installed no root site"
    return app


def _get(app, path: str):
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return await client.get(path)

    return asyncio.run(run())


def test_a_saved_content_edit_is_on_the_very_next_public_page(app_with_sites, config_dir):
    """THE OPERATOR'S SYMPTOM: save, refresh, see the new value. No reload, no restart."""
    first = _get(app_with_sites, "/")
    assert first.status_code == 200, first.text[:400]
    assert "Root Site" in first.text
    assert "Renamed Site" not in first.text

    (config_dir / sites.SITE_FILE).write_text(CONTENT_EDIT, encoding="utf-8")

    second = _get(app_with_sites, "/")
    assert second.status_code == 200, second.text[:400]
    assert "Renamed Site" in second.text, (
        "the public page still renders the catalogue installed at startup: the request path does "
        "not read through sites.live_catalog")


def test_a_group_site_page_also_follows_the_file(app_with_sites, config_dir):
    """The same overlay on a site's OWN prefix — one entry point, not one route."""
    (config_dir / sites.SITE_FILE).write_text(
        SITE_YAML.replace("title: 5th Guards", "title: Guards Renamed"), encoding="utf-8")
    page = _get(app_with_sites, "/group1")
    assert page.status_code == 200, page.text[:400]
    assert "Guards Renamed" in page.text


def test_a_structural_edit_keeps_the_installed_structure_and_says_a_reload_is_owed(
        app_with_sites, config_dir, caplog):
    """A site with no installed route is NOT half-applied: the installed structure keeps serving."""
    (config_dir / sites.SITE_FILE).write_text(STRUCTURAL_EDIT, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="services.webservice.sites"):
        page = _get(app_with_sites, "/")
        added = _get(app_with_sites, "/group2")

    assert page.status_code == 200
    assert "Root Site" in page.text, "the installed content is served while a reload is owed"
    assert added.status_code == 404, "a route cannot be registered by a save"

    warnings = [record.getMessage() for record in caplog.records
                if record.levelno == logging.WARNING and "services.webservice.sites" in
                record.name]
    owed = [message for message in warnings if sites.SITE_FILE in message]
    assert owed, f"a structural edit said nothing about the reload it owes: {warnings}"
    assert "reload" in owed[0], owed[0]


def test_every_public_page_follows_the_file(app_with_sites, config_dir):
    """THE CENSUS: one content edit is on EVERY page address, so no route can bypass the overlay.

    The brand title prints in the top bar of every site page, so it is the single token that proves
    each address read the file rather than the catalogue installed at startup. The addresses are the
    site's own declarations (``site_pages``), not a literal list here.
    """
    from services.webservice import site_pages

    addresses = [site_pages.NAV_PATHS[page] or "/" for page in site_pages.SERVED_NAV_IDS]
    addresses += [site_pages.ME_PATH, site_pages.PLAYER_PATH + "/u-somebody"]
    (config_dir / sites.SITE_FILE).write_text(CONTENT_EDIT, encoding="utf-8")
    for path in addresses:
        page = _get(app_with_sites, path)
        assert page.status_code == 200, f"{path} answered {page.status_code}"
        assert "Renamed Site" in page.text, (
            f"{path} renders the catalogue installed at startup: that route does not read through "
            f"sites.live_catalog")


def test_no_public_page_module_reads_the_installed_catalogue():
    """THE RATCHET: the overlay has ONE entry point, and the page modules go through it.

    A page module that resolved its own context from ``sites.sites_for`` would keep serving the
    installed content and this card's defect would come back on that page alone. So the census reads
    the modules themselves: each one names ``resolve_site`` and none of them touches ``sites_for``.
    """
    import inspect

    from services.webservice import site_pages

    for module in site_pages.page_modules():
        source = inspect.getsource(module)
        assert "resolve_site" in source, f"{module.__name__} resolves no site context"
        assert "sites_for" not in source, (
            f"{module.__name__} reads the installed catalogue directly; a page resolves its "
            f"context through site_pages.resolve_site, which reads sites.live_catalog")


def test_an_unchanged_file_is_parsed_once(app_with_sites, config_dir, monkeypatch):
    """The cache marker still answers: a parse per request is not what this fixed."""
    assert _get(app_with_sites, "/").status_code == 200
    calls: list[int] = []
    real = sites.parse_sites

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(sites, "parse_sites", counting)
    for _ in range(4):
        assert _get(app_with_sites, "/").status_code == 200
    assert not calls, f"the file was parsed {len(calls)} times for 4 requests"
