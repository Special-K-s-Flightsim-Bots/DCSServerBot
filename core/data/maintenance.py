import asyncio

from core import utils
from core.data.node import Node
from core.data.server import Server
from core.data.const import Status, Coalition
from core.utils.helper import format_time


class ServerMaintenanceManager:
    def __init__(self, node: Node, *, warn_times: list[int] = None, message: str = None, shutdown: bool = True):
        self.node: Node = node
        self.warn_times: list[int] = warn_times or [120, 60, 10]
        self.message: str = message or "Server is going down for maintenance in {}"
        self.shutdown: bool = shutdown
        self.to_start: list[Server] = []
        self.in_maintenance: list[Server] = []

    async def shutdown_with_warning(self, server: Server):
        if server.is_populated():
            shutdown_in = max(self.warn_times) if len(self.warn_times) else 0
            while shutdown_in > 0:
                for warn_time in self.warn_times:
                    if warn_time == shutdown_in:
                        await server.sendPopupMessage(Coalition.ALL, self.message.format(format_time(warn_time)))
                await asyncio.sleep(1)
                shutdown_in -= 1
        await server.shutdown()

    async def __aenter__(self):
        tasks = []
        for instance in self.node.instances:
            server = instance.server
            if not server or server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                continue
            if server.maintenance:
                self.in_maintenance.append(server)
            else:
                server.maintenance = True
            self.to_start.append(server)
            if self.shutdown:
                tasks.append(asyncio.create_task(self.shutdown_with_warning(server)))
        # wait for DCS servers to shut down
        if tasks:
            await utils.run_parallel_nofail(*tasks)

    async def __aexit__(self, exc_type, exc_value, traceback):
        tasks = []
        for server in self.to_start:
            if server not in self.in_maintenance:
                server.maintenance = False
            elif self.shutdown:
                tasks.append(server.startup())

        if tasks:
            ret = await asyncio.gather(*tasks, return_exceptions=True)
            for idx in range(0, len(ret)):
                server = self.in_maintenance[idx]
                if isinstance(ret[idx], Exception):
                    self.node.log.error(f'Timeout while starting {server.display_name}, please check it manually!')
