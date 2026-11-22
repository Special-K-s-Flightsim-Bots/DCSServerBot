"""The Chat tab's read model: the ring records the listener keeps, as displayable lines.

The mission plugin's ``onChatMessage`` listener records every line into a bounded in-memory ring
(``plugins.mission.chatlog``) and the console reads it back through the ``chat_history`` action. A
record is ``{"seq", "ts", "ucid", "name", "side", "audience", "text"}``; ``audience`` is ``all``,
``red`` or ``blue`` and ``seq`` increases monotonically per server, so a follower can ask for the
lines after the last one it saw.

This module owns the record SHAPE as the template sees it and the two filters. It does not read the
ring: the action does, and the page's route awaits it.

A malformed record is SKIPPED, never raised: the text is player-supplied and a record that arrived
without the fields the tab needs is not worth a 500.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AUDIENCE_WORDS", "AUDIENCES", "ChatLine", "MISSING_SENTENCE", "NO_MATCH_SENTENCE",
           "TAIL_LINES", "filter_lines", "from_record", "matches", "normalise_filter"]

#: how many lines one tail render carries
TAIL_LINES = 200

#: the audience vocabulary the listener records; ``all`` is the default text colour
AUDIENCES = ("all", "red", "blue")


def _(message: str) -> str:
    """Mark a string as translatable for the extractor; return it UNCHANGED."""
    return message


#: THE CHIP'S WORD on a non-``all`` line. TRANSLATABLE; the template renders it through ``_``.
AUDIENCE_WORDS: dict[str, str] = {"red": _("Red only"), "blue": _("Blue only")}


#: honest sentences — TRANSLATABLE; the route renders them in the request's language
MISSING_SENTENCE = _("No chat lines yet — the tab fills as players talk on this server.")
NO_MATCH_SENTENCE = _("No lines match this search.")


@dataclass(frozen=True)
class ChatLine:
    """One record as the template reads it. ``audience`` is ``all`` / ``red`` / ``blue``."""

    ts: str
    name: str
    side: str
    audience: str
    text: str

    @property
    def clock(self) -> str:
        """The time part of ``ts`` — everything after the ``T``, or the whole stamp."""
        stamp = self.ts or ""
        return stamp.split("T", 1)[1] if "T" in stamp else stamp

    @property
    def audience_class(self) -> str:
        """The CSS class the audience maps to: ``aud-red`` / ``aud-blue``; empty for ``all``."""
        return f"aud-{self.audience}" if self.audience in ("red", "blue") else ""

    @property
    def audience_word(self) -> str:
        """The chip's English word for this audience; empty for ``all`` (no chip there)."""
        return AUDIENCE_WORDS.get(self.audience, "")


def from_record(record) -> ChatLine | None:
    """One ring record as a :class:`ChatLine`, or ``None`` when it cannot be shown."""
    if not isinstance(record, dict):
        return None
    text = str(record.get("text") or "")
    if not text:
        return None
    audience = str(record.get("audience") or "all").strip().lower()
    if audience not in AUDIENCES:
        audience = "all"
    return ChatLine(ts=str(record.get("ts") or ""),
                    name=str(record.get("name") or ""),
                    side=str(record.get("side") or ""),
                    audience=audience,
                    text=text)


def normalise_filter(value) -> str:
    """A filter value as a casefolded substring; empty when absent."""
    return str(value or "").strip().casefold()


def matches(line: ChatLine, who: str = "", q: str = "") -> bool:
    """True when *line* satisfies BOTH filters (case-insensitive substring; empty = no filter)."""
    sender = normalise_filter(who)
    text = normalise_filter(q)
    if sender and sender not in normalise_filter(line.name):
        return False
    if text and text not in normalise_filter(line.text):
        return False
    return True


def filter_lines(lines, who: str = "", q: str = "") -> tuple[list[ChatLine], int]:
    """The lines matching both filters, plus the UNFILTERED count for the ``N of M`` sentence."""
    total = len(lines)
    if not normalise_filter(who) and not normalise_filter(q):
        return list(lines), total
    return [line for line in lines if matches(line, who, q)], total
