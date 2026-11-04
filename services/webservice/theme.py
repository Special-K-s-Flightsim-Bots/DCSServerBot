"""The admin web UI's THEME — the dark/light palette a visitor prefers, and the switch that sets it.

WHY THIS IS A COOKIE AND A ROUTE, NOT A SCRIPT. The console renders every page on the server (the
language control is the precedent: plain GET links, no JavaScript — see :mod:`services.webservice.
i18n`). A theme is the same kind of fact: a DISPLAY preference. It is kept in its own cookie,
``dcssb_theme``, set by the GET :data:`THEME_PATH` route, so

* it works BEFORE sign-in (the login page is where a visitor first meets the console), and
* it survives the session-fixation clear at sign-in (the login/logout flows call
  ``request.session.clear()``, which would wipe a preference kept in the session).

The page is then rendered with the theme the server chose: ``<html data-theme="dark|light">``, and
``shell.css`` does the rest (its ``html[data-theme="light"]`` block re-declares the palette tokens,
so the whole sheet follows one attribute). Nothing in the browser flips a class — the markup a
visitor receives already carries the right theme.

THE PRECEDENCE IS ONE RULE: the stored choice, else :data:`DEFAULT_THEME` (dark — the shipped look).
There is no ``Accept-*`` analogue for a theme and no ``main.yaml`` key yet, so both would be invented
surface; the cookie alone answers. ``normalise_theme`` is the ONE validator: a value is accepted only
when it is one of :data:`THEMES`, so a hostile cookie cannot echo an arbitrary string back into the
``data-theme`` attribute.
"""
from __future__ import annotations

import unicodedata

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from . import permissions
from .i18n import _

__all__ = [
    "COOKIE_NAME", "COOKIE_MAX_AGE", "THEME_PATH", "THEMES", "DEFAULT_THEME",
    "normalise_theme", "resolve_theme", "theme_control", "capabilities", "add_routes",
]

#: the cookie that stores the visitor's theme. Its own cookie, like the language choice — see the
#: module docstring (before sign-in, and past the session clear).
COOKIE_NAME = "dcssb_theme"

#: how long the choice is remembered (one year; a preference, not a credential).
COOKIE_MAX_AGE = 365 * 24 * 60 * 60

#: the path the theme switch links to (absolute and constant, like every shell path).
THEME_PATH = "/theme"

#: the two themes, DEFAULT FIRST. ``dark`` is the shipped console; ``light`` is the second palette
#: (``shell.css``'s ``html[data-theme="light"]`` block). Order is the order the control draws them.
THEMES: tuple[str, ...] = ("dark", "light")

#: the theme used when the visitor has stated no preference — the shipped dark console.
DEFAULT_THEME = "dark"

#: the mode names, ``_()``-marked so Babel extracts them into the ``webui`` catalog (the control
#: draws them as the option's accessible name; the flag control's endonyms are the precedent).
MODE_LABELS: dict[str, str] = {"dark": _("Dark"), "light": _("Light")}

#: the control's own accessible name (the ``<nav>`` label), ``_()``-marked for the catalog.
CONTROL_LABEL = _("Theme")


def normalise_theme(value) -> str | None:
    """*value* as a theme code (``dark``/``light``), or ``None``.

    The ONE validator. A value is accepted ONLY when it is literally one of :data:`THEMES`, so an
    unknown, malformed, over-long or control-character-bearing cookie is refused (and yields
    ``None`` → the default) rather than being echoed into the ``data-theme`` attribute.
    """
    if not isinstance(value, str) or not value:
        return None
    if len(value) > 32:
        return None
    if any(unicodedata.category(char).startswith("C") for char in value):
        return None
    code = value.strip().lower()
    return code if code in THEMES else None


def resolve_theme(request) -> str:
    """The theme THIS request renders in: the stored choice, else :data:`DEFAULT_THEME`."""
    cookies = getattr(request, "cookies", None)
    stored = normalise_theme(cookies.get(COOKIE_NAME) if cookies is not None else None)
    return stored or DEFAULT_THEME


def theme_control(request: Request) -> dict:
    """The theme switch as data: the current theme, the options, and where a switch returns to.

    ``next`` is the current PAGE PATH (never the query string), validated by the same rule the
    language control uses (:func:`services.webservice.i18n._return_path`) — a visitor may only be
    sent back to a path the console DECLARES. The route re-validates it and never trusts the
    client's copy.
    """
    # imported LAZILY: ``i18n`` imports this module lazily inside its ``render`` (so the two are
    # never a top-level cycle), and ``auth.routes`` imports ``i18n`` at top level.
    from . import i18n
    current = resolve_theme(request)
    return {
        "current": current,
        "action": THEME_PATH,
        "next": i18n._return_path(request),
        "label": CONTROL_LABEL,
        "modes": [{"code": code, "name": MODE_LABELS[code], "active": code == current}
                  for code in THEMES],
    }


def capabilities() -> dict[str, str]:
    """The switch route is PUBLIC: it sets a display cookie and grants nothing (like ``/language``).

    A visitor must be able to choose a theme on the login page, before any session exists, so the
    route declares no capability beyond the public marker.
    """
    return {THEME_PATH: permissions.PUBLIC}


def add_routes(router: APIRouter) -> APIRouter:
    """Add the theme-switch route to the shell's own router.

    A **GET** link, not a POST: it changes a display preference and grants nothing, so it needs no
    CSRF token (the login page, before a session exists, has no token to give). An unknown value
    changes nothing and just returns the visitor where they were.
    """
    from . import i18n

    @router.get(THEME_PATH, response_class=RedirectResponse)
    async def choose_theme(request: Request, theme: str = "", next: str = "/"):
        response = RedirectResponse(i18n._safe_target(next), status_code=303)
        code = normalise_theme(theme)
        if code:
            settings = getattr(request.app.state, "webui_session_settings", None) or {}
            response.set_cookie(COOKIE_NAME, code, max_age=COOKIE_MAX_AGE, path="/",
                                secure=bool(settings.get("https_only")), samesite="lax",
                                httponly=True)
        return response

    return router
