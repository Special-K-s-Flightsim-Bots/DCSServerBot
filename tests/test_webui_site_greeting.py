"""The overview's hero greeting: the community's name and the one sentence around it.

WHAT THIS SUITE PINS
--------------------
* :func:`services.webservice.site_pages.community_name` — the FOUR sources in their precedence, each
  driven by a real stub (a declared ``branding.community_name``, a bot whose first guild carries a
  name, ``main.yaml``'s ``guild_name``, then ``branding.title``), and the guarded failures that must
  fall through instead of raising: a registry that refuses, an empty guild list, a blank guild name,
  a config value that is not a string;
* the rendered hero: the greeting and the name, never the old slogan, and an eyebrow that states the
  site's own kind rather than repeating "Welcome";
* the catalogue's shape: the greeting is ONE msgid with the name as its placeholder;
* a 40-character community name at 430px, measured in a real browser: the headline wraps and the
  document does not scroll sideways.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from conftest import asgi_client, create_app, install_shell, link_or_copy
from services.webservice import i18n, mount, site_pages, sites

REPO = Path(__file__).resolve().parent.parent
SITE_PROBE = REPO / "tests" / "site_visual_probe.py"

#: a community name of exactly forty characters — long enough to prove the headline wraps.
LONG_NAME = "5th Guards Community Air Wing Number One"

EYEBROW = re.compile(r'<div class="eyebrow">(.*?)</div>', re.S)


# ------------------------------------------------------------------------------ fixtures

@pytest.fixture(autouse=True)
def restore_console_mount():
    """``mount``'s prefix is process state; a site install must not leak into the next test."""
    saved = mount._PREFIX
    try:
        yield
    finally:
        mount._PREFIX = saved


@pytest.fixture(autouse=True)
def restore_bot_service():
    """``ServiceRegistry``'s singletons are process state: put back what was there."""
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService
    from services.servicebus import ServiceBus

    previous = {key: ServiceRegistry._singletons.get(key) for key in (BotService, ServiceBus)}
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                ServiceRegistry._singletons.pop(key, None)
            else:
                ServiceRegistry._singletons[key] = value


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "servers.yaml").write_text("Root-1:\n  port: 10308\n", encoding="utf-8")
    return tmp_path


def install_bot(*guild_names) -> None:
    """Install a stub ``BotService`` whose bot carries one guild per name."""
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(
        bot=types.SimpleNamespace(guilds=[types.SimpleNamespace(name=name)
                                          for name in guild_names]))


def install_bus(*server_names) -> None:
    """Install a stub ``ServiceBus`` carrying *server_names* and no database pool."""
    from core.services.registry import ServiceRegistry
    from core.utils.helper import ThreadSafeDict
    from services.servicebus import ServiceBus

    servers = ThreadSafeDict({name: types.SimpleNamespace(name=name, locals={}, status=None,
                                                         players={})
                              for name in server_names})
    ServiceRegistry._singletons[ServiceBus] = types.SimpleNamespace(apool=None, servers=servers)


def context(**branding) -> sites.SiteContext:
    """A resolved root-site context carrying *branding* (``title``, ``community_name``, …)."""
    site = sites.Site(key="root", slug="", group_role=None, branding=sites.Branding(**branding))
    return site.context({})


def site_config(**branding):
    return {"sites": {"root": {"slug": "", "branding": branding}}}


def hero(markup: str) -> str:
    """The rendered hero section, without the rest of the page."""
    return markup.split('class="hero"', 1)[1].split("</section>", 1)[0]


def render(config_dir, config=None) -> str:
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config=config or site_config(title="Root Site"))

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/")).text

    return asyncio.run(run())


# ------------------------------------------------------------------------------ the precedence

def test_the_declared_community_name_wins(config_dir):
    """Step 1: ``branding.community_name``, even with a guild and a ``main.yaml`` name present."""
    install_bot("Discord Guild Name")
    (config_dir / "main.yaml").write_text("guild_name: Config Guild Name\n", encoding="utf-8")
    resolved = context(title="Root Site", community_name="  Tactical Air Wing  ")
    assert site_pages.community_name(resolved, config_dir=config_dir) == "Tactical Air Wing"


def test_the_connected_guild_names_the_community_with_no_declared_name(config_dir):
    """Step 2: the bot's first guild, when the site declares no name of its own."""
    install_bot("  5th Guards Air Wing  ", "Another Guild")
    (config_dir / "main.yaml").write_text("guild_name: Config Guild Name\n", encoding="utf-8")
    assert site_pages.community_name(context(title="Root Site"),
                                     config_dir=config_dir) == "5th Guards Air Wing"


def test_main_yamls_guild_name_answers_with_no_guild(config_dir):
    """Step 3: ``main.yaml``'s ``guild_name`` — the no-Discord setup's own key."""
    (config_dir / "main.yaml").write_text("guild_id: 123\nguild_name: Config Guild Name\n",
                                          encoding="utf-8")
    assert site_pages.community_name(context(title="Root Site"),
                                     config_dir=config_dir) == "Config Guild Name"


def test_the_brand_title_is_the_last_resort(config_dir):
    """Step 4: a site always greets with something — its own title."""
    assert site_pages.community_name(context(title="Root Site"),
                                     config_dir=config_dir) == "Root Site"
    # and with no title declared at all, the shipped default
    assert site_pages.community_name(context()) == sites.DEFAULT_BRANDING_TITLE


def test_a_registry_that_raises_falls_through(config_dir, monkeypatch):
    """A broken service bus is "no name", never a failed render."""
    from core.services.registry import ServiceRegistry

    def refuse(_service):
        raise RuntimeError("the registry is down")

    monkeypatch.setattr(ServiceRegistry, "get", staticmethod(refuse))
    (config_dir / "main.yaml").write_text("guild_name: Config Guild Name\n", encoding="utf-8")
    assert site_pages.community_name(context(title="Root Site"),
                                     config_dir=config_dir) == "Config Guild Name"


def test_an_empty_guild_list_falls_through(config_dir):
    install_bot()
    (config_dir / "main.yaml").write_text("guild_name: Config Guild Name\n", encoding="utf-8")
    assert site_pages.community_name(context(title="Root Site"),
                                     config_dir=config_dir) == "Config Guild Name"


@pytest.mark.parametrize("name", ["", "   ", None])
def test_a_blank_guild_name_falls_through(config_dir, name):
    install_bot(name)
    (config_dir / "main.yaml").write_text("guild_name: Config Guild Name\n", encoding="utf-8")
    assert site_pages.community_name(context(title="Root Site"),
                                     config_dir=config_dir) == "Config Guild Name"


@pytest.mark.parametrize("value", [123, ["a"], {"name": "x"}, True])
def test_a_config_value_that_is_not_a_string_falls_through(config_dir, value):
    """A malformed ``guild_name`` neither raises nor wins: the title answers instead."""
    (config_dir / "main.yaml").write_text(f"guild_name: {json.dumps(value)}\n", encoding="utf-8")
    assert site_pages.community_name(context(title="Root Site"),
                                     config_dir=config_dir) == "Root Site"


def test_an_unreadable_main_yaml_and_a_missing_config_dir_fall_through(config_dir, tmp_path):
    (config_dir / "main.yaml").write_text("guild_name: [unclosed\n", encoding="utf-8")
    assert site_pages.community_name(context(title="Root Site"),
                                     config_dir=config_dir) == "Root Site"
    missing = tmp_path / "nowhere"
    assert site_pages.community_name(context(title="Root Site"), config_dir=missing) == "Root Site"
    assert site_pages.community_name(context(title="Root Site")) == "Root Site"


# ------------------------------------------------------------------------------ the greeting

def test_the_greeting_is_one_msgid_with_the_name_as_its_placeholder():
    assert site_pages.GREETING == "Welcome to %(community)s"


def test_the_sentence_is_split_around_the_placeholder(monkeypatch):
    """The locale owns the sentence: the name is wherever its placeholder sits."""
    monkeypatch.setattr(i18n, "translate", lambda request, message, **params: (
        f"{params['community']} — willkommen bei uns"))
    greeting = site_pages.hero_greeting(None, "5th Guards")
    assert greeting == {"lead": "", "name": "5th Guards", "tail": "— willkommen bei uns"}


def test_a_catalog_that_drops_the_placeholder_still_prints_the_name(monkeypatch):
    monkeypatch.setattr(i18n, "translate", lambda request, message, **params: "Welcome")
    assert site_pages.hero_greeting(None, "5th Guards") == {
        "lead": "", "name": "5th Guards", "tail": ""}


# ------------------------------------------------------------------------------ the declared key

def test_the_parser_accepts_and_trims_a_community_name():
    catalog = sites.parse_sites({"root": {"slug": "",
                                         "branding": {"title": "T",
                                                      "community_name": "  5th Guards  "}}})
    assert catalog.root.branding.community_name == "5th Guards"


@pytest.mark.parametrize("value", ["<b>Guards</b>", "two\nlines", "x" * 61])
def test_the_parser_refuses_a_community_name_that_breaks_the_one_line_rule(value):
    with pytest.raises(sites.SiteConfigError):
        sites.parse_sites({"root": {"slug": "", "branding": {"community_name": value}}})


def test_a_non_string_community_name_is_refused():
    with pytest.raises(sites.SiteConfigError):
        sites.parse_sites({"root": {"slug": "", "branding": {"community_name": 5}}})


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_community_name_reads_as_not_declared(value):
    catalog = sites.parse_sites({"root": {"slug": "", "branding": {"community_name": value}}})
    assert catalog.root.branding.community_name is None


def test_the_schema_accepts_a_community_name(tmp_path):
    from pykwalify.core import Core

    schema = REPO / "services" / "webservice" / "schemas" / "webservice_schema.yaml"
    extension = tmp_path / "ext.py"
    extension.write_text("def unique_port(value, rule_obj, path):\n    return True\n",
                         encoding="utf-8")
    source = tmp_path / "webservice.yaml"
    source.write_text("""
DEFAULT:
  frontend: true
  sites:
    root:
      slug: ""
      branding: {title: Community, community_name: 5th Guards}
""", encoding="utf-8")
    Core(source_file=str(source), schema_files=[str(schema)],
         extensions=[str(extension)]).validate()


# ------------------------------------------------------------------------------ the rendered hero

def test_the_hero_greets_with_the_declared_name(config_dir):
    markup = render(config_dir, site_config(title="Root Site", community_name="Tactical Air Wing"))
    section = hero(markup)
    assert "Welcome to" in section
    assert "<em>Tactical Air Wing</em>" in section
    assert "Fly. Fight." not in markup and "Get counted." not in markup


def test_the_hero_greets_with_the_guild_name_when_none_is_declared(config_dir):
    install_bot("5th Guards")
    section = hero(render(config_dir))
    assert "Welcome to" in section and "<em>5th Guards</em>" in section


def test_the_hero_greets_with_main_yamls_name_on_a_no_discord_install(config_dir):
    (config_dir / "main.yaml").write_text("guild_id: 123\nguild_name: Config Guild Name\n",
                                          encoding="utf-8")
    section = hero(render(config_dir))
    assert "Welcome to" in section and "<em>Config Guild Name</em>" in section


def test_the_hero_falls_back_to_the_brand_title(config_dir):
    section = hero(render(config_dir))
    assert "Welcome to" in section and "<em>Root Site</em>" in section


def test_the_eyebrow_states_the_kinds_of_record_and_never_welcome(config_dir):
    markup = render(config_dir, site_config(title="Root Site", community_name="Tactical Air Wing"))
    label = EYEBROW.search(hero(markup))
    assert label is not None, "the hero has no eyebrow"
    assert label.group(1).strip() == "Community record"
    assert "Welcome" not in label.group(1)


def test_the_description_line_is_unchanged(config_dir):
    markup = render(config_dir, site_config(title="Root Site", community_name="Tactical Air Wing"))
    assert ("Our servers, our pilots, our missions. Every sortie flown here is written down"
            in hero(markup))


# ------------------------------------------------------- a long name at 430px, in a real browser

def test_a_forty_character_name_wraps_without_a_horizontal_scroll(config_dir, tmp_path):
    assert len(LONG_NAME) == 40, "the name under test is no longer forty characters"
    install_bus("Root-1", "Root-2")
    markup = render(config_dir, site_config(title="Root Site", community_name=LONG_NAME))
    directory = tmp_path / "rendered"
    directory.mkdir()
    (directory / "overview.html").write_text(markup, encoding="utf-8")
    link_or_copy(REPO / "services" / "webservice" / "static", directory / "static")

    finished = subprocess.run([sys.executable, str(SITE_PROBE), str(directory),
                               str(tmp_path / "shots")],
                              capture_output=True, text=True, encoding="utf-8", timeout=180, check=True)
    payload = json.loads(finished.stdout.strip().splitlines()[-1])
    if isinstance(payload, dict) and payload.get("unavailable"):
        pytest.skip(payload["unavailable"])
    phone = next(row for row in payload if row["width"] == 430)
    assert phone["scrollWidth"] == phone["clientWidth"], \
        f"horizontal scroll at 430px: {phone['scrollWidth']} > {phone['clientWidth']}"
    assert phone["hero"] >= 34, f"the phone headline is {phone['hero']}px, below the readable step"
    # the headline itself stays inside the page, and the long name wraps over several lines
    assert phone["heroRight"] <= phone["clientWidth"], phone
    assert phone["heroHeight"] > phone["hero"] * 2.5, \
        f"the headline did not wrap: {phone['heroHeight']}px at {phone['hero']}px"
