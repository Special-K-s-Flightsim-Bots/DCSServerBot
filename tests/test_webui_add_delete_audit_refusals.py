"""Refusals at the SERVER CREATION pair (Add / Delete) must leave an audit row.

ONLY the ROUTE's refusals are driven here, and only for the creation pair:

* the DELETE of a server (``servers.delete``) — a typed name that does not match, a target outside the
  caller's view, and a body stating an option this console never sends;
* the ADD of a server (``servers.add``) — a node outside the caller's view.

Each asserts a row on the sink the seam actually writes to (``bot.audit``, through the
``recording_bot`` fixture), once per refused outcome.

The ADD path's third refusal — a body stating a channel id that is not one — is RETIRED: the dialog
no longer offers a channel picker, so no route reads a channel field and there is no refusal left to
record (``test_webui_add_delete_server`` pins that a body stating those fields writes none of them).

WHY THIS FILE EXISTS: the ``WriteAction`` dataclass had no ``audit_refusals`` field at all — only
``PlayerAction`` and ``NodeAction`` carried one — so the flag could not be declared on a server write
and ``_audit_refusal``'s ``getattr`` saw ``False`` for every one of them. The two routes of the
creation pair also never called ``_audit_refusal``. Both halves are pinned below: the flag on the
declarations, and a row for each refusal.

What these tests do NOT cover: the ACTION's own success/failure audit (one entry per attempt) is
covered by ``test_webui_add_delete_server``; no real cluster and no real node are exercised.
"""
from __future__ import annotations

import re

import pytest

import test_webui_add_delete_server as H
from services.webservice import permissions
from services.webservice.pages import actions as actions_page
from services.webservice.pages import servers as servers_page

config_dir = H.config_dir
clean_admin_registry = H.clean_admin_registry
recording_bot = H.recording_bot

DELETE = actions_page.SERVER_DELETE_PATH
ADD = actions_page.SERVER_ADD_PATH


def _one_shot_token(dialog: str) -> str:
    """The dialog's own one-shot confirm token, read out of the RENDERED page."""
    match = re.search(rf'name="{actions_page.CONFIRM_FIELD}" value="([^"]*)"', dialog)
    assert match, "the Delete dialog rendered no one-shot token"
    return match.group(1)


def _messages(bot) -> list[str]:
    return [call[0] for call in bot.calls]


# ------------------------------------------------------------------ the declarations carry the flag

def test_the_creation_pair_declares_audit_refusals(config_dir, clean_admin_registry):
    """``audit_refusals`` is True on BOTH declarations — and the field EXISTS on ``WriteAction``.

    Without the field, the flag cannot be declared on any server write and ``_audit_refusal`` always
    reads the ``getattr`` default; the two assertions below are the smallest statement of that.
    """
    import dataclasses

    assert "audit_refusals" in {f.name for f in dataclasses.fields(actions_page.WriteAction)}
    assert [action.audit_refusals for action in actions_page.SERVER_NODE_ACTIONS] == [True]
    assert [action.audit_refusals for action in actions_page.SERVER_PAGE_ACTIONS] == [True]
    for action in actions_page.SERVER_NODE_ACTIONS + actions_page.SERVER_PAGE_ACTIONS:
        assert action.audit_refusals is True, f"{action.key} would not record a refusal"


# ------------------------------------------------------------------ the DELETE refusals

@pytest.mark.asyncio
async def test_a_delete_refused_for_a_typed_name_mismatch_leaves_an_audit_row(
        config_dir, clean_admin_registry, recording_bot):
    """The typed-name refusal (400) records ONE row naming the actor, the action and the target."""
    app = H.build(config_dir, H.cluster_with())
    async with H.client(app) as http:
        await H.sign_in(http)
        dialog = await H.open_delete_dialog(http, "SRS-1")
        response = await http.post(DELETE, data={
            "server": "SRS-1",
            actions_page.CONFIRM_NAME_FIELD: "SRS-2",
            actions_page.CONFIRM_FIELD: _one_shot_token(dialog),
            "_csrf_token": H.token_of(dialog)})
    assert response.status_code == 400
    assert _messages(recording_bot) == [
        "refused delete_server: typed name does not match the target "
        "(server 'SRS-1')"]
    assert len(recording_bot.calls) == 1


@pytest.mark.asyncio
async def test_a_delete_refused_out_of_scope_leaves_an_audit_row(
        config_dir, clean_admin_registry, recording_bot, monkeypatch):
    """The out-of-scope refusal (403) records ONE row, and answers the same 403 in the body.

    The capability is NARROWED for this test to a role pair a restricted caller can hold
    (``DCS Admin`` with a scope), because the module declares it Admin-only and an Admin is
    unrestricted by rule — the branch exists for a wider declaration, and the conftest restores the
    declarations. The one-shot gate is stepped over because the DIALOG route refuses an out-of-scope
    target itself, so a real browser cannot mint a token for one; the scope resolution still runs for
    real, which is the branch under test.
    """
    app = H.build(config_dir, H.cluster_with())
    monkeypatch.setitem(permissions.CAPABILITIES, "servers.delete", ("DCS Admin",))
    monkeypatch.setitem(permissions.SCOPE_GRANTS, "servers.delete", True)
    monkeypatch.setattr(actions_page, "consume_confirm_token", lambda *args, **kwargs: True)
    async with H.client(app) as http:
        await H.sign_in(http, "mod1")               # manages Host A only
        html = await H.fleet(http)
        response = await http.post(DELETE, data={
            "server": "SRS-B",                      # on dcs-b-01 — outside Host A
            actions_page.CONFIRM_NAME_FIELD: "SRS-B",
            actions_page.CONFIRM_FIELD: "x",
            "_csrf_token": H.token_of(html)})
    assert response.status_code == 403
    assert "Not authorized" in response.text
    assert _messages(recording_bot) == [
        "refused delete_server: not permitted (outside your scope) "
        "(server 'SRS-B')"]
    assert len(recording_bot.calls) == 1


@pytest.mark.asyncio
async def test_a_delete_refused_for_an_option_it_never_sent_leaves_an_audit_row(
        config_dir, clean_admin_registry, recording_bot):
    """A checkbox value this console never sends is refused (400) and still leaves a row."""
    app = H.build(config_dir, H.cluster_with())
    async with H.client(app) as http:
        await H.sign_in(http)
        dialog = await H.open_delete_dialog(http, "SRS-1")
        response = await http.post(DELETE, data={
            "server": "SRS-1",
            actions_page.CONFIRM_NAME_FIELD: "SRS-1",
            actions_page.CONFIRM_FIELD: _one_shot_token(dialog),
            actions_page.REMOVE_FILES_FIELD: "maybe",
            "_csrf_token": H.token_of(dialog)})
    assert response.status_code == 400
    assert _messages(recording_bot) == [
        "refused delete_server: invalid option "
        f"(the '{actions_page.REMOVE_FILES_FIELD}' option was not one this console sent) "
        "(server 'SRS-1')"]
    assert len(recording_bot.calls) == 1


# ------------------------------------------------------------------ the ADD refusals

@pytest.mark.asyncio
async def test_an_add_refused_out_of_scope_leaves_an_audit_row(
        config_dir, clean_admin_registry, recording_bot, monkeypatch):
    """The Add path's out-of-scope refusal (403) records ONE row, exactly as the delete's does.

    Same narrowing as the delete test, and the same recipe the Add page control uses: open the dialog
    through ``<path>/confirm``, then POST the node outside the caller's view.
    """
    app = H.build(config_dir, H.cluster_with())
    monkeypatch.setitem(permissions.CAPABILITIES, "servers.add", ("DCS Admin",))
    monkeypatch.setitem(permissions.SCOPE_GRANTS, "servers.add", True)
    async with H.client(app) as http:
        await H.sign_in(http, "mod1")               # manages Host A only
        html = await H.fleet(http)
        assert f'action="{ADD}/confirm"' in html, "no Add control for the narrowed caller"
        dialog = await http.post(f"{ADD}/confirm", data={
            actions_page.ORIGIN_FIELD: servers_page.SERVERS_PATH,
            "_csrf_token": H.token_of(html)})
        assert dialog.status_code == 200
        response = await http.post(ADD, data={
            actions_page.ORIGIN_FIELD: servers_page.SERVERS_PATH,
            "_csrf_token": H.token_of(dialog.text),
            actions_page.ADD_NODE_FIELD: "dcs-b-01",    # outside Host A
            actions_page.ADD_NAME_FIELD: "SRS-2"})
    assert response.status_code == 403
    assert "Not authorized" in response.text
    assert _messages(recording_bot) == [
        "refused add_server: not permitted (outside your scope) "
        "(node 'dcs-b-01')"]
    assert len(recording_bot.calls) == 1
