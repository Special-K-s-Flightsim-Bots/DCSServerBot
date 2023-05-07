from __future__ import annotations
import asyncio
import os
import win32gui
import win32process

from datetime import datetime, timezone
from discord.ext import tasks
from minidump.utils.createminidump import create_dump, MINIDUMP_TYPE
from typing import TYPE_CHECKING, Optional

from core import Status, utils, Server, Channel
from core.services.base import Service
from core.services.registry import ServiceRegistry

if TYPE_CHECKING:
    from services import ServiceBus, DCSServerBot


@ServiceRegistry.register("Monitoring")
class MonitoringService(Service):
    def __init__(self, main):
        super().__init__(main)
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")
        self.bot: Optional[DCSServerBot] = None
        self.hung = dict[str, int]()

    async def start(self):
        await super().start()
        if self.bus.master:
            self.bot = ServiceRegistry.get("Bot").bot
            await self.bot.wait_until_ready()
        self.monitoring.start()
        self.log.info("- Monitoring started.")

    async def stop(self):
        await super().stop()
        self.monitoring.cancel()
        self.log.info("- Monitoring stopped.")

    @staticmethod
    async def check_affinity(server: Server, config: dict):
        if not server.process:
            for exe in ['DCS_server.exe', 'DCS.exe']:
                server.process = utils.find_process(exe, server.installation)
                if server.process:
                    break
        if server.process:
            server.process.cpu_affinity(config['affinity'])

    async def warn_admins(self, server: Server, message: str) -> None:
        if self.main.config.getboolean(server.installation, 'PING_ADMIN_ON_CRASH'):
            message += f"\nLatest dcs-<timestamp>.log can be pulled with /download\n" \
                       f"If the scheduler is configured for this server, it will relaunch it automatically."
            if self.bus.master:
                await self.bot.alert(message, server.get_channel(Channel.ADMIN))
            else:
                await self.bus.sendtoBot({
                    "command": "rpc",
                    "object": "Bot",
                    "method": "alert",
                    "params": {
                        "message": message, "channel": server.get_channel(Channel.ADMIN)
                    }
                })

    @tasks.loop(minutes=1.0)
    async def monitoring(self):
        # check for blocked processes due to window popups
        for title in ["Can't run", "Login Failed", "DCS Login"]:
            handle = win32gui.FindWindowEx(None, None, None, title)
            if handle:
                _, pid = win32process.GetWindowThreadProcessId(handle)
                for server in self.bot.servers.values():
                    if server.process and server.process.pid == pid:
                        if server.is_remote:
                            continue
                        await server.shutdown(force=True)
                        await self.bot.audit(f'Server killed due to a popup with title "{title}".', server=server)

        for server in self.bus.servers.values():
            if server.is_remote or server.maintenance or server.status in [Status.UNREGISTERED, Status.SHUTDOWN]:
                continue
            if server.process and not server.process.is_running():
                server.status = Status.SHUTDOWN
                server.process = None
                message = f"Server \"{server.name}\" died. Setting state to SHUTDOWN."
                self.log.warning(message)
                server.status = Status.SHUTDOWN
                await self.warn_admins(server, message)
            elif server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                try:
# TODO: implement affinity
#                    config = self.get_config(server)
#                    if server.status == Status.RUNNING and 'affinity' in config:
#                        await self.check_affinity(server, config)
                    await server.keep_alive()
                    # remove any hung flag, if the server has responded
                    if server.name in self.hung:
                        del self.hung[server.name]
                except asyncio.TimeoutError:
                    # check if the server process is still existent
                    max_hung_minutes = int(self.bot.config['DCS']['MAX_HUNG_MINUTES'])
                    if max_hung_minutes > 0:
                        self.log.warning(f"Server \"{server.name}\" is not responding.")
                        # process might be in a hung state, so try again for a specified amount of times
                        if server.name in self.hung and self.hung[server.name] >= (max_hung_minutes - 1):
                            message = f"Can't reach server \"{server.name}\" for more than {max_hung_minutes} " \
                                      f"minutes. Killing ..."
                            self.log.warning(message)
                            if server.process:
                                now = datetime.now(timezone.utc)
                                filename = os.path.join(
                                    os.path.expandvars(self.bot.config[server.installation]['DCS_HOME']),
                                    'Logs', f"{now.strftime('dcs-%Y%m%d-%H%M%S')}.dmp"
                                )
                                await asyncio.to_thread(create_dump, server.process.pid, filename,
                                                        MINIDUMP_TYPE.MiniDumpNormal, True)
                                server.process.kill()
                            else:
                                await server.shutdown(True)
                            server.process = None
                            await self.bot.audit("Server killed due to a hung state.", server=server)
                            del self.hung[server.name]
                            server.status = Status.SHUTDOWN
                            await self.warn_admins(server, message)
                        elif server.name not in self.hung:
                            self.hung[server.name] = 1
                        else:
                            self.hung[server.name] += 1
                except Exception as ex:
                    self.log.exception(ex)
