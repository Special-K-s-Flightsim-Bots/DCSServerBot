"""Federation: a dependents row has to name the SERVER, because that is what every consumer compares.

``register_resources`` files this node's servers under its DCS installation, and the consumers of those rows
(``publish``, ``step_down``) filter them with ``getattr(server, 'name', None) in names``. That name is read from
``Config/serverSettings.lua`` and differs from the ``instances:`` key of nodes.yaml in any normal install
(``DCS.openbeta_server_1`` vs ``Growling Sidewinder``), so a row keyed by the folder name matches nothing: the
follower publishes 0 while its DCS runs and acknowledges a window it stopped nothing for.

The doubles mirror the shapes the code reads — ``node.instances`` maps a key to an instance object carrying
``.server``, and ``server_name`` resolves the bound server first, like ``InstanceImpl`` does.
"""
from __future__ import annotations

import types

import pytest

from core import Status
from core.data import resource_window
from core.data.impl import nodeimpl
from core.data.maintenance import ServerMaintenanceManager
from core.data.resource_window import ResourceWindowProtocol

RESOURCE = 'a3f1c2d4e5b60718293a4b5c6d7e8f90112233445566778899aabbccddeeff00'
INSTALL = '//nas/dcs'                       # a network path needs no machine id, so the identity derives anywhere
INSTANCE_KEY = 'DCS.openbeta_server_1'      # the ``instances:`` key in nodes.yaml
SERVER_NAME = 'Growling Sidewinder'         # what serverSettings.lua calls the same server


# -------------------------------------------------------------------------------------------------- doubles

class _Server:
    def __init__(self, name: str, status=Status.RUNNING):
        self.name = name
        self.status = status


class _Instance:
    """An instance as the code reads it: the ``instances:`` key, the bound server, and the name it resolves to."""

    def __init__(self, key: str, settings_name: str | None):
        self.name = key
        self._server_name = settings_name
        self.server: _Server | None = None

    @property
    def server_name(self) -> str | None:
        return self.server.name if self.server else self._server_name


class _Log:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def _record(self, level, msg, *args):
        self.lines.append((level, msg % args if args else msg))

    def info(self, msg, *args):
        self._record('info', msg, *args)

    def warning(self, msg, *args):
        self._record('warning', msg, *args)

    def error(self, msg, *args):
        self._record('error', msg, *args)

    def texts(self, level: str | None = None) -> list[str]:
        return [text for line_level, text in self.lines if level is None or line_level == level]


class _Node:
    def __init__(self, instances=(), installation=INSTALL):
        self.installation = installation
        self.instances = {instance.name: instance for instance in instances}
        self.resources: dict[str, str] = {}
        self.log = _Log()
        self.name = 'node-1'
        self.guild_id = 42
        self.locals = {}


class _RecordingRegistry:
    """What ``register_resources`` handed to ``ResourceRegistry.register``."""

    calls: list = []

    def __init__(self, node=None):
        self.node = node

    async def register(self, identity, *, dependents=(), servers_up=0):
        _RecordingRegistry.calls.append((identity.id, tuple(dependents), servers_up))


class _Registry:
    """The registry's dependents surface, holding rows of one node."""

    def __init__(self, dependent_names=(), state: str = 'in_service'):
        self.rows = [('server', name, state) for name in dependent_names]
        self.up_calls: list[int] = []
        self.state_calls: list[tuple[str, set[str]]] = []

    async def dependents(self, resource_id, **kwargs):
        if kwargs.get('states') == ('stepped_down',):
            return [row for row in self.rows if row[2] == 'stepped_down']
        return list(self.rows)

    async def set_dependents_state(self, resource_id, state, names):
        self.state_calls.append((state, set(names)))

    async def set_servers_up(self, resource_id, servers_up):
        self.up_calls.append(servers_up)

    async def in_service_count(self, resource_id):
        return 0


class _Manager:
    """The maintenance manager's power surface, with the REAL in-service test bound to it."""

    in_service = staticmethod(ServerMaintenanceManager.in_service)
    calls: list = []

    def __init__(self, node, *, message=None, **_kwargs):
        self.node = node
        _Manager.calls.append(('init', message))

    def node_servers(self):
        return [instance.server for instance in self.node.instances.values()]

    async def power_off(self, servers, **kwargs):
        _Manager.calls.append(('power_off', kwargs, [server.name for server in servers]))
        for server in servers:
            server.status = Status.SHUTDOWN
        return types.SimpleNamespace(stopped=list(servers), handled=list(servers))

    async def power_on(self, servers, **kwargs):
        _Manager.calls.append(('power_on', kwargs, [server.name for server in servers]))
        started = [server for server in servers if not self.in_service(server)]
        for server in started:
            server.status = Status.RUNNING
        return types.SimpleNamespace(started=started, skipped=[], cleared=[])


class _Windows:
    def __init__(self):
        self.acks: list[str] = []

    async def state(self, resource_id):
        return None

    async def ack(self, resource_id):
        self.acks.append(resource_id)

    async def unack(self, resource_id):
        if resource_id in self.acks:
            self.acks.remove(resource_id)

    async def acked(self, resource_id):
        return resource_id in self.acks


@pytest.fixture(autouse=True)
def doubles(monkeypatch):
    _RecordingRegistry.calls = []
    _Manager.calls = []
    monkeypatch.setattr(nodeimpl, 'ResourceRegistry', _RecordingRegistry)
    monkeypatch.setattr(resource_window, 'ServerMaintenanceManager', _Manager)


def bound(instance: _Instance) -> _Instance:
    """The instance once ``ServiceBus.init_servers`` has bound its server."""
    instance.server = _Server(instance.server_name)
    return instance


def power_calls() -> list:
    return [call for call in _Manager.calls if call[0] != 'init']


def protocol(node, registry, windows=None) -> ResourceWindowProtocol:
    return ResourceWindowProtocol(node, registry=registry, windows=windows or _Windows())


# ------------------------------------------------------------------------------------- registration

@pytest.mark.asyncio
async def test_registration_hands_the_server_name_to_the_registry():
    """RED: the instance key is the Saved Games folder, which no consumer ever compares against."""
    node = _Node([_Instance(INSTANCE_KEY, SERVER_NAME)])

    await nodeimpl.NodeImpl.register_resources(node)

    (_resource_id, dependents, _servers_up), = _RecordingRegistry.calls
    assert dependents == (('server', SERVER_NAME),)
    assert ('server', INSTANCE_KEY) not in dependents


@pytest.mark.asyncio
async def test_registration_skips_an_instance_that_names_no_server():
    """``None`` and the ``n/a`` placeholder name no server, so a row for either is a row nobody can match."""
    node = _Node([_Instance('key_none', None), _Instance('key_na', 'n/a'), _Instance('key_live', 'Live One')])

    await nodeimpl.NodeImpl.register_resources(node)

    (_resource_id, dependents, _servers_up), = _RecordingRegistry.calls
    assert dependents == (('server', 'Live One'),)


@pytest.mark.asyncio
async def test_registration_uses_the_bound_name_once_the_servers_are_bound():
    """The second registration (after ``init_servers``) is what makes a rename between boots take effect."""
    instance = _Instance(INSTANCE_KEY, 'Stale Name')
    instance.server = _Server(SERVER_NAME)          # bound

    await nodeimpl.NodeImpl.register_resources(_Node([instance]))

    (_resource_id, dependents, _servers_up), = _RecordingRegistry.calls
    assert dependents == (('server', SERVER_NAME),)


@pytest.mark.asyncio
async def test_the_bound_servers_are_registered_again_once_the_bus_has_them():
    """``init_servers`` binds ``instance.server``; the registration must then run again under the bound names."""
    from services.servicebus.service import ServiceBus

    calls: list[bool] = []

    async def register_resources():
        calls.append(True)

    bus = types.SimpleNamespace(node=types.SimpleNamespace(register_resources=register_resources))
    ServiceBus.register_resource_names(bus)

    assert bus._resource_registration is not None
    await bus._resource_registration
    assert calls == [True]


def test_the_second_registration_needs_a_running_loop_and_says_nothing_when_there_is_none():
    from services.servicebus.service import ServiceBus

    bus = types.SimpleNamespace(node=types.SimpleNamespace(register_resources=None))
    assert ServiceBus.register_resource_names(bus) is None


# ----------------------------------------------------------------------------------- the consumers

@pytest.mark.asyncio
async def test_the_server_registered_is_the_server_stepped_down():
    """End to end on the shipped shape: register first, then follow the rows the registration wrote."""
    instance = bound(_Instance(INSTANCE_KEY, SERVER_NAME))
    node = _Node([instance])
    await nodeimpl.NodeImpl.register_resources(node)
    names = [name for _kind, name in _RecordingRegistry.calls[0][1]]

    windows = _Windows()
    subject = protocol(node, _Registry(names), windows)
    assert await subject.step_down(RESOURCE) == 1

    assert power_calls()[0] == ('power_off', {'flag': False, 'stop': True}, [SERVER_NAME])
    assert windows.acks == [RESOURCE]
    assert any('Stepped down' in line for line in node.log.texts('info'))


@pytest.mark.asyncio
async def test_publish_reports_the_real_count_when_the_names_do_not_match():
    """RED: the bug published 0 while DCS ran, which is what let an installer start underneath it."""
    instance = bound(_Instance(INSTANCE_KEY, SERVER_NAME))
    node = _Node([instance])
    registry = _Registry([INSTANCE_KEY])            # the rows the folder-name registration wrote

    subject = protocol(node, registry)
    assert await subject.publish(RESOURCE) == 1
    assert registry.up_calls == [1]

    (warning,) = node.log.texts('warning')
    assert INSTANCE_KEY in warning and SERVER_NAME in warning


@pytest.mark.asyncio
async def test_step_down_never_acknowledges_a_window_it_stopped_nothing_for():
    """RED: the bug acked the window with nothing stopped, and the taker started the installer."""
    instance = bound(_Instance(INSTANCE_KEY, SERVER_NAME))
    node = _Node([instance])
    registry = _Registry([INSTANCE_KEY])
    windows = _Windows()

    assert await protocol(node, registry, windows).step_down(RESOURCE) == 0
    assert windows.acks == []
    assert power_calls() == []
    assert any(INSTANCE_KEY in line and SERVER_NAME in line for line in node.log.texts('warning'))


@pytest.mark.asyncio
async def test_a_node_with_nothing_running_still_answers_the_window():
    """CONTROL: an empty node stopped nothing because there was nothing to stop — it is done, and says so."""
    instance = _Instance(INSTANCE_KEY, SERVER_NAME)
    instance.server = _Server(SERVER_NAME, status=Status.SHUTDOWN)
    node = _Node([instance])
    registry = _Registry([INSTANCE_KEY])
    windows = _Windows()

    assert await protocol(node, registry, windows).step_down(RESOURCE) == 0
    assert windows.acks == [RESOURCE]
    assert registry.up_calls == [0]
    assert node.log.texts('warning') == []


# -------------------------------------------------------------------------------------- controls

@pytest.mark.asyncio
async def test_identical_names_publish_exactly_as_today():
    """CONTROL: key and server name the same — the pre-fix behaviour, unchanged."""
    instance = bound(_Instance('Server-1', 'Server-1'))
    node = _Node([instance, _Instance('Server-2', 'Server-2')])
    node.instances['Server-2'].server = _Server('Server-2', status=Status.SHUTDOWN)
    registry = _Registry(['Server-1', 'Server-2'])

    assert await protocol(node, registry).publish(RESOURCE) == 1
    assert registry.up_calls == [1]
    assert node.log.texts('warning') == []


@pytest.mark.asyncio
async def test_identical_names_step_down_exactly_as_today():
    """CONTROL: the same names stop the server, record it, publish, acknowledge and log."""
    instance = bound(_Instance('Server-1', 'Server-1'))
    node = _Node([instance])
    registry = _Registry(['Server-1'])
    windows = _Windows()

    assert await protocol(node, registry, windows).step_down(RESOURCE) == 1
    assert power_calls()[0] == ('power_off', {'flag': False, 'stop': True}, ['Server-1'])
    assert registry.state_calls[-1] == ('stepped_down', {'Server-1'})
    assert registry.up_calls[-1] == 0
    assert windows.acks == [RESOURCE]
    assert node.log.texts('warning') == []
    assert any(line == '- Stepped down for ' f'{RESOURCE}: 1 of 1 server(s) stopped.'
               for line in node.log.texts('info'))