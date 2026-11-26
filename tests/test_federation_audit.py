"""An update's audit trail: one START row and one END row per update, and one row per window action.

The node method owns both rows, so it does not matter which trigger ran the update — the autoupdate timer, a cron
action, the website, or ``/dcs update``. Two properties are load-bearing and pinned here:

* **A refused update claims nothing.** ``UPDATE_STOOD_BY`` and ``UPDATE_SERVERS_UP`` mean the installer never ran, so
  neither the start row nor an end row is written; the operator gets the alert instead.
* **An audit write never aborts the work it reports on.** Every window action continues when the row cannot be
  written, which is what keeps a broken audit channel from leaving a shared installation stepped down.

The doubles mirror what the code reads off a node — ``name``, ``node.name``, ``locals``, ``log``, the resource map and
``update`` — so the shipped bodies can be driven without a database.
"""
from __future__ import annotations

import types

import pytest

from core import Status
from core.data import resource_window
from core.data.impl import nodeimpl
from core.data.impl.nodeimpl import NodeImpl
from core.data.node import UPDATE_SERVERS_UP, UPDATE_STOOD_BY
from core.data.resource_window import OpenWindow, ResourceWindowProtocol

RESOURCE = 'a3f1c2d4e5b60718293a4b5c6d7e8f90112233445566778899aabbccddeeff00'
PATH = '//nas/dcs'


# -------------------------------------------------------------------------------------------------- doubles

class _Log:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def _record(self, level, msg, *args):
        self.lines.append((level, msg % args if args else msg))

    def info(self, msg, *args):
        self._record('info', msg, *args)

    def debug(self, msg, *args):
        self._record('debug', msg, *args)

    def warning(self, msg, *args):
        self._record('warning', msg, *args)

    def error(self, msg, *args):
        self._record('error', msg, *args)

    def texts(self, level: str | None = None) -> list[str]:
        return [text for line_level, text in self.lines if level is None or line_level == level]


class _Bus:
    """The bot service's audit surface: what reached it, in order, with the sequence it arrived in."""

    def __init__(self, sequence: list | None = None):
        self.sent: list = []
        self.sequence = sequence
        self.raises: Exception | None = None

    async def send_to_node(self, payload):
        if self.raises is not None:
            raise self.raises
        if self.sequence is not None:
            self.sequence.append('audit')
        self.sent.append(payload)

    def methods(self) -> list[str]:
        return [payload['method'] for payload in self.sent]

    def events(self) -> list[dict]:
        return [payload['params'] for payload in self.sent if payload['method'] == 'audit']

    def messages(self) -> list[str]:
        return [event['message'] for event in self.events()]


class _Registry:
    bus: _Bus | None = None

    @classmethod
    def get(cls, _service):
        return cls.bus


class _Node:
    """A node carrying everything ``dcs_update`` reads, with ``update()`` recording instead of installing."""

    def __init__(self, resources=None, rc: int = 0, sequence: list | None = None):
        self.resources: dict[str, str] = {} if resources is None else dict(resources)
        self.name = 'node-1'
        self.node = types.SimpleNamespace(name='node-1')
        self.locals: dict = {}
        self.log = _Log()
        self.rc = rc
        self.sequence = sequence
        self.update_calls: list[dict] = []

    def resource_of_installation(self) -> str | None:
        return next(iter(self.resources), None)

    async def federated_update(self, resource_id, **kwargs):
        return await NodeImpl.federated_update(self, resource_id, **kwargs)

    async def update(self, **kwargs) -> int:
        if self.sequence is not None:
            self.sequence.append('update')
        self.update_calls.append(kwargs)
        return self.rc


class _Protocol:
    """The window protocol as ``federated_update`` drives it, recording the order of the steps."""

    sequence: list = []
    granted = True
    residue = 0

    def __init__(self, node, **_kwargs):
        self.node = node
        self.log = node.log
        self.windows = types.SimpleNamespace(take=self._take, release=self._release)
        self.registry = types.SimpleNamespace(members=self._members)

    async def _take(self, resource_id, action, scope):
        return _Protocol.granted

    async def _release(self, resource_id):
        pass

    async def _members(self, resource_id):
        return []

    async def step_down(self, resource_id, scope='server', warn_times=None):
        _Protocol.sequence.append('step_down')

    async def wait_for_clear(self, resource_id, timeout):
        _Protocol.sequence.append('wait_for_clear')
        return _Protocol.residue

    async def restore(self, resource_id):
        _Protocol.sequence.append('restore')


class _WindowRegistry:
    """The registry surface a window action reads, with the path `resources.path` would answer."""

    def __init__(self, dependents=(), path=PATH):
        self.rows = [('server', name, 'in_service') for name in dependents]
        self.installed_at = path
        self.up_calls: list[int] = []
        self.state_calls: list = []

    async def dependents(self, resource_id, **kwargs):
        if kwargs.get('states') == ('stepped_down',):
            return [row for row in self.rows if row[2] == 'stepped_down']
        return list(self.rows)

    async def set_dependents_state(self, resource_id, state, names):
        self.state_calls.append((state, set(names)))

    async def set_servers_up(self, resource_id, servers_up):
        self.up_calls.append(servers_up)

    async def path(self, resource_id):
        return self.installed_at


class _WindowWindows:
    def __init__(self, window: OpenWindow | None = None):
        self.window = window
        self.acks: list[str] = []

    async def state(self, resource_id):
        return self.window

    async def ack(self, resource_id):
        self.acks.append(resource_id)

    async def unack(self, resource_id):
        if resource_id in self.acks:
            self.acks.remove(resource_id)

    async def acked(self, resource_id):
        return resource_id in self.acks


class _Manager:
    """The maintenance manager's power surface, with the REAL in-service test bound to it."""

    in_service = staticmethod(resource_window.ServerMaintenanceManager.in_service)

    def __init__(self, node, *, message=None, **_kwargs):
        self.servers = list(getattr(node, 'servers', []))

    def node_servers(self):
        return list(self.servers)

    async def power_off(self, servers, **kwargs):
        for server in servers:
            server.status = Status.SHUTDOWN
        return types.SimpleNamespace(stopped=list(servers), handled=list(servers))

    async def power_on(self, servers, **kwargs):
        started = [server for server in servers if not self.in_service(server)]
        for server in started:
            server.status = Status.RUNNING
        return types.SimpleNamespace(started=started, skipped=[], cleared=[])


def _server(name: str, running: bool = True):
    return types.SimpleNamespace(name=name, status=Status.RUNNING if running else Status.SHUTDOWN)


class _WindowNode:
    def __init__(self, servers=()):
        self.servers = list(servers)
        self.name = 'node-1'
        self.guild_id = 42
        self.locals: dict = {}
        self.log = _Log()


@pytest.fixture
def bus(monkeypatch) -> _Bus:
    fake = _Bus()
    _Registry.bus = fake
    monkeypatch.setattr(nodeimpl, 'ServiceRegistry', _Registry)
    return fake


@pytest.fixture
def taker(monkeypatch):
    _Protocol.sequence = []
    _Protocol.granted = True
    _Protocol.residue = 0
    monkeypatch.setattr(nodeimpl, 'ResourceWindowProtocol', _Protocol)
    return _Protocol


# --------------------------------------------------------------------------------------- the pair, per trigger

@pytest.mark.asyncio
async def test_an_update_without_discord_writes_one_start_and_one_end_row(bus):
    """RED: only the successful end was audited, so a timer or cron update left no start row at all."""
    subject = _Node()

    assert await NodeImpl.dcs_update(subject, branch='openbeta', version='2.9.9') == 0

    assert bus.messages() == [
        "started an update to version 2.9.9@openbeta of all DCS servers on node node-1.",
        "DCS World updated to version 2.9.9 on node node-1."
    ]
    assert all('actor' not in event for event in bus.events()), 'nobody triggered this one'


@pytest.mark.asyncio
async def test_the_start_row_names_the_branch_when_that_is_all_the_trigger_knows(bus):
    """The autoupdate timer and a cron action name the branch only — the row has to read on its own."""
    subject = _Node()

    await NodeImpl.dcs_update(subject, branch='openbeta')

    assert bus.messages()[0] == \
        "started an update to branch openbeta of all DCS servers on node node-1."


@pytest.mark.asyncio
async def test_the_federated_start_row_is_written_once_the_installation_is_clear(bus, taker):
    """The row must not claim a start before the other clusters' servers are down: it is the installer's beginning."""
    bus.sequence = _Protocol.sequence
    subject = _Node(resources={RESOURCE: 'server'}, sequence=_Protocol.sequence)

    assert await NodeImpl.dcs_update(subject, branch='openbeta', version='2.9.9') == 0

    assert _Protocol.sequence == ['step_down', 'wait_for_clear', 'audit', 'update', 'restore', 'audit']
    assert bus.messages() == [
        "started an update to version 2.9.9@openbeta of all DCS servers on node node-1.",
        "DCS World updated to version 2.9.9 on node node-1."
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('granted,residue,expected', [
    (False, 0, UPDATE_STOOD_BY),
    (True, 2, UPDATE_SERVERS_UP),
])
async def test_a_refused_update_writes_no_audit_at_all(bus, taker, granted, residue, expected):
    """Neither refusal ran an installer, so neither may claim a start — the alert is the whole record."""
    taker.granted = granted
    taker.residue = residue
    subject = _Node(resources={RESOURCE: 'server'})

    assert await NodeImpl.dcs_update(subject, branch='openbeta', version='2.9.9') == expected

    assert bus.events() == []
    assert subject.update_calls == []


@pytest.mark.asyncio
async def test_a_failed_update_still_writes_its_end_row(bus):
    """The installer ran and answered with an error: the pair is complete and the end row names the result."""
    subject = _Node(rc=2)

    assert await NodeImpl.dcs_update(subject, branch='openbeta', version='2.9.9') == 2

    assert bus.messages() == [
        "started an update to version 2.9.9@openbeta of all DCS servers on node node-1.",
        "DCS World update on node node-1 was aborted (check disk space)!"
    ]


@pytest.mark.asyncio
async def test_dcs_update_produces_exactly_one_start_row_and_carries_the_actor(bus):
    """The command layer must not add its own start row: the node's pair is the update's only record."""
    from plugins.admin.commands import Admin

    node = _CommandNode(_Node())
    admin = object.__new__(Admin)
    admin.bot = _Bot()
    interaction = _Interaction()

    await Admin.dcs_update.callback(admin, interaction, node, warn_time=120, announce=False, force=True)

    starts = [message for message in bus.messages() if message.startswith('started an update')]
    assert len(starts) == 1
    (started,) = [event for event in bus.events() if event['message'].startswith('started an update')]
    assert started['actor'] == 'SpecialK'
    assert [message for message in admin.bot.audits if 'started an update' in message] == []
    assert node.called_kwargs['actor'] == 'SpecialK'


# ------------------------------------------------------------------------------------ the window's own rows

@pytest.mark.asyncio
async def test_step_down_audits_the_path_and_the_node(bus, monkeypatch):
    """RED: a node that stepped down for somebody else's window left only a line in its own log file."""
    monkeypatch.setattr(resource_window, 'ServerMaintenanceManager', _Manager)
    node = _WindowNode([_server('Growling Sidewinder')])
    registry = _WindowRegistry(['Growling Sidewinder'])
    windows = _WindowWindows(OpenWindow(holder_guild=99, holder_node='headless', action='update', scope='server',
                                        holder_live=True))
    subject = ResourceWindowProtocol(node, registry=registry, windows=windows)

    assert await subject.step_down(RESOURCE) == 1

    (message,) = bus.messages()
    assert PATH in message and 'node-1' in message
    assert 'headless' in message and 'update' in message
    assert '1 of 1' in message
    assert RESOURCE not in message, 'the row names the installation, never the derived id'


@pytest.mark.asyncio
async def test_restore_audits_its_own_row(bus, monkeypatch):
    monkeypatch.setattr(resource_window, 'ServerMaintenanceManager', _Manager)
    node = _WindowNode([_server('Growling Sidewinder', running=False)])
    registry = _WindowRegistry(['Growling Sidewinder'])
    registry.rows = [('server', 'Growling Sidewinder', 'stepped_down')]
    subject = ResourceWindowProtocol(node, registry=registry, windows=_WindowWindows())

    assert await subject.restore(RESOURCE) == 1

    (message,) = bus.messages()
    assert message.startswith('restored 1 DCS server(s)')
    assert PATH in message and 'node-1' in message
    assert RESOURCE not in message


@pytest.mark.asyncio
async def test_an_audit_that_fails_never_aborts_the_step_down_or_the_restore(bus, monkeypatch, caplog):
    """An unreachable audit channel must not leave a shared installation stepped down."""
    monkeypatch.setattr(resource_window, 'ServerMaintenanceManager', _Manager)
    node = _WindowNode([_server('Growling Sidewinder')])
    registry = _WindowRegistry(['Growling Sidewinder'])
    windows = _WindowWindows(OpenWindow(holder_guild=99, holder_node='headless', action='update', scope='server',
                                        holder_live=True))
    bus.raises = RuntimeError('the bot service is gone')

    with caplog.at_level('WARNING', logger='core.data.impl.nodeimpl'):
        assert await ResourceWindowProtocol(node, registry=registry, windows=windows).step_down(RESOURCE) == 1
        assert windows.acks == [RESOURCE], 'the window is answered even though the row was lost'
        registry.rows = [('server', 'Growling Sidewinder', 'stepped_down')]
        node.servers[0].status = Status.SHUTDOWN
        assert await ResourceWindowProtocol(node, registry=registry, windows=windows).restore(RESOURCE) == 1

    assert registry.state_calls[-1] == ('in_service', {'Growling Sidewinder'})
    failures = [record for record in caplog.records if 'Could not write the audit entry' in record.message]
    assert len(failures) == 2, 'both writes were attempted and both were reported'


@pytest.mark.asyncio
async def test_a_failed_path_read_still_names_the_installation(bus, monkeypatch):
    monkeypatch.setattr(resource_window, 'ServerMaintenanceManager', _Manager)
    node = _WindowNode([_server('Growling Sidewinder')])
    node.installation = '/opt/dcs'
    registry = _WindowRegistry(['Growling Sidewinder'])

    async def broken_path(resource_id):
        raise RuntimeError('the cluster database is closed')

    registry.path = broken_path
    subject = ResourceWindowProtocol(node, registry=registry, windows=_WindowWindows(
        OpenWindow(holder_guild=99, holder_node='headless', action='update', scope='server', holder_live=True)))

    assert await subject.step_down(RESOURCE) == 1
    (message,) = bus.messages()
    assert '/opt/dcs' in message


# ------------------------------------------------------------------------- the publish cycle stays silent

@pytest.mark.asyncio
async def test_publish_never_audits_and_warns_once_per_distinct_mismatch(bus, monkeypatch, caplog):
    """RED: the mismatch warning repeated every five seconds — about twelve WARNING lines a minute."""
    monkeypatch.setattr(resource_window, 'ServerMaintenanceManager', _Manager)
    node = _WindowNode([_server('Growling Sidewinder')])
    registry = _WindowRegistry(['DCS.openbeta_server_1'], path=PATH)
    subject = ResourceWindowProtocol(node, registry=registry, windows=_WindowWindows())

    with caplog.at_level('DEBUG', logger='core.data.resource_window'):
        assert await subject.publish(RESOURCE) == 1
        assert await subject.publish(RESOURCE) == 1
        assert bus.events() == [], 'publishing is not an event'

        assert len(node.log.texts('warning')) == 1, 'one WARNING, on the node log, for the first sighting'
        repeats = [r for r in caplog.records if 'name mismatch' in r.getMessage()]
        assert [r.levelname for r in repeats] == ['DEBUG'], 'the second cycle repeats into DEBUG only'

        caplog.clear()
        node.servers.append(_server('Second Server'))
        assert await subject.publish(RESOURCE) == 2
        assert len(node.log.texts('warning')) == 2, 'a changed running set is a new mismatch'


@pytest.mark.asyncio
async def test_publish_says_nothing_for_a_node_whose_names_match(bus, monkeypatch):
    """CONTROL: identical names log nothing at all, on either cycle."""
    monkeypatch.setattr(resource_window, 'ServerMaintenanceManager', _Manager)
    node = _WindowNode([_server('Growling Sidewinder')])
    registry = _WindowRegistry(['Growling Sidewinder'], path=PATH)
    subject = ResourceWindowProtocol(node, registry=registry, windows=_WindowWindows())

    assert await subject.publish(RESOURCE) == 1
    assert await subject.publish(RESOURCE) == 1

    assert node.log.texts('warning') == []
    assert bus.events() == []


# ------------------------------------------------------------------------------------------------ controls

@pytest.mark.asyncio
async def test_a_successful_update_still_ends_with_todays_wording(bus):
    """CONTROL: the end row reads exactly as it did before this change — one row, one wording."""
    subject = _Node()

    await NodeImpl.dcs_update(subject, branch='openbeta', version='2.9.9')

    ends = [event for event in bus.events() if event['message'].startswith('DCS World updated')]
    assert [event['message'] for event in ends] == [
        "DCS World updated to version 2.9.9 on node node-1."
    ]


@pytest.mark.asyncio
async def test_a_node_with_nothing_to_step_down_still_reports_it(bus, monkeypatch):
    """The taker's own step-down of an empty node answers the window and says so once."""
    monkeypatch.setattr(resource_window, 'ServerMaintenanceManager', _Manager)
    node = _WindowNode()
    registry = _WindowRegistry(['Growling Sidewinder'])
    windows = _WindowWindows(OpenWindow(holder_guild=42, holder_node='node-1', action='update', scope='server',
                                        holder_live=True))

    assert await ResourceWindowProtocol(node, registry=registry, windows=windows).step_down(RESOURCE) == 0

    (message,) = bus.messages()
    assert 'nothing' in message and PATH in message and 'node-1' in message
    assert windows.acks == [RESOURCE]


# ------------------------------------------------------------------------------ the command layer's doubles

class _CommandNode:
    """The node PROXY as the command sees it: it forwards to the node method, as the RPC does."""

    name = 'node-1'

    def __init__(self, subject: _Node):
        self.subject = subject
        self.called_kwargs: dict = {}

    async def get_dcs_branch_and_version(self):
        return 'openbeta', '2.9.0'

    async def get_latest_version(self, branch):
        return '2.9.9'

    async def dcs_update(self, **kwargs):
        self.called_kwargs = kwargs
        return await NodeImpl.dcs_update(self.subject, **kwargs)


class _Bot:
    def __init__(self):
        self.audits: list[str] = []

    async def audit(self, message, **_kwargs):
        self.audits.append(message)


class _Message:
    def __init__(self):
        self.edits: list[str] = []

    async def edit(self, content):
        self.edits.append(content)


class _Response:
    async def defer(self, **_kwargs):
        pass


class _Followup:
    def __init__(self):
        self.sent: list[str] = []
        self.message = _Message()

    async def send(self, content=None, **_kwargs):
        self.sent.append(content)
        return self.message


class _User:
    id = 4711
    display_name = 'SpecialK'
    roles: list = []


class _Interaction:
    def __init__(self):
        self.response = _Response()
        self.followup = _Followup()
        self.user = _User()
        # get_ephemeral asks the client for the server; none means the answer is ephemeral
        self.client = types.SimpleNamespace(get_server=lambda interaction: None)
