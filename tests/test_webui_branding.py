"""The console's brand mark: the supplied logo, in the header, on the login page and as the favicon.

The logo is a supplied asset, so three of the four decisions are about markup a template refactor
could silently drop -- asserted on the RENDERED page (the ``<img>``, its ``src`` and ``alt``), never
on the template text. The fourth: the pages must not serve the 2.1 MB master. The favicon href must
be a cacheable ``/static`` path (never a ``data:`` URI, which would inline 2 MB into every page) and
resolve to a file that is there; every served surface reads a downscaled derivative whose dimensions
and bytes are a real downscale of the master."""
from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest

from conftest import asgi_client
from test_webui_dashboard import BOT_YAML, Status, _build, _login, _source, instance, node, player, \
    server

WEBSERVICE = Path(__file__).resolve().parent.parent / "services" / "webservice"
TEMPLATES = WEBSERVICE / "templates"
STATIC = WEBSERVICE / "static"

BASE_HTML = TEMPLATES / "base.html"
LOGIN_HTML = TEMPLATES / "login.html"
SHEET = STATIC / "shell.css"

MASTER = "logo.png"
HEADER_ASSET = "logo-256.png"
LOGIN_ASSET = "logo-512.png"

#: the header mark's height, in px. The sheet states it once and this pin keeps the CSS from drifting
#: off it; the brand row is `--nav-h` tall, so its bottom border lines up with the top bar's.
BRAND_HEIGHT = 34
#: the login page's logo width, in px (kept within the 160-240px band).
LOGIN_WIDTH = 220


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _png(path: Path) -> tuple[int, int, int, int]:
    """``(width, height, bit_depth, colour_type)`` straight out of the PNG IHDR chunk.

    No image library on purpose: the pin is about the bytes that ship, and the colour type is what
    says the artwork keeps an ALPHA channel (an opaque rectangle on the console's dark palette is
    exactly the defect the alpha check rules out).
    """
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    bit_depth, colour_type = data[24], data[25]
    return width, height, bit_depth, colour_type


def _rule(selector: str) -> str:
    """The body of one ``selector { … }`` block, comments stripped (the sheet explains itself in
    prose, and prose that quotes a property must not read as a declaration)."""
    sheet = re.sub(r"/\*.*?\*/", "", SHEET.read_text(encoding="utf-8"), flags=re.S)
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", sheet)
    assert match, f"the stylesheet declares no `{selector}` rule"
    return match.group(1)


async def _page(app, path: str = "/") -> str:
    async with asgi_client(app) as client:
        await _login(client)
        response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return response.text


def _sidebar(html: str) -> str:
    match = re.search(r"<aside.*?</aside>", html, re.S)
    assert match, "the page renders no sidebar"
    return match.group(0)


# ------------------------------------------------------------ 1. the header carries the logo

@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/logs"])
async def test_the_header_carries_the_logo_on_every_page(config_dir, path):
    """The brand mark is the logo, on every console page — beside the text brand, never instead of
    it (at header size the logo's own three-line wordmark is not readable, which is why the text
    brand stays).

    Two DIFFERENT documents are rendered (`dashboard.html` and `logs.html`), because that is what
    proves the mark is the CHROME's and not one page's. The four list pages share the third
    document (`list.html`) and are covered by the sweep below, which reads the template tree where
    "every page extends the chrome" is actually decided.
    """
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server(players=[player()])], node_obj=node(instances={"Training": instance()}))
    html = await _page(app, path)

    sidebar = _sidebar(html)
    img = re.search(r'<img[^>]+class="[^"]*brand-logo[^"]*"[^>]*>', sidebar)
    assert img, f"the sidebar brand carries no logo image on {path}"
    tag = img.group(0)
    assert f'src="/static/{HEADER_ASSET}"' in tag, tag
    alt = re.search(r'alt="([^"]*)"', tag)
    assert alt and "DCSServerBot" in alt.group(1), f"the logo needs a meaningful alt: {tag}"
    # the text brand is still there, beside it
    assert "<b>DCSServerBot</b>" in sidebar, "the logo replaced the text brand instead of joining it"


def test_every_page_document_inherits_the_chrome_or_brands_itself():
    """The ratchet behind "every page": a page template that renders its OWN document (a DOCTYPE)
    either extends `base.html` — and so inherits the brand, the favicon and the chrome — or is the
    login page, which does not extend it and therefore has to carry the logo and the favicon itself.

    A third standalone document fails here rather than shipping a page with no brand mark.
    """
    documents = {path.name for path in TEMPLATES.glob("*.html")
                 if "<!DOCTYPE html>" in path.read_text(encoding="utf-8")}
    assert documents == {"base.html", "login.html", "site_base.html"}, (
        f"a page renderer's document set changed: {sorted(documents)} — a new standalone document "
        "must carry the brand render itself (see login.html)")
    # the public site's shell is the third document: it carries the site's own brand, its favicon
    # and its own asset — never the console's chrome
    site_shell = (TEMPLATES / "site_base.html").read_text(encoding="utf-8")
    assert "{{ site.branding.title }}" in site_shell
    assert "/static/logo-mark.png" in site_shell and "/static/site.css" in site_shell
    assert "/static/logo-256.png" not in site_shell, \
        "the public site draws the whole lockup beside its own title"
    for name in sorted(documents - {"base.html"}):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        if name == "site_base.html":
            continue  # the public site's shell: asserted above, it carries the site's own brand
        if name == "login.html":
            assert 'class="login-logo"' in text, "the login page renders no logo"
        else:
            assert '{% extends "base.html" %}' in text, f"{name} does not extend the chrome"


@pytest.mark.asyncio
async def test_the_header_logo_is_sized_by_css_and_never_stretched(config_dir):
    """`height` fixed, `width: auto`: the 3:2 artwork keeps its aspect at every viewport, and the
    sheet states the mark's height once (pinned to `BRAND_HEIGHT`)."""
    body = _rule(".brand img.brand-logo")
    height = re.search(r"(?<![\w-])height:\s*([^;]+);", body)
    width = re.search(r"(?<![\w-])width:\s*([^;]+);", body)
    assert height and width, f"the brand logo declares height and width: {body!r}"
    assert width.group(1).strip() == "auto", f"`width: {width.group(1).strip()}` stretches the logo"
    pixels = re.match(r"(\d+)px", height.group(1).strip())
    assert pixels, f"the brand logo's height is not a px length: {height.group(1)!r}"
    declared = int(pixels.group(1))
    assert declared == BRAND_HEIGHT, \
        f"the brand mark is {declared}px, not the pinned {BRAND_HEIGHT}px — update BRAND_HEIGHT " \
        "deliberately with the sheet"
    # the intrinsic size hint on the element is the same aspect as the file, so nothing shifts
    # while the image loads
    html = BASE_HTML.read_text(encoding="utf-8")
    header_tag = re.search(r'<img[^>]+class="[^"]*brand-logo[^"]*"[^>]*>', html)
    assert header_tag, "base.html renders no brand logo image"
    tag = header_tag.group(0)
    hint = re.search(r'width="(\d+)"\s+height="(\d+)"', tag)
    assert hint, f"the header <img> carries no width/height ratio hint: {tag}"
    master_w, master_h, _, _ = _png(STATIC / MASTER)
    assert abs(int(hint.group(1)) / int(hint.group(2)) - master_w / master_h) < 0.03, \
        "the header's width/height attributes do not carry the logo's own aspect ratio"


# ------------------------------------------------------------ 2. the favicon is a static request

@pytest.mark.asyncio
async def test_the_favicon_is_a_static_png_and_not_a_data_uri(config_dir):
    """A cacheable static request, never an inlined image — and the path it names is really served."""
    app, _registrar = _build(config_dir)
    html = await _page(app)

    link = re.search(r'<link[^>]+rel="icon"[^>]*>', html)
    assert link, "no <link rel=\"icon\"> on the rendered page"
    assert "data:" not in link.group(0), "the favicon must not be inlined as a data: URI"
    href = re.search(r'href="([^"]+)"', link.group(0))
    assert href and href.group(1).startswith("/static/"), link.group(0)
    asset = STATIC / Path(href.group(1)).name
    assert asset.is_file(), f"the favicon points at {href.group(1)}, which is not on disk"

    async with asgi_client(app) as client:
        response = await client.get(href.group(1))
    assert response.status_code == 200, "the favicon path is not served by the shell's asset route"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n", "the served favicon is not a PNG"


# ------------------------------------------------------------ 3. the login page renders it

@pytest.mark.asyncio
async def test_the_login_page_renders_the_logo_bigger(config_dir):
    """The first page a visitor sees: its own document, so it needs its own logo — at a size where
    the badge's wordmark is legible."""
    app, _registrar = _build(config_dir)
    async with asgi_client(app) as client:
        response = await client.get("/auth/login")
    assert response.status_code == 200
    html = response.text

    img = re.search(r'<img[^>]+class="[^"]*login-logo[^"]*"[^>]*>', html)
    assert img, "the login page renders no logo"
    tag = img.group(0)
    assert f'src="/static/{LOGIN_ASSET}"' in tag, tag
    alt = re.search(r'alt="([^"]*)"', tag)
    assert alt and "DCSServerBot" in alt.group(1), f"the login logo needs a meaningful alt: {tag}"

    # the login logo is served wider than the header's, from a bigger derivative — and the width is
    # CAPS with `height: auto`, so the aspect can never be stretched
    body = _rule(".login-logo")
    width = re.search(r"(?<![\w-])width:\s*([^;]+);", body)
    height = re.search(r"(?<![\w-])height:\s*([^;]+);", body)
    assert width and height, f"the login logo declares width and height: {body!r}"
    assert width.group(1).strip() == f"{LOGIN_WIDTH}px", width.group(1)
    assert height.group(1).strip() == "auto", f"`height: {height.group(1).strip()}` stretches it"
    login_w, _, _, _ = _png(STATIC / LOGIN_ASSET)
    assert login_w >= LOGIN_WIDTH, "the login derivative is smaller than the slot it fills"


# ------------------------------------------- 4. the master stays the master; pages read a downscale

def test_the_pages_read_a_downscale_and_the_master_is_untouched():
    """``logo.png`` is the canonical asset and is served by NO page; every surface reads a derivative that
    is a true downscale of it -- same aspect, full alpha, a fraction of the bytes.

    This makes the weight decision checkable: a future change pointing the header at the master fails
    here by name.
    """
    master = STATIC / MASTER
    assert master.is_file(), "the canonical logo.png is missing"
    master_w, master_h, _depth, master_colour = _png(master)
    assert (master_w, master_h) == (1536, 1024), (
        "logo.png is no longer the supplied 1536x1024 master — regenerate the derivatives and update "
        "this pin deliberately rather than servicing a re-encoded master")
    assert master_colour == 6, "the master must keep its alpha channel (it is the canonical asset)"
    master_bytes = master.stat().st_size

    for name, width in ((HEADER_ASSET, 256), (LOGIN_ASSET, 512)):
        path = STATIC / name
        assert path.is_file(), f"{name} is missing: the pages reference it"
        w, h, _bd, colour = _png(path)
        assert w == width, f"{name} is {w}px wide, not {width}"
        assert colour == 6, f"{name} lost its alpha channel (an opaque box on the dark palette)"
        assert abs(w / h - master_w / master_h) < 0.01, f"{name} does not carry the master's aspect"
        assert path.stat().st_size < master_bytes / 10, \
            f"{name} is {path.stat().st_size} bytes — that is not a downscale of the master"

    # and nothing anywhere points a page at the master
    for page in sorted(TEMPLATES.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        assert 'src="/static/logo.png"' not in text, \
            f"{page.name} serves the 2.1 MB master as an image"


# ------------------------------------------------------------ 5. the retired mark leaves nothing

def test_the_retired_text_mark_square_has_no_css_left():
    """The ``.mark`` square (a 34px amber tile with the letters ``DCS``) was the placeholder brand
    mark; the logo replaces it, so the rule goes with it — CSS nothing renders is the same trap as
    a knob nothing reads."""
    sheet = re.sub(r"/\*.*?\*/", "", SHEET.read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"(?<![\w-])\.mark(?![\w-])", sheet), \
        "the .mark rules still ship: nothing renders a mark square"
    for template in sorted(TEMPLATES.glob("*.html")):
        assert 'class="mark"' not in template.read_text(encoding="utf-8"), template.name
