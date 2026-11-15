"""The console's ONE button language and ONE field language, and the Music tab adopting them.

The dialog owned the only styled button (``.dlg .btn``) and the only styled field
(``.dlg-field input|select``), so every control outside a dialog either fell back to the engine's grey
default or restated the same look in a page rule. Both looks are now declared ONCE at shell level —
``.btn`` / ``.btn.primary`` and the field box — and the containers that own a control (``.page-actions``,
``.search``, ``.sel-bar``) join the shared selector list instead of restating it.

Pinned here, in order:

* the sheet declares ``.btn`` once and ``.btn.primary`` as the accent fill, with the hover, focus and
  disabled states, and no second definition of the dialog's button survives;
* the page action band, the search bar and the selection bar resolve to the shared rule — they appear
  ONLY in selector lists that also name ``.btn``, so a grey ``background: var(--panel-2)`` slab cannot
  come back under them, and the three retired container rules are gone from the sheet;
* the field box is declared once for ``input.field`` / ``.field`` / the dialog's ``.dlg-field``;
* the Music tab's controls carry the shared field class (asserted on the rendered server page through
  the real app, and on the template for the branches the fixture does not reach);
* its transport still posts the same path and the same fields, and a caller without ``music.control``
  still sees no control at all — omitted, never drawn disabled.

The rendered LOOK is not pinned by anything here: whether the accent reads right is an eye check.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_webui_music_tab import (  # noqa: F401 - fixtures imported on purpose
    RADIO, SERVER_A, fleet, music_registry, music_service, radio,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, open_page, sign_in, token_of,
)

TEMPLATES = (Path(__file__).resolve().parent.parent
             / "services" / "webservice" / "templates")
SHEET = (Path(__file__).resolve().parent.parent
         / "services" / "webservice" / "static" / "shell.css")


# ------------------------------------------------------------------------------- the stylesheet

def rules(sheet: str) -> list[tuple[list[str], str]]:
    """Every declaration block as ``(selector tokens, body)``.

    Comments are stripped (a comment that NAMES a selector must not read as a rule), and at-rule
    preludes (``@media (…) {``) are dropped while their braces stay, so a rule nested inside one is
    still seen under its OWN selector instead of under a prelude prefix.
    """
    text = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    text = re.sub(r"@[A-Za-z-]+[^{};]*\{", "", text)
    out = []
    for match in re.finditer(r"([^{}]*)\{([^{}]*)\}", text):
        selector = " ".join(match.group(1).split())
        tokens = [" ".join(part.split()) for part in selector.split(",") if part.strip()]
        out.append((tokens, " ".join(match.group(2).split())))
    return out


@pytest.fixture(scope="module")
def sheet_rules() -> list[tuple[list[str], str]]:
    return rules(SHEET.read_text(encoding="utf-8"))


def _only(rules_list, selector: str) -> list[str]:
    """The bodies of every rule whose selector LIST names ``selector`` as one of its own members."""
    return [body for tokens, body in rules_list if selector in tokens]


def _declares(body: str, prop: str, value: str) -> bool:
    return re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*{re.escape(value)}\s*(?:;|$)", body) is not None


# ------------------------------------------------------------------ 1. one button language

def test_the_shell_declares_one_button_and_it_is_the_dialogs_old_shape(sheet_rules):
    """`.btn` is stated once, with the dialog's metrics — the look every other surface now inherits."""
    bodies = _only(sheet_rules, ".btn")
    assert bodies, "the sheet declares no `.btn` rule"
    base = [body for body in bodies if _declares(body, "padding", "8px 14px")]
    assert len(base) == 1, "`.btn` must be declared once, with the dialog's own 8px 14px padding"
    body = base[0]
    for prop, value in (("border-radius", "var(--radius-s)"),
                        ("border", "1px solid var(--line-2)"),
                        ("background", "var(--panel-2)"),
                        ("color", "var(--text)")):
        assert _declares(body, prop, value), f"`.btn` lost `{prop}: {value}`"


def test_the_primary_button_is_the_accent_fill_with_its_own_states(sheet_rules):
    """`.btn.primary` is amber-on-ground, and hover/disabled are stated rather than inherited."""
    bodies = _only(sheet_rules, ".btn.primary")
    assert len(bodies) == 1, "`.btn.primary` must be declared once"
    body = bodies[0]
    assert _declares(body, "background", "var(--accent)"), "the primary button is not the accent fill"
    assert _declares(body, "color", "var(--ground)"), \
        "the accent fill's ink is not the palette's own ground token"
    assert _only(sheet_rules, ".btn.primary:hover"), "the primary button has no hover state"
    disabled = _only(sheet_rules, ".btn.primary:disabled")
    assert disabled and "var(--text-3)" in disabled[0], \
        "a disabled primary must fall back to the dim chip, not stay filled"


def test_the_button_states_are_declared_for_the_shared_class(sheet_rules):
    """hover, keyboard focus and disabled — the three states a control needs to be usable."""
    assert _only(sheet_rules, ".btn:hover"), "`.btn` has no hover state"
    assert _only(sheet_rules, ".btn:focus-visible"), "`.btn` has no focus ring"
    disabled = _only(sheet_rules, ".btn:disabled")
    assert disabled, "`.btn` has no disabled state"
    assert "not-allowed" in disabled[0], "the disabled button does not refuse the cursor"


def test_no_second_definition_of_the_dialogs_button_survives(sheet_rules):
    """The dialog keeps NO copy of its own: two definitions of one look is what drifts."""
    assert _only(sheet_rules, ".dlg .btn") == [], \
        "`.dlg .btn` re-declares the shared look; the dialog's buttons are `.btn`"
    assert _only(sheet_rules, ".dlg .btn.danger") == []


def test_the_danger_button_keeps_its_tokens(sheet_rules):
    bodies = _only(sheet_rules, ".btn.danger")
    assert len(bodies) == 1, "the destructive button lost its one definition"
    assert "var(--danger-btn-top)" in bodies[0]


# --------------------------------------- 2. the grey containers resolve to the shared button

#: every container that owns a button and must therefore inherit the shared rule, never restate it
CONTAINERS = (".page-actions button", ".search button", ".sel-bar button")


@pytest.mark.parametrize("container", CONTAINERS)
def test_the_button_container_resolves_to_the_shared_rule(sheet_rules, container):
    """A container appears ONLY beside `.btn` in a selector list, so it cannot own a second look."""
    owners = [(tokens, body) for tokens, body in sheet_rules if container in tokens]
    assert owners, f"`{container}` is styled nowhere — its buttons fall back to the engine default"
    for tokens, body in owners:
        assert ".btn" in tokens, \
            f"`{container}` is styled by a rule of its own ({body[:60]}…), not by the shared button"
        assert ".btn" in tokens


@pytest.mark.parametrize("container", CONTAINERS)
def test_no_grey_slab_survives_under_a_shared_button(sheet_rules, container):
    """The resting fill of every rule that names the container is the SHARED one, with no exception."""
    for tokens, body in sheet_rules:
        if container in tokens:
            assert _declares(body, "background", "var(--panel-2)") is False or ".btn" in tokens, \
                f"a `{container}` rule hard-codes the grey resting fill"


@pytest.mark.parametrize("retired", (".log-card > .ft button", ".log-picker button"))
def test_the_retired_container_rules_are_gone(sheet_rules, retired):
    """These buttons carry `.btn` already; their container rule was the same look written twice."""
    assert _only(sheet_rules, retired) == [], \
        f"`{retired}` restates the shared button for a control that already wears `.btn`"


def test_the_action_band_still_lays_its_controls_out(sheet_rules):
    """Removing the look must not remove the band: the gap between the page's controls stays."""
    bodies = _only(sheet_rules, ".page-actions")
    assert bodies and "display: flex" in bodies[0]


# --------------------------------------------------------------- 3. one field language

#: the three shapes that address the field box: the class on the control, the generic wrapper, and the
#: dialog's own wrapper (which keeps the label-under-box column and nothing of the box's look)
FIELD_SHAPES = ("input.field", "select.field", ".field input", ".field select",
                ".dlg-field input", ".dlg-field textarea", ".dlg-field select")


def test_the_field_box_is_declared_once_for_every_shape(sheet_rules):
    """One block paints the box; the dialog and a page form cannot drift because they share it."""
    for shape in FIELD_SHAPES:
        owners = [(tokens, body) for tokens, body in sheet_rules
                  if shape in tokens and _declares(body, "background", "var(--ground-2)")]
        assert len(owners) == 1, f"`{shape}` is painted by {len(owners)} rules, not one"
        tokens, body = owners[0]
        assert all(other in tokens for other in FIELD_SHAPES), \
            f"`{shape}` is declared apart from the rest of the field language"
        for prop, value in (("padding", "7px 9px"), ("color", "var(--text)"),
                            ("border", "1px solid var(--line-2)"),
                            ("border-radius", "var(--radius-s)")):
            assert _declares(body, prop, value), f"the field box lost `{prop}: {value}`"


@pytest.mark.parametrize("shape", FIELD_SHAPES)
def test_the_field_focus_ring_is_the_one_ring(sheet_rules, shape):
    owners = _only(sheet_rules, f"{shape}:focus-visible")
    assert owners, f"`{shape}` has no focus ring"
    assert all("var(--accent)" in body for body in owners)


# ------------------------------------------------------- 4. the templates that carry the accent

#: the one template that still renders a page-level control, and the two that must NOT
CARRYING = "list.html"
TABLE = "_table.html"


def test_the_servers_card_action_wears_the_accent_button():
    """`Add server` is what the Servers page is FOR, so its card header holds the accent control.

    The control is rendered by the TABLE component (the card header it lives in is that component's
    header), handed in as `card_action`, and the page templates themselves carry none.
    """
    text = (TEMPLATES / TABLE).read_text(encoding="utf-8")
    control = re.search(r"<button [^>]*title=\"\{\{ say\(card_action\.title\) \}\}\"", text)
    assert control, f"{TABLE} renders no page-level card control"
    classes = re.search(r'class="([^"]*)"', control.group(0))
    assert classes and {"btn", "primary"} <= set(classes.group(1).split()), \
        f"{TABLE}'s card control is not the shared accent button"


@pytest.mark.parametrize("name", (CARRYING, "dashboard.html"))
def test_the_page_templates_carry_no_page_level_control(name):
    """A page's own markup draws no page control: the card header owns it."""
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    assert "{% if card_action" not in text, "the page draws the card's control itself"
    assert '<div class="page-actions">' not in text


# ----------------------------------------------------------------- the Music tab, as rendered

MUSIC = (TEMPLATES / "music_body.html")
TAG = re.compile(r"<(input|select|textarea|button|a)\b([^>]*)>", re.I)


def attributes(raw: str) -> dict[str, str]:
    return {name: value for name, value in re.findall(r'([\w:-]+)="([^"]*)"', raw)}


def tags(markup: str, kind: str | None = None) -> list[dict[str, str]]:
    return [attributes(raw) for tag, raw in TAG.findall(markup) if kind is None or tag == kind]


def forms(page: str, action: str) -> list[str]:
    """Every rendered ``<form … action="<action>">…</form>`` block.

    The transport is one form PER VERB (so no control nests and each posts its own word), which is why
    this returns the list: a single-match helper would silently see only the first verb.
    """
    found = re.findall(r'<form [^>]*action="%s"[^>]*>.*?</form>' % re.escape(action), page, re.S)
    assert found, f"the page renders no form posting to {action}"
    return found


def form(page: str, action: str) -> str:
    """The ONE form posting to *action* — a surface that drew two of them would be ambiguous."""
    found = forms(page, action)
    assert len(found) == 1, f"the page renders {len(found)} forms posting to {action}"
    return found[0]


def classes(tag: dict[str, str]) -> set[str]:
    return set((tag.get("class") or "").split())


async def open_tab(config_dir, clean_registry, *, user="specialk", server_name=SERVER_A):
    """The Music tab of SERVER_A, rendered through the real app by an account that may write."""
    app = build(config_dir, fleet(), plugins=("mission", "music"))
    async with client(app) as http:
        await sign_in(http, user)
        page = await open_page(http, f"/fleet/{server_name}?tab=music")
    return page


@pytest.mark.asyncio
async def test_the_settings_controls_wear_the_shared_field_class(config_dir, music_registry,
                                                                music_service, clean_registry):
    """frequency, modulation, coalition, volume and display-name: five controls, one box."""
    from services.webservice.pages import actions as actions_page

    page = await open_tab(config_dir, clean_registry)
    settings = form(page, actions_page.MUSIC_SETTINGS_PATH)

    for name in ("frequency", "modulation", "coalition", "volume", "display_name"):
        control = [tag for tag in tags(settings) if tag.get("name") == name]
        assert len(control) == 1, f"the settings form renders no single `{name}` control"
        assert "field" in classes(control[0]), \
            f"`{name}` does not wear the shared field class: {control[0]}"


@pytest.mark.asyncio
async def test_the_mode_and_playlist_picks_wear_the_shared_field_class(config_dir, music_registry,
                                                                     music_service, clean_registry):
    from services.webservice.pages import actions as actions_page

    page = await open_tab(config_dir, clean_registry)
    # THE MODE IS A TOGGLE, NOT A PICK: its button states the switch in `aria-label`/`title` and
    # posts the other word in the body, so the tab's picks are the playlist and the two settings
    # selects — every one of them the shared field box.
    mode = [form for form in forms(page, actions_page.MUSIC_CONTROL_PATH) if tags(form, "select")]
    playlist = [tags(form(page, actions_page.MUSIC_PLAYLIST_PATH), "select")]
    assert mode == [], "the mode is a toggle: a pick came back"
    assert len(playlist) == 1, "the playlist pick is missing from its form"
    assert "field" in classes(playlist[0][0]), f"the pick is not a shared field: {playlist[0][0]}"
    toggle = [button for form in forms(page, actions_page.MUSIC_CONTROL_PATH)
              for button in tags(form, "button")]
    assert len(toggle) == 4, f"the transport is four glyph buttons: {toggle}"
    for button in toggle:
        assert "pl-btn" in classes(button), f"a transport button is not a glyph button: {button}"
        assert button.get("aria-label"), f"a glyph button carries no accessible name: {button}"
        assert button.get("title"), f"a glyph button carries no title: {button}"


@pytest.mark.asyncio
async def test_every_select_on_the_tab_wears_the_field_class(config_dir, music_registry,
                                                            music_service, clean_registry):
    """No unclassed pick survives anywhere on the surface — the exhaustive form of the two above."""
    page = await open_tab(config_dir, clean_registry)
    selects = tags(page, "select")
    assert len(selects) == 3, f"the tab renders {len(selects)} picks, expected three"
    for select in selects:
        assert "field" in classes(select), f"an unclassed pick survives: {select}"


@pytest.mark.asyncio
async def test_the_transport_buttons_are_glyph_buttons_and_post_the_same_fields(
        config_dir, music_registry, music_service, clean_registry):
    """The restyle moved the CLASS and the drawing: the verb, the path and every field are untouched."""
    from services.webservice.pages import actions as actions_page
    from services.webservice import session

    page = await open_tab(config_dir, clean_registry)
    control = forms(page, actions_page.MUSIC_CONTROL_PATH)
    assert len(control) == 4, "the transport is three verb forms plus the mode form"
    buttons = [button for block in control for button in tags(block, "button")]
    assert len(buttons) == 4, "one button per control form"
    for button in buttons:
        # A GLYPH BUTTON, not a word button: the 44px icon house of the player's own strip
        assert "pl-btn" in classes(button), f"a transport button is not the glyph button: {button}"
        assert button.get("aria-label"), f"a glyph button names nothing: {button}"

    verbs = {tag.get("value") for block in control for tag in tags(block, "input")
             if tag.get("name") == actions_page.MUSIC_CONTROL_FIELD}
    assert verbs == {"play", "stop", "skip", "mode"}, \
        f"the control form posts a different verb set: {sorted(verbs)}"
    for block in control:
        hidden = {tag.get("name") for tag in tags(block, "input")}
        assert {"server", "radio", session.CSRF_FIELD} <= hidden, \
            f"a control form lost its scope fields: {hidden}"
        assert SERVER_A in {tag.get("value") for tag in tags(block, "input")}
        assert RADIO in {tag.get("value") for tag in tags(block, "input")}


@pytest.mark.asyncio
async def test_the_settings_save_is_the_accent_button(config_dir, music_registry,
                                                     music_service, clean_registry):
    from services.webservice.pages import actions as actions_page

    page = await open_tab(config_dir, clean_registry)
    buttons = tags(form(page, actions_page.MUSIC_SETTINGS_PATH), "button")
    assert [classes(button) for button in buttons] == [{"btn", "primary"}], \
        f"the settings panel's Save is not the shared accent button: {buttons}"


@pytest.mark.asyncio
async def test_a_reader_without_music_control_sees_no_button_and_no_field(
        config_dir, music_registry, music_service, clean_registry, monkeypatch):
    """Omitted, never disabled: the shared classes reach nothing a reader may not press.

    The app is built BEFORE the capability is narrowed, because building it is what installs the
    process's capability map — a patch applied first is replaced by the build. The account is a
    ``DCS Admin``: it reads the tab and holds nothing that may write to it.
    """
    from services.webservice import permissions
    from services.webservice.pages import actions as actions_page

    app = build(config_dir, fleet(), plugins=("mission", "music"))
    monkeypatch.setitem(permissions.CAPABILITIES, actions_page.MUSIC_CONTROL_CAPABILITY, ("Admin",))
    monkeypatch.setitem(permissions.SCOPE_GRANTS, actions_page.MUSIC_CONTROL_CAPABILITY, False)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, f"/fleet/{SERVER_A}?tab=music")

    assert "Alpha Radio" in page, "the radios are still readable"
    for path in (actions_page.MUSIC_CONTROL_PATH, actions_page.MUSIC_PLAYLIST_PATH,
                 actions_page.MUSIC_SETTINGS_PATH):
        assert f'action="{path}"' not in page, f"a control was drawn for {path}"
    # the playlist-EDITOR link is `music.edit`'s control and stays; nothing this right withholds is drawn
    buttons = tags(page, "button")
    assert buttons, "the page rendered no button at all — the sign-out one is always there"
    assert not any("btn" in classes(button) for button in buttons), \
        "a shared button was drawn for a caller who may not control the radio"
    assert tags(page, "select") == [], "a pick was drawn for a caller who may not control the radio"
    assert not [tag for tag in tags(page, "input") if tag.get("type") == "text"], \
        "a text field was drawn for a caller who may not control the radio"


# --------------------------------------------- the template's branches the fixture cannot reach

def template_controls() -> list[dict[str, str]]:
    markup = MUSIC.read_text(encoding="utf-8")
    markup = re.sub(r"\{#.*?#\}", "", markup, flags=re.S)      # the comments name no control
    return tags(markup)


def test_every_control_in_the_template_carries_the_field_class():
    """Read off the TEMPLATE, so a branch the fixture never renders is covered too."""
    controls = [tag for tag in template_controls() if tag.get("name")]
    assert controls, "the template scans as carrying no control at all"
    unclassed = [tag for tag in controls
                 if tag.get("type") == "text" or tag.get("name") in (
                     "frequency", "modulation", "coalition", "volume", "display_name")
                 or tag.get("name", "").startswith("{{ music_")]
    assert unclassed, "the scan found no field to check — the shapes it looks for are gone"
    for tag in unclassed:
        if tag.get("type") == "hidden":
            continue
        assert "field" in classes(tag), f"an unclassed field survives in the template: {tag}"


def test_every_word_button_in_the_template_is_the_shared_button():
    """The tab's own controls spell a WORD, so they are the shared `.btn` — the assign and the save.

    THE TRANSPORT IS NOT ONE OF THEM: its four buttons are GLYPH buttons in the player partial
    (`_music_player.html`, `.pl-btn`), which is where a drawing belongs. This pin reads the TAB, so a
    word button added here still has to wear the console's one button.
    """
    buttons = tags(MUSIC.read_text(encoding="utf-8"), "button")
    assert len(buttons) == 2, f"the tab renders {len(buttons)} word buttons, expected two"
    for button in buttons:
        assert "btn" in classes(button), f"a template button is not the shared button: {button}"
    assert "pl-btn" not in MUSIC.read_text(encoding="utf-8"), \
        "the tab draws a glyph button of its own instead of the shared player"


def test_the_playlist_link_is_a_plain_anchor_not_a_glyph_house():
    """`.act-btn` is a 28px square built for a 16px glyph; the playlist link spells a NAME.

    The link moved into the player (`_music_player.html`) with the rest of the card, so this pin reads
    the PLAYER partial: the playlist's name is a plain anchor — the console's link colour, no `.btn`
    slab and no glyph house around a word.
    """
    player = (TEMPLATES / "_music_player.html")
    assert player.exists(), "the shared player partial is gone"
    links = tags(player.read_text(encoding="utf-8"), "a")
    assert links, "the player renders no playlist link"
    for link in links:
        assert "act-btn" not in classes(link), f"a name wears the glyph house: {link}"
        assert "btn" not in classes(link), f"a name wears the button slab: {link}"

    # the tab imports that ONE partial and draws no link of its own
    assert '_music_player.html' in MUSIC.read_text(encoding="utf-8")
    assert tags(MUSIC.read_text(encoding="utf-8"), "a") == [], \
        "the tab draws a link the player already draws"
