"""A cluster-wide power command runs its nodes at once, not one after another.

``/node offline`` and ``/node online`` fan out to every node, and each node's action runs the engine's
popup chain for its servers — minutes, not milliseconds. One after another makes the operator wait for
the SUM of the nodes when the real cost is the SLOWEST one, and the master, being last, is the one that
appears stuck.

Concurrency is safe here and the seam already says so: ``core.actions.call_action``'s in-flight guard is
keyed per target, so ``node:DE`` and ``node:US`` may run together, while a second call against the SAME
node is still refused. The overlap is pinned by having each fake call wait for the other to be in
flight: a serial implementation never gets the second start, times out, and fails the test.

``plugins/admin/commands.py`` cannot be imported at collection time — its Discord commands are built at
import and need a running bot — so the module comes from a fixture. The *actions* the fan-out calls are
covered by ``tests/test_admin_node_power.py``; this file is only about the fan-out itself.
"""
from __future__ import annotations

import asyncio
import importlib
import types

import pytest

from core import Status
from core.actions import ActionResult


@pytest.fixture
def admin_commands():
    """Import ``plugins.admin.commands`` with the ``BotService`` lookup stubbed.

    The module builds its app-commands at class-body time, where ``core.plugin.Command`` reads
    ``ServiceRegistry.get(BotService).bot`` — a cluster a bare test interpreter has none of.
    """
    from core.services.registry import ServiceRegistry
    from services.bot import BotService

    stub_bot = types.SimpleNamespace(node=types.SimpleNamespace(all_nodes={'n': 1}), servers={}, locals={})
    saved = ServiceRegistry._singletons.get(BotService, None)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=stub_bot)
    try:
        yield importlib.import_module('plugins.admin.commands')
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


class _Calls:
    """A ``call_action`` that records when each node's action starts and finishes."""

    def __init__(self, fail_for: str | None = None, expect_overlap: bool = True):
        self.events: list[tuple[str, str | None]] = []
        self.both_in_flight = asyncio.Event()
        self.fail_for = fail_for
        self.expect_overlap = expect_overlap

    async def __call__(self, qualname, ctx, /, *, node_name=None, **params):
        self.events.append(('start', node_name))
        if len([1 for kind, _name in self.events if kind == 'start']) >= 2:
            self.both_in_flight.set()
        if self.expect_overlap:
 # a serial fan-out never reaches the second start, so this times out rather than passing
            await asyncio.wait_for(self.both_in_flight.wait(), timeout=1.0)
        self.events.append(('end', node_name))
        if node_name == self.fail_for:
            raise RuntimeError('boom')
        return ActionResult(success=True, message=f'{node_name} done')


class _Followup:
    def __init__(self):
        self.messages: list[str] = []

    async def send(self, message, **kwargs):
        self.messages.append(message)


class _Response:
    def __init__(self):
        self.deferred: list[dict] = []

    async def defer(self, **kwargs):
        self.deferred.append(kwargs)


class _Interaction:
    def __init__(self):
        self.followup = _Followup()
        self.response = _Response()


class _Log:
    exceptions: list = []

    def exception(self, ex, *args, **kwargs):
        _Log.exceptions.append(ex)


class _Cog:
    """The slice of the Admin cog that the power commands touch."""

    def __init__(self, names):
        self.names = names
        self.log = _Log()
        self.powered: list[tuple[str, dict]] = []

    async def _power_targets(self, node):
        return list(self.names)

    async def _power_nodes(self, interaction, qualname, node, *, ephemeral, **params):
        """Records the call, so a test can tell "asked and declined" from "acted"."""
        self.powered.append((qualname, params))


@pytest.fixture
def parallel(admin_commands, monkeypatch):
    """Install a fake ``call_action``; returns the installer so a test can pick its behaviour."""
    _Log.exceptions = []

    def install(fake):
        monkeypatch.setattr(admin_commands, 'call_action', fake)
        monkeypatch.setattr(admin_commands.ActionContext, 'from_interaction',
                            classmethod(lambda cls, interaction: object()))

    install(_Calls())
    return install


@pytest.mark.asyncio
async def test_every_node_starts_before_any_of_them_finishes(admin_commands, parallel):
    """The whole point: the nodes overlap, so the wall clock is the slowest node, not their sum."""
    calls = _Calls()
    parallel(calls)
    interaction = _Interaction()
    await admin_commands.Admin._power_nodes(_Cog(['DE', 'US']), interaction, 'take_node_offline', None,
                                           ephemeral=True, maintenance=True)
    assert [kind for kind, _name in calls.events[:2]] == ['start', 'start']
    assert sorted(name for kind, name in calls.events if kind == 'end') == ['DE', 'US']


@pytest.mark.asyncio
async def test_every_node_still_reports_to_the_operator(admin_commands, parallel):
    interaction = _Interaction()
    await admin_commands.Admin._power_nodes(_Cog(['DE', 'US', 'master']), interaction,
                                           'take_node_offline', None, ephemeral=True)
    assert sorted(interaction.followup.messages) == ['DE done', 'US done', 'master done']


@pytest.mark.asyncio
async def test_one_failing_node_does_not_hold_up_the_others(admin_commands, parallel):
    """The serial loop's exception ended the fan-out; the remaining nodes never ran."""
    parallel(_Calls(fail_for='DE'))
    interaction = _Interaction()
    await admin_commands.Admin._power_nodes(_Cog(['DE', 'US']), interaction, 'take_node_offline', None,
                                           ephemeral=True)
    assert 'US done' in interaction.followup.messages
    assert any('DE' in message and 'failed' in message for message in interaction.followup.messages)
    assert _Log.exceptions, 'the failure is logged with its traceback, not swallowed'


@pytest.mark.asyncio
async def test_a_single_named_node_is_still_one_call(admin_commands, parallel):
    calls = _Calls(expect_overlap=False)
    parallel(calls)
    await admin_commands.Admin._power_nodes(_Cog(['DE']), _Interaction(), 'take_node_offline', None,
                                           ephemeral=True)
    assert calls.events == [('start', 'DE'), ('end', 'DE')]


def test_the_in_flight_guard_keys_a_target_per_node():
    """Why the fan-out is allowed to be concurrent at all — pinned against the guard's own helper, because the
    tests above replace ``call_action`` and so never exercise it.

    The guard is per target: ``node:de`` and ``node:us`` are two targets and both are admitted, while a second
    call against the SAME node (whatever its casing or padding) is still refused.
    """
    from core.actions import _normalise_target

    assert _normalise_target({'node_name': 'DE'})[1] == 'node:de'
    assert _normalise_target({'node_name': 'US'})[1] == 'node:us'
    assert _normalise_target({'node_name': 'DE'})[1] == _normalise_target({'node_name': ' de '})[1]


# ------------------------------------------------------------ the confirmation comes first

@pytest.mark.parametrize('maintenance, expected', [(True, 'set them to maintenance'),
                                                   (False, 'untouched')])
@pytest.mark.asyncio
async def test_offline_asks_before_it_touches_anything(admin_commands, monkeypatch, maintenance, expected):
    """The question covers the OPERATION, not the maintenance flag: `maintenance:false` still stops servers,
    and asking only when the flag was set left exactly that run with no confirmation at all."""
    asked = []

    async def declined(interaction, question, **kwargs):
        embed = kwargs.get('embed')
        asked.append(embed.description if embed else question)
        return False

    monkeypatch.setattr(admin_commands.utils, 'get_ephemeral', lambda interaction: True)
    monkeypatch.setattr(admin_commands.utils, 'yn_question', declined)
    cog = _Cog(['DE'])
    await admin_commands.Admin.offline.callback(cog, _Interaction(), None, maintenance=maintenance)
    assert asked, 'the question must be asked'
    assert expected in asked[0], 'and it must say what this run will do'
    assert cog.powered == [], 'nothing may run when it is declined'


@pytest.mark.asyncio
async def test_offline_runs_when_the_question_is_accepted(admin_commands, monkeypatch):
    async def accepted(interaction, question, **kwargs):
        return True

    monkeypatch.setattr(admin_commands.utils, 'get_ephemeral', lambda interaction: True)
    monkeypatch.setattr(admin_commands.utils, 'yn_question', accepted)
    cog = _Cog(['DE'])
    await admin_commands.Admin.offline.callback(cog, _Interaction(), None, maintenance=False)
    assert cog.powered == [('take_node_offline', {'maintenance': False})]


# ------------------------------------------------------------ /node upgrade keeps its question

class _UpgradeCog(_Cog):
    """The slice ``/node upgrade`` touches: this node, its servers, the two group runners and the two ways a
    question can be asked (its own, and the shared node-lifecycle one)."""

    def __init__(self, *, is_major: bool, servers: tuple = ()):
        from plugins.admin.commands import Admin

        super().__init__(['master-node'])
        # the shared node-lifecycle question is an Admin method: bind the real one, so the count of
        # questions asked is the count the command really asks
        self._confirm_node_operation = types.MethodType(Admin._confirm_node_operation, self)
        self.bot = types.SimpleNamespace(servers={server.name: server for server in servers})
        self.upgraded: list = []
        self.offlines: list = []

        async def pending():
            return True

        async def major():
            return is_major

        async def no_active_nodes():
            return []

        async def upgrade(*args, **kwargs):
            self.upgraded.append(True)

        self.node = types.SimpleNamespace(
            name='master-node', master=True, upgrade_pending=pending, upgrade_is_major=major,
            get_active_nodes=no_active_nodes, upgrade=upgrade, shutdown=upgrade, restart=upgrade,
        )

    async def _power_nodes(self, interaction, qualname, node, *, ephemeral, **params):
        self.offlines.append((qualname, params))


@pytest.mark.asyncio
async def test_a_plain_upgrade_still_asks(admin_commands, monkeypatch):
    """This is the case the confirmation had been removed from: a cluster upgrade that is not major, with
    nothing else asking. The shared node-lifecycle question owns that case, so it must be asked — passing
    ``confirm=False`` unconditionally is exactly what left the operator with no question at all."""
    asked = []

    async def declined(interaction, question, **kwargs):
        asked.append(question)
        return False

    monkeypatch.setattr(admin_commands.utils, 'get_ephemeral', lambda interaction: True)
    monkeypatch.setattr(admin_commands.utils, 'yn_question', declined)
    cog = _UpgradeCog(is_major=False)
    await admin_commands.Admin.upgrade.callback(cog, _Interaction(), None)
    assert len(asked) == 1, 'a plain upgrade must still ask'
    assert 'sure' in asked[0] or 'really' in asked[0]
    assert cog.offlines == [], 'and nothing may be upgraded when it is declined'
    assert cog.upgraded == [], 'no upgrade runs when it is declined'


@pytest.mark.asyncio
async def test_an_accepted_upgrade_asks_once_and_then_upgrades(admin_commands, monkeypatch):
    asked = []

    async def accepted(interaction, question, **kwargs):
        asked.append(question)
        return True

    monkeypatch.setattr(admin_commands.utils, 'get_ephemeral', lambda interaction: True)
    monkeypatch.setattr(admin_commands.utils, 'yn_question', accepted)
    cog = _UpgradeCog(is_major=False)
    await admin_commands.Admin.upgrade.callback(cog, _Interaction(), None)
    assert len(asked) == 1
    assert cog.offlines == [('upgrade_node', {'force': False})], \
        'the upgrade reaches the action with force off by default'


@pytest.mark.asyncio
async def test_a_major_upgrade_with_servers_is_asked_once_not_twice(admin_commands, monkeypatch):
    """The major path asks ONE combined question (offline + upgrade) and then does not ask again. That is the
    only case where a second question is wrong — and it must not become a case where there is none."""
    asked = []

    async def accepted(interaction, question, **kwargs):
        asked.append(question)
        return True

    monkeypatch.setattr(admin_commands.utils, 'get_ephemeral', lambda interaction: True)
    monkeypatch.setattr(admin_commands.utils, 'yn_question', accepted)
    running = types.SimpleNamespace(name='WW2', status=Status.RUNNING)
    cog = _UpgradeCog(is_major=True, servers=(running,))
    await admin_commands.Admin.upgrade.callback(cog, _Interaction(), None)
    assert len(asked) == 1, 'one question for the offline step and the upgrade together'
    assert 'MAJOR' in asked[0]
    assert cog.offlines == [('take_node_offline', {'maintenance': True}),
                            ('upgrade_node', {'force': False})]