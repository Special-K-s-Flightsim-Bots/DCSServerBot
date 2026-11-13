"""The public site's page tree: one route per configured site per page, and the shell they share.

WHERE THE SITE'S ADDRESSES COME FROM. A site is served at its own prefix — ``""`` for the root site
(``/``), ``"/<slug>"`` for every other (``/group1``). A page therefore declares a path INSIDE the
site (``""`` is the site's root, ``"/rankings"`` is one step below it) and :func:`site_url` puts
the prefix in front, so no template or page module spells a site root and a second site cannot be
served at the first one's address.

THE NAV IS DECLARED HERE, ONCE. :data:`NAV_PATHS` maps a configured nav page id to its path and
:data:`NAV_LABELS` to its English msgid; :data:`SERVED_NAV_IDS` names the ids whose page this build
actually serves. A site's ``nav:`` list selects and orders the entries, and an id with no served page
renders NO entry: a menu link that answers 404 is worse than an absent one. A page that joins the
site adds its id to :data:`SERVED_NAV_IDS` in the same commit that adds its route.

REGISTRATION. :func:`register` builds one router holding every site's routes and hands it to the
registrar under the owner :data:`OWNER` with an EMPTY prefix (the installation's fixed surface, which
a mount never moves) and the ``public`` capability: the site's pages are readable with no session, no
scope and no capability. The registrar's own console prefix decides nothing here — that is the point,
since the console and the site live at different addresses.

NOTHING HERE READS THE DATABASE. Every figure belongs to :mod:`services.webservice.site_reads`; this
module only builds addresses, declares what is offered, and renders the page a module hands it.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from . import i18n, mount, permissions, session, sites, theme_css

__all__ = [
    "OWNER",
    "NAV_LABELS",
    "NAV_PATHS",
    "SERVED_NAV_IDS",
    "LOGO_PATH",
    "AVATAR_PATH",
    "AVATAR_MARK_SIZE",
    "uploaded_mark",
    "GREETING",
    "GREETING_MARK",
    "LANGUAGE_PATH",
    "CHROME_PREVIEW_PATH",
    "PREVIEW_TEMPLATE",
    "DRAFT_TTL_SECONDS",
    "DRAFT_MAX_ENTRIES",
    "DRAFT_MAX_BYTES",
    "DRAFT_UNMOVED_FIELDS",
    "Draft",
    "draft_block",
    "draft_bytes",
    "draft_context",
    "draft_count",
    "draft_overlay",
    "draft_site",
    "demo_context",
    "forget_draft",
    "forget_session_drafts",
    "remember_draft",
    "reset_drafts",
    "THEME_SHEET_PATH",
    "ME_PATH",
    "PLAYER_PATH",
    "SEARCH_PATH",
    "SEARCH_JSON_PATH",
    "catalog_from_file",
    "chip",
    "community_name",
    "config_guild_name",
    "discord_guild_name",
    "hero_greeting",
    "hero_welcome",
    "HERO_MARK_SIZE",
    "HERO_WELCOME",
    "identity",
    "logo_url",
    "nav_items",
    "page_modules",
    "player_url",
    "pool",
    "public_site_href",
    "reaches_console",
    "register",
    "rebuild",
    "render_site_page",
    "resolve_site",
    "safe_return",
    "sign_in_href",
    "sign_out_context",
    "signed_in",
    "site_url",
    "theme_sheet_url",
]

log = logging.getLogger(__name__)

#: the owner name every site route is registered under.
OWNER = "site"

#: nav page id -> its path inside a site. ``""`` is the site's own root.
#:
#: STAY OFF THE LEGACY REST SURFACE. ``plugins/restapi/commands.py`` registers that surface at its
#: configured ``prefix:``, and that key defaults to ``""`` — the site root — so on a root site the
#: API and the site share one address space. A site page is a LOW-PRIORITY route (see
#: :mod:`services.webservice.registry`) while the plugin's route is a NORMAL one, so on a shared path
#: the API WINS and the page answers its auth refusal instead of rendering. Every address declared
#: here therefore stays off that surface: the leaderboard page is at ``/rankings`` rather than the
#: API's ``/leaderboard``, and the squadrons page at ``/squads`` rather than ``/squadrons``.
#: ``tests/test_webui_site_public_route.py`` reads the plugin's own route list and fails on any
#: declaration here that collides with it, served or not.
NAV_PATHS: dict[str, str] = {
    "overview": "",
    "leaderboard": "/rankings",
    "squadrons": "/squads",
    "players": "/players",
}

#: nav page id -> the English msgid its entry renders (translated at render through the request's
#: ``_``), so the menu and the page title carry one string each.
NAV_LABELS: dict[str, str] = {
    "overview": i18n._("Overview"),
    "leaderboard": i18n._("Leaderboard"),
    "squadrons": i18n._("Squadrons"),
    "players": i18n._("Pilots"),
}

#: the nav page ids whose page THIS build serves. An id absent here is not offered, whatever the
#: site's ``nav:`` list says.
SERVED_NAV_IDS: tuple[str, ...] = ("overview", "leaderboard", "players")

#: the path (inside the site) of one pilot's profile; its UCID is appended as one path segment.
PLAYER_PATH = "/player"

#: the path (inside the site) of the viewer's own statistics. NOT a nav page: it belongs to one
#: identity and the menu is the site's, so it is reached from the shell's own chip.
ME_PATH = "/me"

#: the path (inside the site) of the pilot search — the ``players`` nav entry's own address, so the
#: top bar's search box submits to the page the menu already links.
SEARCH_PATH: str = NAV_PATHS["players"]

#: the path (inside the site) of the search box's own JSON: the suggestions the top bar asks for as
#: the visitor types. Its own address, not the search page's — the page answers HTML and this one
#: answers ``{"pilots": [...]}``.
SEARCH_JSON_PATH = "/search.json"

#: the path (inside the site) of the site's palette sheet, served as CSS.
THEME_SHEET_PATH = "/theme.css"

#: the path (inside the site) of a declared ``branding.logo``, served as an image.
LOGO_PATH = "/logo.png"

#: the path (inside the site) of the mark the operator UPLOADED — the site's own mark once one is
#: uploaded, and the address :func:`logo_url` prefers.
AVATAR_PATH = "/avatar.png"

#: the pixel size of the uploaded mark the site's top bar is served. The bar's mark box is 32 CSS
#: pixels tall (``--mark-h`` in ``site.css``), so 64 is the 2x asset a high-density screen draws and
#: the 128 file would be four times the bytes for no visible gain. It is ALSO the route's default,
#: so the bar's request carries no query string.
AVATAR_MARK_SIZE: int = 64

#: the pixel size of the uploaded mark the hero is served: the ladder's largest drawn size (128 CSS
#: pixels) at 2x. The hero's address names it, and the route answers only an :data:`AVATAR_SIZES`
#: member, so no request value can name a file an upload never wrote.
HERO_MARK_SIZE: int = 256

#: the path (inside the site) of the site's language switch — the SAME segment the console's
#: control uses, so the address is ``<site root>/language`` and only its prefix differs.
LANGUAGE_PATH = i18n.LANGUAGE_PATH

#: the path (inside the site) of the settings card's preview: the site's OWN document, the overview
#: hero for its content and nothing else. Same-origin with the console, so the card's binding reaches
#: ``contentDocument`` with no ``postMessage``. It is NOT a nav page: nothing links to it.
CHROME_PREVIEW_PATH = "/chrome-preview"

#: the image types a declared logo may be served as. SVG IS DELIBERATELY ABSENT: an SVG can carry
#: script and would then run on this page's origin, and an operator-set file is not trusted content
#: on a public page. A logo with any other suffix renders no mark.
LOGO_SUFFIXES: dict[str, str] = {".png": "image/png", ".jpg": "image/jpeg",
                                 ".jpeg": "image/jpeg", ".gif": "image/gif",
                                 ".webp": "image/webp"}

#: the hero's greeting, ONE msgid with the community's name as its placeholder so a locale can put
#: the name where its own grammar wants it. The Python call is the extraction marker.
GREETING: str = i18n._("Welcome to %(community)s")

#: the placeholder stand-in :func:`hero_greeting` substitutes for the name, so the sentence can be
#: split around it and the name printed on its own line. U+001F is a control character that no
#: catalog entry and no community name carries.
GREETING_MARK: str = "\x1f"

#: the hero's paragraph when the site declares no ``branding.welcome``: the shipped sentence, ONE
#: msgid so a locale translates it once. The Python call is the extraction marker.
HERO_WELCOME: str = i18n._("Our servers, our pilots, our missions. Every sortie flown here is "
                           "written down — who flew, what they flew, and how it ended.")


# --------------------------------------------------------------------------------- addresses

def site_url(site: Any, path: str = "") -> str:
    """*path* inside *site*'s tree: the site's prefix in front, its own root at the prefix itself.

    ``site`` is a :class:`~services.webservice.sites.Site` or a ``SiteContext`` — both carry
    ``prefix``. The root site's empty path is ``"/"`` (not ``""``), so every link is absolute.
    """
    prefix = str(getattr(site, "prefix", "") or "")
    if not path:
        return prefix or "/"
    return prefix + path


def player_url(site: Any, ucid: Any) -> str:
    """The address of one pilot's profile: :data:`PLAYER_PATH` with the UCID as one encoded segment."""
    return site_url(site, f"{PLAYER_PATH}/{quote(str(ucid or ''), safe='')}")


def nav_items(context: Any, active: str = "") -> list[dict]:
    """The site's menu entries, in the site's own order, as data for the shell template.

    Driven by the site's configured ``nav`` list and filtered by :data:`SERVED_NAV_IDS`: an id with
    no page is dropped, so the menu never advertises a page this build does not serve. Each entry
    carries the English msgid (the template translates it) and the address built from the site's
    prefix.
    """
    entries: list[dict] = []
    for page_id in getattr(context, "nav", ()) or ():
        if page_id not in SERVED_NAV_IDS or page_id not in NAV_PATHS:
            continue
        entries.append({
            "page": page_id,
            "label": NAV_LABELS[page_id],
            "href": site_url(context, NAV_PATHS[page_id]),
            "active": page_id == active,
        })
    return entries


def public_site_href(app: Any) -> str | None:
    """Where the console's "open the public site" link leads, or ``None`` when it leads nowhere.

    The INSTALLED catalogue decides (a route exists only for what was installed), and only ENABLED
    sites count: a site switched off serves nothing, so a link to it would answer 404.

    * the enabled root site is the answer, at ``/`` — it owns the site root;
    * with no enabled root site, exactly ONE enabled group site is the answer, at its own prefix;
    * several enabled group sites and no root site answer ``None``: the console cannot guess which
      one the operator means, and a link that picks one arbitrarily would be a lie about the rest;
    * nothing enabled answers ``None`` — the console is then serving ``/`` itself.
    """
    enabled = [site for site in sites.sites_for(app).sites if getattr(site, "enabled", True)]
    for site in enabled:
        if site.is_root:
            return site_url(site)
    if len(enabled) == 1:
        return site_url(enabled[0])
    return None


def theme_sheet_url(context: Any) -> str | None:
    """The address of the site's theme sheet, or ``None`` when the site declares no palette.

    A palette is declared by ANY of the three spellings — a preset, the uploaded sheet, the expert
    block — because all three are served from the one address. A site that declares none renders the
    shipped palette in ``site.css`` and needs no second request, so it carries no link.
    """
    theme = getattr(context, "theme", None)
    if theme is None or not any(getattr(theme, key, None)
                                for key in ("preset", "sheet", "expert")):
        return None
    return site_url(context, THEME_SHEET_PATH)


def uploaded_mark(context: Any, config_dir, size: int = AVATAR_MARK_SIZE) -> Path | None:
    """The uploaded mark's file of *size* pixels, or ``None`` when the site has no uploaded one.

    THE one existence rule — the site's ``avatar`` flag AND the file the size names. A flag whose
    file is gone draws nothing, so the site falls back instead of serving a broken image, and the
    resolver and the route can never disagree about which files are there. *size* is an
    :data:`services.webservice.sites.AVATAR_SIZES` member; the bar asks for the default 64, the hero
    for 256.
    """
    if not getattr(context, "avatar", False) or not config_dir:
        return None
    try:
        path = sites.avatar_path(str(getattr(context, "slug", "") or ""), size, config_dir)
    except ValueError:  # pragma: no cover - the sizes and the directory are both ours
        return None
    return path if path.is_file() else None


def logo_url(context: Any, config_dir=None, size: int = AVATAR_MARK_SIZE) -> str | None:
    """The address of the site's own mark: the UPLOADED one first, then ``branding.logo``.

    ``None`` when the site has neither, and the templates then draw the shipped mark. The uploaded
    mark wins because it is the one the operator just chose; ``branding.logo`` stays the fallback so
    a site that names a file and uploads nothing keeps drawing that file. *size* chooses which
    uploaded file the address asks for: the default 64 leaves the bar's request byte-identical, and
    any other :data:`services.webservice.sites.AVATAR_SIZES` member is named in a ``?size=`` query.
    """
    if uploaded_mark(context, config_dir, size) is not None:
        address = site_url(context, AVATAR_PATH)
        return address if size == AVATAR_MARK_SIZE else f"{address}?size={size}"
    branding = getattr(context, "branding", None)
    declared = str(getattr(branding, "logo", "") or "")
    if not declared:
        return None
    if Path(declared).suffix.lower() not in LOGO_SUFFIXES:
        return None
    return site_url(context, LOGO_PATH)


# --------------------------------------------------------------------------- the settings card's mock

def _demo_sheet(resolved: Any, config_dir) -> str:
    """The site's palette as a DATA URI — a mock has no ``<site>/theme.css`` address to ask.

    Composed by the ONE resolver the live route uses (:func:`services.webservice.theme_css.compose`),
    so the mock's colours are the colours the site will be served. A theme that names two sources is
    refused by the loader; a mock that meets one anyway draws the default palette instead of failing.
    """
    try:
        text = theme_css.compose(getattr(resolved, "theme", None), config_dir)
    except ValueError:
        text = theme_css.preset_sheet(theme_css.DEFAULT_PRESET)
    return "data:text/css;base64," + base64.b64encode(text.encode("utf-8")).decode("ascii")


def _demo_mark(resolved: Any, config_dir, size: int) -> str | None:
    """The site's own mark as a DATA URI: the file the site WOULD draw, carried in the document.

    The uploaded file wins and ``branding.logo`` is the fallback, exactly as :func:`logo_url`
    resolves them, and only a suffix :data:`LOGO_SUFFIXES` carries is drawn — an SVG is not a mark on
    this page's origin there either. ``None`` draws the product's mark, which is a static file.
    """
    path = uploaded_mark(resolved, config_dir, size)
    content_type = "image/png"
    if path is None:
        declared = str(getattr(getattr(resolved, "branding", None), "logo", "") or "")
        content_type = LOGO_SUFFIXES.get(Path(declared).suffix.lower()) if declared else None
        if content_type is None or not config_dir:
            return None
        path = sites.asset_path(declared, config_dir)
        if not path.is_file():
            return None
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    return f"data:{content_type};base64,{base64.b64encode(payload).decode('ascii')}"


# --------------------------------------------------------------------------------- the name

def discord_guild_name() -> str | None:
    """The connected bot's first guild name, or ``None``.

    The name is read through the service registry (``BotService.bot.guilds[0].name``), never from a
    second parse of anything. A process with no service bus, no bot object, an empty guild list, or
    a guild whose name is blank or not a string declares no name here — every case answers ``None``
    and never raises, so the caller can fall through to its next source.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.bot.service import BotService

        bot = getattr(ServiceRegistry.get(BotService), "bot", None)
        guilds = getattr(bot, "guilds", None)
        if not guilds:
            return None
        name = getattr(guilds[0], "name", None)
    except Exception:  # noqa: BLE001 - a missing bus, bot or guild is "no name", not an error
        return None
    return name.strip() if isinstance(name, str) and name.strip() else None


def config_guild_name(config_dir) -> str | None:
    """``main.yaml``'s ``guild_name`` — the name the no-Discord setup writes — or ``None``.

    Read defensively: no config directory, a missing or unreadable file, a missing key, or a value
    that is not a non-blank string all answer ``None``.
    """
    if not config_dir:
        return None
    try:
        from ruamel.yaml import YAML

        data = YAML().load((Path(config_dir) / "main.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - any read or parse failure means "not configured"
        return None
    value = data.get("guild_name") if isinstance(data, Mapping) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def community_name(resolved: Any, *, config_dir=None) -> str:
    """The community's name as the site greets it — ONE precedence, in ONE place.

    1. ``branding.community_name`` when the site declares it;
    2. the connected Discord guild's name (:func:`discord_guild_name`);
    3. ``main.yaml``'s ``guild_name`` (:func:`config_guild_name`);
    4. ``branding.title``, so a site always greets a visitor with something.

    Every step but the last is a guarded read that answers ``None`` rather than raising, so a
    bus-less process, an empty guild list, a blank guild name or a non-string config value falls
    through instead of failing the render. The answer is always a non-blank plain string.
    """
    declared = getattr(getattr(resolved, "branding", None), "community_name", None)
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    for candidate in (discord_guild_name(), config_guild_name(config_dir)):
        if candidate:
            return candidate
    title = getattr(getattr(resolved, "branding", None), "title", "")
    return title.strip() if isinstance(title, str) and title.strip() else sites.DEFAULT_BRANDING_TITLE


def hero_greeting(request: Request, name: str) -> dict[str, str]:
    """The hero's headline split around the community name, as plain strings.

    ONE translated message, ``"Welcome to %(community)s"``, with the name as its placeholder: the
    placeholder is filled with a sentinel here, so the locale decides where the name sits in the
    sentence while the page prints it on a line of its own in the accent colour. ``lead`` and
    ``tail`` are the text the locale put before and after the name; a catalog that dropped the
    placeholder answers the name alone.
    """
    message = i18n.translate(request, GREETING, community=GREETING_MARK)
    lead, found, tail = message.partition(GREETING_MARK)
    if not found:
        return {"lead": "", "name": name, "tail": ""}
    return {"lead": lead.strip(), "name": name, "tail": tail.strip()}


def hero_welcome(request: Request, resolved: Any, community: str) -> str:
    """The hero's paragraph: the site's ``branding.welcome`` when set, else the shipped sentence.

    ``%(community)s`` is substituted HERE, in Python, with the resolved community name — a literal
    replace of the one token the parser allows, never ``%``-formatting. The result is a plain string
    the template prints ESCAPED, so neither the welcome nor a name carrying markup can become
    markup on a public page.
    """
    declared = getattr(getattr(resolved, "branding", None), "welcome", None)
    if isinstance(declared, str) and declared.strip():
        return declared.replace(sites.WELCOME_PLACEHOLDER, community)
    return i18n.translate(request, HERO_WELCOME)


# --------------------------------------------------------------------------------- identity

def identity(request: Request) -> Any | None:
    """This request's resolved :class:`~services.webservice.auth.Identity`, or ``None``.

    An installation with no identity layer, no session, or a backend that refuses is anonymous.
    The call happens once per render — :func:`chip` and the pages share this answer instead of
    authenticating a second time.
    """
    manager = getattr(getattr(request.app, "state", None), "webui_auth", None)
    if manager is None:
        return None
    try:
        return manager.authenticate(request)
    except Exception:  # noqa: BLE001 - a broken identity layer is an anonymous visitor, not an error
        log.debug("Site: the identity layer failed while rendering a public page; rendering "
                  "anonymously.")
        return None


def signed_in(request: Request) -> bool:
    """Whether this request carries a resolvable identity — the only thing the site's chip needs.

    Nothing else on a public page depends on the answer: every page renders for an anonymous
    visitor too.
    """
    return identity(request) is not None


def reaches_console(request: Request, who: Any) -> bool:
    """Whether *who* may open the console's landing page — the SAME rule its route is gated by.

    ``dashboard.view`` is the console's root, so a viewer the dashboard would refuse is offered no
    ``Console`` link. The role half is read from the resolved identity, the manager half from the
    installed resolver: ``permissions.allows`` is the one reader, never a second spelling of the
    rule here.
    """
    from .pages import dashboard

    return permissions.allows(dashboard.DASHBOARD_CAPABILITY, getattr(who, "roles", ()),
                              manager=permissions.manages_console(request))


def sign_out_context(request: Request) -> dict[str, str]:
    """The sign-out FORM of the site's own record: path, field name and the session's token.

    A form and not a link: the logout route accepts POST only, so a link would let any page sign
    the visitor out with one image tag.
    """
    from . import session
    from .auth import routes as auth_routes

    return {"path": auth_routes.LOGOUT_PATH, "csrf_field": session.CSRF_FIELD,
            "csrf_token": session.get_csrf_token(request)}


def chip(request: Request, resolved: Any, who: Any = ...) -> dict | None:
    """The shell's identity chip: the viewer's name, their page and a Console link when allowed.

    ``None`` for an anonymous visitor — the site's top bar then carries no identity at all. The
    console link is present only when :func:`reaches_console` says the roles reach it, so a member
    without a console role is never offered a door that answers 403.
    """
    if who is ...:
        who = identity(request)
    if who is None:
        return None
    console = mount.url("/", app=request.app) if reaches_console(request, who) else None
    return {"name": getattr(who, "shown_name", "") or "",
            "me_href": site_url(resolved, ME_PATH),
            "console_href": console}


def safe_return(request: Request, resolved: Any, value: Any) -> str:
    """*value* as a return target inside THIS site, or the site's root.

    The value arrives from a query string, so it is attacker-controlled: returning a visitor to it
    unchecked is an open redirect. A target is refused (the site root is returned instead) when it
    is not a rooted path, is protocol-relative, carries a backslash or a control character, or
    lands inside the console's mount or another site's prefix. Inside this site's own prefix it is
    returned unchanged, query string included.
    """
    home = site_url(resolved)
    text = "" if value is None else str(value)
    cleaned = "".join(char for char in text
                      if not unicodedata.category(char).startswith("C"))
    if not cleaned.startswith("/") or cleaned.startswith("//") or "\\" in cleaned:
        return home
    target = cleaned.split("?", 1)[0].split("#", 1)[0]
    console = mount.console_prefix(getattr(request, "app", None))
    if console and (target == console or target.startswith(console + "/")):
        return home
    prefix = str(getattr(resolved, "prefix", "") or "")
    # THE INSTALLED CATALOGUE, on purpose: a prefix is structure, and a route exists only for what
    # was installed. The live overlay never moves one, so sites_for is the right reader here.
    for other in sites.sites_for(request.app).sites:
        other_prefix = str(getattr(other, "prefix", "") or "")
        if other_prefix and other_prefix != prefix \
                and (target == other_prefix or target.startswith(other_prefix + "/")):
            return home
    if prefix and not (target == prefix or target.startswith(prefix + "/")):
        return home
    return cleaned


def sign_in_href(request: Request, resolved: Any, value: Any = "") -> str | None:
    """The sign-in control's address, or ``None`` when the installation enables no door.

    Discord-only installations go STRAIGHT into the OAuth flow, through the existing start route —
    no second callback, no second cookie. An installation that also offers a password form lands
    on the login page instead, which renders the doors the configuration enabled. Either way the
    target is :func:`safe_return`'s answer, and the surface the visitor returns into is this site's
    prefix, so the door cannot hand them to the console or to another group.
    """
    from .auth import discord_oauth, routes as auth_routes

    doors = auth_routes.login_doors(request)
    if doors.none:
        return None
    target = quote(safe_return(request, resolved, value), safe="/")
    if doors.discord and not doors.password:
        return f"{discord_oauth.START_PATH}?next={target}"
    surface = quote(str(getattr(resolved, "prefix", "") or "/"), safe="/")
    return f"{auth_routes.LOGIN_PATH}?next={target}&surface={surface}"


# --------------------------------------------------------------------------------- rendering

def _scheme_of(resolved: Any) -> Any:
    """The context with ``theme.light`` set from the palette that will actually be served.

    ``color-scheme`` is not a custom property, so a shipped preset cannot declare it;
    :func:`services.webservice.theme_css.scheme` answers which palette is served, and the context's
    ``theme.light`` is set from that answer — the one field the shell reads to pick ``data-theme`` on
    ``<html>``, so a light preset gets light native surfaces instead of a dark page's.
    """
    theme = getattr(resolved, "theme", None)
    if theme is None:
        return resolved
    light = theme_css.scheme(theme) == "light"
    if theme.light == light:
        return resolved
    return replace(resolved, theme=replace(theme, light=light))


def _over(base: Any, drafted: Any) -> Any:
    """*base* with the draft's CONTENT fields over it. The address never moves."""
    if drafted is None:
        return base
    fields = {name: getattr(drafted, name) for name in sites.CONTENT_FIELDS}
    theme = fields.get("theme")
    if theme is not None:
        fields["theme"] = replace(theme, light=(theme_css.scheme(theme) == "light"))
    return replace(base, **fields, enabled=bool(drafted.enabled))


def resolve_site(request: Request, site: Any) -> Any:
    """The site context of THIS request: the live catalogue's resolver, never a second derivation.

    ``site`` is the configured :class:`~services.webservice.sites.Site` the route belongs to; its slug
    is what the resolver is keyed by, so membership is the live registry's at this moment and an
    unknown slug raises :class:`~services.webservice.sites.SiteNotFound`.

    THE ONE PLACE A PAGE READS THE CATALOGUE. Every public page module calls this function and
    nothing else, so the overlay cannot be forgotten by a route: ``live_catalog`` answers the
    INSTALLED structure with each site's content fields taken from ``config/site.yaml`` as it stands
    at this request, which is what makes a saved content edit live on the next page load. A
    structural edit is not applied (a route exists only for what was installed) and logs that a
    reload is owed.

    THE COLOUR SCHEME IS THE PALETTE'S OWN. ``color-scheme`` is not a custom property, so a shipped
    preset cannot declare it; :func:`services.webservice.theme_css.scheme` answers which palette is
    actually served, and the context's ``theme.light`` is set from that answer. It is the one field
    the shell reads to pick ``data-theme`` on ``<html>``, so a light preset gets light native
    surfaces (a ``<select>``'s popup, a scrollbar) instead of a dark page's.
    """
    catalog = sites.live_catalog(request.app).catalog
    return _scheme_of(catalog.resolve(getattr(site, "slug", "")))


def pool() -> Any:
    """The database pool this process already holds, or ``None`` before the bus is up."""
    try:
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        return getattr(ServiceRegistry.get(ServiceBus), "apool", None)
    except Exception:  # noqa: BLE001 - a site without a bus renders its own empty state
        return None


def render_site_page(request: Request, site: Any, template: str, *, active: str = "",
                     resolved: Any = None, search_value: str = "", status_code: int = 200,
                     who: Any = ..., demo: bool = False, **context: Any) -> HTMLResponse:
    """Render one page of *site* through the shell template and the language in force.

    THE LANGUAGE IN FORCE, THE SITE'S LANGUAGE AS THE FALLBACK: precedence is the visitor's stored
    ``dcssb_lang`` cookie first and ``site.lang`` second — ``Accept-Language`` is not consulted and
    neither is ``main.yaml``. A visitor who has chosen nothing gets the language the site's operator
    declared; one who has chosen a language gets that choice on every site. ``site.lang`` is
    validated against the shipped catalogs at startup, so the fallback is always a real language and
    the ``lang`` attribute cannot disagree with the strings inside the document.

    The cookie is the CONSOLE's own (path ``/``), so the choice is shared: French picked on the
    public site is French in the Command Center and vice versa — one stored choice, one control.

    The context is the one :func:`resolve_site` produced for this request (or, when a caller did not
    pass it, the catalog's own resolution), so membership is the live registry's at this moment and
    no field is re-derived here. ``who`` is the identity the page already resolved: passing it keeps
    one authenticate per request, and the chip then cannot disagree with the page's own model.
    """
    if resolved is None:
        resolved = resolve_site(request, site)
    registrar = getattr(getattr(request.app, "state", None), "webui_registrar", None)
    language = i18n.resolve_site_language(request, resolved.lang)
    environment = i18n.environment_for_language(registrar, language)
    if who is ...:
        who = identity(request)
    config_dir = getattr(getattr(getattr(request, "app", None), "state", None),
                         "webui_config_dir", None)
    community = community_name(resolved, config_dir=config_dir)
    values: dict[str, Any] = {
        "language": language,
        "language_control": i18n.site_language_control(request, resolved, language),
        "site": resolved,
        # the hero's greeting: the community's name (declared, Discord, main.yaml, title) and the
        # one translated sentence around it, split so the name prints on its own line
        "greeting": hero_greeting(request, community),
        # the hero's paragraph: the site's welcome with the name substituted, else the shipped one
        "welcome": hero_welcome(request, resolved, community),
        # the hero's mark: the site's own (uploaded first, then branding.logo), at the ladder's 2x
        # asset, and the side it is drawn on. ``None`` draws no mark at all.
        "hero_mark": logo_url(resolved, config_dir, HERO_MARK_SIZE),
        "mark_side": getattr(getattr(resolved, "branding", None), "mark_side",
                             sites.DEFAULT_MARK_SIDE),
        "home_href": site_url(resolved),
        "logo_href": logo_url(resolved, config_dir),
        "nav": nav_items(resolved, active),
        "signed_in": who is not None,
        "chip": chip(request, resolved, who),
        # the anonymous visitor's own control: the door, carrying THIS page as its return target
        # (built by ``sign_in_href`` through ``safe_return``, never hand-assembled here).
        "sign_in_href": sign_in_href(request, resolved, request.url.path),
        "theme_sheet": theme_sheet_url(resolved),
        "search_href": site_url(resolved, SEARCH_PATH),
        "search_json_href": site_url(resolved, SEARCH_JSON_PATH),
        "search_value": search_value,
    }
    if demo:
        # A MOCK IS NOT A PAGE. It exists to show a site's look inside the console's box, for a site
        # whose own address is not served yet — so nothing in it navigates and nothing it names is
        # fetched from that address: every href is inert, the palette and the marks ride INSIDE the
        # document as data URIs, and no visitor chip or language control is drawn (a mock has no
        # visitor and no route to switch a language on).
        values.update(
            home_href="#",
            nav=[{**entry, "href": "#", "active": False} for entry in values["nav"]],
            search_href="#",
            search_json_href="#",
            theme_sheet=_demo_sheet(resolved, config_dir),
            logo_href=_demo_mark(resolved, config_dir, AVATAR_MARK_SIZE),
            hero_mark=_demo_mark(resolved, config_dir, HERO_MARK_SIZE),
            chip=None,
            sign_in_href="",
            language_control=None,
            signed_in=False,
        )
    markup = environment.get_template(template).render(**values, **context)
    return HTMLResponse(markup, status_code=status_code)


# --------------------------------------------------------------------------------- the pages

def page_modules() -> tuple[Any, ...]:
    """The page modules this build serves, in nav order.

    A page module exposes ``NAV_ID`` (its id in :data:`NAV_PATHS`), ``TEMPLATE`` and
    ``add_route(router, site) -> str`` (the path it registered). Imported inside the call because a
    page module imports this module for :func:`site_url` and :func:`render_site_page`.
    """
    from .pages import site_leaderboard, site_me, site_overview, site_player, site_players

    return (site_overview, site_leaderboard, site_player, site_players, site_me)


#: the template the preview route renders: ``site_base.html``'s shell with the overview's hero for
#: its content, and nothing else.
PREVIEW_TEMPLATE = "site_preview.html"


# ------------------------------------------------------- the settings card's unsaved draft
#
# WHAT A DRAFT IS. The Site settings card posts the fields it currently holds, and the preview
# renders the site with that block laid over the saved one, so the demo follows the form BEFORE Save
# and nothing is written anywhere. One parsed block per (session, site): the session is named by the
# CSRF token its own cookie carries, so a draft is only ever reachable from the session that posted
# it — an anonymous request holds no token and therefore no draft.
#
# NOTHING IS WRITTEN. The store is a dict in this process; no file, no log line carrying a value, and
# nothing in ``site.yaml``. Entries expire after :data:`DRAFT_TTL_SECONDS` of idleness and the store
# refuses a NEW entry beyond :data:`DRAFT_MAX_ENTRIES`, so a page left open on a laptop cannot grow it
# without bound.

#: how long an untouched draft survives.
DRAFT_TTL_SECONDS = 30 * 60
#: how many (session, site) blocks the store will hold; a new entry past this is refused.
DRAFT_MAX_ENTRIES = 32
#: the largest block a draft may carry, measured as its JSON text.
DRAFT_MAX_BYTES = 256 * 1024
#: the fields a draft may NOT move: the demo always renders at the preview address, whatever the
#: form's own address box says.
DRAFT_UNMOVED_FIELDS: tuple[str, ...] = ("slug", "group_role")


@dataclass(frozen=True)
class Draft:
    """One session's unsaved block for one site, and when it was last touched."""

    block: Mapping
    touched: float


_drafts: dict[tuple[str, str], Draft] = {}
_drafts_lock = threading.Lock()


def _clock() -> float:
    """The store's own clock (monotonic, so a wall-clock jump cannot expire or revive a draft)."""
    return time.monotonic()


def _purge_expired(now: float) -> None:
    """Drop every entry idle for longer than :data:`DRAFT_TTL_SECONDS`; the caller holds the lock."""
    for key, entry in list(_drafts.items()):
        if now - entry.touched > DRAFT_TTL_SECONDS:
            _drafts.pop(key, None)


def _session_token(request: Request) -> str | None:
    """The token that names this session for the store: its own CSRF token, minted if it has none.

    Minting here is what makes the reference live in the session rather than in this module: sign-out
    and sign-in both run ``session.clear()``, so the next page render mints a DIFFERENT token and
    :func:`draft_block` stops answering the cleared session's entry (which the TTL then reaps).
    """
    return session.get_csrf_token(request) or None


def reset_drafts() -> None:
    """Empty the store (the suite's own entry point; a live process never needs it)."""
    with _drafts_lock:
        _drafts.clear()


def draft_count() -> int:
    """How many (session, site) blocks the store currently holds."""
    with _drafts_lock:
        return len(_drafts)


def remember_draft(request: Request, site_key: str, block: Mapping) -> bool:
    """File *block* as this session's draft for *site_key*; ``False`` when the store is full."""
    token = _session_token(request)
    if not token or not site_key:
        return False
    now = _clock()
    key = (token, site_key)
    with _drafts_lock:
        _purge_expired(now)
        if key not in _drafts and len(_drafts) >= DRAFT_MAX_ENTRIES:
            return False
        _drafts[key] = Draft(block=block, touched=now)
    return True


def draft_block(request: Request, site_key: str) -> Mapping | None:
    """This session's draft for *site_key*, or ``None`` — the ownership test is the token itself."""
    now = _clock()
    with _drafts_lock:
        _purge_expired(now)
        for (token, key), entry in list(_drafts.items()):
            if key != site_key or not session.validate_csrf_token(request, token):
                continue
            _drafts[(token, key)] = Draft(block=entry.block, touched=now)
            return entry.block
    return None


def forget_draft(request: Request, site_key: str) -> None:
    """Drop this session's draft for *site_key* (a save applied it, or the operator left the card)."""
    token = _session_token(request)
    if not token:
        return
    with _drafts_lock:
        _drafts.pop((token, site_key), None)


def forget_session_drafts(request: Request) -> int:
    """Drop every draft this session holds; answers how many entries went."""
    token = _session_token(request)
    if not token:
        return 0
    with _drafts_lock:
        keys = [key for key in _drafts if key[0] == token]
        for key in keys:
            _drafts.pop(key, None)
    return len(keys)


def draft_overlay(block: Mapping) -> dict:
    """The plain copy of *block* a draft stores: no structural field, no ruamel type, JSON-sized.

    ``slug`` and ``group_role`` are dropped here rather than at render time, so a draft can never
    move the address the demo is served at.
    """
    plain = _plain_block(block)
    if not isinstance(plain, dict):
        return {}
    for name in DRAFT_UNMOVED_FIELDS:
        plain.pop(name, None)
    return plain


def _plain_block(value: Any) -> Any:
    """*value* as plain JSON-shaped Python (ruamel's commented maps and seqs become dict and list)."""
    if isinstance(value, Mapping):
        return {str(key): _plain_block(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_block(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def draft_bytes(block: Mapping) -> int:
    """The size of *block* as its JSON text — the measure :data:`DRAFT_MAX_BYTES` bounds."""
    return len(json.dumps(_plain_block(block), ensure_ascii=False).encode("utf-8"))


def _deep_merge(base: Mapping, overlay: Mapping) -> dict:
    """*overlay* laid over *base*: a block present on both sides is merged key by key, anything else
    is replaced, so a draft that states one leaf never erases its siblings."""
    merged = {str(key): value for key, value in base.items()}
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def draft_site(request: Request, site: Any, block: Mapping) -> Any | None:
    """The :class:`~services.webservice.sites.Site` *block* describes, or ``None``.

    The saved block for the same site is the base and the draft is laid over it, parsed by the
    loader's own parser — so a draft is held to exactly the rules a save is, and a block the parser
    refuses answers ``None`` (the preview then renders the saved site).
    """
    config_dir = getattr(getattr(request.app, "state", None), "webui_config_dir", None)
    key = str(getattr(site, "key", "") or "")
    if not key:
        return None
    base: Mapping = {}
    try:
        stored = sites.load_site_block(config_dir)
    except sites.SiteConfigError:
        return None
    if isinstance(stored, Mapping) and isinstance(stored.get(key), Mapping):
        base = stored[key]
    merged = _deep_merge(base, block)
    try:
        catalog = sites.parse_sites(
            {key: merged},
            known_roles=sites.known_managed_by(sites.load_servers_config(config_dir)),
            config_dir=config_dir)
    except sites.SiteConfigError:
        return None
    return catalog.sites[0] if catalog.sites else None


def draft_context(request: Request, site: Any, block: Mapping) -> Any:
    """The site context the preview renders: the saved one with the draft's content merged over it.

    ``enabled`` travels with the draft — it is the one thing that lets a switched-off site render,
    and only for the session holding the draft. Nothing else about the site's structure moves: the
    address is not applied, so the demo is always served at this site's own preview address.
    """
    resolved = resolve_site(request, site)
    drafted = draft_site(request, site, block)
    if drafted is None:
        return resolved
    fields = {name: getattr(drafted, name) for name in sites.CONTENT_FIELDS}
    theme = fields.get("theme")
    if theme is not None:
        fields["theme"] = replace(theme, light=(theme_css.scheme(theme) == "light"))
    return replace(resolved, **fields, enabled=bool(drafted.enabled))


def demo_context(request: Request, site: Any, block: Mapping | None = None) -> Any:
    """The site context the DEMO document renders: the FILE's own site, draft merged over it.

    NOT :func:`resolve_site`: that one resolves through the live registry, and the demo exists
    precisely for a site that registry does not carry yet. The site here is the model the site file
    describes, so the drawing is what the file says — and the session's draft, when there is one, is
    merged over it so the box follows the form before Save.
    """
    return _over(_scheme_of(site), draft_site(request, site, block) if block else None)


def _serves_today(site: Any) -> bool:
    """Whether this site has any route at all today: a switched-off site has none but its assets."""
    return bool(getattr(site, "enabled", True))


def _draft_only(request: Request, site: Any) -> bool:
    """Whether this request reaches a switched-off site's asset: only a session holding a draft."""
    return not _serves_today(site) and draft_block(request, site.key) is not None


def _add_preview_route(router: APIRouter, site: Any) -> str:
    """Serve the settings card's preview at :data:`CHROME_PREVIEW_PATH` — registered for EVERY site.

    ONE ROUTE PER SITE, RESOLVED PER REQUEST, for the reason the palette route gives: a route cannot
    be registered by a save, and the card must be able to show a site that is switched OFF as soon as
    the operator flips its switch. So the address exists either way and the answer is decided here:

    * no draft for this session, site served today — the site's own document, exactly as before;
    * no draft, site switched off — ``404``, which is what an unregistered address answered;
    * a draft — the saved site with the draft's content merged over it (``enabled`` included), so a
      site that is off renders for the session that flipped it and for nobody else.

    Rendered through :func:`render_site_page`, so everything about the document is the site's: its
    own sheet, the palette its theme resolves to, its brand, its nav and its footer. The card's
    binding writes into THIS document's own DOM; nothing here draws a second copy of the chrome.
    """
    path = site_url(site, CHROME_PREVIEW_PATH)

    async def chrome_preview(request: Request) -> HTMLResponse:
        block = draft_block(request, site.key)
        if block is None:
            if not _serves_today(site):
                raise HTTPException(status_code=404)
            return render_site_page(request, site, PREVIEW_TEMPLATE)
        return render_site_page(request, site, PREVIEW_TEMPLATE,
                                resolved=draft_context(request, site, block))

    router.add_api_route(path, chrome_preview, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-chrome-preview-{site.key}")
    return path


def _add_theme_route(router: APIRouter, site: Any) -> str:
    """Serve the site's palette at :data:`THEME_SHEET_PATH` — registered for EVERY site.

    ONE ROUTE PER SITE, RESOLVED PER REQUEST. The route exists however the site is configured
    today, because a route cannot be registered by a save: a site that declares no palette now may
    name a preset or an expert block later, and the promise "a saved sheet shows up without a
    restart" is only true when the address already answers. The sheet itself is resolved per
    request from the live catalogue (:func:`services.webservice.sites.live_catalog`), so the file's
    content fields are in force at once; installed structure — which sites exist, their prefixes —
    is not, and a structural edit waits for the reload.

    A SWITCHED-OFF SITE'S SHEET ANSWERS ITS OWN DRAFT ONLY: the settings card's demo draws the
    palette beside the hero, so the session that is previewing a site it has just switched on gets
    the sheet and every other caller gets the ``404`` an unregistered address answers.

    WHAT IT CAN SERVE. Only the palette of the SITE WHOSE PREFIX WAS REQUESTED: the site is looked
    up by its installed slug, a preset is read from
    :data:`services.webservice.theme_css.PRESET_DIR`, and an uploaded sheet from the site's own asset
    folder through :func:`services.webservice.sites.sheet_path`. No request value ever builds a path,
    and there is no query parameter that could name one — that would serve any readable file of the
    installation as CSS.
    """
    async def theme_sheet(request: Request) -> Response:
        if not _serves_today(site) and not _draft_only(request, site):
            raise HTTPException(status_code=404)
        catalog = sites.live_catalog(request.app).catalog
        current = catalog.site_for_slug(str(getattr(site, "slug", "") or ""))
        if current is None:
            return PlainTextResponse("", media_type=theme_css.CSS_MEDIA_TYPE, status_code=404)
        config_dir = getattr(getattr(request.app, "state", None), "webui_config_dir", None)
        block = draft_block(request, site.key) if not _serves_today(site) else None
        theme = getattr(draft_site(request, site, block), "theme", None) if block else current.theme
        return theme_css.response(request, theme_css.compose(theme or current.theme, config_dir))

    route_path = site_url(site, THEME_SHEET_PATH)
    router.add_api_route(route_path, theme_sheet, methods=["GET"], name=f"site-theme-{site.key}")
    return route_path


def _add_logo_route(router: APIRouter, site: Any) -> str:
    """Serve a site's ``branding.logo`` at :data:`LOGO_PATH` — registered for EVERY site.

    ONE ROUTE PER SITE, RESOLVED PER REQUEST, for the reason the palette route gives: a route cannot
    be registered by a save, so the address answers however the site is configured today and the file
    a save wrote is what it serves. The logo is looked up in the LIVE catalogue, and only for the
    site whose prefix was requested.

    WHAT IT CAN SERVE. A raster file the config names, answered with the type its suffix gives; a
    suffix :data:`LOGO_SUFFIXES` does not carry (an SVG above all) answers 404, so the address can
    never serve a document that runs on this page's origin as an image, and a site that declares no
    logo answers 404 as well — the shell then draws the product mark.
    """
    async def site_logo(request: Request) -> Response:
        if not _serves_today(site) and not _draft_only(request, site):
            raise HTTPException(status_code=404)
        config_dir = getattr(getattr(request.app, "state", None), "webui_config_dir", None)
        catalog = sites.live_catalog(request.app).catalog
        current = catalog.site_for_slug(str(getattr(site, "slug", "") or ""))
        declared = str(getattr(getattr(current, "branding", None), "logo", "") or "")
        content_type = LOGO_SUFFIXES.get(Path(declared).suffix.lower()) if declared else None
        if content_type is None:
            return PlainTextResponse("", media_type="image/png", status_code=404)
        target = sites.asset_path(declared, config_dir)
        if not target.is_file():
            return PlainTextResponse("", media_type=content_type, status_code=404)
        return Response(target.read_bytes(), media_type=content_type,
                        headers={"Cache-Control": "max-age=3600"})

    route_path = site_url(site, LOGO_PATH)
    router.add_api_route(route_path, site_logo, methods=["GET"], name=f"site-logo-{site.key}")
    return route_path


def _add_avatar_route(router: APIRouter, site: Any) -> str:
    """Serve a site's UPLOADED mark at :data:`AVATAR_PATH` — registered for EVERY site.

    ONE ROUTE PER SITE, RESOLVED PER REQUEST, for the reason the palette route gives: a route cannot
    be registered by an upload, so the address answers however the site is configured today and the
    files an upload wrote are what it serves. The rule is :func:`uploaded_mark` — the same one
    :func:`logo_url` uses — so the address the page points at and the bytes it answers with cannot
    disagree. A site that has not uploaded a mark answers 404, and the shell then draws
    ``branding.logo`` or the shipped mark.

    WHAT IT CAN SERVE. Only the file :func:`services.webservice.sites.avatar_path` builds, at a size
    :data:`services.webservice.sites.AVATAR_SIZES` names. The bound ``?size=`` picks it and defaults
    to :data:`AVATAR_MARK_SIZE`, so the bar's request carries no query string; any other value
    answers 404. No request value builds a path.
    """
    async def site_avatar(request: Request, size: str = "") -> Response:
        if not _serves_today(site) and not _draft_only(request, site):
            raise HTTPException(status_code=404)
        config_dir = getattr(getattr(request.app, "state", None), "webui_config_dir", None)
        catalog = sites.live_catalog(request.app).catalog
        current = catalog.site_for_slug(str(getattr(site, "slug", "") or ""))
        if size and size not in {str(value) for value in sites.AVATAR_SIZES}:
            return PlainTextResponse("", media_type="image/png", status_code=404)
        target = (uploaded_mark(current, config_dir, int(size) if size else AVATAR_MARK_SIZE)
                  if current is not None else None)
        if target is None:
            return PlainTextResponse("", media_type="image/png", status_code=404)
        # revalidate every time: a replaced mark must show on the next page load, not an hour later
        return Response(target.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "no-cache"})

    route_path = site_url(site, AVATAR_PATH)
    router.add_api_route(route_path, site_avatar, methods=["GET"], name=f"site-avatar-{site.key}")
    return route_path


def _add_language_route(router: APIRouter, site: Any) -> str | None:
    """Serve a site's language switch at ``<site root>/language``, or register none.

    A GET link (like the console's own switch): it sets a display cookie and grants nothing, so it
    needs no CSRF token. The cookie is the console's SHARED ``dcssb_lang`` — path ``/``, the same
    attributes the console's route uses — so one choice covers both surfaces.

    ``next`` is validated against THIS SITE's tree by :func:`safe_return`; anything else (an
    absolute URL, a protocol-relative ``//host``, a console path or another site's prefix) lands on
    the site root. The console's own ``_safe_target`` is not reused and not relaxed: a site path is
    not a console path.

    The ROOT site (``""`` prefix) resolves to ``/language``, the installation's fixed address,
    which the shell's own route already owns and serves with the same cookie. Registering a second
    route there would collide, so the root site keeps the existing switch and this function returns
    ``None`` — its control's ``action`` still names ``/language``.
    """
    path = site_url(site, LANGUAGE_PATH)
    if path == LANGUAGE_PATH:
        return None

    async def choose_language(request: Request, lang: str = "", next: str = "/"):
        resolved = resolve_site(request, site)
        response = RedirectResponse(safe_return(request, resolved, next), status_code=303)
        code = i18n.normalise_language(lang)
        if code:
            settings = getattr(getattr(request, "app", None), "state", None)
            settings = getattr(settings, "webui_session_settings", None) or {}
            response.set_cookie(i18n.COOKIE_NAME, code, max_age=i18n.COOKIE_MAX_AGE, path="/",
                                secure=bool(settings.get("https_only")), samesite="lax",
                                httponly=True)
        return response

    router.add_api_route(path, choose_language, methods=["GET"], name=f"site-language-{site.key}")
    return path


def _build_router(catalog) -> tuple[APIRouter, dict[str, str]] | None:
    """Build the public site's router and its capability declarations from *catalog*.

    ``None`` when the catalog declares no site at all: the console then owns the site root. A catalog
    whose sites are all switched off still builds a table — the preview and the three assets it draws
    are registered for every site (they answer ``404`` until a draft exists) — while the console owns
    ``/``, because :func:`services.webservice.mount.site_owns_root` reads the switch and not the table.
    The caller owns the registrar's bookkeeping — this function only paints routes, so it is safe to
    build a whole new table before anything live is touched.
    """
    if not catalog:
        return None
    from .pages import site_players

    modules = page_modules()
    router = APIRouter()
    capabilities: dict[str, str] = {}
    for site in catalog.sites:
        served = bool(getattr(site, "enabled", True))
        if served:
            for module in modules:
                path = module.add_route(router, site)
                capabilities[path] = permissions.PUBLIC
            # the top bar's suggestions: a JSON sibling of the search page, registered here so it
            # is declared ``public`` exactly like the page whose read it reuses.
            search_path = site_players.search_route(router, site)
            if search_path is not None:
                capabilities[search_path] = permissions.PUBLIC
        else:
            # a site switched off registers no PAGE: its prefix answers 404, its pages included
            log.debug("Sites: '%s' is disabled; no page route is registered for %s.", site.key,
                      site.prefix or "/")
        # THE ADDRESSES THAT MUST EXIST EITHER WAY. A route cannot be registered by a save, and the
        # settings card has to show a site the operator has just switched on — so the preview and the
        # three assets it draws are registered for EVERY site and resolved per request: a switched-off
        # site answers its own session's draft and the 404 an unregistered address gave everybody else.
        preview_path = _add_preview_route(router, site)
        capabilities[preview_path] = permissions.PUBLIC
        # the site's palette: always registered (a save cannot add a route), resolved per request
        sheet_path = _add_theme_route(router, site)
        capabilities[sheet_path] = permissions.PUBLIC
        mark_path = _add_logo_route(router, site)
        capabilities[mark_path] = permissions.PUBLIC
        # the uploaded mark's own address: always registered, resolved per request (see logo_url)
        avatar_path = _add_avatar_route(router, site)
        capabilities[avatar_path] = permissions.PUBLIC
        if served:
            # the site's own language switch; the root site keeps the installation's fixed /language
            switch_path = _add_language_route(router, site)
            if switch_path is not None:
                capabilities[switch_path] = permissions.PUBLIC
    if not capabilities:
        return None
    return router, capabilities


def register(registrar, catalog) -> Any:
    """Register every configured site's page tree under the registrar. Returns the registration.

    One router, one owner, an EMPTY prefix: the routes keep the absolute addresses
    :func:`site_url` built for them, whatever the console's mount is. Every path is declared
    ``public`` — the site has no session, no capability and no scope — and a catalog with no site
    registers nothing at all, so the console owns ``/``
    (:func:`services.webservice.mount.site_owns_root` is the one rule both sides read). A site
    switched off keeps only its preview and its three assets, each answering ``404`` to every caller
    but the session holding a draft for it.
    """
    built = _build_router(catalog)
    if built is None:
        return None
    router, capabilities = built
    registration = registrar.register_pages(OWNER, router, capabilities=capabilities, prefix="")
    log.debug("Sites: registered %d public page(s) at %s.",
              len(capabilities), ", ".join(sorted(capabilities)))
    return registration


def _restore_installed(registrar, app, old_prefix: str, old_catalog) -> None:
    """Put the console's mount and the previous site table back after a failed apply.

    Restores what an apply changes in order — the console's registrations, the site's route table,
    the catalog recorded on the application and the published mount — to *old_prefix* and
    *old_catalog*, the values that were serving before the call. Each step is guarded on its own: the
    caller's original exception is the one that matters, so a step that cannot be undone is logged at
    warning and the rest still run.
    """
    try:
        registrar.move_console(old_prefix)
    except Exception as ex:  # noqa: BLE001 - the original exception is the one that surfaces
        log.warning("Sites: the console could not be moved back to '%s' after a failed apply "
                    "(%s). The next successful apply moves it.", old_prefix or "/", ex)
    try:
        if old_catalog is None:
            registrar.replace_owner(OWNER, None)
        else:
            rebuilt = _build_router(old_catalog)
            if rebuilt is None:
                registrar.replace_owner(OWNER, None)
            else:
                router, capabilities = rebuilt
                registrar.replace_owner(OWNER, router, capabilities=capabilities, prefix="")
    except Exception as ex:  # noqa: BLE001 - see above
        log.warning("Sites: the previous site table could not be restored after a failed apply "
                    "(%s). The next successful apply replaces it.", ex)
    try:
        setattr(app.state, sites.STATE_ATTR, old_catalog)
    except Exception as ex:  # noqa: BLE001 - see above
        log.warning("Sites: the installed site catalog could not be recorded after a failed apply "
                    "(%s).", ex)
    try:
        mount.install_console_mount(app, old_catalog)
    except Exception as ex:  # noqa: BLE001 - see above
        log.warning("Sites: the console's mount could not be republished after a failed apply "
                    "(%s).", ex)


def catalog_from_file(app) -> Any:
    """The catalogue ``config/site.yaml`` declares, as the console would install it.

    THE ONE READER of the file's structure: a rebuild installs what this answers and the apply door
    validates it BEFORE scheduling one, so the door cannot accept a file the rebuild would drop.
    Raises :class:`~services.webservice.sites.SiteConfigError` for a block the console cannot serve;
    an application carrying no config answers the loader's own empty catalogue.
    """
    config = getattr(getattr(app, "state", None), "webui_config", None)
    config_dir = getattr(getattr(app, "state", None), "webui_config_dir", None)
    return sites.load_sites(config, config_dir=config_dir)


def rebuild(registrar, app, catalog=None) -> Any:
    """Rebuild the public site's route table in place for the file's CURRENT structure.

    The new router and its include wrapper are built first; the application's route table is then
    swapped in with ONE assignment (:meth:`~services.webservice.registry.Registrar.replace_owner`),
    so a request already walking the old table finishes on it and a failure while building leaves
    the previous table serving. ``enabled`` and ``slug`` therefore take effect without a restart:

    * a site switched off loses every route (pages, search JSON, preview, theme sheet, logo, avatar,
      language switch); switching it back on registers them again;
    * a new ``slug`` is served at its new prefix while the old prefix answers 404;
    * when the enabled set gains or loses the root site, `/` moves between the public site and the
      console: the console's own registrations are re-mounted at the other prefix and
      :func:`services.webservice.mount.install_console_mount` republishes the mount, so the
      dashboard and the console's "open the public site" link follow.

    *catalog* is the structure to apply; ``None`` loads it from ``config/site.yaml``. A file that
    cannot be parsed raises nothing here: the caller keeps the installed table, which is the state
    a half-written file deserves. Returns the new registration (``None`` when no site is enabled).

    A raise from the move, the swap or the republish — a claim a peer took since install, a route
    that cannot be re-mounted — restores the console's prefix, the previous site table and the
    published mount (:func:`_restore_installed`) and then re-raises, so the caller records the apply
    as failed and the installed tree keeps serving what it served before the call.
    """
    if catalog is None:
        try:
            catalog = catalog_from_file(app)
        except sites.SiteConfigError as ex:
            log.warning("Sites: the site file could not be rebuilt (%s). The installed sites keep "
                        "serving; the table is unchanged.", ex)
            return None
    built = _build_router(catalog)
    new_prefix = mount.CONSOLE_PREFIX if mount.site_owns_root(catalog) else ""
    old_prefix = mount.console_prefix(app)
    # the state the rollback restores: the catalog serving BEFORE this call, read before any mutation
    old_catalog = getattr(getattr(app, "state", None), sites.STATE_ATTR, None)
    # THE ORDER THAT NEVER COLLIDES: with the site taking `/`, the console must vacate it BEFORE the
    # site claims it; with the site leaving `/`, the site must drop its claim BEFORE the console
    # claims it. Both registrars release their old claims and claim the new ones.
    try:
        if new_prefix != old_prefix and new_prefix == mount.CONSOLE_PREFIX:
            registrar.move_console(new_prefix)
        if built is None:
            registration = registrar.replace_owner(OWNER, None)
        else:
            router, capabilities = built
            registration = registrar.replace_owner(OWNER, router, capabilities=capabilities,
                                                   prefix="")
        if new_prefix != old_prefix and new_prefix == "":
            registrar.move_console(new_prefix)
        setattr(app.state, sites.STATE_ATTR, catalog)
        mount.install_console_mount(app, catalog)
    except Exception:
        # ANY raise between the move, the swap and the republish leaves the console's tree, the
        # site table and the published mount disagreeing about who answers `/`. Put all three back
        # before the exception leaves this function.
        _restore_installed(registrar, app, old_prefix, old_catalog)
        raise
    log.info("Sites: rebuilt the public route table from the site file (%d site(s), %d route(s)); "
             "console at '%s'.", len(getattr(catalog, "sites", ()) or ()),
             registrar.route_count, new_prefix or "/")
    return registration
