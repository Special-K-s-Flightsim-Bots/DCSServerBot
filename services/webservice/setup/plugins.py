"""Step 9 of the tour — \"What do you run?\": plugin templates and the extension rows.

TEMPLATES ARE DATA. One template is one YAML file under ``templates/`` (``<id>.yaml``) carrying
``id``, ``label``, ``description``, ``plugins`` (the plugin folder names the template switches on)
and the optional ``order`` (its tile position; an absent ``order`` sorts after every numbered one,
tie-broken by ``id``). A new template is a new file — no Python change, and none of the plugin names
on this screen are written in this module.

WHAT THE STEP OWNS. The ticked template ids and the extension answers: one radio choice (SRS or DCS
Voice Chat) per configured instance, and the Tacview / LotAtC ticks. The paths the extension rows
show are read by :func:`services.webservice.setup.apps.detect_app` and passed in by the caller;
nothing here probes the machine, installs anything, or starts anything. Every other extension is
the console's own config editor.

NOTHING HERE WRITES A FILE: the tour's draft is the destination (see
:mod:`services.webservice.setup.state`), and the extension paths land in ``nodes.yaml``'s
``extensions`` block through the same data the Apps step already carries.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence

from ruamel.yaml import YAML

from .. import i18n, session

__all__ = [
    "ROUTE", "STEP_ID", "TEMPLATES_DIR", "TEMPLATE_SUFFIX",
    "RADIO_SRS", "RADIO_VOICE_CHAT", "RADIO_CHOICES",
    "Template", "PluginAnswers",
    "load_templates", "answers_from_mapping", "non_secret_document", "resolve_answers",
    "screen_body",
]

log = logging.getLogger(__name__)

#: The tour's one step route. Pinned against the tour's own constant by the step's tests.
ROUTE = "/setup/step"

#: The step id this module renders (the rail's id, not the row number it shows).
STEP_ID = 9

#: The template files' directory and suffix.
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_SUFFIX = ".yaml"

#: The keys a template file must carry.
_TEMPLATE_KEYS = ("id", "label", "description", "plugins")

#: The two radio answers. One per configured instance, and they exclude each other because both
#: would fight over the same audio.
RADIO_SRS = "srs"
RADIO_VOICE_CHAT = "voicechat"
RADIO_CHOICES = (RADIO_SRS, RADIO_VOICE_CHAT)


# ------------------------------------------------------------------------------------ the templates

@dataclass(frozen=True)
class Template:
    """One offered template: its file id, the tile's words, and the plugins it switches on."""

    id: str
    label: str
    description: str
    plugins: tuple[str, ...]
    order: int = 0


def _as_template(data: Any) -> Template | None:
    """*data* as a :class:`Template`, or ``None`` when a required key is missing or empty."""
    if not isinstance(data, Mapping):
        return None
    for key in _TEMPLATE_KEYS:
        if not data.get(key):
            return None
    plugins = data.get("plugins")
    if not isinstance(plugins, (list, tuple)):
        return None
    try:
        order = int(data.get("order") or 0)
    except (TypeError, ValueError):
        order = 0
    return Template(id=str(data["id"]),
                    label=str(data["label"]),
                    description=str(data["description"]),
                    plugins=tuple(str(item) for item in plugins),
                    order=order)


def load_templates(directory: Path = TEMPLATES_DIR) -> tuple[Template, ...]:
    """Every readable template in *directory*, in tile order.

    A file that cannot be read, or that does not carry the four required keys, is reported and
    skipped: one malformed template must not take the screen down.
    """
    templates: list[Template] = []
    for path in sorted(Path(directory).glob("*" + TEMPLATE_SUFFIX)):
        try:
            data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
        except Exception as ex:  # noqa: BLE001 - an unreadable template is skipped, never fatal
            log.warning("Setup tour: the template '%s' could not be read (%s).", path, ex)
            continue
        template = _as_template(data)
        if template is None:
            log.warning("Setup tour: the template '%s' is missing one of %s.", path,
                        ", ".join(_TEMPLATE_KEYS))
            continue
        templates.append(template)
    return tuple(sorted(templates, key=lambda item: (item.order, item.id)))


# ------------------------------------------------------------------------------------- the answers

@dataclass
class PluginAnswers:
    """Step 9's answers: the ticked template ids and the extension rows.

    ``radio`` maps an instance name to :data:`RADIO_SRS` or :data:`RADIO_VOICE_CHAT`; an instance
    with no answer is absent, and the screen shows SRS for it.
    """

    templates: list[str] = field(default_factory=list)
    radio: dict[str, str] = field(default_factory=dict)
    tacview: bool = False
    lotatc: bool = False


def answers_from_mapping(data: Mapping | None) -> PluginAnswers:
    """Build the answers from the draft's ``plugins`` block (missing keys get an empty answer)."""
    data = data if isinstance(data, Mapping) else {}
    stored = data.get("templates")
    templates = [str(item) for item in stored] if isinstance(stored, (list, tuple)) else []
    radio = data.get("radio")
    radio = radio if isinstance(radio, Mapping) else {}
    return PluginAnswers(
        templates=templates,
        radio={str(name): str(value) for name, value in radio.items()
               if str(value) in RADIO_CHOICES},
        tacview=bool(data.get("tacview", False)),
        lotatc=bool(data.get("lotatc", False)))


def non_secret_document(answers: PluginAnswers) -> dict:
    """The draft-writable part of the answers (this step holds no secret)."""
    return {"templates": list(answers.templates),
            "radio": dict(answers.radio),
            "tacview": bool(answers.tacview),
            "lotatc": bool(answers.lotatc)}


def resolve_answers(values: Mapping, instances: Sequence[str],
                    templates: Sequence[Template] | None = None) -> PluginAnswers:
    """The submitted form as answers.

    Ticked tiles are read by their INDEX in the rendered template list, so a template added to the
    data files needs no field name of its own. A submitted VALUE that names a known template id wins
    over the catalogue entry at that index: the request is shaped by the tile the browser rendered, and
    a template file added or reordered between that render and this submit would otherwise be recorded
    as the wrong plugin set. An unknown or empty value falls back to the index, so nothing is refused.
    An instance whose radio was not submitted is recorded as :data:`RADIO_SRS`, which is what the
    screen shows as the default.
    """
    catalogue = load_templates() if templates is None else templates
    known = {template.id for template in catalogue}
    chosen: list[str] = []
    for index, template in enumerate(catalogue):
        value = values.get(f"template_{index}")
        if value is None:
            continue
        submitted = str(value).strip()
        chosen.append(submitted if submitted in known else template.id)
    radio: dict[str, str] = {}
    for index, instance in enumerate(instances):
        value = str(values.get(f"radio_{index}") or "")
        radio[str(instance)] = value if value in RADIO_CHOICES else RADIO_SRS
    return PluginAnswers(templates=chosen,
                         radio=radio,
                         tacview=values.get("tacview") is not None,
                         lotatc=values.get("lotatc") is not None)


# --------------------------------------------------------------------------------------- the words

T_LEAD = i18n._(
    "Pick anything that sounds like your server. Every tick pre-fills a set of plugins for all the "
    "servers you just configured — you can change any of it, and nothing is written yet.")
T_ALSO = i18n._("Also install")
T_RADIO = i18n._("Radio for {instance} — pick one")
T_RADIO_HELP = i18n._("Both at once would fight over the same audio.")
T_SRS = i18n._("DCS-SRS")
T_SRS_FOUND = i18n._(
    "Found at {path}. Each server gets its own SRS port, two apart, above the ports already in use.")
T_SRS_MISSING = i18n._(
    "Not found on this machine. Install it first, or add the path later in the console. Each server "
    "gets its own SRS port, two apart, above the ports already in use.")
T_VOICE_CHAT = i18n._("DCS Voice Chat")
T_VOICE_CHAT_HELP = i18n._(
    "Built into DCS. Nothing to install; the bot just switches the server side on.")
T_TACTICAL = i18n._("Tactical view")
T_TACVIEW = i18n._("Tacview")
T_TACVIEW_FOUND = i18n._("Found at {path}. One recorded ACMI per flight.")
T_LOTATC = i18n._("LotAtC")
T_FOUND = i18n._("Found at {path}.")
T_MISSING = i18n._("Not found on this machine. Install it first, or add the path later in the console.")
T_MORE = i18n._(
    "There are many more extensions — GCI, weather, persistence, mod manager and others. Every one "
    "of them has a form in the web admin console once the bot is running.")
T_BACK = i18n._("Back")
T_CONTINUE = i18n._("Continue")


def _say(translate, msgid: str, **params) -> str:
    """Translate *msgid* then fill its holes (translate first, interpolate after), escaped.

    A catalog line whose placeholders do not match degrades to the English source instead of raising
    into the render.
    """
    text = translate(msgid)
    if not params:
        return escape(text)
    try:
        return escape(text.format(**params))
    except (KeyError, IndexError, ValueError):
        return escape(msgid)


# ---------------------------------------------------------------------------------------- the body

def _tile(index: int, template: Template, chosen: Sequence[str], translate) -> str:
    """One template tile: its switch, its words, and the plugins it switches on."""
    mark = " checked" if template.id in chosen else ""
    plugins = " · ".join(template.plugins)
    return (f'<label class="tile"><input type="checkbox" name="template_{index}"'
            f' value="{escape(template.id)}"{mark}>'
            f'<b>{escape(translate(template.label))}</b>'
            f'<span class="sub">{escape(translate(template.description))}</span>'
            f'<span class="plugs">{escape(plugins)}</span></label>')


def _radio_block(index: int, instance: tuple[str, str], answers: PluginAnswers,
                 detected: Mapping, translate) -> str:
    """The one radio choice for *instance* — a ``(key, shown name)`` pair — SRS or DCS voice chat."""
    key, shown = instance
    chosen = answers.radio.get(key) or RADIO_SRS
    srs_path = detected.get("SRS")
    srs_help = (_say(translate, T_SRS_FOUND, path=srs_path) if srs_path
                else escape(translate(T_SRS_MISSING)))
    legend = f'<div class="choice-legend"><b>{_say(translate, T_RADIO, instance=shown)}</b>' \
             f'{escape(translate(T_RADIO_HELP))}</div>'
    return (f'<fieldset class="choice">{legend}'
            f'<label class="opt"><input type="radio" name="radio_{index}" value="{RADIO_SRS}"'
            f'{" checked" if chosen == RADIO_SRS else ""}>'
            f'<span><b>{escape(translate(T_SRS))}</b>'
            f'<span class="help">{srs_help}</span></span></label>'
            f'<label class="opt"><input type="radio" name="radio_{index}"'
            f' value="{RADIO_VOICE_CHAT}"'
            f'{" checked" if chosen == RADIO_VOICE_CHAT else ""}>'
            f'<span><b>{escape(translate(T_VOICE_CHAT))}</b>'
            f'<span class="help">{escape(translate(T_VOICE_CHAT_HELP))}</span></span></label>'
            f'</fieldset>')


def _detected_help(found: str | None, translate, *, found_msg: str) -> str:
    return _say(translate, found_msg, path=found) if found else escape(translate(T_MISSING))


def screen_body(answers: PluginAnswers, instances: Sequence[tuple[str, str]], detected: Mapping,
                translate, *, token: str, back_link: str = "",
                templates: Sequence[Template] | None = None) -> str:
    """The step's form: the template tiles, then the extension rows, then Continue.

    *instances* are the configured DCS instances in the draft's own order, each a
    ``(instance, shown name)`` pair; *detected* maps an application key
    (``SRS``/``Tacview``/``LotAtC``) to the path it was found at, or ``None``.
    """
    catalogue = load_templates() if templates is None else templates
    tiles = "".join(_tile(index, template, answers.templates, translate)
                    for index, template in enumerate(catalogue))
    radios = "".join(_radio_block(index, instance, answers, detected, translate)
                     for index, instance in enumerate(instances))
    tacview = " checked" if answers.tacview else ""
    lotatc = " checked" if answers.lotatc else ""
    tactical = (
        f'<fieldset class="choice">'
        f'<div class="choice-legend"><b>{escape(translate(T_TACTICAL))}</b></div>'
        f'<label class="opt"><input type="checkbox" name="tacview"{tacview}>'
        f'<span><b>{escape(translate(T_TACVIEW))}</b>'
        f'<span class="help">{_detected_help(detected.get("Tacview"), translate, found_msg=T_TACVIEW_FOUND)}</span>'
        f'</span></label>'
        f'<label class="opt"><input type="checkbox" name="lotatc"{lotatc}>'
        f'<span><b>{escape(translate(T_LOTATC))}</b>'
        f'<span class="help">{_detected_help(detected.get("LotAtC"), translate, found_msg=T_FOUND)}</span>'
        f'</span></label></fieldset>')
    return (
        f'<form method="post" action="{ROUTE}">'
        f'<input type="hidden" name="{session.CSRF_FIELD}" value="{escape(token)}">'
        f'<input type="hidden" name="n" value="{STEP_ID}">'
        f'<p class="lead">{escape(translate(T_LEAD))}</p>'
        f'<div class="tiles">{tiles}</div>'
        f'<h2 class="sec">{escape(translate(T_ALSO))}</h2>'
        f'{radios}{tactical}'
        f'<p class="note">{escape(translate(T_MORE))}</p>'
        f'<div class="actions">{back_link}'
        f'<button type="submit">{escape(translate(T_CONTINUE))}</button></div></form>')
