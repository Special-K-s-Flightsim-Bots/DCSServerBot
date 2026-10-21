"""The Bans page's read model: one row per ban ENTRY, shaped for the table.

PURE — no await, no HTTP, no Discord, and duck-typed inputs, like every other read model in this
package. The rows come from the service bus, which ALREADY groups the N rows of one ban (same
``ucid``, ``banned_at`` and ``reason``) back into a single entry (``ServiceBus.bans``); this module
only SHAPES that entry for the console — it never re-queries and never re-groups, so there is no
second spelling of "one ban".

WHAT AN ENTRY CARRIES. ``server_name`` is a LIST of server names for a scoped ban, or ``None`` for
a bot-wide one (the bus's own grouping). ``banned_by`` names who wrote the row; the DGSA sync writes
it as the cloud plugin's own name (:data:`DGSA_BANNED_BY`). The name is the ``players`` table's own
name for the ucid (the bus LEFT JOINs it), and an unknown ucid renders the UCID rather than a blank.

THE PERMANENT SENTINEL. A ban with no expiry is stored with ``banned_until`` in the year 9999
(``ServiceBus.ban``), so :func:`permanent` is the ONE reading of that fact and the row renders the
word ``never`` instead of a timestamp nobody can act on. The timestamps are NAIVE UTC, so every
stamp states ``UTC`` explicitly — the console renders no client-side date formatting.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .access import text

__all__ = [
    "DGSA_BANNED_BY", "DGSA_PARAM", "DGSA_ON", "PERMANENT_YEAR", "NO_BANS_MESSAGE", "ALL_SERVERS",
    "NEVER", "UNTIL_FORMAT", "BanView", "permanent", "expires_utc", "ban_views", "dgsa_requested",
    "split_dgsa",
]


def _(message: str) -> str:
    """Mark a string as translatable for the extractor; return it UNCHANGED (see ``readmodels.views``)."""
    return message


#: The ``banned_by`` value the cloud plugin writes for a DGSA ban — the plugin's OWN name
#: (``plugins/cloud/commands.py`` writes ``banned_by=self.plugin_name``, and ``core/plugin.py``
#: derives that from the plugin directory name ``cloud``). The console may not import a plugin
#: module, so it keeps its own copy of the word here, and a test pins the two equal.
DGSA_BANNED_BY = "cloud"

#: THE VIEW OPTION's query pair: the parameter that reveals the DGSA rows and the one value that
#: turns it on (``?dgsa=1``). A GET parameter, never script — the option is linkable, bookmarkable
#: and works with JavaScript off.
DGSA_PARAM = "dgsa"
DGSA_ON = "1"

#: The sentinel year ``ServiceBus.ban`` writes for a ban with no expiry.
PERMANENT_YEAR = 9999

#: The empty-state sentence for the Bans table.
NO_BANS_MESSAGE = _("No bans are in place on the servers you manage.")

#: The scope word a bot-wide ban renders (a row that names no server).
ALL_SERVERS = _("all servers")

#: The word a ban with no expiry renders in place of a timestamp.
NEVER = _("never")

#: The format of a rendered stamp: a naive UTC timestamp, stated as UTC because the console does
#: no client-side date formatting (a bare stamp would state a time nobody can verify).
STAMP_FORMAT = "%Y-%m-%d %H:%M UTC"

#: The EDIT dialog's absolute-UTC text layout: the value its ``Expires (UTC)`` field reads and
#: writes. A naive UTC wall-clock stamp, with no trailing ``UTC`` (the field's own label states it).
#: The SECONDS are part of the layout: a days-created ban stores seconds, so the value that writes the
#: entry's own end back must carry them or a reason-only save would truncate the stored instant.
UNTIL_FORMAT = "%Y-%m-%d %H:%M:%S"


def _field(entry: Any, name: str) -> Any:
    """One field of *entry*, whether it is a mapping (the bus's dicts) or a duck-typed object."""
    if isinstance(entry, Mapping):
        return entry.get(name)
    return getattr(entry, name, None)


def permanent(entry: Any) -> bool:
    """Whether *entry* never expires (``banned_until`` in the sentinel year).

    The ONE reading of the permanent sentinel: a ban with no expiry is stored with a
    ``banned_until`` in the year 9999, and the table renders the word ``never`` for it.
    """
    until = _field(entry, "banned_until")
    return getattr(until, "year", None) == PERMANENT_YEAR


def expires_utc(entry: Any) -> str:
    """The entry's OWN end as the EDIT dialog's UTC text value — ``''`` when permanent.

    The dialog's field states an ABSOLUTE UTC stamp, so the pre-filled value is the entry's own
    ``banned_until`` and NOTHING ELSE: saving the form untouched writes the same instant back, so a
    reason-only edit cannot drift the expiration. An entry with no expiry (the year-9999 sentinel)
    pre-fills empty, which is the permanent value the field means by empty.
    """
    until = _field(entry, "banned_until")
    if not isinstance(until, datetime) or permanent(entry):
        return ""
    return until.strftime(UNTIL_FORMAT)


def _stamp(value: Any) -> str:
    """A ``banned_at``/``banned_until`` value as a rendered UTC stamp, or ``''`` when unreadable."""
    if not isinstance(value, datetime):
        return ""
    return value.strftime(STAMP_FORMAT)


def _iso(value: Any) -> str:
    """The ISO identity of a ban's own timestamp — the key half the write posts back."""
    if not isinstance(value, datetime):
        return ""
    return value.isoformat()


def _servers(entry: Any) -> tuple[str, ...]:
    """The server names a ban covers, in the order the bus grouped them (empty for a bot-wide ban)."""
    raw = _field(entry, "server_name")
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,) if raw else ()
    return tuple(text(name) for name in raw if text(name))


@dataclass(frozen=True, slots=True)
class BanView:
    """One ban ENTRY, shaped for the table.

    ``name`` is the ``players`` table's name for the ucid, falling back to the UCID itself (never a
    blank). ``banned_at``/``banned_until`` are rendered UTC stamps; ``permanent`` says the second is
    the word ``never`` instead. ``scope`` is the server names or the ``all servers`` word, with
    ``bot_wide`` telling the two apart for the markup. ``dgsa`` marks a row the cloud plugin wrote.
    ``key`` is the entry's IDENTITY (``<ucid>@<banned_at ISO>``) — the key its LIFT control and its
    confirm token are built on.
    """
    name: str
    ucid: str
    reason: str
    banned_at: str
    banned_until: str
    permanent: bool
    scope: str
    bot_wide: bool
    servers: tuple[str, ...]
    dgsa: bool
    key: str
    banned_at_key: str
    #: the EDIT dialog's pre-filled expiration: the entry's OWN end as a UTC text stamp, or ``''``
    #: for a ban that never expires. The field states an ABSOLUTE UTC datetime, so this is the value
    #: that writes the ban's own instant back when the form is saved untouched.
    expires_utc: str


def _view(entry: Any) -> BanView:
    """One entry as the table reads it — nothing computed in the template."""
    ucid = text(_field(entry, "ucid"))
    name = text(_field(entry, "name")) or ucid
    servers = _servers(entry)
    is_permanent = permanent(entry)
    banned_at_key = _iso(_field(entry, "banned_at"))
    return BanView(
        name=name,
        ucid=ucid,
        reason=text(_field(entry, "reason")),
        banned_at=_stamp(_field(entry, "banned_at")),
        banned_until=NEVER if is_permanent else _stamp(_field(entry, "banned_until")),
        permanent=is_permanent,
        scope=ALL_SERVERS if not servers else ", ".join(servers),
        bot_wide=not servers,
        servers=servers,
        dgsa=text(_field(entry, "banned_by")) == DGSA_BANNED_BY,
        key=f"{ucid}@{banned_at_key}",
        banned_at_key=banned_at_key,
        expires_utc=expires_utc(entry),
    )


def ban_views(entries: Sequence[Any] | None) -> tuple[BanView, ...]:
    """The ban entries as table rows, in the order the bus returned them.

    An entry whose ``banned_at`` is not a readable timestamp has NO identity to post back: its
    ``banned_at_key`` would be empty, and an empty key matches any empty candidate in the LIFT's
    confirm re-read (``pages/actions.py``: ``candidate.banned_at_key == banned_at``). Such an entry
    is not a row, so every rendered row's identity is total and unique.
    """
    views = (_view(entry) for entry in (entries or ()))
    return tuple(view for view in views if view.banned_at_key)


def dgsa_requested(query: Any) -> bool:
    """Whether *query* asks for the DGSA rows (``?dgsa=1``).

    Only the ticked box's own value counts. An absent pair — what the form sends with the box
    cleared — and every other value render the default view, which withholds the DGSA rows.
    """
    if not hasattr(query, "get"):
        return False
    return text(query.get(DGSA_PARAM)) == DGSA_ON


def split_dgsa(views: Sequence[BanView], *, show: bool) -> tuple[tuple[BanView, ...], int]:
    """The rows to RENDER and how many DGSA rows were WITHHELD.

    The filter runs INSIDE the set the caller's scoped read returned — a DGSA ban is bot-wide, so
    only a global caller's set holds any — which is what keeps the withheld count from disclosing a
    ban outside the caller's view: it counts rows that caller could already see.

    With *show* every row is rendered and nothing is withheld, so the page states no count.
    """
    rows = tuple(views or ())
    if show:
        return rows, 0
    shown = tuple(view for view in rows if not getattr(view, "dgsa", False))
    return shown, len(rows) - len(shown)
