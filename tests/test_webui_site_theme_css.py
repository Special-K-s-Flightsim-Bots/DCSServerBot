"""The site's theme sheets: the four shipped presets, the expert block, and the route that serves them.

WHAT THIS SUITE PINS
--------------------
* the registry is the list and the files are the values: every preset id has a sheet, every sheet
  has an id, and the four re-declare the SAME token names — a preset that names a token ``site.css``
  never declares would silently do nothing;
* every shipped sheet is TOKEN-ONLY: one ``:root`` block of custom-property declarations, no
  selector, no ``@import``, no ``url(``;
* the expert block accepts declarations only, and every forbidden construct is refused by the
  sentence that names it and its line — never by a generic failure;
* the composer answers exactly ONE source (a preset, the operator's sheet, the expert block), and
  refuses two;
* the route answers ``text/css; charset=utf-8`` with ``nosniff`` and a strong ``ETag``, and
  ``304`` on a matching ``If-None-Match``;
* the route is registered for every site and resolved per request: a preset saved after the
  console installed is served without a reload, and a disabled site serves nothing.

The save-side half of T1 — "nothing is written, the file is sha-identical, no ``.bak``" — belongs to
the settings page that performs the write, not to the validator this module owns.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest
from starlette.requests import Request

from conftest import asgi_client, create_app, install_shell
from core.utils.helper import ThreadSafeDict
from services.webservice import mount, permissions, site_pages, sites, theme_css

REPO = Path(__file__).resolve().parent.parent
SITE_CSS = REPO / "services" / "webservice" / "static" / "site.css"

SERVERS_YAML = """\
Root-1:
  port: 10308
Group-1:
  managed_by:
    - 5th Guards
"""

#: the two palettes a light sheet would need the browser's native surfaces for
LIGHT_PRESETS = tuple(preset.id for preset in theme_css.PRESETS if preset.scheme == "light")


# --------------------------------------------------------------------------- doubles

class _Cursor:
    def __init__(self, pool):
        self._pool = pool
        self._rows = iter(())

    async def execute(self, statement, params=None):
        self._pool.calls.append((statement, params))
        self._rows = iter(())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._rows)
        except StopIteration:
            raise StopAsyncIteration from None


class _Connection:
    def __init__(self, pool):
        self._pool = pool

    def cursor(self, **_kwargs):
        return _Cursor(self._pool)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class StubPool:
    """A pool that answers every read with an empty result: these pins read no figure."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def connection(self):
        return _Connection(self)


class StubBus:
    def __init__(self):
        self.apool = StubPool()
        self.servers = ThreadSafeDict({
            "Root-1": types.SimpleNamespace(name="Root-1", locals={}, status=None, players={}),
            "Group-1": types.SimpleNamespace(name="Group-1", locals={"managed_by": ["5th Guards"]},
                                             status=None, players={}),
        })


@pytest.fixture
def bus():
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    previous = ServiceRegistry._singletons.get(ServiceBus)
    ServiceRegistry._singletons[ServiceBus] = StubBus()
    try:
        yield
    finally:
        if previous is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = previous


@pytest.fixture(autouse=True)
def restore_console_mount():
    """``mount``'s prefix is process state; a site install must not leak into the next test."""
    saved = mount._PREFIX
    try:
        yield
    finally:
        mount._PREFIX = saved


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


def site_config(root=None, group1=None):
    """Two sites — the root site and a group's — with the fields the palette reads."""
    root_block = {"slug": "", "branding": {"title": "Root Site"}}
    root_block.update(root or {})
    group_block = {"slug": "group1", "group_role": "5th Guards",
                   "branding": {"title": "5th Guards"}}
    group_block.update(group1 or {})
    return {"sites": {"root": root_block, "group1": group_block}}


def build(config_dir, config=None):
    """The real shell, from the production factory, with a site (or two) configured."""
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config=config or site_config())
    return app


def get(app, path, headers=None):
    """One GET through the real application, as a visitor's browser would send it."""
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return await client.get(path, headers=headers or {})

    return asyncio.run(run())


def markup(app, path="/"):
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get(path)).text

    return asyncio.run(run())


# ----------------------------------------------- the registry is the list, the files are the values

def test_every_preset_id_has_a_sheet_and_every_sheet_has_an_id():
    files = {path.stem for path in theme_css.PRESET_DIR.glob("*.css")}
    assert files == set(theme_css.PRESET_IDS), (
        f"the registry and the shipped folder disagree: files={sorted(files)} "
        f"ids={sorted(theme_css.PRESET_IDS)}")
    assert len(theme_css.PRESETS) == len(theme_css.PRESET_IDS), "a preset id is listed twice"
    for preset in theme_css.PRESETS:
        assert preset.filename == f"{preset.id}.css"
        assert (theme_css.PRESET_DIR / preset.filename).is_file()
        assert preset.scheme in ("dark", "light"), preset
        assert preset.label, "a preset with no label cannot be drawn on the gallery"
    assert theme_css.DEFAULT_PRESET in theme_css.PRESET_IDS


# --------------------------------------------------------------- every preset is token-only

COMMENT = re.compile(r"/\*.*?\*/", re.S)
DECLARATION = re.compile(r"^--[a-z0-9-]{1,64}\s*:\s*\S.*;$")


def _sheet(preset) -> str:
    return (theme_css.PRESET_DIR / preset.filename).read_text(encoding="utf-8")


def _without_comments(text: str) -> str:
    return COMMENT.sub("", text)


def _root_body(text: str) -> str:
    """The declarations of the sheet's one ``:root`` block, comments removed."""
    body = _without_comments(text).strip()
    assert body.startswith(":root {") and body.endswith("}"), body[:80]
    return body[body.index("{") + 1: body.rindex("}")]


def test_every_shipped_preset_is_token_only():
    for preset in theme_css.PRESETS:
        text = _sheet(preset)
        # the file grammar, at the file level: nothing loads, fetches or at-rules
        assert "@import" not in text, f"{preset.filename} imports a sheet"
        assert "@" not in text, f"{preset.filename} carries an at-rule"
        assert "url(" not in text.lower(), f"{preset.filename} fetches a file"

        body = _root_body(text)
        declarations = [part.strip() for part in body.split(";") if part.strip()]
        assert declarations, f"{preset.filename} declares nothing"
        for declaration in declarations:
            assert declaration.startswith("--"), (
                f"{preset.filename} carries a non-token declaration: {declaration!r}")
            assert DECLARATION.match(declaration + ";"), (
                f"{preset.filename} carries a malformed declaration: {declaration!r}")

        # no selector text: with the block and the comments gone, nothing is left
        rest = _without_comments(text)
        start, end = rest.index(":root {"), rest.rindex("}")
        assert not rest[end + 1:].strip(), f"{preset.filename} carries text after its block"
        assert not rest[:start].strip(), f"{preset.filename} carries text before its block"


def test_the_four_presets_re_declare_the_same_token_names():
    """A preset is a restatement of the same contract: same names, different values.

    It is what makes "anything a preset can do, a hand-written sheet can do" true — and it means a
    preset cannot silently lose a token the components read.
    """
    names = {}
    for preset in theme_css.PRESETS:
        body = _root_body(_sheet(preset))
        names[preset.id] = [part.split(":", 1)[0].strip()
                            for part in body.split(";") if part.strip()]
    reference = names[theme_css.DEFAULT_PRESET]
    assert len(reference) == len(set(reference)), "the default sheet declares a token twice"
    for preset_id, declared in names.items():
        assert sorted(declared) == sorted(reference), (
            f"{preset_id} declares a different token set: "
            f"extra={sorted(set(declared) - set(reference))} "
            f"missing={sorted(set(reference) - set(declared))}")


def test_every_token_a_preset_declares_is_one_site_css_reads():
    declared_in_site_css = set(re.findall(r"(--[a-z0-9-]+)\s*:",
                                          SITE_CSS.read_text(encoding="utf-8")))
    for preset in theme_css.PRESETS:
        body = _root_body(_sheet(preset))
        for name in re.findall(r"(--[a-z0-9-]+)\s*:", body):
            assert name in declared_in_site_css, (
                f"{preset.filename} re-declares {name}, which site.css never declares")


# --------------------------------------------------------------------- T1: the expert validator

#: ``(text, the words the refusal must carry)`` — one per forbidden construct, each on line 1.
REFUSALS: tuple[tuple[str, str], ...] = (
    ("a.nav { color: #ffffff; }", "a component selector is not accepted"),
    ("--hero-glow: url(site/glow.png);", "url() is not accepted"),
    ('@import "other.css";', "an at-rule is not accepted"),
    ("--accent: expression(alert(1));", "expression() is not accepted"),
    ("--accent: javascript:alert(1);", "javascript: is not accepted"),
    ("--accent: -moz-binding foo;", "-moz-binding is not accepted"),
    ("--accent: behavior:expr;", "behavior: is not accepted"),
    ("--accent: #fff !important;", "!important is not accepted"),
    ("--accent: <b>;", "'<' and '>' are not accepted"),
    ("--accent: a;}", "'}' is not accepted"),
    ("--accent: {a};", "'{' is not accepted"),
    ("color: red;", "only a custom property may be declared here"),
    ("--Accent: red;", "a token name matches"),
    ("--accent: red", "a declaration ends with ';'"),
    ("--accent: ;", "a declaration carries a value"),
    ("--a: red; --b: blue;", "one declaration per line"),
)


def test_refuses_a_component_rule():
    """T1 — every forbidden construct is refused by its OWN sentence, naming the line."""
    for text, expected in REFUSALS:
        refusal = theme_css.validate(text)
        assert refusal is not None, f"accepted a block it must refuse: {text!r}"
        assert refusal.line == 1, f"{text!r} was reported on line {refusal.line}"
        assert expected in refusal.message, (
            f"{text!r} was refused with the wrong sentence: {refusal.message!r}")
        assert refusal.as_dict()["ok"] is False


def test_the_refusal_names_the_line_the_construct_sits_on():
    refusal = theme_css.validate("--ground: #17120d;\n\n--fg: #f5eee1;\na.nav { color: red; }\n")
    assert refusal is not None
    assert refusal.line == 4
    assert "Line 4" in refusal.message
    assert "component selector" in refusal.message


def test_accepts_a_block_of_declarations():
    assert theme_css.validate("--ground: #17120d;\n\n  --fg: #f5eee1;\n") is None
    assert theme_css.validate("") is None
    assert theme_css.validate(None) is None


def test_the_length_cap_is_the_models_own():
    assert theme_css.MAX_EXPERT_LENGTH == sites.MAX_EXPERT_LENGTH
    too_long = "--pad: " + "1" * theme_css.MAX_EXPERT_LENGTH + ";"
    refusal = theme_css.validate(too_long)
    assert refusal is not None
    assert str(theme_css.MAX_EXPERT_LENGTH) in refusal.message
    assert refusal.line is None, "the cap is the whole block's, not one line's"


# ------------------------------------------------------------------------- the composer

def test_the_composer_refuses_two_sources():
    theme = sites.Theme(preset="brass", expert="--accent: #fff;")
    with pytest.raises(ValueError) as refusal:
        theme_css.compose(theme)
    message = str(refusal.value)
    assert "preset" in message and "expert" in message, message


def test_the_composer_serves_a_presets_sheet_verbatim():
    """A preset IS a token sheet: the composer answers the shipped file's own text, unedited."""
    theme = sites.Theme(preset="slate")
    assert theme_css.compose(theme) == (
        theme_css.PRESET_DIR / theme_css.PRESET_BY_ID["slate"].filename).read_text(encoding="utf-8")


def test_an_unknown_preset_through_the_composer_serves_the_default_palette():
    theme = sites.Theme(preset="no-such-preset")
    assert theme_css.compose(theme) == theme_css.preset_sheet(theme_css.DEFAULT_PRESET)


def test_an_expert_block_is_wrapped_in_one_root_rule():
    composed = theme_css.compose(sites.Theme(expert="--ground: #000;\n\n--fg: #fff;"))
    assert composed == ":root {\n  --ground: #000;\n  --fg: #fff;\n}\n"


def test_a_refused_expert_block_serves_the_default_palette():
    """A public page must not 500 over a hand-edited file: it keeps its palette, the editor says."""
    theme = sites.Theme(expert="a.nav { color: #ffffff; }")
    assert theme_css.compose(theme) == theme_css.preset_sheet(theme_css.DEFAULT_PRESET)


def test_a_site_that_names_no_palette_is_served_the_default_sheet():
    assert theme_css.compose(sites.Theme()) == theme_css.preset_sheet(
        theme_css.DEFAULT_PRESET)


# ------------------------------------------------------ T3: the uploaded sheet is a source too

SHEET_TEXT = """\
:root {
  --brass: #7a4bd1;
}
.hero .band { border-color: var(--brass); }
"""


def _write_sheet(config_dir, text: str = SHEET_TEXT) -> Path:
    folder = Path(config_dir) / sites.ASSET_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / sites.SHEET_LEAF
    path.write_text(text, encoding="utf-8")
    return path


def test_the_composer_serves_an_uploaded_sheet_verbatim(config_dir):
    """A sheet is the operator's own CSS: no wrapping, no rewriting, no validation."""
    _write_sheet(config_dir)
    assert theme_css.compose(sites.Theme(sheet=sites.SHEET_LEAF), config_dir) == SHEET_TEXT


def test_a_sheet_whose_file_is_gone_serves_the_default_palette_with_one_warning(config_dir, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="services.webservice.theme_css"):
        served = theme_css.compose(sites.Theme(sheet="missing.css"), config_dir)
    assert served == theme_css.preset_sheet(theme_css.DEFAULT_PRESET)
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1, [record.getMessage() for record in warnings]
    assert "missing.css" in warnings[0].getMessage()


def test_the_composer_refuses_a_sheet_beside_another_source(config_dir):
    _write_sheet(config_dir)
    for other in ({"preset": "brass"}, {"expert": "--brass: #fff;"}):
        theme = sites.Theme(sheet=sites.SHEET_LEAF, **other)
        with pytest.raises(ValueError) as refusal:
            theme_css.compose(theme, config_dir)
        message = str(refusal.value)
        assert "sheet" in message, message


def test_a_sheet_takes_the_deprecated_light_flag_for_its_scheme():
    """A sheet names no scheme of its own, so the page's scheme falls back to the flag."""
    assert theme_css.scheme(sites.Theme(sheet=sites.SHEET_LEAF)) == "dark"
    assert theme_css.scheme(sites.Theme(sheet=sites.SHEET_LEAF, light=True)) == "light"


def test_the_route_serves_the_uploaded_sheet_and_its_etag_follows(config_dir, bus):
    """The public address answers the FILE, and the tag moves when the file does."""
    _write_sheet(config_dir)
    (config_dir / sites.SITE_FILE).write_text(
        "sites:\n"
        "  root:\n"
        "    slug: \"\"\n"
        f"    theme:\n      sheet: {sites.SHEET_LEAF}\n"
        "  group1:\n"
        "    slug: group1\n"
        "    group_role: 5th Guards\n",
        encoding="utf-8")
    app = build(config_dir)

    first = get(app, "/theme.css")
    assert first.status_code == 200
    assert first.text == SHEET_TEXT, "the route did not answer the uploaded file verbatim"
    assert first.headers["content-type"] == "text/css; charset=utf-8"

    _write_sheet(config_dir, SHEET_TEXT + "\n.band { color: #fff; }\n")
    second = get(app, "/theme.css")
    assert second.text != first.text
    assert second.headers["etag"] != first.headers["etag"], "the ETag did not follow the bytes"


def test_the_page_links_the_theme_address_for_a_sheet_only_site(config_dir, bus):
    """A site whose palette IS an uploaded sheet still gets the second request."""
    _write_sheet(config_dir)
    (config_dir / sites.SITE_FILE).write_text(
        "sites:\n"
        "  root:\n"
        "    slug: \"\"\n"
        f"    theme:\n      sheet: {sites.SHEET_LEAF}\n",
        encoding="utf-8")
    page = markup(build(config_dir), "/")
    assert 'href="/theme.css"' in page, "the page does not load the sheet's address"



def test_an_unknown_preset_falls_back_to_the_default_sheet(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="services.webservice.theme_css"):
        assert theme_css.preset_sheet("paper") == theme_css.preset_sheet(theme_css.DEFAULT_PRESET)
    assert any("paper" in record.getMessage() for record in caplog.records), \
        "a preset this build does not ship must say so once"


# --------------------------------------------------------------------------- the answer

def test_the_etag_is_strong_and_a_match_answers_304():
    def request(headers=None):
        pairs = [(name.lower().encode(), value.encode())
                 for name, value in (headers or {}).items()]
        return Request({"type": "http", "headers": pairs})

    first = theme_css.response(request(), "--ground: #000;")
    etag = first.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"') and not etag.startswith("W/"), etag
    assert first.headers["content-type"] == "text/css; charset=utf-8"
    assert first.headers["x-content-type-options"] == "nosniff"
    assert first.headers["cache-control"] == "no-cache"
    assert first.body == b"--ground: #000;"

    again = theme_css.response(request({"if-none-match": etag}), "--ground: #000;")
    assert again.status_code == 304
    assert again.body == b"", "a 304 carries no body"
    assert again.headers["etag"] == etag

    # a weak copy of the same tag is still the same representation; a different body is not
    assert theme_css.response(request({"if-none-match": f"W/{etag}"}),
                              "--ground: #000;").status_code == 304
    changed = theme_css.response(request({"if-none-match": etag}), "--ground: #fff;")
    assert changed.status_code == 200
    assert changed.headers["etag"] != etag


# ------------------------------------------------------- T2: the route, for every site

def test_serves_preset_then_expert(config_dir, bus):
    """T2 — ``text/css`` + ``nosniff``; the site's declarations load AFTER the shipped sheet.

    The route is one request, and its body is the palette in force: the preset sheet when the site
    names one, one ``:root`` rule when the site wrote the declarations. Either way the page loads
    ``site.css`` first and this answer second, so the site's values win by source order.
    """
    expert = "--brass: #e8b04b;\n--brass-hi: #f0c070;"
    app = build(config_dir, site_config(root={"theme": {"preset": "midnight"}},
                                        group1={"theme": {"expert": expert}}))

    sheet = get(app, "/theme.css")
    assert sheet.status_code == 200
    assert sheet.headers["content-type"] == "text/css; charset=utf-8"
    assert sheet.headers["x-content-type-options"] == "nosniff"
    assert sheet.text == theme_css.preset_sheet("midnight")
    assert sheet.headers["etag"]

    wrapped = get(app, "/group1/theme.css")
    assert wrapped.status_code == 200
    assert wrapped.text == ":root {\n  --brass: #e8b04b;\n  --brass-hi: #f0c070;\n}\n"

    # source order on the page: the shipped sheet first, the site's sheet after it
    page = markup(app, "/")
    assert page.index('href="/static/site.css') < page.index('href="/theme.css"')
    grouped = markup(app, "/group1")
    assert 'href="/group1/theme.css"' in grouped


def test_a_304_is_answered_through_the_route(config_dir, bus):
    app = build(config_dir, site_config(root={"theme": {"preset": "slate"}}))
    first = get(app, "/theme.css")
    again = get(app, "/theme.css", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""


def test_the_route_exists_for_a_site_that_names_no_palette(config_dir, bus):
    """A route cannot be registered by a save, so the address answers whatever the site says now."""
    app = build(config_dir)
    for path in ("/theme.css", "/group1/theme.css"):
        answer = get(app, path)
        assert answer.status_code == 200, path
        assert answer.text == theme_css.preset_sheet(theme_css.DEFAULT_PRESET)


def test_a_preset_saved_after_install_is_served_without_a_reload(config_dir, bus):
    """The sheet is resolved per request from the live catalogue, not from the installed one."""
    app = build(config_dir)
    assert get(app, "/theme.css").text == theme_css.preset_sheet(theme_css.DEFAULT_PRESET)

    (config_dir / sites.SITE_FILE).write_text(
        "sites:\n"
        "  root:\n"
        "    slug: \"\"\n"
        "    theme:\n"
        "      preset: parchment\n"
        "  group1:\n"
        "    slug: group1\n"
        "    group_role: 5th Guards\n", encoding="utf-8")

    assert get(app, "/theme.css").text == theme_css.preset_sheet("parchment")


def test_a_disabled_site_serves_no_sheet(config_dir, bus):
    app = build(config_dir, site_config(group1={"enabled": False}))
    assert get(app, "/group1/theme.css").status_code == 404
    assert get(app, "/group1").status_code == 404, "a disabled site registers no route at all"
    assert get(app, "/theme.css").status_code == 200, "the root site is untouched"


def test_an_unknown_prefix_answers_404(config_dir, bus):
    app = build(config_dir)
    assert get(app, "/nosuchgroup/theme.css").status_code == 404


def test_the_route_registers_under_the_site_owner_as_public(config_dir, bus):
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True,
                                                             config_dir=str(config_dir)),
                              config=site_config(root={"theme": {"preset": "brass"}}))
    owned = {route.path for route in registrar.routes if getattr(route, "path", None)}
    assert site_pages.THEME_SHEET_PATH in owned
    assert "/group1" + site_pages.THEME_SHEET_PATH in owned
    assert registrar.capability_for(path=site_pages.THEME_SHEET_PATH) == permissions.PUBLIC


# ------------------------------------------------------------------ the light palettes' scheme

def test_a_light_preset_flips_the_scheme_and_a_dark_one_keeps_it(config_dir, bus):
    light = markup(build(config_dir, site_config(root={"theme": {"preset": LIGHT_PRESETS[0]}})))
    assert 'data-theme="light"' in light, "a light preset must not render a dark page's scheme"

    dark = markup(build(config_dir, site_config(root={"theme": {"preset": "midnight"}})))
    assert 'data-theme="dark"' in dark


def test_site_css_carries_the_light_scheme_block():
    """``color-scheme`` is not a custom property, so the shipped sheet has to state it."""
    text = SITE_CSS.read_text(encoding="utf-8")
    assert re.search(r'html\[data-theme="light"\]\s*\{\s*color-scheme:\s*light;?\s*\}', text), \
        "site.css has no html[data-theme=\"light\"] colour-scheme block"
