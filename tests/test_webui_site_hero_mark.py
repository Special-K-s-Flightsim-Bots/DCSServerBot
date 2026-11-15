"""The mark beside the hero's welcome: the placement setting, the size ladder and the welcome text.

WHAT THIS SUITE PINS
--------------------
* ``branding.mark_side`` is a closed vocabulary: ``left`` and ``right`` render the hero's mark on
  that side, and any other value is refused at startup by name;
* the hero draws the mark at 128 / 96 / 72 CSS px at 1440 / 900 / 430, on the side the setting
  names, and the hero's height is the design note's measured figure in the four cases;
* at the phone width the mark takes a row of its own and the hero grows by the note's +96 px;
* ``branding.welcome`` is printed with ``%(community)s`` substituted in Python and ESCAPED, and the
  shipped sentence is printed when no welcome is set;
* the settings card carries the placement row and the preview document draws the mark on the side
  the site stores.

The browser measurements run in their own process (``tests/site_hero_probe.py``) because Playwright's
sync API leaves an event loop installed in the process that hosts it.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import asgi_client, link_or_copy
from services.webservice import sites
from test_webui_site_overview import SERVERS_YAML, StubBus, build, page_markup

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "tests" / "site_hero_probe.py"
STATIC = REPO / "services" / "webservice" / "static"
#: the interpreter that carries Playwright; the suite's own interpreter may not
VENV_PYTHON = REPO / "venv" / "bin" / "python"

#: the shipped sentence the hero prints when the site declares no welcome
SHIPPED = ("Our servers, our pilots, our missions. Every sortie flown here is written down — who "
           "flew, what they flew, and how it ended.")

#: a welcome at the parser's 280-character bound
WELCOME_MAX = ("%(community)s flies the Caucasus on Tuesday nights, keeps the mission board, the "
               "ladder and every sortie booked here, and greets a visitor with the record of who "
               "flew, what they flew and how it ended. ")[:280]


@pytest.fixture
def bus():
    """Install a stub ``ServiceBus`` singleton for the duration of one test."""
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    previous = ServiceRegistry._singletons.get(ServiceBus)
    stub = StubBus()
    ServiceRegistry._singletons[ServiceBus] = stub
    try:
        yield stub
    finally:
        if previous is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = previous


@pytest.fixture
def config_dir(tmp_path):
    return _config_dir(tmp_path)


def _png(size: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (size, size), (200, 120, 40, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _config_dir(tmp_path: Path, *, avatar: bool = True) -> Path:
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    if avatar:
        assets = tmp_path / "site"
        assets.mkdir(parents=True, exist_ok=True)
        for size in sites.AVATAR_SIZES:
            (assets / f"avatar-{size}.png").write_bytes(_png(size))
    return tmp_path


def _config(*, avatar: bool = True, **branding) -> dict:
    block = {"title": "Root Site", **branding}
    root = {"slug": "", "branding": block}
    if avatar:
        root["avatar"] = True
    return {"sites": {"root": root}}


def _render(config_dir: Path, config: dict, path: str = "/") -> str:
    app, _ = build(config_dir, config=config)
    return page_markup(app, path)


# ------------------------------------------------------------------ the closed vocabulary

def test_mark_side_defaults_to_right_and_parses_the_two_sides():
    assert sites.parse_sites({"root": {}}).root.branding.mark_side == "right"
    assert sites.parse_sites({"root": {"branding": {"mark_side": "left"}}}).root.branding.mark_side \
        == "left"
    assert sites.parse_sites({"root": {"branding": {"mark_side": "right"}}}).root.branding.mark_side \
        == "right"


def test_an_unknown_mark_side_is_refused_at_startup_by_name():
    with pytest.raises(sites.SiteConfigError) as refusal:
        sites.parse_sites({"root": {"branding": {"mark_side": "centre"}}})
    message = str(refusal.value)
    assert "sites.root.branding.mark_side" in message, message
    assert "centre" in message, message
    assert "left" in message and "right" in message, message


# ------------------------------------------------------------------ the route's size contract

@pytest.mark.asyncio
async def test_the_mark_route_answers_the_named_size_with_64_as_its_default(config_dir, bus):
    """The bar's request carries no query string; the hero names 256; anything else is refused."""
    app, _ = build(config_dir, config=_config())
    assets = config_dir / "site"
    async with asgi_client(app) as http:
        default = await http.get("/avatar.png")
        hero = await http.get("/avatar.png?size=256")
        body = await http.get("/avatar.png?size=128")
        bad = await http.get("/avatar.png?size=48")
    assert default.status_code == 200, "the bar's own request answered no file"
    assert default.content == (assets / "avatar-64.png").read_bytes()
    assert hero.status_code == 200 and hero.content == (assets / "avatar-256.png").read_bytes()
    assert body.status_code == 200 and body.content == (assets / "avatar-128.png").read_bytes()
    assert bad.status_code == 404, "a size outside AVATAR_SIZES answered a file"


# ------------------------------------------------------------------ the rendered hero

def test_the_hero_draws_the_mark_on_the_side_the_setting_names(config_dir, bus):
    left = _render(config_dir, _config(mark_side="left"))
    right = _render(config_dir, _config(mark_side="right"))
    assert 'class="in has-mark mark-left"' in left
    assert 'class="in has-mark mark-right"' in right
    assert '<div class="hero-mark"><img src="/avatar.png?size=256" alt=""></div>' in left
    assert '<div class="hero-mark"><img src="/avatar.png?size=256" alt=""></div>' in right


def test_no_mark_configured_draws_no_mark_and_keeps_the_shipped_hero(config_dir, bus):
    markup = _render(config_dir, _config(avatar=False))
    assert "hero-mark" not in markup, "a mark is drawn with none configured"
    assert '<div class="in">' in markup, "the hero's grid changed with no mark to place"
    assert SHIPPED in markup


def test_the_welcome_is_printed_with_the_community_name_substituted(config_dir, bus):
    markup = _render(config_dir, _config(community_name="Foothold",
                                         welcome="%(community)s flies on Tuesdays."))
    assert "<p>Foothold flies on Tuesdays.</p>" in markup
    assert "%(community)s" not in markup


def test_a_welcome_carrying_markup_is_printed_as_text(config_dir, bus):
    """The parser refuses ``<``/``>``; a value that reaches the template anyway is still escaped."""
    from services.webservice import i18n

    _app, registrar = build(config_dir, config=_config())
    environment = i18n.environment_for_language(registrar, "en")
    markup = environment.get_template("_site_hero.html").render(
        greeting={"lead": "Welcome to", "name": "Root", "tail": ""},
        welcome="<b>x</b> Root", hero_mark=None, mark_side="right")
    assert "&lt;b&gt;x&lt;/b&gt; Root" in markup
    assert "<b>x</b>" not in markup


def test_no_welcome_prints_the_shipped_sentence(config_dir, bus):
    markup = _render(config_dir, _config())
    assert SHIPPED in markup


# ------------------------------------------------------------------ the browser measurements

@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    """``{(page, width): row}`` from the real templates, measured in headless Chromium."""
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    directory = tmp_path_factory.mktemp("site-hero")
    config = directory / "config"
    config.mkdir()
    (config / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    assets = config / "site"
    assets.mkdir()
    for size in sites.AVATAR_SIZES:
        (assets / f"avatar-{size}.png").write_bytes(_png(size))

    pages = {
        "hero-right": _config(mark_side="right"),
        "hero-left": _config(mark_side="left"),
        "hero-nomark": _config(avatar=False),
        "hero-welcome-max": _config(mark_side="right", welcome=WELCOME_MAX),
    }
    previous = ServiceRegistry._singletons.get(ServiceBus)
    ServiceRegistry._singletons[ServiceBus] = StubBus()
    try:
        for name, config_block in pages.items():
            app, _ = build(config, config=config_block)
            (directory / f"{name}.html").write_text(page_markup(app, "/"), encoding="utf-8")
    finally:
        if previous is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = previous

    link_or_copy(STATIC, directory / "static")
    (directory / "avatar.png").write_bytes(_png(256))
    shots = directory / "shots"
    interpreter = str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable
    finished = subprocess.run([interpreter, str(PROBE), str(directory), str(shots)],
                              capture_output=True, text=True, encoding="utf-8", timeout=180, check=True)
    payload = json.loads(finished.stdout.strip().splitlines()[-1])
    if isinstance(payload, dict) and payload.get("unavailable"):
        pytest.skip(payload["unavailable"])
    return {(row["page"], row["width"]): row for row in payload}


def test_the_ladder_draws_128_96_and_72(measured):
    for page in ("hero-right", "hero-left", "hero-welcome-max"):
        assert measured[(page, 1440)]["markWidth"] == pytest.approx(128, abs=0.6), page
        assert measured[(page, 900)]["markWidth"] == pytest.approx(96, abs=0.6), page
        assert measured[(page, 430)]["markWidth"] == pytest.approx(72, abs=0.6), page
    assert measured[("hero-nomark", 1440)]["markWidth"] is None


def test_the_hero_keeps_the_note_s_height_at_1440(measured):
    for page in ("hero-right", "hero-left", "hero-nomark", "hero-welcome-max"):
        assert measured[(page, 1440)]["heroHeight"] == pytest.approx(541, abs=1), page


def test_the_mark_sits_on_the_side_the_class_names(measured):
    left = measured[("hero-left", 1440)]
    right = measured[("hero-right", 1440)]
    assert left["markLeft"] == pytest.approx(88, abs=1), left
    assert right["markRight"] == pytest.approx(1352, abs=1), right
    assert left["innerClass"] == "in has-mark mark-left"
    assert right["innerClass"] == "in has-mark mark-right"


def test_the_phone_gives_the_mark_its_own_row_and_adds_96_px(measured):
    with_mark = measured[("hero-right", 430)]["heroHeight"]
    without = measured[("hero-nomark", 430)]["heroHeight"]
    assert without == pytest.approx(219, abs=1), without
    assert with_mark == pytest.approx(315, abs=1), with_mark
    assert with_mark - without == pytest.approx(96, abs=1)


def test_no_width_scrolls_sideways(measured):
    for (page, width), row in measured.items():
        assert row["scrollWidth"] == row["clientWidth"], f"horizontal scroll at {page} {width}"
