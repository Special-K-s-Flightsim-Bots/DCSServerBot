"""A server's DCS configuration, read as the page needs it — the READ HALF's data source.

WHAT THIS READS, AND WHAT IT MUST NOT: the page renders from the IN-PROCESS snapshot the master
already holds — ``server.settings`` (the ``SettingsDict`` / ``RemoteSettingsDict`` the bot loaded at
startup) and ``server.locals`` (``read_locals()``). Nothing here is awaited, no file is opened, and
``Server.get_config()`` / ``ServerProxy`` is NEVER called: on an agent node those are RPCs to the
owning node, and a render that issued one would turn every page paint into a node round-trip. The
function is a pure read of duck-typed objects, exactly like
:func:`services.webservice.readmodels.servers.server_view`.

THE FIELD DECLARATION is the console's OWN table, grouped by MEANING. ``serverSettings.lua`` has NO
schema to generate a form from, so this table mirrors, key for key, the curated set the Discord modals
already allow (``plugins/scheduler/actions.py``: ``DCS_FIELDS`` / ``DCS_READONLY_FIELDS``).
``tests/test_webui_server_config.py`` imports the action's own table and asserts the two key sets are
IDENTICAL, so a key added on one side and not the other fails a test rather than confusing an operator.
(The action's table is imported by the TEST, never by this module: the read models must stay free of
``core``, which the action's module pulls in.)

SECRETS: a PASSWORD field DECLARES which it is, in ONE attribute
(:data:`ConfigField.protection`) whose every value means exactly one thing — there is no second flag
that could be read as "secret". Two states:

* :data:`UNPROTECTED` — the value IS rendered into the page's field (masked, with the eye) and the
  write is the plain diff: untouched keeps it, emptying clears it, typing replaces it.
* :data:`PROTECTED` — the value is NEVER rendered (nothing about it reaches the browser): the row
  shows the "a password is set" state (a fixed placeholder, :data:`PROTECTED_SENTINEL`) and the write
  keeps / clears / replaces it. A new password field MUST declare which it is; the DEFAULT is
  "not a password at all", never protected — the safe branch, because only a protected value is truly
  absent from the page.

The DCS server password is UNPROTECTED, so the operator can read it. The coalition password HASHES are
PROTECTED and read-only: their keys are declared only so the drift test can pin them, and they are
never read into a view record.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .access import attr, safe, text
from .model import StatusView, status_view


def _(message: str) -> str:
    """Mark a string as translatable for the extractor; return it UNCHANGED.

    See :func:`services.webservice.i18n._` and :mod:`.model`: Babel reads the literal out of this
    module while the runtime call is an identity, so the read model stays pure and has no language
    of its own. The label/help a row renders is translated where it is RENDERED, by the per-language
    template environment's own ``_`` (``{{ _(f.label) }}``), never here.
    """
    return message

__all__ = [
    "KIND_BOOL", "KIND_INT", "KIND_STR", "KIND_SEQ",
    "GROUP_ORDER", "GROUP_LABELS", "GROUP_IDENTITY", "GROUP_BEHAVIOUR", "GROUP_REQUIREMENTS",
    "GROUP_RESTRICTIONS", "GROUP_ANTICHEAT", "GROUP_CHANNELS", "GROUP_MISSION", "GROUP_COALITIONS",
    "ConfigField", "EDITABLE_FIELDS", "READONLY_FIELDS",
    "FieldView", "ConfigGroup", "ChannelView", "ServerConfigView",
    "PROTECTED", "UNPROTECTED", "PROTECTED_SENTINEL",
    "WRITABLE_RAW", "STOP_FIRST_SENTENCE", "SET_SENTINEL", "UNSET_SENTINEL",
    "bound_text", "server_config_view", "editable_keys", "readonly_keys",
    "EDITABLE_BY_KEY", "current_setting", "config_revision",
    # the channels face: the per-server rows, the guild's options and the central rule
    "CHANNEL_KEYS", "CHANNEL_ADMIN_KEY", "CHANNEL_UNSET", "CHANNEL_UNSET_VALUE", "CHANNEL_APPLY_NOTE",
    "ChannelOption", "ChannelGroup", "channel_value_text",
    "live_bot", "central_admin_channel", "guild_channel_groups",
    # the coalition face: the two plaintext rows, their states and the honest notes
    "COALITION_FIELDS", "COALITION_TOKENS", "COALITION_OF_KEY", "COALITION_APPLY_NOTE",
    "COALITION_UNKNOWN_NOTE", "COALITION_DB_ONLY_NOTE", "COALITION_REFUSED_SENTENCE",
    "WRITABLE_COALITION_RAW", "CoalitionView", "coalition_views", "coalition_keys",
]

# ------------------------------------------------------------------------------------ kinds

KIND_BOOL = "bool"
KIND_INT = "int"
KIND_STR = "str"
KIND_SEQ = "seq"

# ------------------------------------------------------------------------------------ groups
# The meaning-groups, in render order. The ``channels`` group is the ``servers.yaml`` face and the
# ``mission`` group is the read-only declaration the drift test pins (``missionList`` + the two
# coalition hashes). ``listStartIndex`` / ``current`` are NOT here: they belong to the Missions tab
# (``pages/server_detail``), which reads them through the ``get_mission_list`` action and never off
# ``server.settings``.

GROUP_IDENTITY = "identity"
GROUP_BEHAVIOUR = "behaviour"
GROUP_REQUIREMENTS = "requirements"
GROUP_RESTRICTIONS = "restrictions"
GROUP_ANTICHEAT = "anticheat"
GROUP_CHANNELS = "channels"
GROUP_MISSION = "mission"
GROUP_COALITIONS = "coalitions"

GROUP_LABELS: dict[str, str] = {
    GROUP_IDENTITY: _("Identity & access"),
    GROUP_BEHAVIOUR: _("Server behaviour"),
    GROUP_REQUIREMENTS: _("Requirements"),
    GROUP_RESTRICTIONS: _("Restrictions"),
    GROUP_ANTICHEAT: _("Anti-cheat"),
    GROUP_CHANNELS: _("Discord channels"),
    GROUP_MISSION: _("Mission list — owned by a feature of its own"),
    GROUP_COALITIONS: _("Coalition passwords"),
}

#: the groups rendered as editable form groups, in order (channels and mission are their own shapes)
GROUP_ORDER: tuple[str, ...] = (GROUP_IDENTITY, GROUP_BEHAVIOUR, GROUP_REQUIREMENTS,
                                GROUP_RESTRICTIONS, GROUP_ANTICHEAT)


# ------------------------------------------------------------------------------------ channels
# The per-server channel ROWS and the GUILD's options. The rows are the trio the bot's own Discord
# editor offers (``plugins/scheduler/views.py``): status, chat and — only when ``bot.yaml`` declares NO
# central admin channel — admin. The OPTIONS are the guild's own channels, read IN-PROCESS off the
# bot's Discord connection (no node RPC on a render).

#: The per-server channel keys this console edits, in render order — the bot's own trio
#: (``plugins/scheduler/views.py``). ``admin`` is present only when no central admin channel exists.
CHANNEL_KEYS: tuple[str, ...] = ("admin", "status", "chat")

#: The one key whose ROW is ABSENT when a central admin channel is defined in ``bot.yaml``.
CHANNEL_ADMIN_KEY = "admin"

#: The value a channel has when it is UNSET — the bot's own "disabled" marker (``-1``,
#: ``schemas/servers_schema.yaml``: ``range: {min: -1}``; ``plugins/scheduler/commands.py`` writes it).
CHANNEL_UNSET = -1

#: The explicit ``not set`` OPTION's submitted value — deliberately NOT ``""`` (which is what an
#: untouched select would post) so that clearing a SET channel is an explicit change, never a blank.
#: The write maps it back to :data:`CHANNEL_UNSET`.
CHANNEL_UNSET_VALUE = "unset"

#: The honest apply note for the channels face. A channel change is written through the bot's own
#: ``Server.update_channels`` (``core/data/impl/serverimpl.py`` / ``core/data/proxy/serverproxy.py``),
#: which rewrites ``servers.yaml``, updates the in-memory ``locals`` and clears the channel cache, so
#: the new channel is used AT ONCE — for a master-hosted and an agent-hosted server alike. NO restart
#: is needed (the page says so rather than borrowing the DCS face's "applies when the server next
#: starts").
CHANNEL_APPLY_NOTE = _("A channel change is applied immediately: the bot rewrites servers.yaml and "
                       "starts using the new channel at once, with no restart.")


def channel_value_text(raw) -> str:
    """A stored channel value as the row renders it: the id as text, or ``""`` when it is UNSET.

    An absent value and the bot's own ``-1`` both mean "not set" (``schemas/servers_schema.yaml``:
    ``-1`` disables a channel), so both render as the empty string — the value the ``not set``
    option is marked with. Anything unreadable is passed through as text so a hand-edited file shows
    what it really holds rather than a coerced guess.
    """
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return str(raw)
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return text(raw)
    return "" if number == CHANNEL_UNSET else str(number)



# ------------------------------------------------------------------------------------ protection
# ONE DECLARATION, TWO BRANCHES. A field that is a password states WHICH it is in the single
# ``ConfigField.protection`` attribute below, and each value means exactly one thing — so no field ever
# carries two flags that could both be read as "secret". A field that is NOT a password leaves it
# empty (the default): there is no "protected by default", because only a PROTECTED value is truly
# absent from the page, and a new password field must SAY which it is rather than inherit a guess.

#: The value is NEVER rendered. The row shows the "a password is set" state and the write keeps /
#: clears / replaces it; nothing about the value reaches the browser.
PROTECTED = "protected"

#: The value IS rendered, masked, with the eye; the write is the plain string diff.
UNPROTECTED = "unprotected"

#: The FIXED placeholder a PROTECTED field's control holds when a value is set. It is NOT the value —
#: the field renders this in place of anything real — and the write reads it back as "unchanged": an
#: untouched control (this sentinel) keeps the value, an emptied one clears it, a typed one replaces it.
PROTECTED_SENTINEL = "********"


@dataclass(frozen=True, slots=True)
class ConfigField:
    """One declared field of the DCS face: its key, kind, its bound, and the words a reader needs.

    ``key`` is DOTTED for the nested ``advanced`` map (``advanced.allow_change_skin``), matching the
    action's own vocabulary, so the read, the write and the drift test share one spelling.
    ``group`` places the field by MEANING. ``protection`` DECLARES a password field: :data:`PROTECTED`
    (never rendered) or :data:`UNPROTECTED` (rendered masked) — see the section above.
    """
    key: str
    kind: str
    label: str
    help: str
    group: str
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple = ()
    #: a PASSWORD field's declaration (:data:`PROTECTED` / :data:`UNPROTECTED`); ``""`` = not a password.
    #: The ONE flag: it says which kind of password field this is, and its default is "none".
    protection: str = ""
    readonly: bool = False
    #: Rendered as a MULTI-LINE field (a textarea) instead of a one-line box. It is a property of the
    #: DECLARATION, not of the template: "which field is longer than a line" is a fact about the
    #: field, and a template that answers it by key name (``f.key == 'description'``) is a second
    #: declaration that drifts the first time the set changes. Declared on exactly the one field whose
    #: own maximum (2000) does not fit a single-line box.
    multiline: bool = False


#: The CURATED editable set — EXACTLY the 22 keys the action's ``DCS_FIELDS`` declares, no more and no
#: fewer. The bounds are the modals' own (``plugins/scheduler/views.py``): ``description`` ≤ 2000 and
#: ``password`` ≤ 80 are the modal slice lengths, ``port`` a 1–65535 range the modal caps at five
#: digits, ``maxPlayers`` up to three digits (999), ``maxPing`` three digits, and ``resume_mode`` the
#: modal's three Select options.
EDITABLE_FIELDS: tuple[ConfigField, ...] = (
    ConfigField("name", KIND_STR, _("Server name"),
                _("Renamed by the bot, not by the file: a rename moves the server (its instance "
                  "folder and its servers.yaml entry) and rewrites the channel names and messages "
                  "that carry the old name."), GROUP_IDENTITY, minimum=1),
    ConfigField("description", KIND_STR, _("Description"),
                _("A brief description, shown in /status and in the server list. The Discord editor "
                  "allows up to 2000 characters."), GROUP_IDENTITY, maximum=2000, multiline=True),
    ConfigField("port", KIND_INT, _("Server port"),
                _("The UDP port DCS listens on. The Discord editor takes a 5-digit value here. A port "
                  "another server on this node already uses is refused, naming it."), GROUP_IDENTITY,
                minimum=1, maximum=65535),
    ConfigField("password", KIND_STR, _("Password"),
                _("The DCS password a client needs to join. The field shows the stored password: "
                  "leave it as it is to keep it, empty it to remove it, type in it to set a new one."),
                GROUP_IDENTITY, maximum=80, protection=UNPROTECTED),
    ConfigField("maxPlayers", KIND_INT, _("Maximum number of players"),
                _("The slot count DCS advertises. The Discord editor takes up to three digits here, "
                  "which is where the 999 ceiling comes from."), GROUP_BEHAVIOUR, minimum=1, maximum=999),
    ConfigField("isPublic", KIND_BOOL, _("Public server"),
                _("Lists the server in the public DCS server browser."), GROUP_BEHAVIOUR),
    ConfigField("advanced.resume_mode", KIND_INT, _("Resume mode"),
                _("What DCS does when the last client leaves and when a mission loads."), GROUP_BEHAVIOUR,
                choices=(0, 1, 2)),
    ConfigField("advanced.maxPing", KIND_INT, _("Maximum allowed ping"),
                _("Values above 300 tend to cause lags / desyncs (the editor's own words). 0 turns the "
                  "check off."), GROUP_BEHAVIOUR, minimum=0, maximum=999),
    ConfigField("advanced.server_can_screenshot", KIND_BOOL, _("Server can screenshot"),
                _("Lets the server take screenshots (used by plugins that ask a client for a picture)."),
                GROUP_BEHAVIOUR),
    ConfigField("advanced.allow_trial_only_clients", KIND_BOOL, _("Allow trial-only clients"),
                _("Clients that own no module may still join."), GROUP_BEHAVIOUR),
    ConfigField("require_pure_clients", KIND_BOOL, _("Require pure clients"),
                _("A client with any modification is refused at join."), GROUP_REQUIREMENTS),
    ConfigField("require_pure_scripts", KIND_BOOL, _("Require pure scripts"),
                _("Modified mission scripts are refused."), GROUP_REQUIREMENTS),
    ConfigField("require_pure_models", KIND_BOOL, _("Require pure models"),
                _("Modified 3D models are refused."), GROUP_REQUIREMENTS),
    ConfigField("require_pure_textures", KIND_BOOL, _("Require pure textures"),
                _("Modified textures are refused."), GROUP_REQUIREMENTS),
    ConfigField("advanced.allow_change_tailno", KIND_BOOL, _("Allow change tail number"),
                _("Players may repaint their tail number in the cockpit."), GROUP_RESTRICTIONS),
    ConfigField("advanced.allow_dynamic_radio", KIND_BOOL, _("Allow dynamic radio"),
                _("Players may tune any radio channel, not only the mission's presets."),
                GROUP_RESTRICTIONS),
    ConfigField("advanced.allow_change_skin", KIND_BOOL, _("Allow change skin"),
                _("Players may pick another livery for their aircraft."), GROUP_RESTRICTIONS),
    ConfigField("advanced.allow_object_export", KIND_BOOL, _("Allow object export"),
                _("DCS's export switch for third-party tooling that reads the mission state."),
                GROUP_ANTICHEAT),
    ConfigField("advanced.allow_sensor_export", KIND_BOOL, _("Allow sensor export"),
                _("DCS's export switch for sensor data."), GROUP_ANTICHEAT),
    ConfigField("advanced.allow_ownship_export", KIND_BOOL, _("Allow ownship export"),
                _("DCS's export switch for the player's own aircraft."), GROUP_ANTICHEAT),
    ConfigField("advanced.allow_players_pool", KIND_BOOL, _("Allow players pool"),
                _("Players nobody slots are held in a pool instead of leaving."), GROUP_ANTICHEAT),
    ConfigField("advanced.disable_events", KIND_BOOL, _("Disable all events"),
                _("Turns DCS's own mission events off. The bot's event-driven plugins see nothing "
                  "while it is on."), GROUP_ANTICHEAT),
)

#: The declared editable fields keyed by their dotted key — the write route's own lookup, built from
#: the ONE declaration above so the form, the drift test and the submit path cannot disagree.
EDITABLE_BY_KEY: dict[str, ConfigField] = {field.key: field for field in EDITABLE_FIELDS}

#: The declared READ-ONLY fields — the same three keys the action's ``DCS_READONLY_FIELDS`` declares,
#: kept so the drift test can pin them. ``missionList`` and the two coalition password HASHES are
#: NEVER read into a view record or rendered: the Missions tab owns the list (through the
#: ``get_mission_list`` action, never off ``server.settings``), and the hashes stay secret.
READONLY_FIELDS: tuple[ConfigField, ...] = (
    ConfigField("missionList", KIND_SEQ, "Mission list",
                "The missions DCS cycles through, in order. Managed by the mission autoscan and "
                "validated at boot — a bad path is a boot failure — so this tab does not edit it.",
                GROUP_MISSION, readonly=True),
    ConfigField("advanced.bluePasswordHash", KIND_STR, "Blue coalition password (hash)",
                "", GROUP_MISSION, protection=PROTECTED, readonly=True),
    ConfigField("advanced.redPasswordHash", KIND_STR, "Red coalition password (hash)",
                "", GROUP_MISSION, protection=PROTECTED, readonly=True),
)

# ------------------------------------------------------------------------------------ coalitions
# THE COALITION FACE — a THIRD face of the tab, following the channels precedent exactly. The blue and
# red coalition passwords are NOT ``serverSettings.lua`` keys: the file carries only a HASH
# (``advanced.bluePasswordHash`` / ``redPasswordHash``, declared read-only + protected above), and the
# bot keeps the PLAINTEXT in its own database (``servers.blue_password`` / ``red_password``). So these
# two fields are declared here — never added to :data:`EDITABLE_FIELDS`, whose submit path diffs the
# DCS file and would write a plaintext key into it — and they render their own card and form.
#
# THE PROTECTION IS DECLARED per the one attribute (:attr:`ConfigField.protection`): both are
# :data:`UNPROTECTED` — Frank's call, the password is the value the operator may read back, not a
# secret the console hides. The keys are deliberately NOT ``advanced.*``: they are not
# ``serverSettings`` keys, and an ``advanced.`` prefix would invite a reader to look in the wrong place.

#: The two coalition tokens, in render order — the vocabulary the bot's own command uses
#: (``/password <server> [blue|red]``, ``plugins/scheduler/commands.py``).
COALITION_TOKENS: tuple[str, ...] = ("blue", "red")

#: ``(token, label, help)`` for each coalition, so the field table and the token map below are built
#: from ONE declaration and cannot drift apart.
_COALITION_SPECS: tuple[tuple[str, str, str], ...] = (
    ("blue", _("Blue coalition password"),
     _("The password a client picks to join the blue side. The bot stores it and asks DCS to apply it.")),
    ("red", _("Red coalition password"),
     _("The password a client picks to join the red side. The bot stores it and asks DCS to apply it.")),
)

#: The declared coalition fields — their own table, disjoint from :data:`EDITABLE_FIELDS` and
#: :data:`READONLY_FIELDS`. Keys are the form-field names the write route reads.
COALITION_FIELDS: tuple[ConfigField, ...] = tuple(
    ConfigField(f"{token}Password", KIND_STR, label, help_, GROUP_COALITIONS,
                protection=UNPROTECTED)
    for token, label, help_ in _COALITION_SPECS)

#: ``field key -> coalition token`` — the ONE mapping the read model, the page diff and the drift test
#: share, so a field key is never re-parsed for its token.
COALITION_OF_KEY: dict[str, str] = {
    f"{token}Password": token for token, _label, _help in _COALITION_SPECS}

#: The honest apply note for the coalition face. The bot's own ``setCoalitionPassword`` sends the
#: change to DCS while the server is up and writes the hash into ``serverSettings.lua`` while it is
#: down; either way the cleartext lands in the ``servers`` table. NO restart is needed by this page.
COALITION_APPLY_NOTE = ("A coalition password can be changed while the server is running, paused or "
                        "stopped: the bot sends it to DCS and keeps the cleartext in its database.")

#: Shown when a HASH is set in ``serverSettings.lua`` but the bot holds NO plaintext — the password was
#: set directly in DCS. An empty field alone would read as \"no password\" and be a lie.
COALITION_UNKNOWN_NOTE = _("A password is set outside DCSServerBot — the bot knows only its hash. Type "
                           "a new one to replace it.")

#: Shown when the bot HOLDS a plaintext but DCS currently carries no hash for that coalition (e.g. after
#: a file reset). Cheap honesty: the operator learns the two sides disagree.
COALITION_DB_ONLY_NOTE = _("The bot knows this password but DCS currently carries no hash for it — "
                           "saving here sets it in DCS too.")

#: The refusal a state that cannot take the change gets. Sent as the action's own sentence too, so the
#: console and a direct caller give the same answer.
COALITION_REFUSED_SENTENCE = ("The coalition passwords can only be changed while the server is "
                              "shutdown, stopped, running or paused. Wait for it to settle, then edit "
                              "here.")

#: The RAW statuses the coalition write is allowed in. Lower-cased, so this module needs no ``core``
#: import (the action's own ``WRITABLE_COALITION_STATES`` is the authority). ``LOADING`` /
#: ``SHUTTING_DOWN`` / ``UNREGISTERED`` are refused: the file-writing branch of the bot's own method
#: would race the process.
WRITABLE_COALITION_RAW: frozenset[str] = frozenset(
    {"shutdown", "stopped", "running", "paused"})

#: the RAW statuses a config write is allowed in. Lower-cased raw status values, so this module needs
#: no ``core.data.const.Status`` import: ``SHUTDOWN``, ``STOPPED`` and ``UNREGISTERED``. ``LOADING``
#: and ``SHUTTING_DOWN`` are transitional and are refused too.
WRITABLE_RAW: frozenset[str] = frozenset({"shutdown", "stopped", "unregistered"})

#: The stop-first explanation, verbatim from the action so the console and Discord give the same
#: answer. This page only explains; the write refusal itself is the action's.
STOP_FIRST_SENTENCE = ("The server must be stopped to change its configuration. "
                       "Stop it on the Servers page, then edit here.")

SET_SENTINEL = "set"
UNSET_SENTINEL = "not set"


def _split(key: str) -> tuple[str, str | None]:
    """``(root, leaf)`` for a dotted key, or ``(key, None)`` for a top-level one."""
    root, sep, leaf = key.partition(".")
    return (root, leaf) if sep else (key, None)


def _read_setting(settings, key: str):
    """The current value of *key* in *settings*, reading the nested ``advanced`` map one level deep."""
    if settings is None:
        return None
    root, leaf = _split(key)
    if leaf is None:
        return safe(lambda: settings.get(root), None)
    nested = safe(lambda: settings.get(root), None)
    return nested.get(leaf) if isinstance(nested, dict) else None


def current_setting(settings, key: str):
    """The public read of one setting — the write route diffs a submitted value against it.

    The write route must NOT re-implement "what is the current value": it reads it here, through the
    same nested-aware read the tab's own rows use, so a submitted value is compared with the value the
    page showed.
    """
    return _read_setting(settings, key)


def config_revision(server, *, servers_yaml_mtime=None) -> str:
    """The server's CONFIG REVISION — the pulse's signal.

    A cheap, IN-PROCESS stamp, read WITHOUT an RPC: the ``SettingsDict``'s own ``mtime`` hashed with
    the ``servers.yaml`` mtime. It changes when the DCS file is written (the settings write) and when
    the override block is rewritten, so a config write — either face — is observable. On an AGENT
    node the master holds a ``RemoteSettingsDict`` snapshot whose ``mtime`` only refreshes on the
    background poller's tick, so there the revision may move on that tick rather than on the response:
    the pulse is an honest maybe, bounded by ``AWAIT_CHANGE_SECONDS``.

    ``servers_yaml_mtime`` is passed in by the caller (the page reads it off the node's ``config_dir``
    with no RPC); a caller that cannot resolve it passes ``None`` and the revision is the settings
    mtime alone — still a correct signal for the DCS face, just without the override half.
    """
    settings = attr(server, "settings", None)
    own = str(attr(settings, "mtime", "") or "")
    other = "" if servers_yaml_mtime is None else str(servers_yaml_mtime)
    return f"{own}:{other}"


def _coerce(field: ConfigField, raw):
    """The value the page renders for *field*: a bool as a real bool, numbers as ints, else text.

    Never guesses: an absent value stays ``None`` (rendered as an empty control), and a value of an
    unexpected type is passed through as text so the operator sees what the file really holds rather
    than a coerced guess.
    """
    if raw is None:
        return None
    if field.kind == KIND_BOOL:
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if field.kind == KIND_INT:
        if isinstance(raw, bool):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return text(raw)


def bound_text(field: ConfigField) -> str:
    """The ``type · bound`` string a field row shows (e.g. ``int · 1–65535``, ``str · ≤2000``).

    One rendering of the declaration, so the template computes nothing and a bound has one owner.
    """
    kind = field.kind
    if kind == KIND_BOOL:
        return "bool"
    if kind == KIND_INT and field.choices:
        return "enum · " + " | ".join(str(choice) for choice in field.choices)
    if kind == KIND_INT:
        if field.minimum is not None and field.maximum is not None:
            return f"int · {field.minimum}–{field.maximum}"
        if field.minimum is not None:
            return f"int · ≥{field.minimum}"
        if field.maximum is not None:
            return f"int · ≤{field.maximum}"
        return "int"
    if kind == KIND_STR and field.maximum is not None:
        return f"str · ≤{field.maximum}"
    if kind == KIND_SEQ:
        return "seq[str]"
    return kind


# ------------------------------------------------------------------------------------ records


@dataclass(frozen=True, slots=True)
class FieldView:
    """One field as the form renders it: its declaration, its value, and the row's flags.

    ``value`` is the field's current value, EXCEPT for a PROTECTED field, whose value is never carried:
    there ``value`` is :data:`PROTECTED_SENTINEL` when a value is set and ``None`` when it is not, so
    the row can show the "a password is set" state without the browser ever seeing the value.
    ``protection`` carries the declaration to the template (``""`` = not a password;
    :data:`UNPROTECTED` = rendered masked with the eye; :data:`PROTECTED` = never rendered).
    ``overridden`` marks a key ALSO pinned by the server's ``servers.yaml`` ``serverSettings`` block:
    INFORMATION, never a prohibition.
    """
    key: str
    label: str
    kind: str
    help: str
    bound: str
    value: object = None
    #: the field's PASSWORD declaration, carried verbatim so the template branches on it alone
    #: (never on the field's key): ``""`` / :data:`UNPROTECTED` / :data:`PROTECTED`.
    protection: str = ""
    readonly: bool = False
    overridden: bool = False
    choices: tuple = ()
    #: the DECLARATION's own ceiling, carried as a number (``bound`` above is its human rendering,
    #: "str · ≤2000"). The multi-line control needs the number itself: the browser's own `maxlength`
    #: is what stops a pasted 5000-character description from being *submitted and refused*.
    maximum: int | None = None
    #: the field renders as a multi-line control (see :attr:`ConfigField.multiline`)
    multiline: bool = False


@dataclass(frozen=True, slots=True)
class ConfigGroup:
    """One meaning-group of the form: its label and its fields, in declaration order."""
    key: str
    label: str
    fields: tuple[FieldView, ...]


@dataclass(frozen=True, slots=True)
class ChannelOption:
    """One channel of the guild, as a select option: the id (as text) and the ``#name`` label.

    ``value`` is the channel id STRINGIFIED because a form value is text and this is what the
    browser posts; the write maps it back to an int. The label carries the ``#`` so a reader sees a
    channel name where a Discord reader would.
    """

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class ChannelGroup:
    """One ``<optgroup>`` of :class:`ChannelOption` — a Discord CATEGORY, or the uncategorized rest.

    ``label`` is the category name, or ``""`` for the channels that sit under no category. The
    group's channels are ordered the way Discord orders them (by position, then name).
    """

    label: str
    options: tuple[ChannelOption, ...]


@dataclass(frozen=True, slots=True)
class ChannelView:
    """One ``servers.yaml`` channel ROW — the id it holds and the guild's options to pick from.

    ``value`` is the channel id as text, or ``""`` when the channel is UNSET (:data:`CHANNEL_UNSET`)
    — the value the ``not set`` option is marked with. ``options`` is the guild's channel list,
    grouped by category (the SAME list on every row, read once per render). ``value_offered`` is
    False when a value is set but is NOT among the options (a channel since deleted, or one from
    another guild): the template then renders that value as its own option so an untouched form
    still posts it (unchanged) instead of silently reading as "not set".
    """

    key: str
    label: str
    help: str
    value: str
    required: bool = False
    options: tuple[ChannelGroup, ...] = ()
    value_offered: bool = True


@dataclass(frozen=True, slots=True)
class CoalitionView:
    """One coalition password ROW — the plaintext the bot knows, and the states it must be honest about.

    ``value`` is the cleartext the bot stored (``""`` when it knows none), shown per Frank's call —
    not hidden, unlike a PROTECTED field. ``hash_set`` says whether ``serverSettings.lua`` carries a
    hash for this coalition, read IN-PROCESS off the settings snapshot (no RPC). ``note`` is the honest
    sentence for the two states where ``value`` alone would read wrong: a hash set while the bot knows
    no plaintext (the password was set directly in DCS), and a plaintext the bot knows while DCS carries
    no hash (:data:`COALITION_UNKNOWN_NOTE` / :data:`COALITION_DB_ONLY_NOTE`, ``""`` otherwise).
    """

    key: str
    label: str
    help: str
    value: str
    protection: str = UNPROTECTED
    hash_set: bool = False
    note: str = ""


@dataclass(frozen=True, slots=True)
class ServerConfigView:
    """Everything the Configuration tab renders, in one record — the tab's only data source.

    ``serviceable`` is False for a server DCS cannot pick a config change up from (anything but
    STOPPED/SHUTDOWN); ``apply_note`` then carries the stop-first explanation. ``revision`` is a
    cheap in-process stamp (the ``SettingsDict``'s own mtime), read without an RPC.
    """
    name: str
    status: StatusView
    groups: tuple[ConfigGroup, ...]
    channels: tuple[ChannelView, ...]
    overridden: tuple[str, ...]
    serviceable: bool
    apply_note: str
    revision: str
    group_counts: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    #: the channels face's own facts. ``channel_apply_note`` is the honest "when it
    #: applies" sentence; ``channel_options_available`` is whether the guild's channel list could be
    #: read at all (a bot not connected to a guild offers only "not set"); ``central_admin`` is
    #: whether ``bot.yaml`` defines a central admin channel, i.e. whether the admin row is ABSENT.
    channel_apply_note: str = CHANNEL_APPLY_NOTE
    channel_options_available: bool = False
    central_admin: bool = False
    #: whether the channels write is DENIED to the viewer (a manager may not write the channels at
    #: all): the rows are NOT BUILT in that case and the whole card is omitted. False for an Admin
    #: (and every non-manager).
    channels_denied: bool = False
    #: THE COALITION FACE. ``coalitions`` is EMPTY when the read could not be taken (the action is
    #: absent, or the caller is refused) — the page then renders no card — and holds the two rows
    #: otherwise. ``coalitions_serviceable`` is whether the current state may take a coalition change
    #: (:data:`WRITABLE_COALITION_RAW`); ``coalition_apply_note`` is the reason when it may not.
    coalitions: tuple[CoalitionView, ...] = field(default_factory=tuple)
    coalitions_serviceable: bool = True
    coalition_apply_note: str = ""


# ------------------------------------------------------------------------------------ reading


def _override_keys(locals_) -> set[str]:
    """The dotted keys the server's ``servers.yaml`` entry pins (``locals['serverSettings']``)."""
    overrides: set[str] = set()
    block = (locals_ or {}).get("serverSettings")
    if not isinstance(block, dict):
        return overrides
    for key, value in block.items():
        if key == "advanced" and isinstance(value, dict):
            overrides.update(f"advanced.{leaf}" for leaf in value)
        else:
            overrides.add(key)
    return overrides


def _set(raw) -> bool:
    """Whether a stored value reads as SET — the protected field's presence test (never its value).

    Mirrors the action's own redaction rule: an absent value and a whitespace-only string both mean
    "not set". The protected branch uses this to decide whether to show the "a password is set" state,
    so the value itself is never read into a view record.
    """
    if raw is None:
        return False
    if isinstance(raw, str):
        return bool(raw.strip())
    return bool(raw)


def _field_view(field: ConfigField, settings, overridden: set[str]) -> FieldView:
    """One editable field's row.

    An UNPROTECTED password carries its value like any other string, so the field can show it. A
    PROTECTED password carries NO value at all: its row holds :data:`PROTECTED_SENTINEL` when a value
    is set (and ``None`` when none is) — nothing about the value reaches the page.

    A DENIED key never reaches here: :func:`server_config_view` omits it before building the group,
    so a manager's view simply has no row for it (the refusal itself is the action's, server-side).
    """
    raw = _read_setting(settings, field.key)
    if field.protection == PROTECTED:
        value = PROTECTED_SENTINEL if _set(raw) else None
    else:
        value = _coerce(field, raw)
    return FieldView(key=field.key, label=field.label, kind=field.kind, help=field.help,
                     bound=bound_text(field), value=value, protection=field.protection,
                     readonly=field.readonly,
                     overridden=field.key in overridden, choices=field.choices,
                     maximum=field.maximum, multiline=field.multiline)


def live_bot():
    """The running bot, through the service registry, or ``None``. Never raises.

    The ONE resolution of the bot object for the channels face, and it is the SAME lookup the rest
    of the console uses (``auth.discord_oauth.current_bot`` / ``core.actions._resolve_bot`` /
    ``readmodels.source.resolve_source``): ``ServiceRegistry.get(BotService).bot``. It imports
    ``services`` INSIDE the function because the bot object is a RUNTIME fact — ``None`` early, after
    a takeover and on a headless install — and because ``readmodels`` must not bind it at import
    time. A test injects a stub bot by monkeypatching THIS function (or by passing ``bot`` to
    :func:`server_config_view`), which is exactly how the channel list is exercised without a
    Discord connection.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.bot.service import BotService

        return getattr(ServiceRegistry.get(BotService), "bot", None)
    except Exception:  # noqa: BLE001 - a missing/early registry must not break a render
        return None


def central_admin_channel(bot) -> str:
    """The CENTRAL admin channel id declared in ``bot.yaml``, as text — or ``""`` when none is.

    The one fact the hiding rule depends on: when a central admin channel is set the per-server admin
    channel is not per-server at all, so its row is ABSENT. It is read straight off the bot's OWN
    loaded configuration — ``bot.locals['channels']['admin']`` — which the bot itself reads to resolve
    the effective admin channel (``plugins/scheduler/views.py``, ``core/data/impl/serverimpl.py``).
    Read tolerantly: a partial bot object, a ``locals`` that is not a mapping, or a channel map that is
    not one all yield ``""`` (the truthful "no central channel") rather than raising on a render.
    """
    locals_ = getattr(bot, "locals", None)
    if not isinstance(locals_, dict):
        return ""
    channels = locals_.get("channels")
    if not isinstance(channels, dict):
        return ""
    return text(channels.get("admin"))


def _is_category(channel) -> bool:
    """Whether *channel* is a Discord CATEGORY — the grouping, never a target.

    Duck-typed on ``channel.type.name == "category"`` (the repo's own idiom, read without importing
    ``discord``): a category channel has ``type`` :class:`discord.ChannelType.category`, whose name
    is ``"category"``. A stub that carries no ``type`` is treated as a normal channel, so the
    read model can be driven with ``SimpleNamespace`` stand-ins.
    """
    kind = getattr(channel, "type", None)
    return str(getattr(kind, "name", "") or "") == "category"


def _position(value) -> int:
    """A sortable channel position from *value*, tolerant of a missing/non-numeric one (``0``)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def guild_channel_groups(bot) -> tuple[ChannelGroup, ...]:
    """The guild's channels, grouped by CATEGORY — read IN-PROCESS, never by a node call.

    This is the console's standing rule made concrete: the console runs inside the bot's own
    process, so ``bot.guilds[*].channels`` is already cached on the Discord client and reading it
    costs NO RPC and NO Discord API request — a render that called the API would turn every page
    paint into a round-trip. The category channels themselves are SKIPPED (they are the ``<optgroup>``
    labels); ordering is the guild's own (position, then name), so the picker reads like the Discord
    sidebar.

    Tolerant throughout, like every read model here: a bot that is ``None`` (early start, headless,
    after a takeover), a guild with no readable channels, and a channel with no id/name each simply
    contribute nothing — the result is ``()``, never an exception.
    """
    by_group: dict[str, list[tuple[int, str, str]]] = {}
    for guild in (getattr(bot, "guilds", None) or ()):
        for channel in (getattr(guild, "channels", None) or ()):
            if _is_category(channel):
                continue
            channel_id = attr(channel, "id", None)
            name = attr(channel, "name", None)
            if channel_id is None or not name:
                continue
            group = attr(attr(channel, "category", None), "name", "") or ""
            by_group.setdefault(str(group), []).append(
                (_position(attr(channel, "position", 0)), str(name), str(channel_id)))
    groups: list[ChannelGroup] = []
    for group_name in sorted(by_group):
        ordered = sorted(by_group[group_name], key=lambda row: (row[0], row[1].casefold()))
        groups.append(ChannelGroup(
            label=group_name,
            options=tuple(ChannelOption(value=value, label=f"#{name}")
                          for _pos, name, value in ordered)))
    return tuple(groups)


def _channel_options_index(groups: tuple[ChannelGroup, ...]) -> set[str]:
    """Every option VALUE in *groups* — what "the value is offered" means for a row."""
    return {option.value for group in groups for option in group.options}


def _channels(locals_, *, groups: tuple[ChannelGroup, ...] = (),
              central: str = "", channels_denied: bool = False) -> tuple[ChannelView, ...]:
    """The ``servers.yaml`` channel rows, each carrying the guild's options.

    THE CENTRAL-ADMIN RULE lives here (the design decision, not a template trick): when *central* is
    truthy — ``bot.yaml`` defines a central admin channel — the ``admin`` row is NOT BUILT AT ALL, so
    the page cannot render it and no CSS/disabled attribute is involved. In that configuration the
    admin channel is not a per-server setting, so offering it would be a lie. When *central* is
    empty the row is offered normally. Both branches are exercised by the suite.

    ``channels_denied`` is the MANAGER deny-list's verdict on the channels write as a whole: when it
    is true NO ROW IS BUILT AT ALL — the page cannot render a picker the action would refuse, and the
    card is omitted (the same rule the action enforces: a manager may not write the channels at all).
    """
    if channels_denied:
        # a manager may not write the channels at all: the rows are not built, so the page has
        # nothing to render and no control can be offered — never disabled, never merely hidden.
        return ()
    channels = (locals_ or {}).get("channels")
    channels = channels if isinstance(channels, dict) else {}
    specs = (
        ("admin", _("Admin channel"),
         _("The channel where admin commands for this server are accepted."), True),
        ("status", _("Status channel"),
         _("Where the bot posts joins, leaves and mission changes."), True),
        ("chat", _("Chat channel"), _("Relays in-game chat. Not set means the bot relays nowhere."), False),
    )
    offered_values = _channel_options_index(groups)
    out: list[ChannelView] = []
    for key, label, help_, required in specs:
        if key == CHANNEL_ADMIN_KEY and central:
            # a central admin channel is defined in bot.yaml: this setting is not per-server in that
            # configuration, so the row is ABSENT — never hidden with CSS, never rendered disabled.
            continue
        value = channel_value_text(channels.get(key))
        out.append(ChannelView(key=f"channels.{key}", label=label, help=help_, value=value,
                               required=required, options=groups,
                               value_offered=(not value) or value in offered_values))
    return tuple(out)


def coalition_views(data) -> tuple[CoalitionView, ...]:
    """The two coalition ROWS, built from the read action's payload — or ``()`` when there is none.

    ``data`` is ``get_server_coalitions``'s ``result.data``:
    ``{"blue": …, "red": …, "blue_hash_set": …, "red_hash_set": …}``. A payload that is not a mapping
    (the action was absent, or the caller was refused) yields ``()``, so the page renders no card
    rather than two rows reading \"no password\" for a server nobody could read. The plaintext is
    carried VERBATIM (no coercion): a value the database holds is what the operator sees.
    """
    if not isinstance(data, dict):
        return ()
    out: list[CoalitionView] = []
    for field in COALITION_FIELDS:
        token = COALITION_OF_KEY[field.key]
        raw = data.get(token)
        value = "" if raw is None else text(raw)
        hash_set = bool(data.get(f"{token}_hash_set"))
        note = ""
        if not value and hash_set:
            note = COALITION_UNKNOWN_NOTE     # set in DCS: the bot knows only the hash
        elif value and not hash_set:
            note = COALITION_DB_ONLY_NOTE     # the bot knows it; DCS carries no hash
        out.append(CoalitionView(key=field.key, label=field.label, help=field.help, value=value,
                                 protection=field.protection, hash_set=hash_set, note=note))
    return tuple(out)


def coalition_keys() -> tuple[str, ...]:
    """The declared coalition keys, in render order — the drift test's console-side set."""
    return tuple(field.key for field in COALITION_FIELDS)


def server_config_view(server, *, servers_yaml_mtime=None, bot=None,
                       denied=None, channels_denied: bool = False,
                       coalitions=None) -> ServerConfigView:
    """The Configuration tab's whole data source, read from the IN-PROCESS snapshot.

    NO await, NO file open, NO node RPC: only ``server.settings`` (the ``SettingsDict`` loaded at
    startup), ``server.locals`` (``read_locals()``), ``server.status`` and ``server.name`` are touched
    — the objects the master already holds. ``servers_yaml_mtime`` is an mtime the CALLER already read
    (no RPC) and is folded into the revision only.

    ``bot`` is the running bot object, read for the CHANNELS face alone: its guilds supply the
    channel options (in-process, no Discord API call — see :func:`guild_channel_groups`) and its
    loaded configuration decides the central-admin rule (:func:`central_admin_channel`). When it is
    ``None`` it is resolved through :func:`live_bot`; a process with no bot renders the channels
    rows with only the "not set" option, never an error. Never raises for a half-initialised server:
    a missing piece renders as an empty control or an empty group, not a 500.

    ``denied`` is the MANAGER deny-list — the keys a manager may not write (its values are the
    sentences the ACTION refuses with, unused here). A denied key is NOT BUILT: its row is absent
    from the view entirely, so the page has nothing to render (the refusal stays server-side, in the
    action). ``channels_denied`` is the same verdict for the channels write as a whole: true leaves
    the channels rows unbuilt and the whole card omitted. Both are ``False``/``None`` for an Admin
    (and every non-manager), whose view is unchanged.
    """
    settings = attr(server, "settings", None)
    locals_ = attr(server, "locals", None) or {}
    overridden = _override_keys(locals_)
    deny_keys = set(denied or {})

    groups: list[ConfigGroup] = []
    for group_key in GROUP_ORDER:
        fields = tuple(_field_view(field, settings, overridden)
                       for field in EDITABLE_FIELDS
                       if field.group == group_key and field.key not in deny_keys)
        if fields:
            groups.append(ConfigGroup(key=group_key, label=GROUP_LABELS[group_key], fields=fields))

    status = status_view(attr(server, "status", None))
    serviceable = (status.raw or "").strip().lower() in WRITABLE_RAW
    coalitions_serviceable = (status.raw or "").strip().lower() in WRITABLE_COALITION_RAW

    revision = config_revision(server, servers_yaml_mtime=servers_yaml_mtime)

    # THE CHANNELS FACE: the guild's options and the central-admin rule, both from the SAME bot
    # object, read once for the whole tab.
    if bot is None:
        bot = live_bot()
    channel_groups = guild_channel_groups(bot)
    central = central_admin_channel(bot)

    return ServerConfigView(
        name=text(attr(server, "name", None)),
        status=status,
        groups=tuple(groups),
        channels=_channels(locals_, groups=channel_groups, central=central,
                           channels_denied=channels_denied),
        overridden=tuple(sorted(overridden)),
        serviceable=serviceable,
        apply_note="" if serviceable else STOP_FIRST_SENTENCE,
        revision=revision,
        group_counts=tuple((group.key, len(group.fields)) for group in groups),
        channel_options_available=bool(channel_groups),
        central_admin=bool(central),
        channels_denied=bool(channels_denied),
        coalitions=coalition_views(coalitions),
        coalitions_serviceable=coalitions_serviceable,
        coalition_apply_note="" if coalitions_serviceable else COALITION_REFUSED_SENTENCE,
    )


def editable_keys() -> tuple[str, ...]:
    """The declared editable keys, in declaration order — the drift test's console-side set."""
    return tuple(field.key for field in EDITABLE_FIELDS)


def readonly_keys() -> tuple[str, ...]:
    """The declared read-only keys (``missionList`` + the two hashes) — pinned by the drift test."""
    return tuple(field.key for field in READONLY_FIELDS)
