import asyncio
import math

from core import EventListener, Plugin, event, Server, utils, ServiceRegistry
from services.bot import BotService
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Monitoring


class MonitoringListener(EventListener["Monitoring"]):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.fps = {}
        self.minutes_fps = {}
        self.minutes_ram = {}
        self.warning_sent = {}

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        self.warning_sent[server.name] = False

    @event(name="perfmon")
    async def perfmon(self, server: Server, data: dict):
        fps = float(data['fps'])
        self.fps[server.name] = fps

        # check FPS
        config = self.get_config(server).get('thresholds', {}).get('FPS', {})
        if config:
            min_fps = config.get("min", 30)
            if fps < min_fps:
                self.minutes_fps[server.name] = self.minutes_fps.get(server.name, 0) + 1
                period = config.get("period", 5)
                if self.minutes_fps[server.name] == period:
                    message = utils.format_string(
                        config.get("message",
                                   "Server {server} FPS ({fps}) has been below {min_fps} for more than "
                                   "{period} minutes."),
                        server=server.name, fps=round(fps, 2), min_fps=min_fps, period=period)
                    if config.get("mentioning", True):
                        asyncio.create_task(ServiceRegistry.get(BotService).alert(title="Server Performance Low!",
                                                                                  message=message, server=server))
                    else:
                        admin_channel = self.bot.get_admin_channel(server)
                        if admin_channel:
                            asyncio.create_task(admin_channel.send(message))
                    self.minutes_fps[server.name] = 0
            else:
                self.minutes_fps[server.name] = 0

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

        mission_time = (server.current_mission.start_time + server.current_mission.mission_time) if server.current_mission else None
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO serverstats (server_name, node, mission_id, users, status, mission_time, cpu, mem_total, 
                                             mem_ram, read_bytes, write_bytes, bytes_sent, bytes_recv, fps, ping) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (server.name, server.node.name, server.mission_id, len(server.get_active_players()),
                      server.status.name, mission_time, cpu, data['mem_total'], data['mem_ram'], data['read_bytes'],
                      data['write_bytes'], data['bytes_sent'], data['bytes_recv'], fps, ping))

        # check RAM
        config = self.get_config(server).get('thresholds', {}).get('RAM', {})
        if config:
            max_ram = config.get('max', 32)
            ram = data['mem_total'] / (1024 ** 3)
            if ram > max_ram:
                self.minutes_ram[server.name] = self.minutes_ram.get(server.name, 0) + 1
                period = config.get("period", 5)
                if self.minutes_ram[server.name] == period and not self.warning_sent[server.name]:
                    message = utils.format_string(
                        config.get("message",
                                   "Server {server} RAM usage is {ram} GB, exceeding the maximum of {max_ram} GB "
                                   "for more than {period} minutes."),
                        server=server.name, ram=round(ram, 2), max_ram=max_ram, period=period)
                    if config.get("mentioning", True):
                        asyncio.create_task(ServiceRegistry.get(BotService).alert(title="Excessive RAM consumption!",
                                                                                  message=message, server=server))
                    else:
                        admin_channel = self.bot.get_admin_channel(server)
                        if admin_channel:
                            asyncio.create_task(admin_channel.send(message))
                    self.minutes_ram[server.name] = 0
                    self.warning_sent[server.name] = True
            else:
                self.minutes_ram[server.name] = 0
                self.warning_sent[server.name] = False
