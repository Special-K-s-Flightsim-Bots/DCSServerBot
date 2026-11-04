"""Audit: the cluster's own trail, one row per entry, shaped for the console.

PURE — no await, no HTTP, no database. The rows come from the page's one read (``pages/audit``,
which owns the SQL like ``pages/server_detail`` owns its single fact), and this module only PARSES
the request's filters and SHAPES the records, so the table, the count sentence and the pager cannot
disagree about what was asked.

THE FILTERS ARE A CLOSED, FORGIVING VOCABULARY. Four text filters — node, server, actor, and the
event text — plus a time RANGE. Every one of them is a GET parameter, so a view is linkable and works
with JavaScript off; an unreadable value is IGNORED rather than refused (the operator gets the wider
trail, and the page says which filters it actually applied), because a mistyped date is not an error
the trail can explain.

A DATE WITH NO TIME MEANS THE WHOLE DAY: ``since=2026-11-02`` starts at 00:00 that day and
``until=2026-11-02`` runs THROUGH 23:59:59 — an operator asking for "yesterday" must not get a range
that excludes the day they named. The stamps the page renders are UTC, which is what the column
holds (``audit.time`` is ``now() AT TIME ZONE 'utc'``), so the range compares in the same clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as day_time
from typing import Any, Mapping, Sequence

from .access import attr, text
from .bans import STAMP_FORMAT

__all__ = ["PAGE_SIZE", "FILTER_PARAMS", "MAX_FILTER_LENGTH", "DATE_FORMATS", "AuditFilters",
           "filters_from", "moment", "AuditRow", "AuditView", "audit_rows", "audit_view",
           "empty_message", "range_sentence", "select_values", "filter_options"]

#: how many entries one page carries. The audit trail grows without limit, so the page is always
#: cut by the read — never rendered whole and hidden by the browser.
PAGE_SIZE = 100

#: the filter names, in the order the form shows them. One spelling, read by the parser, the form
#: and the links the pager carries.
FILTER_PARAMS: tuple[str, ...] = ("node", "server", "actor", "q", "since", "until")

#: how much of a filter value is kept. Long enough for a node or server name, short enough that a
#: hostile query string cannot make the page or its links unreadable.
MAX_FILTER_LENGTH = 64

#: what a time filter accepts, most specific first. A bare date is the whole day (see the module
#: docstring); the rest are the shapes an operator or another console view hands over.
DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                                 "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S")


def _value(raw: Any) -> str:
    """*raw* as a trimmed, capped one-line string — the value the read is given."""
    return text(raw)[:MAX_FILTER_LENGTH].strip()


def moment(raw: Any, *, end_of_day: bool = False) -> datetime | None:
    """*raw* as the instant a filter asks for, or ``None`` when it is not a value this page reads.

    A bare DATE is the WHOLE day: its start when *end_of_day* is false, its last second when true.
    Anything unreadable is ``None`` — the caller drops the bound rather than refusing the page.
    """
    value = _value(raw)
    if not value:
        return None
    for shape in DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, shape)
        except ValueError:
            continue
        if shape == "%Y-%m-%d" and end_of_day:
            return datetime.combine(parsed.date(), day_time(23, 59, 59))
        return parsed
    return None


@dataclass(frozen=True, slots=True)
class AuditFilters:
    """The filters in force for one render — the values the read was given AND the page shows."""

    node: str = ""
    server: str = ""
    actor: str = ""
    query: str = ""
    since: datetime | None = None
    #: INCLUSIVE: the read asks for ``time <= until``, so a bare date covers its own last second
    until: datetime | None = None

    @property
    def any(self) -> bool:
        """Whether the caller narrowed the trail at all — the page says so when they did."""
        return any((self.node, self.server, self.actor, self.query, self.since, self.until))

    @property
    def carry(self) -> dict[str, str]:
        """The filters as query-string values, for a link that must not lose them.

        An empty filter is ABSENT rather than blank, so the URL stays readable and a link built
        from it cannot pin a filter nobody asked for.
        """
        carried = {name: value for name, value in (("node", self.node), ("server", self.server),
                                                   ("actor", self.actor), ("q", self.query))
                   if value}
        if self.since is not None:
            carried["since"] = self.since.strftime("%Y-%m-%d")
        if self.until is not None:
            carried["until"] = self.until.strftime("%Y-%m-%d")
        return carried


def filters_from(params: Mapping[str, Any]) -> AuditFilters:
    """The :class:`AuditFilters` a request asks for. Unknown and unreadable values are ignored."""
    return AuditFilters(node=_value(params.get("node")), server=_value(params.get("server")),
                        actor=_value(params.get("actor")), query=_value(params.get("q")),
                        since=moment(params.get("since")),
                        until=moment(params.get("until"), end_of_day=True))


@dataclass(frozen=True, slots=True)
class AuditRow:
    """ONE audit entry as the table renders it.

    ``actor`` is the entry's own column and is EMPTY for every entry written before that column
    existed — the page renders nothing there rather than guessing an actor back out of the event
    text. ``discord_id``/``ucid`` name the player or member the entry is about, when it has one.
    """

    id: int
    time: str
    node: str
    actor: str
    server: str
    event: str
    discord_id: str
    ucid: str


def _field(record: Any, name: str, default: Any = None) -> Any:
    """One field of a read record — a MAPPING (psycopg's ``dict_row``) or an object alike."""
    if isinstance(record, Mapping):
        return record.get(name, default)
    return attr(record, name, default)


def _stamp(value: Any) -> str:
    """An ``audit.time`` value as a rendered UTC stamp, or ``''`` when unreadable."""
    if not isinstance(value, datetime):
        return ""
    return value.strftime(STAMP_FORMAT)


def _identity(value: Any) -> str:
    """A numeric id as text, or ``''`` — ``-1`` is the column's own "no Discord" value."""
    if value is None or value == "" or value == -1:
        return ""
    return text(value)


def audit_rows(records: Sequence[Any]) -> tuple[AuditRow, ...]:
    """The read's records as table rows. A record missing a field renders an empty cell."""
    rows = []
    for index, record in enumerate(records or ()):
        rows.append(AuditRow(
            id=int(_field(record, "id", 0) or 0) or index,
            time=_stamp(_field(record, "time")),
            node=text(_field(record, "node")),
            actor=text(_field(record, "actor")),
            server=text(_field(record, "server_name")),
            event=text(_field(record, "event")),
            discord_id=_identity(_field(record, "discord_id")),
            ucid=text(_field(record, "ucid"))))
    return tuple(rows)


def select_values(values: Sequence[str], current: str) -> tuple[str, ...]:
    """The choices of a filter select, KEEPING the value in force when the list does not hold it.

    A server can be renamed or deleted while a filter naming it is linked or bookmarked. Dropping the
    value would silently turn the filter off — the reader would see a wider trail and no sign that the
    thing they asked for is gone — so the current value leads the list as its own choice.
    """
    kept = [value for value in values if value]
    if current and current not in kept:
        return (current, *kept)
    return tuple(kept)


def filter_options(state: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(node names, server names)`` for the filter selects, read off the caller's own Overview.

    The names come from the same in-process source the dashboard renders, so a node or server the
    cluster is not carrying is not offered. An unreadable Overview yields two empty lists: the select
    then offers the "any" choice and, through :func:`select_values`, whatever is already in force.
    """
    nodes = {attr(item, "name", "") for item in (attr(state, "nodes", ()) or ())}
    servers = {attr(item, "name", "") for item in (attr(state, "servers", ()) or ())}
    return (tuple(sorted(text(name) for name in nodes if text(name))),
            tuple(sorted(text(name) for name in servers if text(name))))


def empty_message() -> str:
    """The empty-state copy for a trail nothing matched (the table reads ``empty[table.name]``)."""
    return _("No audit entry matches this view.")


@dataclass(frozen=True, slots=True)
class AuditView:
    """One page of the trail plus the figures a page needs to state where it sits."""

    rows: tuple[AuditRow, ...]
    total: int
    page: int
    pages: int
    per_page: int
    filters: AuditFilters


def audit_view(data: Mapping[str, Any] | None) -> AuditView:
    """The read's answer as a page. A missing or malformed answer is an EMPTY page, never an error."""
    answer = data or {}
    rows = audit_rows(answer.get("rows") or ())
    total = int(answer.get("total") or len(rows))
    per_page = int(answer.get("per_page") or PAGE_SIZE) or PAGE_SIZE
    pages = int(answer.get("pages") or max((total + per_page - 1) // per_page, 1))
    return AuditView(rows=rows, total=total, page=int(answer.get("page") or 1),
                     pages=max(pages, 1), per_page=per_page,
                     filters=answer.get("filters") or AuditFilters())


def range_sentence(view: AuditView) -> str:
    """The one sentence above the table: how much of the trail this page is showing.

    It states the TOTAL behind the page and, when the caller narrowed the trail, that they did —
    a filtered list that reads as the whole trail would let an operator believe nothing else
    happened.
    """
    filters = view.filters
    if filters.since is not None and filters.until is not None:
        span = _("from %(since)s through %(until)s") % {
            "since": filters.since.strftime("%Y-%m-%d"),
            "until": filters.until.strftime("%Y-%m-%d")}
    elif filters.since is not None:
        span = _("since %(since)s") % {"since": filters.since.strftime("%Y-%m-%d")}
    elif filters.until is not None:
        span = _("through %(until)s") % {"until": filters.until.strftime("%Y-%m-%d")}
    else:
        span = ""
    if filters.any:
        narrowed = _("filtered") if not span else span
        return _("Entries: %(total)s · %(narrowed)s") % {"total": view.total, "narrowed": narrowed}
    return _("Entries: %(total)s") % {"total": view.total}


def _(message: str) -> str:
    """Mark a string as translatable for the extractor; return it UNCHANGED (see ``readmodels``)."""
    return message
