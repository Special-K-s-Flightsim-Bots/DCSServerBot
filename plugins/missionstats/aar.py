"""
After Action Report (AAR).

Renders a printable, multipage PDF debriefing for a single pilot.

The report is built in two steps:

* :meth:`AAR._collect` gathers everything from the database (and downloads the
  squadron badge) asynchronously,
* :func:`build_pdf` turns that plain data structure into a PDF. It is a pure,
  blocking function and is therefore executed in a worker thread.

Rendering deliberately avoids ``pyplot``: other reports of this bot switch
matplotlib to a global dark style (see ``core.report.elements.Graph.render``),
which would otherwise bleed into this document. Everything here uses the
object oriented API and sets its colours explicitly.
"""
from __future__ import annotations

import re
import textwrap

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from math import ceil

from matplotlib.axes import Axes
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, Rectangle

from core import get_translation
from core.report.elements import get_supported_fonts

_ = get_translation(__name__.split('.')[1])

__all__ = ["Sortie", "reconstruct_sorties", "build_pdf", "PAGE_W", "PAGE_H"]


# ---------------------------------------------------------------------------
# page geometry (inches, A4 portrait)
# ---------------------------------------------------------------------------
PAGE_W, PAGE_H = 8.27, 11.69
MARGIN = 0.60
CONTENT_W = PAGE_W - 2 * MARGIN
COVER_BAND_H = 1.85                     # header band on the cover page
BAND_H = 0.46                           # running header on every other page
FOOTER_Y = 0.48                         # baseline of the footer rule
HEADING_H = 0.52                        # vertical space taken by a section heading


class Theme:
    """Colours of the document. Everything is set explicitly, nothing inherited."""
    paper = '#ffffff'
    band = '#152029'
    ink = '#18222c'
    muted = '#66798a'
    faint = '#9aabb7'
    accent = '#4c9f44'                  # the bot's report green
    accent_dark = '#2e6b28'
    blue = '#2f6fb5'
    red = '#b5432f'
    amber = '#bd8419'
    line = '#d4dee5'
    grid = '#e9eff3'
    zebra = '#f4f8f4'
    panel = '#f2f6f9'
    tile = '#f8fbfb'
    on_band = '#ffffff'
    on_band_soft = '#9db3c2'


# ---------------------------------------------------------------------------
# sortie reconstruction
# ---------------------------------------------------------------------------
START_EVENTS = ('S_EVENT_BIRTH', 'S_EVENT_TAKEOFF')
END_EVENTS = {
    'S_EVENT_LAND': 'RTB',
    'S_EVENT_CRASH': 'Crashed',
    'S_EVENT_EJECT': 'Ejected',
    'S_EVENT_PILOT_DEAD': 'Pilot killed',
    'S_EVENT_UNIT_LOST': 'Shot down / lost',
    'S_EVENT_PLAYER_LEAVE_UNIT': 'Left aircraft',
    'S_EVENT_DISCONNECT': 'Disconnected'
}
SORTIE_EVENTS = list(START_EVENTS) + list(END_EVENTS.keys())
#: outcomes that mean the airframe did not come back
LOSS_OUTCOMES = frozenset({'Crashed', 'Ejected', 'Pilot killed', 'Shot down / lost'})
OUTCOME_INCOMPLETE = 'Incomplete'

AIR_CATEGORIES = ('Airplanes', 'Helicopters')
SEA_CATEGORIES = ('Ships',)


@dataclass
class Sortie:
    """A single flight, reconstructed from the mission event stream."""
    mission_id: int
    mission_name: str
    theatre: str
    server: str
    plane: str | None = None
    side: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    departure: str | None = None
    arrival: str | None = None
    airstart: bool = False
    outcome: str = OUTCOME_INCOMPLETE
    kills: int = 0

    @property
    def duration(self) -> float | None:
        if self.start is None or self.end is None:
            return None
        return max(0.0, (self.end - self.start).total_seconds())

    @property
    def lost(self) -> bool:
        return self.outcome in LOSS_OUTCOMES

    @property
    def recovered(self) -> bool:
        return self.outcome == 'RTB'


def reconstruct_sorties(rows: list[dict]) -> tuple[list[Sortie], int]:
    """
    Turn an ordered stream of birth/takeoff/landing/loss events into sorties.

    Returns the sorties plus the number of aircraft that were manned on the
    ground but never got airborne (those are not sorties, but they are worth
    reporting).
    """
    sorties: list[Sortie] = []
    aborted = 0
    pending: Sortie | None = None
    mission_id = None

    def new_sortie(row: dict) -> Sortie:
        return Sortie(mission_id=row['mission_id'], mission_name=row['mission_name'],
                      theatre=row['mission_theatre'], server=row['server_name'],
                      plane=row['init_type'], side=row['init_side'])

    def flush(sortie: Sortie | None) -> None:
        nonlocal aborted
        if sortie is None:
            return
        if sortie.start is not None:
            sorties.append(sortie)     # airborne, but we never saw how it ended
        else:
            aborted += 1               # manned on the ramp, never launched

    for row in rows:
        if row['mission_id'] != mission_id:
            flush(pending)
            pending = None
            mission_id = row['mission_id']

        event = row['event']
        if event == 'S_EVENT_BIRTH':
            flush(pending)
            pending = new_sortie(row)
            if row['place']:
                pending.departure = row['place']
            else:
                # no airbase => the pilot spawned in the air, the clock runs
                pending.airstart = True
                pending.start = row['time']
        elif event == 'S_EVENT_TAKEOFF':
            if pending is None:
                pending = new_sortie(row)
            if pending.plane is None:
                pending.plane = row['init_type']
            if row['place']:
                pending.departure = row['place']
            if pending.start is None:
                pending.start = row['time']
                pending.airstart = False
        else:
            if pending is None:
                continue               # e.g. a landing without a matching start
            if pending.start is None:
                aborted += 1           # never left the ground
                pending = None
                continue
            pending.end = row['time']
            pending.outcome = END_EVENTS.get(event, OUTCOME_INCOMPLETE)
            if event == 'S_EVENT_LAND':
                pending.arrival = row['place']
            sorties.append(pending)
            pending = None

    flush(pending)
    return sorties, aborted


def attribute_kills(sorties: list[Sortie], kills: list[dict]) -> None:
    """Assign every kill to the sortie it was scored on (used for kills/sortie)."""
    by_mission: dict[int, list[Sortie]] = defaultdict(list)
    for sortie in sorties:
        if sortie.start and sortie.end:
            by_mission[sortie.mission_id].append(sortie)
    for kill in kills:
        for sortie in by_mission.get(kill['mission_id'], []):
            if sortie.start <= kill['time'] <= sortie.end:
                sortie.kills += 1
                break


# ---------------------------------------------------------------------------
# small formatting helpers
# ---------------------------------------------------------------------------
def fw(inches: float) -> float:
    """inches -> figure fraction (horizontal)"""
    return inches / PAGE_W


def fh(inches: float) -> float:
    """inches -> figure fraction (vertical)"""
    return inches / PAGE_H


def duration(seconds: float | None, *, empty: str = '—') -> str:
    if seconds is None:
        return empty
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def num(value: int | float | None, *, empty: str = '—') -> str:
    if value is None:
        return empty
    return f"{value:,}".replace(',', ' ')


def pct(part: float, total: float, *, empty: str = '—') -> str:
    if not total:
        return empty
    return f"{100.0 * part / total:.1f}%"


def ratio(part: float, total: float) -> str:
    if not total:
        return f"{part:.2f}" if part else '0.00'
    return f"{part / total:.2f}"


def counted(count: int, singular: str, plural: str) -> str:
    """'1 theatre' / '4 theatres' — both forms stay translatable."""
    return f"{num(count)} {singular if count == 1 else plural}"


def ellipsize(text, width: float, fontsize: float) -> str:
    """Trim `text` so it fits into `width` inches at `fontsize` points."""
    text = '' if text is None else str(text)
    if not text:
        return ''
    char_w = 0.545 * fontsize / 72.0
    limit = max(3, int(width / char_w))
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + '…'


def short_name(text, limit: int = 22) -> str:
    text = '' if text is None else str(text)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def kill_domain(category: str | None) -> str:
    if category in AIR_CATEGORIES:
        return 'air'
    if category in SEA_CATEGORIES:
        return 'sea'
    return 'ground'


def same_side(a, b) -> bool:
    """True if both sides are known, identical and not neutral."""
    if a is None or b is None:
        return False
    a, b = str(a), str(b)
    return a == b and a not in ('', '0')


# ---------------------------------------------------------------------------
# layout engine
# ---------------------------------------------------------------------------
@dataclass
class Col:
    """A table column: label, relative width and alignment ('l', 'c' or 'r')."""
    label: str
    width: float = 1.0
    align: str = 'l'


class Document:
    """
    A minimal top-down flow layout on top of matplotlib figures.

    Everything is measured in inches. A cursor travels from the top of the page
    downwards; every block asks for the vertical space it needs and a new page
    is started as soon as it no longer fits. That way blocks can never overlap
    or fall off the page, no matter how much data there is.
    """

    def __init__(self, *, title: str, subtitle: str):
        self.title = title
        self.subtitle = subtitle
        self.fonts = self._font_stack()
        self.figures: list[Figure] = []
        self.fig: Figure | None = None
        self.section = ''
        self.y = 1.0                                # cursor, figure fraction
        self._heading = ''
        self._pending: tuple[str, str | None] | None = None

    @staticmethod
    def _font_stack() -> list[str]:
        fonts = [f"Noto Sans {x}" for x in sorted(get_supported_fonts())]
        fonts.extend(['Arial', 'DejaVu Sans', 'sans-serif'])
        return fonts

    # -- primitives -------------------------------------------------------
    @property
    def bottom(self) -> float:
        return fh(FOOTER_Y + 0.22)

    def text(self, x: float, y: float, s: str, *, size: float = 8.5, color: str = Theme.ink,
             weight: str = 'normal', ha: str = 'left', va: str = 'baseline',
             style: str = 'normal', alpha: float = 1.0):
        return self.fig.text(x, y, s, fontsize=size, color=color, fontweight=weight, ha=ha, va=va,
                             style=style, alpha=alpha, family=self.fonts)

    def rect(self, x: float, y: float, w: float, h: float, *, color: str,
             edge: str = 'none', lw: float = 0.0, zorder: int = 1):
        patch = Rectangle((x, y), w, h, transform=self.fig.transFigure, facecolor=color,
                          edgecolor=edge, linewidth=lw, zorder=zorder)
        self.fig.add_artist(patch)
        return patch

    def rule(self, y: float, *, x0: float = None, x1: float = None,
             color: str = Theme.line, lw: float = 0.7):
        x0 = fw(MARGIN) if x0 is None else x0
        x1 = (1.0 - fw(MARGIN)) if x1 is None else x1
        self.fig.add_artist(Line2D([x0, x1], [y, y], transform=self.fig.transFigure,
                                   color=color, linewidth=lw))

    def alloc(self, height: float) -> Axes:
        """Reserve `height` inches and return an axes whose data units are inches."""
        ax = self.fig.add_axes([fw(MARGIN), self.y - fh(height), fw(CONTENT_W), fh(height)])
        ax.set_xlim(0, CONTENT_W)
        ax.set_ylim(0, height)
        ax.set_axis_off()
        ax.patch.set_visible(False)
        self.y -= fh(height)
        return ax

    def space(self, height: float) -> None:
        self.y -= fh(height)

    @property
    def available(self) -> float:
        """Remaining space on the current page, in inches."""
        return (self.y - self.bottom) * PAGE_H

    def ensure(self, height: float) -> None:
        """
        Make room for `height` inches, starting a new page if needed.

        A heading is only drawn from here, together with the block that follows
        it — that is what keeps headings from being orphaned at the bottom of a
        page while their table starts on the next one.
        """
        if self.fig is None or self.available < height + (HEADING_H if self._pending else 0.0):
            self.new_page()
        self._draw_heading()

    # -- pages ------------------------------------------------------------
    def new_page(self, section: str | None = None, *, cover: bool = False) -> None:
        self.fig = Figure(figsize=(PAGE_W, PAGE_H), facecolor=Theme.paper, dpi=100)
        self.fig.patch.set_facecolor(Theme.paper)
        self.figures.append(self.fig)
        if section is not None:
            self.section = section
        if cover:
            self.y = 1.0 - fh(COVER_BAND_H)
            return
        self.rect(0, 1.0 - fh(BAND_H), 1.0, fh(BAND_H), color=Theme.band)
        self.rect(0, 1.0 - fh(BAND_H) - fh(0.035), 1.0, fh(0.035), color=Theme.accent)
        base = 1.0 - fh(BAND_H) + fh(0.155)
        self.text(fw(MARGIN), base, self.title.upper(), size=8, weight='bold',
                  color=Theme.on_band)
        self.text(1.0 - fw(MARGIN), base, self.section.upper(), size=8, ha='right',
                  color=Theme.on_band_soft)
        self.y = 1.0 - fh(BAND_H + 0.52)

    # -- blocks -----------------------------------------------------------
    def heading(self, text: str, *, sub: str | None = None) -> None:
        """
        Queue a section heading. It is drawn together with the next block, so a
        section that turns out to be empty simply disappears instead of leaving
        a heading behind.
        """
        self._pending = (text, sub)
        self._heading = text

    def _draw_heading(self) -> None:
        if not self._pending:
            return
        text, sub = self._pending
        self._pending = None
        top = self.y
        self.rect(fw(MARGIN), top - fh(0.20), fw(0.055), fh(0.185), color=Theme.accent)
        self.text(fw(MARGIN + 0.15), top - fh(0.185), text.upper(), size=11.5, weight='bold')
        if sub:
            self.text(1.0 - fw(MARGIN), top - fh(0.175), sub, size=7.5, ha='right',
                      color=Theme.muted)
        self.rule(top - fh(0.34))
        self.y = top - fh(HEADING_H)

    def continued(self) -> None:
        if not self._heading:
            return
        self.text(fw(MARGIN), self.y - fh(0.14),
                  '{} {}'.format(self._heading.upper(), _('(continued)')),
                  size=8, weight='bold', color=Theme.muted)
        self.rule(self.y - fh(0.24))
        self.y -= fh(0.42)

    def paragraph(self, text: str, *, size: float = 8.0, color: str = Theme.muted) -> None:
        chars = max(20, int(CONTENT_W / (0.5 * size / 72.0)))
        lines = textwrap.wrap(text, chars) or ['']
        height = 0.06 + len(lines) * (size * 1.55 / 72.0)
        self.ensure(height)
        top = self.y
        for i, line in enumerate(lines):
            self.text(fw(MARGIN), top - fh(0.05 + (i + 1) * (size * 1.45 / 72.0)), line,
                      size=size, color=color)
        self.y = top - fh(height)

    def bullets(self, items: list[str], *, size: float = 8.5) -> None:
        if not items:
            return
        line_h = 0.235
        height = 0.10 + len(items) * line_h
        self.ensure(height)
        ax = self.alloc(height)
        for i, item in enumerate(items):
            y = height - 0.10 - i * line_h
            ax.add_patch(Rectangle((0.045, y - 0.045), 0.055, 0.055, facecolor=Theme.accent,
                                   edgecolor='none'))
            ax.text(0.20, y - 0.018, ellipsize(item, CONTENT_W - 0.28, size), fontsize=size,
                    color=Theme.ink, va='center', ha='left', family=self.fonts)

    def info_strip(self, items: list[tuple[str, str]], *, cols: int = 3) -> None:
        if not items:
            return
        rows = ceil(len(items) / cols)
        row_h = 0.46
        height = 0.20 + rows * row_h
        self.ensure(height)
        ax = self.alloc(height)
        ax.add_patch(Rectangle((0, 0), CONTENT_W, height, facecolor=Theme.panel,
                               edgecolor=Theme.line, linewidth=0.7))
        cell_w = CONTENT_W / cols
        for i, (label, value) in enumerate(items):
            row, col = divmod(i, cols)
            x = 0.16 + col * cell_w
            y = height - 0.14 - row * row_h
            if col:
                ax.add_line(Line2D([col * cell_w, col * cell_w],
                                   [height - 0.10 - row * row_h - row_h + 0.06,
                                    height - 0.10 - row * row_h],
                                   color=Theme.line, linewidth=0.7))
            ax.text(x, y - 0.10, label.upper(), fontsize=6.5, color=Theme.muted,
                    va='center', family=self.fonts)
            ax.text(x, y - 0.30, ellipsize(value, cell_w - 0.30, 9.5), fontsize=9.5,
                    color=Theme.ink, fontweight='bold', va='center', family=self.fonts)

    def kpis(self, items: list[tuple[str, str, str | None]], *, cols: int = 4) -> None:
        if not items:
            return
        gap = 0.10
        tile_w = (CONTENT_W - gap * (cols - 1)) / cols
        tile_h = 0.84
        rows = ceil(len(items) / cols)
        height = rows * tile_h + (rows - 1) * gap
        self.ensure(height)
        ax = self.alloc(height)
        for i, (label, value, sub) in enumerate(items):
            row, col = divmod(i, cols)
            x = col * (tile_w + gap)
            y = height - (row + 1) * tile_h - row * gap
            ax.add_patch(FancyBboxPatch((x, y), tile_w, tile_h,
                                        boxstyle='round,pad=0,rounding_size=0.05',
                                        facecolor=Theme.tile, edgecolor=Theme.line,
                                        linewidth=0.7, mutation_aspect=1))
            ax.add_patch(Rectangle((x, y + 0.05), 0.045, tile_h - 0.10, facecolor=Theme.accent,
                                   edgecolor='none'))
            ax.text(x + 0.16, y + tile_h - 0.19, label.upper(), fontsize=6.3, color=Theme.muted,
                    va='center', family=self.fonts)
            ax.text(x + 0.16, y + tile_h - 0.47, ellipsize(value, tile_w - 0.26, 15),
                    fontsize=15, color=Theme.ink, fontweight='bold', va='center',
                    family=self.fonts)
            if sub:
                ax.text(x + 0.16, y + 0.15, ellipsize(sub, tile_w - 0.26, 6.3), fontsize=6.3,
                        color=Theme.faint, va='center', family=self.fonts)

    def table(self, columns: list[Col], rows: list[list], *, fontsize: float = 8.0,
              row_h: float = 0.225, note: str | None = None) -> None:
        """
        Draw a table, breaking it over as many pages as needed.

        A cell is either a plain value or a ``(value, colour)`` tuple.
        """
        if not rows:
            return
        head_h = 0.27
        total = sum(c.width for c in columns) or 1.0
        widths = [CONTENT_W * c.width / total for c in columns]
        pad = 0.07
        index = 0
        is_continuation = False

        while index < len(rows):
            self.ensure(head_h + row_h * min(4, len(rows) - index) + 0.12)
            if is_continuation:
                self.continued()
            fits = max(1, int((self.available - head_h - 0.12) / row_h))
            chunk = rows[index:index + fits]
            height = head_h + row_h * len(chunk)
            ax = self.alloc(height)

            # header
            ax.add_patch(Rectangle((0, height - head_h), CONTENT_W, head_h,
                                   facecolor=Theme.band, edgecolor='none'))
            x = 0.0
            for col, width in zip(columns, widths):
                ax.text(*self._cell_xy(x, width, height - head_h / 2, col.align, pad),
                        ellipsize(col.label, width - 2 * pad, fontsize),
                        fontsize=fontsize - 0.5, color=Theme.on_band, fontweight='bold',
                        va='center', ha=self._ha(col.align), family=self.fonts)
                x += width

            # body
            for r, row in enumerate(chunk):
                y = height - head_h - (r + 1) * row_h
                if r % 2:
                    ax.add_patch(Rectangle((0, y), CONTENT_W, row_h, facecolor=Theme.zebra,
                                           edgecolor='none'))
                x = 0.0
                for value, col, width in zip(row, columns, widths):
                    color = Theme.ink
                    if isinstance(value, tuple):
                        value, color = value
                    ax.text(*self._cell_xy(x, width, y + row_h / 2, col.align, pad),
                            ellipsize(value, width - 2 * pad, fontsize),
                            fontsize=fontsize, color=color, va='center',
                            ha=self._ha(col.align), family=self.fonts)
                    x += width
            ax.add_line(Line2D([0, CONTENT_W], [0, 0], color=Theme.line, linewidth=0.7))

            index += len(chunk)
            self.space(0.10)
            is_continuation = index < len(rows)
            if is_continuation:
                self.new_page()

        if note:
            self.paragraph(note, size=7, color=Theme.faint)

    @staticmethod
    def _ha(align: str) -> str:
        return {'l': 'left', 'r': 'right', 'c': 'center'}[align]

    @staticmethod
    def _cell_xy(x: float, width: float, y: float, align: str, pad: float) -> tuple[float, float]:
        if align == 'r':
            return x + width - pad, y
        if align == 'c':
            return x + width / 2, y
        return x + pad, y

    def chart(self, height: float, *, pad_left: float = 0.0, pad_right: float = 0.12,
              pad_bottom: float = 0.38, pad_top: float = 0.10) -> Axes:
        """Reserve `height` inches and return a styled axes to plot into."""
        self.ensure(height)
        top = self.y
        ax = self.fig.add_axes([
            fw(MARGIN + pad_left),
            top - fh(height - pad_bottom),
            fw(CONTENT_W - pad_left - pad_right),
            fh(height - pad_bottom - pad_top)
        ])
        self.y = top - fh(height)
        ax.set_facecolor(Theme.paper)
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        for side in ('left', 'bottom'):
            ax.spines[side].set_color(Theme.line)
            ax.spines[side].set_linewidth(0.7)
        ax.tick_params(colors=Theme.muted, labelsize=7, length=2.0, width=0.6)
        return ax

    def finish_axes(self, ax: Axes, *, xgrid: bool = False, ygrid: bool = False) -> None:
        """Apply grid + fonts after the data has been plotted."""
        if xgrid or ygrid:
            ax.set_axisbelow(True)
            ax.grid(axis='x' if xgrid else 'y', color=Theme.grid, linewidth=0.7)
        for label in list(ax.get_xticklabels()) + list(ax.get_yticklabels()):
            label.set_family(self.fonts)
            label.set_color(Theme.muted)
        for axis in (ax.xaxis, ax.yaxis):
            axis.label.set_family(self.fonts)
            axis.label.set_color(Theme.muted)
            axis.label.set_fontsize(7.5)

    def note(self, text: str) -> None:
        """A short 'nothing to report' line."""
        self.paragraph(text, size=8, color=Theme.faint)

    # -- output -----------------------------------------------------------
    def save(self, metadata: dict | None = None) -> BytesIO:
        total = len(self.figures)
        for page, fig in enumerate(self.figures, start=1):
            self.fig = fig
            self.rule(fh(FOOTER_Y), color=Theme.line, lw=0.7)
            self.text(fw(MARGIN), fh(FOOTER_Y - 0.20), self.subtitle, size=6.8,
                      color=Theme.faint)
            self.text(1.0 - fw(MARGIN), fh(FOOTER_Y - 0.20),
                      _('Page {} of {}').format(page, total), size=6.8, ha='right',
                      color=Theme.faint)
        buffer = BytesIO()
        with PdfPages(buffer, metadata=metadata or {}) as pdf:
            for fig in self.figures:
                # facecolor/edgecolor are passed explicitly: another report may have
                # left a dark 'savefig.facecolor' behind in the global rcParams
                pdf.savefig(fig, facecolor=Theme.paper, edgecolor='none')
        buffer.seek(0)
        return buffer


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------
def summarize(raw: dict) -> dict:
    """Turn the collected rows into everything the pages need."""
    sorties: list[Sortie] = raw['sorties']
    ucid: str = raw['ucid']
    kills_raw: list[dict] = raw['kills']
    deaths_raw: list[dict] = raw['deaths']

    kills, teamkills, self_kills = [], [], 0
    for row in kills_raw:
        if row['target_id'] == ucid:
            self_kills += 1
        elif same_side(row['init_side'], row['target_side']):
            teamkills.append(row)
        else:
            kills.append(row)

    deaths = [row for row in deaths_raw if row['init_id'] != ucid]
    friendly_deaths = [row for row in deaths if same_side(row['init_side'], row['target_side'])]

    flown = [s for s in sorties if s.duration is not None]
    flight_time = sum(s.duration for s in flown)
    lost = [s for s in sorties if s.lost]
    recovered = [s for s in sorties if s.recovered]

    domains = Counter(kill_domain(row['target_cat']) for row in kills)

    # -- per airframe ----------------------------------------------------
    planes: dict[str, dict] = defaultdict(lambda: {
        'sorties': 0, 'time': 0.0, 'longest': 0.0, 'timed': 0, 'rtb': 0, 'lost': 0, 'kills': 0
    })
    for sortie in sorties:
        entry = planes[sortie.plane or _('Unknown')]
        entry['sorties'] += 1
        if sortie.duration is not None:
            entry['time'] += sortie.duration
            entry['timed'] += 1
            entry['longest'] = max(entry['longest'], sortie.duration)
        entry['rtb'] += int(sortie.recovered)
        entry['lost'] += int(sortie.lost)
    for row in kills:
        planes[row['aircraft'] or _('Unknown')]['kills'] += 1

    # -- per theatre / mission ------------------------------------------
    theatres: dict[str, dict] = defaultdict(
        lambda: {'missions': set(), 'sorties': 0, 'time': 0.0, 'kills': 0, 'lost': 0})
    missions: dict[int, dict] = {}
    for sortie in sorties:
        theatre = theatres[sortie.theatre or _('Unknown')]
        theatre['missions'].add(sortie.mission_id)
        theatre['sorties'] += 1
        theatre['time'] += sortie.duration or 0.0
        theatre['lost'] += int(sortie.lost)
        mission = missions.setdefault(sortie.mission_id, {
            'name': sortie.mission_name, 'theatre': sortie.theatre, 'server': sortie.server,
            'start': sortie.start, 'sorties': 0, 'time': 0.0, 'kills': 0, 'lost': 0
        })
        mission['sorties'] += 1
        mission['time'] += sortie.duration or 0.0
        mission['lost'] += int(sortie.lost)
        if sortie.start and (mission['start'] is None or sortie.start < mission['start']):
            mission['start'] = sortie.start
    for row in kills:
        theatres[row['mission_theatre'] or _('Unknown')]['kills'] += 1
        if row['mission_id'] in missions:
            missions[row['mission_id']]['kills'] += 1

    # -- adversaries (PvP) -----------------------------------------------
    pvp: dict[str, dict] = defaultdict(lambda: {'name': '', 'killed': 0, 'died': 0})
    for row in kills:
        if row['target_id'] and row['target_id'] != ucid:
            entry = pvp[row['target_id']]
            entry['name'] = row['opponent'] or row['target_id'][:8]
            entry['killed'] += 1
    for row in deaths:
        if row['init_id']:
            entry = pvp[row['init_id']]
            entry['name'] = entry['name'] or row['opponent'] or row['init_id'][:8]
            entry['died'] += 1

    weapons = [dict(row) for row in raw['weapons']]
    shots = sum(w['shots'] for w in weapons)
    hits = sum(w['hits'] for w in weapons)

    return {
        **raw,
        'kills': kills,
        'teamkills': teamkills,
        'self_kills': self_kills,
        'deaths': deaths,
        'friendly_deaths': friendly_deaths,
        'flown': flown,
        'flight_time': flight_time,
        'lost': lost,
        'recovered': recovered,
        'domains': domains,
        'planes': planes,
        'theatres': theatres,
        'missions': missions,
        'pvp': pvp,
        'weapons': weapons,
        'shots': shots,
        'hits': hits,
        'outcomes': Counter(s.outcome for s in sorties),
        'kill_types': Counter(row['target_type'] or _('Unknown') for row in kills),
        'kill_cats': Counter(row['target_cat'] or _('Unknown') for row in kills),
        'threat_types': Counter(row['threat'] or _('Unknown') for row in deaths),
        'airfields': Counter(
            place for s in sorties for place in (s.departure, s.arrival) if place
        ),
    }


def activity_buckets(sorties: list[Sortie]) -> tuple[list[str], list[int], list[float], str]:
    """Bucket sorties over time, choosing a granularity that stays readable."""
    dated = [s for s in sorties if s.start]
    if not dated:
        return [], [], [], ''
    first = min(s.start for s in dated)
    last = max(s.start for s in dated)

    def by_day(d): return d.date()
    def by_week(d): return (d - timedelta(days=d.weekday())).date()
    def by_month(d): return d.date().replace(day=1)

    span = (last - first).days
    if span <= 60:
        key, step, fmt, unit = by_day, timedelta(days=1), '%d %b', _('day')
    elif span <= 540:
        key, step, fmt, unit = by_week, timedelta(days=7), '%d %b', _('week')
    else:
        key, step, fmt, unit = by_month, None, '%b %y', _('month')

    counts: Counter = Counter()
    hours: dict = defaultdict(float)
    for sortie in dated:
        bucket = key(sortie.start)
        counts[bucket] += 1
        hours[bucket] += (sortie.duration or 0.0) / 3600.0

    # fill the gaps so the timeline stays continuous
    keys, current, stop = [], key(first), key(last)
    while current <= stop and len(keys) < 200:
        keys.append(current)
        if step is not None:
            current = current + step
        else:
            current = (current.replace(day=28) + timedelta(days=8)).replace(day=1)

    labels = [k.strftime(fmt) for k in keys]
    return labels, [counts.get(k, 0) for k in keys], [hours.get(k, 0.0) for k in keys], unit


def thin_labels(ax: Axes, labels: list[str], maximum: int = 14) -> None:
    positions = range(len(labels))
    stride = max(1, ceil(len(labels) / maximum))
    ax.set_xticks([p for p in positions if p % stride == 0])
    ax.set_xticklabels([labels[p] for p in positions if p % stride == 0],
                       rotation=45, ha='right')


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
def page_summary(doc: Document, d: dict) -> None:
    doc.new_page(_('Summary'), cover=True)
    fig = doc.fig

    # -- header band ------------------------------------------------------
    doc.rect(0, 1.0 - fh(COVER_BAND_H), 1.0, fh(COVER_BAND_H), color=Theme.band)
    doc.rect(0, 1.0 - fh(COVER_BAND_H), 1.0, fh(0.045), color=Theme.accent)
    top = 1.0 - fh(0.52)
    doc.text(fw(MARGIN), top, ' '.join(_('After Action Report').upper()), size=8.5,
             weight='bold', color=Theme.accent)
    doc.text(fw(MARGIN), top - fh(0.44), ellipsize(d['pilot'], CONTENT_W - 1.35, 25),
             size=25, weight='bold', color=Theme.on_band)
    line = d['squadron'] or _('No squadron')
    doc.text(fw(MARGIN), top - fh(0.72), line, size=9.5, color=Theme.on_band_soft)
    doc.text(fw(MARGIN), top - fh(1.00), '{}  ·  {}'.format(
        d['period'], _('generated {}').format(d['generated'].strftime('%Y-%m-%d %H:%M'))),
        size=7.5, color=Theme.on_band_soft)

    if d.get('logo'):
        _draw_logo(fig, d['logo'])

    doc.space(0.30)

    # -- reporting window -------------------------------------------------
    sorties = d['sorties']
    dated = [s.start for s in sorties if s.start]
    first = min(dated).strftime('%Y-%m-%d %H:%M') if dated else '—'
    last = max(dated).strftime('%Y-%m-%d %H:%M') if dated else '—'
    servers = sorted({s.server for s in sorties if s.server})
    doc.info_strip([
        (_('Reporting period'), d['period'].strip() or _('Overall')),
        (_('First sortie'), first),
        (_('Last sortie'), last),
        (_('Missions'), num(len(d['missions']))),
        (_('Theatres'), ', '.join(sorted(d['theatres'])) or '—'),
        (_('Servers'), ', '.join(servers) or '—'),
    ])
    doc.space(0.28)

    # -- key figures ------------------------------------------------------
    doc.heading(_('Mission Summary'))
    flown, lost, kills = d['flown'], d['lost'], d['kills']
    longest = max((s.duration for s in flown), default=None)
    avg = (d['flight_time'] / len(flown)) if flown else None
    loss_breakdown = ', '.join(
        f"{count}× {_(name).lower()}" for name, count in
        Counter(s.outcome for s in lost).most_common(2)
    )
    doc.kpis([
        (_('Sorties'), num(len(sorties)),
         _('{} never launched').format(d['aborted']) if d['aborted'] else _('all launched')),
        (_('Flight time'), duration(d['flight_time']),
         counted(len(flown), _('timed sortie'), _('timed sorties'))),
        (_('Avg. sortie'), duration(avg), _('longest {}').format(duration(longest))),
        (_('Missions flown'), num(len(d['missions'])),
         counted(len(d['theatres']), _('theatre'), _('theatres'))),

        (_('Total kills'), num(len(kills)),
         _('{} against players').format(sum(1 for k in kills if k['target_id']))),
        (_('Air-to-air'), num(d['domains'].get('air', 0)), _('planes & helicopters')),
        (_('Air-to-ground'), num(d['domains'].get('ground', 0)), _('units & structures')),
        (_('Naval'), num(d['domains'].get('sea', 0)), _('ships')),

        (_('Aircraft lost'), num(len(lost)), loss_breakdown or _('none')),
        (_('Kill / loss'), ratio(len(kills), len(lost)), _('kills per aircraft lost')),
        (_('Return rate'), pct(len(d['recovered']), len(sorties)),
         counted(len(d['recovered']), _('safe landing'), _('safe landings'))),
        (_('Kills / sortie'), ratio(len(kills), len(sorties)), _('average per launch')),

        (_('Shots fired'), num(d['shots']),
         counted(len(d['weapons']), _('weapon type'), _('weapon types'))),
        (_('Hit rate'), pct(d['hits'], d['shots']), counted(d['hits'], _('hit'), _('hits'))),
        (_('Friendly fire'), num(len(d['teamkills'])),
         _('{} own losses').format(d['self_kills']) if d['self_kills'] else _('no own goals')),
        (_('Refuellings'), num(d['refuelings']), _('completed A/A refuellings')),
    ])
    doc.space(0.26)

    # -- highlights -------------------------------------------------------
    highlights = _highlights(d)
    if highlights:
        doc.heading(_('Operational Highlights'))
        doc.bullets(highlights)


def _highlights(d: dict) -> list[str]:
    out: list[str] = []
    planes = d['planes']
    if planes:
        name, entry = max(planes.items(), key=lambda kv: kv[1]['time'])
        out.append(_('Most flown airframe: {} — {}, {} airborne').format(
            name, counted(entry['sorties'], _('sortie'), _('sorties')),
            duration(entry['time'])))
        best = max(planes.items(), key=lambda kv: kv[1]['kills'])
        if best[1]['kills']:
            out.append(_('Most successful airframe: {} — {} kills').format(best[0],
                                                                          best[1]['kills']))
    effective = [w for w in d['weapons'] if w['kills'] and w['shots']]
    if effective:
        weapon = max(effective, key=lambda w: (w['kills'] / w['shots'], w['kills']))
        out.append(_('Most effective weapon: {} — {} kills from {} shots ({})').format(
            weapon['weapon'], weapon['kills'], weapon['shots'],
            pct(weapon['kills'], weapon['shots'])))
    best_sortie = max((s for s in d['sorties'] if s.kills), key=lambda s: s.kills, default=None)
    if best_sortie:
        out.append(_('Best single sortie: {} kills in the {} on {}').format(
            best_sortie.kills, best_sortie.plane,
            best_sortie.start.strftime('%Y-%m-%d') if best_sortie.start else '—'))
    if d['theatres']:
        name, entry = max(d['theatres'].items(), key=lambda kv: kv[1]['sorties'])
        out.append(_('Primary area of operations: {} — {} over {}').format(
            name, counted(entry['sorties'], _('sortie'), _('sorties')),
            counted(len(entry['missions']), _('mission'), _('missions'))))
    nemesis = max((v for v in d['pvp'].values() if v['died']), key=lambda v: v['died'],
                  default=None)
    if nemesis:
        out.append(_('Nemesis: {} — shot you down {}×, you got them {}×').format(
            nemesis['name'], nemesis['died'], nemesis['killed']))
    if d['airfields']:
        name, count = d['airfields'].most_common(1)[0]
        out.append(_('Home plate: {} — used {}×').format(name, count))
    return out


def _draw_logo(fig: Figure, logo: bytes) -> None:
    try:
        from PIL import Image as PILImage

        image = PILImage.open(BytesIO(logo)).convert('RGBA')
        box = 1.05
        scale = min(box / image.width, box / image.height)
        width, height = image.width * scale, image.height * scale
        ax = fig.add_axes([
            1.0 - fw(MARGIN + width),
            1.0 - fh(COVER_BAND_H / 2 + height / 2),
            fw(width), fh(height)
        ])
        ax.imshow(image)
        ax.set_axis_off()
        ax.patch.set_visible(False)
    except Exception:
        pass        # a missing badge must never break the report


def page_sorties(doc: Document, d: dict) -> None:
    doc.new_page(_('Sortie Analysis'))
    doc.heading(_('Airframe Performance'),
                sub=_('{} sorties · {} airborne').format(len(d['sorties']),
                                                         duration(d['flight_time'])))
    rows = []
    for name, entry in sorted(d['planes'].items(), key=lambda kv: -kv[1]['time']):
        avg = entry['time'] / entry['timed'] if entry['timed'] else None
        rows.append([
            name, num(entry['sorties']), duration(entry['time']), duration(avg),
            duration(entry['longest'] or None), num(entry['rtb']), num(entry['lost']),
            num(entry['kills']), ratio(entry['kills'], entry['lost'])
        ])
    doc.table([
        Col(_('Aircraft'), 2.4), Col(_('Sorties'), 0.85, 'r'), Col(_('Total time'), 1.15, 'r'),
        Col(_('Avg.'), 1.0, 'r'), Col(_('Longest'), 1.0, 'r'), Col(_('RTB'), 0.7, 'r'),
        Col(_('Lost'), 0.7, 'r'), Col(_('Kills'), 0.75, 'r'), Col(_('K/L'), 0.75, 'r')
    ], rows)
    doc.space(0.22)

    # -- flight hours by airframe ----------------------------------------
    top = sorted(d['planes'].items(), key=lambda kv: kv[1]['time'], reverse=True)[:12]
    top = [(name, entry) for name, entry in top if entry['time'] > 0]
    if top:
        doc.heading(_('Flight Hours by Airframe'))
        height = max(1.5, 0.60 + len(top) * 0.24)
        ax = doc.chart(height, pad_left=1.45, pad_bottom=0.42)
        names = [short_name(name, 18) for name, _e in reversed(top)]
        hours = [entry['time'] / 3600.0 for _n, entry in reversed(top)]
        bars = ax.barh(range(len(names)), hours, color=Theme.accent, height=0.62)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names)
        ax.tick_params(axis='y', length=0)
        ax.set_xlabel(_('hours'))
        ax.set_xlim(0, max(hours) * 1.16)
        for bar, value in zip(bars, hours):
            ax.text(bar.get_width() + max(hours) * 0.015, bar.get_y() + bar.get_height() / 2,
                    f"{value:.1f}", va='center', fontsize=6.8, color=Theme.muted,
                    family=doc.fonts)
        doc.finish_axes(ax, xgrid=True)
        doc.space(0.20)

    # -- outcomes ---------------------------------------------------------
    if d['outcomes']:
        doc.heading(_('Sortie Outcomes'))
        total = sum(d['outcomes'].values())
        rows = []
        for outcome, count in d['outcomes'].most_common():
            color = Theme.red if outcome in LOSS_OUTCOMES else (
                Theme.accent_dark if outcome == 'RTB' else Theme.ink)
            rows.append([(_(outcome), color), num(count), pct(count, total)])
        doc.table([Col(_('Outcome'), 3.2), Col(_('Sorties'), 1.0, 'r'),
                   Col(_('Share'), 1.0, 'r')], rows,
                  note=_('Aircraft lost = crashed, ejected, pilot killed or shot down. '
                         'Leaving the aircraft or disconnecting is not counted as a loss.'))
        doc.space(0.18)

    # -- activity over time ----------------------------------------------
    labels, counts, hours, unit = activity_buckets(d['sorties'])
    if labels and len(labels) > 1:
        doc.heading(_('Activity Over Time'), sub=_('per {}').format(unit))
        ax = doc.chart(2.5, pad_left=0.0, pad_right=0.45, pad_bottom=0.75)
        ax.bar(range(len(labels)), counts, color=Theme.accent, width=0.72,
               label=_('sorties'))
        ax.set_ylabel(_('sorties'))
        thin_labels(ax, labels)
        twin = ax.twinx()
        twin.plot(range(len(labels)), hours, color=Theme.blue, linewidth=1.3,
                  marker='o', markersize=2.4, label=_('flight hours'))
        twin.set_ylabel(_('flight hours'))
        twin.set_facecolor('none')
        for side in ('top', 'left'):
            twin.spines[side].set_visible(False)
        twin.spines['right'].set_color(Theme.line)
        twin.spines['right'].set_linewidth(0.7)
        twin.spines['bottom'].set_color(Theme.line)
        twin.tick_params(colors=Theme.muted, labelsize=7, length=2.0, width=0.6)
        doc.finish_axes(ax, ygrid=True)
        doc.finish_axes(twin)
        doc.space(0.22)

    # -- where it happened -------------------------------------------------
    if d['theatres']:
        doc.heading(_('Theatre Activity'))
        rows = []
        for name, entry in sorted(d['theatres'].items(), key=lambda kv: -kv[1]['time']):
            rows.append([name, num(len(entry['missions'])), num(entry['sorties']),
                         duration(entry['time']), num(entry['kills']), num(entry['lost'])])
        doc.table([
            Col(_('Theatre'), 2.6), Col(_('Missions'), 1.0, 'r'), Col(_('Sorties'), 1.0, 'r'),
            Col(_('Flight time'), 1.4, 'r'), Col(_('Kills'), 1.0, 'r'), Col(_('Lost'), 1.0, 'r')
        ], rows)


def page_combat(doc: Document, d: dict) -> None:
    doc.new_page(_('Combat Effectiveness'))
    kills, deaths = d['kills'], d['deaths']

    doc.heading(_('Engagement Balance'))
    doc.kpis([
        (_('Kills'), num(len(kills)), _('confirmed, hostiles only')),
        (_('Killed by others'), num(len(deaths)),
         _('{} by players').format(sum(1 for r in deaths if r['init_id']))),
        (_('Kill / death'), ratio(len(kills), len(deaths)), _('against all threats')),
        (_('Friendly fire'), num(len(d['teamkills'])),
         _('{} friendly losses taken').format(len(d['friendly_deaths']))),
    ], cols=4)
    doc.space(0.24)

    if not kills and not deaths:
        doc.note(_('No engagements were recorded in this period.'))
        return

    # -- kills by category ------------------------------------------------
    if d['kill_cats']:
        doc.heading(_('Kills by Target Category'))
        total = sum(d['kill_cats'].values())
        rows = [[name, num(count), pct(count, total)]
                for name, count in d['kill_cats'].most_common()]
        doc.table([Col(_('Category'), 3.2), Col(_('Kills'), 1.0, 'r'),
                   Col(_('Share'), 1.0, 'r')], rows)
        doc.space(0.16)

        categories = d['kill_cats'].most_common()
        height = max(1.4, 0.55 + len(categories) * 0.26)
        ax = doc.chart(height, pad_left=1.35, pad_bottom=0.40)
        names = [short_name(name, 17) for name, _c in reversed(categories)]
        values = [count for _n, count in reversed(categories)]
        bars = ax.barh(range(len(names)), values, color=Theme.blue, height=0.6)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names)
        ax.tick_params(axis='y', length=0)
        ax.set_xlabel(_('kills'))
        ax.set_xlim(0, max(values) * 1.16)
        for bar, value in zip(bars, values):
            ax.text(bar.get_width() + max(values) * 0.015,
                    bar.get_y() + bar.get_height() / 2, str(value), va='center',
                    fontsize=6.8, color=Theme.muted, family=doc.fonts)
        doc.finish_axes(ax, xgrid=True)
        doc.space(0.22)

    # -- top targets ------------------------------------------------------
    if d['kill_types']:
        by_type: dict[str, dict] = defaultdict(lambda: {'kills': 0, 'cat': ''})
        for row in kills:
            entry = by_type[row['target_type'] or _('Unknown')]
            entry['kills'] += 1
            entry['cat'] = row['target_cat'] or _('Unknown')
        ordered = sorted(by_type.items(), key=lambda kv: -kv[1]['kills'])
        doc.heading(_('Targets Destroyed'), sub=_('{} distinct types').format(len(ordered)))
        limit = 25
        note = None
        if len(ordered) > limit:
            note = _('Showing the top {} of {} target types.').format(limit, len(ordered))
        rows = [[name, entry['cat'], num(entry['kills'])] for name, entry in ordered[:limit]]
        doc.table([Col(_('Target'), 2.6), Col(_('Category'), 1.8),
                   Col(_('Kills'), 1.0, 'r')], rows, note=note)
        doc.space(0.18)

    # -- threats ----------------------------------------------------------
    if deaths:
        by_threat: dict[str, dict] = defaultdict(lambda: {'count': 0, 'cat': '', 'weapon': ''})
        weapons_per_threat: dict[str, Counter] = defaultdict(Counter)
        for row in deaths:
            name = row['threat'] or _('Unknown')
            entry = by_threat[name]
            entry['count'] += 1
            entry['cat'] = row['threat_cat'] or _('Unknown')
            weapons_per_threat[name][row['weapon'] or _('Gun')] += 1
        ordered = sorted(by_threat.items(), key=lambda kv: -kv[1]['count'])
        doc.heading(_('Threat Analysis'), sub=_('what brought you down'))
        rows = []
        for name, entry in ordered[:20]:
            weapon = weapons_per_threat[name].most_common(1)[0][0]
            rows.append([name, entry['cat'], weapon, num(entry['count'])])
        doc.table([Col(_('Threat'), 2.2), Col(_('Category'), 1.5), Col(_('Typical weapon'), 2.0),
                   Col(_('Losses'), 0.9, 'r')], rows)


def page_weapons(doc: Document, d: dict) -> None:
    weapons = [w for w in d['weapons'] if w['shots'] or w['hits'] or w['kills']]
    if not weapons:
        return
    doc.new_page(_('Weapon Employment'))
    doc.heading(_('Weapon Employment'),
                sub=_('{} shots · {} hits · {} kills').format(
                    num(d['shots']), num(d['hits']), num(sum(w['kills'] for w in weapons))))
    ordered = sorted(weapons, key=lambda w: (-w['kills'], -w['hits'], -w['shots']))
    rows = [[
        w['weapon'], num(w['shots']), num(w['hits']), pct(w['hits'], w['shots']),
        num(w['kills']), pct(w['kills'], w['shots'])
    ] for w in ordered]
    doc.table([
        Col(_('Weapon'), 2.8), Col(_('Shots'), 1.0, 'r'), Col(_('Hits'), 1.0, 'r'),
        Col(_('Hit rate'), 1.1, 'r'), Col(_('Kills'), 1.0, 'r'), Col(_('Kills/shot'), 1.2, 'r')
    ], rows, note=_('Guns are aggregated as a single weapon. Hits are counted per event, so a '
                    'single burst can register multiple hits.'))
    doc.space(0.20)

    scored = [w for w in ordered if w['shots'] >= 2][:12]
    if scored:
        doc.heading(_('Accuracy by Weapon'), sub=_('weapons with at least two shots'))
        height = max(1.5, 0.60 + len(scored) * 0.26)
        ax = doc.chart(height, pad_left=1.65, pad_bottom=0.42)
        names = [short_name(w['weapon'], 21) for w in reversed(scored)]
        values = [100.0 * w['hits'] / w['shots'] for w in reversed(scored)]
        bars = ax.barh(range(len(names)), values, color=Theme.amber, height=0.6)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names)
        ax.tick_params(axis='y', length=0)
        ax.set_xlabel(_('hit rate (%)'))
        ax.set_xlim(0, max(100.0, max(values) * 1.1))
        for bar, value in zip(bars, values):
            ax.text(bar.get_width() + 1.2, bar.get_y() + bar.get_height() / 2,
                    f"{value:.0f}%", va='center', fontsize=6.8, color=Theme.muted,
                    family=doc.fonts)
        doc.finish_axes(ax, xgrid=True)


def page_adversaries(doc: Document, d: dict) -> None:
    pvp = {k: v for k, v in d['pvp'].items() if v['killed'] or v['died']}
    log = _engagement_log(d)
    if not pvp and not log:
        return

    doc.new_page(_('Adversaries & Engagements'))

    if pvp:
        ordered = sorted(pvp.values(), key=lambda v: (-(v['killed'] + v['died']), -v['killed']))
        won = sum(v['killed'] for v in ordered)
        lost = sum(v['died'] for v in ordered)
        doc.heading(_('Player vs. Player Record'),
                    sub=_('{} won · {} lost · exchange {}').format(won, lost,
                                                                   ratio(won, lost)))
        limit = 30
        note = None
        if len(ordered) > limit:
            note = _('Showing the top {} of {} opponents.').format(limit, len(ordered))
        rows = []
        for entry in ordered[:limit]:
            balance = entry['killed'] - entry['died']
            color = Theme.accent_dark if balance > 0 else (Theme.red if balance < 0 else Theme.ink)
            rows.append([entry['name'], num(entry['killed']), num(entry['died']),
                         (f"{balance:+d}", color), ratio(entry['killed'], entry['died'])])
        doc.table([
            Col(_('Pilot'), 2.8), Col(_('You killed'), 1.2, 'r'), Col(_('They killed you'), 1.4, 'r'),
            Col(_('Balance'), 1.0, 'r'), Col(_('Exchange'), 1.1, 'r')
        ], rows, note=note)
        doc.space(0.20)

    if log:
        limit = 90
        doc.heading(_('Engagement Log'), sub=_('most recent first'))
        note = None
        if len(log) > limit:
            note = _('Showing the {} most recent of {} engagements.').format(limit, len(log))
        rows = []
        for entry in log[:limit]:
            is_kill = entry['result'] == 'KILL'
            rows.append([
                entry['time'].strftime('%Y-%m-%d %H:%M'),
                (_('KILL') if is_kill else _('LOST'), Theme.accent_dark if is_kill else Theme.red),
                entry['aircraft'] or '—',
                entry['opponent'] or '—',
                entry['weapon'] or _('Gun'),
                entry['mission'] or '—'
            ])
        doc.table([
            Col(_('Time (UTC)'), 1.5), Col(_('Result'), 0.65), Col(_('Your aircraft'), 1.35),
            Col(_('Opponent'), 1.85), Col(_('Weapon'), 1.6), Col(_('Mission'), 1.7)
        ], rows, fontsize=7.4, row_h=0.205, note=note)


def _engagement_log(d: dict) -> list[dict]:
    log = []
    for row in d['kills']:
        log.append({
            'time': row['time'], 'result': 'KILL', 'aircraft': row['aircraft'],
            'opponent': row['opponent'] or row['target_type'], 'weapon': row['weapon'],
            'mission': row['mission_name']
        })
    for row in d['deaths']:
        log.append({
            'time': row['time'], 'result': 'LOST', 'aircraft': row['aircraft'],
            'opponent': row['opponent'] or row['threat'], 'weapon': row['weapon'],
            'mission': row['mission_name']
        })
    log.sort(key=lambda e: e['time'], reverse=True)
    return log


def page_missions(doc: Document, d: dict) -> None:
    if not d['missions']:
        return
    doc.new_page(_('Missions Flown'))
    doc.heading(_('Missions Flown'), sub=_('{} missions').format(len(d['missions'])))
    ordered = sorted(d['missions'].values(),
                     key=lambda m: m['start'] or datetime.min, reverse=True)
    rows = [[
        entry['start'].strftime('%Y-%m-%d') if entry['start'] else '—',
        entry['name'] or '—', entry['theatre'] or '—', entry['server'] or '—',
        num(entry['sorties']), duration(entry['time']), num(entry['kills']), num(entry['lost'])
    ] for entry in ordered]
    doc.table([
        Col(_('Date'), 1.1), Col(_('Mission'), 2.5), Col(_('Theatre'), 1.2),
        Col(_('Server'), 1.7), Col(_('Sorties'), 0.75, 'r'), Col(_('Flight time'), 1.1, 'r'),
        Col(_('Kills'), 0.7, 'r'), Col(_('Lost'), 0.7, 'r')
    ], rows, fontsize=7.4, row_h=0.205)

    if d['airfields']:
        doc.space(0.20)
        doc.heading(_('Airfields Used'))
        total = sum(d['airfields'].values())
        rows = [[name, num(count), pct(count, total)]
                for name, count in d['airfields'].most_common(20)]
        doc.table([Col(_('Airbase / FARP / Carrier'), 3.2), Col(_('Movements'), 1.1, 'r'),
                   Col(_('Share'), 1.0, 'r')], rows,
                  note=_('A movement is a departure or a recovery.'))


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def build_pdf(raw: dict) -> BytesIO:
    """Render the collected data into a PDF. Blocking — call it in a thread."""
    d = summarize(raw)
    doc = Document(
        title=_('After Action Report'),
        subtitle='{} · {}'.format(d['pilot'], d['period'].strip() or _('Overall'))
    )
    page_summary(doc, d)
    page_sorties(doc, d)
    page_combat(doc, d)
    page_weapons(doc, d)
    page_adversaries(doc, d)
    page_missions(doc, d)
    return doc.save(metadata={
        'Title': '{} – {}'.format(_('After Action Report'), d['pilot']),
        'Author': 'DCSServerBot',
        'Subject': _('Flight and combat debriefing for {}').format(d['pilot']),
        'Creator': 'DCSServerBot'
    })


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[^\w.\-]+', '_', name, flags=re.UNICODE).strip('._')
    return cleaned or 'pilot'
