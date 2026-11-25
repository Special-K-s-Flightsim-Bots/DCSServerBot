"""The public site's "my statistics" — the viewer's own figures on THIS site's servers.

EVERY FIGURE IS ``site_reads.my_statistics``'S ANSWER, run once per request for the signed-in
identity's own Discord id: the UCIDs that Discord link owns on this site's servers, their combined
totals, their per-airframe hours and their per-server hours. The page composes and formats; it
never queries, and the formatting is :mod:`pages.site_player`'s — one rule for a figure, whether a
visitor reads somebody else's profile or their own.

WHAT AN ANONYMOUS VISITOR GETS: the page itself, with the sign-in control and no figure at all. It
is never a redirect and never a 403 — a search engine or a shared link must not land a stranger on
a login form, and a door that answers with a bounce is a door nobody can bookmark.

WHAT A SIGNED-IN VISITOR WITH NO RECORD HERE GETS: the same page over this site's servers, saying
there is no record. Not a refusal: the figures are already public on the pilot profile, so refusing
would protect nothing, and a refusal message would tell the visitor which group they are in.

THE BAND IS THE POINT. :data:`TEMPLATE` opens with the coloured "this is you" band so a viewer can
never mistake these figures for another pilot's; the band is rendered only for a resolved identity.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from .. import i18n, site_reads
from ..site_pages import (ME_AAR_PATH, ME_PATH, NAV_PATHS, SEARCH_PATH, identity, player_url, pool,
                          render_site_page, resolve_site, sign_in_href, sign_out_context, site_url)


from .site_overview import bars_chart
from .site_player import (DAY_MONTH, MONTH_YEAR, _record, airframes, count, discipline, hours,
                          last_flown, ratio, rating_view, recent, sentence, servers, sortie_count,
                          sortie_note)

__all__ = [
    "NAV_ID",
    "TEMPLATE",
    "PROGRESS_PERIODS",
    "DEFAULT_PROGRESS_PERIOD",
    "readouts",
    "campaigns",
    "period_switch",
    "standing_view",
    "progress_view",
    "view",
    "anonymous",
    "add_route",
]

log = logging.getLogger(__name__)

#: This page belongs to no menu entry: it is one visitor's own record, reached from the shell's
#: chip, so marking a nav id would highlight an entry the menu does not carry.
NAV_ID = ""

TEMPLATE = "site_me.html"


# --------------------------------------------------------------------------------- formatting

def readouts(totals: dict) -> list[dict]:
    """The five cumulative figures of a viewer's own record, in the profile page's own order.

    The labels and the two sentence notes are the pilot profile's msgids, so a figure is named the
    same way on both pages; a viewer with no record gets the same five rows at zero, because the
    strip is the page's shape and not a claim about the visitor.
    """
    return [
        {"key": "playtime", "value": count(hours(totals.get("playtime"))), "unit": i18n._("h"),
         "label": i18n._("Playtime"), "note": i18n._("on this site's servers")},
        {"key": "sessions", "value": count(totals.get("sessions")), "unit": None,
         "label": i18n._("Sessions"), "note": i18n._("recorded")},
        {"key": "sorties", "value": count(sortie_count(totals)), "unit": None,
         "label": i18n._("Sorties"), "note": sortie_note(totals)},
        {"key": "kills", "value": count(totals.get("kills")), "unit": None,
         "label": i18n._("Kills"),
         "note": sentence(i18n._("{count} in PvP"), count(totals.get("pvp")))},
        {"key": "kd", "value": ratio(totals.get("kills"), totals.get("deaths")), "unit": None,
         "label": i18n._("K/D"),
         "note": sentence(i18n._("{count} deaths"), count(totals.get("deaths")))},
    ]


def _shown_name(who: Any) -> str:
    """The identity's display name, or ``""`` — never its subject (a Discord id is not a name)."""
    return str(getattr(who, "shown_name", "") or "")


def _ucids(rows: list[dict], resolved: Any) -> list[dict]:
    """The player IDs this Discord link owns here, each with that UCID's public profile URL.

    The page uses the first entry only: its name is the record's name and its URL is the "open my
    public profile" link. A second UCID still counts into the figures; it is not listed.
    """
    return [{"name": row.get("name") or str(row.get("ucid") or ""),
             "href": player_url(resolved, row.get("ucid"))}
            for row in (rows or []) if row.get("ucid")]


def campaigns(rows: list[dict]) -> list[dict]:
    """The campaigns the viewer holds credits or a badge in, newest first, ready for the table.

    A campaign is a server's own structure, so this list is the site's campaigns the viewer took
    part in — never the whole table. ``badge_url`` is the plugin's configured image address and may
    be empty: a badge with no image renders its name alone.
    """
    return [{"name": row.get("name") or "",
             "points": count(row.get("points")),
             "badge": row.get("badge") or "",
             "badge_url": row.get("badge_url") or "",
             "running": bool(row.get("running"))}
            for row in (rows or []) if row.get("name")]


#: The periods the progress panel offers, in the bot's own vocabulary (``PeriodFilter``) paired with
#: the words the console's period chips already use for them — the same msgids, so one period is
#: named the same way on both surfaces. ``month`` is the last calendar month, not 30 fixed days.
PROGRESS_PERIODS: tuple[tuple[str, str], ...] = (
    ("month", i18n._("30 days")),
    ("quarter", i18n._("90 days")),
    ("halfyear", i18n._("6 months")),
    ("year", i18n._("12 months")),
    ("all", i18n._("All time")),
)

#: The period the page opens on when the visitor names none — the console's own default.
DEFAULT_PROGRESS_PERIOD = "quarter"


def _bucket_label(start: Any, grain: str) -> str:
    """One bucket's axis label, from the page's own translated date shapes.

    A monthly bucket is the month and the year, a weekly one the day and the month. Both come from
    :data:`~.site_player.MONTH_YEAR` / :data:`~.site_player.DAY_MONTH`, so a language that declines
    its months prints them its own way.
    """
    try:
        day = date.fromisoformat(str(start))
    except (TypeError, ValueError):
        return ""
    if grain == "month":
        return MONTH_YEAR[day.month - 1].format(year=day.year)
    return DAY_MONTH[day.month - 1].format(day=day.day)


def period_switch(href: str, active: str) -> list[dict]:
    """The progress panel's period chips: plain links, so the control works with JavaScript off."""
    return [{"key": key, "label": label, "href": f"{href}?period={key}", "active": key == active}
            for key, label in PROGRESS_PERIODS]


def kd_geometry(buckets: list[dict]) -> dict:
    """The K/D line as geometry: a polyline per unbroken run of buckets that hold a rate.

    A bucket with no loss has NO rate, and the line breaks there rather than drawing across it or
    pretending the ratio was zero. Positions are percentages of the plot, so the template's CSS
    sizes it and the geometry never needs to know the pixel size.
    """
    rates: list[float | None] = []
    for bucket in (buckets or []):
        lost = int(bucket.get("lost") or 0)
        rates.append(round(float(bucket.get("kills") or 0) / lost, 2) if lost else None)
    known = [rate for rate in rates if rate is not None]
    if not known:
        return {"segments": [], "ticks": [], "empty": True}
    floor, ceiling = min(known), max(known)
    flat = ceiling == floor
    span = ceiling - floor or 1.0
    total = max(len(rates) - 1, 1)
    segments, current = [], []
    for index, rate in enumerate(rates):
        if rate is None:
            if current:
                segments.append(_segment(current))
                current = []
            continue
        # a flat series is drawn down the middle: on the floor it would read as no data at all
        height = 50.0 if flat else 100 - (rate - floor) / span * 100
        current.append(f"{round(index / total * 100, 2)},{round(height, 2)}")
    if current:
        segments.append(_segment(current))
    ticks = ([{"top": 50.0, "text": f"{ceiling:.2f}"}] if flat else
             [{"top": 0.0, "text": f"{ceiling:.2f}"}, {"top": 100.0, "text": f"{floor:.2f}"}])
    return {"segments": segments, "empty": False, "ticks": ticks}


def _segment(points: list[str]) -> dict:
    """One polyline of the ratio line; a lone bucket gets a hair-long stub so it draws as a dot."""
    if len(points) == 1:
        x, y = points[0].split(",")
        points = [points[0], f"{round(float(x) + 0.01, 2)},{y}"]
    return {"points": " ".join(points)}


def progress_view(progress: dict | None) -> dict | None:
    """The progress section's model, or ``None`` for a site whose events table does not exist.

    ``None`` is the whole point of the degrade: the section is not rendered at all, so a site
    without the ``missionstats`` plugin shows the page it showed before this panel existed.
    """
    if not progress:
        return None
    buckets = progress.get("buckets") or []
    grain = progress.get("grain") or "week"
    entries = [{"label": _bucket_label(bucket.get("start"), grain),
                "value": bucket.get("sorties") or 0, "now": False} for bucket in buckets]
    sorties = int(progress.get("sorties") or 0)
    returned = int(progress.get("returned") or 0)
    lost = int(progress.get("lost") or 0)
    kills = int(progress.get("kills") or 0)
    kd = progress.get("kd")
    return {
        "period": progress.get("period"),
        "grain": grain,
        "sorties": count(sorties),
        "empty": not sorties,
        "chart": bars_chart(entries),
        "chart_narrow": bars_chart(entries, "narrow"),
        "kd": kd_geometry(buckets),
        "readouts": [
            {"key": "sorties", "value": count(sorties), "unit": None,
             "label": i18n._("Sorties"), "note": i18n._("flown in this period")},
            {"key": "returned", "value": f"{_percent(returned, sorties)}%", "unit": None,
             "label": i18n._("Returned"),
             "note": sentence(i18n._("{count} returned"), count(returned))},
            {"key": "lost", "value": count(lost), "unit": None,
             "label": i18n._("Lost"), "note": i18n._("ended otherwise")},
            {"key": "kd", "value": f"{kd:.2f}" if kd is not None else "—", "unit": None,
             "label": i18n._("K/D"),
             "note": sentence(i18n._("{count} kills"), count(kills))},
        ],
        "split": {"sorties": count(sorties), "returned": count(returned), "lost": count(lost)},
    }


def _percent(value: Any, total: Any) -> str:
    """*value* of *total* as a whole percentage, for a sentence that carries it as a string."""
    try:
        whole = float(total or 0)
    except (TypeError, ValueError):
        whole = 0.0
    return f"{round(float(value or 0) * 100 / whole):d}" if whole else "0"


# --------------------------------------------------------------------------------- the model

def _band_edges(size: int) -> dict[str, int]:
    """The last place that still holds each band a cohort of *size* can have.

    ``top10`` is an absolute place, so a cohort smaller than :data:`site_reads.STANDING_BANDS`'
    minimum never gets one — the card must not name a target that cannot exist. The shares are
    rounded UP (``ceil``), so the pilot exactly on a boundary holds the better band.
    """
    edges: dict[str, int] = {}
    for key, share, minimum in site_reads.STANDING_BANDS:
        if size < minimum:
            continue
        if share is None:
            edges[key] = 10
        elif share == 0.25:
            edges[key] = (size + 3) // 4
        else:
            edges[key] = (size + 1) // 2
    return edges


def _band_sentence(key: str) -> str:
    """The sentence the card prints for the band a pilot holds. Literals, so the extractor sees them."""
    if key == "top10":
        return i18n._("You are in the top 10.")
    if key == "quarter":
        return i18n._("You are in the best quartile.")
    return i18n._("You are in the better half.")


def standing_view(standing: dict | None) -> dict | None:
    """The standing card's model: the pilot's place, the band they hold, and the NEXT reachable step.

    The target is always the nearest band the pilot has NOT reached — never a fixed top 3, which
    says nothing to somebody on place 1,500. A band the cohort is too small to have is not offered
    at all, so a twelve-pilot site never tells anyone to reach the top 10; a pilot who holds the
    highest band the site can give is pointed at the place above instead.
    """
    if not standing:
        return None
    size = int(standing.get("size") or 0)
    place = standing.get("place")
    floor = int(standing.get("floor") or 0)
    sorties = int(standing.get("sorties") or 0)
    if not place:
        return {
            "size": count(size),
            "place": None,
            "value": None,
            "band": None,
            "goal": None,
            "unranked": _record(i18n._("Flights needed: {count} of {floor}."),
                                count=count(max(floor - sorties, 0)), floor=count(floor)),
        }
    edges = _band_edges(size)
    held = [key for key in ("half", "quarter", "top10")
            if key in edges and place <= edges[key]]
    goal_key = next((key for key in ("half", "quarter", "top10")
                     if key in edges and place > edges[key]), None)
    if goal_key is not None:
        target = edges[goal_key]
    elif place > 1:
        target = place - 1
    else:
        target = None
    return {
        "size": count(size),
        "place": f"#{count(place)}",
        "value": f"{float(standing.get('value') or 0):.2f}",
        "band": _band_sentence(held[-1]) if held else None,
        "goal": (None if target is None
                 else _record(i18n._("Next step: place {target}."), target=f"#{count(target)}")),
        "unranked": None,
    }


def view(resolved: Any, who: Any, stats: dict, href: str = "") -> dict:
    """The page's whole signed-in model from the read's own answer — pure, so a test can pin it."""
    found = bool(stats.get("found"))
    totals = dict(stats.get("totals") or {})
    linked = _ucids(stats.get("ucids") or [], resolved)
    progress = progress_view(stats.get("progress"))
    return {
        "signed_in": True,
        "unavailable": False,
        "found": found,
        "name": (linked[0]["name"] if linked else "") or _shown_name(who),
        "last_flown": last_flown(stats.get("recent") or []),
        "readouts": readouts(totals),
        "discipline": discipline(totals),
        "rating": rating_view(stats.get("rating")),
        "progress": progress,
        "standing": standing_view(stats.get("standing")),
        # the AAR is built from the event stream, so the link exists only where that stream does
        "aar_href": site_url(resolved, ME_AAR_PATH) if progress else None,
        "periods": period_switch(href or site_url(resolved, ME_PATH),
                                 (stats.get("progress") or {}).get("period")
                                 or DEFAULT_PROGRESS_PERIOD),
        "airframes": airframes(stats.get("per_aircraft") or [], totals.get("playtime")),
        "servers": servers(stats.get("per_server") or []),
        "recent": recent(stats.get("recent") or []),
        "campaigns": campaigns(stats.get("campaigns") or []),
        "public_href": linked[0]["href"] if linked else None,
        "search_href": site_url(resolved, SEARCH_PATH),
        "leaderboard_href": site_url(resolved, NAV_PATHS["leaderboard"]),
    }


def anonymous(resolved: Any, href: str | None) -> dict:
    """The model of the signed-out page: the door and nothing else."""
    return {"signed_in": False, "sign_in_href": href}


def _unavailable(resolved: Any, who: Any) -> dict:
    """The signed-in model of a process with no database: the identity, no figure."""
    model = view(resolved, who, {})
    model.update({"unavailable": True})
    return model


# --------------------------------------------------------------------------------- the route

def _period(value: Any) -> str:
    """The period a request names, or the page's default; an unknown value never reaches a query."""
    wanted = str(value or "").strip().lower()
    return wanted if wanted in dict(PROGRESS_PERIODS) else DEFAULT_PROGRESS_PERIOD


def add_route(router: APIRouter, site: Any) -> str:
    """Serve the viewer's own statistics at ``<site>/me`` and return the registered path."""
    path = site_url(site, ME_PATH)

    async def site_me(request: Request, next: str = "", period: str = "") -> HTMLResponse:
        resolved = resolve_site(request, site)
        # ONE identity read for the whole request: the shell's chip and this page's model are the
        # same answer, so a visitor cannot be signed in for the chip and anonymous for the figures
        who = identity(request)
        if who is None:
            return render_site_page(request, site, TEMPLATE, resolved=resolved, who=who,
                                    model=anonymous(resolved,
                                                    sign_in_href(request, resolved, next)))
        db = pool()
        if db is None:
            model = _unavailable(resolved, who)
        else:
            stats = await site_reads.my_statistics(resolved, db, getattr(who, "subject", ""),
                                                   _period(period))
            model = view(resolved, who, stats, path)
        model["sign_out"] = sign_out_context(request)
        return render_site_page(request, site, TEMPLATE, resolved=resolved, who=who,
                                model=model)

    router.add_api_route(path, site_me, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-me-{site.key}")
    return path


def _bot() -> Any:
    """The bot this process serves, or ``None``: the AAR builder needs its pool, log and proxy."""
    try:
        from core.services.registry import ServiceRegistry
        from services.bot.service import BotService

        return getattr(ServiceRegistry.get(BotService), "bot", None)
    except Exception:  # noqa: BLE001 - no bus, no bot: the route answers its 503 sentence
        return None


def aar_route(router: APIRouter, site: Any) -> str:
    """Serve the signed-in viewer's After Action Report at ``<site>/me/aar`` and return its path.

    The report is the bot's own — ``plugins.missionstats.reports.AAR``, the same builder ``/aar``
    answers with in Discord — so the download and the slash command cannot disagree about what a
    sortie is. The route is ``public`` like the page that links it (``site_pages`` declares it), and
    it refuses with the sign-in redirect rather than a figure for an anonymous request: the report is
    the pilot's own, and there is nobody to build it for.

    ``?period=`` is the page's own vocabulary, resolved by :func:`_period`; anything else is the
    page's default. A missing events table, an unlinked identity or a period with no flight answers
    a plain 404 sentence, never an empty PDF.
    """
    path = site_url(site, ME_AAR_PATH)

    async def site_me_aar(request: Request, period: str = "") -> Response:
        resolved = resolve_site(request, site)
        who = identity(request)
        if who is None:
            return RedirectResponse(sign_in_href(request, resolved, ""), status_code=307)
        db, bot = pool(), _bot()
        if db is None or bot is None:
            return PlainTextResponse(i18n._("The report is not available yet."), status_code=503)
        ucids = await site_reads.my_ucids(resolved, db, getattr(who, "subject", ""))
        if not ucids:
            return PlainTextResponse(i18n._("No record on these servers."), status_code=404)
        try:
            from plugins.missionstats.reports import AAR
            from plugins.userstats.filter import MissionStatisticsFilter
        except ImportError:  # the plugin that owns the report is not installed here
            return PlainTextResponse(i18n._("The report is not available yet."), status_code=503)

        report = AAR(bot, ucids[0], _shown_name(who) or "", MissionStatisticsFilter(_period(period)))
        file = await report.render()
        if file is None:
            return PlainTextResponse(i18n._("No flight is recorded in this period."), status_code=404)
        try:
            data = file.fp.read()
        finally:
            file.fp.close()
        return Response(content=data, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{file.filename}"'})

    router.add_api_route(path, site_me_aar, methods=["GET"], response_class=Response,
                         name=f"site-me-aar-{site.key}")
    return path
