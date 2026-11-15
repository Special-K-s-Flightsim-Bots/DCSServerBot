"""The sidebar honours a nav item's availability test, per request.

Four facts, each proved independently:

* an entry whose ``available()`` answers False is DROPPED like an unauthorised one, and a group left
  with nothing under it disappears with it — every other entry and group is unchanged;
* an entry whose test answers True is offered as before;
* a test that RAISES drops the entry instead of breaking the sidebar render;
* a declaration that passes no test keeps the old behaviour (always offered);
* the music page's own registration passes ``get_music_radios``' availability, and the page route
  stays registered while the action is absent — only the sidebar entry goes.
"""
from __future__ import annotations

import dataclasses
import types

import pytest

from conftest import create_app, install_shell

from core import actions as actions_module
from services.webservice import i18n, permissions
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import music as music_page
from services.webservice.registry import NavItem

ROLES = {"Admin"}
ANCHOR = '<a [^>]*href="{url}"'


def _fake_registrar(items):
    """A registrar double: the sidebar reads exactly one attribute."""
    return types.SimpleNamespace(nav=tuple(items))


def _with(item, *, available):
    return dataclasses.replace(item, available=available)


def _music_item(registrar) -> NavItem:
    matches = [item for item in registrar.nav if item.url == music_page.MUSIC_PATH]
    assert len(matches) == 1, f"the console declares {len(matches)} music nav items"
    return matches[0]


def _flat(groups) -> list[tuple[str, str]]:
    return [(group["label"], link["url"]) for group in groups for link in group["links"]]


def _render_rail(app, groups) -> str:
    request = types.SimpleNamespace(
        app=app, cookies={}, headers={}, url=types.SimpleNamespace(path="/", query=""))
    return i18n.render(request, "base.html", nav_groups=groups)


@pytest.fixture(autouse=True)
def music_read_action_registered(monkeypatch):
    """The console under test runs as an installation with the music plugin.

    The music entry is offered only while its read action is registered; this file imports no plugin
    module, so the registration is stubbed at the same seam the entry reads. The tests that need the
    action ABSENT delete it from the registry themselves.
    """
    from core import actions as actions_module

    monkeypatch.setitem(actions_module._ACTIONS, "get_music_radios", lambda *args, **kwargs: None)


@pytest.fixture
def console():
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True), config={})
    return app, registrar


# --------------------------------------------------- the unavailable entry is dropped, the rest stays

def test_an_unavailable_entry_leaves_the_sidebar_and_its_group_disappears(console):
    _app, registrar = console
    baseline = dashboard_page.nav_groups(registrar, ROLES, current="/")
    assert ("Media", music_page.MUSIC_PATH) in _flat(baseline)

    items = [_with(item, available=lambda: False) if item.url == music_page.MUSIC_PATH else item
             for item in registrar.nav]
    groups = dashboard_page.nav_groups(_fake_registrar(items), ROLES, current="/")

    assert music_page.MUSIC_PATH not in [url for _label, url in _flat(groups)]
    assert "Media" not in [group["label"] for group in groups], \
        "Media has no other entry, so its heading must disappear rather than head an empty list"
    assert _flat(groups) == [(label, url) for label, url in _flat(baseline)
                             if url != music_page.MUSIC_PATH], \
        "every other entry changed with the music entry dropped"

    rail = _render_rail(_app, groups)
    assert f'href="{music_page.MUSIC_PATH}"' not in rail
    assert ">Media<" not in rail


def test_an_available_entry_is_offered(console):
    _app, registrar = console
    groups = dashboard_page.nav_groups(registrar, ROLES, current="/")
    media = [group for group in groups if group["label"] == "Media"]
    assert len(media) == 1
    assert [link["url"] for link in media[0]["links"]] == [music_page.MUSIC_PATH]


# ---------------------------------------------------------------- a raising test must not break it

def test_an_availability_test_that_raises_drops_the_entry_without_breaking_render(console):
    _app, registrar = console

    def explode():
        raise RuntimeError("the product state could not be read")

    target = next(item for item in registrar.nav if item.url == "/settings")
    assert target.group == "Administration"
    items = [_with(item, available=explode) if item.group == "Administration" else item
             for item in registrar.nav]
    groups = dashboard_page.nav_groups(_fake_registrar(items), ROLES, current="/")

    assert "/settings" not in [url for _label, url in _flat(groups)]
    assert "Administration" not in [group["label"] for group in groups]

    rail = _render_rail(_app, groups)
    assert 'href="/settings"' not in rail
    assert 'href="/fleet"' in rail, "the rest of the sidebar stopped rendering with one bad test"


# ------------------------------------------------------------------ no test means always offered

def test_an_entry_without_a_test_behaves_as_before(console):
    _app, registrar = console
    assert all(getattr(item, "available", None) is None
               for item in registrar.nav if item.url != music_page.MUSIC_PATH), \
        "a page that passes no test declares an availability test"

    bare = NavItem(label="Ghost", url="/ghost", capability=permissions.PUBLIC,
                   group="Data", glyph="grid")
    assert bare.available is None
    groups = dashboard_page.nav_groups(_fake_registrar([bare]), ROLES, current="/")
    assert _flat(groups) == [("Data", "/ghost")]


# --------------------------------------------------------- the music page's own registration hook

def test_the_music_entry_follows_its_read_action():
    item = music_page.nav_items()[0]

    assert item.available is not None
    assert item.available() is True, "the test suite registers the music read action"

    del actions_module._ACTIONS["get_music_radios"]
    assert item.available() is False, \
        "the entry is offered while the music read action is not registered"
    actions_module._ACTIONS["get_music_radios"] = lambda *args, **kwargs: None


def test_the_music_page_route_stays_registered_while_its_action_is_absent():
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True), config={})
    assert actions_module.action_available("get_music_radios")

    del actions_module._ACTIONS["get_music_radios"]
    try:
        paths = {getattr(route, "path", None) for route in registrar.routes}
        assert music_page.MUSIC_PATH in paths, "the page was removed with its sidebar entry"
        assert music_page.MUSIC_PATH not in [
            url for _label, url in _flat(dashboard_page.nav_groups(registrar, ROLES, current="/"))]
    finally:
        actions_module._ACTIONS["get_music_radios"] = lambda *args, **kwargs: None
