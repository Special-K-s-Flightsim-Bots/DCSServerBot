from __future__ import annotations
import asyncio
import os
import platform
import traceback

from core import NodeImpl, ServiceRegistry
from migrate import migrate
from services import BotService, Dashboard
from typing import cast


class Main:

    def __init__(self, node: NodeImpl):
        self.node = node
        self.log = node.log

    async def run(self):
        await self.node.register()
        async with ServiceRegistry(node=self.node) as registry:
            bus = registry.new("ServiceBus")
            if self.node.master:
                # config = registry.new("Configuration")
                # asyncio.create_task(config.start())
                bot = cast(BotService, registry.new("Bot"))
                asyncio.create_task(bot.start())
            asyncio.create_task(bus.start())
            asyncio.create_task(registry.new("Monitoring").start())
            asyncio.create_task(registry.new("Backup").start())
            if self.node.config.get('use_dashboard', True):
                dashboard = cast(Dashboard, registry.new("Dashboard"))
                asyncio.create_task(dashboard.start())
            while True:
                # wait until the master changes
                while self.node.master == self.node.check_master():
                    await asyncio.sleep(1)
                # switch master
                self.node.master = not self.node.master
                if self.node.master:
                    self.log.info("Master is not responding... taking over.")
                    if self.node.config.get('use_dashboard', True):
                        await dashboard.stop()
                    # config = registry.new("Configuration")
                    # asyncio.create_task(config.start())
                    bot = cast(BotService, registry.new("Bot"))
                    asyncio.create_task(bot.start())
                else:
                    self.log.info("Second Master found, stepping back to Agent configuration.")
                    if self.node.config.get('use_dashboard', True):
                        await dashboard.stop()
                    # await config.stop()
                    await bot.stop()
                if self.node.config.get('use_dashboard', True):
                    await dashboard.start()


if __name__ == "__main__":
    if os.path.exists('config/dcsserverbot.ini'):
        migrate()
    elif not os.path.exists('config/main.yaml'):
        print("Please run 'python install.py' first.")
        exit(-1)
    if int(platform.python_version_tuple()[0]) < 3 or int(platform.python_version_tuple()[1]) < 9:
        print("You need Python 3.9 or higher to run DCSServerBot!")
        exit(-1)
    elif int(platform.python_version_tuple()[1]) == 9:
        print("Python 3.9 is outdated, you should consider upgrading it to 3.10 or higher.")
    try:
        # Install.verify()
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()
        )
        asyncio.run(Main(NodeImpl()).run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        exit(-1)
    except Exception as ex:
        traceback.print_exc()
        exit(-1)
