"""THE EXTENSION TILE'S TRASHCAN OPENS THE CONSOLE'S OWN CONFIRMATION DIALOG.

The card's switch is a plain, non-destructive toggle (``disable``/``enable``). REMOVING an extension's
block — the destructive write — is behind the console's shared confirmation page
(``templates/confirm.html``), the one every destructive server action uses. Pinned over the REAL app:

* a configured extension's tile carries NO inline warning (``st-warn`` / ``st-warn-yaml``): the switch
  is a FORM and the trashcan is a LINK to a DIFFERENT URL;
* that URL renders ``confirm.html`` (``role="dialog"``), names the extension and its scope, shows the
  block's own YAML, offers ``Cancel`` back to the node page's tab, and mints the removal acknowledgement
  (the hidden token the POST carries back);
* the POST carrying that token removes exactly that block, and the notice names the extension;
* a bare POST of ``remove`` with no token is still REFUSED and the file is byte-identical.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice.pages import node_settings as node_settings_page
from test_webui_dashboard import _login
from test_webui_node_page import (_add_options, _app, _block,  # noqa: F401
                                  _extension_block, _instance_block, _page_url, node_data, node_dir)
from test_webui_node_page_extension_switch import _card, _get, _switch

_DLG_FORM = re.compile(r'<form id="dlg-form"[^>]*action="([^"]*)"[^>]*>(.*?)</form>', re.DOTALL)


def _trash_link(page: str, name: str) -> str:
    """The configured card's trashcan href — a LINK, never the switch's form action."""
    card = _card(page, name)
    assert card is not None, f"no card for {name}"
    link = re.search(r'<a class="st-trash" href="([^"]*)"', card["body"])
    assert link is not None, f"{name}'s card carries no trashcan"
    return html.unescape(link.group(1))


def _dialog(page: str) -> dict:
    """The confirmation page's form: its action and every hidden field it posts back."""
    form = _DLG_FORM.search(page)
    assert form is not None, "the page rendered no dialog form"
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form.group(2)))
    return {"action": html.unescape(form.group(1)), "fields": fields}


# ================================================================== the tile keeps no inline warning

@pytest.mark.asyncio
async def test_the_configured_tile_has_no_inline_warning_and_no_switch_link(node_dir, node_data):  # noqa: F811
    """A CONFIGURED extension's tile carries NO ``st-warn`` block, NO discarded-YAML ``st-warn-yaml``
    and NO switch on the Node-wide tab (ON/OFF lives on the instance tab) — and the trashcan points
    somewhere OTHER than the page's own save path, so removal and any switch can never be one write.
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    card = _card(page, "RealWeather")
    assert card is not None
    body = card["body"]
    assert "st-warn" not in body, "the tile grew back an inline warning"
    assert "st-warn-yaml" not in body
    assert "st-swform" not in body, "the Node-wide tile grew a switch back"
    assert _trash_link(page, "RealWeather") != \
        f'{node_settings_page.node_page_url("dcs-a-01")}?tab=0', \
        "the trashcan still shares the save's URL"


# ================================================================== the dialog, and the removal

@pytest.mark.asyncio
async def test_the_trashcan_opens_the_confirmation_dialog(node_dir, node_data):  # noqa: F811
    """GET on the trashcan's target is the console's ONE confirmation page: a ``role="dialog"``
    region, a heading naming the extension and its scope, the block's own YAML verbatim in a ``<pre>``,
    a ``Cancel`` link back to the node page's tab, a destructive primary button — and the
    acknowledgement, minted here and nowhere else."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        response = await client.get(_trash_link(page, "RealWeather"))
        assert response.status_code == 200, response.text[:400]
        dialog = response.text
    assert path.read_text(encoding="utf-8") == before, "opening the dialog wrote to the file"
    assert 'role="dialog"' in dialog
    body = html.unescape(dialog)
    assert "RealWeather" in body
    assert "Node-wide" in body, "the dialog does not name the scope"
    assert "<pre" in body and "installation: /opt/rw-a" in body, "the block's YAML is not shown"
    # Cancel returns to the node page's own tab
    assert f'href="{_page_url("dcs-a-01")}?tab=0"' in dialog
    # the primary button is the destructive one
    assert 'class="btn danger"' in dialog
    form = _dialog(dialog)
    assert form["action"] == f'{node_settings_page.node_page_url("dcs-a-01")}?tab=0'
    assert form["fields"].get(node_settings_page.NODE_PAGE_EXTENSION_FIELD) == "RealWeather"
    assert form["fields"].get(node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD) \
        == node_settings_page.NODE_PAGE_EXTENSION_REMOVE
    assert form["fields"].get(node_settings_page.NODE_PAGE_EXTENSION_ACK_FIELD), \
        "the dialog minted no acknowledgement"


@pytest.mark.asyncio
async def test_the_dialog_confirms_into_the_removal(node_dir, node_data):  # noqa: F811
    """The dialog's POST removes exactly that block, leaves every other node and scope
    byte-identical, and the notice names the extension that went away."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        response = await client.get(_trash_link(page, "RealWeather"))
        form = _dialog(response.text)
        reply = await client.post(form["action"], data=form["fields"])
        shown = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "RealWeather" not in _block(after, "dcs-a-01"), "the block was not removed"
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
    assert _instance_block(after, "dcs-a-01", "Syria") == _instance_block(before, "dcs-a-01", "Syria")
    assert html.unescape(node_settings_page.NODE_PAGE_EXTENSION_REMOVED
                         % {"name": "RealWeather"}) in html.unescape(shown)


@pytest.mark.asyncio
async def test_a_bare_remove_without_the_token_is_still_refused(node_dir, node_data):  # noqa: F811
    """The dialog is the ONLY place a removal token exists, so a POST that never opened it — valid
    CSRF and revision, no acknowledgement — is refused and the file stays byte-identical."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "RealWeather",
                              node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page, ack="")
        shown = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a bare remove wrote the file"
    assert html.unescape(node_settings_page.SAVE_REFUSED_EXTENSION_ACK) in html.unescape(shown)


@pytest.mark.asyncio
async def test_the_dialog_for_an_instance_block_names_that_instance(node_dir, node_data):  # noqa: F811
    """The INSTANCE tab's trashcan opens the same dialog for THAT instance's block: the heading and
    the discarded YAML are the instance's, ``Cancel`` returns to the instance tab, and the POST
    removes the instance's block only."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        response = await client.get(_trash_link(page, "Trackfile"))
        assert response.status_code == 200, response.text[:400]
        body = html.unescape(response.text)
        assert "Syria" in body
        assert "target: /opt/track" in body
        assert f'href="{_page_url("dcs-a-01")}?tab=1"' in response.text
        form = _dialog(response.text)
        assert form["fields"].get(node_settings_page.NODE_PAGE_SCOPE_FIELD) \
            == node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria"
        reply = await client.post(form["action"], data=form["fields"])
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "Trackfile" not in _instance_block(after, "dcs-a-01", "Syria")
    assert _extension_block(after, "dcs-a-01", "RealWeather") == \
        _extension_block(before, "dcs-a-01", "RealWeather")


@pytest.mark.asyncio
async def test_the_dialog_renders_in_german(node_dir, node_data):  # noqa: F811
    """The dialog's sentences are real msgids: with German chosen the page reads the catalog's
    German, while the block's YAML — operator data — stays verbatim."""
    from services.webservice import i18n

    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        page = await _get(client, _page_url("dcs-a-01"))
        response = await client.get(_trash_link(page, "RealWeather"))
    body = html.unescape(response.text)
    assert "installation: /opt/rw-a" in body
    assert node_settings_page.NODE_PAGE_EXTENSION_REMOVE_HEADING not in body
    assert "entfernt" in body, body[:600]


@pytest.mark.asyncio
async def test_the_dialog_refuses_an_extension_the_scope_does_not_configure(node_dir, node_data):  # noqa: F811
    """A hand-typed dialog URL for an extension this scope does not configure removes NOTHING: it
    redirects back to the node page rather than minting a token for an absent block."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        url = f'{node_settings_page.node_page_url("dcs-a-01")}/extensions/SRS/remove?tab=0'
        response = await client.get(url, follow_redirects=False)
    assert response.status_code in (303, 307), response.status_code
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_the_unconfigured_extension_keeps_its_add_form(node_dir, node_data):  # noqa: F811
    """The dialog is for REMOVAL only: an extension the scope does not configure is still offered
    as an ``add`` choice in the ``+ Add`` tile, and the tile carries no trashcan."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    assert "SRS" in _add_options(page)
    assert _card(page, "SRS") is None


# ============================================================== ONE home for the template's name

def test_the_confirm_template_name_is_imported_from_the_action_module():
    """The dialog renders the console's SHARED confirmation page, so the template NAME lives in the
    action module and this module BINDS that name — it declares no copy of ``"confirm.html"`` of its
    own, and the two names cannot drift apart."""
    from services.webservice.pages import actions as actions_page

    assert node_settings_page.CONFIRM_TEMPLATE == actions_page.CONFIRM_TEMPLATE == "confirm.html"
    source = Path(node_settings_page.__file__).read_text(encoding="utf-8")
    assert "from .actions import CONFIRM_TEMPLATE" in source
    assert 'CONFIRM_TEMPLATE = "confirm.html"' not in source, "the template name is duplicated again"
