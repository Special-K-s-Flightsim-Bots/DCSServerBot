"""Sorting and searching the console's tables — the ONE validated parameter layer.

``?sort=``, ``?dir=`` and ``?q=`` are USER INPUT arriving in the query string, so they are parsed
HERE, validated against explicit allow-lists, and turned into plain data a template can read — one
place to review, one place to test, and no template ever computes a direction or compares a column
key. The pattern is the log level filter's (:func:`services.webservice.readmodels.logtail.
normalise_level`): the URL says it, a closed vocabulary validates it, an unknown value silently
falls back to the default, and no arbitrary string is ever echoed back into the page or a link.

WHAT IS ALLOWED, and what happens to junk:

* ``sort`` — only the COLUMN KEYS OF THE TABLE IN QUESTION (see :data:`TABLE_COLUMNS`). A value
  that is not a column of that table is not an error: the table renders in its documented default
  order (``name``, ascending, for all four tables — the read model's own order, so nothing changes
  until the reader asks). The raw value is dropped, never carried into a link.
* ``dir`` — only ``asc``/``desc``; anything else is the default ``asc``.
* ``q`` — the players search text: trimmed, control characters removed, capped at
  :data:`MAX_QUERY_LENGTH`. It is echoed back (the form re-fills) and it round-trips through every
  link, both URL-encoded by :func:`urllib.parse.urlencode` — it never becomes a URL or attribute
  injection vector.
* ``log`` — the placement override, only ``bottom``/``right``/``expanded``.

TYPED COMPARATORS. Each column declares its kind once, next to its label (:class:`Column`), so the
sorting behaviour and the markup cannot drift apart: ``num`` columns compare as integers (9 before
10), ``text`` columns case-insensitively. The secondary key is always the row's NAME and it is
applied in the same direction for both orders (see :func:`sort_rows`), so equal keys produce a
deterministic order that never depends on the input sequence.

PURE AND REUSABLE. Nothing here imports HTTP, ``core`` or Discord, and nothing takes a
``Request``: :func:`view_params` takes a query MAPPING and :func:`view_state` takes a path, so the
standalone Servers/Nodes/Instances/Players pages can call the same layer with their own path and
their own rows (requirement 6). Sorting works on the already-built tuple views — no new query, and
no re-reading of anything.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Mapping, Sequence
from urllib.parse import urlencode

__all__ = [
    "MAX_QUERY_LENGTH", "MAX_SORT_LENGTH", "SORT_DIRECTIONS", "DEFAULT_SORT", "DEFAULT_DIRECTION",
    "LOG_POSITIONS", "TEXT", "NUM", "Column", "ColumnView", "TableView", "PlayerSearch",
    "ViewParams", "ViewState", "TABLE_COLUMNS", "TABLE_LABELS", "ALL_COLUMN_KEYS",
    "column_keys", "normalise_sort", "normalise_direction", "normalise_query",
    "normalise_position", "opposite_direction", "natural_direction",
    "sort_rows", "search_players", "column_link", "table_view", "view_state", "view_params",
]


def _(message: str) -> str:
    """Mark a string as translatable for the extractor; return it UNCHANGED.

    The console's extraction marker (see :func:`services.webservice.i18n._`): Babel reads the literal
    out of this module, while at runtime this is an identity, so the read model stays pure and has no
    language of its own. The translation happens where the string is RENDERED, by the per-language
    Jinja environment's own ``_`` (``{{ _(column.label) }}`` in the templates). Defined here rather
    than imported so this module keeps its zero-package-import purity.
    """
    return message

#: how long the players search text may be. Long enough for a name or a UCID, short enough that a
#: hostile query string cannot make the page or its links unreadable.
MAX_QUERY_LENGTH = 64

#: a sort key is a column key from a closed allow-list, so it has no room for prose
MAX_SORT_LENGTH = 32

#: the two directions, and the one in force when the URL says nothing
SORT_DIRECTIONS: tuple[str, ...] = ("asc", "desc")
DEFAULT_SORT = "name"
DEFAULT_DIRECTION = "asc"

#: the log placement override (the panel's own vocabulary, see the mockups' log header)
LOG_POSITIONS: tuple[str, ...] = ("bottom", "right", "expanded")

#: column kinds. Declared once per column, so the comparator and the header cannot disagree.
TEXT = "text"
NUM = "num"

#: worst-case words for the number of characters a string-typed cell may contribute; the comparator
#: only cares about the two kinds above. Marked translatable: the template's "Sorted by …"/"Sort by …"
#: tooltip composes them through the environment's ``_``, and the extraction marker here is what puts
#: them in the catalog (a ``_(…)`` call inside a Jinja ``{% trans %}`` variable is not extracted).
_DIRECTION_WORDS = {"asc": _("ascending"), "desc": _("descending")}


@dataclass(frozen=True, slots=True)
class Column:
    """One sortable column: the key in the URL, the label the header shows, and how it compares."""
    key: str
    label: str
    kind: str = TEXT


#: The sortable columns of each table, in the order the table renders them. The keys are the
#: ``?sort=`` vocabulary AND the per-table allow-list: a value that is not a key here is refused for
#: that table.
#:
#: ``nodes`` deliberately has no ``heartbeat`` column: the mockups draw one, but ``NodeView``
#: carries no heartbeat age (a read-model change, listed in the mockups' "would need new backend
#: data"), and a sort key with no source would be a control that lies. ``master``/``online`` are
#: equally not offered — they are state the row already shows as a dot and a tag.
TABLE_COLUMNS: dict[str, tuple[Column, ...]] = {
    "servers": (Column("name", _("Server")), Column("node", _("Node")), Column("status", _("Status")),
                Column("mission", _("Mission")), Column("elapsed", _("Elapsed"), NUM),
                Column("players", _("Players"), NUM)),
    "nodes": (Column("name", _("Node")), Column("instances", _("Instances"), NUM),
              Column("servers", _("Servers"), NUM), Column("players", _("Players"), NUM)),
    "instances": (Column("name", _("Instance")), Column("node", _("Node")), Column("server", _("Server")),
                  Column("dcs_port", _("DCS port"), NUM), Column("webgui_port", _("WebGUI port"), NUM)),
    "players": (Column("name", _("Player")), Column("ucid", _("UCID")), Column("server", _("Server")),
                Column("slot", _("Slot"))),
}

TABLE_LABELS: dict[str, str] = {
    "servers": _("Servers"), "nodes": _("Nodes"), "instances": _("Instances"), "players": _("Players"),
}

#: every column key of every table. Used ONLY to decide whether a ``sort`` value is worth carrying
#: into a link at all (the per-table allow-list is still the authority on what a table honours).
ALL_COLUMN_KEYS: frozenset[str] = frozenset(
    column.key for columns in TABLE_COLUMNS.values() for column in columns)


# --------------------------------------------------------------------------------- cleaning

def _clean(value, limit: int) -> str:
    """*value* as text, control characters removed, trimmed, and capped at *limit* characters.

    Control characters (Unicode category ``C*`` — the C0/C1 sets, DEL, and the invisible
    formatting characters) are deleted rather than escaped: they are never part of a name, a UCID
    or a query a reader typed, and a raw one in a URL or an attribute is what makes a value
    hostile. Everything else survives, because the HTML escaping is the template's job and
    stripping it here would corrupt a legitimate search for, say, ``A-10C``.
    """
    if value is None:
        return ""
    text = str(value)
    cleaned = "".join(char for char in text if not unicodedata.category(char).startswith("C"))
    return cleaned.strip()[:limit]


def normalise_sort(table: str, value) -> str:
    """A column key of *table*, or :data:`DEFAULT_SORT`. Nothing else is ever passed on."""
    key = _clean(value, MAX_SORT_LENGTH)
    return key if key in column_keys(table) else DEFAULT_SORT


def normalise_direction(value) -> str:
    """``asc`` or ``desc``; anything unrecognised is the default (``asc``)."""
    key = _clean(value, MAX_SORT_LENGTH).lower()
    return key if key in SORT_DIRECTIONS else DEFAULT_DIRECTION


def normalise_query(value) -> str:
    """The search text as it may be echoed and matched: trimmed, control-free, capped."""
    return _clean(value, MAX_QUERY_LENGTH)


def normalise_position(value) -> str | None:
    """A log placement override from :data:`LOG_POSITIONS`, or ``None`` (the computed default)."""
    key = _clean(value, MAX_SORT_LENGTH).lower()
    return key if key in LOG_POSITIONS else None


def column_keys(table: str) -> tuple[str, ...]:
    """The ``?sort=`` vocabulary of *table* — its allow-list, in render order."""
    return tuple(column.key for column in TABLE_COLUMNS.get(table, ()))


def _column(table: str, key: str) -> Column:
    for column in TABLE_COLUMNS.get(table, ()):
        if column.key == key:
            return column
    raise KeyError(f"{table} has no sortable column {key!r}")


def natural_direction(kind: str) -> str:
    """The direction a NEW column sorts by: numbers descending (the interesting end first), text
    ascending (A first). One function, so the rule is stated once."""
    return "desc" if kind == NUM else "asc"


def opposite_direction(direction: str) -> str:
    """The direction a click on the ACTIVE column asks for (validated first)."""
    return "asc" if normalise_direction(direction) == "desc" else "desc"


# ----------------------------------------------------------------------------- the parameters

@dataclass(frozen=True, slots=True)
class ViewParams:
    """The validated view parameters of one request.

    ``sort`` is kept as stated (cleaned) and validated PER TABLE by :meth:`sort_for`, because the
    allow-list is the table's: ``?sort=players`` is a Servers column, not an Instances one, and the
    same request has to answer honestly for both.
    """
    sort: str = ""
    direction: str = DEFAULT_DIRECTION
    query: str = ""
    position: str | None = None

    def sort_for(self, table: str) -> str:
        """The column *table* sorts by: the requested one when it is a column of *table*, else its
        default."""
        return normalise_sort(table, self.sort)

    @property
    def known_sort(self) -> bool:
        """Whether the requested ``sort`` names a column of ANY table (worth carrying in a link)."""
        return self.sort in ALL_COLUMN_KEYS

    def carry(self, *, table: str | None = None) -> dict[str, str]:
        """The pairs to preserve in a link or a stream URL — VALIDATED values only.

        A ``sort`` that names no column is dropped rather than forwarded, so an attacker-shaped
        value cannot be laundered from the page's URL into the stream's. The direction rides along
        only when it is stated or when a known sort makes it meaningful, which is what keeps an
        unadorned page's stream URL exactly ``?level=…``.

        ``table`` is the table the URL being built will actually RENDER (the dashboard's active
        tab, for the stream URL). When it is given, a ``sort`` that is a column of some OTHER table
        is dropped too (Finding 3): ``?sort=dcs_port`` on the Servers tab sorts nothing there, so
        carrying it into the stream URL would state a view the page never showed. ``table=None``
        keeps the wider "names a column of any table" rule, which is what a link that does not yet
        know its target wants.
        """
        honoured = (self.known_sort if table is None
                    else bool(self.sort) and self.sort in column_keys(table))
        carried: dict[str, str] = {}
        if honoured:
            carried["sort"] = self.sort
            carried["dir"] = self.direction
        elif self.direction != DEFAULT_DIRECTION:
            carried["dir"] = self.direction
        if self.query:
            carried["q"] = self.query
        if self.position:
            carried["log"] = self.position
        return carried


def view_params(query: Mapping | None) -> ViewParams:
    """Parse ``sort``/``dir``/``q``/``log`` out of a query mapping into :class:`ViewParams`.

    *query* is a plain mapping (``request.query_params``, or a dict in a test) — no ``Request``
    object, so this module stays HTTP-free. A missing mapping is the same as an empty one.
    """
    lookup = query if hasattr(query, "get") else {}
    return ViewParams(
        sort=_clean(lookup.get("sort"), MAX_SORT_LENGTH),
        direction=normalise_direction(lookup.get("dir")),
        query=normalise_query(lookup.get("q")),
        position=normalise_position(lookup.get("log")),
    )


# -------------------------------------------------------------------------------- the sorting

def _cell(table: str, key: str, row):
    """The raw value *row* carries for column *key* of *table*. Raises for an unlisted pair — the
    allow-list is checked before this is ever called, so reaching the raise is a programming
    error, not a user input problem."""
    if table == "servers":
        if key == "name":
            return row.name
        if key == "node":
            return row.node_name
        if key == "status":
            return row.status.word
        if key == "mission":
            return row.mission_name or ""
        if key == "elapsed":
            return row.mission_seconds
        if key == "players":
            return row.players
    elif table == "nodes":
        if key == "name":
            return row.name
        if key == "instances":
            return row.instances
        if key == "servers":
            return row.servers
        if key == "players":
            return row.players
    elif table == "instances":
        if key == "name":
            return row.name
        if key == "node":
            return row.node_name
        if key == "server":
            return row.server_name or ""
        if key == "dcs_port":
            return row.dcs_port
        if key == "webgui_port":
            return row.webgui_port
    elif table == "players":
        if key == "name":
            return row.name
        if key == "ucid":
            return row.ucid
        if key == "server":
            return row.server_name
        if key == "slot":
            return row.slot
    raise KeyError(f"no cell for {table}.{key}")


def _number(value) -> int:
    """A numeric cell as an int. ``None`` (an unknown port) is 0 and sorts first ascending."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _text(value) -> str:
    return "" if value is None else str(value)


def sort_rows(table: str, rows: Sequence, sort: str = DEFAULT_SORT,
              direction: str = DEFAULT_DIRECTION) -> list:
    """*rows* ordered by the validated column and direction. Never raises on input, never mutates.

    THE SECONDARY KEY IS THE NAME, ASCENDING IN BOTH DIRECTIONS. The list is first ordered by name
    (the stable, complete tie-breaker — every table has a name) and then by the requested column,
    using a STABLE sort. Sorting by the primary in reverse therefore cannot shuffle the ties: equal
    keys always come out in name order, whichever way the primary runs, and the result never
    depends on the order the read model happened to hand the rows over in.
    """
    key = normalise_sort(table, sort)
    direction = normalise_direction(direction)
    kind = _column(table, key).kind

    def name_key(row):
        return _text(_cell(table, "name", row)).casefold()

    def primary_key(row):
        value = _cell(table, key, row)
        return _number(value) if kind == NUM else _text(value).casefold()

    ordered = sorted(rows, key=name_key)
    ordered.sort(key=primary_key, reverse=(direction == "desc"))
    return ordered


# --------------------------------------------------------------------------------- the search

@dataclass(frozen=True, slots=True)
class PlayerSearch:
    """The players search result: the matched rows, the query, and BOTH counts.

    ``filtered`` alone would let the page imply the fleet shrank ("3 players online"), so the
    unfiltered ``total`` is carried next to it and the sentence is built from the same record the
    table is built from.
    """
    rows: tuple
    query: str
    total: int
    filtered: int

    @property
    def active(self) -> bool:
        return bool(self.query)

    @property
    def label(self) -> str:
        """The one statement of the filter, in words (the template renders it, never composes it)."""
        return (f"Filter: name or UCID contains {self.query} · "
                f"{self.filtered} of {self.total} players")


def search_players(rows: Sequence, query=None) -> PlayerSearch:
    """Filter *rows* by a case-insensitive substring on the name OR the UCID.

    An empty (or whitespace-only, or control-only) query means NO filter: every row is returned and
    the result reports ``active`` False, so an empty search can never look like "no players".
    """
    needle = normalise_query(query).casefold()
    if not needle:
        return PlayerSearch(rows=tuple(rows), query="", total=len(rows), filtered=len(rows))
    matched = tuple(row for row in rows
                    if needle in _text(getattr(row, "name", "")).casefold()
                    or needle in _text(getattr(row, "ucid", "")).casefold())
    return PlayerSearch(rows=matched, query=normalise_query(query), total=len(rows),
                        filtered=len(matched))


# ---------------------------------------------------------------------------------- the links

@dataclass(frozen=True, slots=True)
class ColumnView:
    """One header cell, as DATA: what to show, whether it is the active sort, and the link it
    should render. The template computes nothing — not the direction, not the arrow, not the
    ``aria-sort``."""
    key: str
    label: str
    kind: str
    link: str
    active: bool
    direction: str          # the direction THIS link applies when clicked
    arrow: str              # ▼ / ▲ on the active column, ↕ on the others
    aria_sort: str          # descending / ascending / none
    title: str


@dataclass(frozen=True, slots=True)
class TableView:
    """One table: its rows in the validated order, and its columns as renderable data."""
    name: str
    label: str
    columns: tuple[ColumnView, ...]
    rows: tuple
    sort: str
    direction: str
    sorted_label: str       # "Sorted by Players, descending"


@dataclass(frozen=True, slots=True)
class ViewState:
    """EVERYTHING the template needs about the view, in one value (requirement 4).

    ``tables`` holds all four tables of the console, each validated against its own allow-list, so
    the dashboard tabs and the standalone pages read the same shape. ``search`` carries the players
    counts; ``params`` is what a form must carry along (the level and any active filter) so a search
    does not throw away the view.
    """
    query: str
    params: dict
    tables: dict
    search: PlayerSearch

    def table(self, name: str) -> TableView:
        """The table of that name (a KeyError is a caller asking for a table that does not exist)."""
        return self.tables[name]


def _preserved(params: ViewParams | None, carry: Mapping | None) -> dict[str, str]:
    """The validated pairs a link must keep: whatever the caller proved valid, plus the view's own
    ``q``/``log``. ``sort``/``dir`` are set by the link itself, so they are not carried here."""
    kept = {str(key): str(value) for key, value in dict(carry or {}).items() if value not in
            (None, "")}
    if params is not None:
        if params.query:
            kept["q"] = params.query
        if params.position:
            kept["log"] = params.position
    return kept


def column_link(path: str, table: str, key: str, *, sort: str = DEFAULT_SORT,
                direction: str = DEFAULT_DIRECTION, params: Mapping | None = None) -> str:
    """The href for a column header: *path* plus the validated view state a click should produce.

    The ACTIVE column asks for the opposite direction; a NEW column asks for its natural one
    (numbers descending, text ascending). Every value in the URL is one this module validated, and
    the whole query is built by :func:`urllib.parse.urlencode`, so the search text is percent-
    encoded rather than concatenated — it cannot break out of the attribute or add a parameter.
    """
    column = _column(table, normalise_sort(table, key))
    current = normalise_sort(table, sort)
    direction = normalise_direction(direction)
    wanted = opposite_direction(direction) if column.key == current \
        else natural_direction(column.kind)

    query: dict[str, str] = dict(params or {})
    query.pop("sort", None)
    query.pop("dir", None)
    query["sort"] = column.key
    query["dir"] = wanted
    return f"{path}?{urlencode(query)}"


def _column_view(path: str, table: str, column: Column, *, sort: str,
                 direction: str, params: Mapping | None) -> ColumnView:
    active = column.key == sort
    wanted = (opposite_direction(direction) if active
              else natural_direction(column.kind))
    if active:
        arrow = "▼" if direction == "desc" else "▲"
        aria = _DIRECTION_WORDS[direction]
        title = (f"Sorted by {column.label}, {aria} — "
                 f"click to sort {_DIRECTION_WORDS[wanted]}")
    else:
        arrow = "↕"
        aria = "none"
        title = f"Sort by {column.label}, {_DIRECTION_WORDS[wanted]}"
    return ColumnView(key=column.key, label=column.label, kind=column.kind,
                      link=column_link(path, table, column.key, sort=sort, direction=direction,
                                       params=params),
                      active=active, direction=wanted, arrow=arrow, aria_sort=aria, title=title)


def table_view(path: str, table: str, rows: Sequence, *, sort: str = DEFAULT_SORT,
               direction: str = DEFAULT_DIRECTION, params: Mapping | None = None) -> TableView:
    """One table as the template reads it: sorted rows plus every header's link and state."""
    key = normalise_sort(table, sort)
    direction = normalise_direction(direction)
    columns = tuple(_column_view(path, table, column, sort=key, direction=direction, params=params)
                    for column in TABLE_COLUMNS.get(table, ()))
    label = _column(table, key).label
    return TableView(name=table, label=TABLE_LABELS.get(table, table), columns=columns,
                     rows=tuple(sort_rows(table, rows, key, direction)),
                     sort=key, direction=direction,
                     sorted_label=f"Sorted by {label}, {_DIRECTION_WORDS[direction]}")


def view_state(path: str, rows: Mapping | None = None, *, params: ViewParams | None = None,
               carry: Mapping | None = None) -> ViewState:
    """The whole validated view for one render (requirement 4).

    *rows* is a mapping of table name to the ALREADY-BUILT tuple views (``servers``, ``nodes``,
    ``instances``, ``players``) — the read models' output, untouched. Missing tables render empty.
    *path* is where the table lives (``/`` for the dashboard, ``/players`` for the standalone
    page), and *carry* holds any further validated pair a link must keep (the resolved level).
    """
    params = params if params is not None else ViewParams()
    preserved = _preserved(params, carry)
    source = dict(rows or {})
    search = search_players(source.get("players") or (), params.query)
    source["players"] = search.rows
    tables = {name: table_view(path, name, source.get(name) or (),
                               sort=params.sort_for(name), direction=params.direction,
                               params=preserved)
              for name in TABLE_COLUMNS}
    return ViewState(query=params.query, params=preserved, tables=tables, search=search)
