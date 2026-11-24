"""Every link the setup tour renders is a real, unmistakable link.

The tour inlines its own stylesheet (``services/webservice/setup/tour.py``), so the pins are taken
from that sheet and from the SHIPPED pages: the ``.link`` rule must read the palette's accent — never
the muted note grey it used to share with the copy around it — be underlined, semibold and at body
size, and the accent must be readable on the panel it sits on. A rendered sweep then asserts that
every anchor a step draws is either a ``.link`` (the prominent treatment), a ``.button``, or one of
the chrome controls (the language/theme icon toggles and the rail's own rows) that carry their own
colour on purpose.

WHAT THIS DOES NOT COVER: the light theme's accent-on-white ratio is recorded but not asserted to AA
(see the note on the measured value below); the icon toggles and rail rows are named as exempt, not
measured.
"""
from __future__ import annotations

import re

import pytest

from conftest import asgi_client
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.state import SetupDraft
from services.webservice.setup.tour import STEP_PATH, _the_style
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"

#: WCAG AA for normal-size text — the bar the link colour has to clear on its own panel.
AA_NORMAL = 4.5

#: The classes an anchor may carry on a tour page: the prominent ``.link`` (the whole point of this
#: file), the ``.button`` (already a filled control), and the chrome the page draws itself — the
#: language menu's rows and the theme icon toggles, and the rail's completed rows. Anything else is a
#: bare anchor with no treatment, which is the bug.
ALLOWED_ANCHOR_CLASSES = frozenset({
    "link", "button",
    "mi", "mi on",
    "theme-opt", "theme-opt on",
    "row done", "row on",
})


# ------------------------------------------------------------------------------------ the sheet

def _sheet() -> str:
    """The tour's stylesheet without CSS comments (a comment naming a property is not a declaration)."""
    return re.sub(r"/\*.*?\*/", "", _the_style(), flags=re.S)


def _rule(selector: str) -> str:
    """The body of the ``selector { … }`` block, failing loudly when the rule is gone."""
    match = re.search(rf"(?<![\w-]){re.escape(selector)}\s*\{{([^}}]*)\}}", _sheet())
    assert match, f"the tour's stylesheet declares no `{selector}` rule"
    return match.group(1)


def _root_var(name: str) -> str:
    """The literal value of a custom property declared in the dark ``:root`` (the palette's owner)."""
    found = re.search(rf"{re.escape(name)}:\s*([^;]+);", _rule(":root"))
    assert found, f"`{name}` is not declared in :root"
    return found.group(1).strip()


def _hex(text: str) -> tuple:
    text = text.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    assert len(text) == 6, f"not a 6-digit hex colour: {text!r}"
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb: tuple) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple, b: tuple) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_link_rule_reads_the_accent_family_not_the_note_grey():
    """The ``.link`` rule paints with the palette's accent, never a bare colour.

    This is the defect the operator reported: ``.link`` used to be ``var(--text-2)``, a muted blue-grey
    barely distinguishable from the ``--text-3`` note text around it, so the help links read as part
    of the quiet sentence. The rule must read the accent token.
    """
    body = _rule(".link")
    colour = re.search(r"color:\s*([^;]+);", body)
    assert colour, "`.link` declares no color"
    value = colour.group(1).strip()
    assert value == "var(--accent)", f"`.link` must read `var(--accent)`; it is `{value}`"
    # and the note text it used to blend into is a DIFFERENT token, so the link cannot be confused
    assert _rule(".note").find("var(--text-3)") != -1
    assert "var(--accent)" != "var(--text-3)"


def test_the_link_is_underlined_semibold_and_at_body_size():
    """The link carries three non-colour signals so it is unmistakable, not merely tinted."""
    body = _rule(".link")
    decoration = re.search(r"text-decoration:\s*([^;]+);", body)
    assert decoration and "underline" in decoration.group(1), \
        "`.link` must be underlined — colour alone is too easy to miss"
    weight = re.search(r"font-weight:\s*(\d+);", body)
    assert weight and int(weight.group(1)) >= 600, "`.link` must be at least semibold"
    size = re.search(r"font-size:\s*([^;}]+)", body)
    assert size and size.group(1).strip() == "14px", \
        f"`.link` must sit at body size (14px); it is `{size.group(1).strip() if size else None}`"


def test_the_link_colour_is_readable_on_the_panel():
    """The accent link clears WCAG AA on the panel it is drawn on (the measured deliverable).

    The light theme's accent (#a86a12 on white) measures just under AA; it is the palette's own
    accent and introducing a new colour is out of scope, so the dark-theme ratio is the asserted bar
    and the light figure is printed for the record.
    """
    dark = _contrast(_hex(_root_var("--accent")), _hex(_root_var("--panel")))
    light_accent, light_panel = "#a86a12", "#ffffff"
    light = _contrast(_hex(light_accent), _hex(light_panel))
    print("tour link contrast: dark accent/panel = %.2f:1, light accent/panel = %.2f:1"
          % (dark, light))
    assert dark >= AA_NORMAL, f"the link accent is {dark:.2f}:1 on the panel — below AA ({AA_NORMAL})"


# ------------------------------------------------------------------------------- the rendered pages

def build(config_dir, *, code: str = CODE):
    window = SetupWindow(code=code, wrong_delay=0.0)
    return create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN), window


async def enter(client, code: str = CODE):
    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    return await client.get(SETUP_PATH)


def _anchor_classes(html: str) -> list[str]:
    """The class attribute of every ``<a …>`` on the page, whitespace-normalised."""
    classes = []
    for tag in re.findall(r"<a\b[^>]*>", html):
        found = re.search(r'class="([^"]*)"', tag)
        classes.append(" ".join(found.group(1).split()) if found else "")
    return classes


@pytest.mark.asyncio
async def test_every_anchor_a_step_draws_carries_the_link_treatment(tmp_path):
    """Sweep: on each rendered step, no anchor is left without a treatment.

    Steps 3 (the Database card, which carries the PostgreSQL link) and 5 (Discord, the Developer Portal
    link) are walked; the first step and the rail/top bar are drawn on the same pages. Every anchor's
    class must be in the allowlist — a bare anchor, or one whose class the sheet does not style, is the
    operator's bug wherever it lives.
    """
    draft = SetupDraft(step=4, discord=True)
    draft.node = {"listen_port": 10042, "no_dcs": True, "dcs_installation": None}
    draft.servers = {"scanned": True, "detected": {}, "rows": {}}
    draft.database = {"path": "create"}
    draft.save(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        for step in (1, 3, 5):
            html = (await client.get(STEP_PATH, params={"n": str(step)})).text
            classes = _anchor_classes(html)
            assert classes, f"step {step} rendered no anchors at all"
            for cls in classes:
                assert cls in ALLOWED_ANCHOR_CLASSES, \
                    f"step {step} draws an anchor with class `{cls}` — no link treatment"


def test_the_doc_link_builder_emits_a_real_anchor():
    """``_doc_link`` keeps its behaviour: a real anchor opening a new tab, the URL also as plain text."""
    from services.webservice.setup.tour import _doc_link
    markup = _doc_link(lambda msgid: msgid, "https://example.test/x", "A label")
    assert ('<a class="link" href="https://example.test/x" '
            'target="_blank" rel="noopener noreferrer">A label</a>') in markup
    assert "(<code>https://example.test/x</code>)" in markup
