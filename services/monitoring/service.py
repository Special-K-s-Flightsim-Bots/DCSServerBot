from __future__ import annotations
import asyncio
import os
import psutil
import sys
if sys.platform == 'win32':
    import win32gui
    import win32process
    from minidump.utils.createminidump import create_dump, MINIDUMP_TYPE

from datetime import datetime, timezone
from discord.ext import tasks
from typing import TYPE_CHECKING

from core import Status, utils, Server, ServerImpl, Autoexec
from core.services.base import Service
from core.services.registry import ServiceRegistry

if TYPE_CHECKING:
    from services import ServiceBus

__all__ = [
    "MonitoringService"
]


@ServiceRegistry.register("Monitoring")
class MonitoringService(Service):
    def __init__(self, node, name: str):
        super().__init__(node, name)
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")
        self.hung = dict[str, int]()
        self.io_counters = {}
        self.net_io_counters = None

    async def start(self):
        await super().start()
        self.check_autoexec()
        self.monitoring.start()

    async def stop(self):
        self.monitoring.cancel()
        await super().stop()

    def check_autoexec(self):
        for instance in self.node.instances:
            try:
                cfg = Autoexec(instance)
                if cfg.crash_report_mode is None:
                    self.log.info('  => Adding crash_report_mode = "silent" to autoexec.cfg')
                    cfg.crash_report_mode = 'silent'
                elif cfg.crash_report_mode != 'silent':
                    self.log.warning('=> crash_report_mode is NOT "silent" in your autoexec.cfg! DCSServerBot '
                                     'will not work properly on DCS crashes, please change it manually to "silent" '
                                     'to avoid that.')
            except Exception as ex:
                self.log.error(f"  => Error while parsing autoexec.cfg: {ex.__repr__()}")

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
            self.bus.send_to_node({
                "command": "rpc",
                "service": "Bot",
                "method": "alert",
                "params": {
                    "server": server.name,
                    "message": message
                }
            })

    async def check_popups(self):
        # check for blocked processes due to window popups
        for title in [
            "Can't run",
            "Login Failed",
            "DCS Login",
            "Authorization failed",
            "Login session has expired"
        ]:
            handle = win32gui.FindWindowEx(None, None, None, title)
            if handle:
                _, pid = win32process.GetWindowThreadProcessId(handle)
                for server in [x for x in self.bus.servers.values() if not x.is_remote]:
                    if server.process and server.process.pid == pid:
                        await server.shutdown(force=True)
                        await self.node.audit(f'Server killed due to a popup with title "{title}".',
                                              server=server)

    async def heartbeat(self):
        for server in self.bus.servers.values():  # type: ServerImpl
            if server.is_remote or server.status in [Status.UNREGISTERED, Status.SHUTDOWN]:
                continue
            if not server.maintenance and server.process is not None and not server.process.is_running():
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
                    for ext in [x for x in server.extensions.values() if not x.is_running()]:
                        await ext.startup()
                except (TimeoutError, asyncio.TimeoutError):
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
                                if sys.platform == 'win32':
                                    await asyncio.to_thread(create_dump, server.process.pid, filename,
                                                            MINIDUMP_TYPE.MiniDumpNormal, True)
                                server.process.kill()
                            else:
                                await server.shutdown(True)
                            server.process = None
                            await self.node.audit("Server killed due to a hung state.", server=server)
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
            try:
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
                    "mem_total": memory.vms,
                    "mem_ram": memory.rss,
                    "read_bytes": read_bytes,
                    "write_bytes": write_bytes,
                    "bytes_recv": bytes_recv,
                    "bytes_sent": bytes_sent,
                    "server_name": server.name
                })
            except PermissionError:
                self.log.warning(f"Server {server.name} was not started by the bot, "
                                 f"skipping server load gathering.")

    @tasks.loop(minutes=1.0, reconnect=True)
    async def monitoring(self):
        try:
            if self.node.master:
                await self.check_nodes()
            if sys.platform == 'win32':
                await self.check_popups()
            await self.heartbeat()
            if 'serverstats' in self.node.config.get('opt_plugins', []):
                await self.serverload()
        except Exception as ex:
            self.log.exception(ex)
