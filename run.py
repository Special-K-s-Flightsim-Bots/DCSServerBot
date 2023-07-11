from __future__ import annotations

from core import NodeImpl
from migrate import migrate
from services import *


class Main:

    def __init__(self, node: NodeImpl):
        self.node = node
        self.log = node.log

    async def run(self):
        await self.node.register()
        async with ServiceRegistry(node=self.node) as registry:
            for name in registry.services().keys():
                if not self.node.master and registry.master_only(name):
                    continue
                if name == 'Dashboard':
                    if self.node.config.get('use_dashboard', True):
                        dashboard = registry.new(name)
                        asyncio.create_task(dashboard.start())
                    continue
                else:
                    asyncio.create_task(registry.new(name).start())
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
                                asyncio.create_task(registry.new(name).start())
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
