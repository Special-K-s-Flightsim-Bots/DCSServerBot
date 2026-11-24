"""The password PROTECTION declaration — a per-field choice, and the two branches it opens.

``ConfigField.protection`` says WHICH a password field is, in ONE attribute: ``UNPROTECTED`` renders
the value (masked, with the eye) and diffs it as a string; ``PROTECTED`` never renders the value — the
row shows only the "a password is set" state (a fixed placeholder) and the write keeps / clears /
replaces it. Both branches are reached by the DECLARATION alone: the template branches on
``FieldView.protection`` and never on the field's key.

Driven in-process through the real application. The protected branch is exercised with a SYNTHETIC
field injected into the declaration (no field is protected right now), which is also what proves the
template is not keying on ``password``.
"""
from __future__ import annotations

import re

import pytest

from conftest import asgi_client
from core.action_results import ServerConfigResult
from services.webservice.pages import actions as actions_page
from services.webservice.readmodels import serverconfig as server_config
from test_webui_server_config import (BASE_SETTINGS, _full_form, _namespace_server,
                                      _post_config, _spy, app_with, config_dir, get)

# the shipped eye's markup, counted to prove a protected field adds none (the shipped markup contract)
REVEAL_BUTTON = re.compile(r'<button type="button" class="cfg-reveal" data-reveal="cfg-password"[^>]*>')

# The SYNTHETIC protected field. A different key from ``password`` on purpose: if the template ever
# special-cased ``password`` this field would render the wrong branch, so the choice is proven by the
# declaration alone. It sits in the IDENTITY group so it renders with the other editable rows.
PROTECTED_FIELD = server_config.ConfigField(
    "advanced.protectedToken", server_config.KIND_STR, "Protected token",
    "A password nobody may read back.", server_config.GROUP_IDENTITY,
    maximum=80, protection=server_config.PROTECTED)

PROTECTED_VALUE = "top-secret-value"


def _declare_protected(monkeypatch) -> None:
    """Inject :data:`PROTECTED_FIELD` into the console's declaration (both readings of it)."""
    monkeypatch.setattr(server_config, "EDITABLE_FIELDS",
                        server_config.EDITABLE_FIELDS + (PROTECTED_FIELD,))
    monkeypatch.setattr(server_config, "EDITABLE_BY_KEY",
                        {**server_config.EDITABLE_BY_KEY, PROTECTED_FIELD.key: PROTECTED_FIELD})


def _protected_settings(value=PROTECTED_VALUE) -> dict:
    """The base settings with the synthetic protected field carrying *value*."""
    settings = dict(BASE_SETTINGS)
    advanced = dict(settings["advanced"])
    advanced["protectedToken"] = value
    settings["advanced"] = advanced
    return settings


# ----------------------------------------------------------- 1. the declaration, one flag, one meaning


def test_the_two_branches_are_two_values_of_one_declaration():
    """The declaration is ONE attribute; each of its two password values means exactly one thing."""
    assert server_config.PROTECTED == "protected"
    assert server_config.UNPROTECTED == "unprotected"
 # the DCS password is UNPROTECTED (V2's behaviour): its value is rendered.
    assert server_config.EDITABLE_BY_KEY["password"].protection == server_config.UNPROTECTED
 # a non-password carries no declaration at all — the default is "not a password", never protected.
    assert server_config.EDITABLE_BY_KEY["name"].protection == ""
    assert server_config.EDITABLE_BY_KEY["port"].protection == ""
 # the coalition hashes stay PROTECTED and read-only.
    for key in ("advanced.bluePasswordHash", "advanced.redPasswordHash"):
        field = next(f for f in server_config.READONLY_FIELDS if f.key == key)
        assert field.protection == server_config.PROTECTED, key
        assert field.readonly is True, key


# ----------------------------------------------------------- 2. PROTECTED: the value never reaches the page


@pytest.mark.asyncio
async def test_a_protected_value_never_appears_in_the_rendered_page(config_dir, monkeypatch):
    """A protected field renders the "a password is set" state — never the value itself."""
    _declare_protected(monkeypatch)
    app, _ = app_with(config_dir, ["Admin"],
                      servers=[_namespace_server(settings=_protected_settings())])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")

    assert PROTECTED_VALUE not in html, "a protected value must NOT reach the page"
    assert f'name="{PROTECTED_FIELD.key}"' in html, "the protected row is still rendered"
 # the field holds the fixed placeholder — the "a password is set" state — not the value
    assert re.search(rf'<input type="password" id="cfg-advanced-protectedToken" '
                     rf'name="{re.escape(PROTECTED_FIELD.key)}"\s+value="{re.escape(server_config.PROTECTED_SENTINEL)}"',
                     html), "a set protected field must hold the placeholder"
    assert f" · {server_config.PROTECTED}" in html, "the row declares itself protected"


@pytest.mark.asyncio
async def test_a_protected_field_carries_no_value_into_its_row(config_dir, monkeypatch):
    """The read model hands the template the SENTINEL, never the stored value."""
    _declare_protected(monkeypatch)
    server_obj = _namespace_server(settings=_protected_settings())
    view = server_config.server_config_view(server_obj)
    row = next(f for group in view.groups for f in group.fields if f.key == PROTECTED_FIELD.key)
    assert row.protection == server_config.PROTECTED
    assert row.value == server_config.PROTECTED_SENTINEL, "the row must hold the placeholder"
    assert PROTECTED_VALUE not in repr(view), "the value must not be anywhere in the view record"


@pytest.mark.asyncio
async def test_an_unset_protected_field_renders_an_empty_control(config_dir, monkeypatch):
    """With no protected value set, the control is empty — there is no "a password is set" state."""
    _declare_protected(monkeypatch)
    app, _ = app_with(config_dir, ["Admin"],
                      servers=[_namespace_server(settings=_protected_settings(value=""))])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert re.search(rf'<input type="password" id="cfg-advanced-protectedToken" '
                     rf'name="{re.escape(PROTECTED_FIELD.key)}"\s+value=""', html), \
        "an unset protected field renders empty"


@pytest.mark.asyncio
async def test_a_protected_field_renders_no_eye(config_dir, monkeypatch):
    """Nothing to reveal: a protected field adds no eye beside the one UNPROTECTED field already has."""
    _declare_protected(monkeypatch)
    app, _ = app_with(config_dir, ["Admin"],
                      servers=[_namespace_server(settings=_protected_settings())])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
 # exactly the one eye the UNPROTECTED DCS password renders — the protected field adds none
    assert len(REVEAL_BUTTON.findall(html)) == 1, "a protected field must render no eye"


# ----------------------------------------------------------- 3. PROTECTED: keep / clear / replace


@pytest.mark.asyncio
async def test_a_protected_field_keeps_clears_and_replaces(config_dir, monkeypatch):
    """The three writes, read off the placeholder: untouched keeps, emptied clears, typed replaces."""
    _declare_protected(monkeypatch)
    app, _ = app_with(config_dir, ["Admin"],
                      servers=[_namespace_server(settings=_protected_settings())])
    calls = _spy(monkeypatch, ServerConfigResult(success=True, server_name="DCS Syria Campaign",
                                                 message="ok"))
 # UNTOUCHED: the field posts the placeholder back -> no change
    await _post_config(app, _full_form(**{PROTECTED_FIELD.key: server_config.PROTECTED_SENTINEL}))
    assert calls[-1][1]["values"] == {}, "the placeholder must read as keep"
 # EMPTIED: nothing left -> the explicit clear
    await _post_config(app, _full_form(**{PROTECTED_FIELD.key: ""}))
    assert calls[-1][1]["values"] == {PROTECTED_FIELD.key: None}, "an emptied field is the clear"
 # TYPED: a new value replaces
    await _post_config(app, _full_form(**{PROTECTED_FIELD.key: "fresh"}))
    assert calls[-1][1]["values"] == {PROTECTED_FIELD.key: "fresh"}, "a typed field replaces"


@pytest.mark.asyncio
async def test_a_protected_post_without_the_field_clears_nothing(config_dir, monkeypatch):
    """A crafted/partial POST that does not state the field cannot remove a protected value."""
    _declare_protected(monkeypatch)
    app, _ = app_with(config_dir, ["Admin"],
                      servers=[_namespace_server(settings=_protected_settings())])
    calls = _spy(monkeypatch, ServerConfigResult(success=True, server_name="DCS Syria Campaign",
                                                 message="ok"))
    body = _full_form()
    body.pop(PROTECTED_FIELD.key, None)
    await _post_config(app, body)
    assert calls[-1][1]["values"] == {}, "an unstated field is not a clear"


@pytest.mark.asyncio
async def test_an_empty_protected_field_on_a_server_without_one_changes_nothing(config_dir, monkeypatch):
    """With no value stored, an emptied field has nothing to clear."""
    _declare_protected(monkeypatch)
    app, _ = app_with(config_dir, ["Admin"],
                      servers=[_namespace_server(settings=_protected_settings(value=""))])
    calls = _spy(monkeypatch, ServerConfigResult(success=True, server_name="DCS Syria Campaign",
                                                 message="ok"))
    await _post_config(app, _full_form(**{PROTECTED_FIELD.key: ""}))
    assert calls[-1][1]["values"] == {}, "there is nothing to remove, so nothing was cleared"


def test_a_protected_value_is_read_only_for_the_route_never_rendered(monkeypatch):
    """``config_values_from_form`` reads the current protected value only to know it is SET."""
    _declare_protected(monkeypatch)
    settings = {"advanced": {"protectedToken": "stored"}}
    values = actions_page.config_values_from_form(settings, {PROTECTED_FIELD.key: ""})
    assert values == {PROTECTED_FIELD.key: None}


# ----------------------------------------------------------- 5. the branches are declaration-only

UNPROTECTED_FIELD = server_config.ConfigField(
    "advanced.openToken", server_config.KIND_STR, "Open token",
    "A password the operator may read.", server_config.GROUP_IDENTITY,
    maximum=80, protection=server_config.UNPROTECTED)


@pytest.mark.asyncio
async def test_the_unprotected_branch_is_reachable_by_declaration_alone(config_dir, monkeypatch):
    """A second UNPROTECTED field (a novel key) renders its value and its own eye — the branch is the
    declaration, never the key ``password``."""
    monkeypatch.setattr(server_config, "EDITABLE_FIELDS",
                        server_config.EDITABLE_FIELDS + (UNPROTECTED_FIELD,))
    monkeypatch.setattr(server_config, "EDITABLE_BY_KEY",
                        {**server_config.EDITABLE_BY_KEY, UNPROTECTED_FIELD.key: UNPROTECTED_FIELD})
    settings = dict(BASE_SETTINGS)
    settings["advanced"] = dict(settings["advanced"], openToken="readable")
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=settings)])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")

    assert 'value="readable"' in html, "a declared UNPROTECTED value is rendered"
    assert f'name="{UNPROTECTED_FIELD.key}"' in html, "the row is named from its key, not hardcoded"
    assert 'data-reveal="cfg-advanced-openToken"' in html, "and its own eye is keyed to its own field"


# ----------------------------------------------------------- 4. UNPROTECTED stays behaviour-exact

@pytest.mark.asyncio
async def test_the_unprotected_branch_still_renders_and_round_trips(config_dir):
    """The UNPROTECTED branch (V2's behaviour) renders the value and keeps / clears / replaces it."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert 'value="hunter2"' in html, "the unprotected password value is rendered"
    assert len(REVEAL_BUTTON.findall(html)) == 1, "and it carries its eye"
 # the same field, diffed as a string (a settings dict carrying only the password, so the diff
 # isolates it)
    settings = {"password": "hunter2"}
    assert actions_page.config_values_from_form(settings, {"password": "hunter2"}) == {}
    assert actions_page.config_values_from_form(settings, {"password": ""}) == {"password": None}
    assert actions_page.config_values_from_form(settings, {"password": "new"}) == {"password": "new"}
