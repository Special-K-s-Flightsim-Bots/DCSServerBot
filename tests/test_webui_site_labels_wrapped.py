"""A site template asks the environment for the translation of every ``labels.<name>`` it prints.

A ``labels`` value is a marked literal (``SITE_PALETTE = i18n._("Palette")``): the catalog carries
the msgid and its translation, but printing the value directly prints the MSGID — so the chrome
stays English in every language. The one correct spelling is the environment's own call at RENDER
time, ``{{ _(labels.x) }}``, because a module-level ``_`` would bind one language for the whole
process (see :mod:`services.webservice.i18n`).

Three pins:

* the AST check below flags a ``labels.<name>`` reference that no translation call encloses — in an
  attribute as much as in text, since an attribute is chrome like any other;
* a POSITIVE CONTROL feeds it a snippet with an unwrapped reference, so a checker that matched
  nothing could not pass for the right reason;
* a German request renders the page and each label is asserted by VALUE against the compiled
  catalog, not by the absence of English. The page draws its branches by state, so every state is
  rendered and the labels are asserted against the union of the documents the console really wrote.
"""
from __future__ import annotations

import re

import pytest
from jinja2 import nodes
from markupsafe import escape

from conftest import REPO_ROOT
from services.webservice import i18n, templating
from test_webui_site_settings import (  # noqa: F401 - the harness this page's pins share
    ADMIN, BOT_YAML, GROUP_ADMIN, SERVERS_YAML, SHEET_TEXT, _users, cluster, page_path)
from test_webui_write_pause import build, client, sign_in

TEMPLATES = REPO_ROOT / "services" / "webservice" / "templates"

#: every template the public site and the Site settings page render together.
SITE_TEMPLATES = ("site_config.html", "site_base.html", "site_overview.html", "site_player.html",
                  "site_players.html", "site_leaderboard.html", "site_me.html", "_site_hero.html",
                  "site_preview.html")

#: the names that TRANSLATE their argument: ``_`` (Jinja's i18n extension) and ``gettext``.
TRANSLATION_CALLS = ("_", "gettext")


# ------------------------------------------------------------------- the checker

def site_environment():
    """The console's own environment (``jinja2.ext.i18n``), plus ``do`` so a page template parses.

    ``build_environment`` is the ONE builder the application uses, so this parses a template with
    exactly the grammar a request renders it with — a construct the application accepts cannot fail
    here for a reason of its own.
    """
    environment = templating.build_environment()
    environment.add_extension("jinja2.ext.do")
    return environment


def label_references(environment, source: str, name: str = "<snippet>") -> list[tuple[str, bool]]:
    """Every ``labels.<name>`` in *source*, as ``(name, inside_a_translation_call)``.

    Walks the parse tree itself: a Jinja node carries no parent link, so the walker passes the
    "enclosed" flag DOWN. A reference is enclosed only when it is an argument of ``_()``/``gettext``
    — anything else (a bare print, a conditional, a filter, a concatenation) is what the page must
    never do.
    """
    found: list[tuple[str, bool]] = []

    def walk(node: nodes.Node, enclosed: bool) -> None:
        if isinstance(node, nodes.Call) and isinstance(node.node, nodes.Name) \
                and node.node.name in TRANSLATION_CALLS:
            for argument in node.args:
                walk(argument, True)
            for keyword in node.kwargs:
                walk(keyword, True)
            return
        if isinstance(node, nodes.Getattr) and isinstance(node.node, nodes.Name) \
                and node.node.name == "labels":
            found.append((node.attr, enclosed))
            return
        for child in node.iter_child_nodes():
            walk(child, enclosed)

    for node in environment.parse(source, name=name).body:
        walk(node, False)
    return found


def unwrapped(environment, source: str, name: str = "<snippet>") -> list[str]:
    """The ``labels.<name>`` references in *source* that no translation call encloses."""
    return [attr for attr, enclosed in label_references(environment, source, name) if not enclosed]


# ------------------------------------------------------------------- the positive control

def test_the_checker_flags_an_unwrapped_reference():
    """The control: an unwrapped reference must be reported, and a wrapped one must not.

    Without this, a checker that found nothing (a typo in the attribute name, an empty template
    list) would pass every pin in this file for the wrong reason.
    """
    environment = site_environment()
    source = (
        '<h3>{{ labels.palette }}</h3>'
        '<p title="{{ _(labels.panel) }}">{{ _(labels.check) }}</p>'
        '<span>{{ labels.on if enabled else labels.off }}</span>'
        '<i aria-label="{{ _(labels.remove) }}"></i>'
    )
    assert set(unwrapped(environment, source, "control.html")) == {"palette", "on", "off"}
    enclosed = {attr for attr, inside in label_references(environment, source, "control.html")
                if inside}
    assert enclosed == {"panel", "check", "remove"}


def test_the_checker_reads_a_conditional_and_a_filter_too():
    """A reference inside a larger expression is not enclosed, however it is spelled."""
    environment = site_environment()
    source = ('<b>{{ labels.palette_rule_b | upper }}</b>'
              '<i>{{ labels.address ~ "/" }}</i>'
              '<s>{{ labels.links if x }}</s>')
    assert len(unwrapped(environment, source, "control2.html")) == 3


# ------------------------------------------------------------------- the pin

def test_every_site_template_wraps_every_label_reference():
    """No site template prints a ``labels`` value directly — in text or in an attribute."""
    environment = site_environment()
    offenders: list[str] = []
    references = 0
    textual = 0
    for name in SITE_TEMPLATES:
        path = TEMPLATES / name
        assert path.is_file(), f"missing site template: {path}"
        source = path.read_text("utf-8")
        references += len(label_references(environment, source, name))
        textual += len(re.findall(r"labels\.[A-Za-z_][A-Za-z0-9_]*", source))
        offenders += [f"{name}: labels.{attr}"
                      for attr in unwrapped(environment, source, name)]
    assert not offenders, "these references print the msgid, so the chrome stays English: " + \
        ", ".join(offenders)
    # The walker must see every reference the TEXT carries: a template whose labels vanished, or a
    # walker that stopped descending into an expression, cannot pass for the right reason. This is
    # the floor's job, and it costs no number to keep in step with the templates.
    assert references == textual, (
        f"the parse tree carries {references} label references, the templates carry {textual}")
    assert references > 0, "the site templates reference no label at all"


# ------------------------------------------------------------------- the German page
#
# The page draws its branches by STATE — the Admin's card and a group admin's, a site switched on
# and one switched off, a stored sheet and none, a file the console cannot read — so each state is
# rendered and every label is asserted against the union of what the console really wrote. A label
# no state draws would be chrome nothing serves, and the union makes that a failure, not a skip.

def _sheet_block(indent: str = "        ") -> str:
    """The shipped sheet as a YAML block scalar, indented under ``theme.expert: |``."""
    return "".join(f"{indent}{line}\n" for line in SHEET_TEXT.splitlines())


def _sites_yaml() -> str:
    """Four sites covering the card's states: on and off, with a sheet and without, in scope."""
    return ("sites:\n"
            "  root:\n"
            '    slug: ""\n'
            "    branding:\n"
            "      title: Community\n"
            "    theme:\n"
            "      expert: |\n" + _sheet_block() +
            "  root-off:\n"
            "    slug: root-off\n"
            '    group_role: "Other Token"\n'
            "    enabled: false\n"
            "    theme:\n"
            "      expert: |\n" + _sheet_block() +
            "  guards:\n"
            '    group_role: "5th Guards"\n'
            "    slug: guards\n"
            "  guards-off:\n"
            '    group_role: "5th Guards"\n'
            "    slug: guards-off\n"
            "    enabled: false\n")


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (tmp_path / "site.yaml").write_text(_sites_yaml(), encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(config_dir):
    return build(config_dir, cluster(), users=_users())


@pytest.fixture
def unreadable_app(tmp_path):
    """An application whose ``site.yaml`` is corrupted AFTER the install, so the page draws its
    error notice (the console refuses to install a site file it cannot read, so the file must go
    bad between the install and the request — which is exactly the operator's case)."""
    root = tmp_path / "unreadable"
    (root / "services").mkdir(parents=True)
    (root / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (root / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (root / "site.yaml").write_text(_sites_yaml(), encoding="utf-8")
    application = build(root, cluster(), users=_users())
    (root / "site.yaml").write_text("sites: [\n", encoding="utf-8")
    return application


@pytest.fixture
def pending_app(app, config_dir):
    """An application whose file gained a site AFTER the install, so that card draws the pending box.

    Routes are registered at startup, so this is the state the console is really in right after an
    "Add a site" save: the card exists, and the box beside it says the address answers on the next
    restart instead of mounting a frame. It is the only state that draws that sentence.
    """
    with open(config_dir / "site.yaml", "a", encoding="utf-8") as handle:
        handle.write('  later:\n    group_role: "5th Guards"\n    slug: later\n')
    return app


async def _german_page(application, username: str) -> str:
    """One GET of the page in German as *username*."""
    async with client(application) as http:
        await sign_in(http, username)
        http.cookies.set(i18n.COOKIE_NAME, "de")
        response = await http.get(page_path())
        assert response.status_code == 200, \
            f"the page answered {response.status_code} for {username}: {response.text[:400]}"
        return response.text


def _used_label_keys() -> list[str]:
    source = (TEMPLATES / "site_config.html").read_text("utf-8")
    return sorted(set(re.findall(r"labels\.([A-Za-z_][A-Za-z0-9_]*)", source)))


@pytest.mark.asyncio
async def test_a_german_request_carries_the_catalog_translation_of_every_label(
        app, unreadable_app, pending_app, monkeypatch):
    """Each label the page prints comes out in German: the catalog's value, asserted one by one.

    The page context is captured from the real render (the module builds the ``labels`` dict from
    its marked literals), so the assertion pairs the SAME msgid the page handed the template with
    the catalog's answer for it — nothing here restates a msgid by hand.
    """
    captured: dict = {}
    real_render = i18n.render

    def spy(request, name, **context):
        if "labels" in context and "cards" in context:
            captured.update(context)
        return real_render(request, name, **context)

    monkeypatch.setattr(i18n, "render", spy)

    documents = [await _german_page(app, ADMIN), await _german_page(app, GROUP_ADMIN),
                 await _german_page(unreadable_app, ADMIN), await _german_page(pending_app, ADMIN)]
    html = "\n".join(documents)

    labels = captured["labels"]
    catalog = i18n.translations_for("de")
    keys = _used_label_keys()
    assert keys, "the page template references no label at all"

    missing: list[str] = []
    translated = 0
    for key in keys:
        assert key in labels, f"the template prints labels.{key}, which the page does not provide"
        msgid = labels[key]
        expected = catalog.gettext(msgid)
        if expected != msgid:
            translated += 1
        if str(escape(expected)) not in html:
            missing.append(f"{key}: {expected!r} ({msgid!r})")
    assert not missing, "these labels never reached the German page: " + "; ".join(missing)
    # the run must be evidence: the page's labels are real catalog answers, not msgid fallbacks
    # (measured: 68 of the 72 keys answer with a German msgstr; the rest are identity translations
    # such as "Palette", or values the page module already translated for the request)
    assert translated >= 60, f"only {translated} of {len(keys)} labels had a German msgstr"
