"""Site configuration and the resolved site context — one value per request, no pages.

A SITE is a view of a subset of the cluster's servers. Membership is not declared here:
``servers.yaml``'s ``managed_by`` is the only authority.

* a site that carries a group token shows the servers whose ``managed_by`` names that token;
* the root site (the site with no token) shows the servers that declare no ``managed_by`` at all;
* a server naming two tokens belongs to both of those sites, and to no other.

The ``sites:`` block
--------------------
One block in ``config/site.yaml`` — the file the Site settings page owns, beside ``nodes.yaml``::

    sites:
      root:                      # the site with no group token
        slug: ""                 # served at the site root
        enabled: true            # false = declared, not served: no route is registered
        avatar: true             # written by the avatar upload route, never by hand
        branding: {title: ..., logo: ..., welcome: ..., footer_links: [...]}
        theme: {preset: ..., expert: ..., light: false}
        nav: [overview, leaderboard, squadrons, players]
        tiles: [active_week, online_now, playtime_total, avg_session, peak_hour]
        sections: {squadrons: false, season: false}
        metrics: [playtime, kd_ratio]
        lang: en
        character: pvpve         # pvp | pve | pvpve — which figures a page leads with
      group1:
        slug: group1
        group_role: "5th Guards"  # a managed_by token, as written in servers.yaml

The SAME block under ``config/services/webservice.yaml`` is the legacy home: an install that has no
``config/site.yaml`` is answered from there, with one warning naming both files. The moment
``config/site.yaml`` exists it is the only source, whatever the legacy block carries.

The ``character:`` key
----------------------
``pvp``, ``pve`` or ``pvpve``, absent meaning :data:`DEFAULT_CHARACTER`. It is DECLARED by the
operator and never derived from the data: the share of PvP kills cannot separate a quiet PvP server
from a mixed one, and a threshold would silently mislabel a site. It decides which figures LEAD a
page — the order of the readout strip's cells and the order of the combat panels — never which
figures exist, and the page states it in one line so a visitor knows why those numbers lead.

The ``branding:`` block
-----------------------
``title`` is the site's name in the top bar and the document title. ``community_name`` is the name
the overview's hero greets a visitor with: one line of plain text (no markup, no line breaks, at most
:data:`MAX_COMMUNITY_NAME_LENGTH` characters), optional, and only used when the operator declares it
— with none, the name comes from the connected Discord guild, then ``main.yaml``'s ``guild_name``,
then ``title`` (see ``site_pages.community_name``). ``logo`` is a raster path the
site renders instead of the shipped mark; with none, the site shows the product's icon mark.
``footer_links`` are the footer's anchors, each ``{label, href}`` with the href validated to an
``http(s)``/``mailto`` URL or a path on this installation.

The footer's text is the product's attribution — ``Powered by DCSServerBot``, written as a literal in
``site_base.html`` — so no site can set, shorten or hide it. The one thing a site adds to the footer
is its own anchors (:data:`BRANDING_KEYS`'s ``footer_links``), and those sit right of the attribution.

``copyright`` is ACCEPTED AND IGNORED (:data:`IGNORED_BRANDING_KEYS`). It named the footer's holder
while the footer printed ``© <year> <holder>``; now that the footer carries the product's fixed
attribution the value has nowhere to go, and it is dropped rather than refused — a ``site.yaml``
written for a release that still documents the key must keep loading.

The other per-site keys
-----------------------
``enabled`` is the site's own switch: absent or ``true`` = served, ``false`` = the block is kept but
no route is registered for it. ``sections`` is a map of section id -> ``true``/``false`` over
:data:`SECTION_IDS`, and an id the map does not name is ON — so an install that declares no map
shows every section, and a section a later release adds is on for every existing site. ``metrics``
is a LIST over :data:`~services.webservice.site_reads.LEADERBOARD_METRICS` (unlike ``sections``,
because it is a pick from a vocabulary the operator can see); absent means every metric.
``theme.preset``/``theme.sheet``/``theme.expert`` are three spellings of one intent and at most one
of them may be set; ``theme.expert`` is the body of one ``:root`` rule, bounded by
:data:`MAX_EXPERT_LENGTH`, and ``theme.sheet`` names a LEAF file in the site's asset folder
(:func:`sheet_path`), bounded in bytes by :data:`MAX_SHEET_LENGTH` where it is uploaded.
``branding.welcome`` is the sentence the overview prints under the
headline — one line, at most :data:`MAX_WELCOME_LENGTH` characters, in which ``%(community)s`` is
the only placeholder. ``branding.mark_side`` is ``left`` or ``right`` and places the hero's mark
beside the headline. ``avatar`` is the state an avatar upload writes (:func:`avatar_path` names the
four files); it is NEVER posted by a form.

:func:`load_sites` parses and validates the block ONCE, at service startup, and stores the result
on the application (:func:`install_sites` / :func:`sites_for`). :func:`live_catalog` is the one
reader for a request: it overlays a site's CONTENT from a fresh parse of the file and never its
structure (which sites exist, their slugs, their switches), because a route exists only for what was
installed. Every refusal it raises names the offending key. :meth:`SiteCatalog.resolve` is the ONE
resolution function: it turns a request's first path segment into a :class:`SiteContext`, whose
``servers`` frozenset is read from the live server registry at that moment. Nothing deeper
re-derives it.

The three traps this module exists to close
-------------------------------------------
* a ``group_role`` that matches no ``managed_by`` value anywhere in ``servers.yaml`` grants nobody,
  serves an empty site and reports nothing — refused at startup;
* ``managed_by: 5th Guards`` written as a SCALAR parses (the schema allows free text) and then
  iterates as single characters, matching no role at all — coerced to a one-element list at load,
  with one warning naming the server, and never a failed start;
* an UNKNOWN first path segment must answer "not found" rather than fall back to the root site, or
  a mistyped URL shows a visitor a group they are not in.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NoReturn

from .registry import RESERVED_PATHS, RESERVED_PREFIXES
from .site_reads import LEADERBOARD_METRICS

__all__ = [
    "AVATAR_SIZES",
    "BRANDING_KEYS",
    "DEFAULT_MARK_SIDE",
    "MARK_SIDES",
    "CHARACTERS",
    "CONTENT_FIELDS",
    "DEFAULT_BRANDING_TITLE",
    "DEFAULT_CHARACTER",
    "DEFAULT_METRICS",
    "DEFAULT_SECTIONS",
    "FOOTER_LINK_KEYS",
    "IGNORED_BRANDING_KEYS",
    "MAX_COMMUNITY_NAME_LENGTH",
    "MAX_EXPERT_LENGTH",
    "MAX_SHEET_LENGTH",
    "MAX_WELCOME_LENGTH",
    "SHEET_LEAF",
    "DEFAULT_LANG",
    "DEFAULT_NAV",
    "DEFAULT_TILES",
    "NAV_PAGE_IDS",
    "ROOT_KEY",
    "SECTION_IDS",
    "SITE_FILE",
    "SITE_KEYS",
    "SLUG_PATTERN",
    "THEME_KEYS",
    "TILE_IDS",
    "Branding",
    "FooterLink",
    "LiveCatalog",
    "Site",
    "SiteCatalog",
    "SiteConfigError",
    "SiteContext",
    "SiteNotFound",
    "Theme",
    "asset_path",
    "avatar_path",
    "first_segment",
    "install_sites",
    "known_managed_by",
    "live_catalog",
    "live_servers",
    "load_servers_config",
    "load_site_block",
    "load_sites",
    "managed_by_of",
    "managed_by_values",
    "parse_sites",
    "sheet_path",
    "site_file",
    "sites_for",
    "structure_of",
]

log = logging.getLogger(__name__)

#: the key of the site that carries no group token. It is the one site served at the site root,
#: and the one a request without a site segment resolves to.
ROOT_KEY = "root"

#: a slug is URL-safe: lowercase alphanumerics and inner dashes, at least one character.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: the nav page ids a site may offer, in the order they are offered when the block names none.
#: A page that joins the site adds its id HERE: an id this list does not carry is refused, so a
#: typo cannot render a nav entry that leads nowhere.
NAV_PAGE_IDS: tuple[str, ...] = ("overview", "leaderboard", "squadrons", "players")

#: the tile ids a site may show, in the order they are shown when the block names none. Same rule
#: as the nav: the id set is this constant, and an unknown id is a refusal.
TILE_IDS: tuple[str, ...] = ("active_week", "online_now", "playtime_total", "avg_session", "peak_hour")

#: the overview section ids a site may switch, in the order the page numbers them. An id the
#: ``sections:`` map does not name is ON, so this tuple is also the default: every section on. A
#: section that joins the page adds its id HERE, and the id must be what the template branches on.
SECTION_IDS: tuple[str, ...] = ("community", "season", "standings", "aircraft", "when_we_fly",
                                "squadrons", "servers")

#: the sections a site shows when its block names none: all of them, in page order.
DEFAULT_SECTIONS: tuple[str, ...] = SECTION_IDS

#: the leaderboard metrics a site offers when its block names none: every one the read layer can
#: rank, in the order it publishes them.
DEFAULT_METRICS: tuple[str, ...] = LEADERBOARD_METRICS

DEFAULT_NAV: tuple[str, ...] = NAV_PAGE_IDS
DEFAULT_TILES: tuple[str, ...] = TILE_IDS

#: the language a site renders in when the block names none (a web UI language code).
DEFAULT_LANG = "en"

#: the kinds of community a site declares, exhaustively. The character decides which figures LEAD a
#: page; it is a DECLARED value and never derived from the data.
CHARACTERS: tuple[str, ...] = ("pvp", "pve", "pvpve")

#: the character a site renders as when the block names none: both halves of the record lead.
DEFAULT_CHARACTER = "pvpve"

#: the brand title a site shows when the block names none.
DEFAULT_BRANDING_TITLE = "DCSServerBot"

#: the keys a site block may carry, exhaustively. An unknown key is a refusal. ``avatar`` and
#: ``enabled`` are parsed but never posted by a form: an uploaded file and the page's own switch
#: write them.
SITE_KEYS: tuple[str, ...] = ("slug", "group_role", "enabled", "avatar", "branding", "theme", "nav",
                              "tiles", "sections", "metrics", "lang", "character")

#: the keys a ``branding:`` block may carry, exhaustively.
BRANDING_KEYS: tuple[str, ...] = ("title", "community_name", "logo", "welcome", "mark_side",
                                  "footer_links")

#: the ``branding:`` keys an older file may still carry and the parser accepts WITHOUT reading: the
#: value is parsed as nothing and never reaches the site. ``copyright`` named the footer's holder, and
#: the footer now prints the product's fixed attribution; refusing the key would refuse a site file
#: that loaded before, so it is dropped instead.
IGNORED_BRANDING_KEYS: tuple[str, ...] = ("copyright",)

#: the sides the hero's mark may sit on, exhaustive. ``right`` is the default: it keeps the headline
#: on the page's one left edge and lands the mark in the hero's own glow.
MARK_SIDES: tuple[str, ...] = ("left", "right")
DEFAULT_MARK_SIDE = "right"

#: the longest ``branding.community_name`` accepted; it prints in the hero's headline.
MAX_COMMUNITY_NAME_LENGTH = 60

#: the longest ``branding.welcome`` line accepted: at most seven lines of the footer column it
#: prints in. The placeholder token does not count against it differently — the bound is the text.
MAX_WELCOME_LENGTH = 280

#: the only placeholder a welcome text may carry, filled with the community's name.
WELCOME_PLACEHOLDER = "%(community)s"

#: the keys a ``footer_links`` entry may carry, exhaustively.
FOOTER_LINK_KEYS: tuple[str, ...] = ("label", "href")

#: the keys a ``theme:`` block may carry, exhaustively. ``preset``, ``sheet`` and ``expert`` are
#: three spellings of one intent: a block carries at most one of them.
THEME_KEYS: tuple[str, ...] = ("preset", "sheet", "expert", "light")

#: the three spellings of "this site's palette", exhaustive — the one-of refusal reads this.
THEME_SOURCES: tuple[str, ...] = ("preset", "sheet", "expert")

#: the largest ``theme.expert`` body accepted, in characters: the allow-listed declarations only.
MAX_EXPERT_LENGTH = 8 * 1024

#: the largest uploaded palette sheet accepted, in BYTES. A sheet is arbitrary CSS rather than a
#: declaration list, so its bound is a size and not a grammar; 256 KB is far more than any token
#: sheet needs and small enough to stay a page asset.
MAX_SHEET_LENGTH = 256 * 1024

#: the leaf an uploaded palette sheet is stored under. FIXED, so no operator-supplied file name ever
#: reaches a path, and a re-upload overwrites the file the site already names.
SHEET_LEAF = "theme.css"

#: a sheet's LEAF name: letters, digits, ``.``, ``-`` and ``_``, starting with a letter or digit. No
#: separator, no leading dot, no ``~`` and no ``$`` — the grammar is the whole path defence, so a
#: declared name can never address anything outside the site's own asset folder.
SHEET_LEAF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: the avatar sizes an uploaded image is re-encoded to, in pixels. Closed: the four file names and
#: the four previews are built from it, and a size outside it is refused by :func:`avatar_path`.
#: 32 is the bar's 1x asset, 64 its 2x, 128 the settings card's largest preview and 256 the hero's
#: 2x asset (drawn at 128 CSS px).
AVATAR_SIZES: tuple[int, ...] = (32, 64, 128, 256)

#: the folder under the config directory a site's assets live in (``avatar_path`` and the sample's
#: ``logo`` both name it).
ASSET_DIR = "site"

#: the file the Site settings page owns, on the level of ``nodes.yaml``. It is read first; the
#: ``sites:`` block of ``config/services/webservice.yaml`` is the legacy home the page takes over.
SITE_FILE = "site.yaml"

#: slugs the shell already owns, derived from its own declarations (``registry.RESERVED_PATHS`` /
#: ``RESERVED_PREFIXES``) so there is one home for the reserved surface: a site mounted under one
#: of these would shadow a shell path (or be shadowed by it) with nothing to report it.
RESERVED_SLUGS: frozenset[str] = frozenset(
    part.strip("/") for part in (RESERVED_PATHS + RESERVED_PREFIXES) if part.strip("/")
)

#: the attribute the parsed catalog is stored under on the FastAPI application
STATE_ATTR = "webui_sites"


class SiteConfigError(ValueError):
    """Raised when the ``sites:`` block (or a ``servers.yaml`` it is validated against) is unusable.

    Raised at startup, never at render time: a site that cannot be served must stop the shell from
    installing rather than render an empty page. The message names the offending key.
    """


class SiteNotFound(LookupError):
    """Raised when a request's first path segment names no configured site.

    The caller answers 404. It is NOT a fall back to the root site: a mistyped URL must not show a
    visitor a group they are not in.
    """


# --------------------------------------------------------------------------- the site's values

@dataclass(frozen=True, slots=True)
class FooterLink:
    """One entry of the site's footer: the words a visitor reads and where they lead.

    ``href`` is validated at parse time (:func:`_href`) to an ``http(s)``/``mailto`` URL or a path
    on this installation, so no other scheme can reach a public page's anchor.
    """

    label: str
    href: str


@dataclass(frozen=True, slots=True)
class Branding:
    """The words and the mark a site wears.

    ``community_name`` is the name the hero greets a visitor with, when the operator declares one:
    one line of plain text, printed ESCAPED, refused at parse time when it carries markup, a line
    break or more than :data:`MAX_COMMUNITY_NAME_LENGTH` characters. ``welcome`` is the sentence the
    overview prints beside it: the same text contract at :data:`MAX_WELCOME_LENGTH`, in which
    ``%(community)s`` is the only placeholder and is filled with the name
    ``site_pages.community_name`` answers. ``footer_links`` are the footer's anchors, whose shape (a
    label and a validated href) is the whole vocabulary.

    The footer's TEXT is not here: it is the product's fixed attribution, written in
    ``site_base.html``, so a site has no value that could change it.
    """

    title: str = DEFAULT_BRANDING_TITLE
    #: the name the hero greets with; ``None`` means the site declares none and the name is resolved
    #: from the connected Discord guild, ``main.yaml``'s ``guild_name``, then ``title``.
    community_name: str | None = None
    #: path to the site's logo; ``None`` means the site ships no logo of its own.
    logo: str | None = None
    #: the sentence printed under the hero's headline; ``None`` keeps the shipped translation.
    welcome: str | None = None
    #: which side of the hero's headline the mark is drawn on: one of :data:`MARK_SIDES`.
    mark_side: str = DEFAULT_MARK_SIDE
    #: the footer's links, in declaration order.
    footer_links: tuple[FooterLink, ...] = ()


@dataclass(frozen=True, slots=True)
class Theme:
    """A site's palette overrides: at most ONE source, and the deprecated ``light`` alias.

    ``preset`` names a shipped token sheet, ``sheet`` names an uploaded CSS file in the site's asset
    folder, and ``expert`` carries the body of one ``:root`` rule — three spellings of one intent, so
    the parse refuses a block that sets more than one. A preset and an expert block are TOKEN-ONLY:
    they re-declare custom properties and never a component rule, because the site's components are
    the shipped ones. An uploaded sheet is served AS WRITTEN and is therefore not token-only — the
    operator owns its content. ``light`` asks for the light palette instead of the dark one.
    """

    #: the shipped preset's id; ``None`` means the site declares none.
    preset: str | None = None
    #: the uploaded sheet's leaf name inside the site's asset folder; ``None`` means none.
    sheet: str | None = None
    #: the ``:root`` declarations the operator wrote, body only (the server wraps them).
    expert: str | None = None
    light: bool = False


@dataclass(frozen=True, slots=True)
class Site:
    """One configured site, as declared. Static: it carries no server names.

    The server names are a fact of the RUNNING process and belong to :class:`SiteContext`, which
    :meth:`Site.context` builds for one request.
    """

    #: the site's name — its key in the ``sites:`` block
    key: str
    #: the URL-safe slug it is served under; ``""`` for the root site
    slug: str
    #: the ``managed_by`` token this site shows; ``None`` for the root site
    group_role: str | None
    branding: Branding = Branding()
    theme: Theme = Theme()
    nav: tuple[str, ...] = DEFAULT_NAV
    tiles: tuple[str, ...] = DEFAULT_TILES
    #: the section ids the overview renders, in page order; every id is here when the block names
    #: no ``sections:`` map.
    sections: tuple[str, ...] = DEFAULT_SECTIONS
    #: the leaderboard metrics the site offers, in the order the block names them.
    metrics: tuple[str, ...] = DEFAULT_METRICS
    lang: str = DEFAULT_LANG
    #: the kind of community the site declares (one of :data:`CHARACTERS`), which decides the order
    #: the pages lead their figures in.
    character: str = DEFAULT_CHARACTER
    #: whether the site is served at all: a disabled site registers no route.
    enabled: bool = True
    #: whether the avatar upload wrote the site's four PNGs (see :func:`avatar_path`).
    avatar: bool = False

    @property
    def is_root(self) -> bool:
        """Whether this is the untagged-servers site."""
        return self.group_role is None

    @property
    def prefix(self) -> str:
        """The URL prefix the site is served under: ``""`` for the root site, ``"/<slug>"`` else."""
        return f"/{self.slug}" if self.slug else ""

    def members(self, servers) -> frozenset[str]:
        """The names of *servers* that belong to this site.

        *servers* is a server registry (``ServiceBus.servers``) or any mapping/sequence of server
        objects. The root site takes every server that declares no ``managed_by``; a group site
        takes every server that names its token — so a server naming two tokens is in both of those
        sites and in no other.
        """
        names: set[str] = set()
        for name, server in _registry_pairs(servers):
            tokens = managed_by_of(server)
            if self.group_role is None:
                if tokens:
                    continue
            elif self.group_role not in tokens:
                continue
            names.add(name)
        return frozenset(names)

    def context(self, servers=None) -> "SiteContext":
        """The value a page consumes for THIS site, with the membership of *servers* right now."""
        return SiteContext(
            name=self.key,
            slug=self.slug,
            prefix=self.prefix,
            servers=self.members(servers),
            branding=self.branding,
            theme=self.theme,
            nav=self.nav,
            tiles=self.tiles,
            sections=self.sections,
            metrics=self.metrics,
            lang=self.lang,
            is_root=self.is_root,
            character=self.character,
            enabled=self.enabled,
            avatar=self.avatar,
        )


@dataclass(frozen=True, slots=True)
class SiteContext:
    """What one request's pages consume. Immutable, comparable, loggable.

    Built once per request by :meth:`SiteCatalog.resolve` and handed down; nothing below it
    re-derives a field. ``servers`` is a frozenset of server NAMES, taken from the live registry at
    the moment of resolution.
    """

    name: str
    slug: str
    prefix: str
    servers: frozenset[str]
    branding: Branding
    theme: Theme
    nav: tuple[str, ...]
    tiles: tuple[str, ...]
    lang: str
    is_root: bool
    #: the kind of community the site declared; pages read it to decide which figures lead.
    character: str = DEFAULT_CHARACTER
    #: the section ids the overview renders, in page order (an absent map means all of them).
    sections: tuple[str, ...] = DEFAULT_SECTIONS
    #: the leaderboard metrics the site offers.
    metrics: tuple[str, ...] = DEFAULT_METRICS
    enabled: bool = True
    #: whether the site has an uploaded avatar; pages resolve the three files from it.
    avatar: bool = False


# ------------------------------------------------------------------- the ``managed_by`` tokens

def managed_by_values(value) -> tuple[str, ...]:
    """A declared ``managed_by`` as a tuple of non-empty tokens.

    THE coercion, in one place: a SCALAR is ONE token, never a sequence of characters. A config
    written as ``managed_by: 5th Guards`` would otherwise iterate as ``'5', 't', 'h', ...`` and
    match no role at all — the value is read as ``('5th Guards',)`` here. An integer role id keeps
    its text form, which is what ``servers.yaml`` writes and what a site's ``group_role`` compares
    against.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, Mapping):
        # no shape of managed_by is a map; read it as "declares none" rather than as its keys
        return ()
    if isinstance(value, Iterable):
        tokens: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                tokens.append(text)
        return tuple(tokens)
    return (str(value).strip(),) if str(value).strip() else ()


def managed_by_of(server) -> tuple[str, ...]:
    """A server's declared ``managed_by`` tokens, read tolerantly and coerced.

    Duck-typed on purpose (a live ``ServerImpl``, a test double, an object with no ``locals``): a
    server that declares nothing yields ``()``, which is what both site kinds read as "no token".
    """
    locals_ = getattr(server, "locals", None)
    if locals_ is None or not callable(getattr(locals_, "get", None)):
        return ()
    return managed_by_values(locals_.get("managed_by"))


def _registry_pairs(servers) -> list[tuple[str, Any]]:
    """``(name, server)`` pairs of a server registry, mapping or sequence.

    The live registry is a ``ThreadSafeDict`` whose ``__iter__`` yields ``(name, server)`` PAIRS,
    while iterating a plain ``dict`` yields keys — so the pairs are read through ``items()`` and
    never by iterating the container, which would silently produce tuples on one type and strings
    on the other.
    """
    if not servers:
        return []
    items = getattr(servers, "items", None)
    if callable(items):
        return [(str(name), server) for name, server in items()]
    return [(str(getattr(server, "name", "")), server) for server in servers]


#: set once the missing ``ServiceBus`` has been reported, so the warning prints once per process
_NO_BUS_WARNED: bool = False


def _warn_no_bus(detail: str) -> None:
    """Warn ONCE per process that this process carries no ``ServiceBus``."""
    global _NO_BUS_WARNED
    if _NO_BUS_WARNED:
        return
    _NO_BUS_WARNED = True
    log.warning("Sites: this process has no ServiceBus (%s). Every public site shows no servers "
                "until the service bus is available in this process.", detail)


def live_servers():
    """The process's own server registry — ``ServiceBus.servers``, the map the plugins read.

    Read through the service registry, never from a second parse of ``servers.yaml``. A process
    without a ``ServiceBus`` (early start, a standalone shell) answers ``{}`` instead of raising:
    the honest state is "no site has a server yet". The lookup returns ``None`` when the bus is
    not registered and raises when it cannot be imported at all — both are reported once per
    process at WARNING, because an empty public site with no reason logged is unreportable.
    """
    try:
        from core import ServiceRegistry
        from services.servicebus import ServiceBus

        bus = ServiceRegistry.get(ServiceBus)
    except Exception as ex:  # noqa: BLE001 - a shell without a bus must still resolve a site
        _warn_no_bus(f"{type(ex).__name__}: {ex}")
        return {}
    if bus is None:
        _warn_no_bus("ServiceRegistry.get(ServiceBus) returned None")
        return {}
    return getattr(bus, "servers", None) or {}


def known_managed_by(servers_config: Mapping | None) -> frozenset[str]:
    """Every ``managed_by`` value declared ANYWHERE in a loaded ``servers.yaml``, as text.

    The token set a site's ``group_role`` is validated against. It is a STARTUP check only: what a
    request shows is the live registry (:meth:`Site.members`), never this set.
    """
    tokens: set[str] = set()
    for block in (servers_config or {}).values():
        if not isinstance(block, Mapping):
            continue
        tokens.update(managed_by_values(block.get("managed_by")))
    return frozenset(tokens)


#: ``(server, value)`` pairs already warned about, so a second load of the same file stays quiet
_WARNED_SCALARS: set[tuple[str, str]] = set()


def load_servers_config(config_dir) -> dict:
    """``servers.yaml`` as the bot loads it, with a SCALAR ``managed_by`` coerced to a list.

    Read here for exactly two startup facts: the token set a ``group_role`` must name, and the
    scalar ``managed_by`` a typo produces. The coercion writes the one-element list into the loaded
    document and logs ONE warning naming the server — a scalar must never fail the whole start.
    A missing or unreadable file yields ``{}`` (no token is declared, so a group site is refused by
    its own ``group_role`` check rather than by this reader).
    """
    if not config_dir:
        return {}
    path = Path(config_dir) / "servers.yaml"
    if not path.is_file():
        return {}
    try:
        from core.utils.yaml_helper import new_yaml

        data = new_yaml().load(path.read_text(encoding="utf-8")) or {}
    except Exception as ex:  # noqa: BLE001 - the site validation reports the file, not a traceback
        raise SiteConfigError(f"servers.yaml: could not be read to validate the sites block: {ex}") from ex
    if not isinstance(data, Mapping):
        return {}
    for name, block in data.items():
        if not isinstance(block, MutableMapping):
            continue
        value = block.get("managed_by")
        if isinstance(value, str) and value.strip():
            block["managed_by"] = [value]
            _warn_scalar(str(name), value)
    return dict(data)


def _warn_scalar(server: str, value: str) -> None:
    """Warn ONCE per (server, value) that a scalar ``managed_by`` was read as one token."""
    key = (server, value)
    if key in _WARNED_SCALARS:
        return
    _WARNED_SCALARS.add(key)
    log.warning("servers.yaml: server '%s' declares managed_by as the scalar '%s'. It is read as "
                "the one-element list ['%s']; written as a scalar it would otherwise iterate as "
                "single characters and match no role at all. Declare it as a list.",
                server, value, value)


# ------------------------------------------------------------------------------ the parsing

def _fail(path: str, message: str) -> NoReturn:
    """Raise the refusal for one offending key, prefixed with the key's path in the block."""
    raise SiteConfigError(f"sites.{path}: {message}")


def _text(path: str, value, *, default=None) -> str | None:
    """*value* as a non-empty string, or *default* when it is absent — a non-string is refused."""
    if value is None:
        return default
    if not isinstance(value, str):
        _fail(path, f"must be a string (got {type(value).__name__})")
    return value


def _ids(path: str, value, *, default: tuple[str, ...], known: tuple[str, ...], what: str) -> tuple[str, ...]:
    """A list of ids from *value*, each one of *known*; absent means *default*.

    A scalar is REFUSED rather than read: a single string would iterate as characters and produce
    an id per letter, which matches nothing and reports nothing.
    """
    if value is None:
        return default
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        _fail(path, f"must be a list of {what}s (a single value would iterate as characters); "
                    f"known {what}s: {', '.join(known)}")
    ids: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _fail(path, f"{item!r} is not a {what}; known {what}s: {', '.join(known)}")
        if item not in known:
            _fail(path, f"unknown {what} '{item}'; known {what}s: {', '.join(known)}")
        ids.append(item)
    return tuple(ids)


def _supported_languages() -> tuple[str, ...]:
    """The languages a page can actually render in, or ``()`` when the catalogs are unreadable."""
    try:
        from . import i18n

        return tuple(i18n.selectable_languages())
    except Exception:  # noqa: BLE001 - a missing locale tree must not refuse every site
        log.debug("Sites: the web UI language list could not be read; lang is checked by shape only.")
        return ()


def asset_path(declared: str, config_dir) -> Path:
    """Where a path declared in a site block lives: ``~``/environment expanded, else under the
    config directory.

    ONE resolution for every file a site block declares (``branding.logo``), so the settings page
    and the route that serves the file can never read two different files.
    """
    expanded = os.path.expanduser(os.path.expandvars(declared))
    path = Path(expanded)
    if not path.is_absolute() and config_dir:
        path = Path(config_dir) / path
    return path


def avatar_path(slug: str, size: int, config_dir) -> Path:
    """Where the uploaded avatar of *size* pixels for the site served under *slug* lives.

    ``slug`` is the INSTALLED site's slug (``""`` for the root site), never a request value, and
    *size* is one of :data:`AVATAR_SIZES`; the name is built from those two and a fixed suffix.
    The three names cannot collide: a slug never starts with ``-`` (``SLUG_PATTERN``), so no slug
    can produce the root site's ``avatar-<size>.png`` and two slugs cannot produce one set.
    """
    if size not in AVATAR_SIZES:
        raise ValueError(f"{size} is not an avatar size; sizes: "
                         f"{', '.join(str(value) for value in AVATAR_SIZES)}")
    if not config_dir:
        raise ValueError("an avatar path needs the config directory it is built under")
    name = f"avatar-{f'{slug}-' if slug else ''}{size}.png"
    return Path(config_dir) / ASSET_DIR / name


def sheet_path(leaf: str, config_dir) -> Path:
    """Where a site's uploaded palette sheet lives: ``<config_dir>/site/<leaf>``.

    The LEAF is the whole defence: :data:`SHEET_LEAF_PATTERN` refuses a separator, a leading dot and
    the ``~``/``$`` a later expansion would honour, so a declared name can address no file outside
    the site's own asset folder. The path is then built through :func:`asset_path`, the ONE
    resolution every declared asset file uses, and its resolved parent is asserted to be that folder.
    """
    if not config_dir:
        raise ValueError("a palette sheet path needs the config directory it is built under")
    leaf = str(leaf or "")
    if SHEET_LEAF_PATTERN.match(leaf) is None:
        raise ValueError(f"'{leaf}' is not a sheet file name: a sheet is named by its file inside "
                         f"'{ASSET_DIR}/', with no folder part")
    folder = (Path(config_dir) / ASSET_DIR).resolve()
    path = asset_path(f"{ASSET_DIR}/{leaf}", config_dir)
    if path.resolve().parent != folder:
        raise ValueError(f"'{leaf}' does not resolve inside '{ASSET_DIR}/'")
    return path


#: an absolute footer link: ``http(s)://`` or ``mailto:`` with no whitespace, quote or angle bracket.
_ABSOLUTE_HREF = re.compile(r"^(?:https?://|mailto:)[^\s\"'<>]+$")


def _href(path: str, value) -> str:
    """A footer link's target: an absolute ``http(s)``/``mailto`` URL or a path on this installation."""
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a non-empty string (an http(s)/mailto URL, or a path beginning with '/')")
    text = value.strip()
    if any(char in text for char in "\"'<>") or text.startswith("//"):
        _fail(path, f"'{text}' is not a link target this site accepts: no quotes, no '<' or '>', "
                    f"and no protocol-relative '//' form")
    if not (text.startswith("/") or _ABSOLUTE_HREF.match(text)):
        _fail(path, f"'{text}' is neither an http(s)/mailto URL nor a path beginning with '/', "
                    f"so its scheme cannot be checked")
    return text


def _footer_links(path: str, value) -> tuple["FooterLink", ...]:
    """The site's footer links: a list of ``{label, href}`` maps, each key checked exhaustively."""
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        _fail(path, "must be a list of {label, href} maps")
    links: list[FooterLink] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, Mapping):
            _fail(item_path, "must be a {label, href} map")
        for key in item:
            if key not in FOOTER_LINK_KEYS:
                _fail(f"{item_path}.{key}", f"unknown key; known keys: {', '.join(FOOTER_LINK_KEYS)}")
        label = _text(f"{item_path}.label", item.get("label"))
        if label is None or not label.strip():
            _fail(f"{item_path}.label", "must be a non-empty string")
        links.append(FooterLink(label=label.strip(), href=_href(f"{item_path}.href", item.get("href"))))
    return tuple(links)


def _plain_line(path: str, value, *, limit: int) -> str | None:
    """*value* as one line of plain text of at most *limit* characters, or ``None`` when unset.

    One line of plain text is the whole contract: markup is refused rather than rendered (every
    template prints the value ESCAPED, so ``<``/``>`` are text, and a value carrying them is a
    mistake), a line break or control character is refused, and the length is bounded. A blank
    value reads as "not declared".
    """
    text = _text(path, value)
    if text is None:
        return None
    line = text.strip()
    if not line:
        return None
    if any(char in line for char in "<>"):
        _fail(path, "must be plain text: no '<' or '>' (it is printed as text, never parsed as markup)")
    if any(ord(char) < 32 for char in line):
        _fail(path, "must be one line of plain text (no line breaks or control characters)")
    if len(line) > limit:
        _fail(path, f"must be at most {limit} characters (got {len(line)})")
    return line


def _character(path: str, value) -> str:
    """A site's declared character: one of :data:`CHARACTERS`, :data:`DEFAULT_CHARACTER` when absent.

    Matched EXACTLY (surrounding whitespace aside), because the value is a vocabulary word and the
    service schema's enumeration says the same three strings: a differently-spelled value must be
    refused here rather than render the default and leave the operator reading a page that leads
    with figures they did not choose.
    """
    if value is None:
        return DEFAULT_CHARACTER
    text = str(value).strip()
    if text not in CHARACTERS:
        _fail(path, f"'{value}' is not a character; accepted: {', '.join(CHARACTERS)} "
                    f"(absent means '{DEFAULT_CHARACTER}')")
    return text


def _flag(path: str, value, *, default: bool) -> bool:
    """A boolean switch from *value*, *default* when absent; anything but a boolean is refused."""
    if value is None:
        return default
    if not isinstance(value, bool):
        _fail(path, f"must be true or false (got {type(value).__name__})")
    return value


#: a ``%(name)`` sequence in a welcome text: the token between the parens, and the ``s`` it must
#: be followed by — anything else is a placeholder this site does not fill.
_PLACEHOLDER = re.compile(r"%\(([^)]*)\)(s?)")


def _welcome(path: str, value) -> str | None:
    """A site's welcome line: one line of plain text, with ``%(community)s`` as its only placeholder.

    The text contract is :func:`_plain_line`'s, at :data:`MAX_WELCOME_LENGTH`. Every other
    ``%(…)`` sequence is refused, because the substitution is a literal replace of the one token
    and never ``%``-formatting: an unknown placeholder would print on a public page instead of
    failing where it was written. A lone ``%`` is text and stays text.
    """
    text = _plain_line(path, value, limit=MAX_WELCOME_LENGTH)
    if text is None:
        return None
    for match in _PLACEHOLDER.finditer(text):
        if match.group(1) != "community" or match.group(2) != "s":
            _fail(path, f"'{match.group(0)}' is not a placeholder this site fills; the accepted "
                        f"one is '{WELCOME_PLACEHOLDER}'")
    return text


def _sections(path: str, value) -> tuple[str, ...]:
    """The section ids a site shows: a map of id -> boolean, and an id it does not name is ON.

    A map rather than a list, so a section a later release adds is on for every existing site and
    the operator's own file still says what they thought about the ones they switched. An unknown
    id is refused here and by the schema's closed mapping, because both doors (form and Expert
    Mode) exist.
    """
    if value is None:
        return DEFAULT_SECTIONS
    if isinstance(value, (str, bytes)) or not isinstance(value, Mapping):
        _fail(path, f"must be a map of section id -> true/false; known section ids: "
                    f"{', '.join(SECTION_IDS)}")
    for ident, flag in value.items():
        if ident not in SECTION_IDS:
            _fail(f"{path}.{ident}", f"unknown section id; known section ids: {', '.join(SECTION_IDS)}")
        _flag(f"{path}.{ident}", flag, default=True)
    return tuple(ident for ident in SECTION_IDS if value.get(ident, True))


def _metrics(path: str, value) -> tuple[str, ...]:
    """The leaderboard metrics a site offers: a list of ids, every id when absent."""
    return _ids(path, value, default=DEFAULT_METRICS, known=LEADERBOARD_METRICS,
                what="leaderboard metric")


#: preset ids already reported as not shipped, so a second parse of the same file stays quiet
_WARNED_PRESETS: set[tuple[str, str]] = set()


def _shipped_presets() -> tuple[str, ...]:
    """The preset ids this build ships, or ``()`` when the preset registry is not installed.

    Read from the registry module rather than repeated here, so the list of presets and the list of
    sheets have one home. An absent module reads as "this build ships none", which is the honest
    answer and the same shape a shipped preset that was later removed produces.
    """
    try:
        from . import theme_css

        return tuple(theme_css.PRESET_IDS)
    except Exception:  # noqa: BLE001 - a build without the registry must still parse a site
        return ()


def _preset(path: str, value) -> str | None:
    """A site's preset id, or ``None`` when it declares none or the build no longer ships it.

    A preset id that this build does not ship WARNS and falls back to the shipped palette instead
    of refusing: the value was valid when it was written and the product removed it, so a refusal
    would blame the operator for our change — on a page only the console could fix, and the console
    would then refuse to start.
    """
    text = _text(path, value)
    if text is None or not text.strip():
        return None
    preset = text.strip()
    shipped = _shipped_presets()
    if preset in shipped:
        return preset
    marker = (path, preset)
    if marker not in _WARNED_PRESETS:
        _WARNED_PRESETS.add(marker)
        log.warning("%s: '%s' is not a preset this build ships (%s). The site keeps the shipped "
                    "palette; choose a shipped preset on the settings page.",
                    f"sites.{path}", preset,
                    ", ".join(shipped) if shipped else "this build ships none")
    return None



def _mark_side(path: str, value) -> str:
    """A site's ``branding.mark_side``: one of :data:`MARK_SIDES`, the default when absent.

    Matched EXACTLY (surrounding whitespace aside), because the value names a grid placement: a
    differently-spelled value is refused here rather than silently drawing the default.
    """
    if value is None:
        return DEFAULT_MARK_SIDE
    text = str(value).strip()
    if text not in MARK_SIDES:
        _fail(path, f"'{value}' is not a mark side; accepted: {', '.join(MARK_SIDES)} "
                    f"(absent means '{DEFAULT_MARK_SIDE}')")
    return text


def _branding(path: str, block) -> Branding:
    if block is None:
        return Branding()
    if not isinstance(block, Mapping):
        _fail(path, "must be a map")
    for key in block:
        if key not in BRANDING_KEYS and key not in IGNORED_BRANDING_KEYS:
            _fail(f"{path}.{key}", f"unknown key; known keys: {', '.join(BRANDING_KEYS)}")
    title = _text(f"{path}.title", block.get("title"), default=DEFAULT_BRANDING_TITLE)
    title = title or DEFAULT_BRANDING_TITLE
    logo = _text(f"{path}.logo", block.get("logo"))
    # the ignored keys are read by NOTHING: ``copyright`` may still be in an older file, and its
    # value is dropped here rather than refused above.
    return Branding(title=title,
                    community_name=_plain_line(f"{path}.community_name", block.get("community_name"),
                                               limit=MAX_COMMUNITY_NAME_LENGTH),
                    logo=logo or None,
                    welcome=_welcome(f"{path}.welcome", block.get("welcome")),
                    mark_side=_mark_side(f"{path}.mark_side", block.get("mark_side")),
                    footer_links=_footer_links(f"{path}.footer_links", block.get("footer_links")))


def _sheet(path: str, value, config_dir=None) -> str | None:
    """A site's uploaded palette sheet: a LEAF name inside the site's asset folder, checked on disk.

    The leaf grammar is the whole path defence (:data:`SHEET_LEAF_PATTERN`), so a declared value can
    never leave ``<config_dir>/site``. A name whose file is not there is REFUSED when the caller
    knows the config directory: the route would answer the default palette with only a log line, so
    the mistake is reported where the operator can fix it — the same rule every declared asset file
    follows. Without a config directory the file check cannot run and only the leaf is validated.
    """
    text = _text(path, value)
    if text is None or not text.strip():
        return None
    leaf = text.strip()
    if SHEET_LEAF_PATTERN.match(leaf) is None:
        _fail(path, f"'{leaf}' is not a file name: a sheet is named by its file inside the site's "
                    f"asset folder ('{ASSET_DIR}/'), with letters, digits, '.', '-', '_' and no "
                    f"folder part")
    if config_dir:
        resolved = sheet_path(leaf, config_dir)
        if not resolved.is_file():
            _fail(path, f"'{leaf}' does not exist in the site's asset folder "
                        f"('{ASSET_DIR}/' under the configuration directory); upload the sheet on "
                        f"the Site settings page")
    return leaf


def _theme(path: str, block, config_dir=None) -> Theme:
    if block is None:
        return Theme()
    if not isinstance(block, Mapping):
        _fail(path, "must be a map")
    for key in block:
        if key not in THEME_KEYS:
            _fail(f"{path}.{key}", f"unknown key; known keys: {', '.join(THEME_KEYS)}")
    declared = [name for name in THEME_SOURCES if block.get(name) not in (None, "")]
    if len(declared) > 1:
        _fail(path, f"{' and '.join(f'sites.{path}.{name}' for name in declared)} are all set: "
                    f"{', '.join(THEME_SOURCES)} are three spellings of one intent, so at most "
                    f"one of them may be set")
    expert = _text(f"{path}.expert", block.get("expert"))
    if expert is not None:
        if len(expert) > MAX_EXPERT_LENGTH:
            _fail(f"{path}.expert",
                  f"must be at most {MAX_EXPERT_LENGTH} characters (got {len(expert)})")
        expert = expert.strip() or None
    return Theme(preset=_preset(f"{path}.preset", block.get("preset")),
                 sheet=_sheet(f"{path}.sheet", block.get("sheet"), config_dir),
                 expert=expert,
                 light=_flag(f"{path}.light", block.get("light"), default=False))


def parse_sites(block, *, known_roles: Iterable[str] = (), config_dir=None) -> "SiteCatalog":
    """Parse and validate a ``sites:`` block. Every refusal names the offending key.

    *known_roles* is the ``managed_by`` token set a ``group_role`` must name
    (:func:`known_managed_by`); it is an input, so this function needs no file of its own and a
    test can drive it directly. *config_dir* is the bot's configuration directory: it is where a
    declared ``theme.sheet`` is looked for, and without it that file check does not run.
    """
    if block is None or block == {}:
        return SiteCatalog()
    if not isinstance(block, Mapping):
        _fail("", "must be a map of site name -> site block")

    roles = frozenset(str(role) for role in known_roles or ())
    sites: list[Site] = []
    by_slug: dict[str, str] = {}
    for key, raw in block.items():
        if not isinstance(key, str) or not key.strip():
            _fail("", f"a site needs a name (got {key!r})")
        path = str(key)
        if not isinstance(raw, Mapping):
            _fail(path, "must be a map (the site's own block)")
        for declared in raw:
            if declared not in SITE_KEYS:
                _fail(f"{path}.{declared}", f"unknown key; known keys: {', '.join(SITE_KEYS)}")

        is_root = key == ROOT_KEY
        group_role = raw.get("group_role")
        if is_root:
            if group_role not in (None, ""):
                _fail(f"{path}.group_role",
                      "the root site shows the servers that declare no managed_by, so it takes "
                      "no group_role; give the token to a site of its own")
            group_role = None
            slug = _text(f"{path}.slug", raw.get("slug"), default="") or ""
            if slug:
                _fail(f"{path}.slug",
                      f"the root site is served at the site root, so its slug must be empty "
                      f"(got '{slug}')")
        else:
            if group_role in (None, ""):
                _fail(f"{path}.group_role",
                      "a site other than root needs the managed_by token it shows (the only site "
                      "with no token is 'root')")
            group_role = str(group_role).strip()
            if group_role not in roles:
                _fail(f"{path}.group_role",
                      f"'{group_role}' matches no managed_by value in servers.yaml, so this site "
                      f"would serve nobody and report nothing"
                      + (f"; declared: {', '.join(sorted(roles))}" if roles else
                         "; servers.yaml declares none"))
            slug = _text(f"{path}.slug", raw.get("slug"), default=key) or key
            if not SLUG_PATTERN.match(slug):
                _fail(f"{path}.slug",
                      f"'{slug}' is not URL-safe (lowercase letters, digits and inner dashes, "
                      f"starting with a letter or digit)")
            if slug in RESERVED_SLUGS:
                _fail(f"{path}.slug",
                      f"'{slug}' collides with a path or prefix the shell already owns "
                      f"(reserved: {', '.join(sorted(RESERVED_SLUGS))})")
        if slug in by_slug:
            _fail(f"{path}.slug",
                  f"'{slug}' is already the slug of site '{by_slug[slug]}'; slugs must be unique")
        by_slug[slug] = str(key)

        branding = _branding(f"{path}.branding", raw.get("branding"))
        theme = _theme(f"{path}.theme", raw.get("theme"), config_dir)
        nav = _ids(f"{path}.nav", raw.get("nav"), default=DEFAULT_NAV, known=NAV_PAGE_IDS,
                   what="nav page id")
        tiles = _ids(f"{path}.tiles", raw.get("tiles"), default=DEFAULT_TILES, known=TILE_IDS,
                     what="tile id")
        lang = _text(f"{path}.lang", raw.get("lang"), default=DEFAULT_LANG) or DEFAULT_LANG
        supported = _supported_languages()
        if supported and lang not in supported:
            _fail(f"{path}.lang",
                  f"'{lang}' has no web UI catalog, so the site would render English silently; "
                  f"supported: {', '.join(supported)}")
        character = _character(f"{path}.character", raw.get("character"))
        sections = _sections(f"{path}.sections", raw.get("sections"))
        metrics = _metrics(f"{path}.metrics", raw.get("metrics"))
        enabled = _flag(f"{path}.enabled", raw.get("enabled"), default=True)
        avatar = _flag(f"{path}.avatar", raw.get("avatar"), default=False)
        sites.append(Site(key=str(key), slug=slug, group_role=group_role, branding=branding,
                          theme=theme, nav=nav, tiles=tiles, sections=sections, metrics=metrics,
                          lang=lang, character=character, enabled=enabled, avatar=avatar))
    return SiteCatalog(tuple(sites))


# ---------------------------------------------------------------------------- the catalog

@dataclass(frozen=True, slots=True)
class SiteCatalog:
    """Every configured site, and the ONE resolution function.

    Empty (``sites == ()``) means the block is absent: no site is configured, and every segment —
    including the empty one — resolves to :class:`SiteNotFound`.
    """

    sites: tuple[Site, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.sites)

    @property
    def root(self) -> Site | None:
        """The site with no group token, or ``None`` when the block declares none."""
        for site in self.sites:
            if site.is_root:
                return site
        return None

    @property
    def slugs(self) -> tuple[str, ...]:
        """Every configured slug, in declaration order (the root site's ``""`` included)."""
        return tuple(site.slug for site in self.sites)

    def site_for_slug(self, slug: str) -> Site | None:
        """The site served under *slug*, or ``None``."""
        for site in self.sites:
            if site.slug == slug:
                return site
        return None

    def resolve(self, first: str | None, *, servers=None) -> SiteContext:
        """The ONE resolution: a request's first path segment -> the site context it renders with.

        ``first`` is the first path segment (see :func:`first_segment`). Empty means "no site
        segment", which resolves to the root site; a segment that names no configured site raises
        :class:`SiteNotFound` — it never falls back to the root site. ``servers`` is the server
        registry to take the membership from; absent means the live one (:func:`live_servers`).
        """
        segment = str(first or "").strip("/")
        if segment:
            site = self.site_for_slug(segment)
            if site is None:
                raise SiteNotFound(f"no site is served under '/{segment}'")
        else:
            site = self.root
            if site is None:
                raise SiteNotFound("no root site is configured")
        return site.context(live_servers() if servers is None else servers)


def first_segment(path: str | None) -> str:
    """The first non-empty segment of *path* — ``''`` when the path has none."""
    for part in str(path or "").split("/"):
        if part:
            return part
    return ""


# ------------------------------------------------------------------- the startup entry points

def site_file(config_dir) -> Path:
    """The file the site settings page owns: ``<config_dir>/site.yaml``."""
    return Path(config_dir or ".") / SITE_FILE


def load_site_block(config_dir) -> Mapping | None:
    """The ``sites:`` block of ``config/site.yaml``, or ``None`` when the file does not exist.

    The file is the site settings page's own: it carries one top-level ``sites:`` key and nothing
    else the page writes. An unreadable file raises :class:`SiteConfigError` naming it rather than
    letting the shell start on a guess.
    """
    if not config_dir:
        return None
    path = site_file(config_dir)
    if not path.is_file():
        return None
    try:
        from core.utils.yaml_helper import new_yaml

        document = new_yaml().load(path.read_text(encoding="utf-8")) or {}
    except Exception as ex:  # noqa: BLE001 - the refusal names the file, not a traceback
        raise SiteConfigError(f"{path}: could not be read: {ex}") from ex
    if not isinstance(document, Mapping):
        raise SiteConfigError(f"{path}: must be a map carrying a 'sites:' block")
    block = document.get("sites")
    if block is not None and not isinstance(block, Mapping):
        raise SiteConfigError(f"{path}: 'sites:' must be a map of site name -> site block")
    return block


def load_sites(config, *, config_dir=None) -> SiteCatalog:
    """Parse and validate the ``sites:`` block ONCE, at startup, from the first home that answers.

    ``config/site.yaml`` answers whenever it exists — it is then the only source, whatever the
    legacy block carries. Without it, ``config`` (the service's own ``webservice.yaml`` block,
    ``Service.get_config()``) answers from its ``sites:`` key, with one warning naming both files.
    ``config_dir`` is the bot's config directory: it holds the site file and it is where
    ``servers.yaml`` is read for the token set a ``group_role`` must name. A block that cannot be
    served raises :class:`SiteConfigError`, which is what keeps the shell from installing.
    """
    path = site_file(config_dir) if config_dir else None
    if path is not None and path.is_file():
        block = load_site_block(config_dir)
    else:
        block = config.get("sites") if isinstance(config, Mapping) else None
        if block:
            target = str(path) if path is not None else f"config/{SITE_FILE}"
            log.warning("Sites: the sites block is read from config/services/webservice.yaml "
                        "because %s does not exist. The site settings page saves %s; it becomes "
                        "the only source once it exists.", target, target)
    if not block:
        return SiteCatalog()
    declared = load_servers_config(config_dir)
    return parse_sites(block, known_roles=known_managed_by(declared), config_dir=config_dir)


def install_sites(app, config, *, config_dir=None) -> SiteCatalog:
    """Validate the block and store the catalog on *app*, once per application install."""
    catalog = load_sites(config, config_dir=config_dir)
    setattr(app.state, STATE_ATTR, catalog)
    if catalog:
        log.info("Sites: %d site(s) configured (%s).", len(catalog.sites),
                 ", ".join(f"'{site.key}' at '{site.prefix or '/'}'" for site in catalog.sites))
    return catalog


def sites_for(app) -> SiteCatalog:
    """The catalog stored on *app*; an empty one when the application carries none."""
    stored = getattr(getattr(app, "state", None), STATE_ATTR, None)
    return stored if stored is not None else SiteCatalog()


# ------------------------------------------- the content overlay (per request, no reload)

#: the ``Site`` fields a request may see change without the console reloading. Everything else —
#: which sites exist, their keys, slugs and switches — is installed structure.
CONTENT_FIELDS: tuple[str, ...] = ("branding", "character", "avatar", "nav", "tiles", "sections",
                                   "metrics", "lang", "theme")

#: the ``Site`` fields a route was registered for: a difference in any of them needs a reload.
STRUCTURAL_FIELDS: tuple[str, ...] = ("slug", "group_role", "enabled")


def structure_of(catalog: "SiteCatalog") -> dict[str, tuple[Any, ...]]:
    """The projection a running console cannot change without a reload: key -> the route's facts."""
    return {site.key: tuple(getattr(site, field) for field in STRUCTURAL_FIELDS)
            for site in catalog.sites}


@dataclass(frozen=True, slots=True)
class LiveCatalog:
    """The catalogue a request renders with, and whether the file's structure moved.

    ``catalog`` is the INSTALLED catalogue with each site's content fields replaced from the file.
    ``reload_required`` is true when the file's structural projection differs from the installed
    one: then nothing of the file is applied — ``catalog`` is the installed catalogue untouched —
    because a route exists only for what was installed, and a half-applied structure would answer
    404s mid-session.
    """

    catalog: "SiteCatalog"
    reload_required: bool = False


#: the last ``(path, mtime, size, installed identity)`` looked at and what it produced
_cache: tuple[tuple, LiveCatalog] | None = None
_cache_lock = threading.Lock()


def live_catalog(app) -> "LiveCatalog":
    """The catalogue a request reads: installed structure, content from the site file.

    Re-parsed only when the file's ``(mtime_ns, size)`` moved — the parse and the pykwalify-free
    validation both cost more than a public page load should. A file that will not parse is
    reported once per change and answers the installed catalogue with ``reload_required``: a public
    page must not 500 over a config edit, and the operator has to see that a reload is owed.
    """
    global _cache
    installed = sites_for(app)
    config_dir = getattr(getattr(app, "state", None), "webui_config_dir", None)
    if not config_dir:
        return LiveCatalog(installed)
    path = site_file(config_dir)
    try:
        stat = path.stat()
    except OSError:
        return LiveCatalog(installed)
    marker = (str(path), stat.st_mtime_ns, stat.st_size, id(installed))
    with _cache_lock:
        if _cache is not None and _cache[0] == marker:
            return _cache[1]
        value = _overlay(installed, config_dir=config_dir, path=path)
        _cache = (marker, value)
    return value


def _overlay(installed: "SiteCatalog", *, config_dir, path: Path) -> "LiveCatalog":
    """Build the overlay for one file version; the caller holds the cache lock."""
    try:
        fresh = parse_sites(load_site_block(config_dir),
                            known_roles=known_managed_by(load_servers_config(config_dir)),
                            config_dir=config_dir)
    except SiteConfigError as ex:
        log.warning("Sites: %s could not be applied (%s). The installed sites keep serving; a "
                    "reload is owed once the file is fixed.", path, ex)
        return LiveCatalog(installed, reload_required=True)
    if structure_of(fresh) != structure_of(installed):
        # SAID OUT LOUD, ONCE PER FILE VERSION: the routes were built at install time, so a
        # structure this file moves is served by nobody until the console reloads. Silence here is
        # what made the operator's saved edit look like it had done nothing at all.
        log.warning("Sites: %s changes the sites this console serves (a site, a slug or a switch). "
                    "The installed sites keep serving; a reload is owed before the file's "
                    "structure is in force.", path)
        return LiveCatalog(installed, reload_required=True)
    changes = {site.key: site for site in fresh.sites}
    sites = tuple(replace(site, **{field: getattr(changes[site.key], field)
                                   for field in CONTENT_FIELDS})
                  for site in installed.sites)
    return LiveCatalog(SiteCatalog(sites))
