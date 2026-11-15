"""§11 on the Site settings page, read off the RENDERED markup: one preview, and the page's order.

The page's skeleton is two columns — the blocks, and ONE `<iframe>` of the site's own document beside
them — and the blocks are drawn in the design's order. Both are properties of the parsed document, so
they are pinned here against the real application's render, not against the template's text:

* exactly ONE frame per card, whose `src` is the chrome-preview route, a sibling of the editing
  column (never a child of a block), and no block draws one of its own;
* the wizard's `Show the site` / `Put it back` slot is gone from the parsed markup, with the box it
  used to lift;
* the blocks appear in the design's order, read from the parsed `data-sc-block` attributes;
* the panel says once what feeds the frame — and that Character feeds it nothing;
* the three structural rows are folded into ONE `<details>` at the foot of the column.
"""
from __future__ import annotations

import pytest

from services.webservice import site_pages
from test_webui_site_settings import (  # noqa: F401 - fixtures imported on purpose
    ADMIN, app, config_dir, _parse, _resolve, client, open_page, preview_url, sign_in_as,
)

#: the page's order, exactly as `DESIGN.md` §11.1 states it. The identity fold and the sheet are the
#: last two: the sheet is outside the two columns and is still the page's last block.
BLOCK_ORDER = ("branding", "palette", "character", "menu", "links", "pages", "identity", "expert")


def _blocks(html: str) -> list[str]:
    """Every `data-sc-block` value, in document order, for the FIRST card."""
    document = _parse(html)
    card = next(node for node in document.walk()
                if node.tag == "form" and "sc-form" in node.classes())
    return [node.attrs["data-sc-block"] for node in card.walk()
            if node.attrs.get("data-sc-block")]


def _frames(html: str) -> list:
    return [node for node in _parse(html).walk() if node.tag == "iframe"]


@pytest.mark.asyncio
async def test_the_page_draws_exactly_one_frame_and_no_block_draws_one(app):
    """PIN 1: ONE `<iframe>`, its `src` the chrome-preview route, and no block holds a box."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    document = _parse(html)
    frames = _frames(html)
    cards = [node for node in document.walk()
             if node.tag == "form" and "sc-form" in node.classes()]
    assert len(frames) == len(cards), (
        f"the page renders {len(frames)} frame(s) for {len(cards)} card(s) — one preview per card")
    assert len(frames) >= 1
    for frame in frames:
        assert site_pages.CHROME_PREVIEW_PATH in (frame.attrs.get("src") or ""), (
            f"the frame loads '{frame.attrs.get('src')}' instead of the site's own document")
        assert frame.attrs.get("tabindex") == "-1", "the frame is not inert"
        ancestors = []
        node = frame.parent
        while node is not None:
            ancestors.append(node)
            node = node.parent
        forms = [node for node in ancestors if node.tag == "form" and "sc-form" in node.classes()]
        assert forms, "a frame is drawn outside the card's form"
        panels = [node for node in ancestors if "sc-site" in node.classes()]
        assert panels, "the frame is not inside the page's own panel"
        page_nodes = [node for node in ancestors if "sc-page" in node.classes()]
        assert page_nodes, "the panel is not a child of the page's two columns"
        assert not [node for node in ancestors if node.attrs.get("data-sc-block")], (
            "a frame sits inside a block — the page draws the one preview, not a block")
    # the panel is a SIBLING of the editing column, never a child of a block — this is what lets it
    # stay pinned while the column scrolls
    panels = [node for node in document.walk() if "sc-site" in node.classes()]
    assert panels, "the page draws no panel"
    for panel in panels:
        assert panel.parent is not None and "sc-page" in panel.parent.classes()
        columns = [child for child in panel.parent.children if "sc-edcol" in child.classes()]
        assert columns, "the panel has no editing column beside it"
        assert not [node for node in panel.parent.children if "sc-edcol" in node.classes()
                    and panel in list(node.walk())], "the panel is inside the editing column"
    # the blocks draw no frame of their own
    for section in [node for node in document.walk() if node.attrs.get("data-sc-block")]:
        assert not [node for node in section.walk() if node.tag == "iframe"], (
            f"the {section.attrs['data-sc-block']} block draws a preview of its own")


@pytest.mark.asyncio
async def test_the_old_show_and_put_back_controls_are_gone(app):
    """§11.1: the wizard's `[data-sc-wiz-show]` slot and the box it lifted are deleted."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    document = _parse(html)
    assert not [node for node in document.walk() if node.attrs.get("data-sc-wiz-show")], (
        "the wizard still draws its Show the site slot")
    assert not [node for node in document.walk() if "sc-brand-pv" in node.classes()], (
        "the retired box is still in the markup")
    assert "sc-pv-lift" not in html, "the retired lift class is still in the markup"
    assert not [node for node in document.walk() if "sc-pv-marks" in node.classes()], (
        "the retired band chips are still in the markup")


@pytest.mark.asyncio
async def test_the_blocks_are_drawn_in_the_design_s_order(app):
    """PIN 3: the parsed order of the blocks is §11.1's one line."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    assert tuple(_blocks(html)) == BLOCK_ORDER, f"the page's order is {_blocks(html)}"


@pytest.mark.asyncio
async def test_the_identity_fold_keeps_the_role_alone(app):
    """§11.6 after the header line: the fold carries `group_role` and nothing else.

    The address and the switch are the card's own `<summary>` — the fold may not keep a second copy,
    because two controls writing one leaf is a page that can disagree with itself after a press.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    document = _parse(html)
    folds = [node for node in document.walk() if "sc-ident" in node.classes()]
    assert len(folds) >= 1, "the page draws no folded identity block"
    # the `guards` site is not the root: its role is editable, so the fold carries a posted control
    fold = next((node for node in folds
                 if any("f.sites.guards.group_role" in f"<{child.tag} {child.attrs}>"
                        for child in node.walk())), None)
    assert fold is not None, "no identity fold carries the editable role row"
    assert fold.tag == "details", "the identity fold is not a <details>"
    assert "open" not in fold.attrs, "the identity fold is drawn open — it changes no pixel"
    markup = "".join(f"<{node.tag} {node.attrs}>" for node in fold.walk())
    assert "f.sites.guards.group_role" in markup, "the role row left the identity fold"
    for leaf in ("slug", "enabled"):
        assert f"f.sites.guards.{leaf}" not in markup, (
            f"the {leaf} control is still in the identity fold as well as in the card's header")
    assert _blocks(html).index("identity") > _blocks(html).index("pages"), (
        "the identity fold is not at the foot of the column")


def _card_for(html: str, key: str):
    """The `<details class="sc-card">` whose header prints *key* as the site's name."""
    for node in _parse(html).walk():
        if node.tag != "details" or "sc-card" not in node.classes():
            continue
        summary = next((child for child in node.children if child.tag == "summary"), None)
        if summary is None:
            continue
        names = [n for n in summary.walk() if "mono" in n.classes()]
        if any("".join(n.text).strip() == key for n in names):
            return node
    return None


@pytest.mark.asyncio
async def test_the_identity_line_is_the_cards_own_header(app):
    """THE ANATOMY: the name, the address and the switch are ONE row inside the card's `<summary>`.

    Read from the RENDERED markup: the three facts are children of the summary, the two controls are
    bound to the card's own save form (`form="sc-save-<token>"`, the form being their sibling), each
    control exists exactly ONCE in the card, and the long sentence is the switch's `title` rather
    than a paragraph.
    """
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    card = _card_for(html, "guards")
    assert card is not None, "no card header prints the site's name"
    summary = next(child for child in card.children if child.tag == "summary")
    assert "sc-sum" in summary.classes(), "the card's header is not its <summary>"
    form = next((child for child in card.children if child.tag == "form"), None)
    assert form is not None and "sc-form" in form.classes(), "the card draws no save form"
    form_id = form.attrs.get("id")
    assert form_id, "the save form carries no id, so a control outside it cannot reach it"

    inside = list(summary.walk())
    assert any("".join(n.text).strip() == "guards" and "mono" in n.classes() for n in inside), (
        "the site's name is not in the card's header")
    address = [n for n in inside if n.tag == "input" and n.attrs.get("name") == "f.sites.guards.slug"]
    assert len(address) == 1, f"the header carries {len(address)} address fields"
    assert address[0].attrs.get("form") == form_id, (
        f"the address field names form {address[0].attrs.get('form')!r}, not the card's {form_id!r}")
    assert address[0].attrs.get("value") == "guards", (
        f"the address field shows {address[0].attrs.get('value')!r}")

    switches = [n for n in inside if n.tag == "input"
                and n.attrs.get("name") == "f.sites.guards.enabled" and n.attrs.get("type") == "checkbox"]
    assert len(switches) == 1, f"the header carries {len(switches)} switches"
    assert switches[0].attrs.get("role") == "switch", "the switch is not a switch"
    assert switches[0].attrs.get("form") == form_id, "the switch does not name the card's form"
    companions = [n for n in inside if n.tag == "input"
                  and n.attrs.get("name") == "f.sites.guards.enabled" and n.attrs.get("type") == "hidden"]
    assert len(companions) == 1 and companions[0].attrs.get("value") == "0", (
        "the switch lost its hidden `enabled=0` companion")

    # NOTHING IS DUPLICATED IN THE CARD: the two controls exist once, in the header, and nowhere else
    whole = list(card.walk())
    for name, kind in (("f.sites.guards.slug", None),
                       ("f.sites.guards.enabled", "checkbox"),
                       ("f.sites.guards.enabled", "hidden")):
        drawn = [n for n in whole if n.tag == "input" and n.attrs.get("name") == name
                 and (kind is None or n.attrs.get("type") == kind)]
        assert len(drawn) == 1, (
            f"the card draws {len(drawn)} <input type={kind or '*'} name={name}> — one leaf, one control")

    # THE FLAG TRAVELS WITH THE SWITCH, and the sentence is its tooltip, not a paragraph
    flags = [n for n in inside if "st-flag" in n.classes()]
    assert len(flags) == 1, f"the header carries {len(flags)} needs-a-reload flags"
    label = next(n for n in inside if "sc-idsw" in n.classes())
    assert label.tag == "label", "the switch is not a label around its box"
    assert str(label.attrs.get("title") or "").startswith("A site that is switched off registers no"), (
        f"the switch's tooltip reads {label.attrs.get('title')!r}")
    text = " ".join("".join(n.text) for n in whole)
    assert "registers no routes" not in " ".join(text.split()), (
        "the switch's explaining sentence is still drawn as a paragraph")
    assert "registers no routes" in str(label.attrs.get("title") or ""), (
        "the switch states no hint at all")


@pytest.mark.asyncio
async def test_the_root_site_shows_a_bare_address_and_no_field(app):
    """A root site's address is `/` and a sentence — there is no slug to type, so no field is drawn."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    card = _card_for(html, "root")
    assert card is not None, "no card header prints the root site's name"
    summary = next(child for child in card.children if child.tag == "summary")
    inside = list(summary.walk())
    assert not [n for n in inside if n.attrs.get("name") == "f.sites.root.slug"], (
        "the root site's header draws a slug field it cannot write")
    roots = [n for n in inside if "sc-addr-root" in n.classes()]
    assert len(roots) == 1, f"the root site's header carries {len(roots)} address chips"
    assert "".join(part for node in roots[0].walk() for part in node.text).strip() == "/", (
        "the root site's address chip does not read `/`")
    assert any("st-note" in n.classes() for n in inside), (
        "the root site's bare `/` carries no sentence saying what it is")
    assert [n for n in inside if "sc-idsw" in n.classes()], (
        "the root site's header carries no switch")


@pytest.mark.asyncio
async def test_the_panel_states_what_writes_into_the_frame(app):
    """§11.2: one caption, and the line naming which blocks write what — Character included."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    document = _parse(html)
    panel = next(node for node in document.walk() if "sc-site" in node.classes())

    def text_of(name: str) -> str:
        """The text under every *name*-classed element of the panel — its own and its children's."""
        chunks = []
        for node in [node for node in panel.walk() if name in node.classes()]:
            joined = "".join("".join(child.text) for child in [node, *node.walk()])
            chunks.append(" ".join(joined.split()))
        return " | ".join(chunks)

    assert text_of("sc-pv-cap"), "the panel states no caption"
    writes = text_of("sc-sitewrites")
    for word in ("Branding", "Palette", "Footer links", "menu"):
        assert word.lower() in writes.lower(), f"the writes line never names {word}"
    assert site_pages.CHROME_PREVIEW_PATH not in writes, (
        "the writes line prints the route — the panel's own chip names it")
    assert text_of("sc-sitenone"), "the panel does not say what changes nothing"
    assert "Character" in text_of("sc-sitenone")
    header = text_of("sc-sitehd")
    assert site_pages.CHROME_PREVIEW_PATH in header, "the panel's title bar names no document"
    assert "Open" in header, "the panel has no door to the real site"


@pytest.mark.asyncio
async def test_the_nav_block_drives_the_bar_s_nav(app):
    """§11.2: Menu & Tiles writes `.top .nav` — the block carries the element and its addresses."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        preview = await http.get(preview_url("root"))
    document = _parse(html)
    nav = next(node for node in document.walk() if node.attrs.get("data-sc-set") == "nav")
    assert nav.attrs.get("data-sc-drives") == ".top .nav", (
        f"the nav block drives '{nav.attrs.get('data-sc-drives')}'")
    assert nav.attrs.get("data-sc-kind") == "order"
    assert nav.attrs.get("data-sc-order"), "the nav block carries no id → address map"
    # the selector resolves inside the site's OWN document, which is the element the binding writes
    assert len(_resolve(_parse(preview.text), ".top .nav")) == 1, (
        "the nav block names an element the preview document does not carry")
