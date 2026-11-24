"""The palette wizard, driven in a real browser: one colour in, the sheet a site wears out.

The wizard is a UI over `theme.expert` and nothing else, so these pins are about three things a model
test cannot see: the DERIVATION's numbers (23 declarations and ten measured pairs, read off the
screen and out of the module the page loaded), the PREVIEW being the site's own document with the
candidate palette set on its root, and the HANDOFF writing the sheet's text into the editor that was
already on the page — with no request of its own and Save still the only write.

The expected numbers below are the design record's own derivation, transcribed: the wizard's output
for base `#4a7fd1` on a dark page.
"""
from __future__ import annotations

import re

import pytest

from services.webservice import sites, theme_css
from test_webui_site_settings import (ADMIN, GROUP_ADMIN,  # noqa: F401 - fixtures
                                      app, client, config_dir, notice_of, open_page, post,
                                      sign_in_as, site_file)
from test_webui_site_settings_page import (PASSWORD, SITE_KEY,  # noqa: F401
                                           _probe, live_config, served, sheet_config, sheet_served)

#: the 23 declarations the wizard writes, in the handoff's order and with its values
EXPECTED_VALUES = {
    "--ground": "#0b1016", "--stage": "#05070d", "--band": "#11161f",
    "--panel": "#141a23", "--panel-2": "#1a212b", "--line": "#303846", "--line-2": "#5c6778",
    "--fg": "#f0f5fd", "--fg-2": "#c9d0da", "--fg-3": "#979fab",
    "--brass": "#5085d8", "--brass-hi": "#65a1fe", "--brass-dim": "#275398",
    "--brass-wash": "rgba(80, 133, 216, 0.1)", "--sage": "#a9c7b4",
    "--hero-glow": "rgba(80, 133, 216, 0.17)", "--hero-sage": "rgba(169, 199, 180, 0.05)",
    "--wash": "rgba(240, 245, 253, 0.06)", "--hair": "rgba(240, 245, 253, 0.06)",
    "--pulse-ring": "rgba(169, 199, 180, 0.55)", "--pulse-none": "rgba(169, 199, 180, 0)",
    "--pulse-edge": "rgba(169, 199, 180, 0.35)", "--black": "#000000",
}

#: the ten pairs the rule tests, with the ratio it measures on this base
EXPECTED_PAIRS = [
    ("--fg", "--ground", 17.54, 4.5),
    ("--fg-2", "--ground", 12.30, 4.5),
    ("--fg-3", "--ground", 7.16, 4.5),
    ("--fg", "--panel", 16.07, 4.5),
    ("--brass", "--ground", 5.20, 4.5),
    ("--brass", "--panel", 4.77, 4.5),
    ("--brass-hi", "--ground", 7.37, 4.5),
    ("--line-2", "--ground", 3.33, 3.0),
    ("--line-2", "--panel", 3.05, 3.0),
    ("--sage", "--ground", 10.48, 3.0),
]

#: the six rows: the step's number and name, the declarations it emits, its hexes and its ratio
EXPECTED_STEPS = [
    ("ground", "01 ground", "--ground · --stage", "#0b1016 #05070d", "5.20:1", "--brass / --ground"),
    ("panel", "02 panel", "--panel · --panel-2 · --band", "#141a23 #1a212b #11161f", "16.07:1",
     "--fg / --panel"),
    ("line", "03 line", "--line · --line-2", "#303846 #5c6778", "3.05:1", "--line-2 / --panel"),
    ("accent", "04 accent", "--brass · --brass-hi · --brass-dim", "#5085d8 #65a1fe #275398",
     "4.77:1", "--brass / --panel"),
    ("text", "05 text", "--fg", "#f0f5fd", "17.54:1", "--fg / --ground"),
    ("steps", "06 text steps", "--fg-2 · --fg-3", "#c9d0da #979fab", "7.16:1", "--fg-3 / --ground"),
]

#: the handoff's sheet, as the wizard's own grammar spells it: 23 declarations, 580 characters
EXPECTED_TEXT = "\n".join(f"{name}: {value};" for name, value in EXPECTED_VALUES.items())
EXPECTED_CHARACTERS = 580


def _wizard(report: dict) -> dict:
    wizard = report["wizard"]
    assert wizard["shown"], "the admin's card draws no palette wizard"
    return wizard


def _steps(wizard: dict) -> list:
    return [(row["id"], row["name"], row["tokens"], row["hexes"], row["ratio"], row["pair"])
            for row in wizard["steps"]]


# ======================================= pin 1 · the derivation is the note's own numbers

def test_the_derivation_matches_the_note_declaration_by_declaration(served):
    """PIN 1: 23 declarations and ten measured pairs, read out of the module the page loaded.

    The values are compared, not the prose: every token's hex or rgba string, in the handoff's order,
    and each pair's measured ratio against the minimum its role needs.
    """
    wizard = _wizard(_probe(served, "wizard"))
    derive = wizard["derive"]

    assert list(derive["values"]) == list(EXPECTED_VALUES), (
        f"the wizard writes {list(derive['values'])}")
    assert derive["values"] == EXPECTED_VALUES, (
        "the derivation is not the note's: "
        + ", ".join(f"{name}={derive['values'].get(name)} (want {value})"
                    for name, value in EXPECTED_VALUES.items()
                    if derive["values"].get(name) != value))

    measured = [(token, against, round(ratio, 2), minimum)
                for token, against, ratio, minimum in derive["checks"]]
    assert measured == EXPECTED_PAIRS, f"the ten pairs measure {measured}"

    text = derive["text"]
    assert len(text.split("\n")) == 23, f"the sheet carries {len(text.split(chr(10)))} declarations"
    assert len(text) == EXPECTED_CHARACTERS, f"the sheet is {len(text)} characters"


def test_the_six_steps_print_the_number_the_tokens_and_every_hex(served):
    """PIN 1, on the screen: six numbered rows, the token names, every value's hex, the tested pair."""
    wizard = _wizard(_probe(served, "wizard"))
    assert wizard["step1"]["where"] == "Step 1 · the starting point"
    assert wizard["step2"]["where"] == "Step 2 · the six steps"
    assert wizard["step2"]["shown"] is True, "the derive press did not draw step 2"

    assert _steps(wizard) == EXPECTED_STEPS, f"the six rows read {_steps(wizard)}"
    # every chip states a measured pair at or above its role's minimum
    assert all(row["state"] == "ok" for row in wizard["steps"]), wizard["steps"]
    assert all(row["swatch"] not in ("", "rgba(0, 0, 0, 0)") for row in wizard["steps"]), (
        "a step's swatch paints nothing")

    # the constants the palette does not own are printed as carried, by name
    constants = wizard["constants"]
    for name in ("--sage", "--hero-sage", "--pulse-*", "--black", "medal", "font families"):
        assert name in constants, f"the carried constants do not name {name}: {constants!r}"


def test_the_rule_moves_the_pair_it_cannot_pass_and_says_so(served):
    """PIN 1 and the hard rule: a failing pair is MOVED, and the move is on the screen."""
    wizard = _wizard(_probe(served, "wizard"))
    moved = wizard["moves"]
    assert moved, "the rule moved --line-2 and printed nothing"
    assert any("--line-2 moved L 0.44 → 0.49" in line for line in moved), moved
    assert any("3.05:1" in line for line in moved), moved
    assert any("ui needs 3.0:1" in line for line in moved), moved
    # the accent itself was a nudge, so the handoff stayed open and the note says what it did
    assert wizard["warn"]["hidden"] is True, wizard["warn"]
    assert wizard["useDisabled"] is False
    assert any("was used as the accent" in line and "#5085d8" in line for line in moved), moved


# ==================================================== pin 2 · the base that has to become a colour

def test_a_base_that_cannot_be_the_accent_is_moved_and_the_handoff_stays_closed(served):
    """PIN 2: `#1f3a5f` cannot be an accent on this ground; the move is stated and Use is disabled."""
    wizard = _wizard(_probe(served, "wizardadjust"))

    warn = wizard["warn"]
    assert warn["hidden"] is False, "the base was moved with no warning"
    assert "#1f3a5f sits at OKLCH lightness 0.35" in warn["say"], warn["say"]
    assert "needs 0.62" in warn["say"], warn["say"]
    assert "built from #6988b3" in warn["say"], warn["say"]
    assert "a different colour and not a nudge" in warn["say"], warn["say"]
    assert warn["use"] == "Use #6988b3", warn["use"]
    assert wizard["useDisabled"] is True, "the handoff was open on a palette the base cannot make"

    accent = next(row for row in wizard["steps"] if row["id"] == "accent")
    assert accent["hexes"].split()[0] == "#6988b3", accent
    assert accent["ratio"] == "4.81:1", accent
    assert accent["state"] == "ok", "the moved accent's pair does not pass"
    assert float(accent["ratio"].rstrip(":1")) >= 4.5

    accepted = wizard["accepted"]
    assert accepted["useDisabled"] is False, "pressing the offered colour did not open the handoff"
    assert accepted["warnHidden"] is True, "the warning stayed after it was answered"
    assert accepted["steps"] == wizard["steps"], "answering the warning changed the palette it showed"
    assert wizard["sheet_untouched"], "the wizard wrote the sheet before the handoff was asked for"


# ============================================ pins 3, 4 and 5 · the preview, the handoff, no write

def test_the_preview_is_the_site_s_document_wearing_the_candidate(served):
    """PIN 5: the box's own root, read back with `getComputedStyle`, carries the candidate values."""
    wizard = _wizard(_probe(served, "wizard"))
    before, after = wizard["before_preview"], wizard["after_preview"]
    assert before["--ground"] != after["--ground"], (
        f"the preview did not change: it wears {before['--ground']} either way")
    for name in ("--ground", "--panel", "--line-2", "--brass", "--fg", "--fg-3"):
        assert after[name] == EXPECTED_VALUES[name], (
            f"the preview's {name} is {after[name]!r}, the derivation says "
            f"{EXPECTED_VALUES[name]!r}")
    assert after["theme"] == "dark", f"the preview is served {after['theme']!r}"
    assert "--ground" in after["inline"], (
        f"the palette is not injected into the document's own root: {after['inline']!r}")


def test_the_handoff_writes_a_valid_sheet_and_no_request_is_made(served, live_config):
    """PINS 3 and 4: the text is the editor's, the validator accepts it, and nothing was posted.

    Then the card's own Save — the only write — and the site serves the composed sheet it wrote.
    """
    wizard = _wizard(_probe(served, "wizard"))

    # PIN 4: derive and hand off make no request at all
    assert wizard["requests_before_handoff"] == [], (
        f"the wizard talked to the server while deriving: {wizard['requests_before_handoff']}")
    assert wizard["requests_after_handoff"] == [], (
        f"the handoff posted something: {wizard['requests_after_handoff']}")

    handoff = wizard["handoff"]
    assert handoff["wizardHidden"] is True, "the wizard stayed open over the sheet"
    assert handoff["stripHidden"] is False, "the handoff strip is not drawn"
    assert "no longer applied" in handoff["strip"], handoff["strip"]
    assert "Back to the wizard" in handoff["strip"], handoff["strip"]

    sheet = handoff["sheet"]
    assert sheet["text"] == EXPECTED_TEXT, (
        "the editor does not hold the wizard's sheet:\n" + sheet["text"])
    # PIN 3: the contract the validator is — the text is accepted, and it is exactly 23 declarations
    assert theme_css.validate(sheet["text"]) is None, theme_css.validate(sheet["text"])
    assert sheet["badge"] == "from the wizard", sheet["badge"]
    assert sheet["when"] == "written by the wizard · linked after the site's own sheet", sheet["when"]
    assert "580 used" in sheet["count"], sheet["count"]
    assert sheet["verdict"] == ("Accepted — 23 declarations, 10 pairs tested, every one at or above "
                                "AA. Nothing is saved until Save."), sheet["verdict"]

    # one palette, one choice: the preset is released and NO tile is marked — the site names a sheet
    preset = handoff["preset"]
    assert preset["checked"] == "", f"a preset is still selected: {preset['checked']!r}"
    assert preset["on"] == [False, False, False, False, False], preset["on"]
    assert [badge for badge in preset["badges"] if badge] == [], (
        f"a tile still claims to be in use while the sheet holds the palette: {preset['badges']}")
    # …and the head says which of the three the site names now, once
    head = handoff["head"]
    assert head["words"] == "An expert sheet", head
    assert head["key"] == "theme.expert", head
    assert head["value"] == "23 declarations", head
    assert handoff["sheetDrawn"] is True, "the handoff wrote the sheet and left it hidden"
    assert handoff["toggle"] is True, "the handoff wrote a sheet and left Expert Mode closed"

    # BACK returns to the six steps, from memory, with no re-derivation
    again = wizard["again"]
    assert again is not None, "the handoff strip draws no way back"
    assert again["wizardHidden"] is False and again["stripHidden"] is True
    assert _steps({"steps": again["steps"]}) == EXPECTED_STEPS, "Back lost the palette"

    # the card's Save is still the only write
    assert any("Saved" in notice for notice in wizard["saved"]["notices"]), wizard["saved"]
    assert wizard["stored"]["text"] == EXPECTED_TEXT, wizard["stored"]["text"]

    block = sites.load_site_block(live_config) or {}
    theme = (block.get("root") or {}).get("theme") or {}
    # a form post normalizes a textarea's newlines to CRLF; the value the sheet means is the same text
    stored_expert = (theme.get("expert") or "").replace("\r\n", "\n")
    assert stored_expert == EXPECTED_TEXT, stored_expert
    assert theme_css.validate(theme.get("expert")) is None, theme_css.validate(theme.get("expert"))
    assert not theme.get("preset"), f"the save left a preset beside the sheet: {theme}"

    # §2.7: with a sheet stored, the wizard opens FROM it — a fourth tile, pressed, and the sheet's
    # badge is the shipped `edited`
    reloaded = wizard["reloaded"]
    assert reloaded is not None, "the reloaded card carries no wizard payload"
    assert reloaded["start"] == "stored", reloaded["start"]
    assert [tile["id"] for tile in reloaded["tiles"]] == ["preset", "now", "one", "stored"], (
        [tile["id"] for tile in reloaded["tiles"]])
    assert [tile["on"] for tile in reloaded["tiles"]] == [False, False, False, True], (
        [tile["on"] for tile in reloaded["tiles"]])
    assert wizard["stored"]["badge"] == "edited", wizard["stored"]["badge"]

    served_sheet = wizard["sheet"]
    assert served_sheet["status"] == 200, served_sheet
    composed = served_sheet["text"]
    assert composed.startswith(":root {"), composed[:80]
    for name, value in (("--ground", "#0b1016"), ("--brass", "#5085d8"), ("--fg", "#f0f5fd")):
        assert f"{name}: {value};" in composed, f"the served sheet does not carry {name}: {value}"

    # …and the public site is PAINTED with it: the visitor's own ground and accent
    public = wizard["public"]
    assert public["ground"] == "rgb(11, 16, 22)", (
        f"the site renders on {public['ground']}, not the palette's ground")
    assert public["accent"] == "#5085d8", public["accent"]
    assert public["text"] == "#f0f5fd", public["text"]


# ============================================ pin 6 · the group admin's one row, and the closed door

@pytest.mark.asyncio
async def test_a_group_admin_gets_one_row_and_no_wizard(app):
    """PIN 6: `site.write` without `site.expert` draws the row the note states, not a disabled wizard."""
    async with client(app) as http:
        await sign_in_as(http, GROUP_ADMIN)
        html = await open_page(http)
    assert "data-sc-wizard" not in html, "a group admin was drawn the palette wizard"
    assert "data-sc-wiz-use" not in html, "a group admin was drawn the wizard's handoff control"
    assert "A palette of your own" in html, "the row that replaces the wizard is missing"
    assert "Not available to a group admin" in html, html[-600:]
    assert "Ask for one: the sheet it carries is theirs to edit" in html, \
        "the row no longer says who writes the palette"
    assert "Admin only" in html
    assert "data-sc-expert" not in html, "a group admin was drawn the sheet the wizard writes"


@pytest.mark.asyncio
async def test_the_admin_s_card_draws_the_wizard_and_the_sheet(app):
    """The same page for the Admin: the wizard, its three starting points and the sheet beside it."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    assert "data-sc-wizard" in html, "the Admin's card draws no wizard"
    for value in ("preset", "now", "one"):
        assert f'data-sc-wiz-start="{value}"' in html, f"no starting point {value}"
    for value in ("dark", "light"):
        assert f'data-sc-wiz-scheme value="{value}"' in html, f"no scheme {value}"
    cards = html.count('data-sc-wizard=')
    assert cards, "the Admin's card draws no wizard"
    assert html.count('data-sc-wiz-step="') == 6 * cards, "the six step rows are not drawn per card"
    # §2.5: the block ships HIDDEN and only the script shows it, so a page with scripts off draws no
    # wizard at all — a wizard that cannot derive would be a form that lies
    tags = re.findall(r'<div class="sc-wiz"[^>]*>', html)
    assert len(tags) == cards, tags
    assert all(tag.endswith(" hidden>") for tag in tags), tags
    assert "site-palette-wizard.js" in html, "the page does not load the derivation"


@pytest.mark.asyncio
async def test_no_expert_text_can_be_posted_by_a_group_admin(app, site_file):
    """PIN 6, the closed door: the wizard cannot become a second way to publish CSS."""
    before = site_file.read_bytes()
    async with client(app) as http:
        await sign_in_as(http, GROUP_ADMIN)
        html = await open_page(http)
        reply = await post(http, html, {"_site": "guards",
                                        "f.sites.guards.theme.expert": EXPECTED_TEXT.strip()})
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert "so nothing was written" in notice_of(landed), notice_of(landed)
    assert site_file.read_bytes() == before, "a group admin's sheet reached the file"


# ==================================================== the arrangement, at 1440 and on a phone

def test_the_wizard_s_arrangement_at_1440_and_at_430(served):
    """§1.2: ONE block. The gallery is tiles three across with the door beside it; the starting points
    are ROWS (three 147 px columns cannot hold a sentence); the six steps share one width.

    The numbers are the PAGE's boxes, read at both viewports: a wizard that pushed the card sideways
    would be a wizard the operator cannot use.
    """
    wizard = _wizard(_probe(served, "wizardlayout"))

    # the gallery at rest, then the door: one press swaps the panel and nothing else moves
    rest = wizard["rest"]
    assert rest["viewport"] == 1440, rest["viewport"]
    assert rest["perRow"] == 3, f"the presets draw {rest['perRow']} across: {rest}"
    assert rest["overflow"] <= 0, f"the block pushes the page {rest['overflow']} px sideways"
    door = wizard["door"]
    assert door["tag"] == "BUTTON", f"the door is a {door['tag']}, not a press"
    assert door["radios"] == 0, "the door is a member of the exclusive set, not a way out of it"
    assert door["inGallery"] is True, "the door is not drawn as a tile of the gallery"
    assert door["w"] == door["tileW"], (
        f"the door is {door['w']} px beside a {door['tileW']} px tile")
    opened = wizard["opened"]
    assert opened["galleryHidden"] is True, "the gallery stayed drawn behind the wizard"
    assert opened["wizardHidden"] is False, "the door drew nothing"
    assert opened["insideBlock"] is True, "the wizard is not a panel of the palette block"
    assert opened["palettes"] == 1, f"the page draws {opened['palettes']} palette blocks"
    assert opened["wizards"] == 1 and opened["previews"] == 1, (
        f"{opened['wizards']} wizard(s) and {opened['previews']} preview(s) — one of each")

    wide = wizard["wide"]["tiles"]
    assert wide["viewport"] == 1440, wide["viewport"]
    assert wide["perRow"] == 1, f"the three starting points draw {wide['perRow']} across: {wide}"
    assert wide["overflow"] <= 0, f"the wizard pushes the page {wide['overflow']} px sideways"

    rows = wizard["wide_steps"]["steps"]
    assert len(rows["nodes"]) == 6, rows["nodes"]
    assert len({row["w"] for row in rows["nodes"]}) == 1, rows["nodes"]
    assert rows["overflow"] <= 0 and rows["bodyOverflow"] <= 0, rows

    # §11: the box is the PAGE's, so the wizard draws no control of its own and nothing is lifted —
    # the one preview stands beside the block whether the wizard is open or not
    assert wizard["lifted"] is None, (
        f"the wizard still draws a box control: {wizard['lifted']}")
    panel = wizard["panel"]
    assert panel["present"], "the page draws no preview panel beside the wizard"
    assert panel["frames"] == panel["panels"] >= 1, (
        f"{panel['panels']} card(s) carry {panel['frames']} frame(s)")
    assert panel["frameBox"]["h"] == 400, panel["frameBox"]

    narrow = wizard["narrow"]["tiles"]
    assert narrow["viewport"] == 430, narrow["viewport"]
    assert narrow["perRow"] == 1, f"on a phone the tiles still share a row: {narrow}"
    assert narrow["overflow"] <= 0, f"the wizard pushes the phone page {narrow['overflow']} px"
    narrow_rows = wizard["narrow_steps"]["steps"]
    assert len(narrow_rows["nodes"]) == 6 and len({row["w"] for row in narrow_rows["nodes"]}) == 1
    assert narrow_rows["overflow"] <= 0, narrow_rows


# ============================== §2.2 · "the palette this site wears now", read off its own document

#: the default palette's ground and its shipped control border (the pair the rule moves)
SHIPPED_GROUND = "#17120d"
SHIPPED_LINE_2 = "#524634"


def test_the_site_wears_now_is_reported_and_never_moved(served):
    """§9.2: a palette that was READ is MEASURED, not moved — the site's own colours come back as they
    are, and the strip above the steps says so.

    The shipped sheets' control border is below 3:1 against the panel, so the old behaviour moved
    `--line-2` on the act of reading. That is the defect this pin names: the six rows must carry the
    values the document is wearing, the control border included, and the row's own chip must say the
    pair fails rather than the value having been quietly rewritten.
    """
    wizard = _wizard(_probe(served, "wizardnow"))
    preview = wizard["preview"]
    assert preview["--ground"].lower() == SHIPPED_GROUND, (
        f"the site is served {preview['--ground']}, not the default palette")

    rows = {row["id"]: row for row in wizard["steps"]}
    # every step is the site's own value, token for token
    for ident, names in (("ground", ("--ground", "--stage")), ("panel", ("--panel", "--panel-2", "--band")),
                         ("line", ("--line", "--line-2")), ("accent", ("--brass", "--brass-hi", "--brass-dim")),
                         ("text", ("--fg",)), ("steps", ("--fg-2", "--fg-3"))):
        hexes = rows[ident]["hexes"].split()
        assert len(hexes) == len(names), rows[ident]
        for name, hex_value in zip(names, hexes):
            assert hex_value.lower() == preview[name].lower(), (
                f"{ident}: {name} reads {hex_value}, the page is serving {preview[name]}")
    # the control border is BELOW 3:1 against the panel and the row says so — nothing was moved
    assert rows["line"]["hexes"].split()[1].lower() == SHIPPED_LINE_2, (
        "the wizard moved the read palette's control border instead of reporting it")
    assert rows["line"]["state"] == "bad", rows["line"]
    assert float(rows["line"]["ratio"].rstrip(":1")) < 3.0, rows["line"]
    assert wizard["moves"] == [], f"reading the palette printed a move: {wizard['moves']}"
    # …and the panel says what it did, once, above the steps
    stored = wizard["stored"]
    assert stored["present"] and stored["hidden"] is False, stored
    assert "measured as they stand" in stored["text"], stored["text"]
    assert "nothing is moved" in stored["text"], stored["text"]
    assert wizard["useDisabled"] is False, "a reported palette cannot be taken"
    # reading the palette writes nothing: the posted control is still empty and the bar still says
    # the editor is the shipped transcription — shown for reading, not stored
    assert wizard["sheet"]["posted"] == "", "reading the site's own palette wrote the sheet"
    assert wizard["sheet"]["badge"] == "copy of brass.css · not stored", wizard["sheet"]


# ================================== the second ladder: the four shipped accents, value by value

#: base, scheme, and the values derive-out.txt prints for each shipped accent
SHIPPED_ACCENTS = [
    ("#d9a04a", "dark", "#140e08", "#1f180f", "#706453", "#d9a04a", "#fbbb5b", "#9d6e21", "#a9c7b4"),
    ("#1e5fae", "light", "#eef2f7", "#fcfdff", "#818a95", "#1e5fae", "#014288", "#5890db", "#5d7f74"),
    ("#33d1e6", "dark", "#071113", "#0e1c1e", "#526b6f", "#33d1e6", "#74ecff", "#0798a8", "#a9c7b4"),
    ("#7a4e1b", "light", "#f6f1ec", "#fffdfb", "#958a80", "#7a4e1b", "#5a3400", "#a67f57", "#5d7f74"),
]


def test_both_ladders_match_the_note_for_the_four_shipped_accents(served):
    """The light ladder is not the dark one plus a sign: its accent trio inverts, and the shipped
    `--sage` per scheme differs. Four derivations, read out of the page's own module."""
    derive = _wizard(_probe(served, "wizard"))["derive_extra"]
    for base, scheme, ground, panel, line2, brass, hi, dim, sage in SHIPPED_ACCENTS:
        values = derive[base]
        assert (values["--ground"], values["--panel"], values["--line-2"]) == (ground, panel, line2), (
            f"{base} ({scheme}): {values}")
        assert (values["--brass"], values["--brass-hi"], values["--brass-dim"]) == (brass, hi, dim), (
            f"{base} ({scheme}): {values}")
        assert values["--sage"] == sage, f"{base} ({scheme}): {values['--sage']}"
    light = derive["#1e5fae"]
    assert light["--brass-hi"] != light["--brass"] and light["--brass-dim"] != light["--brass"]
    assert float(light["--pulse-ring"].split(",")[-1].strip(" )")) == 0.55


# ============================ §5/§9 · a site with a stored sheet: the wizard opens ON that sheet

#: the declarations the sheet rows print, per step, in the rows' own order
SHEET_STEP_TOKENS = {
    "ground": ("--ground", "--stage"),
    "panel": ("--panel", "--panel-2", "--band"),
    "line": ("--line", "--line-2"),
    "accent": ("--brass", "--brass-hi", "--brass-dim"),
    "text": ("--fg",),
    "steps": ("--fg-2", "--fg-3"),
}


def _sheet_values() -> dict[str, str]:
    """The stored sheet's own ``--token: value`` pairs, read from the text the fixture writes."""
    from test_webui_site_settings_page import SHEET_TEXT

    values = {}
    for line in SHEET_TEXT.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") and ":" in stripped:
            name, _, raw = stripped.partition(":")
            values[name.strip()] = raw.rstrip(";").strip()
    return values


def test_a_site_with_a_stored_sheet_opens_the_wizard_on_it(sheet_served):
    """PIN 5: `start` is `stored`, the six steps are the sheet's values, and reading moves nothing.

    The pin is equality, both ways: every printed hex is the declaration the sheet carries (so the
    rows are a REPORT of the stored palette), and the sheet's text after the derivation is the text
    it had before — a read never rewrites what it read.
    """
    report = _probe(sheet_served, "stored")["wizard"]
    assert report["shown"] is True, "a site with a stored sheet draws no wizard at all"

    assert report["start"] == "stored", report["start"]
    assert [tile["id"] for tile in report["tiles"]] == ["preset", "now", "one", "stored"]
    assert [tile["on"] for tile in report["tiles"]] == [False, False, False, True], report["tiles"]
    assert [tile["value"] for tile in report["step1_tiles"] if tile["on"]] == ["stored"], (
        "the wizard did not press the sheet's own tile")

    values = _sheet_values()
    rows = {row["id"]: row for row in report["steps"]}
    for ident, tokens in SHEET_STEP_TOKENS.items():
        printed = rows[ident]["hexes"].split()
        assert printed == [values[token] for token in tokens], (
            f"{ident}: the row prints {printed}, the sheet declares "
            f"{[values[token] for token in tokens]}")
    # the sheet's control border is below the ui minimum: REPORTED as a failing chip, not moved
    assert rows["line"]["state"] == "bad", rows["line"]
    assert report["moves"] == [], f"reading the sheet printed a move: {report['moves']}"
    stored = report["stored"]
    assert stored["hidden"] is False and "measured as they stand" in stored["text"], stored
    assert report["useDisabled"] is False, "the reported palette cannot be taken"

    # NOTHING CHANGED: the carrier and the editor hold the same text as before the derivation
    assert report["carrier_after"] == report["carrier_before"]
    assert report["sheet_before"] == report["sheet_after"], "opening the wizard rewrote the sheet"
    assert report["carrier_before"].strip() == report["sheet_before"].strip()
    # …and the press talked to nobody
    assert report["requests"] == [], f"reading the sheet made a request: {report['requests']}"

    # the box IS ticked, because this site's palette IS that sheet
    toggle = report["toggle"]
    assert toggle["checked"] is True and toggle["name"] is None, toggle
    assert report["header_before"]["words"] == "An expert sheet", report["header_before"]
    assert report["header_before"]["key"] == "theme.expert", report["header_before"]


def test_the_first_starting_point_names_the_sheet_it_reads(served):
    """§2: the first row says WHICH sheet it will read, not "a shipped palette".

    The gallery is one press away, so a row that does not name its sheet leaves the operator guessing
    which of the four palettes the wizard is about to read. The row names the file, and the way back
    is printed on it.
    """
    wizard = _wizard(_probe(served, "wizard"))
    tiles = {tile["value"]: tile for tile in wizard["step1"]["tiles"]}
    assert list(tiles) == ["preset", "now", "one", "stored"][:len(tiles)], list(tiles)
    say = tiles["preset"]["say"]
    assert "brass.css" in say, f"the first starting point names no sheet: {say!r}"
    assert "← Presets" in say, say
    assert tiles["preset"]["key"] == "theme.preset", tiles["preset"]


# ============================================== §3 · every colour is editable, and the site follows

def _by_name(tokens: list) -> dict:
    return {token["name"]: token for token in tokens}


def test_editing_a_leader_moves_its_followers_and_the_row_names_the_offset(served):
    """§3.1 and §3.2, PIN 1: the row opens on its chevron; editing ``--brass`` moves the two
    declarations the design's own table derives from it, by the design's own OKLCH offsets, and each
    follower is stated as following — its picker and hex field are not a second free value."""
    wizard = _wizard(_probe(served, "paletteedit"))

    opened = wizard["open"]
    assert opened["open"] is True, "the chevron did not open the step's declarations"
    assert opened["toksHidden"] is False, "the declarations are still hidden"
    assert opened["swatchBg"] not in ("", "rgba(0, 0, 0, 0)"), opened

    before = _by_name(wizard["before"])
    after = _by_name(wizard["after"])
    assert list(before) == ["--brass", "--brass-hi", "--brass-dim"], before
    assert before["--brass"]["pick"].lower() == "#5085d8", before["--brass"]

    # the operator's value is the value the row now prints
    assert after["--brass"]["hex"].lower() == "#6e9ff4", after["--brass"]
    assert after["--brass"]["pick"].lower() == "#6e9ff4", after["--brass"]
    assert after["--brass"]["pickDisabled"] is False, "the edited value was locked"
    assert "the accent" in after["--brass"]["said"], after["--brass"]

    # the design's offsets: --brass-hi L +0.09, --brass-dim L -0.17, within 0.005 OKLCH points
    base = after["--brass"]["light"]
    hi, dim = after["--brass-hi"], after["--brass-dim"]
    assert abs(hi["light"] - base - 0.09) <= 0.005, (
        f"--brass-hi sits {hi['light'] - base:+.3f} from --brass")
    assert abs(dim["light"] - base + 0.17) <= 0.005, (
        f"--brass-dim sits {dim['light'] - base:+.3f} from --brass")

    # a follower reads back as following: named, offset stated, and not a free value
    for name, offset in (("--brass-hi", "+0.09"), ("--brass-dim", "−0.17")):
        token = after[name]
        assert token["pickDisabled"] is True, f"{name} is still a free value"
        assert token["hexDisabled"] is True, f"{name} is still a free value"
        assert "follows --brass" in token["said"], token["said"]
        assert offset in token["said"], token["said"]
        assert "moved L" in token["said"], token["said"]
        assert token["hex"] != before[name]["hex"], f"{name} did not move"


def test_editing_the_ground_moves_its_follower_and_the_pair_it_carries(served):
    """§3.2 on the ground's own row: ``--ground`` moves ``--stage`` by the design's L −0.04 and the
    pair the row measures (``--brass`` on ``--ground``) is re-measured — 6.54:1 in the design's shot."""
    wizard = _wizard(_probe(served, "paletteedit"))

    ground = _by_name(wizard["ground"])
    assert ground["--ground"]["hex"].lower() == "#121a2e", ground["--ground"]
    assert ground["--ground"]["pickDisabled"] is False
    assert ground["--stage"]["pickDisabled"] is True, "--stage is still a free value"
    assert "follows --ground" in ground["--stage"]["said"], ground["--stage"]["said"]
    assert "−0.04" in ground["--stage"]["said"], ground["--stage"]["said"]
    delta = ground["--stage"]["light"] - ground["--ground"]["light"]
    assert abs(delta + 0.04) <= 0.005, f"--stage sits {delta:+.3f} from --ground"
    assert ground["--stage"]["hex"].lower() == "#0f1218", ground["--stage"]

    step = wizard["ground_step"]
    assert step["state"] == "ok", step
    assert "6.54" in step["ratio"], step["ratio"]
    assert "6.54" in step["line"], step["line"]
    # the ONE frame carries the moved ground and its follower
    frame = wizard["ground_frame"]
    assert frame["--ground"].lower() == "#121a2e" and frame["--stage"].lower() == "#0f1218", frame


def test_step_six_s_closed_swatch_follows_its_owner(served):
    """PIN 2 — one row, one owner. ``--fg-2`` follows ``--fg``, so step 06's closed swatch is
    disabled and carries the follower's own line; the row still opens and ``--fg`` edits in row 05."""
    wizard = _wizard(_probe(served, "paletteedit"))

    step = wizard["steps"]
    assert step["open"] is True, "the row's opener did not open step 06"
    assert step["toksHidden"] is False, "the opened row kept its declarations hidden"
    assert step["swatchDisabled"] is True, "step 06's swatch still edits --fg-2 as a free value"
    assert step["swatchLabel"] == "follows --fg · L −0.115", (
        f"step 06's swatch is labelled {step['swatchLabel']!r}")

    tokens = _by_name(wizard["steps_tokens"])
    assert tokens["--fg-2"]["pickDisabled"] is True
    assert tokens["--fg-2"]["hexDisabled"] is True
    assert "follows --fg" in tokens["--fg-2"]["said"], tokens["--fg-2"]["said"]

    text = _by_name(wizard["text"])
    assert text["--fg"]["pickDisabled"] is False, "--fg is locked in its own row"
    assert text["--fg"]["hexDisabled"] is False, "--fg is locked in its own row"


def test_the_edit_repaints_the_one_preview_frame_with_no_request(served):
    """§3.2, PIN 2: the edit lands on the ONE preview's own root, read back with ``getComputedStyle``;
    no second frame is mounted and the edit talks to nobody."""
    wizard = _wizard(_probe(served, "paletteedit"))
    assert wizard["frames"] == 1, f"the card carries {wizard['frames']} preview frames"

    after = _by_name(wizard["after"])
    frame = wizard["after_frame"]
    for name in ("--brass", "--brass-hi", "--brass-dim"):
        assert frame[name].lower() == after[name]["hex"].lower(), (
            f"the frame's {name} is {frame[name]!r}, the row prints {after[name]['hex']!r}")
    assert frame["--brass"].lower() != wizard["before_frame"]["--brass"].lower(), (
        "the frame did not move with the edit")
    assert wizard["after_requests"] == [], (
        f"the edit asked the server something: {wizard['after_requests']}")


def test_an_edit_under_aa_states_the_move_and_the_offer_passes(served):
    """§3.3, PIN 3: nothing is clamped. The row keeps the operator's value with a red chip and a line
    naming the failing pair; the strip states the before/after and the measured ratio and offers the
    nearest that clears. Use applies the nearest — the pair passes and the handoff opens."""
    wizard = _wizard(_probe(served, "paletteedit"))

    warn = wizard["aa"]
    assert warn["hidden"] is False, "an edit under AA drew no strip"
    assert "The value you set is #31508f" in warn["say"], warn["say"]
    assert "The nearest value that clears it is #6082c6" in warn["say"], warn["say"]
    assert "4.59:1 on --panel" in warn["say"], warn["say"]
    assert "5.01:1 on the ground" in warn["say"], warn["say"]
    assert "L 0.44 → 0.61" in warn["say"], warn["say"]
    assert warn["use"] == "Use #6082c6", warn["use"]
    assert warn["keep"] == "Keep my #31508f", warn["keep"]
    assert wizard["aa_useDisabled"] is True, "the handoff was open on a pair under AA"

    step = wizard["aa_step"]
    assert step["state"] == "bad", step
    assert "under AA" in step["line"], step["line"]
    assert "the step you are editing" in step["line"], step["line"]
    # the operator's value stays in the field and in the row — nothing silently moved
    assert _by_name(wizard["aa_tokens"])["--brass"]["hex"].lower() == "#31508f", wizard["aa_tokens"]
    accent = next(row for row in wizard["aa_steps"] if row["id"] == "accent")
    assert accent["hexes"].split()[0].lower() == "#31508f", accent
    assert accent["state"] == "bad", accent

    # Use the nearest: the pair passes and the handoff opens — the value is the offer's, not a clamp
    used = wizard["used"]
    assert used["hidden"] is True, "the strip stayed after the offer was taken"
    assert wizard["used_useDisabled"] is False, "Use the nearest did not open the handoff"
    used_step = wizard["used_step"]
    assert used_step["state"] == "ok", used_step
    assert used_step["hexes"].split()[0].lower() == "#6082c6", used_step
    assert "4.59" in used_step["ratio"], used_step["ratio"]


def test_keeping_an_edit_under_aa_is_the_operator_s_choice(served):
    """§3.3, the other answer: Keep leaves the operator's value in the row with its red chip, holds the
    handoff, and writes nothing — the sheet is still the carrier and Save is still the only write."""
    wizard = _wizard(_probe(served, "palettekeep"))

    assert wizard["warn"]["hidden"] is False and wizard["useDisabled"] is True
    assert wizard["step"]["state"] == "bad"
    kept = wizard["kept"]
    assert kept["hidden"] is True, "the strip stayed after Keep"
    assert "under AA" in kept["foot"], kept["foot"]
    assert wizard["kept_step"]["state"] == "bad", "Keep silently moved the failing value"
    assert wizard["kept_step"]["hexes"].split()[0].lower() == "#31508f", wizard["kept_step"]
    assert wizard["kept_useDisabled"] is True, "Keep left the handoff open"
    assert wizard["kept_sheet"] == wizard["sheet"], "Keep wrote the sheet"
    assert wizard["kept_handoff"] == wizard["sheet"], "Keep wrote the sheet"


def test_the_colour_input_and_the_hex_field_are_one_value(served):
    """§3.1, PIN 5: the 26 px picker and the 88 px hex field are the same value in both directions; a
    malformed hex is refused and leaves the previous value standing."""
    wizard = _wizard(_probe(served, "paletteedit"))

    after = _by_name(wizard["after"])
    assert after["--brass"]["pick"].lower() == "#6e9ff4" and after["--brass"]["hex"].lower() == "#6e9ff4"

    typed = _by_name(wizard["hex_to_pick"])
    assert typed["--brass"]["hex"].lower() == "#123456", typed["--brass"]
    assert typed["--brass"]["pick"].lower() == "#123456", "the hex field did not write the picker"

    malformed = _by_name(wizard["malformed"])
    assert malformed["--brass"]["hex"] == "zzzz", malformed["--brass"]
    assert malformed["--brass"]["pick"].lower() == "#123456", (
        "a malformed hex destroyed the previous value")
    assert wizard["malformed_step"]["hexes"].split()[0].lower() == "#123456", (
        "a malformed hex reached the row's own hex line")


def test_the_handoff_still_validates_and_save_writes_it(served):
    """§3.4: the edited palette's handoff passes ``theme_css.validate`` and the card's Save writes it."""
    wizard = _wizard(_probe(served, "paletteedit"))
    handoff = wizard["handoff"]
    assert theme_css.validate(handoff) is None, theme_css.validate(handoff)
    assert "--brass: #6082c6;" in handoff, handoff
    saved = wizard["saved"]
    assert any("Saved" in notice for notice in saved["notices"]), saved
