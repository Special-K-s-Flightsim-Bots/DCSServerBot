"""The Configuration tab's UX defects, pinned on the rendered bytes and the shipped sheet.

(1) the page did not scroll: the shell's scrolling region is a flex column and every card is
``overflow: hidden``, so long cards shrank to the leftover height and their excess was clipped -- the
fix is at the shell level. (2) the description field was one line (now the one
``ConfigField.multiline`` declaration, with the action's own ``maxlength``). (3) unstyled fields fell
through to the engine's light default (the rules now read the shell's tokens and ``:root`` declares
``color-scheme``). (4) the password is a normal masked field that shows the STORED value (the console
and the bot's own Discord editor agree on it)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice import permissions
from services.webservice.pages import actions as actions_page
from test_webui_dashboard import _login
from test_webui_server_config import (  # noqa: F401 - the fixtures are imported on purpose
    BASE_SETTINGS, _namespace_server, app_with, config_dir, get,
)

STATIC = Path(__file__).resolve().parent.parent / "services" / "webservice" / "static"

ZERO_FLEX = re.compile(r"flex:\s*0\s+0\s+auto")
TEXTAREA = re.compile(r'<textarea[^>]*name="description"[^>]*>')
PASSWORD_FIELD = re.compile(r'<input type="password" id="cfg-password" name="password"\s+value="([^"]*)"\s+class="cfg-input"\s+autocomplete="new-password"')
REVEAL_BUTTON = re.compile(r'<button type="button" class="cfg-reveal" data-reveal="cfg-password"[^>]*>')


@pytest.fixture(scope="module")
def sheet() -> str:
    """``shell.css`` with comments removed — the rules, not the prose that explains them."""
    return re.sub(r"/\*.*?\*/", "", (STATIC / "shell.css").read_text(encoding="utf-8"), flags=re.S)


def _rule(sheet: str, selector: str) -> str:
    """The body of the ``selector { … }`` block, by regex so whitespace is not the assertion.

    A selector that shares a rule with others (``.cfg-form .notice > button, .revert button { … }``)
    is found through the selector LIST: everything between the selector and the block is allowed to be
    other selectors, so the test asserts the declaration and not the spelling of the list.
    """
    pattern = re.escape(selector).replace(r"\ ", r"\s+")
    match = re.search(pattern + r"\s*(?:,[^{}]*)?\{([^}]*)\}", sheet)
    assert match, f"the stylesheet declares no `{selector}` rule"
    return match.group(1)


def _section(sheet: str, selector: str, end_selector: str) -> str:
    """The slice of the sheet from *selector*'s rule to *end_selector*'s rule."""
    start = re.search(re.escape(selector) + r"\s*\{", sheet)
    end = re.search(re.escape(end_selector) + r"\s*\{", sheet)
    assert start and end and start.start() < end.start(), "the section moved in the sheet"
    return sheet[start.start():end.start()]


async def _config_page(config_dir, *, settings=None, roles=("Admin",)):
    server_obj = _namespace_server(settings=settings if settings is not None else BASE_SETTINGS)
    app, _ = app_with(config_dir, list(roles), servers=[server_obj])
    return await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")


# ---------------------------------------------------------------- 1. the page scrolls again

def test_the_scrolling_region_lays_its_children_out_at_their_natural_height(sheet):
    """`.wrap > *` makes the region overflow (and so scroll) instead of squeezing the cards."""
    assert ZERO_FLEX.search(_rule(sheet, ".wrap > *")), \
        "the scroll region's children must not shrink: without this a long page cannot be scrolled"


def test_the_top_bar_cannot_be_squeezed_by_a_tall_page(sheet):
    """The bar is a flex item of the same column: it needs the same pin or a long page shrinks it."""
    body = _rule(sheet, ".shell-bar, .top")
    assert ZERO_FLEX.search(body), "the top bar must keep its own height on a long page"
    assert re.search(r"height:\s*var\(--nav-h\)", body), "and that height is still the shell's token"


def test_the_scrolling_region_is_still_the_one_scroll_container(sheet):
    """The fix did not move the scrollbar: the region still owns `overflow: auto`."""
    body = _rule(sheet, ".wrap")
    assert re.search(r"overflow:\s*auto", body), "`.wrap` is still the region that scrolls"
    assert re.search(r"min-height:\s*0", body), "a flex item needs min-height: 0 to overflow at all"
 # and the long list page's own inner scrollbar is untouched
    assert re.search(r"overflow:\s*auto", _rule(sheet, ".tbl")), \
        "the table keeps scrolling inside its own box — a fix that breaks the lists is not a fix"


# ---------------------------------------------------------------- 2. the description field

@pytest.mark.asyncio
async def test_the_description_is_a_multi_line_field_with_the_actions_own_ceiling(config_dir):
    html = await _config_page(config_dir)
    match = TEXTAREA.search(html)
    assert match, "`description` must be rendered as a textarea"
    assert 'maxlength="2000"' in match.group(0), \
        "the browser's own maxlength is the action's declared ceiling (2000)"
    assert 'name="description"' in match.group(0)
    assert re.search(r'<textarea[^>]*>\s*Weekly campaign\s*</textarea>', html), \
        "the current value is the textarea's content"
    assert not re.search(r'<input[^>]*name="description"', html), \
        "and the one-line box is gone"


def test_the_multi_line_declaration_is_the_read_models_own(sheet):
    """Which field is long is a fact of the DECLARATION, not a key name spelled in the template."""
    from services.webservice.readmodels import serverconfig
    multiline = [field.key for field in serverconfig.EDITABLE_FIELDS if field.multiline]
    assert multiline == ["description"], multiline
    description = serverconfig.EDITABLE_BY_KEY["description"]
    assert description.maximum == 2000, "the field it was declared for is the 2000-character one"
    assert serverconfig.FieldView(key="k", label="l", kind="str", help="h", bound="str").multiline is False


def test_the_multi_line_row_uses_the_shared_control_column(sheet):
    """The one multi-line field is an ORDINARY shared row — no rule re-columns it.

    A wider control column for the textarea would push its control out of line with the rows above
    and below it, and at the real column width it collapses the LABEL to zero. So the multi-line row
    keeps the shared `.st-w` column like every other row — exactly as a multi-line control does on
    the node/settings pages.
    """
    assert not re.search(r":has\(textarea\)\s*\{", sheet), \
        "a rule re-columns the multi-line row away from the shared row"
    assert "resize" not in _rule(sheet, ".cfg-input"), \
        "the browser's own resize handle is deliberately left alone"


# ---------------------------------------------------------------- 3. the controls follow the theme

def test_the_scheme_is_declared_with_the_palette(sheet):
    """`color-scheme` is what makes the parts a stylesheet cannot reach follow the dark palette."""
    assert re.search(r"color-scheme:\s*dark", _rule(sheet, ":root")), \
        "without it every native control (select popup, steppers, checkbox) paints light"


def test_every_control_the_tab_renders_is_styled_from_the_shells_tokens(sheet):
    """Every field the tab renders is `.cfg-input` — the SAME control class the schema editor draws
    — so its look comes from ONE rule, stated from the shell's tokens rather than a type list.
    """
    block = _rule(sheet, ".cfg-input")
    for value in ("var(--ground-2)", "var(--text)", "1px solid var(--line-2)", "var(--radius-s)"):
        assert value in block, f"`.cfg-input` states no token `{value}`"
 # the states the tab really shows, each stated
    assert re.search(r"\.cfg-input:focus-visible", sheet), "a focus ring must be stated"
    assert "var(--text-3)" in _rule(sheet, ".cfg-input::placeholder"), \
        "the placeholder text must read as advice, in the theme's own muted tone"
    disabled = _rule(sheet, ".cfg-input:disabled")
    assert "var(--panel-2)" in disabled and "var(--text-3)" in disabled, \
        "a disabled control is the state this tab shows most: it must still follow the theme"
    assert re.search(r"accent-color:\s*var\(--accent\)", sheet), \
        "the Missions selection box keeps the platform's control, tinted with the accent"
    assert "var(--panel-2)" in _rule(sheet, ".cfg-form .notice > button"), \
        "the tab's own buttons are chips, not the engine's default button"
    assert "var(--panel-2)" in _rule(sheet, ".cfg-reveal")


def test_the_tabs_own_save_is_the_one_filled_control(sheet):
    """The DCS face's Save is FILLED with the accent; the two secondary chips keep the base look.

    Three buttons share this page and all three used to look alike. The main Save is now the one filled
    control, so it is hard to miss, and the fill is scoped with ``:not(:disabled)`` so a disabled Save
    still reads as the dim chip (its stated reason intact). It reads only the shell's tokens.
    """
    primary = re.search(r"\.cfg-form \.notice > button\.primary[^{]*\{([^}]*)\}", sheet)
    assert primary, "the primary Save rule must exist"
    assert ":not(:disabled)" in primary.group(0), "the fill must not paint a disabled Save"
    body = primary.group(1)
    assert "var(--accent)" in body, "the primary control is filled with the shell's accent"
    assert "var(--ground)" in body, "and its ink is the shell's own ground token"
 # the base chip (the two secondary controls) still reads as the panel chip
    assert "var(--panel-2)" in _rule(sheet, ".cfg-form .notice > button")


def test_nothing_on_the_tab_hard_codes_a_dark_colour(sheet):
    """Light must stay correct: the rules read tokens, they do not name a dark literal."""
    tab = _section(sheet, ".cfg-form", ".tabs")
    literals = re.findall(r"(?:background|color|border-color):\s*(#[0-9a-fA-F]{3,8})", tab)
    assert literals == [], f"the tab's rules name raw colours: {literals}"


# ---------------------------------------------------------------- 4. the password field

@pytest.mark.asyncio
async def test_the_password_is_a_masked_field_holding_the_stored_value(config_dir):
    html = await _config_page(config_dir)
    match = PASSWORD_FIELD.search(html)
    assert match, "the password row must render a normal masked field"
    assert match.group(1) == "hunter2", \
        "a set password renders the stored value, so the operator can read and keep it"
    assert "autocomplete=\"new-password\"" in match.group(0), \
        "so the browser never offers the operator's saved Discord login here"


@pytest.mark.asyncio
async def test_an_unset_password_renders_an_empty_field(config_dir):
    html = await _config_page(config_dir, settings=dict(BASE_SETTINGS, password=""))
    match = PASSWORD_FIELD.search(html)
    assert match and match.group(1) == "", "no value without a password stored"


@pytest.mark.asyncio
async def test_the_password_is_shown_while_the_hashes_never_are(config_dir):
    """The DCS password is renderable now; the coalition hashes still reach no markup."""
    html = await _config_page(config_dir)
    assert 'value="hunter2"' in html, "the password value must reach the field"
    for secret in ("BLUEHASH123", "REDHASH456"):
        assert secret not in html, f"the coalition hash {secret} was rendered"


@pytest.mark.asyncio
async def test_the_clear_control_is_gone_and_nothing_replaced_it(config_dir):
    """The field itself is the clear: no checkbox, no link, no button."""
    html = await _config_page(config_dir)
    assert "Clear password" not in html, "the checkbox-based clear control must be gone"
    assert "explicit clear control" not in html, "and no copy may still promise one"
    assert f'name="{actions_page.SERVER_CONFIG_CLEAR_FIELD}"' not in html, \
        "and no control posts the clear field any more"


@pytest.mark.asyncio
async def test_the_eye_is_a_real_button_carrying_its_own_state(config_dir):
    html = await _config_page(config_dir)
    assert len(REVEAL_BUTTON.findall(html)) == 1, "one eye, beside the one secret field"
    button = REVEAL_BUTTON.search(html).group(0)
    assert 'aria-pressed="false"' in button, "the announced state is on the control"
    assert 'aria-label="Show password"' in button and 'title="Show password"' in button, \
        "and it names what the next press does"
    assert "hidden" in button, \
        "it ships hidden: with JavaScript off a control that cannot toggle would lie"
    assert "g-show" in html and "g-hide" in html, "both glyphs ride in the button"
    assert re.search(r'<button type="button"[^>]*data-reveal="cfg-password"', html), \
        "it must never be a submit button"


@pytest.mark.asyncio
async def test_the_page_loads_the_one_script_that_enhances_it(config_dir):
    html = await _config_page(config_dir)
    assert '<script src="/static/reveal.js" defer></script>' in html


@pytest.mark.asyncio
async def test_the_reveal_script_is_served_and_never_copies_the_value(config_dir):
    """Revealing flips the masking and nothing else: no hidden copy, no data attribute, no log.

    The value is only ever read from, and typed into, the ONE field it came from, and it is carried
    nowhere else.
    """
    server_obj = _namespace_server(settings=BASE_SETTINGS)
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    async with asgi_client(app) as client:
        await _login(client)
        response = await client.get("/static/reveal.js")
    assert response.status_code == 200, "the shell's asset owner must serve it"
    source = response.text
    assert "field.type" in source, "the masking flip is the whole mechanism"
    assert "innerHTML" not in source and "localStorage" not in source, \
        "and it must not copy the secret anywhere else in the page"
    assert "sessionStorage" not in source and "createElement" not in source, \
        "no second node is built to carry the value"
    assert not re.search(r"console\.(log|debug|info|warn|error)", source), \
        "and it is never written to the console"
    writes = re.findall(r"\.value\s*=(?!=)", source)
    assert writes == [], f"the script must never write a value — the marker drop is gone: {writes}"


def test_every_asset_the_page_loads_is_shipped_where_it_is_asked_for():
    for name in ("shell.css", "submit.js", "reveal.js"):
        assert (STATIC / name).is_file(), f"/static/{name} is referenced but not shipped"


# ---------------------------------------------------------------- the capability's declaration

def test_the_configuration_tab_is_admin_only_with_a_manager_scope_grant():
    """The tab's roles are ``Admin``; the configuration write also carries a MANAGER scope grant.

    The role set is ``Admin`` only, so a DCS Admin still gets nothing (their scope is unscoped). A
    manager may open the tab for their own servers, with the denied rows (the port) and the channels
    card omitted and refused by the action.
    """
    from services.webservice.pages import server_detail as detail_page
    detail_page.capabilities()
    assert permissions.roles_for_capability(detail_page.CONFIG_CAPABILITY) == ("Admin",)
    assert permissions.scope_grants(detail_page.CONFIG_CAPABILITY) is True
    assert permissions.roles_for_capability(actions_page.SERVER_CONFIG_CAPABILITY) == ("Admin",)
    assert permissions.scope_grants(actions_page.SERVER_CONFIG_CAPABILITY) is True
