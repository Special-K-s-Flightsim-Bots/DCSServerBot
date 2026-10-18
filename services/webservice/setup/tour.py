"""The guided first-run tour: the shell (rail + card + legend), every built step, and their writes.

The tour is the setup console's OWN document, in the login page's family: standalone HTML built from
the shell's own tokens, no shell asset path, and no script that anything requires. The few tiny scripts
are progressive enhancement only — one scrolls the current rail row into view when the rail is a
horizontal scroller, one re-renders the rail a moment sooner when the Discord switch is toggled, one
appends a field to a role row's ``+ Add`` list, one follows the member-count switch with its
consequence sentence, one re-renders the roles step when the admin-channel answer changes (so the
central admin channel's field appears with the central choice) — and every screen and every
write works with JavaScript off.

THE RAIL IS DERIVED FROM THE ANSWERS, AND IT IS THE ONLY PROGRESS STATEMENT. The Discord decision
(step 2) decides whether the rail holds nine rows or eight; on the no-Discord path rows 5 and 6
("Discord — the bot", "Discord — roles and channels") are replaced by a single "Your community" row
(the agreed layout). The DCS decision (step 4) decides whether row 8 ("DCS servers") is present:
declining the DCS World installation removes it, and the rows around it stay. Row 8 STAYS when the
machine's own instance scan finds no Saved Games instance at all — the step then asks, with one blank
row to fill in, because the operator's other way to add a server is the command line (see
:mod:`services.webservice.setup.servers`). The scan runs once, from step 4's answer on, and its result
is kept in the draft (see :func:`_rescan_servers`). There is no "step n of m" text and no progress bar
beside it: the rail already states both facts. The current row carries the amber marker and
``aria-current``, a completed row is a LINK back, and a future row is inert text.

THE RAIL ORDER IS THE STEP IDS. Discord (5, 6) precedes the Web console (7) and the DCS servers (8)
so the central admin-channel answer exists before any per-server row reads it, and so a server row's
channel pickers can be fed from the guild the Discord steps already read. The number a row SHOWS is
its POSITION in the rail, never its id.

ORDER AND NAVIGATION. Linear forward; a completed row is the only way back, plus the screen's own
"Back" link. Going back NEVER loses an answer and never re-runs a side effect: every step re-renders
from the draft (``.state``) and re-saving it is a no-op write (the config-write primitive's own rule).

THE WRITES. A step's answers are written through the ONE config-write primitive
(``core.utils.configwrite``) into the tour's draft (``.state.SetupDraft``); no step before the last
touches a real configuration file. Step 9 (:mod:`services.webservice.setup.review`) is the one commit
that writes the real files — ``main.yaml`` LAST, because that file is what closes the setup window —
and removes the draft. Nothing in this module opens a config file itself.
"""

from __future__ import annotations

import logging
import os
import sys

from html import escape
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.background import BackgroundTask

from core.utils import configwrite

from .. import i18n, session
from .. import theme as theme_module
from . import boot as boot_module
from . import community as community_step
from . import console as console_step
from . import database as db_step
from . import discord as discord_step
from . import join as join_step
from . import language as language_step
from . import node as node_step
from . import review as review_step
from . import servers as servers_step
from .state import LAST_STEP, SetupDraft, draft_path

__all__ = [
    "STEP_PATH", "LANGUAGE_PATH", "THEME_PATH", "FLAGS_PATH", "FLAGS_DIR",
    "STEP_LABELS", "COMMUNITY_LABEL", "rail_steps", "current_step", "label_for", "step_label",
    "suggested_listen_port",
    "render_root", "render_step", "render_flags", "submit_step",
    "language_redirect", "theme_redirect",
]

#: The targets keyed by their stable id, for the Review screen's per-file lines.
TARGET_BY_KEY = {target.key: target for target in review_step.TARGETS}

log = logging.getLogger(__name__)

#: The tour's step screen. One route, the step named by ``n`` — keeps the route table small and the
#: path the flag control returns to a value the console can re-validate.
STEP_PATH = "/setup/step"

#: The language and theme switches, the same two routes the login page uses (re-implemented here so
#: the setup application — which never installs the shell — serves them itself).
LANGUAGE_PATH = "/language"
THEME_PATH = "/theme"

#: The query parameter both switches name their choice in (``/language?lang=de``), quoted in the
#: Language step's sentence about where its preselection came from.
LANGUAGE_PARAM = "lang"

#: The vendored flag SVGs, served by THIS application (it does not serve ``/static``).
FLAGS_PATH = "/setup/flags"

#: The flag assets' directory on disk.
FLAGS_DIR = Path(__file__).resolve().parents[1] / "static" / "flags"

#: The step titles (msgids; English is the source and the way back from any translation).
#: THE IDS ARE THE RAIL ORDER (see :func:`rail_steps`): 5 Discord (the bot), 6 Discord (roles and
#: channels), 7 Web console, 8 DCS servers, 9 Review. The number a rail row SHOWS is its POSITION,
#: not its id, so a dropped row (the servers row on a machine with no DCS installation) renumbers it.
STEP_LABELS: dict[int, str] = {
    1: i18n._("Language"),
    2: i18n._("General"),
    3: i18n._("Database"),
    4: i18n._("This node"),
    5: i18n._("Discord — the bot"),
    6: i18n._("Discord — roles and channels"),
    7: i18n._("Web console"),
    8: i18n._("DCS servers"),
    9: i18n._("Review and write"),
}

#: The row that REPLACES steps 5 and 6 when Discord support is off (step id 5, same slot).
COMMUNITY_LABEL = i18n._("Your community")


def label_for(step: int, discord: bool) -> str:
    """The rail label of *step* given the Discord decision (step 5 is the two-paths row)."""
    if step == 5 and not discord:
        return COMMUNITY_LABEL
    return STEP_LABELS.get(step, "")


#: Screen titles that differ from the short rail label (the row says "Language",
#: the screen asks the question).
SCREEN_TITLES: dict[int, str] = {
    1: i18n._("Which language do you speak?"),
    7: i18n._("Do you want the web admin console?"),
}


def step_label(step: int, discord: bool) -> str:
    """*step*'s screen title (the caller translates and escapes it)."""
    return SCREEN_TITLES.get(step) or label_for(step, discord)


# ------------------------------------------------------------------------------ the step sequence

def has_dcs(draft: SetupDraft) -> bool:
    """Whether the rail keeps the "DCS servers" row: the DCS installation was not declined.

    Before step 4 has been answered the draft carries no node block, and the row is present — the
    default the installer offers.
    """
    return bool(node_step.answers_from_mapping(draft.node).has_dcs)


def has_servers(draft: SetupDraft) -> bool:
    """Whether the rail keeps the "DCS servers" row: a DCS installation was configured.

    The machine's own instance scan does NOT decide it: a step that asks nothing because the scan was
    empty would leave the operator with the command line as the only way to add a server. The empty
    screen asks instead, with one blank row to fill in (see :mod:`services.webservice.setup.servers`),
    so declining the DCS installation is the only thing that removes the row.
    """
    return has_dcs(draft)


def rail_rows(draft: SetupDraft, mode: str = boot_module.INSTALL) -> list[int]:
    """The step ids in rail order, for THIS draft's answers and this console's mode."""
    return rail_steps(draft.discord, has_dcs(draft), has_servers(draft), mode)


def rail_steps(discord: bool, dcs: bool = True, servers: bool = True,
               mode: str = boot_module.INSTALL) -> list[int]:
    """The step ids in rail order.

    Discord comes BEFORE the servers so the central admin-channel question (step 6) is answered
    before any per-server row reads it, and so the pickers a server row offers are already fetched.
    Nine rows with Discord, eight without (5+6 -> "Your community"); row 8 ("DCS servers") is
    present only when a DCS installation was configured.

    JOINING SERVES A SUBSET OF THE SAME IDS, IN THE SAME ORDER: database (3), this node (4), DCS
    servers (8, under the same DCS rule) and review (9). The first-run steps are not asked
    again — this installation already has its language, its guild, its roles, its console and its
    database — so they are simply not on the rail. Every id here is a subsequence of the first-run
    rail's, which is why the "furthest completed step" arithmetic needs no change: a draft whose
    ``step`` is 0 starts at 3, and the rows it can never reach are not in the list.
    """
    if mode == boot_module.JOIN:
        rows = [3, 4]
        if dcs and servers:
            rows.append(8)
        return rows + [9]
    rows = [1, 2, 3, 4]
    rows += [5, 6] if discord else [5]
    rows.append(7)
    if dcs and servers:
        rows.append(8)
    rows.append(9)
    return rows


def current_step(draft: SetupDraft, mode: str = boot_module.INSTALL) -> int | None:
    """The first step the operator has not completed yet, or ``None`` when the tour is finished."""
    for step in rail_rows(draft, mode):
        if step > draft.step:
            return step
    return None


def _previous_step(draft: SetupDraft, step: int, mode: str = boot_module.INSTALL) -> int | None:
    """The rail row before *step*, or ``None`` when it is the first."""
    rows = rail_rows(draft, mode)
    if step not in rows:
        return None
    index = rows.index(step)
    return rows[index - 1] if index > 0 else None


# ------------------------------------------------------------------------------------- the styles

#: The tour's stylesheet, inline: the setup application serves no asset path, so the page is one
#: response with nothing to trace. The tokens are the shell's own (``static/shell.css``) so the tour
#: reads as the same product; the breakpoints are the agreed layout's (>=1200 side by side, 768 a
#: horizontal scroller, <=480 one collapsed line).
_STYLE = """<style>
:root{color-scheme:dark;
--ground:#0b0f14;--ground-2:#111720;--panel:#151c26;--panel-2:#1b2430;
--line:#243040;--line-2:#2e3c4f;--line-hover:#3d4d63;
--text:#e6edf5;--text-2:#9fb0c3;--text-3:#7f8fa2;
--accent:#e8a33d;--accent-2:#c8862a;--run:#3fb950;--danger:#f85149;
--nav-h:58px;--side-w:248px;--radius:10px;--radius-s:6px;
--font:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--font-mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;}
html[data-theme="light"]{color-scheme:light;
--ground:#eef1f5;--ground-2:#e6eaf0;--panel:#ffffff;--panel-2:#f5f7fa;
--line:#d3dae3;--line-2:#c2ccd8;--line-hover:#9fb0c3;
--text:#182230;--text-2:#44536a;--text-3:#67758a;
--accent:#a86a12;--accent-2:#8a5610;--run:#1a7f37;--danger:#b3261e;}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--ground);color:var(--text);font:14px/1.55 var(--font);
-webkit-text-size-adjust:100%}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.topbar{height:var(--nav-h);display:flex;align-items:center;gap:14px;padding:0 18px;
border-bottom:1px solid var(--line);background:var(--panel)}
.brand b{font-size:15px;letter-spacing:.02em}
.brand small{display:block;color:var(--text-3);font-size:11px;margin-top:1px}
.spacer{flex:1 1 auto}
.lang,.theme{display:flex;align-items:center;gap:4px}
.lang-opt,.theme-opt{display:inline-flex;align-items:center;justify-content:center;width:30px;
height:24px;border-radius:var(--radius-s);border:1px solid transparent;color:var(--text-3)}
.lang-opt:hover,.theme-opt:hover{border-color:var(--line-2)}
.lang-opt.on,.theme-opt.on{border-color:var(--line-2);background:var(--panel-2);
box-shadow:0 0 0 1px var(--accent)}
.lang-opt img{width:20px;height:15px;display:block;border-radius:2px}
.theme-opt svg{width:16px;height:16px;display:block}
.tour{display:grid;grid-template-columns:var(--side-w) 1fr;align-items:start;
min-height:calc(100vh - var(--nav-h))}
.rail{margin:0;padding:14px 10px;border-right:1px solid var(--line)}
.rail>summary{display:none}
.rail ul{display:block;list-style:none;margin:0;padding:0}
.rail .row{display:flex;align-items:baseline;gap:10px;padding:9px 12px;
border-radius:var(--radius-s);color:var(--text-3);text-decoration:none}
.rail .row .n{font-family:var(--font-mono);font-size:12px;min-width:2.1em}
.rail a.row{color:var(--text-2)}
.rail a.row:hover{background:var(--panel);color:var(--text);text-decoration:none}
.rail .row.on{color:var(--text);background:var(--panel);box-shadow:inset 2px 0 0 var(--accent);
font-weight:600}
.rail .row.done .n::before{content:"\\2713 "}
.panel{padding:22px 26px;max-width:900px}
.legend{color:var(--text-3);font-size:13px;margin:0 0 12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
overflow:hidden}
.card .hd{padding:13px 16px;border-bottom:1px solid var(--line);
background:linear-gradient(180deg,var(--panel-2),var(--panel))}
.card .hd h1{font-size:16px;margin:0;color:var(--text)}
.card .bd{padding:16px}
.flags{display:flex;align-items:center;gap:6px;margin:0 0 4px}
label.switch{display:flex;gap:10px;align-items:flex-start;margin:14px 0;cursor:pointer}
label.switch input{margin-top:3px}
label.switch .help{display:block;color:var(--text-3);font-size:12.5px;margin-top:2px}
.prereq{color:var(--text-2);font-size:13px;border-left:2px solid var(--line-2);
padding:6px 0 6px 12px;margin:16px 0}
.actions{display:flex;align-items:center;gap:16px;margin-top:20px}
.actions .grow{flex:1 1 auto}
button{font:inherit;font-weight:600;color:#1a1206;background:var(--accent);border:0;
border-radius:var(--radius-s);padding:9px 16px;cursor:pointer}
button:hover{background:var(--accent-2)}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
a.button{display:inline-block;font:inherit;font-weight:600;color:#1a1206;background:var(--accent);
border-radius:var(--radius-s);padding:9px 16px;text-decoration:none}
a.button:hover{background:var(--accent-2);text-decoration:none}
a.button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
/* A disabled submit: the accent fill is gone for the muted panel inside the line-2 ring, the text is
   dimmed, the cursor says not-allowed, and neither a hover nor a press brings the accent back. */
button:disabled{background:var(--panel);color:var(--text-3);box-shadow:0 0 0 1px var(--line-2);
cursor:not-allowed;opacity:.6}
button:disabled:hover,button:disabled:active{background:var(--panel);color:var(--text-3)}
/* A LINK the operator must be able to SPOT: every textual anchor the tour renders (the back links,
   the help links that carry a documentation URL, the generated invite link) reads as the accent
   family — never the muted note grey it used to share — underlined, semibold, and at body size, so
   a link sitting inside the quiet 12px note/help copy is still legible and clearly a control rather
   than part of the sentence. The icon toggles (``.lang-opt``/``.theme-opt``) and the rail rows carry
   their own colour and are untouched. */
.link{color:var(--accent);font-weight:600;text-decoration:underline;text-underline-offset:2px;
font-size:14px}
.link:hover{color:var(--accent-2);text-decoration:underline}
.note{color:var(--text-3);margin:0}
/* The DCS-servers channel guide (step 8): the intro as short paragraphs, the channel roles and the
   permissions as one `<li>` per line. The list keeps the note's muted colour. */
.guide p{margin:0 0 6px}
.guide ul{margin:2px 0 10px;padding-left:20px}
.guide ul:last-child{margin-bottom:0}
.guide li{margin:1px 0}
.source{color:var(--text-3);font-size:13px;margin:0 0 12px}
fieldset.choice{border:0;margin:0 0 14px;padding:0}
.choice-legend{color:var(--text-3);font-size:12.5px;padding:0 0 6px}
/* The admin-channel question: a role row's own shape — a bold lead with its sentence beneath it —
   and the spacing that keeps it from running into the role-mapping note above it. */
.choice.admin-choice{margin-top:14px}
.choice-legend b{display:block;font-weight:600;color:var(--text);font-size:13px}
/* The joining question's two choices: two DISTINCT blocks of equal weight, each headed by a
   headline at the SAME size, weight and colour as the other (``.join-head`` on both), separated by
   their own borders and the grid's gap rather than by spacing alone. */
.join-choices{display:grid;gap:14px;margin:2px 0 0}
.join-choice{border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:var(--radius);background:var(--panel-2);padding:14px 16px}
.join-head{font-size:17px;font-weight:600;color:var(--text);margin:0 0 4px}
.join-help{color:var(--text-3);font-size:13px;margin:0 0 12px}
label.opt{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;cursor:pointer;
border:1px solid var(--line);border-radius:var(--radius-s);margin-bottom:6px}
label.opt:hover{border-color:var(--line-2)}
label.opt input{margin-top:3px}
label.opt b{color:var(--text);font-weight:600}
label.opt .help{display:block;color:var(--text-3);font-size:12.5px;margin-top:2px}
.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 16px;
margin:4px 0 14px}
label.field{display:flex;flex-direction:column;gap:4px}
label.field .lbl{color:var(--text-2);font-size:12.5px}
label.field input{font:inherit;color:var(--text);background:var(--ground-2);
border:1px solid var(--line-2);border-radius:var(--radius-s);padding:7px 9px;width:100%}
label.field input[type="password"]{font-family:var(--font-mono);letter-spacing:.1em}
label.field input:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
label.field select{font:inherit;color:var(--text);background:var(--ground-2);
border:1px solid var(--line-2);border-radius:var(--radius-s);padding:7px 9px;width:100%}
label.field select:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.orlist select{flex:1 1 14em;min-width:8em;font:inherit;color:var(--text);
background:var(--ground-2);border:1px solid var(--line-2);border-radius:var(--radius-s);
padding:6px 8px}
.orlist select:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
label.field .help{color:var(--text-3);font-size:12px}
.notice{display:flex;gap:8px;align-items:baseline;padding:9px 12px;border-radius:var(--radius-s);
font-size:13px;margin:0 0 12px;border:1px solid var(--line-2)}
.notice::before{content:"\\25CF";line-height:1.2}
.notice.ok{border-color:var(--run);color:var(--run)}
.notice.bad{border-color:var(--danger);color:var(--danger)}
.notice.warn{border-color:var(--accent);color:var(--accent)}
.tag{display:inline-block;padding:3px 8px;border-radius:999px;font-size:12px;
border:1px solid var(--accent);color:var(--accent);margin:0 0 12px}
.tag.warn{border-color:var(--accent);color:var(--accent)}
/* A COLLAPSIBLE ROW — the SAME `st-block` block the config editor draws (templates/_editor.html):
   a native `<details>` whose `<summary>` is the header (`chev` + `.k` name + `.kd` line + `.r`
   controls) and whose `.bd` is the edit block. Native, so the collapse works with JavaScript OFF. */
.st-block{border:1px solid var(--line);border-radius:var(--radius-s);background:var(--ground-2);
margin:0 0 12px}
.st-block[open]>summary{border-bottom:1px solid var(--line)}
.st-block>summary{display:flex;align-items:center;gap:10px;padding:11px 13px;cursor:pointer;
list-style:none;border-radius:var(--radius-s) var(--radius-s) 0 0}
.st-block>summary::-webkit-details-marker{display:none}
.st-block>summary:hover{background:var(--panel-2)}
.st-block>summary .chev{width:14px;height:14px;color:var(--text-3);flex:0 0 auto}
.st-block[open]>summary .chev{transform:rotate(90deg)}
.st-block>summary .k{font-family:var(--font-mono);font-size:13px;color:var(--text)}
.st-block>summary .kd{font-size:12px;color:var(--text-3)}
.st-block>summary .r{margin-left:auto;display:flex;align-items:center;gap:8px}
.st-block>summary label.switch{margin:0}
.st-block> .bd{padding:10px 13px 12px}
.review{margin:0 0 14px}
.review h2{font-size:13px;color:var(--text-2);margin:0 0 6px;font-weight:600}
.review ul{list-style:none;margin:0;padding:0}
.review li{display:flex;gap:10px;align-items:baseline;padding:4px 0;font-size:13px;
color:var(--text-2)}
.review li .path{font-family:var(--font-mono);color:var(--text)}
.review li .state{color:var(--text-3);white-space:nowrap}
.review li.ok .state{color:var(--run)}
.review li.bad .state{color:var(--danger)}
.stat{display:flex;flex-direction:column;gap:4px}
.stat .lbl{color:var(--text-2);font-size:12.5px}
.stat .val{font-family:var(--font-mono);color:var(--text);font-size:13px}
.app-row{display:flex;gap:14px;align-items:flex-start;padding:10px 12px;
border-bottom:1px solid var(--line)}
.app-row:last-child{border-bottom:0}
.app-state{min-width:15em}
.app-state .glyph{display:inline-block;width:1.1em;font-weight:700}
.app-state .word{color:var(--text-2);font-size:12.5px}
.app-state .where{display:block;font-family:var(--font-mono);font-size:12.5px;color:var(--text);
word-break:break-all;margin-top:3px}
.app-row label.field{min-width:16em}
.app-switch{white-space:nowrap}
.role-row{padding:10px 12px;border:1px solid var(--line);border-radius:var(--radius-s);
margin:0 0 8px;background:var(--panel-2)}
.role-name{font-family:var(--font-mono);font-weight:600;color:var(--text);font-size:13px}
.role-meaning{color:var(--text-3);font-size:12.5px;margin:2px 0 8px}
.orlist{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.orlist input{flex:1 1 14em;min-width:8em;font:inherit;color:var(--text);
background:var(--ground-2);border:1px solid var(--line-2);border-radius:var(--radius-s);
padding:6px 8px}
.orlist input:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.orlist input:disabled{opacity:.5}
button.add{font-weight:600;color:var(--text-2);background:var(--panel);
border:1px solid var(--line-2);border-radius:var(--radius-s);padding:6px 10px}
button.add:hover{background:var(--panel-2);color:var(--text)}
/* The optional cluster database: the Federation support checkbox, then the second block it reveals,
   separated from the first block above it by a rule. */
label.switch.fed{margin:18px 0 0;padding-top:14px;border-top:1px solid var(--line)}
.cluster{margin-top:14px;padding-top:14px;border-top:1px solid var(--line-2)}
@media (max-width:640px){.fields{grid-template-columns:1fr}}
@media (max-width:1199px){
.tour{grid-template-columns:1fr}
.rail{border-right:0;border-bottom:1px solid var(--line);padding:8px 10px}
.rail ul{display:flex;gap:4px;overflow-x:auto}
.rail .row{white-space:nowrap}
.panel{max-width:none}
}
@media (max-width:480px){
.rail ul{display:none}
.rail[open] ul{display:flex;flex-direction:column}
.rail>summary{display:block;cursor:pointer;color:var(--text-2);font-size:13px;padding:4px 6px;
list-style:none}
.rail>summary::-webkit-details-marker{display:none}
.rail>summary .n{font-family:var(--font-mono);color:var(--text-3);margin-right:6px}
.panel{padding:16px}
.card .bd{padding:14px}
}
</style>"""


def _the_style() -> str:
    return _STYLE.replace("\\2713", "\u2713").replace("\\25CF", "\u25CF")


# --------------------------------------------------------------------------------- the chrome

def _flag_control(request: Request, lang: str, next_target: str) -> str:
    """The language control: one flag per selectable language, the current one marked.

    Plain GET links to :data:`LANGUAGE_PATH`, exactly like the shell's own control — no form, no
    script, so it works with JavaScript off and before any session exists.
    """
    options = []
    for code in i18n.selectable_languages():
        name = i18n.display_name(code)
        asset = i18n.LANGUAGE_FLAGS.get(code, i18n.FLAG_NEUTRAL_ASSET)
        href = f"{LANGUAGE_PATH}?lang={code}&next={quote(next_target, safe='/')}"
        active = ' aria-current="true"' if code == lang else ''
        cls = "lang-opt on" if code == lang else "lang-opt"
        options.append(
            f'<a class="{cls}" hreflang="{escape(code)}" title="{escape(name)}" '
            f'aria-label="{escape(name)}" href="{escape(href)}"{active}>'
            f'<img class="flag" src="{FLAGS_PATH}/{escape(asset)}" alt="" width="20" '
            f'height="15"></a>')
    label = i18n.translations_for(lang).gettext(T_LANGUAGE)
    return f'<nav class="lang" aria-label="{escape(label)}">{"".join(options)}</nav>'


_SUN = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 '
        '1.4M6.3 17.7l-1.4 1.4"/></svg>')
_MOON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
         'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
         '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>')


def _theme_control(request: Request, lang: str, current: str, next_target: str) -> str:
    """The theme control: the same two sun/moon links the shell's own control draws."""
    translate = i18n.translations_for(lang).gettext
    options = []
    for code in theme_module.THEMES:
        # the mode names and the control's own label are the shell's marked msgids, reused
        name = translate(theme_module.MODE_LABELS[code])
        href = f"{THEME_PATH}?theme={code}&next={quote(next_target, safe='/')}"
        active = ' aria-current="true"' if code == current else ''
        cls = "theme-opt on" if code == current else "theme-opt"
        icon = _SUN if code == "light" else _MOON
        options.append(
            f'<a class="{cls}" title="{escape(name)}" '
            f'aria-label="{escape(name)}" href="{escape(href)}"{active}>{icon}</a>')
    label = escape(translate(theme_module.CONTROL_LABEL))
    return f'<nav class="theme" aria-label="{label}">{"".join(options)}</nav>'


# ------------------------------------------------------------------------------------ the rail

def _mode(request: Request) -> str:
    """This console's state (``boot.INSTALL`` / ``boot.JOIN``), recorded on the application at start.

    Read from the application and never from the draft: the mode decides which rail is served and
    what a commit may write, so it must not be something a caller-writable file can change.
    """
    recorded = getattr(request.app.state, "webui_setup_mode", boot_module.INSTALL)
    return str(recorded) if recorded in (boot_module.INSTALL, boot_module.JOIN) \
        else boot_module.INSTALL


def _node_name(request: Request) -> str:
    """The node name this console writes and renames, recorded on the application at start."""
    return str(getattr(request.app.state, "webui_setup_node", "") or "")


def _rail(draft: SetupDraft, current: int | None, translate, mode: str = boot_module.INSTALL) -> str:
    """The rail: numbers + labels, the current row amber, completed rows links, future rows inert."""
    rows = rail_rows(draft, mode)
    items = []
    current_label = ""
    for position, step in enumerate(rows, start=1):
        label = translate(label_for(step, draft.discord))
        number = f'<span class="n">{position}</span>'
        entry = f'{number}<span class="lbl">{escape(label)}</span>'
        done = current is None or step <= draft.step
        if step == current:
            current_label = label
            items.append(f'<li><span class="row on" aria-current="step">{entry}</span></li>')
        elif done:
            items.append(f'<li><a class="row done" href="{STEP_PATH}?n={step}">{entry}</a></li>')
        else:
            items.append(f'<li><span class="row">{entry}</span></li>')
    if not current_label and rows:
        current_label = translate(label_for(rows[-1], draft.discord))
    summary = f'{escape(current_label)} — {escape(translate(T_TAP))}'
    label = escape(translate(T_RAIL_LABEL))
    return (f'<details class="rail" aria-label="{label}">'
            f'<summary class="rail-summary">{summary}</summary>'
            f'<ul>{"".join(items)}</ul></details>')


# ----------------------------------------------------------------------------------- the pages

#: The one live statement of what has been written. Steps 1 and 2 write only the
#: tour's own draft, so the opening sentence is the true one for the whole of this phase.
LEGEND = i18n._("Nothing is written yet — the last screen writes your configuration.")

#: Fixed copy, each marked so Babel extracts it AND rendered through the request's translator.
#: English is the source: an entry without a translation renders the msgid, never blank.
T_SETUP = i18n._("Setup")
T_LANGUAGE = i18n._("Language")
T_TAP = i18n._("tap to see the steps")
T_RAIL_LABEL = i18n._("Setup steps")
T_CONTINUE = i18n._("Continue")
T_BACK = i18n._("Back")
T_BOT_LANGUAGE = i18n._(
    "The bot can be set to the same language, which means, that all Discord and in-game messages "
    "will be in your language as well. Would you like me to configure the bot this way?")
T_BOT_LANGUAGE_HELP = i18n._(
    "Unticked, the bot keeps its own default language (English) — the console still speaks the "
    "language of your browser.")
#: The one line step 1 shows about WHERE its preselection came from, one msgid per source of
#: :mod:`services.webservice.setup.language` — so a silent override (a stale saved answer outranking
#: the browser) is stated instead of happening quietly. Each template carries the holes its own
#: source can fill; unused ones are ignored.
T_LANG_SOURCE = {
    language_step.SOURCE_BROWSER: i18n._('Preselected from your browser — it sent "{header}".'),
    language_step.SOURCE_PARAMETER: i18n._(
        'Preselected from the language in the link you followed — the "{param}" parameter.'),
    language_step.SOURCE_COOKIE: i18n._(
        'Preselected from the language this console stored for you — the "{cookie}" cookie.'),
    language_step.SOURCE_ANSWER: i18n._(
        "Preselected from the answer you saved to this step on an earlier run."),
    language_step.SOURCE_DEFAULT: i18n._(
        "Preselected from this install's default language — nothing else named one."),
}
T_PREREQUISITES = i18n._(
    "Before you start you need PostgreSQL 14 or higher, and a Discord bot token. On a no-Discord "
    "install the token is not needed.")
T_AUTOUPDATE = i18n._("Do you want your DCSServerBot being auto-updated?")
T_DISCORD = i18n._("Do you want to run DCSServerBot with Discord support (recommended)?")
T_DISCORD_HELP = i18n._(
    "Discord is how the bot talks to your admins and players. Without it you manage everything "
    "from this console.")
T_NOT_BUILT = i18n._("This step is not built yet.")
#: The one sentence a FAILED draft write renders on the step (see :func:`_save_failure_response`).
#: Scoped to the draft on purpose: a step's own action (step 3's Test) may have run a real side
#: effect, so only the write to the draft is what this states plainly did not happen.
T_SAVE_FAILED = i18n._(
    "Your answers could not be saved — the setup draft was not written, so this step is not "
    "recorded. Try again.")

#: The two documentation links the tour offers, each a canonical page the project's own README
#: points at: where to get the PostgreSQL server the bot needs (the Database step) and where the
#: bot application is created (the Discord step).
POSTGRESQL_DOWNLOAD_URL = "https://www.postgresql.org/download/"
DISCORD_PORTAL_URL = "https://discord.com/developers/applications"

# ---- step 3: Database -------------------------------------------------------------------------
T_DB_CREATE = i18n._("Create a new database")
T_DB_CREATE_HELP = i18n._(
    "The bot creates its own database and its own user. You enter the PostgreSQL superuser "
    "credentials below.")
T_DB_EXISTING = i18n._("Use an existing database")
T_DB_EXISTING_HELP = i18n._(
    "Point the bot at a database that already exists. You enter the credentials the bot should "
    "use, not a superuser's.")
T_DB_CHOICE = i18n._("How should the bot get its database?")
T_DB_HOST = i18n._("host")
T_DB_HOST_HELP = i18n._("Where PostgreSQL runs. The bot usually runs on the same machine.")
T_DB_PORT = i18n._("port")
T_DB_MASTER_DATABASE = i18n._("master database")
T_DB_MASTER_DATABASE_HELP = i18n._("The maintenance database of your PostgreSQL server.")
T_DB_MASTER_USER = i18n._("master user")
T_DB_MASTER_USER_HELP = i18n._(
    "The PostgreSQL superuser the bot uses once, to create its own database and user.")
T_DB_MASTER_PASSWORD = i18n._("master password")
T_DB_DATABASE = i18n._("database")
T_DB_DATABASE_HELP = i18n._("The database the bot should use.")
T_DB_USER = i18n._("user")
T_DB_USER_HELP = i18n._("The user the bot connects with.")
T_DB_PASSWORD = i18n._("password")
T_DB_SECRET_HELP = i18n._("Stored in config/.secret/, never on this page again.")
T_DB_DERIVED = i18n._("The bot will create the database {database} and the user {user}.")
T_DB_KEPT = i18n._("A password is kept for this test — leave the field empty to reuse it.")
T_DB_TEST_CREATE = i18n._("Test connection and create")
#: The one Test label every other block and step reuses (the Discord token fetch included).
T_TEST_CONNECTION = i18n._("Test connection")
#: The ONE sentence shown wherever Continue is withheld until the step's own Test has passed. Visible
#: on the step (a `title` on a disabled control is not reliably shown), and identical on every step.
T_CONTINUE_NEEDS_TEST = i18n._("Test the connection before you continue.")
T_DB_USER_EXISTS = i18n._("The user {user} already exists.")
T_DB_USER_EXISTS_RESET = i18n._(
    "Reset its password (a new one is generated and stored in config/.secret/).")
T_DB_EXISTING_USER_PASSWORD = i18n._("its password")
#: The Database step's install nudge, shown at the very TOP of the page when nothing answers on the
#: local PostgreSQL port: the operator has no server on this machine yet. ``{link}`` is the download
#: page (see :func:`_sentence_with_link`).
T_DB_INSTALL = i18n._(
    "To use DCSServerBot, you need to install PostgreSQL. You can download it {link}.")
#: The install nudge's link word — the sentence puts the download page on the word "here".
T_DB_DOWNLOAD_HERE = i18n._("here")

#: The BUSY label a submit wears while its request is in flight (see :func:`_busy_submit_script`).
#: An HONEST statement of what the press is doing — never a progress claim, never a percentage.
T_DB_TEST_CREATE_BUSY = i18n._("Creating the database…")
T_DB_TEST_EXISTING_BUSY = i18n._("Testing the connection…")
T_CONTINUE_BUSY = i18n._("Continuing…")

# ---- step 3: the optional cluster database (Federation support) --------------------------------
#: The sibling block's own words. The second block is the SAME renderer as the first, so only the
#: block's title, the checkbox and its help are new strings; every field reuses the first block's.
T_FEDERATION = i18n._("Federation support")
T_FEDERATION_HELP = i18n._(
    "Share resources with another DCSServerBot installation. This adds a second, cluster-wide "
    "database to config/main.yaml — only needed when this installation joins an existing federation.")
T_CLUSTER_LEGEND = i18n._("How should the bot get the cluster database?")

# The step's result sentences, one per classified outcome code (see ``setup.database``). Never a
# raw driver string: the code is what the driver failure was TURNED INTO.
DB_NOTICE: dict[str, str] = {
    "ok_created": i18n._(
        "Connected to PostgreSQL {version}. Created database {database} and user {user}."),
    "ok_reused_user": i18n._(
        "Connected to PostgreSQL {version}. The database {database} was created; the user {user} "
        "already existed."),
    "ok_reused_database": i18n._(
        "Connected to PostgreSQL {version}. The database {database} already existed; the bot will "
        "migrate it on first start."),
    "user_password_reset": i18n._(
        "Connected to PostgreSQL {version}. The user {user}'s password was reset and stored in "
        "config/.secret/."),
    "ok_existing_empty": i18n._(
        "Connected to {database} (PostgreSQL {version}). The database is empty — the bot will "
        "create its tables on first start."),
    "ok_existing_data": i18n._(
        "Connected. This database already holds DCSServerBot data ({count} tables). The bot will "
        "continue from it."),
    "ok_existing_older": i18n._(
        "Connected. This database holds data from an older DCSServerBot ({count} tables). The bot "
        "will migrate it on first start."),
    "master_password_refused": i18n._("Could not connect: the master password was refused."),
    "bad_master_user": i18n._("The master user may only use letters, digits, '_' and '-'."),
    "bad_reserved_master_user": i18n._(
        "The master user may not be named after one of the bot's own secret files. Choose another "
        "name."),
    "unreachable": i18n._(
        "Could not connect: nothing is answering on {host}:{port}. Is the PostgreSQL service "
        "running?"),
    "connection_failed": i18n._("Could not connect to {host}:{port}."),
    "user_cannot_login": i18n._(
        "Could not connect: {user} cannot sign in to this database. Check the password, or switch "
        "to 'Create a new database' — only the superuser credentials on that path can reset it."),
    "no_database": i18n._("Could not connect: the database {database} does not exist."),
    "bad_foreign_tables": i18n._(
        "This database contains tables DCSServerBot did not create ({count} tables, e.g. "
        "{example}). Choose an empty database, or create a new one."),
    "bad_database_holds_data": i18n._(
        "{database} already exists and holds data. Switch to 'Use an existing database' to adopt "
        "it, or enter a different name."),
    "user_exists": i18n._("The user {user} already exists."),
    "user_password_refused": i18n._(
        "That password was not accepted ({left} attempts left before the password is reset)."),
    "provision_failed": i18n._(
        "The server refused while the database or user was being created (database {database}, "
        "user {user}). Check the PostgreSQL log and your superuser's rights, then test again."),
    "continue_blocked": T_CONTINUE_NEEDS_TEST,
    "ok_verified": i18n._(
        "The connection was verified for these values — nothing has been written yet."),
    db_step.SAME_DATABASE_CODE: i18n._(
        "Connected to {database}. This database has the same name as this installation's own — the "
        "bot treats a federation by database name, so this will not federate anything."),
}

#: The outcome codes that pass the step's gate (a warning still passes).
DB_OK_CODES = frozenset({
    "ok_created", "ok_reused_user", "ok_reused_database", "user_password_reset",
    "ok_existing_empty", "ok_existing_data", "ok_existing_older", "ok_verified",
    db_step.SAME_DATABASE_CODE,
})

#: The two outcomes that ask the operator for the existing role's password.
DB_ASK_CODES = frozenset({"user_exists", "user_password_refused"})

#: The outcome codes rendered as an amber warning rather than a plain success.
DB_WARN_CODES = frozenset({"ok_existing_older", "user_password_reset", db_step.SAME_DATABASE_CODE})

T_DB_VERSION_OLD = i18n._("PostgreSQL {version} is older than the supported {min}.")

# ---- step 4: This node -----------------------------------------------------------------------

T_NODE_DCS_LABEL = i18n._("DCS World installation")
T_NODE_DCS_HELP = i18n._(
    "Where DCS World is installed. The bot writes Scripts/MissionScripting.lua there.")
T_NODE_DCS_OTHER = i18n._("Other — I will enter the path")
T_NODE_DCS_DECLINE = i18n._("Continue without a DCS installation")
T_NODE_DCS_PATH = i18n._("path")
T_NODE_DCS_AUTOUPDATE = i18n._(
    "Do you want your DCS installation being auto-updated by the bot?")
T_NODE_LISTEN_PORT = i18n._("listen port")
T_NODE_LISTEN_PORT_HELP = i18n._("Other nodes of this installation talk to this one on this port.")
T_NODE_STATIC_IP = i18n._("Is {ip} a static IP-address for this node?")
T_NODE_DGSA_INTRO = i18n._(
    "DCSServerBot can subscribe to the DGSA banlist managed by a group of DCS administrators.\n"
    "It is split into two parts: one for banned DCS players and one for banned Discord users.\n"
    "For public servers, it's recommended to subscribe to this list for optimal security.")
T_NODE_DGSA_DCS = i18n._("Do you want to use the global banlist for DCS players?")
T_NODE_DGSA_DISCORD = i18n._("Do you want to use the global banlist for Discord users?")

#: The step's result sentences, one per classified code (see ``setup.node`` and the submit path).
#: Never a raw exception: the code is what the check was TURNED INTO.
NODE_NOTICE: dict[str, str] = {
    "port_invalid": i18n._("The listen port must be a whole number between 1 and 65535."),
    "port_busy": i18n._("Something is already listening on port {port}."),
    "dcs_path_required": i18n._("Enter the path to your DCS World installation."),
    "dcs_path_missing": i18n._("That folder does not exist: {path}. Check the path and try again."),
    "dcs_choice_required": i18n._("Choose a DCS World installation, or the last entry."),
}

#: The outcome codes that BLOCK Continue (the answer stays on the step).
NODE_BAD_CODES = frozenset({"port_invalid", "port_busy", "dcs_path_required", "dcs_path_missing",
                            "dcs_choice_required"})

# ---- step 8: DCS servers ---------------------------------------------------------------------

T_SRV_SEARCHING = i18n._("Searching for existing DCS server configurations ...")
T_SRV_FOUND = i18n._("{count} found.")
#: Shown when the scan found NOTHING. The step still asks (see
#: :mod:`services.webservice.setup.servers`), so the sentence must not send the operator away — it
#: names the row the screen offers next.
T_SRV_NONE = i18n._(
    "No configured DCS servers found. Add one below, or continue and add your servers later in the "
    "admin console.")
#: The blank row's own header, where a detected row shows its instance/name (it has neither yet).
T_SRV_ADD = i18n._("Add a server")
#: The blank row's ONE field. For a hand-added server there is no detected folder name, so the name
#: the operator types IS the instance (the Saved Games folder) — which the label and its help say.
T_SRV_ADD_INSTANCE = i18n._("instance — the Saved Games folder name")
T_SRV_ADD_INSTANCE_HELP = i18n._(
    "The name of this instance's folder under Saved Games, for example DCS.openbeta. "
    "DCSServerBot starts DCS from there.")
#: Step 8's channel guide. The channels come from the PICKERS (see :func:`_channel_control`), so the
#: copy asks the operator to CHOOSE them from their Discord server — it never tells them to look up an
#: id. The intro, the three channel roles and the permissions DCSServerBot needs are each their own
#: string, rendered as short paragraphs and one ``<li>`` per line (see :func:`_channels_intro`).
T_SRV_CHANNELS_INTRO = i18n._(
    "Pick each server's Status, Chat and Admin channels below from your Discord server — create any "
    "you are missing there first. The channels are used like this:")
T_SRV_CH_STATUS = i18n._("Status Channel — readable by everyone, writable only by the bot.")
T_SRV_CH_CHAT = i18n._("Chat Channel — readable and writable by everyone.")
T_SRV_CH_ADMIN = i18n._(
    "Admin channel, central or per server — readable and writable only by Admin and DCS Admin users.")
#: The permissions DCSServerBot needs on those channels — one entry per line, each its own msgid so a
#: translator can name the permission the way Discord shows it. The set is
#: ``core.const.DEFAULT_CHANNEL_PERMISSIONS``.
T_SRV_PERMS_LEAD = i18n._("DCSServerBot needs these permissions on the channels:")
T_SRV_PERMS = (
    i18n._("View Channel"),
    i18n._("Send Messages"),
    i18n._("Read Messages"),
    i18n._("Read Message History"),
    i18n._("Add Reactions"),
    i18n._("Attach Files"),
    i18n._("Embed Links"),
    i18n._("Manage Messages"),
)
#: Shown INSTEAD of the permission list when the bot already holds Administrator in the guild: it has
#: every permission already, so there is nothing to set by hand (see :func:`_channels_intro`).
T_SRV_PERMS_ADMIN = i18n._(
    "The bot is an administrator on your server, so it already holds every permission it needs — "
    "nothing to set by hand.")
T_SRV_MANAGE = i18n._("Manage this server through DCSServerBot")
T_SRV_HANDOVER = i18n._(
    "This server is not managed \u2014 channels and settings are set on the console's server page "
    "after setup.")
T_SRV_INSTANCE = i18n._("instance")
T_SRV_NAME = i18n._("server name")
T_SRV_BOT_PORT = i18n._("bot port")
T_SRV_AUTOSTART = i18n._("autostart")
T_SRV_CHANNELS = i18n._("channels")
T_SRV_STATUS = i18n._("status")
T_SRV_CHAT = i18n._("chat")
T_SRV_ADMIN = i18n._("admin")

#: The pickers' own words. A picker is offered only when the guild could be read; otherwise the
#: step keeps the installer's free-text field (see :func:`_channel_control` / :func:`_role_control`).
T_PICK_NONE = i18n._("— not set —")
T_PICK_CHANNEL = i18n._("Chosen from your Discord server.")
T_PICK_ROLE = i18n._("Pick one or more roles from your Discord server.")

#: The sentences a FAILED guild read renders on the steps whose pickers it feeds. Never a raw
#: exception: the code is what the read was TURNED INTO (see ``setup.discord``).
GUILD_NOTICE: dict[str, str] = {
    "discord_bad_token": i18n._(
        "Discord refused this token, so the bot has no connection and DCSServerBot cannot start. "
        "Get the token from the Discord Developer Portal \u2192 your application \u2192 Bot \u2192 Reset "
        "Token; it is shown once. If you no longer have it, reset it there and copy the new value "
        "\u2014 a reset invalidates the old one."),
    "discord_guild_unknown": i18n._("The bot is not in that guild \u2014 or the id is wrong."),
    "discord_forbidden": i18n._("The bot cannot read that guild \u2014 grant it access."),
    "discord_rate_limited": i18n._("Discord is rate-limiting us \u2014 try again in a moment."),
    "discord_unreachable": i18n._(
        "Discord could not be reached \u2014 enter the values by hand, or pick them later in the console."),
}

#: The guide sentences for the two guild-count states. Each is one translated block: what is wrong,
#: why it matters, and what to do about it (see :func:`_guild_notice`).
T_DISCORD_NO_GUILD = i18n._(
    "Discord accepted this token, but the bot is not a member of any Discord server. DCSServerBot "
    "manages exactly one server and cannot start without it.")
T_DISCORD_INVITE = i18n._("Invite the bot with this link, then test again:")
T_DISCORD_MULTIPLE = i18n._(
    "Discord accepted this token, but the bot is a member of more than one Discord server, and "
    "DCSServerBot allows exactly one.")
T_DISCORD_KICK = i18n._(
    "Kick it from every server but the one you keep, then test again. The servers it is in:")
T_DISCORD_CHOOSER = i18n._(
    "Or let the tour make the bot leave the others: pick the server to keep \u2014 it becomes the "
    "guild id written to your configuration.")
T_DISCORD_CONFIRM_LEAVE = i18n._("I understand the bot will leave every other server.")
T_DISCORD_LEAVE_BUTTON = i18n._("Leave the other servers")
T_DISCORD_LEAVE_DONE = i18n._(
    "The bot left {left}. The server kept is {guild} \u2014 that is the guild id your configuration gets.")
T_DISCORD_LEAVE_FAILED = i18n._(
    "These could not be left, so kick them by hand and test again: {failed}.")

#: The step's result sentences, one per classified code (see ``setup.servers``).
SERVER_NOTICE: dict[str, str] = {
    "server_name_required": i18n._("Give every managed server a name."),
    "status_required": i18n._("Enter the status channel id for {name}."),
    "channel_invalid": i18n._(
        "The {field} channel id for {name} must be a number, or -1 for none."),
    "server_name_duplicate": i18n._("Two servers are named {name}. Names must be unique."),
}

# ---- step 5: Discord — the bot ---------------------------------------------------------------

T_DISCORD_GUILD_ID = i18n._("Guild ID")
T_DISCORD_GUILD_ID_HELP = i18n._("Right-click your Discord server \u2192 Copy Server ID.")
T_DISCORD_TOKEN = i18n._("TOKEN")
#: The token's help line's TAIL, after the Developer Portal link (see :func:`_token_help`).
T_DISCORD_TOKEN_HELP = i18n._(
    "→ your application → Bot → Reset Token. This is shown once; the console stores it in "
    "config/.secret/.")
#: The Discord step's help link label: the portal where the application is created (see
#: :func:`_doc_link`).
T_DISCORD_PORTAL = i18n._("Discord Developer Portal")
#: The Discord step's FIRST line: where the four values come from. ``{link}`` is the portal (see
#: :func:`_sentence_with_link`).
T_DISCORD_PORTAL_INTRO = i18n._(
    "To obtain the following values, you need to log into the {link}.")
T_DISCORD_OWNER = i18n._("Owner ID")
T_DISCORD_OWNER_HELP = i18n._("Right-click your own Discord user \u2192 Copy User ID.")
T_DISCORD_MEMBERS = i18n._("Do you have more than 10000 members in your Discord server?")
T_DISCORD_MEMBERS_NOTE = i18n._("You need to grant the 'View Audit Log' permission to your bot.")
T_DISCORD_TOKEN_KEPT = i18n._("A token is kept for this step \u2014 leave the field empty to reuse it.")
#: The console's Discord OAuth2 login — the secret sits with the bot token, in the step's own
#: credential grid, and is asked for HERE (never on the Web-console step), only while Discord
#: support is on. Its own help line and the skip note below name it; no separate group heading.
T_DISCORD_CLIENT_SECRET = i18n._("client secret")
T_DISCORD_CLIENT_SECRET_HELP = i18n._(
    "Discord Developer Portal \u2192 your application \u2192 OAuth2 \u2192 Client Secret. Shown once; the console "
    "stores it in config/.secret/.")
T_DISCORD_CLIENT_SECRET_KEPT = i18n._(
    "A client secret is kept \u2014 leave the field empty to reuse it.")
T_DISCORD_CLIENT_SECRET_SKIP = i18n._(
    "Leave it empty and the console's Discord login stays off.")
#: The BUSY label the Continue shows while the guild read is in flight (see :func:`_busy_submit_script`).
T_DISCORD_FETCH_BUSY = i18n._("Connecting to Discord…")
#: The Test's SUCCESS confirmation: it names what the read found (the guild), so a working connection
#: is stated rather than merely implied by the absent failure guides. The ``{guild}`` hole is filled
#: from the read's own answer — ``Name (id)`` — never from the draft.
T_DISCORD_CONNECTED = i18n._("Connected — the bot is in {guild}.")

#: Step 6's result sentences, one per classified code.
DISCORD_NOTICE: dict[str, str] = {
    "discord_guild_required": i18n._("Enter your Discord Guild ID."),
    "discord_guild_invalid": i18n._("The Guild ID must be a whole number."),
    "discord_owner_required": i18n._("Enter your Discord Owner ID."),
    "discord_owner_invalid": i18n._("The Owner ID must be a whole number."),
    "discord_token_required": i18n._("Enter your Discord TOKEN."),
    "continue_blocked": T_CONTINUE_NEEDS_TEST,
    "leave_choose_required": i18n._("Choose the server to keep."),
    "leave_confirm_required": i18n._("Tick the box to confirm the bot will leave the other servers."),
}

# ---- step 7: Discord — roles and channels -----------------------------------------------------

T_ROLES_INTRO = i18n._(
    "We now need to setup your Discord roles and channels.\n"
    "DCSServerBot creates a role mapping for your bot users. It has the following internal roles:")
T_ROLES_DEFAULTS = i18n._(
    "You can keep the defaults, if unsure and create the respective roles in your Discord server.")
T_ROLES_ADD = i18n._("+ Add")
T_ROLE_MEANINGS: dict[str, str] = {
    "Admin": i18n._("Users can delete data, change the bot, run commands on your server"),
    "DCS Admin": i18n._("Users can upload missions, start/stop DCS servers, kick/ban users, etc."),
    "DCS": i18n._("Normal user, can pull statistics, ATIS, etc."),
}
T_DISCORD_AUDIT = i18n._("audit channel")
T_DISCORD_AUDIT_HELP = i18n._("A channel for audit events \u2014 optional, -1 for none.")
T_DISCORD_ADMIN_INTRO = i18n._(
    "The bot can either use a dedicated admin channel for each server or a central admin channel "
    "for all servers.")
T_DISCORD_ADMIN_PER_SERVER = i18n._("A dedicated admin channel per server")
T_DISCORD_ADMIN_CENTRAL = i18n._("One central admin channel for all servers")
T_DISCORD_ADMIN = i18n._("admin channel")
T_DISCORD_ADMIN_HELP = i18n._("The id of the central admin channel \u2014 optional, -1 for none.")

#: Step 7's result sentences, one per classified code.
ROLES_NOTICE: dict[str, str] = {
    "role_required": i18n._("Enter at least one role or role id for {role}."),
    "channel_invalid": i18n._("A channel id must be a whole number, or -1 for none."),
}

# ---- step 5': Your community (the no-Discord path) --------------------------------------------

T_COMMUNITY_NAME = i18n._("Please enter the name of your DCS group")
T_COMMUNITY_INTRO = i18n._(
    "You now need to setup your users.\n"
    "DCSServerBot uses the following internal roles:")
T_COMMUNITY_MEANINGS: dict[str, str] = {
    "Admin": i18n._("Users can delete data and change the bot"),
    "DCS Admin": i18n._("Users can restart missions, kick/ban users, etc."),
}
T_COMMUNITY_UCIDS = i18n._("Please enter a comma-separated list of UCIDs")

#: Step 6' - the no-Discord screen's result sentences, one per classified code.
COMMUNITY_NOTICE: dict[str, str] = {
    "community_name_required": i18n._("Enter the name of your DCS group."),
}

# ---- the Web console step (step 7) ------------------------------------------------------------

T_CONSOLE_SWITCH = i18n._("Run the web admin console (recommended)")
T_CONSOLE_SWITCH_HELP = i18n._(
    "Configure and watch this installation in a browser. Turn this off and DCSServerBot runs "
    "bot-only — you edit the YAML files by hand.")
T_CONSOLE_OFF_NOTE = i18n._(
    "The console is off. DCSServerBot runs bot-only, and enabling the console later is one key in "
    "config/services/webservice.yaml.")
T_CONSOLE_ADDRESS = i18n._("public address")
T_CONSOLE_ADDRESS_HELP = i18n._(
    "The address this console is reachable at. Discord sends its login back here, so it must match "
    "the redirect you registered.")
T_CONSOLE_TITLE = i18n._("Console account")
T_CONSOLE_HELP = i18n._(
    "The console's own sign-in: a username and a password. Only the password's hash is stored. This "
    "account always exists — it is how you get into the console when Discord is down.")
T_CONSOLE_USERNAME = i18n._("username")
T_CONSOLE_PASSWORD = i18n._("password")
T_CONSOLE_PASSWORD_KEPT = i18n._("A password is set — leave the field empty to keep it.")

#: The console account's result sentences, one per classified code.
CONSOLE_NOTICE: dict[str, str] = {
    "console_username_required": i18n._("Enter a username for the console account."),
    "console_password_required": i18n._("Enter a password for the console account."),
    "console_password_invalid": i18n._("The password must be at most {max} characters."),
}

# ---- step 9: Review and write ------------------------------------------------------------------

T_REVIEW_ALREADY = i18n._("Already done")
T_REVIEW_WILL_WRITE = i18n._("Will be written, nothing yet")
T_REVIEW_NOT_WRITTEN = i18n._("Not written")
T_REVIEW_WRITE = i18n._("Write configuration")
T_REVIEW_WRITE_AGAIN = i18n._("Write again")
T_REVIEW_WRITTEN = i18n._("written")
T_REVIEW_NOT_WRITTEN_ITEM = i18n._("not written")
T_REVIEW_RESULT = i18n._("What the write did")
T_REVIEW_SKIPPED = i18n._("not attempted — an earlier file was not written")
T_REVIEW_HALF = i18n._(
    "Only the entries marked written reached the disk. The setup is not finished — fix the reason "
    "below and write again.")
T_REVIEW_NO_REASON = i18n._("the file was not written")
T_REVIEW_DB_CREATED = i18n._("the bot's database and its own user were created (step 3)")
T_REVIEW_DB_VERIFIED = i18n._("the bot's database and its user were verified (step 3)")
T_REVIEW_MASTER_STORED = i18n._("the PostgreSQL master password is stored in config/.secret/")
T_REVIEW_DB_PASSWORD_STORED = i18n._("the bot's database password is stored in config/.secret/")
T_REVIEW_DISCORD_NOT_WRITTEN = i18n._(
    "the Discord answers (guild, owner, roles) — Discord support is off")
T_REVIEW_COMMUNITY_NOT_WRITTEN = i18n._(
    "the community answers (group name, users) — Discord support is on")
T_REVIEW_MISSING = i18n._("Missing — enter again")
T_REVIEW_INCOMPLETE = i18n._(
    "A restart dropped a secret this configuration needs, and nothing was written. Go back to the "
    "step named below, enter the value again, then write.")
T_REVIEW_MISSING_TOKEN = i18n._("the Discord bot token is not held — enter it again on step 5")
T_REVIEW_MISSING_DATABASE = i18n._("the database password is not held — enter it again on step 3")
T_REVIEW_MISSING_CLUSTER_DATABASE = i18n._(
    "the cluster database's password is not held — enter it again on step 3")
T_REVIEW_MAIN_CLUSTER = i18n._("guild, language, autoupdate, the cluster database")
T_REVIEW_MAIN_CLUSTER_SAME = i18n._(
    "guild, language, autoupdate, the cluster database — NOT a federation (same database name)")
T_REVIEW_CLUSTER_NOT_WRITTEN = i18n._(
    "the cluster database (config/.secret/clusterdb.pkl and main.yaml) — Federation support is off")
T_REVIEW_CLUSTER_NOT_VERIFIED = i18n._(
    "the cluster database (config/.secret/clusterdb.pkl and main.yaml) — the cluster database was "
    "not verified, so it is not written")
T_REVIEW_CONSOLE_LOGIN_NOT_WRITTEN = i18n._(
    "the console's Discord login — no client secret is held, so it stays off")

#: The one-line description of each written target, keyed by its stable id (``setup.review``).
T_REVIEW_FILES: dict[str, str] = {
    "main": i18n._("guild, language, autoupdate"),
    "bot": i18n._("roles, channels, owner"),
    "webservice": i18n._("the console and its sign-in"),
    "nodes": i18n._("this node, DCS"),
    "servers": i18n._("status/chat/admin channels"),
    "scheduler": i18n._("autostart per server"),
    "cloud": i18n._("DGSA banlist opt-ins"),
    "token": i18n._("the Discord bot token"),
    "discord_client_secret": i18n._("the console's Discord client secret"),
    "database_password": i18n._("the bot's database password"),
    "clusterdb_password": i18n._("the cluster database's password"),
}

# ---- the Done screen ---------------------------------------------------------------------------

T_DONE_TITLE = i18n._("Setup is done")
T_DONE_WRITTEN = i18n._(
    "Your configuration is written. The setup console closes now and DCSServerBot starts.")
T_DONE_OPEN = i18n._("The console will be at {url} once it is up.")
T_DONE_SIGNIN = i18n._("Sign in with the console account you just created.")
T_DONE_ADD_SERVERS = i18n._("Add your DCS servers in the console: Servers → Add Server.")
T_DONE_ADD_SERVERS_OFF = i18n._(
    "To add or change DCS servers, edit config/nodes.yaml and config/servers.yaml.")
T_DONE_NO_CONSOLE = i18n._(
    "The web admin console is off — DCSServerBot runs bot-only until you enable it in "
    "config/services/webservice.yaml.")
T_DONE_START = i18n._("If it is not running, start it with {script}.")
T_DONE_BUTTON = i18n._("Open the console")

# ---- the joining state: the add/rename question, its refusals and its own Done sentence ----

T_JOIN_TITLE = i18n._("This node is not configured yet")
T_JOIN_INTRO = i18n._(
    "This installation is already configured, but it has no entry for the node {node} in "
    "config/nodes.yaml, so the bot cannot start on this machine. Is this a new node, or did the "
    "hostname of an existing node change?")
T_JOIN_NEW = i18n._("A new node")
T_JOIN_NEW_HELP = i18n._(
    "Add this machine to config/nodes.yaml. Nothing else of this installation is changed.")
T_JOIN_RENAME = i18n._("An existing node whose hostname changed")
T_JOIN_RENAME_HELP = i18n._(
    "Rename one of the configured nodes to {node}. Its configuration and its database rows are "
    "carried over, and nothing is added.")
T_JOIN_OLD = i18n._("node to rename")
T_JOIN_CONFIRM = i18n._(
    "I understand: the node picked above was this machine, and it is now called {node}")
T_JOIN_CHOOSE = i18n._("Choose whether this is a new node or a renamed one.")
T_JOIN_CONFIRM_NEEDED = i18n._("Tick the confirmation before renaming a node.")
T_JOIN_OLD_NEEDED = i18n._("Pick the node whose hostname changed.")
T_JOIN_FAILED = i18n._(
    "The node could not be renamed. Nothing was changed by this page; the log names the reason.")
T_JOIN_RENAMED = i18n._("Node {old} was renamed to {node}.")
T_DONE_JOINED = i18n._("This node was added to config/nodes.yaml as {node}.")

#: The two action values the joining state's first screen posts. They are not step ids: the screen
#: comes before the rail exists, so it is routed on its own action and not on ``n``.
JOIN_NEW_ACTION = "join_new"
JOIN_RENAME_ACTION = "join_rename"
JOIN_ACTIONS = (JOIN_NEW_ACTION, JOIN_RENAME_ACTION)


def _top_bar(request: Request, lang: str, theme: str, translate) -> str:
    """The tour's top bar: brand, the flag control and the theme control."""
    next_target = _return_target(request)
    return (f'<header class="topbar"><div class="brand"><b>DCSServerBot</b>'
            f'<small>{escape(translate(T_SETUP))}</small></div><div class="spacer"></div>'
            f'{_flag_control(request, lang, next_target)}'
            f'{_theme_control(request, lang, theme, next_target)}</header>')


def _document(request: Request, draft: SetupDraft, *, title: str, body: str,
              current: int | None, notice: str = "") -> str:
    """The tour's whole document: top bar (brand, flags, theme), rail, legend, card.

    *notice* is the classified save-failure banner a failed draft write renders above the card body.
    """
    lang = language_step.prefill_language(request, draft)
    theme = theme_module.resolve_theme(request)
    translate = i18n.translations_for(lang).gettext
    top = _top_bar(request, lang, theme, translate)
    return (
        "<!doctype html>\n"
        f'<html lang="{escape(lang)}" data-theme="{escape(theme)}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>DCSServerBot setup</title>\n'
        f'{_the_style()}\n</head>\n<body>\n{top}\n'
        f'<div class="tour">{_rail(draft, current, translate, _mode(request))}'
        f'<main class="panel"><p class="legend">{escape(translate(LEGEND))}</p>'
        f'<section class="card"><div class="hd"><h1>{escape(translate(title))}</h1></div>'
        f'<div class="bd">{notice}{body}</div></section></main></div>\n'
        # The rail's current row is scrolled into view when the rail is a horizontal scroller
        # (below the side-by-side breakpoint). Nothing here is required: without scripting the rail
        # is still the whole progress statement, just scrolled to its start.
        '<script>(function(){var on=document.querySelector(".rail .row.on");'
        'if(on&&on.scrollIntoView)on.scrollIntoView({inline:"center",block:"nearest"});})();</script>\n'
        '</body>\n</html>\n')


def _return_target(request: Request) -> str:
    """The page a flag/theme switch returns to: this request's own path and query, when safe.

    The ``lang`` parameter is dropped from the returned query: it is a one-shot override for THIS
    render, and carrying it into the switch's target would pin the language and make the flag the
    operator just pressed have no effect.
    """
    path = request.url.path
    if not path.startswith("/setup"):
        return STEP_PATH
    query = urlencode([(key, value) for key, value in parse_qsl(request.url.query, keep_blank_values=True)
                       if key != "lang"])
    return f"{path}?{query}" if query else path


def _flags_row(request: Request, lang: str, next_target: str) -> str:
    """The answer's flag row on step 1 — the same control, pointing back at step 1."""
    return f'<div class="flags">{_flag_control(request, lang, next_target)}</div>'


def _language_source_note(choice: language_step.LanguageChoice, translate) -> str:
    """The one line naming where step 1's preselection came from, in the reader's language.

    The source is the one :func:`services.webservice.setup.language.prefill_choice` actually used, so
    the sentence and the marked flag cannot disagree; the browser's own header is quoted back when
    that is what answered.
    """
    msgid = T_LANG_SOURCE.get(choice.source) or T_LANG_SOURCE[language_step.SOURCE_DEFAULT]
    text = _say(translate, msgid, header=choice.header or "", param=LANGUAGE_PARAM,
                cookie=i18n.COOKIE_NAME)
    return f'<p class="source">{text}</p>'


def _step_one(request: Request, draft: SetupDraft, translate) -> str:
    token = session.get_csrf_token(request)
    choice = language_step.prefill_choice(request, draft)
    lang = choice.code
    next_target = f"{STEP_PATH}?n=1"
    checked = ' checked' if draft.bot_language else ''
    return (
        f'<form method="post" action="{STEP_PATH}">'
        f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        f'<input type="hidden" name="n" value="1">'
        f'{_flags_row(request, lang, next_target)}'
        f'{_language_source_note(choice, translate)}'
        f'<label class="switch"><input type="checkbox" name="bot_language"{checked}>'
        f'<span>{escape(translate(T_BOT_LANGUAGE))}'
        f'<span class="help">{escape(translate(T_BOT_LANGUAGE_HELP))}</span></span></label>'
        f'<aside class="prereq">{escape(translate(T_PREREQUISITES))}</aside>'
        f'<div class="actions"><span class="grow"></span>'
        f'<button type="submit">{escape(translate(T_CONTINUE))}</button></div>'
        f'</form>')


def _step_two(request: Request, draft: SetupDraft, translate, *, stay: bool = False) -> str:
    token = session.get_csrf_token(request)
    back = _previous_step(draft, 2)
    autoupdate = ' checked' if draft.autoupdate else ''
    discord = ' checked' if draft.discord else ''
    back_link = (f'<a class="link" href="{STEP_PATH}?n={back}">'
                 f'{escape(translate(T_BACK))}</a>' if back else '<span class="grow"></span>')
    # The one progressive enhancement: toggling the Discord switch re-renders the rail a moment
    # sooner (its rows change). Without it the answer is still saved, on Continue.
    script = ('<script>(function(){var c=document.querySelector(\'input[name="discord"]\');'
              'if(!c)return;c.addEventListener("change",function(){var f=c.form;if(!f)return;'
              'var s=document.createElement("input");s.type="hidden";s.name="stay";s.value="1";'
              'f.appendChild(s);f.submit();});})();</script>')
    return (
        f'<form method="post" action="{STEP_PATH}">'
        f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        f'<input type="hidden" name="n" value="2">'
        f'<label class="switch"><input type="checkbox" name="autoupdate"{autoupdate}>'
        f'<span>{escape(translate(T_AUTOUPDATE))}</span></label>'
        f'<label class="switch"><input type="checkbox" name="discord"{discord}>'
        f'<span>{escape(translate(T_DISCORD))}'
        f'<span class="help">{escape(translate(T_DISCORD_HELP))}</span></span></label>'
        f'<div class="actions">{back_link}'
        f'<button type="submit">{escape(translate(T_CONTINUE))}</button></div>'
        f'</form>{script}')


def _say(translate, msgid: str, **params) -> str:
    """Translate *msgid* then fill its holes (translate first, interpolate after), escaped.

    A catalog line whose placeholders do not match degrades to the English source instead of
    raising into the render — the same rule the shell's per-request sentences follow.
    """
    text = translate(msgid)
    if not params:
        return escape(text)
    try:
        return escape(text.format(**params))
    except (KeyError, IndexError, ValueError):
        return escape(msgid)


def _doc_link(translate, url: str, label_msgid: str) -> str:
    """A help link: a real anchor, so the tour works with scripting off, whose translated label names
    where it goes, followed by the URL as plain text so a copy carries the address."""
    label = escape(translate(label_msgid))
    safe = escape(url)
    return (f'<a class="link" href="{safe}" target="_blank" rel="noopener noreferrer">{label}</a> '
            f'(<code>{safe}</code>)')


def _sentence_with_link(translate, template_msgid: str, url: str, label_msgid: str) -> str:
    """A translated sentence carrying ONE help link: its ``{link}`` becomes the rendered anchor.

    The sentence is translated and split at the placeholder, so the anchor and its plain-text URL sit
    INSIDE the sentence in the reader's own word order — the link is part of the sentence, not an
    appended control. A template without the placeholder renders whole, with no link.
    """
    text = translate(template_msgid)
    if "{link}" not in text:
        return escape(text)
    pre, _, post = text.partition("{link}")
    return f'{escape(pre)}{_doc_link(translate, url, label_msgid)}{escape(post)}'


def _field(label: str, name: str, value, help_text: str | None = None, *,
           type_: str = "text", required: bool = False, help_markup: str | None = None) -> str:
    """One labelled input. A password field is always rendered EMPTY (secrets never come back).

    *help_markup* is an ALREADY-escaped help line (a sentence carrying a help link, see
    :func:`_doc_link`); when given it is rendered verbatim in place of the escaped *help_text*.
    """
    if help_markup is not None:
        help_html = f'<span class="help">{help_markup}</span>'
    else:
        help_html = f'<span class="help">{escape(help_text)}</span>' if help_text else ""
    req = " required" if required else ""
    return (f'<label class="field"><span class="lbl">{escape(label)}</span>'
            f'<input type="{type_}" name="{name}" value="{escape(str(value))}"{req}>'
            f'{help_html}</label>')


def _busy_attr(translate, msgid: str) -> str:
    """The ``data-busy`` attribute a submit carries: its translated label while the request is in
    flight (see :func:`_busy_submit_script`). Rendered on the button, so it is already translated
    when the client-side enhancement reads it — the script never owns a string."""
    return f' data-busy="{escape(translate(msgid))}"'


# ------------------------------------------------------------------------- the Discord pickers

def _guild_context(request: Request, draft: SetupDraft):
    """The guild read for the draft's CURRENT Discord answers: ``(guild_or_None, failure_code)``.

    The state comes from the in-memory cache and status, NEVER from the draft: the data is a live
    machine fact (a guild layout) and the draft is plain text on disk. A key the last read did not
    cover yields no pickers; a read that failed for THIS key yields its code, so the step can say so.

    WHILE JOINING the key is the INSTALLATION's own: this flow asks no Discord question, so the token
    comes from the shared ``.secret/token.pkl`` and the guild id from the shared ``main.yaml`` (see
    :func:`setup.join.discord_context`) rather than from the draft.
    """
    if _mode(request) == boot_module.JOIN:
        context = join_step.discord_context(_config_dir(request))
        if not context.discord:
            return None, ""
        token, guild_id = context.token, context.guild_id
    else:
        if not draft.discord:
            return None, ""
        token = discord_step.token_store(request.app).get(discord_step.TOKEN_KEY)
        guild_id = discord_step.answers_from_mapping(draft.discord_bot).guild_id
    data = discord_step.cached_guild(request.app, token, guild_id)
    if data is not None:
        return data, ""
    return None, discord_step.fetch_status(request.app, token, guild_id) or ""


def _guild_name(guild) -> str:
    """A guild as the guide names it: ``Name (id)``. The name is operator data and is escaped where
    it is rendered (through :func:`_say` for a hole, or :func:`escape` for a list entry)."""
    gid, name = guild
    return f"{name} ({gid})"


def _guild_notice(request: Request, draft: SetupDraft, translate, code: str, *,
                  chooser_below: bool = False) -> str:
    """The honest guide for a FAILED guild read, or "" (nothing to say).

    A plain failure says its one sentence. The two guild-count states carry a fuller block — what is
    wrong, why it matters, and what to do about it — because the operator has a step to take outside
    the form: invite the bot (a generated invite link, clickable AND plain text), or kick it from the
    servers it should not be in. The invite link is built from the token's own application id and the
    project's own permission set (see :func:`setup.discord.invite_url`), never invented here.

    *chooser_below* says the too-many-guilds CHOOSER is rendered right after this block (step 5), whose
    own legend already carries the chooser sentence — so it is not repeated above it.
    """
    if not code:
        return ""
    if code == "discord_no_guild":
        return _no_guild_guide(request, translate)
    if code == "discord_multiple_guilds":
        return _multiple_guilds_guide(request, draft, translate, chooser_below=chooser_below)
    if code in GUILD_NOTICE:
        return (f'<p class="notice warn" role="status">'
                f'{_say(translate, GUILD_NOTICE[code])}</p>')
    return ""


def _invite_link_html(translate, token) -> str:
    """The generated invite link: a clickable anchor AND the same URL as plain text, so it survives a
    copy-paste. ``""`` when no link can be built (no derivable application id)."""
    url = discord_step.invite_url(token)
    if not url:
        return ""
    safe = escape(url)
    return (f'<p class="note">{escape(translate(T_DISCORD_INVITE))}</p>'
            f'<p class="note"><a class="link" href="{safe}">{safe}</a></p>'
            f'<p class="note"><code>{safe}</code></p>')


def _no_guild_guide(request: Request, translate) -> str:
    """The no-guild guide: the bot is in no server, so invite it — with the generated link."""
    token = discord_step.token_store(request.app).get(discord_step.TOKEN_KEY)
    return (f'<p class="notice warn" role="status">{_say(translate, T_DISCORD_NO_GUILD)}</p>'
            f'{_invite_link_html(translate, token)}')


def _multiple_guilds_guide(request: Request, draft: SetupDraft, translate, *,
                           chooser_below: bool = False) -> str:
    """The too-many-guilds guide: name every server it is in (escaped), and offer the two paths.

    The list is the read's own guild list (a live fact, never the draft); a server that cannot be
    named still gets a line, so the operator sees the full picture rather than a silently short list.
    The CHOOSER's controls are rendered by the step that owns the form (see :func:`_leave_chooser`),
    not here, so this function stays valid inside any step's form; *chooser_below* drops the chooser
    sentence when that chooser (with its own legend) follows this block on the same screen.
    """
    token = discord_step.token_store(request.app).get(discord_step.TOKEN_KEY)
    guild_id = discord_step.answers_from_mapping(draft.discord_bot).guild_id
    guilds = discord_step.fetch_guilds(request.app, token, guild_id)
    items = "".join(f'<li>{escape(_guild_name(guild))}</li>' for guild in guilds)
    listing = f'<ul class="guilds">{items}</ul>' if items else ""
    chooser_line = "" if chooser_below \
        else f'<p class="note">{escape(translate(T_DISCORD_CHOOSER))}</p>'
    return (f'<p class="notice warn" role="status">{_say(translate, T_DISCORD_MULTIPLE)}</p>'
            f'<p class="note">{escape(translate(T_DISCORD_KICK))}</p>{listing}{chooser_line}')


def _leave_chooser(request: Request, draft: SetupDraft, translate) -> str:
    """The chooser's controls, inside the step's own form: pick the server to keep, confirm, leave.

    Offered only while the read says the bot is in more than one guild. The radio values are the
    read's own guild ids; the server POST re-checks the choice against that list, so a crafted value
    can never name a server the bot is not in. The confirmation is an explicit tick (JavaScript off).
    """
    token = discord_step.token_store(request.app).get(discord_step.TOKEN_KEY)
    guild_id = discord_step.answers_from_mapping(draft.discord_bot).guild_id
    guilds = discord_step.fetch_guilds(request.app, token, guild_id)
    if len(guilds) < 2:
        return ""
    options = "".join(
        f'<label class="opt"><input type="radio" name="keep_guild" value="{gid}">'
        f'<span>{escape(_guild_name((gid, name)))}</span></label>'
        for gid, name in guilds)
    return (
        f'<fieldset class="choice"><legend class="choice-legend">'
        f'{escape(translate(T_DISCORD_CHOOSER))}</legend>{options}'
        f'<label class="opt"><input type="checkbox" name="confirm_leave">'
        f'<span>{escape(translate(T_DISCORD_CONFIRM_LEAVE))}</span></label>'
        f'<div class="actions"><button type="submit" name="action" value="leave_others">'
        f'{escape(translate(T_DISCORD_LEAVE_BUTTON))}</button></div></fieldset>')


def _leave_result(draft: SetupDraft, translate) -> str:
    """The per-guild outcome of a leave the tour performed, or "" when none is recorded.

    A success names the servers left and the one kept; a failure names the ones that refused, honestly,
    so the operator kicks them by hand — the tour never claims a leave it did not achieve.
    """
    result = draft.discord_bot.get("leave")
    if not isinstance(result, dict):
        return ""
    parts: list[str] = []
    kept = result.get("kept")
    left = [tuple(entry) for entry in (result.get("left") or [])]
    failed = [tuple(entry) for entry in (result.get("failed") or [])]
    if left:
        left_text = ", ".join(_guild_name(entry) for entry in left)
        kept_text = _guild_name(tuple(kept)) if kept else ""
        parts.append(f'<p class="notice ok" role="status">'
                     f'{_say(translate, T_DISCORD_LEAVE_DONE, left=left_text, guild=kept_text)}</p>')
    if failed:
        failed_text = ", ".join(f"{_guild_name((entry[0], entry[1]))} [{entry[2]}]"
                                for entry in failed)
        parts.append(f'<p class="notice warn" role="status">'
                     f'{_say(translate, T_DISCORD_LEAVE_FAILED, failed=failed_text)}</p>')
    return "".join(parts)


def _channel_control(label: str, name: str, value, guild, translate,
                     help_text: str | None = None) -> str:
    """A channel field: a REAL ``<select>`` when the guild was read, else the installer's text field.

    The picker is plain markup, so it works with JavaScript off. A stored value that is not among the
    guild's channels (a hand-typed id, or the installer's ``-1``) is kept as its own selected option,
    so nothing the operator already answered is silently dropped. The no-picker fallback keeps the
    installer's own *help_text*, so the step reads the same with or without a guild.
    """
    if guild is None:
        return _field(label, name, value, help_text)
    current = str(value or "").strip()
    if current == "-1":
        current = ""
    groups: dict[str, list] = {}
    for option in guild.channels:
        groups.setdefault(option.category, []).append(option)
    known = {str(option.id) for option in guild.channels}
    parts = [f'<option value=""{" selected" if not current else ""}>'
             f'{escape(translate(T_PICK_NONE))}</option>']
    if current and current not in known:
        parts.append(f'<option value="{escape(current)}" selected>{escape(current)}</option>')
    for group in sorted(groups):
        options = "".join(
            f'<option value="{option.id}"'
            f'{" selected" if current == str(option.id) else ""}>#{escape(option.name)}</option>'
            for option in groups[group])
        if group:
            parts.append(f'<optgroup label="{escape(group)}">{options}</optgroup>')
        else:
            parts.append(options)
    return (f'<label class="field"><span class="lbl">{escape(label)}</span>'
            f'<select name="{name}">{"".join(parts)}</select>'
            f'<span class="help">{escape(translate(T_PICK_CHANNEL))}</span></label>')


def _role_select(name: str, values, guild) -> str:
    """A multiple ``<select>`` for a role row: one ``<option>`` per guild role, name-valued.

    No JavaScript: a browser posts one repeated ``role_<index>`` field per selected option, which is
    exactly the shape the server already reads. A stored value the guild does not offer (a default
    like ``@everyone``, or a hand-typed id) is kept as its own selected option.
    """
    selected = [str(value) for value in values]
    offered = list(guild.roles)
    for value in selected:
        if value not in offered:
            offered.append(value)
    options = "".join(
        f'<option value="{escape(role)}"{" selected" if role in selected else ""}>'
        f'{escape(role)}</option>' for role in offered)
    size = min(max(len(offered), 4), 12)
    return f'<select name="{name}" multiple size="{size}">{options}</select>'


def _db_last(document) -> dict:
    """The classified ``last`` outcome of a draft database block, or ``{}``."""
    last = document.get("last") if isinstance(document, dict) else None
    return last if isinstance(last, dict) else {}


def _cluster_last(document, local_answers, cluster_answers, *, enabled: bool) -> dict:
    """The cluster block's ``last`` outcome, with the same-name relation recomputed at RENDER time.

    The relation is not a fact the Test stored — it is a property of the two CURRENT database names,
    so a local rename adds or removes the amber warning without the cluster block being re-tested,
    exactly as the Review line already recomputes it. A failed test keeps its own sentence.
    """
    last = _db_last(document)
    if not enabled or last.get("code") not in DB_OK_CODES:
        return last
    if db_step.same_database(local_answers, cluster_answers):
        return {**last, "code": db_step.SAME_DATABASE_CODE,
                "data": {**(last.get("data") or {}), "database": cluster_answers.database}}
    if last.get("code") == db_step.SAME_DATABASE_CODE:
        return {**last, "code": "ok_verified", "data": {}}
    return last


def _db_notice(translate, last: dict, *, enabled: bool) -> tuple[str, str | None]:
    """The classified notice for ONE database block, and the code it was drawn from.

    The code is turned into the operator's sentence (never a driver string); a plain success whose
    values have since changed is dropped; an outdated server rides on the outcome as an amber tag
    instead of blocking it. Shared by the local block and the cluster block.
    """
    code = last.get("code")
    if not enabled and code in DB_OK_CODES:
        code = None
    level = "warn" if code in DB_WARN_CODES else ("ok" if code in DB_OK_CODES else "bad")
    notice = ""
    if code in DB_NOTICE:
        data = dict(last.get("data") or {})
        data.setdefault("version", last.get("version"))
        notice = (f'<p class="notice {level}" role="alert">'
                  f'{_say(translate, DB_NOTICE[code], **data)}</p>')
    if last.get("version_outdated") and code is not None:
        notice += (f'<p class="tag warn">'
                   f'{_say(translate, T_DB_VERSION_OLD, version=last.get("version"), min=db_step.MIN_SERVER_VERSION_MAJOR)}</p>')
    return notice, code


def _db_block(translate, answers: db_step.DatabaseAnswers, *, master_password: str,
              bot_password: str, enabled: bool, last: dict, legend: str, prefix: str,
              action_test: str) -> str:
    """One database block: the path radio, the per-path fields, the classified notice and the Test.

    The SAME renderer serves the local database (``prefix=""``) and the optional cluster database
    (``prefix="cluster_"``). The prefix namespaces every field name AND the radio, so one form can
    carry both blocks without their values colliding and the first block's ``input[name="path"]``
    re-submit cannot fire for this one. ``action_test`` is the action value this block's Test posts.
    """
    notice, code = _db_notice(translate, last, enabled=enabled)

    def name(field: str) -> str:
        return f"{prefix}{field}"

    create_checked = " checked" if answers.path == db_step.CREATE else ""
    existing_checked = " checked" if answers.path == db_step.EXISTING else ""
    choice = (
        f'<fieldset class="choice"><legend class="choice-legend">{escape(translate(legend))}</legend>'
        f'<label class="opt"><input type="radio" name="{name("path")}" value="create"{create_checked}>'
        f'<span><b>{escape(translate(T_DB_CREATE))}</b>'
        f'<span class="help">{escape(translate(T_DB_CREATE_HELP))}</span></span></label>'
        f'<label class="opt"><input type="radio" name="{name("path")}" '
        f'value="existing"{existing_checked}>'
        f'<span><b>{escape(translate(T_DB_EXISTING))}</b>'
        f'<span class="help">{escape(translate(T_DB_EXISTING_HELP))}</span></span></label>'
        f'</fieldset>')

    fields = [_field(translate(T_DB_HOST), name("host"), answers.host,
                     translate(T_DB_HOST_HELP), required=True),
              _field(translate(T_DB_PORT), name("port"), answers.port, None, required=True)]
    if answers.path == db_step.CREATE:
        fields.append(_field(translate(T_DB_MASTER_DATABASE), name("master_database"),
                             answers.master_database, translate(T_DB_MASTER_DATABASE_HELP)))
        fields.append(_field(translate(T_DB_MASTER_USER), name("master_user"), answers.master_user,
                             translate(T_DB_MASTER_USER_HELP)))
        fields.append(_field(translate(T_DB_MASTER_PASSWORD), name("master_password"), "",
                             translate(T_DB_SECRET_HELP), type_="password",
                             required=not master_password))
        fields.append(f'<p class="note">{_say(translate, T_DB_DERIVED, database=answers.database, user=answers.user)}</p>')
        fields.append(_field(translate(T_DB_DATABASE), name("database"), answers.database))
        fields.append(_field(translate(T_DB_USER), name("user"), answers.user))
        if master_password:
            fields.append(f'<p class="note">{escape(translate(T_DB_KEPT))}</p>')
        if code in DB_ASK_CODES:
            fields.append(f'<p class="help">{_say(translate, T_DB_USER_EXISTS, user=answers.user)}</p>')
            fields.append(_field(translate(T_DB_EXISTING_USER_PASSWORD),
                                 name("existing_user_password"), "", None, type_="password"))
            fields.append(f'<label class="switch"><input type="checkbox" name="{name("reset_user")}">'
                          f'<span>{escape(translate(T_DB_USER_EXISTS_RESET))}</span></label>')
    else:
        fields.append(_field(translate(T_DB_DATABASE), name("database"), answers.database,
                             translate(T_DB_DATABASE_HELP)))
        fields.append(_field(translate(T_DB_USER), name("user"), answers.user,
                             translate(T_DB_USER_HELP)))
        fields.append(_field(translate(T_DB_PASSWORD), name("password"), "",
                             translate(T_DB_SECRET_HELP), type_="password",
                             required=not bot_password))
        if bot_password:
            fields.append(f'<p class="note">{escape(translate(T_DB_KEPT))}</p>')

    test_label = translate(T_DB_TEST_CREATE if answers.path == db_step.CREATE
                           else T_TEST_CONNECTION)
    busy = _busy_attr(translate, T_DB_TEST_CREATE_BUSY if answers.path == db_step.CREATE
                      else T_DB_TEST_EXISTING_BUSY)
    return (f'{choice}<div class="fields">{"".join(fields)}</div>{notice}'
            f'<div class="actions"><button type="submit" name="action" value="{action_test}"{busy}>'
            f'{escape(test_label)}</button></div>')


def _resubmit_on_change(selector: str, action: str) -> str:
    """A SCOPED progressive enhancement: changing one control re-posts the form with *action*.

    Each block gets its own listener on its own selector (``input[name="path"]`` for the local
    block, ``input[name="cluster_path"]`` for the cluster one), so a change in one block never fires
    for the other. Without scripting the change is still saved, on the next Test / Continue.
    """
    return ('<script>(function(){var els=document.querySelectorAll(\'' + selector + '\');'
            'for(var i=0;i<els.length;i++){els[i].addEventListener("change",function(){'
            'var f=this.form;if(!f)return;'
            'var old=f.querySelector(\'input[name="action"]\');if(old)old.remove();'
            'var a=document.createElement("input");a.type="hidden";a.name="action";'
            'a.value="' + action + '";f.appendChild(a);'
            'var s=document.createElement("input");s.type="hidden";s.name="stay";s.value="1";'
            'f.appendChild(s);f.submit();});}})();</script>')


def _busy_submit_script() -> str:
    """The client-side busy state: the pressed submit says what it is doing and the form locks.

    A FORM THAT HOLDS A ``data-busy`` BUTTON is enhanced. On submit the pressed button's text is
    replaced by its ``data-busy`` value (already translated, see :func:`_busy_attr`), the button is
    marked ``aria-busy``, EVERY submit control of the form is disabled and a second submit event is
    refused — so a double press cannot put two requests on the wire.

    THE PRESSED CONTROL IS MIRRORED BEFORE IT IS DISABLED. A disabled submitter is dropped from the
    form data set, so its ``name``/``value`` (the ``action`` the handler routes on) is copied into a
    hidden input first; without the mirror disabling the button would silently lose the action.

    Nothing is faked: the label names the request in flight, there is no percentage and no bar. With
    scripting off the script never runs and the form is exactly the served one — the browser's own
    in-flight state is all there is, which is the honest no-JS behaviour.
    """
    return (
        '<script>(function(){'
        'var forms=document.querySelectorAll("form");'
        'function mirror(f,b){if(!b||!b.name)return;'
        'var h=document.createElement("input");h.type="hidden";h.name=b.name;h.value=b.value;'
        'f.appendChild(h);}'
        'function arm(f){f.dataset.busy="1";'
        'var c=f.querySelectorAll("button[type=submit],input[type=submit]");'
        'for(var i=0;i<c.length;i++)c[i].disabled=true;}'
        'for(var i=0;i<forms.length;i++){(function(f){'
        'if(!f.querySelector("button[data-busy]"))return;'
        'f.addEventListener("submit",function(e){'
        'if(f.dataset.busy){e.preventDefault();return;}'
        'var b=e.submitter||null;'
        'if(b&&b.dataset&&b.dataset.busy){b.textContent=b.dataset.busy;'
        'b.setAttribute("aria-busy","true");}'
        'mirror(f,b);arm(f);});})(forms[i]);}'
        '})();</script>')


def _step_three(request: Request, draft: SetupDraft, translate) -> str:
    """Step 3 — Database: one card, the two-path radio choice, the per-path fields and Test.

    THE INSTALL NUDGE IS THE CARD'S FIRST LINE, and only while the local PostgreSQL port is not a
    proven open (see :func:`_prepare_detection` and ``setup.database.local_postgres_open``): an
    operator with no server on this machine reads where to get one before anything else. The port is
    only ever probed on loopback, so a server on another HOST leaves it closed and the sentence reads
    as a nudge — the fields below still take any host.

    The path decides which block is shown and the Test button's label. Continue is disabled until
    the CURRENT field values have passed a test (the fingerprint in the draft), so a green light
    earned by other values can never be carried over.

    Below the first block, and before Continue, sits the OPTIONAL second block: a ``Federation
    support`` checkbox (default OFF) that reveals the cluster database's own copy of the SAME
    machinery. With it off the screen is exactly today's and nothing extra is written; while it is
    ticked, BOTH blocks must have passed a test before Continue opens.
    """
    token = session.get_csrf_token(request)
    store = db_step.secret_store(request.app)
    answers = db_step.answers_from_mapping(draft.database)
    master_password = store.get(db_step.MASTER_PASSWORD_KEY, "")
    bot_password = store.get(db_step.BOT_PASSWORD_KEY, "")
    local_signature = db_step.signature(answers, master_password, bot_password)
    # the gate is the draft's fingerprint AND a Test that passed in THIS process: a signature restored
    # from a draft cannot reopen it, so a restarted console must test again.
    enabled = (db_step.verification(draft.database, app=request.app)
               and db_step.tested_here(request.app, local_signature))

    # Federation is a FIRST-RUN question: this installation either has its cluster database already
    # or has none, and the joining flow never writes main.yaml — so the block is not asked here.
    joining = _mode(request) == boot_module.JOIN
    federation = (not joining) and bool(draft.database.get(db_step.FEDERATION_KEY))
    cluster_block = draft.database.get(db_step.CLUSTER_KEY)
    cluster_answers = db_step.answers_from_mapping(cluster_block,
                                                   secret_key=db_step.CLUSTER_SECRET_KEY)
    cluster_store = db_step.secret_store(request.app, db_step.CLUSTER_SECRET_KEY)
    cluster_signature = db_step.signature(cluster_answers,
                                          cluster_store.get(db_step.MASTER_PASSWORD_KEY, ""),
                                          cluster_store.get(db_step.BOT_PASSWORD_KEY, ""))
    cluster_enabled = (db_step.verification(cluster_block, secret_key=db_step.CLUSTER_SECRET_KEY,
                                            app=request.app)
                       and db_step.tested_here(request.app, cluster_signature,
                                               secret_key=db_step.CLUSTER_SECRET_KEY))

    local_block = _db_block(
        translate, answers, master_password=master_password, bot_password=bot_password,
        enabled=enabled, last=_db_last(draft.database), legend=T_DB_CHOICE, prefix="",
        action_test="test")

    federation_row = ""
    if not joining:
        federation_row = (
            f'<label class="switch fed"><input type="checkbox" name="federation"'
            f'{" checked" if federation else ""}>'
            f'<span>{escape(translate(T_FEDERATION))}'
            f'<span class="help">{escape(translate(T_FEDERATION_HELP))}</span></span></label>')

    cluster_html = ""
    if federation:
        cluster_html = ('<div class="cluster">' + _db_block(
            translate, cluster_answers,
            master_password=cluster_store.get(db_step.MASTER_PASSWORD_KEY, ""),
            bot_password=cluster_store.get(db_step.BOT_PASSWORD_KEY, ""), enabled=cluster_enabled,
            last=_cluster_last(cluster_block, answers, cluster_answers, enabled=cluster_enabled),
            legend=T_CLUSTER_LEGEND, prefix="cluster_",
            action_test="cluster_test") + '</div>')

    # Continue opens only when every TESTED block verifies — the cluster block counted while ticked
    enabled_all = enabled and (cluster_enabled if federation else True)
    back = _previous_step(draft, 3, _mode(request))
    back_link = (f'<a class="link" href="{STEP_PATH}?n={back}">'
                 f'{escape(translate(T_BACK))}</a>' if back else '<span class="grow"></span>')
    disabled = "" if enabled_all else " disabled"
    reason = "" if enabled_all else f' title="{escape(translate(T_CONTINUE_NEEDS_TEST))}"'
    # the WHY, visible: a `title` on a disabled control is not reliably shown, so the sentence stands
    # on the step. A blocked Continue already states it as its own notice, so it is not repeated here.
    hint = ""
    if not enabled_all and _db_last(draft.database).get("code") != "continue_blocked":
        hint = (f'<p class="note" role="note">{escape(translate(T_CONTINUE_NEEDS_TEST))}</p>')
    scripts = [_resubmit_on_change('input[name="path"]', "switch")]
    if not joining:
        scripts.append(_resubmit_on_change('input[name="federation"]', "federation"))
    if federation:
        scripts.append(_resubmit_on_change('input[name="cluster_path"]', "cluster_switch"))
    scripts.append(_busy_submit_script())
    # The install nudge sits at the VERY TOP of the card, and only while the local port is not a
    # PROVEN open: an open port (a server already there) skips it, a refused or unanswered port shows
    # it — a missing link costs an install, an extra nudge costs nothing. A PostgreSQL on another HOST
    # leaves this port closed, so the sentence is a nudge (the fields below still accept any host) and
    # not a block; it is drawn ONCE per page, above BOTH blocks, so the cluster block never repeats it.
    install = ""
    if db_step.cached_local_postgres(request.app) is not True:
        install = ('<p class="note">'
                   + _sentence_with_link(translate, T_DB_INSTALL, POSTGRESQL_DOWNLOAD_URL,
                                         T_DB_DOWNLOAD_HERE)
                   + '</p>')
    return (
        f'<form method="post" action="{STEP_PATH}">'
        f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        f'<input type="hidden" name="n" value="3">'
        f'{install}'
        f'{local_block}'
        f'{federation_row}'
        f'{cluster_html}'
        f'{hint}'
        f'<div class="actions">{back_link}'
        f'<button type="submit" name="action" value="continue"{disabled}{reason}'
        f'{_busy_attr(translate, T_CONTINUE_BUSY)}>'
        f'{escape(translate(T_CONTINUE))}</button></div>'
        f'</form>{"".join(scripts)}')


def _node_choice(answers: node_step.NodeAnswers, detection: node_step.Detection) -> str | None:
    """The DCS radio row that is marked, or ``None`` when the whole DCS block is declined.

    The STORED choice wins: it is the row the operator actually picked, so ``"other"`` stays marked
    (and its path field stays revealed) before a path has been typed, and a re-render never drops the
    pick. A draft written before the choice was stored falls back to the derivation below — a
    candidate's index when the stored path is one of them, ``"other"`` for a typed path, and the
    FIRST detected installation (index ``0``) when candidates exist and nothing has been chosen yet:
    the machine's own detection is the default, so the list is never left unanswered. Without a
    single detected candidate the typed-path row is marked.
    """
    if answers.no_dcs:
        return None
    if answers.dcs_choice is not None:
        return answers.dcs_choice
    for index, (_name, path) in enumerate(detection.candidates):
        if path == answers.dcs_installation:
            return str(index)
    if answers.dcs_installation:
        return "other"
    return "0" if detection.candidates else "other"


def _radio_row(name: str, value: str, label: str, help_text: str | None, checked: bool) -> str:
    """One radio option row (the same ``label.opt`` vocabulary step 3 uses for its path choice)."""
    mark = ' checked' if checked else ''
    help_html = f'<span class="help">{escape(help_text)}</span>' if help_text else ''
    return (f'<label class="opt"><input type="radio" name="{name}" value="{value}"{mark}>'
            f'<span><b>{escape(label)}</b>{help_html}</span></label>')


def suggested_listen_port(config_dir) -> int:
    """The port the node step offers: the installer's own formula over the WHOLE cluster.

    ``nodes.yaml`` is cluster-wide, so the nodes already in it are the starting point — the CLI
    derives its port as ``max(existing listen_ports) + 1`` (falling back to ``10042`` with no nodes).
    Only a bound-port probe would be blind to another node's port, so the suggestion is seeded from
    the file, then advanced past any port actually bound on this host. A node block that is not a
    mapping carries no port and is skipped.
    """
    existing = [node for node in join_step.existing_nodes(config_dir).values()
                if isinstance(node, dict)]
    return node_step.next_free_listen_port(existing)


def _step_four(request: Request, draft: SetupDraft, translate) -> str:
    """Step 4 — This node: DCS World installation, its auto-update, listen port, static IP and the
    DGSA opt-ins.

    The DCS choices come from the machine's own detection (never re-detected here) and the FIRST
    detected installation is preselected while nothing has been chosen yet; the decline checkbox
    removes the whole DCS block, exactly as the installer's ``get_dcs_installation`` offers. The
    static-IP switch is drawn only when an address was actually detected, the auto-update switch
    only once a DCS installation is configured (the installer's ``if dcs_installation:``), and the
    DGSA block carries the installer's own explanatory paragraph.
    """
    token = session.get_csrf_token(request)
    detection = node_step.cached_detection(request.app)
    answers = node_step.answers_from_mapping(draft.node)
    last = draft.node.get("last")
    last = last if isinstance(last, dict) else {}
    code = last.get("code")
    notice = ""
    if code in NODE_NOTICE:
        notice = (f'<p class="notice bad" role="alert">'
                  f'{_say(translate, NODE_NOTICE[code], **dict(last.get("data") or {}))}</p>')

    choice = _node_choice(answers, detection)
    blocks: list[str] = []

    # the DCS World installation: the detected candidates, the typed-path row, and the decline box
    dcs_help = f'<span class="help">{escape(translate(T_NODE_DCS_HELP))}</span>'
    if answers.no_dcs:
        dcs_rows = ""
    else:
        rows = [_radio_row("dcs_choice", str(index), name, path, choice == str(index))
                for index, (name, path) in enumerate(detection.candidates)]
        rows.append(_radio_row("dcs_choice", "other", translate(T_NODE_DCS_OTHER), None,
                               choice == "other"))
        dcs_rows = "".join(rows)
    blocks.append(
        f'<fieldset class="choice"><legend class="choice-legend">'
        f'{escape(translate(T_NODE_DCS_LABEL))}</legend>'
        f'<p class="note">{dcs_help}</p>{dcs_rows}</fieldset>')
    if not answers.no_dcs and choice == "other":
        blocks.append(_field(translate(T_NODE_DCS_PATH), "dcs_path",
                             answers.dcs_installation or ""))
    declined = ' checked' if answers.no_dcs else ''
    blocks.append(
        f'<label class="switch"><input type="checkbox" name="no_dcs"{declined}>'
        f'<span>{escape(translate(T_NODE_DCS_DECLINE))}</span></label>')

    # the DCS auto-update question: the installer asks it only under ``if dcs_installation:``, so
    # it is drawn only once an installation is configured — a chosen path, or a detected candidate
    if not answers.no_dcs and (answers.dcs_installation or detection.candidates):
        marked = ' checked' if answers.dcs_autoupdate else ''
        blocks.append(
            f'<label class="switch"><input type="checkbox" name="dcs_autoupdate"{marked}>'
            f'<span>{escape(translate(T_NODE_DCS_AUTOUPDATE))}</span></label>')

    # the listen port, prefilled with the derived next free port over the whole cluster
    stored_port = "listen_port" in (draft.node or {})
    port = answers.listen_port if stored_port else suggested_listen_port(_config_dir(request))
    blocks.append(_field(translate(T_NODE_LISTEN_PORT), "listen_port", port,
                         translate(T_NODE_LISTEN_PORT_HELP), type_="number", required=True))

    # the static-IP switch, only when an address was actually detected
    if detection.public_ip:
        marked = ' checked' if answers.public_ip == detection.public_ip else ''
        blocks.append(
            f'<label class="switch"><input type="checkbox" name="static_ip"{marked}>'
            f'<span>{_say(translate, T_NODE_STATIC_IP, ip=detection.public_ip)}</span></label>')

    # the DGSA banlist block, with the installer's own paragraph and its own two defaults.
    # NOT asked while joining: the opt-ins live in plugins/cloud.yaml, which belongs to the whole
    # installation and is never written by this flow.
    if _mode(request) != boot_module.JOIN:
        dgsa = [f'<p class="note">{escape(translate(T_NODE_DGSA_INTRO))}</p>']
        dcs_marked = ' checked' if answers.dgsa_dcs else ''
        dgsa.append(f'<label class="switch"><input type="checkbox" name="dgsa_dcs"{dcs_marked}>'
                    f'<span>{escape(translate(T_NODE_DGSA_DCS))}</span></label>')
        if draft.discord:
            discord_marked = ' checked' if answers.dgsa_discord else ''
            dgsa.append(
                f'<label class="switch"><input type="checkbox" name="dgsa_discord"{discord_marked}>'
                f'<span>{escape(translate(T_NODE_DGSA_DISCORD))}</span></label>')
        blocks.append("".join(dgsa))

    back = _previous_step(draft, 4, _mode(request))
    back_link = (f'<a class="link" href="{STEP_PATH}?n={back}">'
                 f'{escape(translate(T_BACK))}</a>' if back else '<span class="grow"></span>')
    # the one progressive enhancement: toggling the DCS choice or the decline box re-renders the
    # block it swaps, a moment sooner. Without scripting the answer is still saved, on Continue.
    script = ('<script>(function(){var c=document.querySelector(\'input[name="no_dcs"]\');'
              'var rs=document.querySelectorAll(\'input[name="dcs_choice"]\');'
              'function go(){var f=(c&&c.form)||(rs[0]&&rs[0].form);if(!f)return;'
              'var s=document.createElement("input");s.type="hidden";s.name="stay";s.value="1";'
              'f.appendChild(s);f.submit();}'
              'if(c)c.addEventListener("change",go);'
              'for(var i=0;i<rs.length;i++){rs[i].addEventListener("change",go);}})();</script>')
    return (
        f'<form method="post" action="{STEP_PATH}">'
        f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        f'<input type="hidden" name="n" value="4">'
        f'{"".join(blocks)}'
        f'{notice}'
        f'<div class="actions">{back_link}'
        f'<button type="submit">{escape(translate(T_CONTINUE))}</button></div>'
        f'</form>{script}')


def _stat(label: str, value: str) -> str:
    """A read-only fact drawn in the same grid as a field (the derived bot port, the instance id)."""
    return (f'<span class="stat"><span class="lbl">{escape(label)}</span>'
            f'<span class="val">{escape(value)}</span></span>')


#: The collapse chevron — the SAME SVG the config editor's ``st-block`` summary carries (see
#: ``templates/_editor.html``), so a tour row and an editor block turn their marker the same way.
_CHEV = ('<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
         'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
         '<path d="M9.4 5.4 16 12l-6.6 6.6"/></svg>')


def _channel_fields(translate, index: int, row: servers_step.ServerRow, guild, *,
                    central_admin: bool, channels: bool) -> str:
    """A row's channel line — the three pickers (or the installer's typed fields), or "".

    ``channels`` says whether the channel ids are asked at all (see :func:`_servers_discord`); with a
    CENTRAL admin channel (``central_admin``) the per-server admin field is not rendered at all.
    """
    if not channels:
        return ""
    controls = [_channel_control(translate(T_SRV_STATUS), f"row_status_{index}", row.status,
                                 guild, translate),
                _channel_control(translate(T_SRV_CHAT), f"row_chat_{index}", row.chat,
                                 guild, translate)]
    if not central_admin:
        controls.append(_channel_control(translate(T_SRV_ADMIN), f"row_admin_{index}",
                                         row.admin, guild, translate))
    return f'<div class="fields">{"".join(controls)}</div>'


def _server_row(translate, index: int, row: servers_step.ServerRow, port: int | None, guild, *,
                central_admin: bool = False, channels: bool = True) -> str:
    """One detected instance's row, drawn as the shared collapsible ``<details class="st-block">``.

    The ``<summary>`` is the row's header: the chevron, the server name and the manage switch — plus,
    while the row is NOT managed, the one-line handover sentence. The ``.bd`` is the edit block: the
    instance, the name, the derived port, autostart and — on the Discord path — the channel pickers
    (real ``<select>`` when the guild was read).

    *channels* is the caller's decision, and it is the SAME decision on both flows: the channel ids
    are asked whenever this installation talks to Discord. On a first run that is step 2's answer; on
    a joining run it is the EXISTING installation's own Discord state (see
    :func:`setup.join.discord_context`), because the joining flow asks no Discord question of its own.
    The pickers the rows offer are the first-run ones, on the same terms.

    A managed row renders ``open``, an unticked row renders COLLAPSED, so a re-render keeps the page
    where it was and a no-JS operator cannot tab into a field the row is not asking for.
    """
    manage = ' checked' if row.manage else ''
    autostart = ' checked' if row.autostart else ''
    opn = ' open' if row.manage else ''
    handover = '' if row.manage else f'<span class="kd">{escape(translate(T_SRV_HANDOVER))}</span>'
    fields = [_stat(translate(T_SRV_INSTANCE), row.instance),
              _field(translate(T_SRV_NAME), f"row_name_{index}", row.name),
              _stat(translate(T_SRV_BOT_PORT), str(port) if port is not None else "—")]
    return (
        f'<details class="st-block"{opn}>'
        f'<summary>{_CHEV}<span class="k">'
        f'{escape(row.name or row.detected_name or row.instance)}</span>{handover}'
        f'<span class="r"><label class="switch">'
        f'<input type="checkbox" name="row_manage_{index}"{manage}>'
        f'<span>{escape(translate(T_SRV_MANAGE))}</span></label></span></summary>'
        f'<div class="bd">'
        f'<div class="fields">{"".join(fields)}'
        f'<label class="switch"><input type="checkbox" name="row_autostart_{index}"{autostart}>'
        f'<span>{escape(translate(T_SRV_AUTOSTART))}</span></label></div>'
        f'{_channel_fields(translate, index, row, guild, central_admin=central_admin, channels=channels)}'
        f'</div></details>')


def _add_server_row(translate, index: int, row: servers_step.ServerRow, port: int | None, guild, *,
                    central_admin: bool = False, channels: bool = True) -> str:
    """The row a server is ADDED in — blank on an empty scan, and one per instance added already.

    A hand-added server has no detected folder name, so there is no read-only instance to show and no
    separate server name: ONE field carries both, which its label and its help line say. Everything
    else — the manage switch, the derived port, autostart, the channel line — is the detected row's
    own markup, and its values leave through the same builders (see ``setup.servers``).
    """
    manage = ' checked' if row.manage else ''
    autostart = ' checked' if row.autostart else ''
    opn = ' open' if row.manage else ''
    title = escape(row.name) if row.name else escape(translate(T_SRV_ADD))
    fields = [_field(translate(T_SRV_ADD_INSTANCE), f"row_name_{index}", row.name,
                     translate(T_SRV_ADD_INSTANCE_HELP)),
              _stat(translate(T_SRV_BOT_PORT), str(port) if port is not None else "—")]
    return (
        f'<details class="st-block"{opn}>'
        f'<summary>{_CHEV}<span class="k">{title}</span>'
        f'<span class="r"><label class="switch">'
        f'<input type="checkbox" name="row_manage_{index}"{manage}>'
        f'<span>{escape(translate(T_SRV_MANAGE))}</span></label></span></summary>'
        f'<div class="bd">'
        f'<div class="fields">{"".join(fields)}'
        f'<label class="switch"><input type="checkbox" name="row_autostart_{index}"{autostart}>'
        f'<span>{escape(translate(T_SRV_AUTOSTART))}</span></label></div>'
        f'{_channel_fields(translate, index, row, guild, central_admin=central_admin, channels=channels)}'
        f'</div></details>')


def _channels_intro(translate, *, admin: bool) -> str:
    """Step 8's channel guide, above the server rows.

    Prose in short paragraphs and a real ``<ul>`` with one ``<li>`` per line: the intro, the three
    channel roles and — unless the bot already holds Administrator — the permissions it needs. With
    *admin* the permission list is DROPPED entirely and one sentence says the bot already has every
    permission, so there is nothing to set by hand. A permission state that could not be read is not
    admin, so the list is printed (see :func:`setup.discord._bot_administrator`).
    """
    roles = "".join(f"<li>{escape(translate(msgid))}</li>"
                    for msgid in (T_SRV_CH_STATUS, T_SRV_CH_CHAT, T_SRV_CH_ADMIN))
    if admin:
        tail = f'<p>{escape(translate(T_SRV_PERMS_ADMIN))}</p>'
    else:
        perms = "".join(f"<li>{escape(translate(name))}</li>" for name in T_SRV_PERMS)
        tail = (f'<p>{escape(translate(T_SRV_PERMS_LEAD))}</p><ul>{perms}</ul>')
    return (f'<div class="note guide"><p>{escape(translate(T_SRV_CHANNELS_INTRO))}</p>'
            f'<ul>{roles}</ul>{tail}</div>')


def _servers_discord(request: Request, draft: SetupDraft) -> tuple[bool, bool]:
    """Step 8's two Discord decisions: ``(ask the channel line, suppress the per-server admin)``.

    A first run answers both from the tour's own steps — step 2 says whether this install talks to
    Discord at all, and step 6's admin scope says whether a per-server admin channel is offered. The
    joining flow asks neither of those questions, so it takes both from the EXISTING installation's
    shared config instead (:func:`setup.join.discord_context`): its ``no_discord`` flag, and whether
    ``services/bot.yaml`` already carries a central admin channel.
    """
    if _mode(request) == boot_module.JOIN:
        context = join_step.discord_context(_config_dir(request))
        return context.discord, context.central_admin
    if not draft.discord:
        return False, False
    return True, discord_step.roles_from_mapping(draft.roles).central_admin


def _step_servers(request: Request, draft: SetupDraft, translate) -> str:
    """Step 8 — DCS servers: one screen, one collapsible row per Saved Games instance.

    The rows come from the scan the draft carries (never re-detected here). A row is complete when it
    has a name and a status channel; the chat and admin channels are optional, and the whole channel
    line is absent on the no-Discord path. When the central admin channel was chosen (step 6) the
    per-server admin channel is not rendered at all. On the Discord path the channel fields are REAL
    pickers whenever the guild was read; otherwise they degrade to the installer's typed ids and the
    step says why (see :func:`_guild_notice`). The step is deliberately minimal: it records WHICH
    servers the bot runs and where their channels are — extensions and per-server settings belong to
    the console's server page.

    AN EMPTY SCAN IS A SCREEN THAT ASKS, not one that goes away: the rows are empty, so ONE BLANK ROW
    is offered after the note (see :func:`_add_server_row`), and the channel line and its guide are
    the same ones a detected row gets. A row the operator added earlier is rendered the same way —
    with the instance name editable, because there is no detected folder to show.

    THE SAME SCREEN IS SERVED WHILE JOINING. That flow asks no Discord question of its own, so both
    the channel line and the admin scope come from the existing installation (see
    :func:`_servers_discord`) — the rows, their fields and their help text are the first-run ones.
    """
    token = session.get_csrf_token(request)
    answers = servers_step.answers_from_mapping(draft.servers)
    # the Discord context decides whether the channel fields are asked and whether a row offers a
    # per-server admin channel (see :func:`_servers_discord`)
    channels, central_admin = _servers_discord(request, draft)
    guild, code = _guild_context(request, draft)
    last = draft.servers.get("last")
    last = last if isinstance(last, dict) else {}
    notice = ""
    if last.get("code") in SERVER_NOTICE:
        notice = (f'<p class="notice bad" role="alert">'
                  f'{_say(translate, SERVER_NOTICE[last["code"]], **dict(last.get("data") or {}))}</p>')

    blocks: list[str] = []
    if not answers.rows:
        blocks.append(f'<p class="note">{escape(translate(T_SRV_NONE))}</p>')
    else:
        header = [escape(translate(T_SRV_SEARCHING)),
                  _say(translate, T_SRV_FOUND, count=len(answers.rows))]
        blocks.append(f'<p class="note">{" ".join(header)}</p>')
    if channels:
        admin = bool(guild is not None and guild.bot_is_admin)
        blocks.append(_channels_intro(translate, admin=admin))
        blocks.append(_guild_notice(request, draft, translate, code))
    # the derived ports start above every bot_port the install's own nodes.yaml already carries, so
    # an added instance cannot take one another instance holds (see servers.bot_port_base)
    base = _bot_port_base(request)
    ports = servers_step.bot_ports(answers, base=base)
    added = set(answers.added)
    for index, row in enumerate(answers.rows):
        render = _add_server_row if row.instance in added else _server_row
        blocks.append(render(translate, index, row, ports.get(row.instance), guild,
                             central_admin=central_admin, channels=channels))
    if not answers.detected:
        # the blank row is the row AFTER the managed ones, so the port it would take is the one the
        # formula would give it — a prediction of a derived value, never an answer of its own
        blocks.append(_add_server_row(translate, len(answers.rows),
                                      servers_step.ServerRow(instance=""),
                                      base + len(servers_step.managed_rows(answers)), guild,
                                      central_admin=central_admin, channels=channels))

    back = _previous_step(draft, 8, _mode(request))
    back_link = (f'<a class="link" href="{STEP_PATH}?n={back}">'
                 f'{escape(translate(T_BACK))}</a>' if back else '<span class="grow"></span>')
    # the one progressive enhancement: switching a row's manage/autostart answer re-renders the
    # rows (a managed row's port is derived, so the ones below it change). Without scripting the
    # answer is still saved, on Continue.
    script = ('<script>(function(){var rs=document.querySelectorAll('
              '\'input[name^="row_manage_"],input[name^="row_autostart_"]\');'
              'for(var i=0;i<rs.length;i++){rs[i].addEventListener("change",function(){'
              'var f=this.form;if(!f)return;'
              'var s=document.createElement("input");s.type="hidden";s.name="stay";s.value="1";'
              'f.appendChild(s);f.submit();});}})();</script>')
    return (
        f'<form method="post" action="{STEP_PATH}">'
        f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        f'<input type="hidden" name="n" value="8">'
        f'{"".join(blocks)}'
        f'{notice}'
        f'<div class="actions">{back_link}'
        f'<button type="submit">{escape(translate(T_CONTINUE))}</button></div>'
        f'</form>{script}')


def _role_input(name: str, value: str) -> str:
    """One value field of a role/UCID row (repeated names read back with ``getlist``)."""
    return f'<input type="text" name="{name}" value="{escape(value)}">'


def _role_row(label: str, meaning: str, name: str, values, translate, *, add: bool,
              guild=None) -> str:
    """One internal-role row: the role word (mono, NEVER translated — it is the key in the config),
    its meaning in one line, and the value control.

    With *guild* (a read guild) the control is a real multiple ``<select>`` of the guild's roles — it
    renders and submits with NO JavaScript, and ``add`` is unused because a multi-select already
    carries several values. Without it the control is the installer's own repeated field plus the
    ``+ Add`` control (each value its own field, the comma form still accepted server-side).
    """
    if guild is not None:
        control = f'<div class="orlist">{_role_select(name, values, guild)}</div>'
    else:
        inputs = "".join(_role_input(name, str(value)) for value in values)
        button = ""
        if add:
            button = (f'<button type="button" class="add" data-name="{name}">'
                      f'{escape(translate(T_ROLES_ADD))}</button>')
        control = f'<div class="orlist">{inputs}{button}</div>'
    mode = (f'<div class="role-meaning">{escape(translate(T_PICK_ROLE))}</div>'
            if guild is not None else "")
    return (f'<div class="role-row"><div class="role-name">{escape(label)}</div>'
            f'<div class="role-meaning">{meaning}</div>'
            f'{mode}{control}</div>')


def _add_row_script() -> str:
    """``+ Add`` appends another value field to its own row. Nothing else needs scripting."""
    return ('<script>(function(){var bs=document.querySelectorAll("button.add");'
            'for(var i=0;i<bs.length;i++){bs[i].addEventListener("click",function(){'
            'var f=document.createElement("input");f.type="text";f.name=this.dataset.name;'
            'this.parentNode.insertBefore(f,this);f.focus();});}})();</script>')


def _console_base_url(request: Request, answers) -> str:
    """The public base URL the step prefills: the stored one, else the detected public address."""
    if answers.public_base_url:
        return answers.public_base_url
    detection = node_step.cached_detection(request.app)
    port = review_step.console_port(request.app)
    if detection.public_ip:
        return f"http://{detection.public_ip}:{port}"
    return f"http://localhost:{port}"


def _step_console(request: Request, draft: SetupDraft, translate) -> str:
    """Step 7 — the Web console: whether the console runs at all, the address it is reached at, and the
    account that always opens it.

    The switch is the whole decision: with it OFF the console-less install is written (a minimal
    ``services/webservice.yaml`` with ``frontend: false``, so enabling it later is one key) and nothing
    else is asked. With it ON the break-glass account is MANDATORY — never "Discord only".

    THE DISCORD CLIENT SECRET IS NOT ASKED HERE ANY MORE. It belongs with the other Discord
    credentials, on step 5 (:func:`_step_discord_bot`); the console's Discord login is configured from
    the secret collected there, and the no-Discord path (where no Discord page exists) has none at
    all. This step keeps its own questions — the switch, the address and the account.
    """
    token = session.get_csrf_token(request)
    answers = console_step.answers_from_mapping(draft.console)
    last = draft.console.get("last")
    last = last if isinstance(last, dict) else {}
    notice = ""
    if last.get("code") in CONSOLE_NOTICE:
        notice = (f'<p class="notice bad" role="alert">'
                  f'{_say(translate, CONSOLE_NOTICE[last["code"]], **dict(last.get("data") or {}))}'
                  f'</p>')

    switch = ' checked' if answers.enabled else ''
    block: list[str] = []
    if answers.enabled:
        block.append(
            f'<div class="fields">'
            f'{_field(translate(T_CONSOLE_ADDRESS), "console_public_base_url", _console_base_url(request, answers), translate(T_CONSOLE_ADDRESS_HELP))}'
            f'</div>')
        password_help = translate(T_CONSOLE_PASSWORD_KEPT) if answers.password_hash else None
        block.append(
            f'<fieldset class="choice"><legend class="choice-legend">'
            f'{escape(translate(T_CONSOLE_TITLE))}</legend>'
            f'<p class="note">{escape(translate(T_CONSOLE_HELP))}</p>'
            f'<div class="fields">'
            f'{_field(translate(T_CONSOLE_USERNAME), "console_username", answers.username, required=True)}'
            f'{_field(translate(T_CONSOLE_PASSWORD), "console_password", "", password_help, type_="password", required=not answers.password_hash)}'
            f'</div></fieldset>')
    else:
        block.append(f'<p class="note">{escape(translate(T_CONSOLE_OFF_NOTE))}</p>')

    back = _previous_step(draft, 7)
    back_link = (f'<a class="link" href="{STEP_PATH}?n={back}">'
                 f'{escape(translate(T_BACK))}</a>' if back else '<span class="grow"></span>')
    # the one progressive enhancement: the switch re-renders the block it swaps a moment sooner.
    # Without scripting the answer is still saved, on Continue.
    script = ('<script>(function(){var c=document.querySelector(\'input[name="console_enabled"]\');'
              'if(!c)return;c.addEventListener("change",function(){var f=c.form;if(!f)return;'
              'var s=document.createElement("input");s.type="hidden";s.name="stay";s.value="1";'
              'f.appendChild(s);f.submit();});})();</script>')
    return (
        f'<form method="post" action="{STEP_PATH}">'
        f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        f'<input type="hidden" name="n" value="7">'
        f'<label class="switch"><input type="checkbox" name="console_enabled"{switch}>'
        f'<span>{escape(translate(T_CONSOLE_SWITCH))}'
        f'<span class="help">{escape(translate(T_CONSOLE_SWITCH_HELP))}</span></span></label>'
        f'{"".join(block)}{notice}'
        f'<div class="actions">{back_link}'
        f'<button type="submit">{escape(translate(T_CONTINUE))}</button></div>'
        f'</form>{script}')


def _token_help(translate) -> str:
    """The token's help line: where in the portal the token is reset.

    The portal itself is the FIRST line of the page (:func:`_step_discord_bot`), so this line only
    carries the path inside it — the link is not repeated here.
    """
    return escape(translate(T_DISCORD_TOKEN_HELP))


def _step_discord_bot(request: Request, draft: SetupDraft, translate) -> str:
    """Step 5 — Discord, the bot: the guild id, the OWNER, the TOKEN, the console's client secret and
    the member-count switch.

    THE PAGE STARTS with where the values come from: the Developer Portal sentence, its link on the
    portal's own name. The token field's help then only names the path INSIDE the portal — the link
    lives at the top, once.

    LAYOUT: one two-column credential grid, in DOM order Guild ID, Owner ID, TOKEN, client secret —
    line 1 the guild id and the owner, line 2 the token with the console's client secret below it.
    The skip note under the grid says what an empty secret means. The client secret is asked for on
    THIS step and never on the Web-console step (which keeps its own switch, address and account);
    the no-Discord path has no Discord page at all and so asks for neither a client secret nor a
    Discord login — see :func:`_step_community`.

    The TOKEN is drawn as a password-style field and never rendered back; when one is held for this
    process's lifetime the field is optional and a note says so (``setup.discord``). The step's own
    Test READS the guild (channels + roles) for the pickers the following steps offer, and Continue
    is offered only once that read has SUCCEEDED for these values in THIS process — a restart holds
    no read, and the token is never in the draft, so it must be re-entered and read again. That
    SUCCESS is stated (:data:`T_DISCORD_CONNECTED`, naming the guild the read found), so a working
    connection reads as one rather than as the mere absence of a failure guide. The member-count
    switch carries the installer's consequence sentence when it is on: a guild with more than 10000
    members cannot hold the Server Members Intent, so the bot needs "View Audit Log".

    A FAILED read renders a GUIDE, not a raw code (see :func:`_guild_notice`): an invalid token names
    where the token comes from and how to reset it; a bot in NO server gets a generated invite link
    (clickable and plain text); a bot in MORE THAN ONE gets the list of where it is, kick instructions,
    and the chooser that makes the bot leave the extras (:func:`_leave_chooser`).
    """
    token = session.get_csrf_token(request)
    answers = discord_step.answers_from_mapping(draft.discord_bot)
    held_token = discord_step.token_store(request.app).get(discord_step.TOKEN_KEY)
    held = bool(held_token)
    secret_held = bool(discord_step.client_secret_store(request.app).get(discord_step.CLIENT_SECRET_KEY))
    last = draft.discord_bot.get("last")
    last = last if isinstance(last, dict) else {}
    # the gate: the guild read for THESE values must have happened, and succeeded, in THIS process.
    fetch_code = discord_step.fetch_status(request.app, held_token, answers.guild_id)
    fetched = fetch_code == ""
    notice = ""
    if last.get("code") in DISCORD_NOTICE:
        notice = (f'<p class="notice bad" role="alert">'
                  f'{_say(translate, DISCORD_NOTICE[last["code"]], **dict(last.get("data") or {}))}'
                  f'</p>')
    # the console's Discord OAuth2 login: the client secret is asked for HERE, with the rest of the
    # Discord credentials, and never on the Web-console step. It renders EMPTY and never comes back;
    # a held one shows only its note. Written only when the console is on (see ``setup.review``).
    secret_help = (translate(T_DISCORD_CLIENT_SECRET_KEPT) if secret_held
                   else translate(T_DISCORD_CLIENT_SECRET_HELP))
    # ONE grid, in the order the step asks for the values: Guild ID and Owner ID on the first line,
    # the TOKEN with the client secret below it on the second. The NO-JS layout is this grid's own
    # (the stylesheet collapses it to one column on a narrow screen); nothing here depends on script.
    fields = [
        _field(translate(T_DISCORD_GUILD_ID), "guild_id",
               "" if answers.guild_id is None else answers.guild_id,
               translate(T_DISCORD_GUILD_ID_HELP), required=True),
        _field(translate(T_DISCORD_OWNER), "owner",
               "" if answers.owner is None else answers.owner,
               translate(T_DISCORD_OWNER_HELP), required=True),
        _field(translate(T_DISCORD_TOKEN), "token", "",
               translate(T_DISCORD_TOKEN_KEPT) if held else None,
               type_="password", required=not held,
               help_markup=None if held else _token_help(translate)),
        _field(translate(T_DISCORD_CLIENT_SECRET), "console_client_secret", "", secret_help,
               type_="password"),
    ]
    secret_note = f'<p class="note">{escape(translate(T_DISCORD_CLIENT_SECRET_SKIP))}</p>'
    members = ' checked' if not answers.privileged_intents else ''
    hidden = '' if not answers.privileged_intents else ' hidden'
    switch = (f'<label class="switch"><input type="checkbox" name="more_than_members"{members}>'
              f'<span>{escape(translate(T_DISCORD_MEMBERS))}</span></label>'
              f'<p class="note" id="members-note"{hidden}>'
              f'{escape(translate(T_DISCORD_MEMBERS_NOTE))}</p>')
    # the POSITIVE half of the step's own Test: a read that SUCCEEDED for these values in THIS
    # process (the same fact the gate reads) names the guild it found, so a working connection is
    # stated. The name is the read's own answer, never the draft's.
    connected = ""
    if fetched:
        read = discord_step.cached_guild(request.app, held_token, answers.guild_id)
        if read is not None:
            label = (_guild_name((read.guild_id, read.guild_name)) if read.guild_name
                     else str(read.guild_id))
            connected = (f'<p class="notice ok" role="status">'
                         f'{_say(translate, T_DISCORD_CONNECTED, guild=label)}</p>')
    back = _previous_step(draft, 5)
    back_link = (f'<a class="link" href="{STEP_PATH}?n={back}">'
                 f'{escape(translate(T_BACK))}</a>' if back else '<span class="grow"></span>')
    # the WHY, visible: the sentence stands on the step (a `title` on a disabled control is not
    # reliably shown). A blocked Continue already states it as its own notice, so it is not repeated.
    hint = ""
    if not fetched and last.get("code") != "continue_blocked":
        hint = f'<p class="note" role="note">{escape(translate(T_CONTINUE_NEEDS_TEST))}</p>'
    disabled = "" if fetched else " disabled"
    reason = "" if fetched else f' title="{escape(translate(T_CONTINUE_NEEDS_TEST))}"'
    # the guide for a FAILED read (invalid token / no server / too many servers), the chooser that can
    # make the bot leave the extra servers, and the per-guild outcome of a leave this tour performed.
    guide = _guild_notice(request, draft, translate, fetch_code or "",
                          chooser_below=(fetch_code == "discord_multiple_guilds"))
    chooser = _leave_chooser(request, draft, translate) \
        if fetch_code == "discord_multiple_guilds" else ""
    leave_result = _leave_result(draft, translate)
    # the one progressive enhancement: the consequence sentence follows the switch a moment sooner
    script = ('<script>(function(){var c=document.querySelector('
              '\'input[name="more_than_members"]\');var n=document.getElementById("members-note");'
              'if(!c||!n)return;c.addEventListener("change",function(){n.hidden=!c.checked;});'
              '})();</script>')
    # the step's FIRST line: where the four values come from. The link is not repeated in the token
    # field's own help (see :func:`_token_help`).
    portal_intro = ('<p class="note">'
                    + _sentence_with_link(translate, T_DISCORD_PORTAL_INTRO, DISCORD_PORTAL_URL,
                                          T_DISCORD_PORTAL)
                    + '</p>')
    return (
        f'<form method="post" action="{STEP_PATH}">'
        f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        f'<input type="hidden" name="n" value="5">'
        f'{portal_intro}'
        f'<div class="fields">{"".join(fields)}</div>'
        f'{secret_note}'
        f'{switch}{notice}{connected}'
        f'{guide}{chooser}{leave_result}'
        f'<div class="actions"><button type="submit" name="action" value="test"'
        f'{_busy_attr(translate, T_DISCORD_FETCH_BUSY)}>'
        f'{escape(translate(T_TEST_CONNECTION))}</button></div>'
        f'{hint}'
        f'<div class="actions">{back_link}'
        f'<button type="submit" name="action" value="continue"{disabled}{reason}'
        f'{_busy_attr(translate, T_CONTINUE_BUSY)}>'
        f'{escape(translate(T_CONTINUE))}</button></div>'
        f'</form>{script}{_busy_submit_script()}')


def _step_roles(request: Request, draft: SetupDraft, translate) -> str:
    """Step 6 — Discord, roles and channels: the three-row role mapping, the admin-channel question
    and the optional channels.

    THE ADMIN-CHANNEL QUESTION COMES BEFORE ANY CHANNEL CONTROL. It is answered HERE, BEFORE any
    per-server row can read it (step 8), and it is the FIRST channel-scoped control on the page: the
    audit channel and — only while the central answer is in force — the central admin channel follow
    it. On the per-server answer the admin-channel field is ABSENT from the document (not
    disabled-but-present), because the admin channel then belongs to the server rows.

    The role rows and the two channel fields are REAL pickers whenever the guild was read (see
    :func:`_guild_context`); otherwise they degrade to the installer's typed values and the step says
    why. The console account is NOT collected here any more — it lives on the Web-console step.
    """
    token = session.get_csrf_token(request)
    answers = discord_step.roles_from_mapping(draft.roles)
    guild, fetch_code = _guild_context(request, draft)
    last = draft.roles.get("last")
    last = last if isinstance(last, dict) else {}
    notice = ""
    if last.get("code") in ROLES_NOTICE:
        notice = (f'<p class="notice bad" role="alert">'
                  f'{_say(translate, ROLES_NOTICE[last["code"]], **dict(last.get("data") or {}))}'
                  f'</p>')
    rows = []
    for index, name in enumerate(discord_step.DEFAULT_ROLE_NAMES):
        meaning = escape(translate(T_ROLE_MEANINGS.get(name, "")))
        rows.append(_role_row(name, meaning, f"role_{index}",
                              answers.roles.get(name, ()), translate, add=True, guild=guild))
    per = ' checked' if not answers.central_admin else ''
    central = ' checked' if answers.central_admin else ''
    admin_choice = (
        f'<fieldset class="choice admin-choice"><legend class="choice-legend">'
        f'<b>{escape(translate(T_DISCORD_ADMIN))}</b>'
        f'{escape(translate(T_DISCORD_ADMIN_INTRO))}</legend>'
        f'<label class="opt"><input type="radio" name="admin_scope" value="per_server"{per}>'
        f'<span><b>{escape(translate(T_DISCORD_ADMIN_PER_SERVER))}</b></span></label>'
        f'<label class="opt"><input type="radio" name="admin_scope" value="central"{central}>'
        f'<span><b>{escape(translate(T_DISCORD_ADMIN_CENTRAL))}</b></span></label>'
        f'</fieldset>')
    # the central admin channel is asked for ONLY while the central choice is in force: on the
    # per-server choice the admin channel belongs to the server rows (step 8), so this field is
    # ABSENT from the document (not disabled-but-present) and a no-JS operator cannot fill it in.
    controls = [_channel_control(translate(T_DISCORD_AUDIT), "audit_channel", answers.audit_channel,
                                 guild, translate, translate(T_DISCORD_AUDIT_HELP))]
    if answers.central_admin:
        controls.append(_channel_control(translate(T_DISCORD_ADMIN), "admin_channel",
                                         answers.admin_channel, guild, translate,
                                         translate(T_DISCORD_ADMIN_HELP)))
    channels = f'<div class="fields">{"".join(controls)}</div>'
    back = _previous_step(draft, 6)
    back_link = (f'<a class="link" href="{STEP_PATH}?n={back}">'
                 f'{escape(translate(T_BACK))}</a>' if back else '<span class="grow"></span>')
    # the one progressive enhancement: changing the admin-channel answer re-renders the step, so the
    # admin channel field appears with the central choice and is GONE with the per-server one. Without
    # scripting the answer is still saved on Continue, and the served state already matches the
    # stored answer.
    script = ('<script>(function(){var rs=document.querySelectorAll(\'input[name="admin_scope"]\');'
              'for(var i=0;i<rs.length;i++){rs[i].addEventListener("change",function(){'
              'var f=this.form;if(!f)return;'
              'var s=document.createElement("input");s.type="hidden";s.name="stay";s.value="1";'
              'f.appendChild(s);f.submit();});}})();</script>')
    return (
        f'<form method="post" action="{STEP_PATH}">'
        f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        f'<input type="hidden" name="n" value="6">'
        f'<p class="note">{escape(translate(T_ROLES_INTRO))}</p>'
        f'{"".join(rows)}'
        f'<p class="note">{escape(translate(T_ROLES_DEFAULTS))}</p>'
        f'{admin_choice}{channels}'
        f'{_guild_notice(request, draft, translate, fetch_code)}{notice}'
        f'<div class="actions">{back_link}'
        f'<button type="submit">{escape(translate(T_CONTINUE))}</button></div>'
        f'</form>{_add_row_script()}{script}')


def _step_community(request: Request, draft: SetupDraft, translate) -> str:
    """Step 5' — "Your community": the DCS group's name and the users of the two internal roles.

    The no-Discord answer, and the row that replaces steps 5 and 6 in the rail when Discord support
    is off. Each role's UCIDs are one comma-separated field — the installer's own form, and the one a
    browser without scripting can still fill in. The console account is NOT collected here: it lives on
    the Web-console step, which is on the rail on both paths. NEITHER IS A DISCORD CLIENT SECRET: this
    path has no Discord at all — no bot, no OAuth2 login — so nothing Discord-shaped is asked for
    anywhere on it, and the console signs the operator in with the break-glass account alone.
    """
    token = session.get_csrf_token(request)
    answers = community_step.answers_from_mapping(draft.community)
    last = draft.community.get("last")
    last = last if isinstance(last, dict) else {}
    notice = ""
    if last.get("code") in COMMUNITY_NOTICE:
        notice = (f'<p class="notice bad" role="alert">'
                  f'{_say(translate, COMMUNITY_NOTICE[last["code"]], **dict(last.get("data") or {}))}'
                  f'</p>')
    rows = []
    for index, name in enumerate(community_step.DEFAULT_ROLES):
        meaning = escape(translate(T_COMMUNITY_MEANINGS.get(name, "")))
        values = answers.users.get(name, ())
        rows.append(_role_row(name, meaning, f"user_{index}", [", ".join(values)],
                              translate, add=False))
    back = _previous_step(draft, 5)
    back_link = (f'<a class="link" href="{STEP_PATH}?n={back}">'
                 f'{escape(translate(T_BACK))}</a>' if back else '<span class="grow"></span>')
    return (
        f'<form method="post" action="{STEP_PATH}">'
        f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        f'<input type="hidden" name="n" value="5">'
        f'<div class="fields">'
        f'{_field(translate(T_COMMUNITY_NAME), "guild_name", answers.guild_name, required=True)}'
        f'</div>'
        f'<p class="note">{escape(translate(T_COMMUNITY_INTRO))}</p>'
        f'{"".join(rows)}'
        f'<p class="note">{escape(translate(T_COMMUNITY_UCIDS))}</p>'
        f'{notice}'
        f'<div class="actions">{back_link}'
        f'<button type="submit">{escape(translate(T_CONTINUE))}</button></div>'
        f'</form>')


def _review_path(target: review_step.Target) -> str:
    """The path the Review screen shows for a target: ``config/<relative path>``."""
    return f"{review_step.CONFIG_PREFIX}/{target.relpath}"


def _already_done(request: Request, draft: SetupDraft, translate) -> list[str]:
    """The "already done" list: the side effects the earlier steps really performed.

    Only facts, and only the ones a step actually caused: the database step's two paths differ here
    (``CREATE`` made the database, its user and stored the master password; ``EXISTING`` verified a
    database it did not change).
    """
    answers = db_step.answers_from_mapping(draft.database)
    if answers.is_create:
        return [escape(translate(T_REVIEW_DB_CREATED)),
                escape(translate(T_REVIEW_MASTER_STORED))]
    return [escape(translate(T_REVIEW_DB_VERIFIED))]


def _main_detail(draft: SetupDraft, *, app) -> str:
    """``main.yaml``'s line on the Review screen.

    Today's three keys, plus the cluster database when the write gate holds (the box is ticked and
    the block verifies) — and, when the cluster block names the SAME database as this node's own,
    the honest statement that this is no federation at all.
    """
    if not review_step.cluster_written(draft, app=app):
        return T_REVIEW_FILES["main"]
    if db_step.same_database(db_step.answers_from_mapping(draft.database),
                             review_step.cluster_answers(draft)):
        return T_REVIEW_MAIN_CLUSTER_SAME
    return T_REVIEW_MAIN_CLUSTER


def _will_write(request: Request, draft: SetupDraft, translate) -> list[str]:
    """The "will be written" list, taken from the commit itself (``setup.review.plan``).

    Rendering the plan rather than a hand-kept list is what keeps the promise and the write the same
    set: a target the commit will not write cannot appear here.
    """
    lines: list[str] = []
    for item in review_step.plan(draft, app=request.app, mode=_mode(request)):
        if item.target is review_step.MAIN:
            detail = _main_detail(draft, app=request.app)
        else:
            detail = T_REVIEW_FILES.get(item.target.key, "")
        lines.append(
            f'<li><span class="path">{escape(_review_path(item.target))}</span>'
            f'<span class="state">{escape(translate(detail)) if detail else ""}</span></li>')
    return lines


def _not_written(draft: SetupDraft, app, translate, mode: str = boot_module.INSTALL) -> list[str]:
    """The answers the commit will NOT write: the blocks the earlier decisions left out.

    Named only when a block actually holds answers, so the list never claims something was dropped
    that was never given. The cluster database is named whenever the operator gave one but the
    commit will not write it (Federation support is off, or its own Test never passed). The console's
    Discord login is named when Discord is on, the console is enabled and no client secret is held in
    this process — the secret is process-memory only, so a mid-tour restart drops it and the login
    silently stays off.

    JOINING NAMES NOTHING: this flow asks no Discord, console or cluster question at all, so there is
    no answer of those it could be leaving out.
    """
    if mode == boot_module.JOIN:
        return []
    lines: list[str] = []
    if draft.discord:
        if draft.community:
            lines.append(escape(translate(T_REVIEW_COMMUNITY_NOT_WRITTEN)))
        if (console_step.answers_from_mapping(draft.console).enabled
                and not discord_step.client_secret_store(app).get(discord_step.CLIENT_SECRET_KEY)):
            lines.append(escape(translate(T_REVIEW_CONSOLE_LOGIN_NOT_WRITTEN)))
    elif draft.discord_bot or draft.roles:
        lines.append(escape(translate(T_REVIEW_DISCORD_NOT_WRITTEN)))
    if db_step.CLUSTER_KEY in draft.database and not review_step.cluster_written(draft, app=app):
        # two states: the box off, or on but the block has not (or no longer) verified
        detail = (T_REVIEW_CLUSTER_NOT_WRITTEN if not review_step.federation_on(draft)
                  else T_REVIEW_CLUSTER_NOT_VERIFIED)
        lines.append(escape(translate(detail)))
    return lines


def _review_list(title: str, lines: list[str]) -> str:
    """One of the Review screen's lists (a heading and its items; an empty list renders nothing)."""
    if not lines:
        return ""
    return (f'<div class="review"><h2>{escape(title)}</h2>'
            f'<ul>{"".join(lines)}</ul></div>')


def _review_write_form(token: str, label: str) -> str:
    """The commit's one button, as its own form (the screen carries no other input)."""
    return (f'<form method="post" action="{STEP_PATH}">'
            f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
            f'<input type="hidden" name="n" value="9">'
            f'<div class="actions"><span class="grow"></span>'
            f'<button type="submit" name="action" value="write">{escape(label)}</button>'
            f'</div></form>')


def _review_results(token: str, results: list, translate) -> str:
    """The screen after a write attempt: one row per target, and the honest half-written notice.

    A row is green only when that target reached the disk; a refused target carries the primitive's
    own reason, and a target that was never attempted (``main.yaml``, held back because something
    before it was refused) says that in words.
    """
    rows: list[str] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        target = TARGET_BY_KEY.get(str(entry.get("key")))
        if target is None:
            continue
        ok = bool(entry.get("ok"))
        state = translate(T_REVIEW_WRITTEN) if ok else translate(T_REVIEW_NOT_WRITTEN_ITEM)
        if entry.get("skipped"):
            reason = translate(T_REVIEW_SKIPPED)
        else:
            reason = str(entry.get("error") or "").strip() or translate(T_REVIEW_NO_REASON)
        detail = (f'<span class="state">{escape(reason)}</span>'
                  if not ok and reason else "")
        rows.append(f'<li class="{"ok" if ok else "bad"}">'
                    f'<span class="path">{escape(_review_path(target))}</span>'
                    f'<span class="state">{escape(state)}</span>{detail}</li>')
    notice = (f'<p class="notice bad" role="alert">{escape(translate(T_REVIEW_HALF))}</p>')
    return (notice + _review_list(translate(T_REVIEW_RESULT), rows)
            + _review_write_form(token, translate(T_REVIEW_WRITE_AGAIN)))


def _review_refused(reason: str, back_link: str) -> str:
    """The Review screen when the commit itself cannot be built.

    The joining plan raises (:class:`review.JoinNodeDatabaseError`) rather than writing a node that
    would inherit the federation's database, so the screen shows the classified reason and NO commit
    button — the plan cannot be rendered as a "will be written" list when it refuses. The reason names
    the missing answer, and the Back link returns to the step that collects it.
    """
    notice = f'<p class="notice bad" role="alert">{escape(reason)}</p>'
    return notice + f'<div class="actions">{back_link}</div>'


def _review_missing(request: Request, draft: SetupDraft, missing: list, back_link: str,
                    translate) -> str:
    """The Review screen when a required secret is not held in THIS process.

    NOTHING can be committed in this state: a write here would close the setup window with a
    configuration the bot cannot sign in with (no Discord token) or reach its database with (no
    password). The screen names the step to re-enter for each missing value (``review.SECRET_STEPS``)
    and still shows what IS held — the plan's own list — so nothing is hidden and no commit is
    offered.
    """
    reasons = {review_step.TOKEN_SECRET.key: T_REVIEW_MISSING_TOKEN,
               review_step.DATABASE_SECRET.key: T_REVIEW_MISSING_DATABASE,
               review_step.CLUSTER_SECRET.key: T_REVIEW_MISSING_CLUSTER_DATABASE}
    lines = [f'<li class="bad"><span class="path">{escape(_review_path(target))}</span>'
             f'<span class="state">{escape(translate(reasons.get(target.key, "")))}</span></li>'
             for target in missing]
    notice = f'<p class="notice bad" role="alert">{escape(translate(T_REVIEW_INCOMPLETE))}</p>'
    return (notice
            + _review_list(translate(T_REVIEW_WILL_WRITE), _will_write(request, draft, translate))
            + _review_list(translate(T_REVIEW_MISSING), lines)
            + f'<div class="actions">{back_link}</div>')


def _step_nine(request: Request, draft: SetupDraft, translate) -> str:
    """Step 9 — Review and write: two lists, no input, and the one commit button.

    Before the write the screen states what is already done and what will be written; after a
    REFUSED write it swaps to the per-target results (see :func:`_review_results`), which is the
    only honest thing to show when only part of the configuration reached the disk.

    The step is GATED on the required secrets being held in this process (``review.missing_secrets``):
    a mid-tour restart drops the in-memory token and, on the existing-database path, the password —
    writing then would close the window without them, so the screen refuses and names the step to
    re-enter instead of offering a commit that must not happen.
    """
    token = session.get_csrf_token(request)
    back = _previous_step(draft, 9, _mode(request))
    back_link = (f'<a class="link" href="{STEP_PATH}?n={back}">'
                 f'{escape(translate(T_BACK))}</a>' if back else '<span class="grow"></span>')
    # a joining commit that cannot be built is refused BEFORE the commit button: the plan would raise
    # for a federated node with no database of its own, so the reason is shown and no write is offered
    if _mode(request) == boot_module.JOIN:
        refusal = review_step.join_database_refusal(draft, app=request.app)
        if refusal:
            return _review_refused(refusal, back_link)
    missing = review_step.missing_secrets(draft, app=request.app, mode=_mode(request))
    if missing:
        return _review_missing(request, draft, missing, back_link, translate)
    results = draft.review.get("results")
    if isinstance(results, list) and results and not draft.review.get("written"):
        return _review_results(token, results, translate)
    return (
        _review_list(translate(T_REVIEW_ALREADY), _already_done(request, draft, translate))
        + _review_list(translate(T_REVIEW_WILL_WRITE), _will_write(request, draft, translate))
        + _review_list(translate(T_REVIEW_NOT_WRITTEN),
                       _not_written(draft, request.app, translate, _mode(request)))
        + f'<form method="post" action="{STEP_PATH}">'
        + f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        + f'<input type="hidden" name="n" value="9">'
        + f'<div class="actions">{back_link}'
        + f'<button type="submit" name="action" value="write">'
        + f'{escape(translate(T_REVIEW_WRITE))}</button></div></form>')


def _console_enabled(config_dir) -> bool:
    """Whether the console was ENABLED by the write, read from the file that was just written.

    The Done screen is reached both right after the commit (where the draft is already discarded) and
    on a later reload, so it reads the fact from ``services/webservice.yaml`` rather than from any
    in-memory state: the file's ``DEFAULT.frontend`` is what the console itself checks at boot.
    """
    path = Path(config_dir) / review_step.WEBSERVICE.relpath
    try:
        document = configwrite.load_config_file(path)
    except Exception:  # noqa: BLE001 - an unreadable file is simply "not enabled"
        return False
    default = document.get("DEFAULT") if isinstance(document, dict) else None
    return bool(isinstance(default, dict) and default.get("frontend"))


def _join_document(request: Request, draft: SetupDraft, *, notice: str = "") -> str:
    """The joining state's FIRST screen: the add/rename question, and nothing else.

    It carries NO rail: which of the two flows this is decides the rail, so there is nothing to show
    before the answer. Both routes are ONE form and two submit controls (no scripting: the rename
    route reads the select and the confirmation beside it, the add route ignores both), because the
    operator may press either without the page having to know which they meant.
    """
    lang = language_step.prefill_language(request, draft)
    theme = theme_module.resolve_theme(request)
    translate = i18n.translations_for(lang).gettext
    token = session.get_csrf_token(request)
    node = _node_name(request) or "this host"
    names = sorted(join_step.existing_nodes(_config_dir(request)))
    options = "".join(f'<option value="{escape(name)}">{escape(name)}</option>' for name in names)
    body = (
        f'<p class="note">{_say(translate, T_JOIN_INTRO, node=node)}</p>'
        f'{notice}'
        f'<form method="post" action="{STEP_PATH}">'
        f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        f'<div class="join-choices">'
        f'<section class="join-choice"><h2 class="join-head">{escape(translate(T_JOIN_NEW))}</h2>'
        f'<p class="join-help">{escape(translate(T_JOIN_NEW_HELP))}</p>'
        f'<div class="actions"><button type="submit" name="action" '
        f'value="{JOIN_NEW_ACTION}">{escape(translate(T_CONTINUE))}</button></div></section>'
        f'<section class="join-choice"><h2 class="join-head">{escape(translate(T_JOIN_RENAME))}</h2>'
        f'<p class="join-help">{_say(translate, T_JOIN_RENAME_HELP, node=node)}</p>'
        f'<div class="fields">'
        f'<label class="field" for="join_old_node">'
        f'<span class="lbl">{escape(translate(T_JOIN_OLD))}</span>'
        f'<select id="join_old_node" name="join_old_node">{options}</select></label>'
        f'</div>'
        f'<label class="switch"><input type="checkbox" name="join_confirm">'
        f'<span>{_say(translate, T_JOIN_CONFIRM, node=node)}</span></label>'
        f'<div class="actions"><button type="submit" name="action" '
        f'value="{JOIN_RENAME_ACTION}">{escape(translate(T_JOIN_RENAME))}</button></div>'
        f'</section></div></form>')
    return (
        "<!doctype html>\n"
        f'<html lang="{escape(lang)}" data-theme="{escape(theme)}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>DCSServerBot setup</title>\n'
        f'{_the_style()}\n</head>\n<body>\n{_top_bar(request, lang, theme, translate)}\n'
        f'<main class="panel"><section class="card">'
        f'<div class="hd"><h1>{escape(translate(T_JOIN_TITLE))}</h1></div>'
        f'<div class="bd">{body}</div></section></main>\n</body>\n</html>\n')


def _submit_join_route(request: Request, draft: SetupDraft, form, config_dir) -> Response:
    """The add/rename answer: continue into the tour, or rename and END the flow.

    RENAME IS THE INSTALLER'S OWN CALL. :func:`services.webservice.setup.join.rename_node` hands the
    pair to ``Install.rename_node``, which rewrites the config yamls key by key and migrates the
    database — so the console owns no second implementation of it, and nothing is written here that
    the command line installer would not write. The flow ends on the spot: the renamed node's block
    is what closes the window.
    """
    translate = _translator(request, draft)
    node = _node_name(request)
    if str(form.get("action")) == JOIN_NEW_ACTION:
        join_step.set_route(draft, join_step.ROUTE_NEW)
        draft.save(config_dir)
        return RedirectResponse(STEP_PATH, status_code=303)

    def refused(msgid) -> HTMLResponse:
        return HTMLResponse(_join_document(request, draft,
                                          notice=f'<p class="notice bad" role="alert">'
                                                 f'{escape(translate(msgid))}</p>'))

    old = str(form.get("join_old_node") or "").strip()
    if not old or old not in join_step.existing_nodes(config_dir):
        return refused(T_JOIN_OLD_NEEDED)
    if form.get("join_confirm") is None:
        return refused(T_JOIN_CONFIRM_NEEDED)
    try:
        join_step.rename_node(config_dir, old, node)
    except Exception as ex:  # noqa: BLE001 - the operator gets a sentence, the log the reason
        log.warning("Setup join: node '%s' could not be renamed to '%s' (%s).", old, node, ex,
                    exc_info=True)
        return refused(T_JOIN_FAILED)
    log.info("Setup join: node '%s' was renamed to '%s' through the installer's own helper.",
             old, node)
    _discard_draft(config_dir)
    response = HTMLResponse(_done_document(request, draft, renamed=(old, node)))
    finish = getattr(request.app.state, "setup_finish", None)
    if callable(finish):
        response.background = BackgroundTask(finish)
    return response


def _done_document(request: Request, draft: SetupDraft,
                   renamed: tuple[str, str] | None = None) -> str:
    """The terminal screen: the configuration is written and the console is handing over.

    It stays the tour's own document — the admin shell's banner, pills and environment bar are
    derived from live state that does not exist until the bot has started on the NEW configuration,
    so rendering it here would open the first page with a false alarm.

    The console is mentioned only when it was actually enabled: a console-less install drops the link
    (and the sign-in line) and says so instead, because a button to a console that will never serve
    would be a lie.

    JOINING HAS ITS OWN TWO LINES. This install already has its console and its servers, so the
    handed-over lines ("the console will be at ...", "add your DCS servers") are both false here; what
    is true is that this node is now in ``nodes.yaml`` (or was renamed into it) and can be started.
    """
    lang = language_step.prefill_language(request, draft)
    theme = theme_module.resolve_theme(request)
    translate = i18n.translations_for(lang).gettext
    url = f"http://localhost:{review_step.console_port(request.app)}"
    script = "run.cmd" if sys.platform == "win32" else "run.sh"
    node = _node_name(request)
    buttons = ""
    if _mode(request) == boot_module.JOIN:
        if renamed is not None:
            sentence = _say(translate, T_JOIN_RENAMED, old=renamed[0], node=renamed[1])
        else:
            sentence = _say(translate, T_DONE_JOINED, node=node)
        lines = [f'<p class="note">{sentence}</p>',
                 f'<p class="note">{_say(translate, T_DONE_START, script=script)}</p>']
        return _standalone_document(request, lang, theme, translate, T_DONE_TITLE,
                                    "".join(lines))
    lines = [f'<p class="note">{escape(translate(T_DONE_WRITTEN))}</p>']
    if _console_enabled(_config_dir(request)):
        lines.append(f'<p class="note">{_say(translate, T_DONE_OPEN, url=url)}</p>')
        lines.append(f'<p class="note">{escape(translate(T_DONE_SIGNIN))}</p>')
        lines.append(f'<p class="note">{escape(translate(T_DONE_ADD_SERVERS))}</p>')
        buttons = (f'<div class="actions"><a class="button" href="{escape(url)}">'
                   f'{escape(translate(T_DONE_BUTTON))}</a></div>')
    else:
        lines.append(f'<p class="note">{escape(translate(T_DONE_NO_CONSOLE))}</p>')
        lines.append(f'<p class="note">{escape(translate(T_DONE_ADD_SERVERS_OFF))}</p>')
    lines.append(f'<p class="note">{_say(translate, T_DONE_START, script=script)}</p>')
    return _standalone_document(request, lang, theme, translate, T_DONE_TITLE,
                               "".join(lines) + buttons)


def _standalone_document(request: Request, lang: str, theme: str, translate, title: str,
                         body: str) -> str:
    """One of the tour's OWN documents, outside the rail (the Done screen, the joining question).

    It carries the top bar (brand, flags, theme) and the same stylesheet, and deliberately no rail:
    neither screen is a step of a walkable tour.
    """
    return (
        "<!doctype html>\n"
        f'<html lang="{escape(lang)}" data-theme="{escape(theme)}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>DCSServerBot setup</title>\n'
        f'{_the_style()}\n</head>\n<body>\n{_top_bar(request, lang, theme, translate)}\n'
        f'<main class="panel"><section class="card">'
        f'<div class="hd"><h1>{escape(translate(title))}</h1></div>'
        f'<div class="bd">{body}</div></section></main>\n</body>\n</html>\n')


def _placeholder(draft: SetupDraft, step: int, translate) -> str:
    """A step whose content is built later: its title, an honest note, and the way back."""
    back = _previous_step(draft, step)
    back_link = (f'<a class="link" href="{STEP_PATH}?n={back}">'
                 f'{escape(translate(T_BACK))}</a>' if back else "")
    return (f'<p class="note">{escape(translate(T_NOT_BUILT))}</p>'
            f'<div class="actions">{back_link}</div>')


def _card_body(request: Request, draft: SetupDraft, step: int, translate, *,
               stay: bool = False) -> str:
    if step == 1:
        return _step_one(request, draft, translate)
    if step == 2:
        return _step_two(request, draft, translate, stay=stay)
    if step == 3:
        return _step_three(request, draft, translate)
    if step == 4:
        return _step_four(request, draft, translate)
    if step == 5:
        return _step_discord_bot(request, draft, translate) if draft.discord \
            else _step_community(request, draft, translate)
    if step == 6:
        return _step_roles(request, draft, translate)
    if step == 7:
        return _step_console(request, draft, translate)
    if step == 8:
        return _step_servers(request, draft, translate)
    if step == 9:
        return _step_nine(request, draft, translate)
    return _placeholder(draft, step, translate)


# ----------------------------------------------------------------------------------- rendering

def _config_dir(request: Request) -> str:
    """The tour's config directory, recorded on the application at start (never re-derived)."""
    return getattr(request.app.state, "webui_config_dir", "config")


def _bot_port_base(request: Request) -> int:
    """The first bot port this step may assign: above every ``bot_port`` ``nodes.yaml`` already holds.

    Read from the INSTALLATION's own file, on every render and never from the draft — it is a machine
    fact like the instance scan, and the port the page shows must be the one the commit writes. An
    absent file (a first run), or one that cannot be read, answers the installer's own default, which
    is what the formula counted from before.
    """
    path = Path(_config_dir(request)) / "nodes.yaml"
    try:
        nodes = configwrite.load_config_file(path)
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup tour: '%s' could not be read (%s); assigning bot ports from the "
                    "installer's own first port.", path, ex)
        return servers_step.DEFAULT_BOT_PORT
    return servers_step.bot_port_base(nodes)


def _setup_finished(request: Request) -> bool:
    """Whether the setup window is closed FOR THIS CONSOLE — the boot rule, read here.

    The rule is the boot window's own (:func:`services.webservice.setup.boot.should_run_setup`), read
    so the tour shows the terminal screen instead of a step list the moment its own mode is done —
    even before the console stops.

    THE MODE DECIDES WHICH FACT THAT IS. A first-run console is finished when ``main.yaml`` exists; a
    joining one when this node's block is in ``nodes.yaml`` (which is what closes the window for it,
    and what makes the tour self-finishing the instant the merge lands). The node is passed ONLY in
    joining: a first-run console must never read a node block in a ``nodes.yaml`` that already has one
    as "the setup is over" — that file is exactly what it is there to write.
    """
    config_dir = _config_dir(request)
    if _mode(request) == boot_module.JOIN:
        return not boot_module.should_run_setup(config_dir, _node_name(request))
    return not boot_module.should_run_setup(config_dir)


def _draft_for(request: Request) -> SetupDraft:
    return SetupDraft.load(_config_dir(request))


async def _prepare_detection(request: Request, step: int | None) -> None:
    """Probe the machine once, before the step that needs it is rendered.

    Step 3 reads whether the LOCAL PostgreSQL port answers (the install nudge's own fact), and steps
    4/7 read detection (the DCS candidates, the public address). Both are cached on the application,
    so a re-render never probes twice and the socket/network calls are paid on those steps only.

    Step 8 WHILE JOINING also warms the guild read, so its channel pickers are real ``<select>``s
    instead of the typed-id degrade: the token and the guild id come from the existing installation's
    shared config (see :func:`setup.join.discord_context`), and the read is cached on
    ``(token, guild_id)``, so it is paid once per process and a failure degrades the same way it does
    on a first run (see :func:`_guild_notice`).
    """
    if step == 3:
        await db_step.local_postgres_open(request.app)
    if step in (4, 7):
        await node_step.detection_for(request.app)
    if step == 8 and _mode(request) == boot_module.JOIN:
        context = join_step.discord_context(_config_dir(request))
        if context.discord and context.token and context.guild_id is not None:
            await discord_step.fetch_guild_data(request.app, context.token, context.guild_id)


def _node_answered(draft: SetupDraft) -> bool:
    """Whether step 4 has been submitted (the node block is what it writes)."""
    return bool(draft.node)


def _rescan_servers(draft: SetupDraft, config_dir, *, force: bool = False) -> None:
    """(Re-)scan the machine for Saved Games instances and keep the result in the draft.

    The scan runs ONCE per draft — the rail's "DCS servers" row is derived from it, so it has to
    survive a reload and a restart, and the draft is where the tour's state lives. It is not run
    before step 4 has been answered (the row is the installer's default until then) and not run at
    all when the DCS installation was declined. A re-scan (``force``) keeps the answers of every
    instance the machine still holds.
    """
    if not has_dcs(draft) or not _node_answered(draft):
        return
    previous = servers_step.answers_from_mapping(draft.servers)
    if previous.scanned and not force:
        return
    document = servers_step.non_secret_document(previous)
    document["scanned"] = True
    answers = servers_step.answers_from_mapping(document, detected=servers_step.detect_instances())
    draft.servers = servers_step.non_secret_document(answers)
    draft.save(config_dir)


def _rescan_or_fail(request: Request, draft: SetupDraft,
                    mode: str = boot_module.INSTALL) -> HTMLResponse | None:
    """Run the once-per-tour server scan, or return the save-failure page when its draft write fails.

    The scan is the ONE draft write a GET can make (see :func:`_rescan_servers`); a failure here is
    the same failure a step submit can hit, and it renders the same classified notice instead of a
    raw 500.
    """
    try:
        _rescan_servers(draft, _config_dir(request))
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup tour: the draft at '%s' could not be written (%s); the scan result was "
                    "not persisted.", _config_dir(request), ex)
        return _save_failure_response(request, draft, current_step(draft, mode))
    return None


def _seed_or_fail(request: Request, draft: SetupDraft, mode: str) -> HTMLResponse | None:
    """Seed the joining draft's database pre-fill, or report a failed draft write.

    The pre-fill is the ONE draft write a GET makes in this mode, beside the server scan, and it runs
    before the step is rendered so step 3 opens with "Use an existing database" already chosen. A
    failure renders the same classified notice a step submit's failed write renders.

    It is INERT on a first run: that flow writes its own database block on step 3, and seeding there
    would put an answer in a draft nobody gave.
    """
    if mode != boot_module.JOIN:
        return None
    try:
        join_step.seed_draft(draft, _config_dir(request))
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup join: the draft at '%s' could not be written (%s); the database pre-fill "
                    "was not persisted.", _config_dir(request), ex)
        return _save_failure_response(request, draft, current_step(draft, mode))
    return None


async def render_root(request: Request) -> HTMLResponse:
    """``GET /setup`` once accepted: the tour at its current step, or the terminal Done screen.

    The Done screen is decided by the FILESYSTEM, not by the draft: the moment this console's mode is
    finished the setup is over, and a reload (before the console has stopped, or after) says so rather
    than re-offering a step.

    In the joining state the FIRST screen is the add/rename question and not a step: which of the two
    flows this is decides everything after it.
    """
    draft = _draft_for(request)
    if _setup_finished(request):
        return HTMLResponse(_done_document(request, draft))
    mode = _mode(request)
    if mode == boot_module.JOIN and join_step.route(draft) != join_step.ROUTE_NEW:
        return HTMLResponse(_join_document(request, draft))
    failure = _seed_or_fail(request, draft, mode)
    if failure is not None:
        return failure
    failure = _rescan_or_fail(request, draft, mode)
    if failure is not None:
        return failure
    step = current_step(draft, mode)
    await _prepare_detection(request, step)
    return HTMLResponse(_render(request, draft, step))


async def render_step(request: Request, requested) -> HTMLResponse:
    """``GET /setup/step?n=``: a step the operator may reach (a completed one, or the current one).

    A step in the future, one that is not on the rail, or a value that is not a step at all renders
    the CURRENT step instead — a future row is not a link, and a crafted ``n`` must not jump ahead.
    """
    draft = _draft_for(request)
    if _setup_finished(request):
        return HTMLResponse(_done_document(request, draft))
    mode = _mode(request)
    if mode == boot_module.JOIN and join_step.route(draft) != join_step.ROUTE_NEW:
        return HTMLResponse(_join_document(request, draft))
    failure = _seed_or_fail(request, draft, mode)
    if failure is not None:
        return failure
    failure = _rescan_or_fail(request, draft, mode)
    if failure is not None:
        return failure
    current = current_step(draft, mode)
    step = _as_reachable_step(requested, draft, current, mode)
    await _prepare_detection(request, step)
    return HTMLResponse(_render(request, draft, step))


def _as_reachable_step(requested, draft: SetupDraft, current: int | None,
                       mode: str = boot_module.INSTALL) -> int | None:
    rows = rail_rows(draft, mode)
    if current is None:
        return None
    try:
        number = int(requested)
    except (TypeError, ValueError):
        return current
    if number not in rows or number > current:
        return current
    return number


def _render(request: Request, draft: SetupDraft, step: int | None, *,
            stay: bool = False, notice: str = "") -> str:
    translate = _translator(request, draft)
    mode = _mode(request)
    if step is None:
        # Every rail row is done: the configuration is written, so the terminal screen is the only
        # honest state (a step list would offer a tour that no longer exists).
        return _done_document(request, draft)
    return _document(request, draft, title=step_label(step, draft.discord),
                     body=_card_body(request, draft, step, translate, stay=stay),
                     current=current_step(draft, mode), notice=notice)


def _translator(request: Request, draft: SetupDraft):
    return i18n.translations_for(language_step.prefill_language(request, draft)).gettext


# ---------------------------------------------------------------------------------- the writes

def _save_failure_notice(request: Request, draft: SetupDraft) -> str:
    """The classified notice a failed draft write renders on the step.

    The operator's own words, never the exception: the primitive's message names a path and would
    read as a crash, and the point is only that this step's answers were not kept.
    """
    return (f'<p class="notice bad" role="alert">'
            f'{escape(_translator(request, draft)(T_SAVE_FAILED))}</p>')


def _save_failure_response(request: Request, draft: SetupDraft, step: int | None) -> HTMLResponse:
    """Re-render *step* in place with the classified save-failure notice (the write did not happen)."""
    return HTMLResponse(_render(request, draft, step, stay=True,
                                notice=_save_failure_notice(request, draft)))


def _snapshot_state(draft: SetupDraft) -> dict:
    """The step-level state a submit sets in memory: the completed step and step 3's test verdicts.

    Captured before a submit so a FAILED draft write can put them back. Neither was persisted, so
    the re-render must not show a step the operator never reached, nor a verified database (the
    local one or the cluster one) whose fingerprint never reached the draft. The ANSWERS are
    deliberately not captured — they are the typed values a failed save keeps.
    """
    cluster = draft.database.get(db_step.CLUSTER_KEY)
    cluster = cluster if isinstance(cluster, dict) else {}
    return {"step": draft.step,
            "verified": draft.database.get("verified"),
            "signature": draft.database.get("signature"),
            "federation": draft.database.get(db_step.FEDERATION_KEY),
            "cluster_verified": cluster.get("verified"),
            "cluster_signature": cluster.get("signature")}


def _restore_state(draft: SetupDraft, state: dict) -> None:
    """Put the step-level state back exactly as it was (a missing key stays missing)."""
    draft.step = state["step"]
    for key in ("verified", "signature"):
        if state[key] is None:
            draft.database.pop(key, None)
        else:
            draft.database[key] = state[key]
    if state["federation"] is None:
        draft.database.pop(db_step.FEDERATION_KEY, None)
    else:
        draft.database[db_step.FEDERATION_KEY] = state["federation"]
    if db_step.CLUSTER_KEY in draft.database:
        block = dict(draft.database.get(db_step.CLUSTER_KEY) or {})
        for key, previous in (("verified", state["cluster_verified"]),
                              ("signature", state["cluster_signature"])):
            if previous is None:
                block.pop(key, None)
            else:
                block[key] = previous
        draft.database[db_step.CLUSTER_KEY] = block


async def submit_step(request: Request) -> Response:
    """``POST /setup/step``: save a step's answers, then move on (or re-render, on ``stay``).

    The CSRF token is checked by the route's own dependency, and the step is bounded to the one the
    operator may be on: a crafted ``n`` falls back to the current step's answers. The write goes
    through :meth:`SetupDraft.save` — the ONE config-write primitive — and never touches a real
    configuration file.

    A POST that arrives once the setup window is closed (``main.yaml`` exists) is answered with the
    terminal Done screen and NOTHING else: the tour state is spent, so the draft is never recreated
    and the tour cannot be re-walked by a replay.

    A draft write that FAILS (a :class:`~core.utils.configwrite.ConfigWriteError`, or the OSError a
    filesystem refusal raises) is caught here, at the one place every step's save passes through: it
    is turned into a classified notice on THIS step, the typed answers are kept, the step is not
    advanced and the draft on disk is left as it was. It never escapes as a raw 500.
    """
    draft = _draft_for(request)
    config_dir = getattr(request.app.state, "webui_config_dir", "config")
    mode = _mode(request)
    if _setup_finished(request):
        # The configuration is written and the tour state is spent. A POST replayed after the commit
        # (a stale form, a back button, an embedding that never stopped the console) must NOT
        # recreate the draft and re-open the tour — the same file check render_root and render_step
        # carry is enforced here, on the ONE write route.
        return HTMLResponse(_done_document(request, draft))
    current = current_step(draft, mode)
    form = await request.form()
    if mode == boot_module.JOIN and form.get("action") in JOIN_ACTIONS:
        # the joining flow's FIRST screen posts its own action, and it is not a step
        return _submit_join_route(request, draft, form, config_dir)
    step = _as_reachable_step(form.get("n"), draft, current, mode)
    if step is None:
        return RedirectResponse(STEP_PATH, status_code=303)
    stay = bool(form.get("stay"))
    state = _snapshot_state(draft)
    try:
        return await _dispatch_step(request, draft, step, form, config_dir, stay)
    except configwrite.ConfigWriteError as ex:
        _restore_state(draft, state)
        log.warning("Setup tour: the draft at '%s' could not be written (%s); the step keeps its "
                    "answers and nothing was persisted.", config_dir, ex)
        return _save_failure_response(request, draft, step)


async def _dispatch_step(request: Request, draft: SetupDraft, step: int, form, config_dir,
                         stay: bool) -> Response:
    """The step's own write path: its handler, or step 1/2's own save."""
    response: Response
    if step == 1:
        # The operator's language is the console cookie (set by the flag links, and re-stated here
        # so the choice sticks even when no flag was pressed). The bot's language is the switch.
        language = language_step.prefill_language(request, draft)
        draft.language = language
        draft.bot_language = form.get("bot_language") is not None
        if not stay:
            draft.step = max(draft.step, 1)
        draft.save(config_dir)
        response = RedirectResponse(f"{STEP_PATH}?n=2" if not stay else f"{STEP_PATH}?n=1",
                                    status_code=303)
        _remember_language(response, request, language)
        if not stay:
            return response
    elif step == 2:
        draft.autoupdate = form.get("autoupdate") is not None
        draft.discord = form.get("discord") is not None
        if not stay:
            draft.step = max(draft.step, 2)
        draft.save(config_dir)
        if not stay:
            return RedirectResponse(STEP_PATH, status_code=303)
    elif step == 3:
        return _submit_database(request, draft, form, config_dir)
    elif step == 4:
        await node_step.detection_for(request.app)
        return _submit_node(request, draft, form, config_dir, stay)
    elif step == 5:
        if draft.discord:
            return await _submit_discord_bot(request, draft, form, config_dir, stay)
        return _submit_community(request, draft, form, config_dir, stay)
    elif step == 6:
        return _submit_roles(request, draft, form, config_dir, stay)
    elif step == 7:
        return _submit_console(request, draft, form, config_dir, stay)
    elif step == 8:
        return _submit_servers(request, draft, form, config_dir, stay)
    elif step == 9:
        return _submit_review(request, draft, form, config_dir)

    # re-render the same step against the just-saved draft (the "stay" path, and step 1's own)
    refreshed = SetupDraft.load(config_dir)
    return HTMLResponse(_render(request, refreshed, step, stay=True))


def _submit_database(request: Request, draft: SetupDraft, form, config_dir) -> Response:
    """Step 3's one write path: save the answers, then Test / Continue / re-render.

    One form submission is one attempt — that is how the installer's 3-try password rules map onto
    a browser. The non-secret answers go to the draft (which keeps ``verified``/``signature``); the
    typed passwords go to the process store, never to the draft and never back into a field.

    ONE form carries BOTH blocks — the local one always, the optional cluster one while Federation
    support is ticked — so this one handler merges, persists and tests each with the SAME machinery.
    The action value names which block's Test was pressed; both blocks' typed values survive either.
    """
    store = db_step.secret_store(request.app)
    previous = db_step.answers_from_mapping(draft.database)

    def submitted(name: str, default):
        raw = form.get(name)
        return raw if raw not in (None, "") else default

    def merge(prefix: str, previous_block: db_step.DatabaseAnswers) -> dict:
        """The submitted values of ONE block, field-prefixed so the two blocks never collide."""
        return {
            "path": (form.get(f"{prefix}path")
                     if form.get(f"{prefix}path") in db_step.PATHS else previous_block.path),
            "host": submitted(f"{prefix}host", previous_block.host),
            "port": submitted(f"{prefix}port", previous_block.port),
            "master_database": submitted(f"{prefix}master_database", previous_block.master_database),
            "master_user": submitted(f"{prefix}master_user", previous_block.master_user),
            "database": submitted(f"{prefix}database", previous_block.database),
            "user": submitted(f"{prefix}user", previous_block.user),
            "user_password_attempts": previous_block.user_password_attempts,
        }

    answers = db_step.answers_from_mapping(merge("", previous))

    supplied_master = form.get("master_password")
    if supplied_master:
        store[db_step.MASTER_PASSWORD_KEY] = supplied_master
    master_password = store.get(db_step.MASTER_PASSWORD_KEY, "")

    # path A's existing-role password and path B's bot password share one slot
    supplied_bot = form.get("password") or form.get("existing_user_password")
    if supplied_bot:
        store[db_step.BOT_PASSWORD_KEY] = supplied_bot
    bot_password = store.get(db_step.BOT_PASSWORD_KEY, "")

    # ---- the optional cluster block, the same machinery in its own store ----
    federation = form.get("federation") is not None
    had_cluster = db_step.CLUSTER_KEY in draft.database
    cluster_store = db_step.secret_store(request.app, db_step.CLUSTER_SECRET_KEY)
    previous_cluster = db_step.answers_from_mapping(draft.database.get(db_step.CLUSTER_KEY),
                                                    secret_key=db_step.CLUSTER_SECRET_KEY)
    cluster_answers = db_step.answers_from_mapping(merge("cluster_", previous_cluster),
                                                   secret_key=db_step.CLUSTER_SECRET_KEY)

    supplied_cluster_master = form.get("cluster_master_password")
    if supplied_cluster_master:
        cluster_store[db_step.MASTER_PASSWORD_KEY] = supplied_cluster_master
    cluster_master_password = cluster_store.get(db_step.MASTER_PASSWORD_KEY, "")
    supplied_cluster_bot = (form.get("cluster_password")
                            or form.get("cluster_existing_user_password"))
    if supplied_cluster_bot:
        cluster_store[db_step.BOT_PASSWORD_KEY] = supplied_cluster_bot
    cluster_bot_password = cluster_store.get(db_step.BOT_PASSWORD_KEY, "")

    def cluster_signature() -> str:
        return db_step.signature(cluster_answers, cluster_master_password, cluster_bot_password)

    def cluster_block() -> dict:
        """The draft's cluster block as a MUTABLE dict, created when it is not there yet."""
        block = draft.database.get(db_step.CLUSTER_KEY)
        if not isinstance(block, dict):
            block = {}
            draft.database[db_step.CLUSTER_KEY] = block
        return block

    def persist() -> None:
        """Write both blocks' non-secret answers to the draft (a secret never reaches it).

        The cluster block (and the tick) appear in the draft ONLY once the box was ticked or the
        block already exists, so the default path's draft stays byte-identical to today's.
        """
        draft.database = dict(draft.database)
        draft.database.update(db_step.non_secret_document(answers))
        if federation or had_cluster:
            block = cluster_block()
            block.update(db_step.non_secret_document(cluster_answers))
        if federation:
            draft.database[db_step.FEDERATION_KEY] = True
        else:
            draft.database.pop(db_step.FEDERATION_KEY, None)
        draft.save(config_dir)

    action = form.get("action") or "switch"
    if action == "continue":
        signature = db_step.signature(answers, master_password, bot_password)
        # the fingerprint AND a Test that passed in THIS process: a signature restored from a draft
        # is not enough, so a restarted console (an empty store) must test again before it continues.
        local_ok = (bool(draft.database.get("verified"))
                    and draft.database.get("signature") == signature
                    and db_step.tested_here(request.app, signature))
        block = draft.database.get(db_step.CLUSTER_KEY) or {}
        cluster_ok = (not federation) or (bool(block.get("verified"))
                                          and block.get("signature") == cluster_signature()
                                          and db_step.tested_here(
                                              request.app, cluster_signature(),
                                              secret_key=db_step.CLUSTER_SECRET_KEY))
        if local_ok and cluster_ok:
            draft.step = max(draft.step, 3)
            draft.database["last"] = {"code": "ok_verified", "data": {}, "version": None,
                                      "version_outdated": False}
            persist()
            return RedirectResponse(STEP_PATH, status_code=303)
        draft.database["last"] = {"code": "continue_blocked", "data": {}, "version": None,
                                  "version_outdated": False}
        persist()
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 3))

    if action == "test":
        outcome = db_step.run_test(answers, master_password=master_password,
                                   bot_password=bot_password,
                                   reset_user=form.get("reset_user") is not None,
                                   config_dir=config_dir)
        if outcome.ok:
            draft.database["verified"] = True
            draft.database["signature"] = db_step.signature(answers, master_password, bot_password)
            answers.user_password_attempts = 0
        else:
            draft.database["verified"] = False
            draft.database.pop("signature", None)
            if outcome.code == "user_password_refused":
                answers.user_password_attempts = int(outcome.data.get("attempts") or 0)
        draft.database["last"] = {"code": outcome.code, "data": dict(outcome.data),
                                  "version": outcome.version,
                                  "version_outdated": outcome.version_outdated}
        persist()
        # the process proof is recorded only once the answers reached the draft, and only for a pass
        if outcome.ok:
            db_step.mark_tested(request.app, draft.database["signature"])
        else:
            db_step.clear_tested(request.app)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 3))

    if action == "cluster_test" and federation:
        outcome = db_step.run_test(cluster_answers, master_password=cluster_master_password,
                                   bot_password=cluster_bot_password,
                                   reset_user=form.get("cluster_reset_user") is not None,
                                   config_dir=config_dir)
        code = outcome.code
        data = dict(outcome.data)
        # the bot decides a federation by database NAME: an equal name is warned about, not blocked
        if outcome.ok and db_step.same_database(answers, cluster_answers):
            code = db_step.SAME_DATABASE_CODE
            data["database"] = cluster_answers.database
        block = cluster_block()
        if outcome.ok:
            block["verified"] = True
            block["signature"] = cluster_signature()
            cluster_answers.user_password_attempts = 0
        else:
            block["verified"] = False
            block.pop("signature", None)
            if outcome.code == "user_password_refused":
                cluster_answers.user_password_attempts = int(outcome.data.get("attempts") or 0)
        block["last"] = {"code": code, "data": data, "version": outcome.version,
                         "version_outdated": outcome.version_outdated}
        persist()
        if outcome.ok:
            db_step.mark_tested(request.app, block["signature"],
                                secret_key=db_step.CLUSTER_SECRET_KEY)
        else:
            db_step.clear_tested(request.app, secret_key=db_step.CLUSTER_SECRET_KEY)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 3))

    # a path switch (either block), the Federation tick, or a stray submit: save the values without
    # running anything, and re-close each gate whose fingerprint the values no longer match
    draft.database["verified"] = bool(draft.database.get("verified")) and (
        draft.database.get("signature") == db_step.signature(answers, master_password, bot_password))
    if not draft.database["verified"]:
        draft.database.pop("signature", None)
    if federation or had_cluster:
        block = cluster_block()
        block["verified"] = bool(block.get("verified")) and block.get("signature") == cluster_signature()
        if not block["verified"]:
            block.pop("signature", None)
    persist()
    return HTMLResponse(_render(request, SetupDraft.load(config_dir), 3))


def _resolve_node(form, detection: node_step.Detection, previous: node_step.NodeAnswers, *,
                  discord: bool, validate: bool):
    """Turn the submitted form into step 4's answers.

    With ``validate`` set, an invalid answer returns ``(None, (code, data))`` — the classified
    failure that keeps the step open. Without it the answers are best-effort, which is what the
    progressive-enhancement re-render uses so a mere switch never reports a false failure.
    """
    raw_port = form.get("listen_port")
    if raw_port in (None, ""):
        port = previous.listen_port
    else:
        port = node_step.parse_listen_port(raw_port)
        if port is None:
            if validate:
                return None, ("port_invalid", {})
            port = previous.listen_port

    no_dcs = form.get("no_dcs") is not None
    dcs_installation = None
    dcs_choice = None
    if not no_dcs:
        choice = form.get("dcs_choice")
        candidates = detection.candidates
        typed = str(form.get("dcs_path") or "").strip()
        if choice is None and candidates:
            if validate:
                return None, ("dcs_choice_required", {})
            dcs_choice = previous.dcs_choice
            dcs_installation = previous.dcs_installation
        elif choice is None or choice == "other" or not candidates:
            # the typed-path row: the choice is kept on its own, so the next render reveals (and
            # keeps) the path field even while no path has been typed yet
            dcs_choice = "other"
            if typed:
                expanded = os.path.expandvars(typed)
                if validate and not os.path.exists(expanded):
                    return None, ("dcs_path_missing", {"path": typed})
                dcs_installation = expanded
            elif validate:
                return None, ("dcs_path_required", {})
            else:
                dcs_installation = previous.dcs_installation
        else:
            index = int(choice) if str(choice).isdigit() else -1
            if 0 <= index < len(candidates):
                dcs_choice = str(index)
                dcs_installation = candidates[index][1]
            elif validate:
                return None, ("dcs_choice_required", {})
            else:
                dcs_choice = previous.dcs_choice
                dcs_installation = previous.dcs_installation

    if validate and not node_step.port_is_free(port):
        return None, ("port_busy", {"port": port})

    # the DCS auto-update box is drawn only once an installation is configured; when it was not
    # drawn its absence is not a "no", so the previous answer (the installer's default, on) stands
    dcs_configured = (not no_dcs) and (dcs_installation is not None or bool(detection.candidates))
    dcs_autoupdate = ((form.get("dcs_autoupdate") is not None) if dcs_configured
                      else previous.dcs_autoupdate)

    answers = node_step.NodeAnswers(
        listen_port=port,
        use_upnp=detection.upnp,
        no_dcs=no_dcs,
        dcs_choice=dcs_choice,
        dcs_installation=dcs_installation,
        dcs_autoupdate=dcs_autoupdate,
        public_ip=detection.public_ip if form.get("static_ip") is not None else None,
        dgsa_dcs=form.get("dgsa_dcs") is not None,
        dgsa_discord=(form.get("dgsa_discord") is not None) if discord else False,
    )
    return answers, None


def _submit_node(request: Request, draft: SetupDraft, form, config_dir, stay: bool) -> Response:
    """Step 4's one write path: save the answers, or re-render with the classified failure.

    One form submission is one attempt. An invalid answer is reported ON THIS STEP and never
    advances — and what the operator typed is kept, because the best-effort answers are persisted
    even when the step refuses. The write goes to the draft through the ONE config-write primitive.
    """
    detection = node_step.cached_detection(request.app)
    previous = node_step.answers_from_mapping(draft.node)

    answers, failure = _resolve_node(form, detection, previous, discord=draft.discord,
                                     validate=not stay)
    draft.node = dict(draft.node)
    if failure is not None:
        code, data = failure
        best, _ = _resolve_node(form, detection, previous, discord=draft.discord, validate=False)
        assert best is not None            # a best-effort resolution never fails
        draft.node.update(node_step.non_secret_document(best))
        draft.node["last"] = {"code": code, "data": data}
        draft.save(config_dir)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 4))

    assert answers is not None             # a validated resolution that did not fail is an answer
    draft.node.update(node_step.non_secret_document(answers))
    draft.node.pop("last", None)
    if not stay:
        draft.step = max(draft.step, 4)
        draft.save(config_dir)
        # the machine is scanned from step 4's answer on: the row set step 8 shows, and the rail's
        # decision to keep or drop that row, both hang on it
        _rescan_servers(draft, config_dir, force=True)
        return RedirectResponse(STEP_PATH, status_code=303)
    draft.save(config_dir)
    return HTMLResponse(_render(request, SetupDraft.load(config_dir), 4, stay=True))


def _submit_servers(request: Request, draft: SetupDraft, form, config_dir, stay: bool) -> Response:
    """Step 5's one write path: save the rows, or re-render with the classified failure.

    One form submission is one attempt. An incomplete managed row is reported ON THIS STEP and never
    advances — and what the operator typed is kept, because the best-effort answers are persisted
    even when the step refuses. The write goes to the draft through the ONE config-write primitive.
    """
    answers = servers_step.answers_from_mapping(draft.servers)
    # the channel fields are required exactly where the step ASKED for them — the same Discord
    # context that renders the rows decides it (see :func:`_servers_discord`)
    channels, _central_admin = _servers_discord(request, draft)
    resolved, failure = servers_step.resolve_rows(form, answers, validate=not stay,
                                                  channels=channels)
    draft.servers = dict(draft.servers)
    if failure is not None:
        code, data = failure
        best, _ = servers_step.resolve_rows(form, answers, validate=False)
        assert best is not None            # a best-effort resolution never fails
        draft.servers.update(servers_step.non_secret_document(best))
        draft.servers["last"] = {"code": code, "data": data}
        draft.save(config_dir)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 8))

    assert resolved is not None            # a validated resolution that did not fail is an answer
    draft.servers.update(servers_step.non_secret_document(resolved))
    draft.servers.pop("last", None)
    if not stay:
        draft.step = max(draft.step, 8)
        draft.save(config_dir)
        return RedirectResponse(STEP_PATH, status_code=303)
    draft.save(config_dir)
    return HTMLResponse(_render(request, SetupDraft.load(config_dir), 8, stay=True))


def _persist_console(request: Request, draft: SetupDraft, form, *, validate: bool
                     ) -> tuple[str, dict] | None:
    """Resolve the Web-console step's fields into the draft, and return its classified failure.

    THE PLAINTEXT SECRET NEVER LEAVES THIS CALL: a typed account password is hashed here
    (``setup.console``) and only its hash reaches the draft. The Discord client secret is NOT read
    here — it is collected on step 5 and held in its own process store
    (``setup.discord.client_secret_store``). A failing answer still keeps what it can — the typed
    username, the url, the toggle and the stored hash — so a missing field never costs the operator
    the rest.
    """
    previous = console_step.answers_from_mapping(draft.console)
    default_base_url = _console_base_url(request, previous)
    answers, failure = console_step.resolve_console(form, previous, validate=validate,
                                                    default_base_url=default_base_url)
    draft.console = dict(draft.console)
    if failure is not None:
        draft.console.update({"enabled": form.get("console_enabled") is not None,
                              "public_base_url": (str(form.get("console_public_base_url") or "")
                                                  .strip() or previous.public_base_url
                                                  or default_base_url),
                              "username": str(form.get("console_username") or "").strip(),
                              "password_hash": previous.password_hash})
        draft.console["last"] = {"code": failure[0], "data": failure[1]}
        return failure
    assert answers is not None             # a validated resolution that did not fail is an answer
    draft.console.update(console_step.non_secret_document(answers))
    draft.console.pop("last", None)
    return None


def _submit_console(request: Request, draft: SetupDraft, form, config_dir, stay: bool) -> Response:
    """Step 7's one write path: the console switch, the address and the account.

    One form submission is one attempt. A refusal (a missing username or password while the console is
    ON) is reported ON THIS STEP and never advances, and what the operator typed is kept. The write
    goes to the draft through the ONE config-write primitive. The Discord client secret is not part of
    this step — it is collected on step 5 and held in process memory.
    """
    failure = _persist_console(request, draft, form, validate=not stay)
    if failure is not None:
        draft.save(config_dir)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 7))
    if not stay:
        draft.step = max(draft.step, 7)
        draft.save(config_dir)
        return RedirectResponse(STEP_PATH, status_code=303)
    draft.save(config_dir)
    return HTMLResponse(_render(request, SetupDraft.load(config_dir), 7, stay=True))


async def _leave_others_submit(request: Request, draft: SetupDraft, form, config_dir,
                               answers, token) -> Response:
    """Step 5's leave action: make the bot leave every guild but the one the operator keeps.

    The choice is re-checked against the read's own guild list (never trusted from the form), and an
    explicit confirmation tick is required (JavaScript off). The kept guild is written to the draft's
    ``guild_id`` — it is what the tour's ``main.yaml`` gets — and the read is refreshed for it, so the
    step's gate reflects the guilds that are actually left. The outcome is reported PER guild.
    """
    guild_id = answers.guild_id
    keep = discord_step.parse_id(form.get("keep_guild"))
    known = [gid for gid, _ in discord_step.fetch_guilds(request.app, token, guild_id)]
    refusal = None
    if keep is None or keep not in known:
        refusal = "leave_choose_required"
    elif form.get("confirm_leave") is None:
        refusal = "leave_confirm_required"
    if refusal is not None:
        draft.discord_bot.update(discord_step.non_secret_document(answers))
        draft.discord_bot["last"] = {"code": refusal, "data": {}}
        draft.save(config_dir)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 5))

    result = await discord_step.leave_others(token, keep)
    if result is None:
        # the leave could not even be attempted (login or the choice failed live): ask again
        draft.discord_bot.update(discord_step.non_secret_document(answers))
        draft.discord_bot["last"] = {"code": "leave_choose_required", "data": {}}
        draft.save(config_dir)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 5))

    assert keep is not None                # the refusal above returns unless a valid choice was made
    kept_id = keep
    kept = discord_step.BotAnswers(guild_id=kept_id, owner=answers.owner,
                                   privileged_intents=answers.privileged_intents)
    draft.discord_bot.update(discord_step.non_secret_document(kept))
    draft.discord_bot.pop("last", None)
    draft.discord_bot["leave"] = {
        "kept": [int(result["kept"][0]), str(result["kept"][1])],
        "left": [[int(gid), str(name)] for gid, name in result["left"]],
        "failed": [[int(gid), str(name), str(reason)]
                   for gid, name, reason in result["failed"]],
    }
    draft.save(config_dir)
    # the leaves changed the bot's own guild list: refresh the read for the kept guild so the gate and
    # the pickers reflect what is left now.
    await discord_step.fetch_guild_data(request.app, token, kept_id)
    return HTMLResponse(_render(request, SetupDraft.load(config_dir), 5))


async def _submit_discord_bot(request: Request, draft: SetupDraft, form, config_dir,
                              stay: bool) -> Response:
    """Step 5's one write path: the guild id / owner / member-count answer, the TOKEN and the CLIENT
    SECRET, and the step's own Test.

    BOTH SECRETS ARE HELD IN PROCESS MEMORY ONLY, never in the draft. The TOKEN goes to
    ``setup.discord.token_store`` — it is what the Review step turns into ``config/.secret/token.pkl``,
    the destination the installer uses. The CLIENT SECRET goes to ``setup.discord.client_secret_store``
    under ``setup.discord.CLIENT_SECRET_KEY`` (``config/.secret/discord_client_secret.pkl``), and is
    written only when the console is enabled and the token's application id is known
    (``setup.review.plan``). A typed secret is kept the moment it arrives — even when another field of
    the same submit is refused — so a failure never costs the operator the paste. Both submits need a
    token: the one just typed, or the one already held.

    ``action="test"`` READS the guild (channels + roles, plus the bot's own guild list — ids and names
    only) and stays on the step; the read is HTTP-only, HTTPS, exactly the one guild from the form, and
    its result lives in process memory keyed on ``(token, guild_id)``, so changing either re-fetches.
    ``action="leave_others"`` (the too-many-guilds chooser) leaves every guild but the chosen one over
    the token's own HTTP call and reports the outcome per guild (see :func:`_leave_others_submit`).
    ``action="continue"`` advances ONLY once that read has succeeded for these values in THIS process:
    the token is never in the draft, so a restarted console holds no read and must test again.
    """
    action = form.get("action") or "continue"
    store = discord_step.token_store(request.app)
    supplied = form.get("token")
    if supplied:
        store[discord_step.TOKEN_KEY] = supplied
    supplied_secret = form.get("console_client_secret")
    if supplied_secret:
        discord_step.client_secret_store(request.app)[discord_step.CLIENT_SECRET_KEY] = \
            supplied_secret
    previous = discord_step.answers_from_mapping(draft.discord_bot)
    answers, failure = discord_step.resolve_bot(form, previous, validate=not stay)
    draft.discord_bot = dict(draft.discord_bot)
    if failure is not None:
        code, data = failure
        best, _ = discord_step.resolve_bot(form, previous, validate=False)
        assert best is not None            # a best-effort resolution never fails
        draft.discord_bot.update(discord_step.non_secret_document(best))
        draft.discord_bot["last"] = {"code": code, "data": data}
        draft.save(config_dir)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 5))

    assert answers is not None             # a validated resolution that did not fail is an answer
    if not store.get(discord_step.TOKEN_KEY):
        draft.discord_bot.update(discord_step.non_secret_document(answers))
        draft.discord_bot["last"] = {"code": "discord_token_required", "data": {}}
        draft.save(config_dir)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 5))

    token = store.get(discord_step.TOKEN_KEY)
    if action == "test":
        # the Test: save the non-secret answers, run the read, and stay on the step (never advance)
        draft.discord_bot.update(discord_step.non_secret_document(answers))
        draft.discord_bot.pop("last", None)
        draft.discord_bot.pop("leave", None)
        draft.save(config_dir)
        await discord_step.fetch_guild_data(request.app, token, answers.guild_id)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 5))

    if action == "leave_others":
        return await _leave_others_submit(request, draft, form, config_dir, answers, token)

    if discord_step.fetch_status(request.app, token, answers.guild_id) != "":
        # Continue before this process read these values successfully: refuse, advance nothing
        draft.discord_bot.update(discord_step.non_secret_document(answers))
        draft.discord_bot["last"] = {"code": "continue_blocked", "data": {}}
        draft.save(config_dir)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 5))

    draft.discord_bot.update(discord_step.non_secret_document(answers))
    draft.discord_bot.pop("last", None)
    draft.discord_bot.pop("leave", None)
    # a re-render never repeats the read (the cache answers for the same key), so Continue is a no-op
    # on the wire now that the read has already succeeded.
    await discord_step.fetch_guild_data(request.app, token, answers.guild_id)
    if not stay:
        draft.step = max(draft.step, 5)
        draft.save(config_dir)
        return RedirectResponse(STEP_PATH, status_code=303)
    draft.save(config_dir)
    return HTMLResponse(_render(request, SetupDraft.load(config_dir), 5, stay=True))


def _submit_roles(request: Request, draft: SetupDraft, form, config_dir, stay: bool) -> Response:
    """Step 6's one write path: the role mapping, the two channels and the admin-channel choice."""
    previous = discord_step.roles_from_mapping(draft.roles)
    answers, failure = discord_step.resolve_roles(form, previous, validate=not stay)
    draft.roles = dict(draft.roles)
    if failure is not None:
        code, data = failure
        best, _ = discord_step.resolve_roles(form, previous, validate=False)
        assert best is not None            # a best-effort resolution never fails
        draft.roles.update(discord_step.roles_document(best))
        draft.roles["last"] = {"code": code, "data": data}
        draft.save(config_dir)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 6))

    assert answers is not None             # a validated resolution that did not fail is an answer
    draft.roles.update(discord_step.roles_document(answers))
    draft.roles.pop("last", None)
    if not stay:
        draft.step = max(draft.step, 6)
        draft.save(config_dir)
        return RedirectResponse(STEP_PATH, status_code=303)
    draft.save(config_dir)
    return HTMLResponse(_render(request, SetupDraft.load(config_dir), 6, stay=True))


def _submit_community(request: Request, draft: SetupDraft, form, config_dir, stay: bool) -> Response:
    """Step 5' - the no-Discord write path: the group name and the UCID lists.

    The Discord blocks are untouched: they are still in the draft, which is what keeps their answers
    when the rail flips back.
    """
    previous = community_step.answers_from_mapping(draft.community)
    answers, failure = community_step.resolve_community(form, previous, validate=not stay)
    draft.community = dict(draft.community)
    if failure is not None:
        code, data = failure
        best, _ = community_step.resolve_community(form, previous, validate=False)
        assert best is not None            # a best-effort resolution never fails
        draft.community.update(community_step.non_secret_document(best))
        draft.community["last"] = {"code": code, "data": data}
        draft.save(config_dir)
        return HTMLResponse(_render(request, SetupDraft.load(config_dir), 5))

    assert answers is not None             # a validated resolution that did not fail is an answer
    draft.community.update(community_step.non_secret_document(answers))
    draft.community.pop("last", None)
    if not stay:
        draft.step = max(draft.step, 5)
        draft.save(config_dir)
        return RedirectResponse(STEP_PATH, status_code=303)
    draft.save(config_dir)
    return HTMLResponse(_render(request, SetupDraft.load(config_dir), 5, stay=True))


def _discard_draft(config_dir) -> None:
    """Remove the tour's draft and its ``.bak``: the configuration is written, the state is spent."""
    draft = draft_path(config_dir)
    for path in (draft, draft.with_name(draft.name + ".bak")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as ex:
            log.warning("Setup tour: '%s' could not be removed (%s).", path, ex)


def _submit_review(request: Request, draft: SetupDraft, form, config_dir) -> Response:
    """Step 9's one write path: the whole configuration, in one ordered commit.

    :func:`services.webservice.setup.review.write` writes every target through the ONE config-write
    primitive and writes the CLOSING target LAST, and only when everything before it reached the
    disk: ``main.yaml`` on a first run, this node's ``nodes.yaml`` merge while joining.

    GATED ON THE REQUIRED SECRETS BEING HELD in this process: a mid-tour restart loses the in-memory
    Discord token and, on the existing-database path, the password. Committing then would write a
    configuration with no token and close the window — an install the bot can never sign in with — so
    the step refuses outright, writes NOTHING (least of all the closing file) and re-renders the
    refusal that names the step to re-enter.

    A commit whose CLOSING target landed closes the setup window: the draft (tour state, no longer
    needed) is removed, the terminal Done screen is returned, and the console is asked to stop AFTER
    the response is on the wire, so the launcher continues into the normal boot. A commit that did
    not is reported per target and the window stays open — the reason can be fixed and the write
    repeated.
    """
    mode = _mode(request)
    if mode == boot_module.JOIN:
        refusal = review_step.join_database_refusal(draft, app=request.app)
        if refusal:
            log.warning("Setup join: the Review step refused — %s Nothing was written and the setup "
                        "window stays open.", refusal)
            return HTMLResponse(_render(request, draft, 9))
    missing = review_step.missing_secrets(draft, app=request.app, mode=mode)
    if missing:
        log.warning("Setup tour: the Review step refused — %s not held in this process; nothing was "
                    "written and the setup window stays open.", ", ".join(t.key for t in missing))
        return HTMLResponse(_render(request, draft, 9))
    plan = review_step.plan(draft, app=request.app, mode=mode)
    closing = review_step.NODES if mode == boot_module.JOIN else review_step.MAIN
    report = review_step.write(plan, config_dir, mode=mode, closing=closing)
    results = [{"key": outcome.target.key, "ok": outcome.ok, "changed": outcome.changed,
                "skipped": outcome.skipped, "error": outcome.error}
               for outcome in report.outcomes]
    if report.completed:
        draft.review = {"written": True, "results": results}
        draft.step = LAST_STEP
        _discard_draft(config_dir)
        log.info("Setup tour: the configuration was written and %s closed the setup window "
                 "(%d target(s)).", closing.relpath, len(results))
        response = HTMLResponse(_done_document(request, draft))
        finish = getattr(request.app.state, "setup_finish", None)
        if callable(finish):
            response.background = BackgroundTask(finish)
        return response
    log.warning("Setup tour: %d of %d target(s) were NOT written; %s was not written and "
                "the setup window stays open.", len(report.failed), len(results), closing.relpath)
    draft.review = {"written": False, "results": results}
    draft.save(config_dir)
    return HTMLResponse(_render(request, SetupDraft.load(config_dir), 9))


def _remember_language(response: Response, request: Request, language: str) -> None:
    """Set the console's language cookie to the operator's choice (the same cookie ``/language``
    sets), so step 1's answer IS the console's language and the switch that follows stays in step."""
    settings = getattr(request.app.state, "webui_session_settings", None) or {}
    response.set_cookie(i18n.COOKIE_NAME, language, max_age=i18n.COOKIE_MAX_AGE, path="/",
                        secure=bool(settings.get("https_only")), samesite="lax", httponly=True)


# ------------------------------------------------------------- the flag + theme switch routes

def language_redirect(request: Request, lang: str, next_target: str) -> Response:
    """``GET /language``: store the choice in its own cookie and return where the caller was.

    The code is written back into the setup draft when one already exists: the draft's ``language``
    outranks the browser, so an answer left over from an earlier run would otherwise come back the
    moment the console cookie is gone (another browser, a cleared jar).
    """
    response = RedirectResponse(i18n._safe_target(next_target), status_code=303)
    code = i18n.normalise_language(lang)
    if code:
        _remember_language(response, request, code)
        _remember_language_in_draft(request, code)
    return response


def _remember_language_in_draft(request: Request, code: str) -> None:
    """Rewrite step 1's saved answer to *code* — only when a draft already holds one.

    A draft that does not exist yet is left alone: before step 1 has an answer there is nothing stale
    to clear, and creating one here would record an answer nobody gave. A write that fails is logged
    and dropped — the cookie the caller just set carries the same choice for this browser, so the
    switch is never lost to it.
    """
    config_dir = _config_dir(request)
    if not draft_path(config_dir).exists():
        return
    try:
        draft = SetupDraft.load(config_dir)
        if draft.language == code:
            return
        draft.language = code
        draft.save(config_dir)
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup tour: the language choice could not be written back to the setup draft "
                    "(%s); the console cookie still carries it.", ex)


def theme_redirect(request: Request, value: str, next_target: str) -> Response:
    """``GET /theme``: store the theme choice in its own cookie and return where the caller was."""
    response = RedirectResponse(i18n._safe_target(next_target), status_code=303)
    code = theme_module.normalise_theme(value)
    if code:
        settings = getattr(request.app.state, "webui_session_settings", None) or {}
        response.set_cookie(theme_module.COOKIE_NAME, code, max_age=theme_module.COOKIE_MAX_AGE,
                            path="/", secure=bool(settings.get("https_only")), samesite="lax",
                            httponly=True)
    return response


def render_flags(name: str) -> Response:
    """``GET /setup/flags/<name>``: the vendored flag SVGs, from a fixed allow-list.

    The setup application serves no ``/static`` area (it installs no shell), so the tour's own flag
    control is fed from here. Only the declared assets are reachable — a name is never a path.
    """
    allowed = set(i18n.LANGUAGE_FLAGS.values()) | {i18n.FLAG_NEUTRAL_ASSET}
    if name not in allowed:
        return Response(status_code=404)
    try:
        data = (FLAGS_DIR / name).read_bytes()
    except OSError:
        return Response(status_code=404)
    return Response(content=data, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})
