"""Closing the bot while its delayed-ready task is still running.

``ConnectionState._delay_ready`` ends by dispatching ``ready``, and ``Client.close()`` neither
cancels that task nor keeps ``Client.loop`` usable — it sets the loop to ``discord.MISSING``. A
close during the ready window (the guild-chunking phase a master switch lands in) therefore ends
with a task dispatching on a client whose loop is gone:

    AttributeError: '_MissingSentinel' object has no attribute 'create_task'

``DCSServerBot.close()`` cancels those tasks first. The tests pin both halves: the cancel keeps the
close quiet, and the failure it prevents is the reported one.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

import discord
import pytest

from services.bot.dcsserverbot import DCSServerBot


class _Client(discord.Client):
    async def on_ready(self) -> None:
        """``Client.dispatch`` only schedules an event the client handles."""


class _StandIn:
    """The part of ``DCSServerBot`` the cancel helper touches."""

    _cancel_delayed_ready = DCSServerBot._cancel_delayed_ready

    def __init__(self, client: discord.Client):
        self._connection = client._connection


async def _client_with_pending_ready(ready_timeout: float = 0.2) -> discord.Client:
    """A client whose delayed-ready task is running, as it is during the guild-chunking phase."""
    client = _Client(intents=discord.Intents.none())
    await client._async_setup_hook()          # sets client.loop, exactly as login() does
    state = client._connection
    state.guild_ready_timeout = ready_timeout
    state._ready_state = asyncio.Queue()      # installed by parse_ready()
    state._ready_task = asyncio.create_task(state._delay_ready())
    await asyncio.sleep(0)
    return client


def _record_async_errors(loop: asyncio.AbstractEventLoop) -> tuple[list[dict[str, Any]], Callable[[], None]]:
    """Collect what reaches the loop's exception handler — where 'never retrieved' surfaces."""
    errors: list[dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    return errors, lambda: loop.set_exception_handler(previous)


def test_close_cancels_a_pending_delayed_ready_task():
    async def scenario() -> None:
        client = await _client_with_pending_ready()
        task = client._connection._ready_task
        assert task is not None and not task.done()

        _StandIn(client)._cancel_delayed_ready()

        # _delay_ready swallows the cancel and ends without dispatching 'ready'
        assert await task is None
        assert task.done() and task.exception() is None

    asyncio.run(scenario())


def test_the_helper_also_cancels_per_shard_ready_tasks():
    async def scenario() -> None:
        client = await _client_with_pending_ready()
        state = client._connection
        shard_task = asyncio.create_task(asyncio.sleep(30))
        setattr(state, '_ready_tasks', {0: shard_task})

        _StandIn(client)._cancel_delayed_ready()

        ready_task = state._ready_task
        assert ready_task is not None
        await ready_task
        with pytest.raises(asyncio.CancelledError):
            await shard_task
        assert shard_task.cancelled()

    asyncio.run(scenario())


def test_close_calls_the_cancel_before_closing():
    """The helper is worthless unless ``close()`` calls it."""
    assert '_cancel_delayed_ready' in DCSServerBot.close.__code__.co_names


def test_a_close_in_the_ready_window_raises_nothing():
    async def scenario() -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        errors, restore = _record_async_errors(loop)
        try:
            client = await _client_with_pending_ready()
            _StandIn(client)._cancel_delayed_ready()
            await client.close()
            await asyncio.sleep(0.4)          # past the window the ready task was waiting on
        finally:
            restore()
        return errors

    assert asyncio.run(scenario()) == []


def test_without_the_cancel_the_dispatch_after_close_fails():
    """The failure the cancel prevents — skipped if discord.py starts cancelling the task itself."""
    async def scenario() -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        errors, restore = _record_async_errors(loop)
        try:
            client = await _client_with_pending_ready()
            await client.close()
            await asyncio.sleep(0.4)
        finally:
            restore()
        return errors

    errors = asyncio.run(scenario())
    if not errors:
        pytest.skip("discord.py no longer leaves the delayed-ready task running after close()")
    messages = [str(context.get('exception')) for context in errors]
    assert any('_MissingSentinel' in message and 'create_task' in message
               for message in messages), messages
