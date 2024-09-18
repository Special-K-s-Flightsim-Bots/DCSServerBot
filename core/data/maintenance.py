import asyncio

from .node import Node
from .server import Server
from .const import Status, Coalition

from ..utils.helper import format_time


class ServerMaintenanceManager:
    def __init__(self, node: Node, warn_times: list[int], message: str):
        self.node: Node = node
        self.warn_times: list[int] = warn_times
        self.message: str = message
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
            if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                continue
            if server.maintenance:
                self.in_maintenance.append(server)
            else:
                server.maintenance = True
            self.to_start.append(server)
            tasks.append(asyncio.create_task(self.shutdown_with_warning(server)))
        # wait for DCS servers to shut down
        if tasks:
            await asyncio.gather(*tasks)

    async def __aexit__(self, exc_type, exc_value, traceback):
        for server in self.to_start:
            if server not in self.in_maintenance:
                server.maintenance = False
            try:
                # the server was running before (being in maintenance mode), so start it again
                await server.startup()
            except (TimeoutError, asyncio.TimeoutError):
                self.node.log.warning(f'Timeout while starting {server.display_name}, please check it manually!')