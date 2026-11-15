"""The admin console's THEME (slice 3): every colour a token, and a real light palette.

Observed behaviour was "the operator hit unreadable colours". The cause was structural, and it is
measured here rather than asserted:

* 41 shipped ``shell.css`` rules hard-coded colour literals, so a second palette could not be
  DECLARED — only re-typed rule by rule. Every one of them is a token now, and the dark value of
  each token IS the shipped literal (pinned below), so dark mode is unchanged to the pixel;
* the editor's SYNTAX colours were already tokens, but its WASHES were literals, so half of the
  editor followed a theme change and half did not — the washes are tokens now too.

The contrast table is the designer's (``mockups/dcssb-admin-ui/settings-tiles/_contrast.py``),
ported to read the REAL sheets: it resolves every ``var()`` chain in both themes and fails if any
pair the console actually paints drops below WCAG AA. One hex value cannot regress it quietly.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from conftest import asgi_client, stub_identity
from services.webservice import i18n, theme

WEBSERVICE = Path(__file__).resolve().parent.parent / "services" / "webservice"
STATIC = WEBSERVICE / "static"
SHELL = (STATIC / "shell.css").read_text(encoding="utf-8")
SCOPE = (STATIC / "settings-scope.css").read_text(encoding="utf-8")


def _no_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


# --------------------------------------------------------------------------- token parsing

def _tokens(text: str, selector: str) -> dict[str, str]:
    """Every ``--name: value;`` inside the ``selector { … }`` blocks (all of them merged)."""
    found: dict[str, str] = {}
    for match in re.finditer(re.escape(selector) + r"\s*\{([^}]*)\}", _no_comments(text)):
        found.update(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", match.group(1)))
    return {k: v.strip() for k, v in found.items()}


DARK = _tokens(SHELL, ":root")                       # the palette + the named literals (both :root blocks)
LIGHT = dict(DARK)
LIGHT.update(_tokens(SHELL, 'html[data-theme="light"]'))


def _resolve(store: dict[str, str], value: str, depth: int = 0) -> str:
    value = value.strip()
    match = re.fullmatch(r"var\((--[\w-]+)\)", value)
    if match and depth < 8:
        return _resolve(store, store.get(match.group(1), "#000000"), depth + 1)
    return value


def _rgb(value: str):
    value = value.strip()
    if value.startswith("#"):
        h = value[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    match = re.fullmatch(r"rgba?\(([^)]+)\)", value)
    if match:
        parts = [p.strip() for p in match.group(1).split(",")]
        return tuple(float(parts[i]) for i in (0, 1, 2))
    return None


def _over(fg: str, bg: str) -> str:
    match = re.fullmatch(r"rgba\(([^)]+)\)", fg.strip())
    if not match:
        return fg
    a, b = _rgb(fg), _rgb(bg)
    if a is None or b is None:
        return fg
    alpha = float(match.group(1).split(",")[3])
    return "#%02x%02x%02x" % tuple(round(a[i] * alpha + b[i] * (1 - alpha)) for i in range(3))


def _luminance(rgb) -> float:
    def channel(x: float) -> float:
        x /= 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(fg: str, bg: str) -> float:
    a, b = _rgb(fg), _rgb(bg)
    assert a and b, (fg, bg)
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


#: name, foreground token, background token, minimum. Every pair the console actually paints — the
#: designer's 20, plus the chip/dot/log pairs the shipped sheet draws. 4.5 for body text, 3.0 for
#: large text / non-text UI (a rail, a status dot, a syntax colour).
PAIRS = [
    ("body text on a card", "--text", "--panel", 4.5),
    ("secondary text on a card", "--text-2", "--panel", 4.5),
    ("help text / caption on a card", "--text-3", "--panel", 4.5),
    ("help text on the page ground", "--text-3", "--ground", 4.5),
    ("secondary text on a raised panel", "--text-2", "--panel-2", 4.5),
    ("plugin rail + kind word", "--kind-plugin", "--panel", 3.0),
    ("service rail + kind word", "--kind-service", "--panel", 3.0),
    ("extension rail + kind word", "--kind-ext", "--panel", 3.0),
    ("kind word on its own wash", "--kind-ext", "--kind-ext-wash", 3.0),
    ("warning chip text on its chip", "--warn-text", "--warn-bg", 4.5),
    # the node row's waiting-DCS glyph rests on the CARD (its strip button is transparent at rest)
    ("waiting DCS glyph on a card", "--warn-text", "--panel", 4.5),
    # the attention banner's ADVISORY tone: body text on the amber wash, and the amber dot on it
    ("advisory banner text on its wash", "--text", "--warn-bg", 4.5),
    ("advisory banner dot on its wash", "--warn-text", "--warn-bg", 3.0),
    ("info chip text on its chip", "--inc-text", "--inc-bg", 4.5),
    ("cycle chip text on its chip", "--cycle-text", "--cycle-bg", 4.5),
    ("danger glyph on a card", "--danger-text", "--panel", 4.5),
    ("field label on a block ground", "--text", "--ground-2", 4.5),
    ("input text on an input", "--text", "--ground-2", 4.5),
    ("log text in the log well", "--text-2", "--well", 4.5),
    ("editor keyword", "--link", "--ground-2", 3.0),
    ("editor string / bool", "--run", "--ground-2", 3.0),
    ("editor number", "--accent", "--ground-2", 3.0),
    ("editor comment / gutter", "--text-3", "--ground-2", 3.0),
    ("editor meta", "--pause", "--ground-2", 3.0),
    ("status dot RUN", "--run", "--panel", 3.0),
    ("status dot PAUSE", "--pause", "--panel", 3.0),
    ("status dot DEAD", "--dead", "--panel", 3.0),
    ("log info line", "--log-info", "--well", 3.0),
]


def _table(store: dict[str, str]):
    rows, bad = [], []
    for label, fg_token, bg_token, minimum in PAIRS:
        bg = _resolve(store, store.get(bg_token, "#000000"))
        if bg.startswith("rgba("):
            bg = _over(bg, _resolve(store, store.get("--panel", "#000000")))
        fg = _over(_resolve(store, store.get(fg_token, "#000000")), bg)
        value = _ratio(fg, bg)
        ok = value >= minimum
        if not ok:
            bad.append((label, value))
        rows.append((label, value, minimum, ok))
    return rows, bad


def test_every_token_pair_clears_wcag_aa_in_BOTH_themes():
    """The measured table: 0 FAIL in dark, 0 FAIL in light (WCAG AA / large-text minimums)."""
    for name, store in (("DARK", DARK), ("LIGHT", LIGHT)):
        rows, bad = _table(store)
        tightest = sorted(rows, key=lambda row: row[1])[:4]
        print(f"[{name}] tightest: " + ", ".join(f"{l}={v:.2f}(min {m})" for l, v, m, _ in tightest))
        assert not bad, f"{name} theme: below AA: " + ", ".join(f"{l}={v:.2f}" for l, v in bad)


# --------------------------------------------------------------------------- the dark literal pin

#: token -> the SHIPPED literal it replaces. If one changes, dark mode changed, and that is a
#: deliberate act that must edit BOTH this map and the sheet.
SHIPPED_DARK = {
    "--nav-active": "rgba(232, 163, 61, .16)", "--nav-active-2": "rgba(232, 163, 61, .03)",
    "--bar-hd": "#121924", "--bar-hd2": "#0f151d", "--flag-ring": "rgba(255, 255, 255, .18)",
    "--run-halo": "rgba(63, 185, 80, .15)", "--pause-halo": "rgba(232, 163, 61, .15)",
    "--dead-halo": "rgba(248, 81, 73, .15)", "--hd": "#18202b", "--hd2": "#151c26",
    "--warn-line": "#5a4319", "--warn-text": "#f0c689", "--warn-bg": "#241c0d",
    "--inc-line": "#274a55", "--inc-text": "#9fd4e0", "--inc-bg": "#12242a",
    "--cycle-line": "#5a2a2a", "--cycle-text": "#f0a3a3", "--cycle-bg": "#2a1414",
    "--th": "#131a24", "--row-hover": "#161e29", "--well": "#0a0e13", "--log-info": "#63d0ff",
    "--ok-line": "#21402a", "--ok-wash": "rgba(63, 185, 80, .06)",
    "--bad-line": "#5a2a28", "--bad-wash": "rgba(248, 81, 73, .06)",
    "--danger-text": "#ffb3ae", "--danger-strong": "#ffd7d3",
    "--danger-wash": "rgba(248, 81, 73, .08)", "--danger-hover": "rgba(248, 81, 73, .10)",
    "--danger-btn-text": "#2a0b08", "--danger-btn-line": "#ff6a60", "--danger-btn-top": "#ff6a60",
    "--sel-wash": "rgba(88, 166, 255, .20)", "--sel-wash2": "rgba(88, 166, 255, .28)",
    "--line-wash": "rgba(255, 255, 255, .03)", "--block-wash": "rgba(232, 163, 61, .10)",
    "--err-wash": "rgba(248, 81, 73, .22)", "--tab-active": "rgba(232, 163, 61, .10)",
    "--elev": "rgba(0, 0, 0, .6)", "--elev-sm": "rgba(0, 0, 0, .55)",
    "--scrim": "rgba(0, 0, 0, .62)",
    "--login-line": "#a9711f", "--login-text": "#1a1204", "--login-hover": "#c98f34",
    "--login-lead": "#3a3320", "--login-ring": "rgba(232, 163, 61, .12)",
    "--sw-off": "#26313f", "--sw-on": "#2c4a30", "--sw-on-line": "#3d6b44",
}


def test_the_dark_value_of_every_named_literal_is_the_shipped_literal():
    """Dark is the safety property: each token's dark value IS the literal the rule used to type."""
    for token, literal in SHIPPED_DARK.items():
        assert token in DARK, f"{token} is not declared in :root"
        assert DARK[token] == literal, \
            f"{token} is {DARK[token]!r}, not the shipped literal {literal!r} — dark mode changed"


# --------------------------------------------------------------------------- every colour a token

_HEX_OR_RGBA = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)")
_DECLARATION = re.compile(r"--[\w-]+\s*:[^;]*;")


@pytest.mark.parametrize("sheet", [("shell.css", SHELL), ("settings-scope.css", SCOPE)],
                         ids=["shell.css", "settings-scope.css"])
def test_no_colour_literal_survives_outside_a_token_declaration(sheet):
    """A literal in a RULE BODY is the defect: it cannot follow a theme. Only the token blocks may
    carry colour literals (their dark value IS the literal — pinned above)."""
    name, text = sheet
    body = _DECLARATION.sub("", _no_comments(text))   # drop every ``--name: value;`` declaration
    leftovers = _HEX_OR_RGBA.findall(body)
    assert not leftovers, f"{name} still hard-codes colours in rules: {sorted(set(leftovers))}"


def test_the_editor_s_washes_and_syntax_both_come_from_tokens():
    """The half-and-half bug: the editor's syntax colours were tokens, its washes were literals."""
    washes = {
        ".cfg-editor .CodeMirror-selected": "--sel-wash",
        ".cfg-editor .CodeMirror-focused .CodeMirror-selected": "--sel-wash2",
        ".cfg-editor .CodeMirror-activeline-background": "--line-wash",
        ".cfg-editor .cm-yaml-block": "--block-wash",
        ".cfg-editor .cm-yaml-error": "--err-wash",
    }
    sheet = _no_comments(SHELL)
    for selector, token in washes.items():
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", sheet)
        assert match, f"{selector} is gone"
        background = re.search(r"background:\s*([^;]+);", match.group(1))
        assert background and background.group(1).strip() == f"var({token})", \
            f"{selector} must read var({token}); it is {background and background.group(1).strip()!r}"
    # the syntax colours were already tokens — prove they still are
    for selector, token in {".cfg-editor .CodeMirror .cm-keyword": "--link",
                            ".cfg-editor .CodeMirror .cm-string": "--run",
                            ".cfg-editor .CodeMirror .cm-number": "--accent"}.items():
        match = re.search(re.escape(selector) + r"[^{}]*\{([^}]*)\}", sheet)
        assert match, f"{selector} is gone"
        assert f"var({token})" in match.group(1), f"{selector} lost its {token} token"


# --------------------------------------------------------------------------- the switch, rendered

def _request(app, *, theme_code=None, language=None):
    request = types.SimpleNamespace()
    request.app = app
    request.cookies = {}
    if theme_code:
        request.cookies[theme.COOKIE_NAME] = theme_code
    if language:
        request.cookies[i18n.COOKIE_NAME] = language
    request.headers = {}
    request.url = types.SimpleNamespace(path="/", query="")
    return request


_THEME_NAV = re.compile(r'<nav class="theme".*?</nav>', re.S)


def _normalise(html: str, code: str) -> str:
    return _THEME_NAV.sub("<theme-nav/>", html).replace(f'data-theme="{code}"', 'data-theme="X"')


def test_the_theme_switch_flips_the_attribute_not_the_document(shell_app):
    """Same markup, one attribute: the light render differs from dark only in ``data-theme`` and
    the control's own active marker — i.e. the theme flips TOKENS, not structure."""
    app, _ = shell_app
    stub_identity({"Admin"})
    nav = [{"label": "Operations",
            "links": [{"label": "Dashboard", "url": "/", "active": True}]}]
    dark = i18n.render(_request(app, theme_code="dark"), "base.html", nav_groups=nav)
    light = i18n.render(_request(app, theme_code="light"), "base.html", nav_groups=nav)

    assert 'data-theme="dark"' in dark and 'data-theme="light"' in light
    assert _normalise(dark, "dark") == _normalise(light, "light"), \
        "the light and dark documents differ somewhere other than the theme attribute/control"
    for html in (dark, light):                    # the control renders both options, one marked
        assert html.count('class="theme-opt on"') == 1
        assert html.count('href="/theme?theme=') == 2


def test_the_default_theme_is_dark(shell_app):
    """No cookie → the shipped dark console, resolved server-side."""
    app, _ = shell_app
    assert theme.resolve_theme(_request(app)) == "dark"
    assert 'data-theme="dark"' in i18n.render(_request(app), "base.html")


@pytest.mark.asyncio
async def test_the_theme_renders_on_the_login_page_from_the_cookie(shell_app):
    """The login page is where a visitor first meets the console: the cookie themes it too."""
    app, _ = shell_app
    async with asgi_client(app) as client:
        client.cookies.set(theme.COOKIE_NAME, "light")
        html = (await client.get("/auth/login")).text
    assert 'data-theme="light"' in html
    assert 'href="/theme?theme=' in html


def test_the_theme_names_are_translated(shell_app):
    """The control's option names render in the request's language (the catalog carries them)."""
    app, _ = shell_app
    german = i18n.render(_request(app, language="de"), "base.html")
    assert 'title="Dunkel"' in german and 'aria-label="Hell"' in german
    assert 'title="Dark"' not in german
    english = i18n.render(_request(app, language="en"), "base.html")
    assert 'title="Dark"' in english and 'title="Light"' in english


# --------------------------------------------------------------------------- the switch route

@pytest.mark.asyncio
async def test_the_theme_route_stores_the_choice_and_returns_to_a_safe_path(shell_app):
    """A GET link like /language: sets its own cookie and returns to a page the console declares."""
    app, _ = shell_app
    async with asgi_client(app) as client:
        chosen = await client.get("/theme", params={"theme": "light", "next": "/logs"},
                                  follow_redirects=False)
        unknown = await client.get("/theme", params={"theme": "zz", "next": "//evil.example.com"},
                                   follow_redirects=False)
    assert chosen.status_code == 303
    assert chosen.headers["location"] == "/logs"
    cookie = chosen.headers["set-cookie"]
    assert "dcssb_theme=light" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    # an unknown theme changes nothing, and an off-site target is refused
    assert unknown.status_code == 303
    assert unknown.headers["location"] == "/"
    assert "set-cookie" not in unknown.headers


def test_the_validator_refuses_anything_that_is_not_a_theme():
    assert theme.normalise_theme("dark") == "dark"
    assert theme.normalise_theme("LIGHT") == "light"
    assert theme.normalise_theme("  light ") == "light"
    for bad in ("", "blue", "../../etc", "dark\nX: 1", "D" * 64, None, 7):
        assert theme.normalise_theme(bad) is None


def test_the_light_block_is_a_palette_not_a_rule_restatement():
    """§LIGHT re-declares TOKENS (higher specificity than :root); it does not re-state rules.

    This is the whole design: one attribute flips every token, so the shipped rules need no light
    copy — and a ``html[data-theme="light"] <selector>`` rule (a re-typed rule) would be the
    anti-pattern the tokenisation exists to remove.
    """
    assert re.search(r'html\[data-theme="light"\]\s*\{', _no_comments(SHELL)), "§LIGHT is gone"
    for token in ("--ground", "--panel", "--text", "--text-2", "--text-3", "--accent", "--link",
                  "--well", "--log-info", "--sel-wash", "--kind-ext"):
        assert token in LIGHT, f"{token} is not re-declared for light"
        assert LIGHT[token] != DARK[token], f"{token} did not change for light"
    # and it re-states NO shipped selector (only the one palette block matches the attribute)
    assert not re.search(r'html\[data-theme="light"\]\s+[.#:\w]', _no_comments(SHELL)), \
        "the light theme re-states a rule instead of re-declaring a token"
