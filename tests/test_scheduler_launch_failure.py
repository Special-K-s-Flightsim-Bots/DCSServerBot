"""A failed DCS launch must be visible - the state loop's fire-and-forget task is not a reason for it to vanish.

``Scheduler.launch_dcs`` used to handle only ``TimeoutError``, and the state loop fires it as a task whose
reference is dropped, so a launch that failed for any other reason (a port already in use among them) left no
line anywhere. The command path had the same cause by a different route: it waited for the server to reach
LOADING *before* awaiting the task, so a failure that happens earlier came out as a 180s status timeout and
three "crashed during startup" retries - the real message never printed.

Both halves are driven here with doubles: one on the two reporting paths of ``launch_dcs``, one on the command
flow, which must show the reason on the embed it already edits instead of waiting for a status that never comes.
"""
from __future__ import annotations

import importlib
import logging
import types

import pytest

from core import Status
from core.data.impl.serverimpl import PortConflictError

REASON = ("DCS port 5040 is already in use on 0.0.0.0. Another DCS server, or a second bot cluster on "
          "this machine, is most likely holding it. Startup refused - give this instance a different port "
          "in nodes.yaml.")


@pytest.fixture
def scheduler_commands():
    """Import ``plugins.scheduler.commands`` with the ``BotService`` lookup stubbed.

    The module builds its app-commands at class-body time, where ``core.plugin.Command`` reads
    ``ServiceRegistry.get(BotService).bot`` - a cluster a bare test interpreter has none of.
    """
    from core.services.registry import ServiceRegistry
    from services.bot import BotService

    stub_bot = types.SimpleNamespace(node=types.SimpleNamespace(all_nodes={"n": 1}),
                                     servers={}, locals={})
    saved = ServiceRegistry._singletons.get(BotService, None)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=stub_bot)
    try:
        yield importlib.import_module("plugins.scheduler.commands")
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


class _Log:
    """Records what was logged, with the level, so an error is not mistaken for a debug line."""

    def __init__(self):
        self.lines = []

    def _record(self, level, msg, *args, **kwargs):
        self.lines.append((level, msg if not args else msg % args))

    def info(self, msg, *args, **kwargs):
        self._record(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._record(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._record(logging.ERROR, msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        self._record(logging.ERROR, msg, *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        self._record(logging.DEBUG, msg, *args, **kwargs)


class _Bot:
    def __init__(self):
        self.audits = []

    async def audit(self, message, **kwargs):
        self.audits.append(message)


class _Server:
    def __init__(self, name="SRS-1"):
        from core import Status
        self.name = name
        self.display_name = name
        self.status = Status.SHUTDOWN
        self.maintenance = False
        self.status_waits = []

    async def startup(self, **_kwargs):
        raise PortConflictError(REASON)

    async def get_current_mission_file(self):
        return None

    async def getMissionList(self):
        return []

    async def wait_for_status_change(self, status=None, timeout=None):
        self.status_waits.append((status, timeout))
        raise TimeoutError()


class _Embed:
    def __init__(self):
        self.description = ""
        self.thumbnail = None

    def set_thumbnail(self, url):
        self.thumbnail = url


class _Msg:
    def __init__(self):
        self.edits = []

    async def edit(self, embed=None, **kwargs):
        self.edits.append(embed.description if embed is not None else None)


class _Interaction:
    def __init__(self):
        self.user = types.SimpleNamespace(display_name="tester")
        self.followup = types.SimpleNamespace(send=self._send)
        self.sent = []

    async def _send(self, embed=None, **kwargs):
        self.sent.append(embed.description if embed is not None else None)
        return _Msg()


def _scheduler(raise_on_launch=True):
    """The slice of the cog the two methods read: a log, a bot to audit on, and the launch itself."""
    self_ = types.SimpleNamespace(log=_Log(), bot=_Bot(), __cog_name__="Scheduler")

    async def launch_dcs(s, member=None, **kwargs):
        if raise_on_launch:
            raise PortConflictError(REASON)
    self_.launch_dcs = launch_dcs
    return self_


# ------------------------------------------------------------------ launch_dcs: report, then behave as asked

@pytest.mark.asyncio
async def test_launch_dcs_logs_and_audits_a_non_timeout_failure(scheduler_commands):
    """The failure has to leave a trace: an error line and an audit entry, not silence."""
    self_ = _scheduler()
    await scheduler_commands.Scheduler.launch_dcs(self_, _Server(), ignore_exception=True)
    assert any(level == logging.ERROR and "could not be started" in line for level, line in self_.log.lines), \
        self_.log.lines
    assert any(REASON in line for _level, line in self_.log.lines), self_.log.lines
    assert self_.bot.audits and REASON in self_.bot.audits[0], self_.bot.audits


@pytest.mark.asyncio
async def test_launch_dcs_re_raises_unless_the_caller_asked_for_silence(scheduler_commands):
    """The state loop passes ignore_exception=True and must not be handed an exception it drops again."""
    self_ = _scheduler()
    await scheduler_commands.Scheduler.launch_dcs(self_, _Server(), ignore_exception=True)
    with pytest.raises(PortConflictError):
        await scheduler_commands.Scheduler.launch_dcs(self_, _Server())


@pytest.mark.parametrize('status', [Status.LOADING, Status.RUNNING, Status.PAUSED])
@pytest.mark.asyncio
async def test_launch_dcs_launches_the_server_the_loop_marked_loading(scheduler_commands, status):
    """The state loop sets ``status = LOADING`` and THEN schedules this call, so a launch for a server that
    reads as Loading (or Running) must still be ATTEMPTED — the double's ``startup()`` raises
    ``PortConflictError`` and the audit that raises produces IS the proof that the launch happened.

    Whether the server is genuinely up is decided INSIDE ``startup()``, against its own process — never by a
    status pre-check in this caller. A status pre-check here is what silently stopped every scheduled start
    and left the servers sitting in LOADING."""
    self_ = _scheduler()
    server = _Server()
    server.status = status
    await scheduler_commands.Scheduler.launch_dcs(self_, server, ignore_exception=True)
    assert self_.bot.audits, 'the launch must be attempted - startup() decides, not this caller'


# ------------------------------------------------- _startup: show the reason, not a status wait

@pytest.mark.asyncio
async def test_the_command_flow_shows_the_reason_instead_of_waiting_for_loading(scheduler_commands):
    self_ = _scheduler()
    server = _Server()
    embed = _Embed()
    msg = _Msg()

    await scheduler_commands.Scheduler._startup(self_, _Interaction(), embed, server, msg=msg)

    assert server.status_waits == [], "the LOADING wait is what hid the reason; it must be gone"
    assert msg.edits, "the flow must still report back"
    assert REASON.split(".")[0] in msg.edits[-1], msg.edits[-1]
