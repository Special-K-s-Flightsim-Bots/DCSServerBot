from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import psutil
import shutil
import sys

if sys.platform == 'win32':
    import win32api
    import win32con
    import win32gui
    import win32process
    from minidump.utils.createminidump import create_dump, MINIDUMP_TYPE

from datetime import datetime, timezone
from discord.ext import tasks

from core import Status, Server, ServerImpl, Autoexec, utils
from core.services.base import Service
from core.services.registry import ServiceRegistry

from ..servicebus import ServiceBus
from ..bot import BotService

__all__ = [
    "MonitoringService"
]

last_wait_time = 0


@ServiceRegistry.register()
class MonitoringService(Service):
    def __init__(self, node):
        super().__init__(node, name="Monitoring")
        self.bus = ServiceRegistry.get(ServiceBus)
        self.io_counters = {}
        self.net_io_counters = None
        self.space_warning_sent: dict[str, bool] = {}
        self.space_alert_sent: dict[str, bool] = {}

    async def start(self):
        await super().start()
        install_drive = os.path.splitdrive(os.path.expandvars(self.node.locals['DCS']['installation']))[0]
        self.space_warning_sent[install_drive] = False
        self.space_alert_sent[install_drive] = False
        if install_drive != 'C:':
            self.space_warning_sent['C:'] = False
            self.space_alert_sent['C:'] = False
        self.check_autoexec()
        self.monitoring.start()
        if self.get_config().get('time_sync', False):
            time_server = self.get_config().get('time_server', None)
            if time_server:
                if sys.platform == 'win32':
                    try:
                        retval = ctypes.windll.shell32.ShellExecuteW(
                            None,
                            "runas", 'w32tm', f'/config /manualpeerlist:{time_server} /syncfromflags:MANUAL',
                            None, 1
                        )
                        if retval > 31:
                            self.log.info(f"- Time server configured as {time_server}.")
                        else:
                            self.log.info(f"- Could not configure time server, errorcode: {retval}")
                    except OSError as ex:
                        if ex.winerror == 740:
                            self.log.error("You need to disable User Access Control (UAC), "
                                           "to use the automated time sync.")
                        raise
                else:
                    # not implemented for UNIX
                    pass

            self.time_sync.start()

    async def stop(self):
        if self.get_config().get('time_sync', False):
            self.time_sync.cancel()
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

    async def send_alert(self, title: str, message: str, **kwargs):
        params = {
            "title": title,
            "message": message
        }
        if 'server' in kwargs:
            params['server'] = kwargs['server'].name
        else:
            params['node'] = self.node.name
        await self.bus.send_to_node({
            "command": "rpc",
            "service": BotService.__name__,
            "method": "alert",
            "params": params
        })

    async def warn_admins(self, server: Server, title: str, message: str) -> None:
        message += f"\nLatest dcs-<timestamp>.log can be pulled with /download\n" \
                   f"If the scheduler is configured for this server, it will relaunch it automatically."
        await self.send_alert(title, message, server=server)

    async def check_popups(self):
        # check for blocked processes due to window popups
        for title in [
            "Can't run",
            "Login Failed",
            "DCS Login",
            "Authorization failed",
            "Login session has expired",
            "Mission script error"
        ]:
            handle = win32gui.FindWindowEx(None, None, None, title)
            if handle:
                if title == "Mission script error":
                    def callback(hwnd, extra):
                        if win32gui.GetWindowText(hwnd) == "OK":  # Find the child with "OK" text
                            extra.append(hwnd)

                    child_windows = []
                    win32gui.EnumChildWindows(handle, callback, child_windows)

                    if child_windows:
                        # Press the OK button
                        ok_button_handle = child_windows[0]
                        win32api.SendMessage(ok_button_handle, win32con.BM_CLICK, 0, 0)
                        return

                _, pid = win32process.GetWindowThreadProcessId(handle)
                for server in [x for x in self.bus.servers.values() if not x.is_remote]:
                    if server.process and server.process.pid == pid:
                        await server.shutdown(force=True)
                        await self.node.audit(f'Server killed due to a popup with title "{title}".',
                                              server=server)

    async def kill_hung_server(self, server: Server):
        message = (f"Can't reach server \"{server.name}\" for more than "
                   f"{int(server.instance.locals.get('max_hung_minutes', 3))} minutes. Killing ...")
        self.log.warning(message)
        if server.process and server.process.is_running():
            now = datetime.now(timezone.utc)
            if sys.platform == 'win32':
                try:
                    filename = os.path.join(server.instance.home, 'Logs',
                                            f"{now.strftime('dcs-%Y%m%d-%H%M%S')}.dmp")
                    await asyncio.to_thread(create_dump, server.process.pid, filename,
                                            MINIDUMP_TYPE.MiniDumpNormal, True)

                    root = logging.getLogger()
                    if root.handlers:
                        root.removeHandler(root.handlers[0])
                except OSError:
                    self.log.debug("No minidump created due to an error (Linux?).")
            shutil.copy2(os.path.join(server.instance.home, 'Logs', 'dcs.log'),
                         os.path.join(server.instance.home, 'Logs', f"dcs-{now.strftime('%Y%m%d-%H%M%S')}.log"))
            server.process.kill()
        else:
            await server.shutdown(True)
        server.process = None
        await self.node.audit("Server killed due to a hung state.", server=server)
        server.status = Status.SHUTDOWN
        if server.locals.get('ping_admin_on_crash', True):
            await self.warn_admins(server, title=f'Server \"{server.name}\" unreachable', message=message)

    async def heartbeat(self):
        for server in self.bus.servers.values():  # type: ServerImpl
            # don't test remote servers or servers that are not initialized or shutdown
            if server.is_remote or server.status in [Status.UNREGISTERED, Status.SHUTTING_DOWN, Status.SHUTDOWN]:
                continue
            # check if the process is dead (on load it might take some seconds for the process to appear)
            if server.process and not await server.is_running():
                # we do not need to warn, if the server was just launched manually
                if server.maintenance and server.status == Status.LOADING:
                    return
                # only escalate, if the server was not stopped (maybe the process was manually shut down)
                if server.status != Status.STOPPED:
                    now = datetime.now(timezone.utc)
                    shutil.copy2(os.path.join(server.instance.home, 'Logs', 'dcs.log'),
                                 os.path.join(server.instance.home, 'Logs', f"dcs-{now.strftime('%Y%m%d-%H%M%S')}.log"))
                    title = f'Server "{server.name}" died!'
                    message = 'Setting state to SHUTDOWN.'
                    self.log.warning(title + ' ' + message)
                    if server.locals.get('ping_admin_on_crash', True):
                        await self.warn_admins(server, title=title, message=message)
                    await self.node.audit(f'Server died.', server=server)
                server.status = Status.SHUTDOWN
                return
            # No, check if the process is still doing something
            try:
                await server.keep_alive()
                # check if server is alive
                if server.status == Status.LOADING:
                    max_hung = int(server.instance.locals.get('max_hung_minutes', 3)) * 2
                else:
                    max_hung = int(server.instance.locals.get('max_hung_minutes', 3))
                if (datetime.now(timezone.utc) - server.last_seen).total_seconds() / 60 > max_hung:
                    await self.kill_hung_server(server)
                    continue
                if server.status in [Status.RUNNING, Status.PAUSED]:
                    # check extension states
                    for ext in [x for x in server.extensions.values() if not await asyncio.to_thread(x.is_running)]:
                        try:
                            self.log.warning(f"{ext.name} died - restarting ...")
                            await ext.startup()
                        except Exception as ex:
                            self.log.exception(ex)
            except Exception as ex:
                self.log.exception(ex)

    async def nodestats(self):
        global last_wait_time

        bus = ServiceRegistry.get(ServiceBus)
        pstats: dict = self.apool.get_stats()
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO nodestats (
                        node, pool_available, requests_queued, requests_wait_ms, dcs_queue, asyncio_queue
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (self.node.name, pstats.get('pool_available', 0), pstats.get('requests_queued', 0),
                      pstats.get('requests_wait_ms', 0), sum(x.qsize() for x in bus.udp_server.message_queue.values()),
                      len(asyncio.all_tasks(self.bus.loop))))
        self.apool.pop_stats()

    def _pull_load_params(self, server: Server) -> dict:
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
        return {
            "command": "serverLoad",
            "cpu": cpu,
            "mem_total": memory.vms,
            "mem_ram": memory.rss,
            "read_bytes": read_bytes,
            "write_bytes": write_bytes,
            "bytes_recv": bytes_recv,
            "bytes_sent": bytes_sent,
            "server_name": server.name
        }

    async def serverload(self):
        for server in self.bus.servers.values():
            if server.is_remote or server.status not in [Status.RUNNING, Status.PAUSED]:
                continue
            if not server.process or not server.process.is_running():
                self.log.warning(f"DCSServerBot is not attached to a DCS.exe or DCS_Server.exe process on "
                                 f"server {server.name}, skipping server load gathering.")
                continue
            try:
                await self.bus.send_to_node(await asyncio.to_thread(self._pull_load_params, server))
            except (psutil.AccessDenied, PermissionError):
                self.log.debug(f"Server {server.name} was not started by the bot, skipping server load gathering.")
            except psutil.NoSuchProcess:
                self.log.debug(f"Server {server.name} died, skipping server load gathering.")

    @staticmethod
    def convert_bytes(size_bytes: int) -> str:
        scales = ('B', 'KB', 'MB', 'GB', 'TB')
        if size_bytes == 0:
            return "0B"
        idx = 0
        while size_bytes >= 1024 and idx < len(scales) - 1:
            size_bytes /= 1024.0
            idx += 1
        return f"{size_bytes:.2f}{scales[idx]}"

    async def drive_check(self):
        for drive in self.space_warning_sent.keys():
            total, free = utils.get_drive_space(drive)
            warn_pct = (self.get_config().get('drive_warn_threshold', 10)) / 100
            alert_pct = (self.get_config().get('drive_alert_threshold', 5)) / 100
            if (free < total * warn_pct) and not self.space_warning_sent[drive]:
                message = (f"Your freespace on {drive} is below {warn_pct * 100}%!\n{self.convert_bytes(free)} of "
                           f"{self.convert_bytes(total)} bytes free.")
                self.log.warning(message)
                await self.node.audit(message)
                self.space_warning_sent[drive] = True
            if (free < total * alert_pct) and not self.space_alert_sent[drive]:
                message = (f"Your freespace on {drive} is below {alert_pct * 100}%!\n{self.convert_bytes(free)} of "
                           f"{self.convert_bytes(total)} bytes free.")
                self.log.error(message)
                await self.send_alert(title=f"Your DCS drive on node {self.node.name} is running out of space!",
                                      message=message)
                self.space_alert_sent[drive] = True

    @tasks.loop(minutes=1.0)
    async def monitoring(self):
        try:
            if sys.platform == 'win32':
                await self.check_popups()
            await self.heartbeat()
            await self.drive_check()
            if 'serverstats' in self.node.config.get('opt_plugins', []):
                await self.serverload()
            if self.node.locals.get('nodestats', True):
                await self.nodestats()
        except Exception as ex:
            self.log.exception(ex)

    @monitoring.before_loop
    async def before_loop(self):
        if self.node.master:
            bot = ServiceRegistry.get(BotService).bot
            await bot.wait_until_ready()

    @tasks.loop(hours=12)
    async def time_sync(self):
        if sys.platform == 'win32':
            try:
                retval = ctypes.windll.shell32.ShellExecuteW(None, "runas", 'w32tm', '/resync', None, 1)
                if retval > 31:
                    self.log.info("- Windows time synced.")
                else:
                    self.log.info(f"- Windows time NOT synced, errorcode: {retval}")
            except OSError as ex:
                if ex.winerror == 740:
                    self.log.error("You need to disable User Access Control (UAC), "
                                   "to use the automated time sync.")
                raise
        else:
            # not implemented for UNIX
            pass
