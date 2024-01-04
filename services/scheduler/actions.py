import asyncio
import os

from core import Server, ServiceRegistry, Node, PersistentReport, Report
from typing import Optional


async def report(file: str, channel: int, node: Node, persistent: Optional[bool] = True,
                 server: Optional[Server] = None):
    bot = ServiceRegistry.get("Bot").bot
    if persistent:
        r = PersistentReport(bot, 'scheduler', file, channel_id=channel, server=server,
                             embed_name=os.path.basename(file)[:-5])
        await r.render(node=node, server=server)
    else:
        r = Report(bot, 'scheduler', file)
        env = await r.render(node=node, server=server)
        await bot.get_channel(channel).send(embed=env.embed)


async def restart(node: Node, server: Server, shutdown: Optional[bool] = False, rotate: Optional[bool] = False,
                  run_extensions: Optional[bool] = True):
    if not server:
        return
    server.maintenance = True
    if shutdown:
        ServiceRegistry.get("Bus").send_to_node({"command": "onShutdown", "server_name": server.name})
        await asyncio.sleep(1)
        await server.shutdown()
        await server.startup()
    elif rotate:
        await server.loadNextMission(modify_mission=run_extensions)
    else:
        await server.restart(modify_mission=run_extensions)
