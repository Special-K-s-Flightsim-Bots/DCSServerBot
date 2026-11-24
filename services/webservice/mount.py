"""Where the console is mounted: one prefix, decided once, read everywhere.

The community site owns the site root ``/``. The console therefore moves to ``/console`` **while an
enabled site is served at the root**, and keeps ``/`` otherwise — no catalog at all, or a catalog
whose root site is switched off. That is the whole rule, and it lives here so no page module,
template or redirect decides it again: :func:`site_owns_root` reads the same ``enabled`` flag the
site's routes are registered by, so the mount can never leave ``/`` owned by nobody (a disabled
site registers no route, and an unowned ``/`` answers a bare 404).

The prefix is a fact of the INSTALLED SHELL, not of a request: it is resolved once in
:func:`install_console_mount` (called by ``install_shell`` right after the ``sites:`` block is
validated) and read from then on, which is what lets a URL builder deep inside a page produce the
mounted form without being handed the application. One process serves one mount configuration —
a master/agent takeover rebuilds the application and re-installs the mount.

``prefix == ""`` is a REAL mode, not a degenerate one: the console is at ``/``, every URL below is
its own path, and the site is not served at all.

SHELL-FIXED PATHS ARE NOT PREFIXED. The login flow (``/auth/*``), the language switch
(``/language``), the served message map (``/i18n/messages.js``), the theme switch (``/theme``) and
the asset area (``/static``) belong to the installation rather than to the console's page tree: the
login page is the landing page in both modes and the site's "sign in" control points at it, so
those addresses are the same before and after a mount change and a bookmark to them keeps working.
"""
from __future__ import annotations

import logging

__all__ = ["CONSOLE_PREFIX", "CONSOLE_SEGMENT", "FIXED_PATHS", "FIXED_PREFIXES", "STATE_ATTR",
           "console_prefix", "install_console_mount", "is_console_path", "is_fixed",
           "return_prefixes", "site_owns_root", "surface_of", "surface_root", "url"]

log = logging.getLogger(__name__)

#: The path segment the console is mounted under while a community site owns ``/``.
CONSOLE_SEGMENT = "console"
#: The full prefix of that mount (no trailing slash).
CONSOLE_PREFIX = "/" + CONSOLE_SEGMENT

#: The application-state attribute the resolved prefix is stored under.
STATE_ATTR = "webui_console_prefix"

#: Paths the installation owns rather than the console's page tree. They keep their addresses in
#: BOTH mount modes: the door, the display switches and the served message map are where a bookmark
#: and the login page's own document expect them, whatever the console is mounted under.
FIXED_PATHS: tuple[str, ...] = ("/login", "/logout", "/language", "/theme", "/i18n/messages.js")
#: Areas the installation owns wholesale (the login flow, the served message map, the asset area).
FIXED_PREFIXES: tuple[str, ...] = ("/auth/", "/i18n/", "/static/")


def is_fixed(path: str) -> bool:
    """Whether *path* is the installation's fixed surface and must never be moved by a mount."""
    text = str(path or "")
    return text in FIXED_PATHS or any(text.startswith(prefix) for prefix in FIXED_PREFIXES)


#: The process's own prefix. ``""`` until a shell is installed, which is the console-at-root mode
#: and therefore the correct answer for every caller that runs without a shell (a unit test, a
#: template rendered standalone, the setup console).
_PREFIX = ""


def site_owns_root(catalog) -> bool:
    """Whether a SERVED site owns the site root: an enabled site whose prefix is ``""``.

    The same ``enabled`` flag :func:`services.webservice.site_pages.register` registers by, so the
    console's mount and the site's routes cannot disagree about who answers ``/``: a switched-off
    site registers no route and therefore owns no address, whatever the block declares.
    """
    return any(getattr(site, "is_root", False) and getattr(site, "enabled", True)
               for site in getattr(catalog, "sites", ()) or ())


def install_console_mount(app, catalog) -> str:
    """Resolve the console's prefix from the site catalog and publish it. Returns the prefix.

    :func:`site_owns_root` decides: while an enabled site is served at ``/`` the console mounts at
    :data:`CONSOLE_PREFIX`, and otherwise the console keeps ``/`` and every URL stays what it was.
    """
    global _PREFIX
    _PREFIX = CONSOLE_PREFIX if site_owns_root(catalog) else ""
    setattr(app.state, STATE_ATTR, _PREFIX)
    return _PREFIX


def console_prefix(app=None) -> str:
    """The console's base prefix: ``""`` when the console owns ``/``, ``"/console"`` when a site
    does.

    *app* is read when given (so a caller holding the application never depends on the process
    value); a missing attribute falls back to the process prefix, which is what a page module
    building a URL outside a request needs.
    """
    stored = getattr(getattr(app, "state", None), STATE_ATTR, None) if app is not None else None
    return stored if isinstance(stored, str) else _PREFIX


def url(path: str, *, app=None) -> str:
    """*path* as the browser must address it — the console's prefix in front of a console path.

    A path that is already prefixed is returned unchanged (a builder may be fed another builder's
    output), and anything that is not a rooted path — ``#anchor``, ``mailto:``, a bare relative
    segment — is returned untouched: the prefix belongs to this console's tree only.
    """
    prefix = console_prefix(app)
    if not prefix or not isinstance(path, str) or not path.startswith("/"):
        return path
    if path == prefix or path.startswith(prefix + "/"):
        return path
    return prefix + path


def is_console_path(path: str, *, app=None) -> bool:
    """Whether *path* belongs to the console's mounted tree (always true with no prefix)."""
    prefix = console_prefix(app)
    if not prefix:
        return str(path or "").startswith("/")
    return path == prefix or str(path or "").startswith(prefix + "/")


def return_prefixes(app) -> tuple[str, ...]:
    """Every mount a relative return target may lead into, in this installation.

    The console's own mount, plus every configured site's mount (the root site's ``/`` included).
    A ``next=`` that leads outside all of them is refused — that is what keeps one surface's login
    page from returning a visitor into another surface.
    """
    from .sites import sites_for

    prefixes: list[str] = []
    console = console_prefix(app)
    prefixes.append(console or "/")
    for site in sites_for(app).sites:
        prefixes.append(site.prefix or "/")
    out: list[str] = []
    for prefix in prefixes:
        if prefix not in out:
            out.append(prefix)
    return tuple(out)


def surface_of(path, *, app) -> str:
    """The mount *path* belongs to, or the console's own mount when it belongs to none.

    Longest match wins, so ``/group1`` inside ``/group12/x`` is not mistaken for a surface.
    """
    candidates = return_prefixes(app)
    text = str(path or "")
    best = ""
    for prefix in candidates:
        if prefix == "/":
            continue
        if text == prefix or text.startswith(prefix + "/"):
            if len(prefix) > len(best):
                best = prefix
    if best:
        return best
    return "/" if "/" in candidates else console_prefix(app)


def surface_root(prefix: str) -> str:
    """The landing page of the surface mounted at *prefix* — the console's root is its prefix."""
    return prefix or "/"
