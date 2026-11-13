"""``<path>/`` answers ``308`` to ``<path>`` — the address a pasted or bookmarked link carries.

Why a middleware and not ``redirect_slashes``
---------------------------------------------
Starlette's own slash redirect only fires for a route whose declared path carries the slash, and
every page here is declared without one — so ``/players/`` matched nothing and answered ``404``
on a surface whose unslashed address is ``200``.

Why a middleware and not a catch-all route
------------------------------------------
A route is matched by position in one flat table, and this application's route table is assembled by
several registrations whose order decides which one wins. A middleware runs before routing, so it
can never shadow a page, and the answer still comes from the page itself: the redirect's target is
requested by the browser, so the page's own capability gate runs exactly as it does for a typed URL.

WHICH paths redirect is the registration's answer, not this module's — ``Registrar.slash_target``
holds the three conditions (an unslashed address that exists, a slashed form that is not an address
of its own, and never the site root).

``GET`` and ``HEAD`` only: a redirect on a write would turn a ``POST`` into a second request to
another address.
"""
from __future__ import annotations

import logging

from starlette.responses import RedirectResponse

__all__ = ["SLASH_STATUS", "TrailingSlashRedirect", "install_trailing_slash_redirect"]

log = logging.getLogger(__name__)

#: The status a slash redirect answers with. PERMANENT on purpose: the address without the slash is
#: this surface's canonical one, and a ``308`` preserves the method and body (a ``301`` may be
#: rewritten to ``GET`` by a client).
SLASH_STATUS = 308

#: The methods the redirect may answer. A write is never redirected: the browser would re-send the
#: body to another address, and the page's own form action is where it belongs.
_REDIRECTED_METHODS = ("GET", "HEAD")

_STATE_ATTR = "webui_registrar"


class TrailingSlashRedirect:
    """ASGI middleware: ``<path>/`` → ``308`` ``<path>``, when the registration says so.

    ``application`` is the FastAPI application the middleware is installed on: the registrar that
    answers the question is application state, and state is not reachable from the next ASGI app in
    the chain.
    """

    def __init__(self, app, application=None):
        self.app = app
        self.application = application

    async def __call__(self, scope, receive, send):
        location = self._location(scope)
        if location is not None:
            await RedirectResponse(location, status_code=SLASH_STATUS)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _location(self, scope) -> str | None:
        """The unslashed address to redirect to, or ``None`` when this request is not one."""
        if scope.get("type") != "http" or scope.get("method") not in _REDIRECTED_METHODS:
            return None
        registrar = getattr(getattr(self.application, "state", None), _STATE_ATTR, None)
        if registrar is None:
            return None
        target = registrar.slash_target(str(scope.get("path") or ""))
        if target is None:
            return None
        query = (scope.get("query_string") or b"").decode("latin-1")
        return f"{target}?{query}" if query else target


def install_trailing_slash_redirect(app) -> None:
    """Install :class:`TrailingSlashRedirect` on *app*. Called once, by the shell's install."""
    app.add_middleware(TrailingSlashRedirect, application=app)
