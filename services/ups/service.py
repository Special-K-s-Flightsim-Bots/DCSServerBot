import asyncio

from core import Service, ServiceRegistry, Coalition, Status
from discord.ext import tasks
from nut2 import PyNUTClient, PyNUTError
from services.cron.actions import halt
from services.servicebus import ServiceBus


@ServiceRegistry.register(depends_on=[ServiceBus])
class UPSService(Service):
    def __init__(self, node):
        super().__init__(node)
        self.bus = ServiceRegistry.get(ServiceBus)
        self.on_battery = False

    async def start(self):
        if not self.get_config():
            return
        await super().start()
        self.monitoring.start()

    async def stop(self):
        if self.monitoring.is_running():
            self.monitoring.cancel()
        await super().stop()

    @tasks.loop(minutes=1.0)
    async def monitoring(self):
        def get_ups_status() -> dict:
            client = PyNUTClient(
                config['host'],
                config.get('port', 3493),
                config.get('username'),
                config.get('password')
            )
            return client.list_vars(config['device'])

        config = self.get_config()
        if not config:
            return

        try:
            ups_status = await asyncio.to_thread(get_ups_status)
            if ups_status['ups.status'] in ['OB', 'LB', 'B']:
                self.on_battery = True
                thresholds = {
                    "warn": 90,
                    "shutdown": 50,
                    "halt": 20
                } | config.get('thresholds', {})
                for what, charge in thresholds.items():
                    self.log.warning(f"UPS is running on battery and charge is below {what} threshold of {charge}!")
                    if charge < thresholds['warn']:
                        for server in self.bus.servers.values():
                            if not server.is_remote:
                                asyncio.create_task(server.sendPopupMessage(
                                    Coalition.ALL,
                                    "*** Attention ***\n"
                                    "Server PC has a power failure. Expect a shutdown at any minute!")
                                )
                    elif charge < thresholds['shutdown']:
                        for server in self.bus.servers.values():
                            if not server.is_remote and server.status in [Status.RUNNING, Status.PAUSED]:
                                asyncio.create_task(server.shutdown(force=True))
                    elif charge < thresholds['halt']:
                        await halt(self.node)
                        return
            elif self.on_battery:
                self.on_battery = False
                for server in self.bus.servers.values():
                    if not server.is_remote:
                        asyncio.create_task(server.sendPopupMessage(
                            Coalition.ALL,
                            "Power is back, happy flying!")
                        )
        except PyNUTError as ex:
            self.log.error(f"Error connecting to UPS on {config['host']}:{config['port']}: {ex}")
