"""The admin website's two Discord surfaces: the read-only doctor (``/webadmin status``) and the
guided writer (``/webadmin setup``).

The website is served by ``services/webservice`` only while
``config/services/webservice.yaml`` exists, and every part of that file is read when the process
starts. :func:`status_rows` turns the file plus the service's runtime state into one row per gate —
in the order an operator meets them — and :func:`status_embed` renders those rows for Discord.

:func:`mutate_for` is the one function that changes that file from Discord: a pure change applied by
``configwrite.patch_config_file``, so the operator's comments, key order and every key the wizard does
not own survive. The Discord client secret never enters the document — it goes to the node's secret
store under the key the OAuth backend reads (:func:`store_secret`), and a local account is stored as
the hash of the password the operator entered, never as the password itself.

Each login mode collects its whole form in ONE ``utils.ConfigModal`` built from a per-mode config
dict (:func:`mode_config`): the mode picker decides which fields exist, ``ConfigModal`` labels and
bounds them. A mode may never ask for more than :data:`MODAL_LIMIT` fields — that is all one Discord
modal carries.

``status_rows`` performs no I/O: the caller loads the document and fills :class:`Facts`, so the
checks run without a bot or a running service. Nothing here writes, and no row ever prints a
credential value — an unset key may be named, never a value that is stored.
"""
from __future__ import annotations

import asyncio
import discord
import ipaddress
import logging
import os
import secrets

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from discord import app_commands
from discord.ui import Button, Select
from typing import Any, Iterable

from urllib.parse import urlsplit

from core import DEFAULT_TAG, Group, ServiceProxy, ServiceRegistry, command, get_translation, utils
from core.utils import configwrite
from core.utils.yaml_helper import new_yaml
from ruamel.yaml.error import YAMLError
from services.webservice.auth.local import BUILTIN_ROLE_NAMES, known_role_names
from services.webservice.service import WebService
from services.webservice.setup.console import MAX_PASSWORD_LENGTH, hash_password
from services.webservice.setup.discord import CLIENT_SECRET_KEY

_ = get_translation(__name__.split('.')[1])

log = logging.getLogger(__name__)

#: the configuration file that switches the service on, as the operator sees it
CONFIG_FILE = 'config/services/webservice.yaml'
#: the same file below a node's configuration directory
CONFIG_PARTS = ('services', 'webservice.yaml')
#: the schema the write is validated against, exactly as the bot names it
CONFIG_SCHEMA = 'services/webservice/schemas/webservice_schema.yaml'

#: the port the wizard offers; the schema's own default
DEFAULT_PORT = 9876
MIN_PORT = 1024
MAX_PORT = 65535
#: the interface every install is bound to unless the file already names another one
DEFAULT_LISTEN = '0.0.0.0'

#: how many characters a generated session key carries
SECRET_BYTES = 48

#: the shortest local password the wizard accepts, and the longest the login verifies
MIN_PASSWORD = 12
MAX_PASSWORD = MAX_PASSWORD_LENGTH

#: the most components one Discord modal carries
MODAL_LIMIT = 5

#: the most options one Discord select carries
SELECT_LIMIT = 25

#: the role a local account needs to reach the console at all
ADMIN_ROLE = 'Admin'

#: the longest username one account entry may carry
MAX_USERNAME = 64

#: the plugin that caches ``WebService.app`` and re-registers its router on that object once
RESTAPI_PLUGIN = 'restapi'

OK = 'ok'
WARN = 'warn'
FAIL = 'fail'

_GLYPH = {OK: '✅', WARN: '⚠️', FAIL: '❌'}
_COLOUR = {OK: 0x2ECC71, WARN: 0xF1C40F, FAIL: 0xE74C3C}
_SEVERITY = {OK: 0, WARN: 1, FAIL: 2}

#: the login backends, in the order the rows name them
_BACKENDS = ('local', 'discord', 'breakglass')

#: addresses that resolve to the machine the browser already runs on
_LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})

#: the address ranges that are never reachable from the internet: RFC1918, ULA and link-local.
#: ``ipaddress.is_private`` is not used — it also answers ``True`` for documentation and other
#: special-use blocks (203.0.113.0/24 among them), which a plain-http check must not excuse.
_OWN_NETWORKS = tuple(ipaddress.ip_network(text) for text in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',        # RFC1918
    'fc00::/7',                                             # unique local
    '169.254.0.0/16', 'fe80::/10',                          # link-local
))


@dataclass(frozen=True)
class Facts:
    """The runtime half of the picture, collected by the caller.

    ``file_present`` and ``port``/``url`` describe what the file and the running process actually
    are; ``service_present``/``service_running`` distinguish a service that was never loaded from
    one that is loaded but did not start. A ``ServiceProxy`` is not a local running service and
    never reaches this dataclass as one.
    """

    file_present: bool = False
    service_present: bool = False
    service_running: bool = False
    is_master: bool = False
    port: int | None = None
    url: str | None = None


@dataclass(frozen=True)
class Row:
    """One check: its state (``ok``/``warn``/``fail``), the check's name and what is true."""

    state: str
    label: str
    detail: str


def _block(config: Mapping | None) -> Mapping | None:
    """The ``DEFAULT`` block of a parsed document — the block the service itself reads.

    A caller that hands over the block directly is answered unchanged, so both shapes work.
    """
    if not isinstance(config, Mapping):
        return None
    default = config.get(DEFAULT_TAG)
    return default if isinstance(default, Mapping) else config


def _enabled(block: Mapping, *path: str) -> bool:
    """Whether the key at *path* is literally ``true``."""
    node: object = block
    for key in path:
        node = node.get(key) if isinstance(node, Mapping) else None
    return node is True


def _auth(block: Mapping) -> Mapping:
    """The ``auth`` block of *block*, empty when it is absent or not a mapping."""
    auth = block.get('auth')
    return auth if isinstance(auth, Mapping) else {}


def _origin(value: object) -> tuple[str | None, str | None]:
    """``(scheme, host)`` of a public base URL; ``(None, None)`` when it is not one."""
    if not isinstance(value, str):
        return None, None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None, None
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return None, None
    return parts.scheme, parts.hostname


def _loopback_host(host: str) -> bool:
    """Whether *host* resolves to the machine the browser already runs on."""
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _own_network(host: str) -> bool:
    """Whether *host* is an address inside the operator's own network.

    RFC1918, ULA and link-local addresses are not reachable from the internet, so a plain-http
    address on them never crosses it. A host NAME answers ``False``: it may resolve to anything.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _OWN_NETWORKS
               if network.version == address.version)


def _file_row(config: Mapping | None, facts: Facts) -> Row:
    if not facts.file_present or config is None:
        return Row(FAIL, _("Configuration file"),
                   _("{} is missing — no website service exists without it. Run /webadmin setup "
                     "to create it.").format(CONFIG_FILE))
    return Row(OK, _("Configuration file"), _("{} found.").format(CONFIG_FILE))


def _frontend_row(block: Mapping) -> Row:
    if _enabled(block, 'frontend'):
        return Row(OK, _("Frontend"),
                   _("frontend is on — the pages, the login and the session cookie are installed."))
    return Row(FAIL, _("Frontend"),
               _("frontend is off — the service serves the REST API only: no page, no login, no "
                 "session cookie. Set frontend: true."))


def _login_row(block: Mapping) -> Row:
    names = {'local': _("username and password"), 'discord': _("Discord"),
             'breakglass': _("break-glass credential")}
    active = [names[backend] for backend in _BACKENDS if _enabled(block, 'auth', backend, 'enabled')]
    if active:
        return Row(OK, _("Login"), _("Login is enabled for: {}.").format(', '.join(active)))
    return Row(FAIL, _("Login"),
               _("The login block is empty — every page answers 403. Enable at least one of "
                 "auth.local, auth.discord or auth.breakglass."))


def _session_row(block: Mapping) -> Row:
    session = block.get('session')
    secret = session.get('secret') if isinstance(session, Mapping) else None
    if secret is None or secret == '':
        return Row(WARN, _("Session secret"),
                   _("session.secret is not set — a random key is generated at every start, so "
                     "everyone is signed out when the node restarts. Set a secret of at least 32 "
                     "characters."))
    length = len(str(secret))
    if length < 32:
        return Row(WARN, _("Session secret"),
                   _("session.secret is only {} characters long — a key shorter than 32 "
                     "characters is refused at startup. Use at least 32.").format(length))
    return Row(OK, _("Session secret"),
               _("session.secret is set ({} characters) — sessions survive a node restart."
                 ).format(length))


def _public_url_row(block: Mapping) -> Row:
    value = _auth(block).get('public_base_url')
    if value is None or value == '':
        return Row(FAIL, _("Public address"),
                   _("auth.public_base_url is not set — redirect URIs are built from it, so a "
                     "Discord login cannot return. Set the address your users open, e.g. "
                     "https://dcs.example.com."))
    scheme, host = _origin(value)
    if scheme is None:
        return Row(FAIL, _("Public address"),
                   _("auth.public_base_url is not an absolute http(s) address ({}) — redirect URIs "
                     "are built from it.").format(value))
    if host is not None and _loopback_host(host):
        return Row(WARN, _("Public address"),
                   _("auth.public_base_url points at this machine only ({}) — a Discord login "
                     "comes back to this machine and nowhere else. Set the address your users "
                     "open.").format(value))
    if scheme == 'http' and host is not None and not _own_network(host):
        return Row(WARN, _("Public address"),
                   _("auth.public_base_url is {} — plain http carries the login page and the "
                     "session cookie in cleartext. Set an https address, or an address inside your "
                     "own network.").format(value))
    return Row(OK, _("Public address"),
               _("auth.public_base_url is {} — redirect URIs are built from it.").format(value))


def _service_row(facts: Facts) -> Row:
    if not facts.is_master:
        return Row(WARN, _("Service"),
                   _("The website runs on the master node only — this is not the master, so "
                     "nothing is served here. Run the check on the master."))
    if facts.service_running and facts.port:
        return Row(OK, _("Service"),
                   _("The service is running on this node, port {}.").format(facts.port))
    if facts.service_present:
        return Row(FAIL, _("Service"),
                   _("The service is loaded but not running, so nothing answers on a port. Restart "
                     "the node."))
    return Row(FAIL, _("Service"),
               _("The service is not loaded on this node — {} is read at startup. Restart the "
                 "node.").format(CONFIG_FILE))


def _next_step_row(facts: Facts) -> Row:
    restart = _("A configuration change applies on the next node restart, not on a page reload.")
    if facts.url:
        return Row(OK, _("Next step"), _("Open {}.").format(facts.url) + ' ' + restart)
    return Row(WARN, _("Next step"), _("The address is not known yet.") + ' ' + restart)


def status_rows(config: Mapping | None, facts: Facts) -> list[Row]:
    """One row per check, first blocker first; nothing is read, written or fetched.

    *config* is the parsed ``config/services/webservice.yaml`` document (or its ``DEFAULT`` block)
    and ``None`` when the file is absent or unreadable. The checks that read the file are skipped
    while it is absent: their rows would only restate the missing file.
    """
    rows = [_file_row(config, facts)]
    block = _block(config)
    if block is not None:
        rows += [
            _frontend_row(block),
            _login_row(block),
            _session_row(block),
            _public_url_row(block),
        ]
    rows += [_service_row(facts), _next_step_row(facts)]
    return rows


def status_embed(rows: Iterable[Row]) -> discord.Embed:
    """Render *rows* as one embed, coloured by the worst state it carries."""
    rows = list(rows)
    worst = max((row.state for row in rows), key=lambda state: _SEVERITY.get(state, 0),
                default=OK)
    embed = discord.Embed(title=_("Website status"), colour=_COLOUR.get(worst, _COLOUR[OK]))
    for row in rows:
        embed.add_field(name=f"{_GLYPH.get(row.state, '')} {row.label}", value=row.detail,
                        inline=False)
    return embed


def read_config(config_dir: str) -> Mapping | None:
    """The parsed website configuration below *config_dir*, or ``None`` when it cannot be read."""
    path = os.path.join(config_dir, *CONFIG_PARTS)
    try:
        with open(path, encoding='utf-8') as config_file:
            return new_yaml().load(config_file)
    except (OSError, YAMLError):
        return None


def read_facts(node, config: Mapping | None) -> Facts:
    """The runtime half of the picture: the local service's state and the address to open.

    A service reached through a ``ServiceProxy`` runs on another node and is reported as absent
    here — on this process nothing is listening.
    """
    file_present = os.path.isfile(os.path.join(node.config_dir, *CONFIG_PARTS))
    service = ServiceRegistry.get(WebService)
    local = service if service is not None and not isinstance(service, ServiceProxy) else None
    running = False
    port = None
    if local is not None:
        running = bool(local.is_running())
        if running:
            try:
                port = local.get_ports()['WebService'].port
            except (AttributeError, KeyError, TypeError):
                port = None
    block = _block(config)
    url = None
    if block is not None:
        value = _auth(block).get('public_base_url')
        if _origin(value)[0] is not None:
            url = str(value).strip().rstrip('/')
    if url is None and port:
        url = f"http://127.0.0.1:{port}"
    return Facts(
        file_present=file_present,
        service_present=local is not None,
        service_running=running,
        is_master=bool(getattr(node, 'master', False) or getattr(node, 'claimed_master', False)),
        port=port,
        url=url,
    )


# ------------------------------------------------------------------------------------ the answers

def config_path(config_dir: str | os.PathLike) -> str:
    """The website configuration file below *config_dir*."""
    return os.path.join(str(config_dir), *CONFIG_PARTS)


def parse_port(value: object) -> int | None:
    """*value* as a port number in ``1024..65535``, or ``None``."""
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return port if MIN_PORT <= port <= MAX_PORT else None


def listen_address(config: Mapping | None) -> str:
    """The listen address to keep: the one the file already has, else the wizard's default."""
    block = _block(config)
    value = block.get('listen') if block is not None else None
    text = value.strip() if isinstance(value, str) else ''
    return text or DEFAULT_LISTEN


def configured_port(config: Mapping | None) -> int:
    """The port the wizard starts from: the one the file already has, else the schema's default."""
    block = _block(config)
    value = block.get('port') if block is not None else None
    port = parse_port(value)
    return port if port is not None else DEFAULT_PORT


def configured_public_url(config: Mapping | None, host: str | None, port: int) -> str:
    """The address the wizard starts from: the file's own, else the node's public address."""
    block = _block(config)
    value = _auth(block).get('public_base_url') if block is not None else None
    text = str(value).strip() if isinstance(value, str) else ''
    return text or default_public_url(host, port)


def default_public_url(host: str | None, port: int) -> str:
    """The address an install without a configured one is opened at: this node's public address."""
    return f"http://{(host or '').strip() or 'localhost'}:{int(port)}"


def master_name(node) -> str | None:
    """The name of the cluster's master node, or ``None`` when this node cannot read one."""
    for candidate in (getattr(node, 'all_nodes', None) or {}).values():
        if getattr(candidate, 'master', False):
            return str(getattr(candidate, 'name', '') or '') or None
    return None


@dataclass(frozen=True)
class SetupPlan:
    """What the wizard writes: the address, the port, and whichever login backends were set up.

    The local account arrives as an already-hashed credential: the plaintext password exists only in
    the reply that shows it, never in a plan, a document or a log line.
    """

    port: int = DEFAULT_PORT
    public_base_url: str = ''
    listen: str = DEFAULT_LISTEN
    #: write ``auth.discord`` (the OAuth backend, pointing at the secret store key)
    discord: bool = False
    #: the Discord application id; the OAuth client id
    client_id: int | None = None
    #: write or extend ``auth.local``
    local: bool = False
    username: str = ''
    password_hash: str = ''


def _writable(mapping: object) -> MutableMapping:
    """*mapping* when it can be changed in place, else an empty document that replaces it."""
    return mapping if isinstance(mapping, MutableMapping) else {}


def _write_block(document: Any) -> MutableMapping:
    """The ``DEFAULT`` block of *document*, created empty when the file does not have one yet."""
    block = _writable(document.get(DEFAULT_TAG))
    document[DEFAULT_TAG] = block
    return block


def session_secret() -> str:
    """A new session key, long enough for the schema's own minimum."""
    return secrets.token_urlsafe(SECRET_BYTES)


def local_users(config: Mapping | None) -> list:
    """The ``auth.local.users`` list of *config*, empty when the document has none."""
    block = _block(config)
    local = _auth(block).get('local') if block is not None else None
    users = local.get('users') if isinstance(local, Mapping) else None
    return users if isinstance(users, list) else []


def taken_username(config: Mapping | None, username: str) -> str | None:
    """*username* when the document already has a local account of that name, else ``None``."""
    for user in local_users(config):
        if isinstance(user, Mapping) and str(user.get('username', '')).strip() == username:
            return username
    return None


def duplicate_account_text(username: str) -> str:
    """The refusal for a local account whose name the document already has."""
    return _("The local account '{}' already exists — nothing was written. Change its password "
             "with the user password command instead.").format(username)


def mutate_for(plan: SetupPlan) -> Callable[[Any], None]:
    """The change ``configwrite.patch_config_file`` applies for *plan*, as a pure function.

    Only the keys the wizard owns are touched, and the merge is in place: ``dashboard``, ``access``,
    ``debug``, every comment and the operator's key order survive. ``session.secret`` is generated
    only when it is absent or empty — an operator's existing key is never replaced (that would sign
    everyone out on the restart this write asks for). A local account is appended to the users list;
    a name the document already has is never replaced.

    Raises ``ValueError`` for a Discord plan without a client id: the OAuth backend cannot use a
    block whose ``client_id`` is empty, and a document naming ``0`` would be dead on arrival. Raises
    the same for a local account whose name already exists: that hash belongs to a working account
    and is changed by a command of its own, not by this wizard.
    """
    if plan.discord and not plan.client_id:
        raise ValueError('a Discord plan needs a client id')
    client_id = int(plan.client_id) if plan.client_id else None

    def mutate(document: Any) -> None:
        block = _write_block(document)
        block['listen'] = plan.listen
        block['port'] = int(plan.port)
        block['frontend'] = True
        auth = _writable(block.get('auth'))
        block['auth'] = auth
        auth['public_base_url'] = plan.public_base_url
        session = _writable(block.get('session'))
        block['session'] = session
        if not session.get('secret'):
            session['secret'] = session_secret()
        if plan.discord:
            auth['discord'] = {'enabled': True, 'client_id': client_id,
                               'client_secret_key': CLIENT_SECRET_KEY}
        if plan.local:
            local = _writable(auth.get('local'))
            auth['local'] = local
            users = local.get('users')
            if not isinstance(users, list):
                users = local['users'] = []
            if taken_username(document, plan.username):
                raise ValueError('a local account of that name already exists')
            users.append({'username': plan.username, 'password_hash': plan.password_hash,
                          'roles': ['Admin']})
            local['enabled'] = True

    return mutate


def store_secret(config_dir: str | os.PathLike, secret: str) -> None:
    """Store the Discord client secret in the node's secret store under the OAuth backend's key.

    The value never reaches the configuration file: the document only ever names
    :data:`CLIENT_SECRET_KEY`. Raises ``ValueError`` when there is nothing to store.
    """
    if not secret:
        raise ValueError('no client secret to store')
    os.makedirs(os.path.join(str(config_dir), '.secret'), exist_ok=True)
    utils.set_password(CLIENT_SECRET_KEY, secret, str(config_dir))


# --------------------------------------------------------------------------------------- the wizard

def _login_label(mode: str | None) -> str:
    """The sign-in mode as a reader sees it."""
    if mode == 'discord':
        return _("Discord login")
    if mode == 'local':
        return _("username and password")
    if mode == 'both':
        return _("Discord login and a local account")
    if mode == 'manual':
        return _("Manual")
    return _("Not chosen yet — pick a sign-in mode from the list below.")


def _next_step(mode: str | None, *, complete: bool) -> str:
    """What the reader has to do next, one sentence."""
    if mode is None:
        return _("Pick how you sign in from the list below.")
    if not complete:
        return _("Fill in the form the wizard opened.")
    return _("Everything is collected — press the button below to write the configuration.")


# ---------------------------------------------------------------------------------------- the forms

def _field_specs() -> dict[str, dict]:
    """Every answer the wizard can ask for, keyed by the name it is stored under."""
    return {
        'port': {'type': int, 'label': _("Port"), 'placeholder': str(DEFAULT_PORT),
                 'default': DEFAULT_PORT, 'required': True, 'min_length': 4, 'max_length': 5},
        'public_base_url': {'type': str, 'label': _("Public address"),
                            'placeholder': 'https://dcs.example.com', 'required': True,
                            'max_length': 256},
        'client_secret': {'type': str, 'label': _("Client secret"), 'required': True,
                          'max_length': 256},
        'username': {'type': str, 'label': _("Username"), 'default': 'admin', 'required': True,
                     'min_length': 1, 'max_length': 64},
        'password': {'type': str, 'label': _("Password"), 'required': True,
                     'min_length': MIN_PASSWORD, 'max_length': MAX_PASSWORD},
    }


#: the answers each login mode asks for, in the order its modal shows them
MODE_FIELDS = {
    'discord': ('port', 'public_base_url', 'client_secret'),
    'local': ('port', 'public_base_url', 'username', 'password'),
    'both': ('port', 'public_base_url', 'client_secret', 'username', 'password'),
    'manual': ('port', 'public_base_url'),
}

#: the modes whose form is a modal of its own
MODAL_MODES = tuple(MODE_FIELDS)


def mode_fields(mode: str) -> dict[str, dict]:
    """The fields *mode* asks for, in modal order.

    Every mode asks for the address and the port; what differs is which login it adds on top.
    """
    specs = _field_specs()
    return {key: dict(specs[key]) for key in MODE_FIELDS[mode]}


def mode_title(mode: str) -> str:
    """The title of *mode*'s modal: the mode's own sign-in label."""
    return _login_label(mode)


def mode_config(mode: str, port: int, public_base_url: str) -> dict[str, dict]:
    """The ``utils.ConfigModal`` config for *mode*, prefilled with this run's address and port.

    Raises ``ValueError`` when the mode needs more components than one modal carries.
    """
    config = mode_fields(mode)
    if len(config) > MODAL_LIMIT:
        raise ValueError(f'{mode} asks for more than {MODAL_LIMIT} fields')
    config['port']['default'] = int(port)
    config['public_base_url']['default'] = public_base_url
    return config


def mode_modal(mode: str, port: int, public_base_url: str, *, ephemeral: bool = True):
    """One login mode's whole form: a ``utils.ConfigModal`` over :func:`mode_config`."""
    return utils.ConfigModal(mode_title(mode), mode_config(mode, port, public_base_url),
                             ephemeral=ephemeral)


def _write_error_text(ex: BaseException) -> str:
    """A refused write as a reader sees it: the error's type and, when known, the file's name.

    ``str(ex)`` is deliberately never read: a serialiser or validator error can embed the ``repr``
    of a document value, and a value like an existing session key must not reach a Discord reply.
    """
    message = _("The configuration could not be written: {}").format(type(ex).__name__)
    path = getattr(ex, 'path', None)
    if path:
        message += f" ({os.path.basename(str(path))})"
    return message


class SetupWizard:
    """One ``/webadmin setup`` run: the collected answers and the one message they are shown on.

    The client secret lives on this object only, for as long as the run lasts, and is dropped as soon
    as it has been stored (:meth:`write`). A local password is hashed as soon as it arrives and its
    plaintext is never held. No attribute of this class is ever rendered, logged or written to the
    configuration file.
    """

    def __init__(self, node, bot):
        self.node = node
        self.bot = bot
        config = read_config(str(node.config_dir))
        #: the document as the run started, so a name it already carries is refused before a write
        self.config = config
        self.listen = listen_address(config)
        self.port = configured_port(config)
        self.public_base_url = configured_public_url(config, getattr(node, 'public_ip', None),
                                                    self.port)
        self.client_id = int(getattr(bot, 'application_id', None)
                             or getattr(getattr(bot, 'user', None), 'id', 0) or 0)
        self.mode: str | None = None
        self.secret: str | None = None
        self.secret_stored = False
        self.username = ''
        self.password_hash = ''

    # -- the state -------------------------------------------------------------------

    @property
    def discord_login(self) -> bool:
        """Whether the run writes the Discord backend (``manual`` names the store key, nothing more)."""
        return self.mode in ('discord', 'both', 'manual')

    @property
    def needs_secret(self) -> bool:
        """Whether the run asks for the client secret itself."""
        return self.mode in ('discord', 'both')

    @property
    def local_login(self) -> bool:
        """Whether the run sets a local account up."""
        return self.mode in ('local', 'both')

    @property
    def complete(self) -> bool:
        """Whether every answer the chosen mode needs has arrived."""
        if self.mode is None:
            return False
        if self.needs_secret and not self.secret:
            return False
        return not self.local_login or bool(self.password_hash)

    def plan(self) -> SetupPlan:
        """The document change for the answers collected so far."""
        return SetupPlan(port=self.port, public_base_url=self.public_base_url, listen=self.listen,
                         discord=self.discord_login, client_id=self.client_id,
                         local=self.local_login, username=self.username,
                         password_hash=self.password_hash)

    # -- the answers -----------------------------------------------------------------

    def bind_basics(self, port: int, public_base_url: str) -> None:
        """Take the address and the port the mode's form collected."""
        self.port = int(port)
        self.public_base_url = public_base_url

    def bind_mode(self, mode: str) -> None:
        """Take the sign-in mode, forgetting what the previous mode collected."""
        self.mode = mode
        self.secret = None
        self.secret_stored = False
        self.username = ''
        self.password_hash = ''

    def bind_secret(self, secret: str) -> None:
        """Take the client secret (never stored in the document)."""
        self.secret = secret

    def bind_local(self, username: str, password: str) -> None:
        """Take the local account's name and password; only the hash is kept.

        The plaintext is hashed here and dropped: no attribute of this object, no document and no
        log line carries it, and the reply never repeats it.
        """
        self.username = str(username).strip()
        self.password_hash = hash_password(password)

    def bind_answers(self, mode: str, answers: Mapping) -> str | None:
        """Take the answers *mode*'s modal collected. Returns the refusal text, or ``None``.

        Every answer is validated before anything is bound, so a refused form leaves the wizard on
        the previous state. A username the document already has is refused here and written nowhere.
        """
        port = parse_port(answers.get('port'))
        if port is None:
            return _("The port has to be a whole number between {} and {}.").format(MIN_PORT,
                                                                                    MAX_PORT)
        url = str(answers.get('public_base_url') or '').strip().rstrip('/')
        if _origin(url)[0] is None:
            return _("The public address has to be an absolute http or https address.")
        secret = str(answers.get('client_secret') or '').strip()
        if mode in ('discord', 'both') and not secret_looks_valid(secret):
            return _("The client secret is required.")
        username = str(answers.get('username') or '').strip()
        password = str(answers.get('password') or '')
        if mode in ('local', 'both'):
            if not username:
                return _("A username is required.")
            if len(password) < MIN_PASSWORD:
                return _("The password has to be at least {} characters long.").format(MIN_PASSWORD)
            if taken_username(self.config, username):
                return duplicate_account_text(username)
        self.bind_basics(port, url)
        if mode in ('discord', 'both'):
            self.bind_secret(secret)
        if mode in ('local', 'both'):
            self.bind_local(username, password)
        return None

    # -- the write -------------------------------------------------------------------

    def write(self) -> tuple[configwrite.SaveResult | None, str | None]:
        """Store the secret, then patch the file. Returns ``(result, error message)``.

        The secret goes FIRST so a failed configuration write cannot leave a file naming a secret that
        was never stored. Both steps are reported, never raised: the message a caller shows names the
        failure's type and the file it concerns, never the payload that caused it. A local account
        whose name the file already has is refused before either step runs, so nothing is written.
        """
        self.secret_stored = False
        if self.local_login:
            existing = taken_username(read_config(str(self.node.config_dir)), self.username)
            if existing:
                return None, duplicate_account_text(existing)
        try:
            if self.secret:
                store_secret(str(self.node.config_dir), self.secret)
                self.secret_stored = True
        except Exception as ex:  # noqa: BLE001 - a failed write reports the type, never the secret
            log.warning("Website setup: the client secret could not be stored (%s).", type(ex).__name__)
            self.secret = None
            return None, _write_error_text(ex)
        self.secret = None                     # the store holds it now; the view does not
        try:
            result = configwrite.patch_config_file(config_path(str(self.node.config_dir)),
                                                   mutate_for(self.plan()),
                                                   schemas=[CONFIG_SCHEMA])
        except Exception as ex:  # noqa: BLE001 - the reply names the type, never the payload
            log.warning("Website setup: the configuration was not written (%s).", type(ex).__name__)
            return None, _write_error_text(ex)
        return result, None

    # -- the render ------------------------------------------------------------------

    def step_embed(self) -> discord.Embed:
        """The wizard's one message: the answers so far and what is left to do."""
        embed = discord.Embed(title=_("Website setup"), colour=0x3498DB)
        embed.add_field(name=_("Public address"), value=self.public_base_url, inline=True)
        embed.add_field(name=_("Port"), value=str(self.port), inline=True)
        embed.add_field(name=_("Listen"), value=self.listen, inline=True)
        embed.add_field(name=_("Login"), value=_login_label(self.mode), inline=False)
        embed.add_field(name=_("Next step"), value=_next_step(
            self.mode, complete=self.complete), inline=False)
        if self.mode == 'manual':
            embed.add_field(name=_("Manual setup"), value=self.manual_steps(), inline=False)
        return embed

    def manual_steps(self) -> str:
        """The manual route: the redirect to register and the one command that stores the secret.

        The command carries the node's ABSOLUTE configuration directory, so the secret lands in this
        node's store whatever directory the operator's shell happens to be in.
        """
        redirect = str(self.public_base_url).rstrip('/') + '/auth/discord/callback'
        config_dir = os.path.abspath(str(self.node.config_dir))
        command_line = ("python -c \"from core.utils.os import set_password; "
                        "set_password('discord_client_secret', '<secret>', '{}')\""
                        ).format(config_dir)
        return (_("Open the Discord Developer Portal, your application → OAuth2, and add the "
                  "redirect URI {}.").format(redirect) + '\n'
                + _("Store the client secret with:") + '\n```\n' + command_line + '\n```\n'
                + _("The configuration keeps auth.discord.client_secret_key: discord_client_secret."))

    def view(self) -> 'SetupView':
        """The view for the current state (a fresh one per render)."""
        return SetupView(self)


class SetupView(discord.ui.View):
    """The wizard message's controls: the sign-in select and the write."""

    def __init__(self, wizard: SetupWizard):
        super().__init__(timeout=900)
        self.wizard = wizard
        select = Select(placeholder=_("How do you want to sign in?"), row=0, options=[
            discord.SelectOption(label=_("Discord login"), value='discord'),
            discord.SelectOption(label=_("username and password"), value='local'),
            discord.SelectOption(label=_("Discord login and a local account"), value='both'),
            discord.SelectOption(label=_("Manual"), value='manual'),
        ])
        select.callback = self._choose_mode
        self.mode_select = select
        self.add_item(select)
        submit = Button(label=_("Write the configuration"), style=discord.ButtonStyle.primary,
                        row=1, disabled=not wizard.complete)
        submit.callback = self._write
        self.add_item(submit)
        cancel = Button(label=_("Cancel"), style=discord.ButtonStyle.secondary, row=1)
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        """End the run: drop the secret, stop the view, leave one sentence and no controls."""
        self.wizard.secret = None
        self.stop()
        await interaction.response.edit_message(
            content=_("Setup cancelled — nothing was written."), embed=None, view=None)

    async def _choose_mode(self, interaction: discord.Interaction) -> None:
        """Open the chosen mode's one form, which asks for the address, the port and its login."""
        mode = self.mode_select.values[0]
        if mode in ('discord', 'both', 'manual') and not self.wizard.client_id:
            await interaction.response.send_message(
                _("The bot's application id could not be read, so the Discord login cannot be "
                  "configured here."), ephemeral=True)
            return
        modal = mode_modal(mode, self.wizard.port, self.wizard.public_base_url)
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return                      # abandoned: the wizard keeps what it had
        self.wizard.bind_mode(mode)
        refusal = self.wizard.bind_answers(mode, modal.value)
        if refusal:
            await interaction.followup.send(refusal, ephemeral=True)
        await interaction.edit_original_response(embed=self.wizard.step_embed(),
                                                 view=self.wizard.view())

    async def _write(self, interaction: discord.Interaction) -> None:
        if not self.wizard.complete:
            await interaction.response.send_message(_("Pick how you sign in first."), ephemeral=True)
            return
        await interaction.response.defer()
        # off the loop: the read, the ruamel round trip and the schema validation take seconds
        result, error = await asyncio.to_thread(self.wizard.write)
        restart = await restart_hint(self.wizard.bot)
        embed = outcome_embed(self.wizard, result, error, restart)
        # the same control every other write offers: rebuild the console's application object
        view = RestartConsoleView(self.wizard.bot, self.wizard.node)
        await interaction.edit_original_response(embed=embed, view=view)

    async def on_timeout(self) -> None:
        """Drop the secret an abandoned run still holds."""
        self.wizard.secret = None


def secret_looks_valid(value: object) -> bool:
    """Whether *value* can be a Discord client secret: a non-empty string without whitespace."""
    text = str(value or '').strip()
    return bool(text) and not any(character.isspace() for character in text)


async def restart_hint(bot) -> str:
    """How to apply the change: the ``/node restart`` command's mention, or its plain name."""
    try:
        return (await utils.get_command(bot, group='node', name='restart')).mention
    except (app_commands.CommandNotFound, AttributeError, IndexError):
        return '/node restart'


def outcome_embed(wizard: SetupWizard, result, error: str | None,
                  restart: str) -> discord.Embed:
    """The closing message: what was written and how to apply it.

    No password ever appears here: the local one is the operator's own answer and is never echoed,
    and the client-secret row reports the KEY the secret was stored under — the value is never
    rendered and never stored in the document — independently of whether the file itself changed.
    """
    embed = discord.Embed(title=_("Website setup"),
                          colour=0xE74C3C if error else 0x2ECC71)
    if error:
        embed.add_field(name=_("Configuration file"), value=error, inline=False)
        return embed
    path = CONFIG_FILE
    if result is not None and not result.changed:
        embed.add_field(name=_("Configuration file"),
                        value=_("Nothing changed — {} already matches.").format(path), inline=False)
    else:
        embed.add_field(name=_("Configuration file"),
                        value=_("{} was written.").format(path), inline=False)
        if result is not None and result.backup:
            embed.add_field(name=_("Backup"),
                            value=_("The previous version is kept as {}.").format(result.backup.name),
                            inline=False)
    if wizard.secret_stored:
        embed.add_field(name=_("Client secret"), value=_(
            "The client secret is stored under auth.discord.client_secret_key — the configuration "
            "file names that key only, never the value."), inline=False)
    if wizard.local_login and wizard.username:
        embed.add_field(name=_("Local account"), value=_(
            "The local account {} is set up; its password is the one you entered.").format(
            wizard.username), inline=False)
    embed.add_field(name=_("Next step"), value=_(
        "A configuration change applies on the next node restart, not on a page reload: {}"
    ).format(restart) + ' ' + _("Open {}.").format(wizard.public_base_url), inline=False)
    return embed


# ------------------------------------------------------------------------------- the local accounts
# ``auth.local.users`` is a plain list under ``DEFAULT:``. These functions change exactly one entry
# through ``configwrite.patch_config_file``: every other account, comment, key order and un-owned key
# survives. A refusal is raised from INSIDE the mutation, so it sees the document as the lock holds
# it and a name that vanished between the select and the press can never be silently re-created.


class UserWriteRefused(Exception):
    """A local-account write the document's own state refused; the message is what a reader sees."""


def _local_read(document: Any) -> Mapping | None:
    """The ``auth.local`` block of *document*, ``None`` when the document has none."""
    block = document.get(DEFAULT_TAG) if isinstance(document, Mapping) else None
    auth = block.get('auth') if isinstance(block, Mapping) else None
    local = auth.get('local') if isinstance(auth, Mapping) else None
    return local if isinstance(local, Mapping) else None


def _local_write(document: Any) -> MutableMapping:
    """The ``auth.local`` block of *document*, created when the document has none."""
    block = document.get(DEFAULT_TAG)
    if not isinstance(block, MutableMapping):
        block = document[DEFAULT_TAG] = {}
    auth = block.get('auth')
    if not isinstance(auth, MutableMapping):
        auth = block['auth'] = {}
    local = auth.get('local')
    if not isinstance(local, MutableMapping):
        local = auth['local'] = {}
    return local


def _users_of(document: Any) -> list:
    """The ``auth.local.users`` list of *document*, empty when it has none."""
    local = _local_read(document)
    users = local.get('users') if isinstance(local, Mapping) else None
    return users if isinstance(users, list) else []


def _roles_of(user: object) -> list[str]:
    """The role names one account entry declares, empty when it declares none."""
    roles = user.get('roles') if isinstance(user, Mapping) else None
    if not isinstance(roles, (list, tuple)):
        return []
    return [str(role).strip() for role in roles if str(role).strip()]


def _index_of(users: list, username: str) -> int | None:
    """The index of the account named *username* in *users*, or ``None``."""
    for index, user in enumerate(users):
        if isinstance(user, Mapping) and str(user.get('username', '')).strip() == username:
            return index
    return None


def admin_holders(document: Any) -> list[str]:
    """The names of the local accounts that hold :data:`ADMIN_ROLE`."""
    return [str(user.get('username')) for user in _users_of(document)
            if ADMIN_ROLE in _roles_of(user)]


def username_refusal(username: object) -> str | None:
    """Why *username* cannot be a local account's name, or ``None`` when it can."""
    text = str(username or '').strip()
    if not text:
        return _("A username is required.")
    if len(text) > MAX_USERNAME:
        return _("The username has to be at most {} characters long.").format(MAX_USERNAME)
    return None


def password_refusal(password: object) -> str | None:
    """Why *password* cannot be a local password, or ``None`` when it can.

    The two bounds are the login's own: a shorter password is refused by the wizard's policy and a
    longer one by :func:`hash_password` itself.
    """
    text = str(password or '')
    if len(text) < MIN_PASSWORD:
        return _("The password has to be at least {} characters long.").format(MIN_PASSWORD)
    if len(text) > MAX_PASSWORD:
        return _("The password has to be at most {} characters long.").format(MAX_PASSWORD)
    return None


def _missing_account_text(username: str) -> str:
    """The refusal for a name the file no longer carries."""
    return _("The local account '{}' is not in the configuration any more — reload and pick "
             "again.").format(username)


def _unknown_roles_text(unknown: Iterable[str], known: Iterable[str]) -> str:
    """The refusal for role names this installation does not define."""
    return _("The role(s) {} are not defined by this installation — nothing was written. Known "
             "roles: {}.").format(', '.join(sorted(unknown)), ', '.join(sorted(known)))


def mutate_add_user(username: str, password_hash: str, roles: Iterable[str], *,
                    known: Iterable[str]) -> Callable[[Any], None]:
    """The change that appends one local account, as a pure function.

    ``auth.local.enabled`` is set when it is absent — an account nothing can sign in with is a trap.
    A name the document already carries and a role name the installation does not define both refuse
    from inside the mutation, so nothing is written for either.
    """
    name = str(username).strip()
    role_names = [str(role).strip() for role in roles if str(role).strip()]
    known_names = frozenset(str(role).strip() for role in known)
    if not role_names:
        raise ValueError('a local account needs at least one role')

    def mutate(document: Any) -> None:
        if _index_of(_users_of(document), name) is not None:
            raise UserWriteRefused(duplicate_account_text(name))
        unknown = set(role_names) - known_names
        if unknown:
            raise UserWriteRefused(_unknown_roles_text(unknown, known_names))
        local = _local_write(document)
        users = local.get('users')
        if not isinstance(users, list):
            users = local['users'] = []
        users.append({'username': name, 'password_hash': password_hash, 'roles': role_names})
        local.setdefault('enabled', True)

    return mutate


def mutate_delete_user(username: str) -> Callable[[Any], None]:
    """The change that removes one local account, as a pure function.

    The last account holding :data:`ADMIN_ROLE` is refused: removing it would lock everyone out of
    the console. A name the document does not carry refuses rather than being ignored.
    """
    name = str(username).strip()

    def mutate(document: Any) -> None:
        users = _users_of(document)
        index = _index_of(users, name)
        if index is None:
            raise UserWriteRefused(_missing_account_text(name))
        if ADMIN_ROLE in _roles_of(users[index]):
            holders = [str(user.get('username')) for user in users
                       if ADMIN_ROLE in _roles_of(user)]
            if len(holders) <= 1:
                raise UserWriteRefused(_(
                    "The local account '{}' is the last one holding Admin — removing it would lock "
                    "everyone out of the console. Give another account Admin first.").format(name))
        del users[index]

    return mutate


def mutate_set_password(username: str, password_hash: str) -> Callable[[Any], None]:
    """The change that replaces one account's password hash, as a pure function.

    Only ``password_hash`` changes: the entry's ``roles`` and ``scope`` stay exactly as they are, and
    every other account in the list is untouched. A name the document does not carry refuses.
    """
    name = str(username).strip()

    def mutate(document: Any) -> None:
        users = _users_of(document)
        index = _index_of(users, name)
        if index is None:
            raise UserWriteRefused(_missing_account_text(name))
        users[index]['password_hash'] = password_hash

    return mutate


def _local_users_write(config_dir: str | os.PathLike, mutate: Callable[[Any], None]
                       ) -> configwrite.SaveResult:
    """Apply *mutate* to the website's configuration file, keeping everything it does not touch."""
    return configwrite.patch_config_file(config_path(str(config_dir)), mutate, schemas=[CONFIG_SCHEMA])


def local_account_names(config_dir: str | os.PathLike) -> list[str]:
    """The usernames in the file's ``auth.local.users`` list, in the order the file lists them."""
    users = _users_of(read_config(str(config_dir)) or {})
    return [str(user.get('username')) for user in users
            if isinstance(user, Mapping) and str(user.get('username') or '').strip()]


def add_local_user(config_dir: str | os.PathLike, username: str, password: str,
                   roles: Iterable[str], *, known: Iterable[str]) -> configwrite.SaveResult:
    """Append one local account. Raises :class:`UserWriteRefused` for anything the file refuses."""
    refusal = username_refusal(username) or password_refusal(password)
    if refusal:
        raise UserWriteRefused(refusal)
    if not [str(role).strip() for role in roles if str(role).strip()]:
        raise UserWriteRefused(_("Pick at least one role for the new account."))
    password_hash = hash_password(str(password))
    return _local_users_write(config_dir, mutate_add_user(str(username).strip(), password_hash,
                                                          roles, known=known))


def delete_local_user(config_dir: str | os.PathLike, username: str) -> configwrite.SaveResult:
    """Remove one local account. Raises :class:`UserWriteRefused` for anything the file refuses."""
    return _local_users_write(config_dir, mutate_delete_user(username))


def set_local_password(config_dir: str | os.PathLike, username: str,
                       password: str) -> configwrite.SaveResult:
    """Replace one local account's password. Raises :class:`UserWriteRefused` when it refuses."""
    refusal = password_refusal(password)
    if refusal:
        raise UserWriteRefused(refusal)
    password_hash = hash_password(str(password))
    return _local_users_write(config_dir, mutate_set_password(username, password_hash))


def account_embed(username: str, result: configwrite.SaveResult | None, *,
                  deleted: bool = False) -> discord.Embed:
    """The reply after one account write: what changed and in which file."""
    title = _("Local account removed") if deleted else _("Local account")
    embed = discord.Embed(title=title, colour=0x2ECC71)
    embed.add_field(name=_("Account"), value=username, inline=False)
    if result is not None and not result.changed:
        embed.add_field(name=_("Configuration file"),
                        value=_("Nothing changed — {} already matches.").format(CONFIG_FILE),
                        inline=False)
    else:
        embed.add_field(name=_("Configuration file"),
                        value=_("{} was written.").format(CONFIG_FILE), inline=False)
        backup = getattr(result, 'backup', None)
        if backup is not None:
            embed.add_field(name=_("Backup"),
                            value=_("The previous version is kept as {}.").format(backup.name),
                            inline=False)
    return embed


# ------------------------------------------------------------------------------------ the restart

@dataclass(frozen=True)
class RestartOutcome:
    """What rebuilding the console's application object did."""

    ok: bool
    message: str
    url: str | None = None
    #: the restart minted a new session key and needs an explicit second confirmation
    needs_confirm: bool = False


def _session_secret_of(config: Mapping | None) -> str | None:
    """``session.secret`` of *config*, ``None`` when it is absent or empty."""
    block = _block(config)
    session = block.get('session') if isinstance(block, Mapping) else None
    secret = session.get('secret') if isinstance(session, Mapping) else None
    return str(secret) if secret else None


def loaded_plugin_names(bot) -> list[str]:
    """The names of the plugins loaded on this node, lower-cased."""
    names = getattr(bot, 'plugins', None)
    if names is None:
        names = getattr(getattr(bot, 'node', None), 'plugins', None)
    return [str(name).lower() for name in (names or [])]


def _console_url(config: Mapping | None, port: int | None) -> str | None:
    """The address the console is opened at: the file's own, else this machine's loopback."""
    block = _block(config)
    value = _auth(block).get('public_base_url') if isinstance(block, Mapping) else None
    text = str(value).strip().rstrip('/') if isinstance(value, str) else ''
    if text and _origin(text)[0] is not None:
        return text
    if port:
        return f"http://127.0.0.1:{port}"
    return None


def _routes_text(state: str) -> str:
    """What the REST API plugin's routes did: ``reloaded``, ``reload_failed`` or ``not_loaded``."""
    if state == "reloaded":
        return _("The REST API plugin was reloaded, so its routes are attached to the new "
                 "application object.")
    if state == "reload_failed":
        return _("The REST API plugin is loaded on this node, but its reload failed, so its routes "
                 "are still attached to the old application object — reload it by hand.")
    return _("The REST API plugin is not loaded on this node, so no routes had to be re-attached.")


async def restart_web_console(bot, node, *, confirm: bool = False) -> RestartOutcome:
    """Rebuild the console's application object in this process — no node restart.

    ``auth.local.users`` is read ONCE per application object (:func:`install_auth` is idempotent per
    app), so a configuration change only takes effect when that object is rebuilt: the service is
    stopped and started again. The REST API plugin caches the old app and registers its router on it
    once, so it is reloaded after the start when it is loaded. The outcome reports what actually
    happened, never an assumed success.

    An unset ``session.secret`` means the first call refuses and stops nothing: every restart mints a
    new random session key and signs everyone out. ``confirm=True`` is the explicit second step.
    """
    config = await asyncio.to_thread(read_config, str(node.config_dir))
    if not _session_secret_of(config) and not confirm:
        return RestartOutcome(False, _(
            "session.secret is not set — every restart mints a new random session key, so this signs "
            "everyone out. Write session.secret first, or press the button again to confirm."),
            needs_confirm=True)
    plugin_loaded = RESTAPI_PLUGIN in loaded_plugin_names(bot)
    try:
        await ServiceRegistry.stop_service(WebService)
        await ServiceRegistry.start_service(WebService)
    except Exception as ex:  # noqa: BLE001 - the reply names the type, never the payload
        log.warning("Web console restart: the service did not cycle (%s).", type(ex).__name__)
        return RestartOutcome(False, _(
            "The web console could not be restarted ({}). It is down until the bot is restarted."
        ).format(type(ex).__name__) + ' ' + _routes_text(
            "reload_failed" if plugin_loaded else "not_loaded"))
    state = "not_loaded"
    if plugin_loaded:
        try:
            state = "reloaded" if bool(await bot.reload(RESTAPI_PLUGIN)) else "reload_failed"
        except Exception as ex:  # noqa: BLE001 - a failed reload is reported, never raised
            log.warning("Web console restart: the REST API plugin did not reload (%s).",
                        type(ex).__name__)
            state = "reload_failed"
    service = ServiceRegistry.get(WebService)
    local = service if service is not None and not isinstance(service, ServiceProxy) else None
    running = bool(local is not None and local.is_running())
    port = None
    if running:
        try:
            port = local.get_ports()['WebService'].port
        except (AttributeError, KeyError, TypeError):
            port = None
    url = _console_url(config, port)
    if not running:
        return RestartOutcome(False, _(
            "The web console did not come back up — it is down until the bot is restarted. Check the "
            "bot's log for the service's error.") + ' ' + _routes_text(state))
    message = (_("The web console was rebuilt and is running on port {}.").format(port)
               if port else _("The web console was rebuilt and is running."))
    return RestartOutcome(True, message + ' ' + _routes_text(state), url=url)


def restart_embed(outcome: RestartOutcome) -> discord.Embed:
    """The restart's outcome as one embed, coloured by whether it worked."""
    embed = discord.Embed(title=_("Web console restart"),
                          colour=0x2ECC71 if outcome.ok else 0xE74C3C)
    embed.add_field(name=_("Result"), value=outcome.message, inline=False)
    if outcome.url:
        embed.add_field(name=_("Address"), value=outcome.url, inline=False)
    return embed


class ConfirmRestartView(discord.ui.View):
    """The explicit confirmation for a restart with no ``session.secret``."""

    def __init__(self, bot, node):
        super().__init__(timeout=120)
        self.bot = bot
        self.node = node
        confirm = Button(label=_("Restart anyway"), style=discord.ButtonStyle.danger)
        confirm.callback = self._confirm
        self.add_item(confirm)
        cancel = Button(label=_("Cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        outcome = await restart_web_console(self.bot, self.node, confirm=True)
        await interaction.followup.send(embed=restart_embed(outcome), ephemeral=True)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(_("Nothing was restarted."), ephemeral=True)


class RestartConsoleView(discord.ui.View):
    """The one control every write ends with: rebuild the console's application object."""

    def __init__(self, bot, node):
        super().__init__(timeout=300)
        self.bot = bot
        self.node = node
        button = Button(label=_("Restart the web console"), style=discord.ButtonStyle.primary)
        button.callback = self._restart
        self.add_item(button)

    async def _restart(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        outcome = await restart_web_console(self.bot, self.node)
        if outcome.needs_confirm:
            await interaction.followup.send(embed=restart_embed(outcome),
                                            view=ConfirmRestartView(self.bot, self.node),
                                            ephemeral=True)
            return
        await interaction.followup.send(embed=restart_embed(outcome), ephemeral=True)


# ------------------------------------------------------------------------------------- the forms

def add_user_config(role_names: Iterable[str]) -> dict[str, dict]:
    """The ``utils.ConfigModal`` config for a new local account: name, password and roles."""
    names = [str(name) for name in role_names]
    return {
        'username': {'type': str, 'label': _("Username"), 'required': True,
                     'min_length': 1, 'max_length': MAX_USERNAME},
        'password': {'type': str, 'label': _("Password"), 'required': True,
                     'min_length': MIN_PASSWORD, 'max_length': MAX_PASSWORD},
        'roles': {'type': list, 'label': _("Roles"), 'required': True, 'min_values': 1,
                  'max_values': max(1, len(names)), 'options': names},
    }


def password_config() -> dict[str, dict]:
    """The ``utils.ConfigModal`` config for a new password: the password only."""
    return {'password': {'type': str, 'label': _("New password"), 'required': True,
                         'min_length': MIN_PASSWORD, 'max_length': MAX_PASSWORD}}


def _all_selected(value: Mapping, key: str) -> list[str]:
    """Every value a select in *value* carries, not only the first one."""
    selected = value.get(key)
    if isinstance(selected, (list, tuple)):
        return [str(item) for item in selected]
    return [str(selected)] if selected else []


class AddUserModal(utils.ConfigModal):
    """One form for a new local account. Refuses in place; nothing is written on a refusal."""

    def __init__(self, bot, node, role_names: Iterable[str]):
        self.bot = bot
        self.node = node
        super().__init__(_("Add a local account"), add_user_config(role_names), ephemeral=True)

    def get_value(self) -> dict:
        """The form's values, with the roles select read in full rather than as its first value."""
        value = super().get_value()
        if 'roles' in value:
            for child in self.children:
                component = getattr(child, 'component', None)
                if getattr(component, 'custom_id', None) == 'roles':
                    value['roles'] = list(getattr(component, 'values', []) or [])
                    break
        return value

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await super().on_submit(interaction)
        username = str(self.value.get('username') or '').strip()
        password = str(self.value.get('password') or '')
        roles = _all_selected(self.value, 'roles')
        try:
            known = await asyncio.to_thread(known_role_names, str(self.node.config_dir))
            result = await asyncio.to_thread(add_local_user, str(self.node.config_dir),
                                             username, password, roles, known=known)
        except UserWriteRefused as refusal:
            await interaction.followup.send(str(refusal), ephemeral=True)
            return
        except Exception as ex:  # noqa: BLE001 - the reply names the type, never the payload
            log.warning("Adding a local account failed (%s).", type(ex).__name__)
            await interaction.followup.send(_write_error_text(ex), ephemeral=True)
            return
        await interaction.followup.send(embed=account_embed(username, result),
                                        view=RestartConsoleView(self.bot, self.node),
                                        ephemeral=True)


class SetPasswordModal(utils.ConfigModal):
    """One form for an existing account's new password. Replaces the hash only."""

    def __init__(self, bot, node, username: str):
        self.bot = bot
        self.node = node
        self.username = username
        super().__init__(_("Set a new password for {}").format(username), password_config(),
                         ephemeral=True)

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await super().on_submit(interaction)
        password = str(self.value.get('password') or '')
        try:
            result = await asyncio.to_thread(set_local_password, str(self.node.config_dir),
                                             self.username, password)
        except UserWriteRefused as refusal:
            await interaction.followup.send(str(refusal), ephemeral=True)
            return
        except Exception as ex:  # noqa: BLE001 - the reply names the type, never the payload
            log.warning("Setting a local password failed (%s).", type(ex).__name__)
            await interaction.followup.send(_write_error_text(ex), ephemeral=True)
            return
        await interaction.followup.send(embed=account_embed(self.username, result),
                                        view=RestartConsoleView(self.bot, self.node),
                                        ephemeral=True)


# ------------------------------------------------------------------------------------- the views

class UserPicker(discord.ui.View):
    """Pick one local account by name; *handler* receives ``(interaction, username)``."""

    def __init__(self, names: Iterable[str], handler):
        super().__init__(timeout=300)
        self.handler = handler
        options = [discord.SelectOption(label=name, value=name)
                   for name in list(names)[:SELECT_LIMIT]]
        select = Select(placeholder=_("Pick a local account"), options=options)
        select.callback = self._picked
        self.select = select
        self.add_item(select)

    async def _picked(self, interaction: discord.Interaction) -> None:
        await self.handler(interaction, self.select.values[0])


class ConfirmDeleteView(discord.ui.View):
    """The confirmation before one account is removed. Removed means removed."""

    def __init__(self, bot, node, username: str):
        super().__init__(timeout=120)
        self.bot = bot
        self.node = node
        self.username = username
        confirm = Button(label=_("Delete it"), style=discord.ButtonStyle.danger)
        confirm.callback = self._confirm
        self.add_item(confirm)
        cancel = Button(label=_("Cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(delete_local_user, str(self.node.config_dir),
                                             self.username)
        except UserWriteRefused as refusal:
            await interaction.edit_original_response(content=str(refusal), embed=None, view=None)
            return
        except Exception as ex:  # noqa: BLE001 - the reply names the type, never the payload
            log.warning("Removing a local account failed (%s).", type(ex).__name__)
            await interaction.edit_original_response(content=_write_error_text(ex), embed=None,
                                                     view=None)
            return
        await interaction.edit_original_response(
            content=None, embed=account_embed(self.username, result, deleted=True),
            view=RestartConsoleView(self.bot, self.node))

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content=_("Nothing was deleted."), embed=None,
                                                 view=None)


def not_master_refusal(node, command: str) -> str:
    """The refusal a write on a node that is not the master gets — the same one ``setup`` gives."""
    name = master_name(node)
    if name:
        return _("This node is not the master — the website runs on the master node only. "
                 "Run {} on {}.").format(command, name)
    return _("This node is not the master — the website runs on the master node only. "
             "Run {} on the master node.").format(command)


class UserAdmin(Group):
    """The ``/webadmin user`` group: the local accounts that sign in to the admin website.

    Every command is ephemeral, guild-only and Admin-only. A write on a node that is not the master
    refuses with the same wording ``setup`` uses and writes nothing. Every write ends with the same
    ``Restart the web console`` button: the console's application object is built once, so a change
    only takes effect when it is rebuilt.
    """

    @command(name="add", description=_("Adds a local account to the admin website"))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def add(self, interaction: discord.Interaction):
        """Open one form for a new account's name, password and roles. Ephemeral."""
        node = getattr(interaction.client, 'node')
        if not getattr(node, 'master', False):
            await interaction.response.send_message(not_master_refusal(node, '/webadmin user add'),
                                                    ephemeral=True)
            return
        try:
            known = await asyncio.to_thread(known_role_names, str(node.config_dir))
        except Exception as ex:  # noqa: BLE001 - the reply names the type, never the payload
            log.warning("The known role names could not be read (%s).", type(ex).__name__)
            await interaction.response.send_message(_write_error_text(ex), ephemeral=True)
            return
        if not known:
            await interaction.response.send_message(
                _("This installation defines no role names, so no account can be granted one."),
                ephemeral=True)
            return
        await interaction.response.send_modal(AddUserModal(interaction.client, node, sorted(known)))

    @command(name="delete", description=_("Removes a local account from the admin website"))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def delete(self, interaction: discord.Interaction):
        """Pick an account, confirm, remove exactly that entry. Ephemeral."""
        node = getattr(interaction.client, 'node')
        if not getattr(node, 'master', False):
            await interaction.response.send_message(
                not_master_refusal(node, '/webadmin user delete'), ephemeral=True)
            return
        names = await asyncio.to_thread(local_account_names, str(node.config_dir))
        if not names:
            await interaction.response.send_message(
                _("The website configuration carries no local accounts."), ephemeral=True)
            return
        view = UserPicker(names, lambda i, name: self._delete_picked(i, name, node))
        await interaction.response.send_message(
            _("Pick the local account to remove. This cannot be undone."), view=view, ephemeral=True)

    async def _delete_picked(self, interaction: discord.Interaction, username: str, node) -> None:
        """Ask for confirmation before removing *username*."""
        await interaction.response.edit_message(
            content=_("Delete the local account '{}'? This cannot be undone.").format(username),
            embed=None, view=ConfirmDeleteView(interaction.client, node, username))

    @command(name="password", description=_("Sets a new password for a local account"))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def password(self, interaction: discord.Interaction):
        """Pick an account, then open one form for its new password. Ephemeral."""
        node = getattr(interaction.client, 'node')
        if not getattr(node, 'master', False):
            await interaction.response.send_message(
                not_master_refusal(node, '/webadmin user password'), ephemeral=True)
            return
        names = await asyncio.to_thread(local_account_names, str(node.config_dir))
        if not names:
            await interaction.response.send_message(
                _("The website configuration carries no local accounts."), ephemeral=True)
            return
        view = UserPicker(names, lambda i, name: self._password_picked(i, name, node))
        await interaction.response.send_message(
            _("Pick the local account whose password you want to change."), view=view, ephemeral=True)

    async def _password_picked(self, interaction: discord.Interaction, username: str, node) -> None:
        """Open the new-password form for *username*."""
        await interaction.response.send_modal(
            SetPasswordModal(interaction.client, node, username))


class WebAdmin(Group):
    """The ``/webadmin`` command group.

    Commands are methods here on purpose: discord.py binds the cog as a command callback's first
    argument only when the callback's qualified name sits inside a class, and reads a module-level
    callback as taking the interaction first. The bound object is this group, not the cog, so a
    callback reads the node from ``interaction.client.node``.
    """

    @command(name="status",
             description=_("Shows which part of the website configuration is missing"))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def status(self, interaction: discord.Interaction):
        """Report every website gate in one embed. Read-only, ephemeral."""
        node = getattr(interaction.client, 'node')
        # off the loop: the file read is the only I/O of the doctor
        config = await asyncio.to_thread(read_config, node.config_dir)
        rows = status_rows(config, read_facts(node, config))
        await interaction.response.send_message(embed=status_embed(rows), ephemeral=True)

    @command(name="setup", description=_("Turns the admin website on and stores the login secret"))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def setup(self, interaction: discord.Interaction):
        """Run the guided setup wizard: address, sign-in mode, confirm. Ephemeral."""
        node = getattr(interaction.client, 'node')
        if not getattr(node, 'master', False):
            # the webservice runs on the master only: a file written here would serve nothing
            name = master_name(node)
            text = _("This node is not the master — the website runs on the master node only. "
                     "Run /webadmin setup on {}.").format(name) if name else _(
                "This node is not the master — the website runs on the master node only. "
                "Run /webadmin setup on the master node.")
            await interaction.response.send_message(text, ephemeral=True)
            return
        wizard = await asyncio.to_thread(SetupWizard, node, interaction.client)
        # the message already shows the address and the port; the chosen mode's form asks them once
        await interaction.response.send_message(embed=wizard.step_embed(), view=wizard.view(),
                                                ephemeral=True)


webadmin_group = WebAdmin(name="webadmin",
                          description=_("Inspects and sets up the admin website"))
# discord.py 2.7.1 nests groups: /webadmin user add|delete|password
user_group = UserAdmin(name="user",
                       description=_("Manages the local accounts that sign in to the admin website"))
webadmin_group.add_command(user_group)
