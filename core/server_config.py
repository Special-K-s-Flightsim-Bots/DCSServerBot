"""The MANAGER deny-list for a server's configuration — ONE declaration, read by the write ACTIONS and
by the console PAGE.

A MANAGER (an identity that reaches a server only through the ``managed_by`` scope and holds no cluster
role) may write every DCS setting EXCEPT the ones named here. It is a DENY-LIST, not an allow-list: the
set is expected to grow, so it lives in ONE place read by both sides, and a future restriction is a
one-line change the two can never disagree about.

It lives in ``core`` because both readers can import ``core`` without inverting the layering: the
plugin action may not import a service package, and the console's read models are kept ``core``-free
(``services/webservice/readmodels/serverconfig``).

The two readers are:

* the BOT-SIDE write actions — ``plugins/scheduler/actions.set_server_config`` and
  ``set_server_channels`` — where the denial is ENFORCED server-side, so a crafted POST or a direct
  call is refused too (hiding a form is not the control); and
* the CONSOLE page that renders a manager's view of the tab (the denied rows and the channels card are
  OMITTED — not built at all — rather than rendered not editable).
"""
from __future__ import annotations

from typing import Iterable

__all__ = ["CHANNELS_ITEM", "MANAGER_DENIED", "MANAGER_DENIED_KEYS", "manager_denial"]

#: The reserved ITEM name for the CHANNELS WRITE (``set_server_channels``) taken as a WHOLE — never a
#: DCS setting key. ``DCS_FIELDS`` has no ``channels`` field (the channels live in ``servers.yaml``,
#: not ``serverSettings.lua``), so a denied item is either one config key (e.g. ``port``) or this one
#: OPERATION token.
CHANNELS_ITEM = "channels"

#: THE DECLARATION. Each entry maps a denied ITEM (a config key, or :data:`CHANNELS_ITEM` for the
#: channels write as a whole) to the typed sentence a manager is REFUSED with and the page shows beside
#: the row.
MANAGER_DENIED: dict[str, str] = {
    "port": "Changing the server port requires the Admin role — a manager may not set it.",
    CHANNELS_ITEM: ("Changing the Discord channels requires the Admin role — a manager may not set "
                    "them."),
}

#: The denied CONFIG KEYS — the subset of :data:`MANAGER_DENIED` that are DCS settings — which
#: ``set_server_config`` filters a submitted ``values`` mapping against. DERIVED from the declaration,
#: so it cannot drift from it.
MANAGER_DENIED_KEYS: frozenset[str] = frozenset(
    item for item in MANAGER_DENIED if item != CHANNELS_ITEM)


def manager_denial(items: Iterable[str]) -> str | None:
    """The typed refusal sentence for the FIRST denied item in *items*, or ``None`` when none is.

    The check is on the ITEM SET the caller is trying to write: a submitted denied item refuses the
    WHOLE write — the value never lands and the sentence reaches the operator, never a silent drop
    (which would report success for a change that did not happen) and never a 500.
    """
    for item in items:
        sentence = MANAGER_DENIED.get(item)
        if sentence is not None:
            return sentence
    return None
