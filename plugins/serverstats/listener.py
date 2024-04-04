import asyncio
import math

from core import EventListener, Plugin, event, Server, utils, ServiceRegistry
from services import BotService


class ServerStatsListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.fps = {}
        self.minutes = {}

    @event(name="perfmon")
    async def perfmon(self, server: Server, data: dict):
        fps = float(data['fps'])
        self.fps[server.name] = fps
        config = self.get_config(server)
        min_fps = config.get("min_fps", 0)
        if min_fps:
            if fps < min_fps:
                self.minutes[server.name] = self.minutes.get(server.name, 0) + 1
                period = config.get("period", 5)
                if self.minutes[server.name] == period:
                    message = utils.format_string(
                        config.get("message", "The FPS of server {server.name} are below {min_fps} for longer than "
                                              "{period} minutes!"),
                        server=server, fps=round(fps, 2), min_fps=min_fps, period=period)
                    if config.get("mentioning", True):
                        # noinspection PyAsyncCall
                        asyncio.create_task(ServiceRegistry.get(BotService).alert(title="Server Performance Low!",
                                                                                  message=message, server=server))
                    else:
                        # noinspection PyAsyncCall
                        asyncio.create_task(self.bot.get_admin_channel(server).send(message))
                    self.minutes[server.name] = 0
            else:
                self.minutes[server.name] = 0

    @event(name="serverLoad")
    async def serverLoad(self, server: Server, data: dict):
        if server.name not in self.fps:
            return
        ping = (self.bot.latency * 1000) if not math.isinf(self.bot.latency) else -1
        cpu = data['cpu']
        if math.isinf(cpu):
            cpu = -1
        fps = self.fps[server.name]
        if math.isinf(fps):
            fps = -1

        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO serverstats (server_name, node, mission_id, users, status, cpu, mem_total, 
                                             mem_ram, read_bytes, write_bytes, bytes_sent, bytes_recv, fps, ping) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (server.name, server.node.name, server.mission_id, len(server.get_active_players()),
                      server.status.name, cpu, data['mem_total'], data['mem_ram'], data['read_bytes'],
                      data['write_bytes'], data['bytes_sent'], data['bytes_recv'], fps, ping))
