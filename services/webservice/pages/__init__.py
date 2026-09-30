"""The core pages of the admin web UI (card C3).

Each module owns one page: its route, the capability that gates it, the nav item that advertises it
and the copy it renders. The shell registers them (``services/webservice/shell.py::_core_router``)
exactly as it registers the auth routes — flat, on the app's own router, never through a nested
``include_router`` (a nested include is invisible to the registrar's enumeration on the pinned
FastAPI, which is the one source of truth the route/nav tests read).

A page module receives the ``Registrar``'s router and nothing else: no FastAPI app, so a page
cannot add middleware, mount a path or reach past the access gate.
"""
from __future__ import annotations

__all__: list[str] = []