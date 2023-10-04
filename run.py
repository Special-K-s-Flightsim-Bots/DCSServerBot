from __future__ import annotations

import asyncio
import os
import platform
import traceback

from core import NodeImpl, ServiceRegistry, ServiceInstallationError
from install import Install
from migrate import migrate

# Register all services
import services


class Main:

    def __init__(self, node: NodeImpl):
        self.node = node
        self.log = node.log

    async def run(self):
        if self.node.locals.get('autoupdate', False):
            await self.node.upgrade()

        await self.node.register()
        async with ServiceRegistry(node=self.node) as registry:
            if registry.services():
                self.log.info("- Loading Services ...")
            for name in registry.services().keys():
                if not registry.can_run(name):
                    continue
                if name == 'Dashboard':
                    if self.node.config.get('use_dashboard', True):
                        self.log.info("  => Dashboard started.")
                        dashboard = registry.new(name)
                        asyncio.create_task(dashboard.start())
                    continue
                else:
                    try:
                        asyncio.create_task(registry.new(name).start())
                        self.log.debug(f"  => {name} loaded.")
                    except ServiceInstallationError as ex:
                        self.log.error(f"  - {ex.__str__()}")
                        self.log.info(f"  => {name} NOT loaded.")
            if not self.node.master:
                self.log.info("DCSServerBot AGENT started.")
            try:
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
                        for name in registry.services().keys():
                            if registry.master_only(name):
                                try:
                                    asyncio.create_task(registry.new(name).start())
                                except ServiceInstallationError as ex:
                                    self.log.error(f"  - {ex.__str__()}")
                                    self.log.info(f"  => {name} NOT loaded.")
                    else:
                        self.log.info("Second Master found, stepping back to Agent configuration.")
                        if self.node.config.get('use_dashboard', True):
                            await dashboard.stop()
                        for name in registry.services().keys():
                            if registry.master_only(name):
                                await registry.get(name).stop()
                    if self.node.config.get('use_dashboard', True):
                        await dashboard.start()
            finally:
                await self.node.unregister()


if __name__ == "__main__":
    if os.path.exists('config/dcsserverbot.ini'):
        migrate()
    elif not os.path.exists('config/main.yaml'):
        Install.install()
    if int(platform.python_version_tuple()[0]) < 3 or int(platform.python_version_tuple()[1]) < 9:
        print("You need Python 3.9 or higher to run DCSServerBot (3.11 recommended)!")
        exit(-2)
    elif int(platform.python_version_tuple()[1]) == 9:
        print("Python 3.9 is outdated, you should consider upgrading it to 3.10 or higher.")
    try:
        # work around possible bug with several Python versions / asyncio
        # asyncio.get_event_loop().run_until_complete()
        asyncio.run(Main(NodeImpl()).run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        exit(-1)
    except:
        traceback.print_exc()
        exit(-1)
