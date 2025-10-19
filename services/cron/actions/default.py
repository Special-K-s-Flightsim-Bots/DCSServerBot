import asyncio
import atexit
import discord
import os

from core import Server, ServiceRegistry, Node, PersistentReport, Report, Status, Coalition
from datetime import datetime, timezone, timedelta
from services.bot import BotService
from services.servicebus import ServiceBus


async def report(file: str, channel: int, node: Node, persistent: bool | None = True,
                 server: Server | None = None):
    # we can only render on the master node
    if not node.master:
        return
    bot = ServiceRegistry.get(BotService).bot
    if bot.is_closed():
        return
    if persistent:
        r = PersistentReport(bot, 'cron', file, channel_id=channel, server=server,
                             embed_name=os.path.basename(file)[:-5])
        await r.render(node=node, server=server)
    else:
        r = Report(bot, 'cron', file)
        env = await r.render(node=node, server=server)
        await bot.get_channel(channel).send(embed=env.embed)


async def restart(node: Node, server: Server | None = None, shutdown: bool | None = False,
                  rotate: bool | None = False, run_extensions: bool | None = True,
                  reboot: bool | None = False):
    def _reboot():
        os.system("shutdown /r /t 1")

    if server and server.status not in [Status.SHUTDOWN, Status.UNREGISTERED]:
        server.maintenance = True
        if shutdown:
            await ServiceRegistry.get(ServiceBus).send_to_node({"command": "onShutdown", "server_name": server.name})
            await asyncio.sleep(1)
            await server.shutdown()
            await server.startup()
        elif rotate:
            await server.loadNextMission(modify_mission=run_extensions)
        else:
            await server.restart(modify_mission=run_extensions)
        server.maintenance = False
    elif reboot:
        bus = ServiceRegistry.get(ServiceBus)
        for server in [x for x in bus.servers.values() if x.status not in [Status.SHUTDOWN, Status.UNREGISTERED]]:
            if not server.is_remote:
                await bus.send_to_node({"command": "onShutdown", "server_name": server.name})
                await asyncio.sleep(1)
                await server.shutdown()
        atexit.register(_reboot)
        await node.shutdown()


async def halt(node: Node):
    def _halt():
        os.system("shutdown /s /t 1")

    bus = ServiceRegistry.get(ServiceBus)
    for server in [x for x in bus.servers.values() if x.status not in [Status.SHUTDOWN, Status.UNREGISTERED]]:
        if not server.is_remote:
            await bus.send_to_node({"command": "onShutdown", "server_name": server.name})
            await asyncio.sleep(1)
            await server.shutdown()
    atexit.register(_halt)
    await node.shutdown()


async def cmd(node: Node, cmd: str):
    out, err = await node.shell_command(cmd)
    if err:
        node.log.error(err)
    else:
        node.log.info(out)


async def popup(node: Node, server: Server, message: str, to: str | None = 'all', timeout: int | None = 10):
    if server.status == Status.RUNNING:
        await server.sendPopupMessage(Coalition(to), message, timeout)


async def broadcast(node: Node, message: str, to: str | None = 'all', timeout: int | None = 10):
    bus = ServiceRegistry.get(ServiceBus)
    for server in [x for x in bus.servers.values() if x.status == Status.RUNNING]:
        await server.sendPopupMessage(Coalition(to), message, timeout)


async def purge_channel(node: Node, channel: int | list[int], older_than: int = None,
                        ignore: int | list[int] = None, after_id: int = None, before_id: int = None):
    if not node.master:
        return
    bot = ServiceRegistry.get(BotService).bot

    if isinstance(channel, int):
        channels = [channel]
    else:
        channels = channel
    if isinstance(ignore, int):
        ignore = [ignore]
    for c in channels:
        channel = bot.get_channel(c)
        if not channel:
            node.log.warning(f"Channel {c} not found!")
            return

        try:
            def check(message: discord.Message):
                return not ignore or (message.author.id not in ignore and message.id not in ignore)

            if older_than is not None:
                now = datetime.now(tz=timezone.utc)
                before = now - timedelta(days=older_than)
                node.log.debug(f"Deleting messages older than {older_than} days in channel {channel.name} ...")
            elif before_id is not None:
                before = (await channel.fetch_message(before_id)).created_at
                node.log.debug(f"Deleting messages older than {before_id} in channel {channel.name} ...")
            else:
                before = None
            if after_id is not None:
                after = (await channel.fetch_message(after_id)).created_at
                node.log.debug(f"Deleting messages younger than {after_id} in channel {channel.name} ...")
            else:
                after = None
            deleted_messages = await channel.purge(limit=None, after=after, before=before, check=check, bulk=True)
            node.log.debug(f"Purged {len(deleted_messages)} messages from channel {channel.name}.")
        except discord.NotFound:
            node.log.warning(f"Can't delete messages in channel {channel.name}: Not found")
        except discord.Forbidden:
            node.log.warning(f"Can't delete messages in channel {channel.name}: Missing permissions")
        except discord.HTTPException:
            node.log.error(f"Failed to delete message in channel {channel.name}", exc_info=True)


async def dcs_update(node: Node, warn_times: list[int] | None = None):
    branch, version = await node.get_dcs_branch_and_version()
    new_version = await node.get_latest_version(branch)
    if new_version != version:
        if not warn_times:
            warn_times = [120, 60]
        await node.dcs_update(warn_times=warn_times, branch=branch)


async def dcs_repair(node: Node, slow: bool | None = False, check_extra_files: bool | None = False,
                     warn_times: list[int] | None = None):
    await node.dcs_repair(warn_times=warn_times, slow=slow, check_extra_files=check_extra_files)


async def node_shutdown(node: Node, restart: bool | None = False):
    if restart:
        await node.restart()
    else:
        await node.shutdown()
