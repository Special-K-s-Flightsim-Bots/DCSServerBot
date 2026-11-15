"""The site configuration and the resolved site context: ``managed_by`` membership, one resolver.

A SITE is a view of a subset of the cluster's servers, and ``servers.yaml``'s ``managed_by`` is the
only authority for which servers those are — this suite pins that, the four refusal classes that
keep a typo from serving an empty page, and the one resolver every page will consume.

The membership is read from the RUNNING process's registry (a ``ThreadSafeDict``, whose ``__iter__``
yields ``(name, server)`` PAIRS), so the pins hand the shipped code that container and assert its
type: a suite that used a plain dict of hashable doubles would stay green while production produced
a tuple per server.
"""
from __future__ import annotations

import logging
import types
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from core.utils.helper import ThreadSafeDict
from services.webservice import site_reads, sites
from services.webservice.registry import RESERVED_PATHS, RESERVED_PREFIXES
from services.webservice.sites import (
    DEFAULT_BRANDING_TITLE,
    DEFAULT_LANG,
    DEFAULT_NAV,
    DEFAULT_TILES,
    SiteCatalog,
    SiteConfigError,
    SiteNotFound,
    first_segment,
    load_servers_config,
    load_sites,
    managed_by_values,
    parse_sites,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "services" / "webservice" / "schemas" / "webservice_schema.yaml"


# --------------------------------------------------------------------------- doubles


def _server(name: str, managed_by=None):
    """A server as the site code reads it: a ``name`` and the config block in ``locals``."""
    locals_ = {} if managed_by is None else {"managed_by": managed_by}
    return types.SimpleNamespace(name=name, locals=locals_)


def _registry(*servers) -> ThreadSafeDict:
    """The REAL container the live path hands the resolver (``ServiceBus.servers``)."""
    return ThreadSafeDict({server.name: server for server in servers})


def _servers_yaml(directory: Path, body: str) -> Path:
    path = directory / "servers.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- acceptance 1

def test_a_valid_block_parses_and_the_root_site_resolves_the_untagged_servers(tmp_path):
    _servers_yaml(tmp_path, """
Server 1:
  port: 10308
Server 2:
  managed_by:
    - 5th Guards
""")
    config = {"sites": {
        "root": {"slug": ""},
        "group1": {"slug": "group1", "group_role": "5th Guards"},
    }}

    catalog = load_sites(config, config_dir=tmp_path)

    assert isinstance(catalog, SiteCatalog)
    assert catalog.slugs == ("", "group1"), "the declaration order is kept"
    root = catalog.root
    assert root is not None
    assert root.is_root
    group = catalog.site_for_slug("group1")
    assert group is not None
    assert root.prefix == "" and group.prefix == "/group1"

    # every key is optional, and the defaults are the ones the sample states
    assert root.nav == DEFAULT_NAV == ("overview", "leaderboard", "squadrons", "players")
    assert root.tiles == DEFAULT_TILES == ("active_week", "online_now", "playtime_total",
                                           "avg_session", "peak_hour")
    assert root.lang == DEFAULT_LANG == "en"
    assert root.branding.title == DEFAULT_BRANDING_TITLE
    assert root.branding.logo is None
    assert not hasattr(root.branding, "copyright"), "the footer's line is not a site value"
    assert root.theme.preset is None and root.theme.expert is None and root.theme.light is False

    registry = _registry(_server("Server 1"), _server("Server 2", ["5th Guards"]))
    context = catalog.resolve(None, servers=registry)
    assert context.servers == frozenset({"Server 1"}), "the root site shows the untagged servers"
    assert context.is_root is True and context.slug == "" and context.prefix == ""
    assert context.nav == DEFAULT_NAV and context.tiles == DEFAULT_TILES

    group = catalog.resolve("group1", servers=registry)
    assert group.servers == frozenset({"Server 2"})
    assert group.is_root is False and group.name == "group1" and group.prefix == "/group1"


def test_a_theme_stylesheet_is_refused_naming_the_key(tmp_path):
    """The retired key is refused by the parser, and the refusal names it — never silently ignored,
    and never migrated."""
    sheet = tmp_path / "site" / "theme.css"
    sheet.parent.mkdir()
    sheet.write_text(":root { --accent: #e8b04b; }\n", encoding="utf-8")

    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"theme": {"stylesheet": "site/theme.css", "light": True}}})

    message = str(refusal.value)
    assert "sites.root.theme.stylesheet" in message, message
    assert "unknown key" in message, message
    assert sites.THEME_KEYS == ("preset", "sheet", "expert", "light"), sites.THEME_KEYS


def test_membership_comes_from_the_live_registry_not_from_servers_yaml(tmp_path):
    """The config is read for the TOKEN SET only; what a request shows is the registry."""
    _servers_yaml(tmp_path, """
Server 1:
  managed_by:
    - 5th Guards
""")
    catalog = load_sites({"sites": {"root": {}, "group1": {"group_role": "5th Guards"}}},
                         config_dir=tmp_path)
    # the file says Server 1 belongs to group1; the RUNNING process has a different set entirely
    registry = _registry(_server("Live Only", ["5th Guards"]), _server("Untagged"))

    assert catalog.resolve("group1", servers=registry).servers == frozenset({"Live Only"})
    assert catalog.resolve(None, servers=registry).servers == frozenset({"Untagged"})


# --------------------------------------------------------------------------- acceptance 2

def test_a_scalar_managed_by_is_coerced_to_one_token_and_the_warning_names_the_server(tmp_path,
                                                                                     caplog):
    sites._WARNED_SCALARS.clear()          # process state: this pin asserts the one warning
    _servers_yaml(tmp_path, """
Server 7:
  managed_by: 5th Guards
""")

    with caplog.at_level(logging.WARNING, logger="services.webservice.sites"):
        declared = load_servers_config(tmp_path)

    assert declared["Server 7"]["managed_by"] == ["5th Guards"], "a scalar is a ONE-element list"
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1, f"exactly one warning, got {[r.getMessage() for r in warnings]}"
    assert "Server 7" in warnings[0].getMessage(), "the warning names the server"

    # the coercion is what makes the token match at all: read as characters it names no role
    assert managed_by_values("5th Guards") == ("5th Guards",)
    assert "5th Guards" not in tuple("5th Guards"), "the trap this coercion closes"

    catalog = parse_sites({"group1": {"group_role": "5th Guards"}},
                          known_roles=sites.known_managed_by(declared))
    site = catalog.site_for_slug("group1")
    assert site is not None and site.group_role == "5th Guards"
    assert catalog.resolve("group1", servers=_registry(
        _server("Server 7", declared["Server 7"]["managed_by"]))).servers == frozenset({"Server 7"})


def test_a_scalar_managed_by_never_fails_the_start(tmp_path):
    _servers_yaml(tmp_path, """
Server 8:
  managed_by: 5th Guards
""")
    catalog = load_sites({"sites": {"group1": {"group_role": "5th Guards"}}}, config_dir=tmp_path)
    assert catalog.site_for_slug("group1").group_role == "5th Guards"


def test_a_registry_value_written_as_a_scalar_is_read_as_one_token():
    """The registry can carry the scalar too (it is loaded from the same file): same reading."""
    catalog = parse_sites({"group1": {"group_role": "5th Guards"}}, known_roles=["5th Guards"])
    registry = _registry(_server("Server 9", "5th Guards"))
    assert catalog.resolve("group1", servers=registry).servers == frozenset({"Server 9"})


# --------------------------------------------------------------------------- acceptance 3

def test_a_server_naming_two_tokens_belongs_to_exactly_those_two_sites():
    catalog = parse_sites({
        "root": {},
        "fifth": {"group_role": "5th Guards"},
        "hunters": {"group_role": "Hunters"},
        "other": {"group_role": "Nobody's Group"},
    }, known_roles=["5th Guards", "Hunters", "Nobody's Group"])
    registry = _registry(
        _server("Shared", ["5th Guards", "Hunters"]),
        _server("Only Fifth", ["5th Guards"]),
        _server("Untagged"),
    )

    members = {site.key: catalog.resolve(site.slug, servers=registry).servers
               for site in catalog.sites}

    assert members["fifth"] == frozenset({"Shared", "Only Fifth"})
    assert members["hunters"] == frozenset({"Shared"})
    assert members["other"] == frozenset()
    assert members["root"] == frozenset({"Untagged"})
    assert "Shared" in members["fifth"] and "Shared" in members["hunters"]
    assert all("Shared" not in members[key] for key in ("root", "other"))


# --------------------------------------------------------------------------- acceptance 4

def test_an_unknown_tile_id_is_refused_naming_the_key():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"tiles": ["online_now", "active_yesterday"]}})
    assert "sites.root.tiles" in str(refusal.value)
    assert "active_yesterday" in str(refusal.value)


def test_an_unknown_nav_page_id_is_refused_naming_the_key():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"nav": ["overview", "leaderbord"]}})
    assert "sites.root.nav" in str(refusal.value)
    assert "leaderbord" in str(refusal.value)


def test_a_nav_or_tiles_scalar_is_refused_rather_than_iterated_as_characters():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"nav": "overview"}})
    assert "sites.root.nav" in str(refusal.value)
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"tiles": "online_now"}})
    assert "sites.root.tiles" in str(refusal.value)


def test_a_theme_stylesheet_names_the_key_and_the_accepted_keys(tmp_path):
    """A card that still carries the retired key is refused whether or not the file it names exists:
    the refusal lists the keys a ``theme:`` block accepts."""
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"theme": {"stylesheet": "site/missing.css"}}})
    message = str(refusal.value)
    assert "sites.root.theme.stylesheet" in message, message
    assert "preset, sheet, expert, light" in message, message
    assert str(tmp_path) not in message, "the retired key is not resolved against the config directory"


def test_an_unknown_key_is_refused_naming_the_key():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"branding": {"colour": "#fff"}}})
    assert "sites.root.branding.colour" in str(refusal.value)

    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"brand": {"title": "x"}}})
    assert "sites.root.brand" in str(refusal.value)

    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"theme": {"colours": {}}}})
    assert "sites.root.theme.colours" in str(refusal.value)


def test_a_duplicate_slug_is_refused_naming_the_key():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"fifth": {"slug": "shared", "group_role": "A"},
                     "hunters": {"slug": "shared", "group_role": "B"}},
                    known_roles=["A", "B"])
    assert "sites.hunters.slug" in str(refusal.value)
    assert "shared" in str(refusal.value)


def test_a_slug_that_is_not_url_safe_is_refused_naming_the_key():
    for slug in ("5th Guards", "Group1", "group_1", "-group1", "group/1"):
        with pytest.raises(SiteConfigError) as refusal:
            parse_sites({"group1": {"slug": slug, "group_role": "A"}}, known_roles=["A"])
        assert "sites.group1.slug" in str(refusal.value), slug


def test_a_slug_that_collides_with_a_reserved_shell_path_is_refused_naming_the_key():
    reserved = [part.strip("/") for part in (RESERVED_PATHS + RESERVED_PREFIXES) if part.strip("/")]
    assert reserved, "the shell declares reserved paths — the collision check would be vacuous"
    for slug in reserved:
        with pytest.raises(SiteConfigError) as refusal:
            parse_sites({"group1": {"slug": slug, "group_role": "A"}}, known_roles=["A"])
        assert "sites.group1.slug" in str(refusal.value)
        assert slug in str(refusal.value)


def test_a_group_role_that_matches_no_managed_by_is_refused_naming_the_key(tmp_path):
    _servers_yaml(tmp_path, """
Server 1:
  managed_by:
    - 5th Guards
""")
    with pytest.raises(SiteConfigError) as refusal:
        load_sites({"sites": {"group1": {"group_role": "5th Gaurds"}}}, config_dir=tmp_path)
    assert "sites.group1.group_role" in str(refusal.value)
    assert "5th Gaurds" in str(refusal.value)


def test_a_group_role_may_be_a_role_id_written_as_a_number(tmp_path):
    _servers_yaml(tmp_path, """
Server 1:
  managed_by:
    - 123456789
""")
    catalog = load_sites({"sites": {"group1": {"group_role": 123456789}}}, config_dir=tmp_path)
    site = catalog.site_for_slug("group1")
    assert site is not None and site.group_role == "123456789"
    registry = _registry(_server("Server 1", [123456789]))
    assert catalog.resolve("group1", servers=registry).servers == frozenset({"Server 1"})


def test_the_root_site_must_not_carry_a_group_role():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"group_role": "5th Guards"}}, known_roles=["5th Guards"])
    assert "sites.root.group_role" in str(refusal.value)


def test_a_site_other_than_root_needs_a_group_role():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"group1": {"slug": "group1"}})
    assert "sites.group1.group_role" in str(refusal.value)


def test_the_root_site_must_have_an_empty_slug():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"slug": "root"}})
    assert "sites.root.slug" in str(refusal.value)


def test_a_language_with_no_catalog_is_refused_naming_the_key():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"lang": "zz"}})
    assert "sites.root.lang" in str(refusal.value)


# --------------------------------------------------------------------------- acceptance 5

def test_an_unknown_first_segment_resolves_to_not_found_never_the_root_site():
    catalog = parse_sites({"root": {}, "group1": {"group_role": "A"}}, known_roles=["A"])
    registry = _registry(_server("Untagged"))

    with pytest.raises(SiteNotFound) as refusal:
        catalog.resolve("grup1", servers=registry)
    assert "grup1" in str(refusal.value)

    assert catalog.resolve("", servers=registry).is_root is True
    assert catalog.resolve(None, servers=registry).is_root is True


def test_first_segment_reads_the_leading_path_segment():
    assert first_segment("/") == ""
    assert first_segment("") == ""
    assert first_segment(None) == ""
    assert first_segment("/group1") == "group1"
    assert first_segment("/group1/rankings") == "group1"
    assert first_segment("/group1/") == "group1"


def test_a_block_with_no_sites_resolves_nothing():
    catalog = load_sites({}, config_dir=None)
    assert not catalog and catalog.sites == () and catalog.root is None
    with pytest.raises(SiteNotFound):
        catalog.resolve("", servers=_registry())
    with pytest.raises(SiteNotFound):
        catalog.resolve("group1", servers=_registry())


# ------------------------------------------------------------- the live registry, not a dict

def test_membership_reads_the_live_registry_through_items():
    """``ThreadSafeDict.__iter__`` yields PAIRS: a ``set(servers)``/bare loop produces tuples."""
    catalog = parse_sites({"root": {}, "group1": {"group_role": "A"}}, known_roles=["A"])
    registry = _registry(_server("First", ["A"]), _server("Second"))
    assert isinstance(registry, ThreadSafeDict), "the pin is not using the real mapping"

    assert catalog.resolve("group1", servers=registry).servers == frozenset({"First"})
    assert catalog.resolve("", servers=registry).servers == frozenset({"Second"})


def test_a_plain_mapping_and_a_sequence_of_servers_resolve_the_same_way():
    catalog = parse_sites({"group1": {"group_role": "A"}}, known_roles=["A"])
    mapping = {"First": _server("First", ["A"])}
    sequence = [_server("First", ["A"])]
    assert catalog.resolve("group1", servers=mapping).servers == frozenset({"First"})
    assert catalog.resolve("group1", servers=sequence).servers == frozenset({"First"})


def test_a_server_with_no_locals_declares_no_token():
    catalog = parse_sites({"root": {}, "group1": {"group_role": "A"}}, known_roles=["A"])
    bare = types.SimpleNamespace(name="Bare")
    assert catalog.resolve("", servers=_registry(bare)).servers == frozenset({"Bare"})
    assert catalog.resolve("group1", servers=_registry(bare)).servers == frozenset()


# ------------------------------------------------------------------------------ the value

def test_the_resolved_context_is_immutable():
    catalog = parse_sites({"root": {}})
    context = catalog.resolve("", servers=_registry(_server("S")))
    with pytest.raises(FrozenInstanceError):
        context.slug = "other"                      # type: ignore[misc]  # frozen dataclass
    with pytest.raises(AttributeError):
        context.servers.add("Other")                # type: ignore[attr-defined]  # frozenset


def test_a_presentation_value_is_taken_from_the_block():
    catalog = parse_sites({"group1": {
        "group_role": "A",
        "branding": {"title": "5th Guards", "logo": "config/site/crest.png",
                     "copyright": "Amber Wing"},
        "theme": {"light": True},
        "nav": ["overview", "players"],
        "tiles": ["online_now"],
        "lang": "de",
    }}, known_roles=["A"])
    context = catalog.resolve("group1", servers=_registry())
    assert context.branding.title == "5th Guards"
    assert context.branding.logo == "config/site/crest.png"
    assert context.theme.light is True
    assert context.nav == ("overview", "players")
    assert context.tiles == ("online_now",)
    assert context.lang == "de"


# ------------------------------------------------------- the registry read (the ServiceBus)

def _patch_get(monkeypatch, outcome):
    """Make ``ServiceRegistry.get`` raise *outcome* when it is an exception, else return it.

    The registry lookup is the only process seam this module has, and the real one returns ``None``
    in a process that registered no bus, so the pins drive both shapes.
    """
    from core.services.registry import ServiceRegistry

    def fake_get(cls, wanted):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(sites, "_NO_BUS_WARNED", False,
                        raising=False)   # process state: one warning per process
    monkeypatch.setattr(ServiceRegistry, "get", classmethod(fake_get))


def _warnings(caplog):
    return [record for record in caplog.records if record.levelno == logging.WARNING]


def test_a_missing_servicebus_warns_once_and_still_answers_no_servers(monkeypatch, caplog):
    """The lookup raising is the non-fatal path: ``{}`` and ONE warning naming the exception."""
    _patch_get(monkeypatch, RuntimeError("no ServiceBus registered in this process"))

    with caplog.at_level(logging.WARNING, logger="services.webservice.sites"):
        assert sites.live_servers() == {}, "the contract stays: a bus-less process answers {}"

    warnings = _warnings(caplog)
    assert len(warnings) == 1, [record.getMessage() for record in warnings]
    message = warnings[0].getMessage()
    assert "RuntimeError" in message, f"the warning names the exception type: {message}"
    assert "no ServiceBus registered in this process" in message, message
    assert "no servers" in message and "service bus" in message, \
        f"the warning says what the operator loses and until when: {message}"


def test_the_missing_servicebus_warning_is_not_repeated_on_the_next_request(monkeypatch, caplog):
    """Every public page reads the registry, so a per-request warning would flood the log."""
    _patch_get(monkeypatch, RuntimeError("no ServiceBus registered in this process"))

    with caplog.at_level(logging.WARNING, logger="services.webservice.sites"):
        assert sites.live_servers() == {}
        assert sites.live_servers() == {}
        assert sites.live_servers() == {}

    assert len(_warnings(caplog)) == 1, [record.getMessage() for record in _warnings(caplog)]


def test_an_unregistered_bus_is_the_same_loud_state(monkeypatch, caplog):
    """``ServiceRegistry.get`` answers ``None`` — it does not raise — when nothing registered it."""
    _patch_get(monkeypatch, None)

    with caplog.at_level(logging.WARNING, logger="services.webservice.sites"):
        assert sites.live_servers() == {}
        assert sites.live_servers() == {}

    warnings = _warnings(caplog)
    assert len(warnings) == 1, [record.getMessage() for record in warnings]
    assert "None" in warnings[0].getMessage(), warnings[0].getMessage()


def test_a_registered_bus_answers_its_servers_and_warns_nothing(monkeypatch, caplog):
    """The positive control: a warning that is always printed cannot pass this pin."""
    servers = _registry(_server("Server 1"), _server("Server 2", ["5th Guards"]))
    _patch_get(monkeypatch, types.SimpleNamespace(servers=servers))

    with caplog.at_level(logging.WARNING, logger="services.webservice.sites"):
        live = sites.live_servers()

    assert live is servers, "the live registry itself is handed on, never a copy"
    assert set(live.keys()) == {"Server 1", "Server 2"}
    assert not _warnings(caplog), [record.getMessage() for record in _warnings(caplog)]

    catalog = parse_sites({"root": {}, "group1": {"group_role": "5th Guards"}},
                          known_roles=["5th Guards"])
    assert catalog.resolve(None, servers=live).servers == frozenset({"Server 1"})


# --------------------------------------------------------------------- the startup wiring

def test_install_sites_stores_the_catalog_on_the_application(tmp_path):
    from fastapi import FastAPI

    app = FastAPI()
    catalog = sites.install_sites(app, {"sites": {"root": {}}}, config_dir=tmp_path)
    assert sites.sites_for(app) is catalog
    assert catalog.root is not None
    assert not sites.sites_for(FastAPI()), "an application without a block carries an empty catalog"


def test_a_refused_block_stops_the_shell_from_installing(tmp_path):
    """The refusal happens on the REAL install path, before anything is registered."""
    from services.webservice.shell import create_app, install_shell

    app = create_app()
    node = types.SimpleNamespace(config_dir=str(tmp_path))
    with pytest.raises(SiteConfigError) as refusal:
        install_shell(app, node=node, config={
            "frontend": True,
            "sites": {"group1": {"group_role": "No Such Token"}},
        })
    assert "sites.group1.group_role" in str(refusal.value)


def test_a_valid_block_installs_with_the_shell(tmp_path):
    from services.webservice.shell import create_app, install_shell

    app = create_app()
    node = types.SimpleNamespace(config_dir=str(tmp_path))
    install_shell(app, node=node, config={"frontend": True, "sites": {"root": {}}})
    assert sites.sites_for(app).root is not None


# ------------------------------------------------------------------------------ the schema

def _validate(text: str, tmp_path) -> None:
    """Validate *text* against the service schema.

    The real ``unique_port`` extension lives in ``core/utils/validators.py`` and is only needed by
    the unrelated ``port`` rule, so a stand-in is enough here.
    """
    from pykwalify.core import Core

    extension = tmp_path / "ext.py"
    extension.write_text("def unique_port(value, rule_obj, path):\n    return True\n", encoding="utf-8")
    source = tmp_path / "webservice.yaml"
    source.write_text(text, encoding="utf-8")
    Core(source_file=str(source), schema_files=[str(SCHEMA_PATH)],
         extensions=[str(extension)]).validate()


def test_the_schema_accepts_a_sites_block(tmp_path):
    """A block carrying the retired ``branding.copyright`` still validates: the schema keeps the key
    accepted so an older file never fails at startup."""
    _validate("""
DEFAULT:
  frontend: true
  sites:
    root:
      slug: ""
      branding:
        title: Community
        logo: config/site/crest.png
        copyright: Amber Wing
      theme:
        preset: brass
        light: false
      nav: [overview, leaderboard]
      tiles: [online_now, active_week]
      lang: en
    group1:
      slug: group1
      group_role: "5th Guards"
""", tmp_path)


def test_the_schema_accepts_an_uploaded_sheet_key(tmp_path):
    """``theme.sheet`` is a declared key: the schema must not refuse the block that names it."""
    _validate("""
DEFAULT:
  sites:
    root:
      slug: ""
      theme:
        sheet: theme.css
""", tmp_path)


@pytest.mark.parametrize("body", [
    "      brand: {title: x}\n",                       # unknown key on the site
    "      branding:\n        colour: '#fff'\n",       # unknown key in branding
    "      theme:\n        colours: {}\n",             # unknown key in theme
    "      nav: overview\n",                           # a scalar where a list belongs
    "      lang: [en]\n",                              # a list where a string belongs
])
def test_the_schema_refuses_a_sites_block_it_does_not_define(body, tmp_path):
    """The positive control for the pin above: the same validator must REJECT these."""
    from pykwalify.errors import SchemaError

    with pytest.raises(SchemaError):
        _validate("DEFAULT:\n  sites:\n    root:\n      slug: \"\"\n" + body, tmp_path)


# ------------------------------------------------------------------------------ the sample

def test_the_sample_documents_every_key_and_its_default():
    sample = (REPO_ROOT / "samples" / "services" / "webservice.yaml").read_text(encoding="utf-8")
    assert "sites:" in sample
    for key in sites.SITE_KEYS:
        assert key in sample, f"the sample does not document '{key}'"
    for key in sites.BRANDING_KEYS:
        assert key in sample, f"the sample does not document branding.'{key}'"
    for key in sites.THEME_KEYS:
        assert key in sample, f"the sample does not document theme.'{key}'"
    for tile in sites.TILE_IDS:
        assert tile in sample, f"the sample does not document tile '{tile}'"
    for ident in sites.SECTION_IDS:
        assert ident in sample, f"the sample does not document section '{ident}'"
    for metric in site_reads.LEADERBOARD_METRICS:
        assert metric in sample, f"the sample does not document metric '{metric}'"
    for character in sites.CHARACTERS:
        assert character in sample, f"the sample does not document character '{character}'"
    assert sites.SITE_FILE in sample, "the sample names the file the settings page owns"


# ----------------------------------------------------------- the model's new keys (S1)
#
# An install that declares nothing renders exactly what it rendered before these keys existed, so
# every default is pinned here, beside the refusal that keeps a typo from reaching a public page.


def test_an_install_that_declares_nothing_keeps_every_section_and_the_shipped_palette():
    root = parse_sites({"root": {}}).root
    assert root is not None
    assert root.enabled is True, "an absent switch is an enabled site"
    assert root.avatar is False, "an absent avatar flag is a site with no uploaded image"
    assert root.sections == sites.SECTION_IDS == ("community", "season", "standings", "aircraft",
                                                 "when_we_fly", "squadrons", "servers")
    assert root.metrics == site_reads.LEADERBOARD_METRICS
    assert root.character == sites.DEFAULT_CHARACTER == "pvpve"
    assert root.theme.preset is None and root.theme.expert is None
    assert root.theme.light is False
    assert root.branding.welcome is None, "an absent welcome keeps the shipped sentence"


def test_the_new_keys_reach_the_resolved_context():
    catalog = parse_sites({"group1": {
        "group_role": "A",
        "enabled": False,
        "avatar": True,
        "sections": {"season": False},
        "metrics": ["playtime", "kd_ratio"],
        "character": "pve",
        "branding": {"welcome": "%(community)s flies on Tuesday nights."},
        "theme": {"expert": "--accent: #e8b04b;"},
    }}, known_roles=["A"])
    context = catalog.resolve("group1", servers=_registry())
    assert context.enabled is False
    assert context.avatar is True
    assert "season" not in context.sections and "community" in context.sections
    assert context.metrics == ("playtime", "kd_ratio")
    assert context.character == "pve"
    assert context.branding.welcome == "%(community)s flies on Tuesday nights."
    assert context.theme.expert == "--accent: #e8b04b;"


def test_character_is_one_of_three_and_is_never_derived():
    assert sites.CHARACTERS == ("pvp", "pve", "pvpve")
    assert sites.DEFAULT_CHARACTER == "pvpve"
    for value in sites.CHARACTERS:
        assert parse_sites({"root": {"character": value}}).root.character == value


@pytest.mark.parametrize("value", ["pvpx", "PvP", "pvp ve", ""])
def test_a_bad_character_is_refused_with_the_key_the_value_and_the_accepted_set(value):
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"character": value}})
    message = str(refusal.value)
    assert "sites.root.character" in message, message
    assert f"'{value}'" in message, message
    for accepted in sites.CHARACTERS:
        assert accepted in message, message


def test_sections_is_a_closed_map_of_booleans_and_absence_means_on():
    root = parse_sites({"root": {"sections": {"season": False, "squadrons": False}}}).root
    assert root.sections == ("community", "standings", "aircraft", "when_we_fly", "servers")
    assert root.sections == tuple(ident for ident in sites.SECTION_IDS
                                  if ident not in ("season", "squadrons")), \
        "the enabled ids keep the vocabulary's order"


def test_a_section_switched_on_is_in_the_enabled_set():
    root = parse_sites({"root": {"sections": {"season": True}}}).root
    assert root.sections == sites.SECTION_IDS


def test_an_unknown_section_id_is_refused_naming_the_key_and_the_vocabulary():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"sections": {"hours": False}}})
    message = str(refusal.value)
    assert "sites.root.sections.hours" in message, message
    for ident in sites.SECTION_IDS:
        assert ident in message, message


def test_a_sections_list_is_refused_rather_than_read_as_the_map():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"sections": ["community"]}})
    assert "sites.root.sections" in str(refusal.value)


def test_a_section_flag_that_is_not_a_boolean_is_refused():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"sections": {"season": "no"}}})
    assert "sites.root.sections.season" in str(refusal.value)


def test_metrics_is_a_list_of_the_leaderboard_vocabulary_and_absence_means_all():
    root = parse_sites({"root": {"metrics": ["playtime", "kd_ratio"]}}).root
    assert root.metrics == ("playtime", "kd_ratio")


@pytest.mark.parametrize("value", [["kills"], ["playtime", "kills"]])
def test_an_unknown_metric_is_refused_naming_the_key_and_the_value(value):
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"metrics": value}})
    message = str(refusal.value)
    assert "sites.root.metrics" in message, message
    assert "kills" in message, message


def test_a_metrics_scalar_is_refused_rather_than_iterated_as_characters():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"metrics": "playtime"}})
    assert "sites.root.metrics" in str(refusal.value)


def test_enabled_parses_only_a_boolean():
    assert parse_sites({"root": {"enabled": False}}).root.enabled is False
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"enabled": "no"}})
    assert "sites.root.enabled" in str(refusal.value)


# ------------------------------------------------------------------------- the theme shape


def _theme_sheet(directory: Path) -> str:
    sheet = directory / "site" / "theme.css"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_text(":root { --accent: #e8b04b; }\n", encoding="utf-8")
    return "site/theme.css"


def test_two_theme_sources_at_once_are_refused_naming_both_keys():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"theme": {"preset": "paper", "expert": "--accent: #fff;"}}})
    message = str(refusal.value)
    assert "sites.root.theme.preset" in message and "sites.root.theme.expert" in message, message
    assert "three spellings of one intent" in message, message


def test_a_preset_and_a_sheet_at_once_are_refused_naming_both_keys(tmp_path):
    """The sheet is a source like the other two: a block that names it beside a preset is refused,
    and the refusal names BOTH keys — never one of them silently winning."""
    sheet = _theme_sheet(tmp_path)
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"theme": {"preset": "brass", "sheet": "theme.css"}}},
                    config_dir=tmp_path)
    message = str(refusal.value)
    assert "sites.root.theme.preset" in message and "sites.root.theme.sheet" in message, message
    assert "three spellings of one intent" in message, message
    assert (tmp_path / sheet).is_file(), "the fixture's own sheet is not where the key reads"


def test_a_sheet_and_an_expert_block_at_once_are_refused_naming_both_keys(tmp_path):
    _theme_sheet(tmp_path)
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"theme": {"sheet": "theme.css", "expert": "--accent: #fff;"}}},
                    config_dir=tmp_path)
    message = str(refusal.value)
    assert "sites.root.theme.sheet" in message and "sites.root.theme.expert" in message, message


def test_a_sheet_that_is_not_there_is_refused_naming_the_key_and_the_file(tmp_path):
    """A declared sheet whose file is missing is refused WHERE IT IS WRITTEN, naming the key and the
    file — the route would otherwise answer the default palette with only a log line."""
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"theme": {"sheet": "theme.css"}}}, config_dir=tmp_path)
    message = str(refusal.value)
    assert "sites.root.theme.sheet" in message, message
    assert "theme.css" in message and "does not exist" in message, message


def test_a_sheet_that_is_a_path_is_refused_naming_the_key(tmp_path):
    """``theme.sheet`` names a LEAF: a value carrying a folder part cannot address a file at all."""
    for declared in ("../theme.css", "site/theme.css", "/etc/theme.css", "~theme.css"):
        with pytest.raises(SiteConfigError) as refusal:
            parse_sites({"root": {"theme": {"sheet": declared}}}, config_dir=tmp_path)
        message = str(refusal.value)
        assert "sites.root.theme.sheet" in message, message
        assert "is not a file name" in message, message


def test_a_sheet_that_is_there_is_accepted_and_named(tmp_path):
    _theme_sheet(tmp_path)
    root = parse_sites({"root": {"theme": {"sheet": "theme.css"}}}, config_dir=tmp_path).root
    assert root is not None and root.theme.sheet == "theme.css"
    assert root.theme.preset is None and root.theme.expert is None


def test_the_retired_stylesheet_key_is_refused_beside_a_valid_source(tmp_path):
    """The two-source refusal counts the two live spellings only: the retired key is an unknown key,
    and naming it beside `preset` does not make it a second source."""
    sheet = _theme_sheet(tmp_path)
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"theme": {"stylesheet": sheet, "preset": "brass"}}})
    message = str(refusal.value)
    assert "sites.root.theme.stylesheet" in message and "unknown key" in message, message


def test_one_theme_source_alone_is_accepted():
    preset = parse_sites({"root": {"theme": {"preset": "slate"}}}).root
    assert preset is not None and preset.theme.preset == "slate"
    expert = parse_sites({"root": {"theme": {"expert": "--accent: #e8b04b;"}}}).root
    assert expert is not None and expert.theme.expert == "--accent: #e8b04b;"


def test_a_preset_this_build_does_not_ship_warns_once_and_keeps_the_shipped_palette(caplog):
    sites._WARNED_PRESETS.clear()           # process state: this pin asserts the one warning
    with caplog.at_level(logging.WARNING, logger="services.webservice.sites"):
        root = parse_sites({"root": {"theme": {"preset": "no-such-preset"}}}).root
        parse_sites({"root": {"theme": {"preset": "no-such-preset"}}})
    assert root.theme.preset is None, "an unshipped preset falls back to the shipped palette"
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1, [record.getMessage() for record in warnings]
    message = warnings[0].getMessage()
    assert "sites.root.theme.preset" in message and "no-such-preset" in message, message


def test_the_expert_text_is_bounded_and_typed():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"theme": {"expert": "--accent: #fff;\n" * 2000}}})
    message = str(refusal.value)
    assert "sites.root.theme.expert" in message and str(sites.MAX_EXPERT_LENGTH) in message, message
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"theme": {"expert": ["--accent: #fff;"]}}})
    assert "sites.root.theme.expert" in str(refusal.value)


# ----------------------------------------------------------------------- the welcome text


def test_welcome_is_one_line_of_at_most_280_characters():
    assert sites.MAX_WELCOME_LENGTH == 280
    text = "%(community)s flies the Caucasus on Tuesday nights."
    assert parse_sites({"root": {"branding": {"welcome": text}}}).root.branding.welcome == text
    longest = "x" * sites.MAX_WELCOME_LENGTH
    assert parse_sites({"root": {"branding": {"welcome": longest}}}).root.branding.welcome == longest


@pytest.mark.parametrize("text", [
    "x" * 281,                                    # over the bound (pinned at 280 below)
    "two\nlines",                                 # a line break
    "<script>alert(1)</script>",                  # markup
    "a\x00control",
])
def test_a_welcome_that_breaks_the_text_contract_is_refused(text):
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"branding": {"welcome": text}}})
    assert "sites.root.branding.welcome" in str(refusal.value)


def test_a_placeholder_other_than_community_is_refused_naming_the_accepted_one():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"branding": {"welcome": "%(commmunity)s flies on Tuesdays."}}})
    message = str(refusal.value)
    assert "sites.root.branding.welcome" in message, message
    assert "%(commmunity)s" in message, message
    assert "%(community)s" in message, message


def test_a_placeholder_missing_its_suffix_is_refused_too():
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"branding": {"welcome": "Hello %(community)d."}}})
    assert "sites.root.branding.welcome" in str(refusal.value)


def test_a_lone_percent_is_not_a_placeholder():
    text = "100% of our sorties run on Tuesday."
    assert parse_sites({"root": {"branding": {"welcome": text}}}).root.branding.welcome == text


# ----------------------------------------------------------------------------- the avatar


def test_avatar_defaults_to_false_and_parses_only_a_boolean():
    assert parse_sites({"root": {}}).root.avatar is False
    assert parse_sites({"root": {"avatar": True}}).root.avatar is True
    assert parse_sites({"root": {"avatar": False}}).root.avatar is False
    with pytest.raises(SiteConfigError) as refusal:
        parse_sites({"root": {"avatar": "yes"}})
    assert "sites.root.avatar" in str(refusal.value)


def test_avatar_path_names_the_four_files_under_the_config_site_folder(tmp_path):
    assert sites.AVATAR_SIZES == (32, 64, 128, 256)
    assert sites.avatar_path("", 32, tmp_path) == tmp_path / "site" / "avatar-32.png"
    assert sites.avatar_path("", 128, tmp_path) == tmp_path / "site" / "avatar-128.png"
    assert sites.avatar_path("", 256, tmp_path) == tmp_path / "site" / "avatar-256.png"
    assert sites.avatar_path("group1", 64, tmp_path) == tmp_path / "site" / "avatar-group1-64.png"
    with pytest.raises(ValueError):
        sites.avatar_path("group1", 48, tmp_path)


def test_two_slugs_can_never_name_the_same_avatar_files(tmp_path):
    """The root names carry no slug segment and ``SLUG_PATTERN`` forbids a leading dash."""
    root = {sites.avatar_path("", size, tmp_path) for size in sites.AVATAR_SIZES}
    for slug in ("group1", "g", "group-1", "0", "a1"):
        assert not root & {sites.avatar_path(slug, size, tmp_path) for size in sites.AVATAR_SIZES}


# --------------------------------------------------------------- the file the page owns


def test_the_site_file_wins_over_the_webservice_block(tmp_path, caplog):
    (tmp_path / sites.SITE_FILE).write_text("sites:\n  root:\n    character: pve\n",
                                            encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="services.webservice.sites"):
        catalog = load_sites({"sites": {"root": {"character": "pvp"}}}, config_dir=tmp_path)
    assert catalog.root is not None and catalog.root.character == "pve", \
        "the file answers; the webservice block is the legacy home"
    assert not [record for record in caplog.records if record.levelno == logging.WARNING], \
        "the file's presence is not a reason to warn"


def test_a_site_file_without_a_sites_block_wins_by_being_empty(tmp_path):
    """The file is authoritative the moment it exists — deterministically, not by content."""
    (tmp_path / sites.SITE_FILE).write_text("sites: {}\n", encoding="utf-8")
    assert not load_sites({"sites": {"root": {}}}, config_dir=tmp_path)


def test_without_the_site_file_the_webservice_block_answers_and_warns_once(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="services.webservice.sites"):
        catalog = load_sites({"sites": {"root": {"character": "pvp"}}}, config_dir=tmp_path)
    assert catalog.root is not None and catalog.root.character == "pvp"
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1, [record.getMessage() for record in warnings]
    message = warnings[0].getMessage()
    assert sites.SITE_FILE in message and "webservice.yaml" in message, message


def test_a_block_absent_from_both_homes_is_still_silent(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="services.webservice.sites"):
        assert not load_sites({}, config_dir=tmp_path)
    assert not [record for record in caplog.records if record.levelno == logging.WARNING]


def test_an_unreadable_site_file_is_refused_naming_the_file(tmp_path):
    (tmp_path / sites.SITE_FILE).write_text("sites: [unclosed\n", encoding="utf-8")
    with pytest.raises(SiteConfigError) as refusal:
        load_sites({}, config_dir=tmp_path)
    assert sites.SITE_FILE in str(refusal.value)


# ---------------------------------------------------------- live content, installed structure


def _installed_app(tmp_path, block):
    from fastapi import FastAPI

    app = FastAPI()
    sites.install_sites(app, {"sites": block}, config_dir=tmp_path)
    app.state.webui_config_dir = str(tmp_path)
    return app


def test_live_catalog_applies_content_without_a_reload(tmp_path):
    app = _installed_app(tmp_path, {"root": {}})
    installed_root = sites.sites_for(app).root
    assert installed_root is not None and installed_root.character == "pvpve"

    (tmp_path / sites.SITE_FILE).write_text(
        "sites:\n"
        "  root:\n"
        "    character: pve\n"
        "    avatar: true\n"
        "    sections:\n"
        "      season: false\n"
        "    branding:\n"
        "      welcome: \"%(community)s flies on Tuesdays.\"\n", encoding="utf-8")

    live = sites.live_catalog(app)

    assert live.reload_required is False
    root = live.catalog.root
    assert root is not None
    assert root.character == "pve"
    assert root.avatar is True
    assert "season" not in root.sections
    assert root.branding.welcome == "%(community)s flies on Tuesdays."
    assert sites.sites_for(app).root.character == "pvpve", "the installed catalogue is untouched"


def test_live_catalog_serves_the_installed_structure_while_its_own_is_stale(tmp_path):
    app = _installed_app(tmp_path, {"root": {}})

    (tmp_path / sites.SITE_FILE).write_text(
        "sites:\n"
        "  root:\n"
        "    character: pve\n"
        "  group1:\n"
        "    group_role: A\n", encoding="utf-8")

    live = sites.live_catalog(app)

    assert live.reload_required is True, "a structural difference is not half-applied"
    assert [site.key for site in live.catalog.sites] == ["root"], "no route exists for the new site"
    assert live.catalog.root.character == "pvpve", "no content is applied either"


def test_switching_a_site_off_is_structural_and_waits_for_the_reload(tmp_path):
    app = _installed_app(tmp_path, {"root": {}})
    (tmp_path / sites.SITE_FILE).write_text("sites:\n  root:\n    enabled: false\n",
                                            encoding="utf-8")

    live = sites.live_catalog(app)

    root = live.catalog.root
    assert live.reload_required is True
    assert root is not None and root.enabled is True



def test_live_catalog_answers_an_unchanged_file_from_its_cache(tmp_path, monkeypatch):
    app = _installed_app(tmp_path, {"root": {}})
    (tmp_path / sites.SITE_FILE).write_text("sites:\n  root:\n    character: pve\n",
                                            encoding="utf-8")
    first = sites.live_catalog(app)
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return sites.SiteCatalog()

    monkeypatch.setattr(sites, "parse_sites", counting)

    second = sites.live_catalog(app)

    assert not calls, "the (path, mtime, size) key answers the second request from the cache"
    assert second == first


def test_live_catalog_without_the_file_is_the_installed_catalogue(tmp_path):
    app = _installed_app(tmp_path, {"root": {}})
    live = sites.live_catalog(app)
    assert live.reload_required is False
    assert live.catalog is sites.sites_for(app)


def test_a_broken_site_file_does_not_break_the_pages(tmp_path, caplog):
    app = _installed_app(tmp_path, {"root": {}})
    (tmp_path / sites.SITE_FILE).write_text("sites: [unclosed\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="services.webservice.sites"):
        live = sites.live_catalog(app)

    assert live.reload_required is True
    root = live.catalog.root
    assert root is not None and root.character == "pvpve"
    assert [record for record in caplog.records if record.levelno == logging.WARNING], \
        "an unreadable file is reported once, not silently"


# ------------------------------------------------------------- the new schema keys


def test_the_schema_accepts_the_new_site_keys(tmp_path):
    _validate("""
DEFAULT:
  frontend: true
  sites:
    root:
      slug: ""
      enabled: true
      avatar: true
      character: pve
      branding:
        title: Community
        welcome: "%(community)s flies on Tuesday nights."
      theme:
        preset: paper
      nav: [overview, leaderboard]
      sections:
        community: true
        season: false
        standings: true
        aircraft: true
        when_we_fly: false
        squadrons: false
        servers: true
      metrics: [playtime, kd_ratio]
    group1:
      slug: group1
      group_role: "5th Guards"
      theme:
        expert: |
          --accent: #e8b04b;
""", tmp_path)


@pytest.mark.parametrize("body", [
    "      sections:\n        hours: false\n",       # unknown section id
    "      sections:\n        season: no\n",          # a flag that is not a boolean
    "      character: pvpx\n",                        # outside the closed enum
    "      sections: [community]\n",                  # a list where the map belongs
])
def test_the_schema_refuses_a_new_key_outside_its_vocabulary(body, tmp_path):
    """The schema closes the ``sections:`` map and the character enum; the metric VOCABULARY is
    the parser's (:func:`sites._metrics`), like ``nav`` and ``tiles`` — the schema states shape."""
    from pykwalify.errors import SchemaError

    with pytest.raises(SchemaError):
        _validate("DEFAULT:\n  sites:\n    root:\n      slug: \"\"\n" + body, tmp_path)
