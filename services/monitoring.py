from __future__ import annotations
import asyncio
import os
import psutil
import subprocess
import traceback
import win32gui
import win32process

from datetime import datetime, timezone
from discord.ext import tasks
from minidump.utils.createminidump import create_dump, MINIDUMP_TYPE
from typing import TYPE_CHECKING, Optional

from core import Status, utils, Server, Channel, Coalition, Plugin, ServerImpl
from core.services.base import Service
from core.services.registry import ServiceRegistry

if TYPE_CHECKING:
    from services import ServiceBus, DCSServerBot


@ServiceRegistry.register("Monitoring")
class MonitoringService(Service):
    def __init__(self, node, name: str):
        super().__init__(node, name)
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")
        self.bot: Optional[DCSServerBot] = None
        self.hung = dict[str, int]()
        self.update_pending = False
        self.io_counters = {}
        self.net_io_counters = None

    async def start(self):
        await super().start()
        if self.bus.master:
            self.bot = ServiceRegistry.get("Bot").bot
            await self.bot.wait_until_ready()
        self.monitoring.start()
        if self.node.locals['DCS'].get('autoupdate', False):
            self.autoupdate.start()

    async def stop(self):
        await super().stop()
        if self.node.locals['DCS'].get('autoupdate', False):
            self.autoupdate.cancel()
        self.monitoring.cancel()

    async def check_nodes(self):
        active_nodes: list[str] = self.node.get_active_nodes()
        used_nodes: set[str] = set()
        for server in [x for x in self.bus.servers.values() if x.is_remote]:
            if server.node.name not in active_nodes:
                self.log.warning(f"- Node {server.node.name} not responding, removing server {server.name}.")
                del self.bus.servers[server.name]
            else:
                used_nodes.add(server.node.name)
        # any new nodes detected?
        for node in set(active_nodes) - used_nodes:
            await self.bus.register_remote_node(node)

    @staticmethod
    async def check_affinity(server: Server, affinity: list[int]):
        if not server.process:
            for exe in ['DCS_server.exe', 'DCS.exe']:
                server.process = utils.find_process(exe, server.instance.name)
                if server.process:
                    break
        if server.process:
            server.process.cpu_affinity(affinity)

    async def warn_admins(self, server: Server, message: str) -> None:
        if server.locals.get('ping_admin_on_crash', True):
            message += f"\nLatest dcs-<timestamp>.log can be pulled with /download\n" \
                       f"If the scheduler is configured for this server, it will relaunch it automatically."
            if self.bus.master:
                await ServiceRegistry.get("Bot").alert(message, server.channels[Channel.ADMIN])
            else:
                self.bus.send_to_node({
                    "command": "rpc",
                    "service": "Bot",
                    "method": "alert",
                    "params": {
                        "message": message, "channel": server.channels[Channel.ADMIN]
                    }
                })

    async def check_popups(self):
        # check for blocked processes due to window popups
        for title in ["Can't run", "Login Failed", "DCS Login"]:
            handle = win32gui.FindWindowEx(None, None, None, title)
            if handle:
                _, pid = win32process.GetWindowThreadProcessId(handle)
                for server in self.bus.servers.values():
                    if server.process and server.process.pid == pid:
                        if server.is_remote:
                            continue
                        await server.shutdown(force=True)
                        await self.bot.audit(f'Server killed due to a popup with title "{title}".', server=server)

    async def heartbeat(self):
        for server in self.bus.servers.values():  # type: ServerImpl
            if server.is_remote or server.maintenance or server.status in [Status.UNREGISTERED, Status.SHUTDOWN]:
                continue
            if server.process is not None and not server.process.is_running():
                server.process = None
                message = f"Server \"{server.name}\" died. Setting state to SHUTDOWN."
                self.log.warning(message)
                server.status = Status.SHUTDOWN
                await self.warn_admins(server, message)
            elif server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                try:
                    if server.status == Status.RUNNING and 'affinity' in server.instance.locals:
                        await self.check_affinity(server, server.instance.locals['affinity'])
                    # check if server is alive
                    await server.keep_alive()
                    # remove any hung flag, if the server has responded
                    if server.name in self.hung:
                        del self.hung[server.name]
                    # check extension states
                    for ext in server.extensions.values():
                        if not ext.is_running():
                            await ext.startup()
                except asyncio.TimeoutError:
                    # check if the server process is still existent
                    max_hung_minutes = int(server.instance.locals.get('max_hung_minutes', 3))
                    if max_hung_minutes > 0:
                        self.log.warning(f'Server "{server.name}" is not responding.')
                        # process might be in a hung state, so try again for a specified amount of times
                        if server.name in self.hung and self.hung[server.name] >= (max_hung_minutes - 1):
                            message = f"Can't reach server \"{server.name}\" for more than {max_hung_minutes} " \
                                      f"minutes. Killing ..."
                            self.log.warning(message)
                            if server.process:
                                now = datetime.now(timezone.utc)
                                filename = os.path.join(server.instance.home, 'Logs',
                                                        f"{now.strftime('dcs-%Y%m%d-%H%M%S')}.dmp")
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

    async def serverload(self):
        for server in self.bus.servers.values():
            if server.is_remote or server.status not in [Status.RUNNING, Status.PAUSED]:
                continue
            if not server.process or not server.process.is_running():
                for exe in ['DCS_server.exe', 'DCS.exe']:
                    server.process = utils.find_process(exe, server.instance.name)
                    if server.process:
                        break
                else:
                    self.log.warning(f"Could not find a running DCS instance for server {server.name}, "
                                     f"skipping server load gathering.")
                    continue
            cpu = server.process.cpu_percent()
            memory = server.process.memory_full_info()
            io_counters = server.process.io_counters()
            if server.process.pid not in self.io_counters:
                write_bytes = read_bytes = 0
            else:
                write_bytes = io_counters.write_bytes - self.io_counters[server.process.pid].write_bytes
                read_bytes = io_counters.read_bytes - self.io_counters[server.process.pid].read_bytes
            self.io_counters[server.process.pid] = io_counters
            net_io_counters = psutil.net_io_counters(pernic=False)
            if not self.net_io_counters:
                bytes_sent = bytes_recv = 0
            else:
                bytes_sent = int((net_io_counters.bytes_sent - self.net_io_counters.bytes_sent) / 7200)
                bytes_recv = int((net_io_counters.bytes_recv - self.net_io_counters.bytes_recv) / 7200)
            self.net_io_counters = net_io_counters
            self.bus.send_to_node({
                "command": "serverLoad",
                "cpu": cpu,
                "mem_total": memory.private,
                "mem_ram": memory.rss,
                "read_bytes": read_bytes,
                "write_bytes": write_bytes,
                "bytes_recv": bytes_recv,
                "bytes_sent": bytes_sent,
                "server_name": server.name
            })

    @tasks.loop(minutes=1.0)
    async def monitoring(self):
        try:
            if self.node.master:
                await self.check_nodes()
            await self.check_popups()
            await self.heartbeat()
            if 'serverstats' in self.node.config.get('opt_plugins', []):
                await self.serverload()
        except Exception:
            traceback.print_exc()

    async def do_update(self, warn_times: list[int]):
        async def shutdown_with_warning(server: Server):
            if server.is_populated():
                shutdown_in = max(warn_times) if len(warn_times) else 0
                while shutdown_in > 0:
                    for warn_time in warn_times:
                        if warn_time == shutdown_in:
                            server.sendPopupMessage(Coalition.ALL, f'Server is going down for a DCS update in '
                                                                   f'{utils.format_time(warn_time)}!')
                    await asyncio.sleep(1)
                    shutdown_in -= 1
            await server.shutdown()

        self.update_pending = True
        self.log.info('Shutting down DCS servers, warning users before ...')
        servers = []
        tasks = []
        for server_name, server in self.bus.servers.items():
            if server.status in [Status.UNREGISTERED, Status.SHUTDOWN]:
                continue
            if server.maintenance:
                servers.append(server)
            else:
                server.maintenance = True
                tasks.append(asyncio.create_task(shutdown_with_warning(server)))
        # wait for DCS servers to shut down
        if tasks:
            await asyncio.gather(*tasks)
        self.log.info(f"Updating {self.node.locals['DCS']['installation']} ...")
        if self.bot:
            for plugin in self.bot.cogs.values():  # type: Plugin
                await plugin.before_dcs_update()
        # disable any popup on the remote machine
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= (subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW)
        startupinfo.wShowWindow = subprocess.SW_HIDE
        subprocess.run(['dcs_updater.exe', '--quiet', 'update'], executable=os.path.expandvars(
            self.node.locals['DCS']['installation']) + '\\bin\\dcs_updater.exe', startupinfo=startupinfo)
        if self.node.locals['DCS'].get('desanitize', True):
            utils.desanitize(self)
        # run after_dcs_update() in all plugins
        for plugin in self.bot.cogs.values():  # type: Plugin
            await plugin.after_dcs_update()
        self.log.info(f"{self.node.locals['DCS']['installation']} updated to the latest version. "
                      f"Starting up DCS servers again ...")
        for server in self.bus.servers.values():
            if server not in servers:
                # let the scheduler do its job
                server.maintenance = False
            else:
                try:
                    # the server was running before (being in maintenance mode), so start it again
                    await server.startup()
                except asyncio.TimeoutError:
                    self.log.warning(f'Timeout while starting {server.display_name}, please check it manually!')
        self.update_pending = False
        await self.log.info('DCS servers started (or Scheduler taking over in a bit).')

    @tasks.loop(minutes=5.0)
    async def autoupdate(self):
        # don't run, if an update is currently running
        if self.update_pending:
            return
        try:
            branch, old_version = utils.getInstalledVersion(self.node.locals['DCS']['installation'])
            new_version = await utils.getLatestVersion(branch, userid=self.node.locals['DCS'].get('dcs_user'),
                                                       password=self.node.locals['DCS'].get('dcs_password'))
            if new_version and old_version != new_version:
                self.log.info('A new version of DCS World is available. Auto-updating ...')
                await self.do_update([300, 120, 60])
        except Exception as ex:
            self.log.debug("Exception in autoupdate(): " + str(ex))
