from __future__ import annotations

import asyncio
import os
import platform
import psycopg
import sys
import traceback

from core import NodeImpl, ServiceRegistry, ServiceInstallationError, YAMLError, FatalException, COMMAND_LINE_ARGS
from install import Install
from migrate import migrate
from pid import PidFile, PidFileError

# Register all services
import services


class Main:

    def __init__(self, node: NodeImpl, no_autoupdate: bool) -> None:
        self.node = node
        self.log = node.log
        self.no_autoupdate = no_autoupdate

    async def run(self):
        await self.node.post_init()
        # check for updates
        if self.no_autoupdate:
            autoupdate = False
            # remove the exec parameter, to allow restart/update of the node
            if '--x' in sys.argv:
                sys.argv.remove('--x')
            elif '--noupdate' in sys.argv:
                sys.argv.remove('--noupdate')
        else:
            autoupdate = self.node.locals.get('autoupdate', self.node.config.get('autoupdate', False))

        if autoupdate:
            cloud_drive = self.node.locals.get('cloud_drive', True)
            if (cloud_drive and self.node.master) or not cloud_drive:
                await self.node.upgrade()
        elif await self.node.upgrade_pending():
            self.log.warning(
                "New update for DCSServerBot available! Use /node upgrade or enable autoupdate to apply it.")

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
                        # noinspection PyAsyncCall
                        asyncio.create_task(dashboard.start())
                    continue
                else:
                    try:
                        # noinspection PyAsyncCall
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
                    while self.node.master == await self.node.heartbeat():
                        await asyncio.sleep(5)
                    # switch master
                    self.node.master = not self.node.master
                    if self.node.master:
                        self.log.info("Taking over the Master node ...")
                        if self.node.config.get('use_dashboard', True):
                            await dashboard.stop()
                        for name in registry.services().keys():
                            if registry.master_only(name):
                                try:
                                    # noinspection PyAsyncCall
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
                    self.log.info(f"I am the {'MASTER' if self.node.master else 'AGENT'} now.")
            finally:
                await self.node.unregister()


if __name__ == "__main__":
    if int(platform.python_version_tuple()[0]) < 3 or int(platform.python_version_tuple()[1]) < 9:
        print("You need Python 3.9 or higher to run DCSServerBot (3.11 recommended)!")
        exit(-2)
    elif int(platform.python_version_tuple()[1]) == 9:
        print("Python 3.9 is outdated, you should consider upgrading it to 3.10 or higher.")

    # get the command line args from core
    args = COMMAND_LINE_ARGS

    # Call the DCSServerBot 2.x migration utility
    if os.path.exists(os.path.join(args.config, 'dcsserverbot.ini')):
        migrate(node=args.node)
    try:
        with PidFile(pidname=f"dcssb_{args.node}", piddir='.'):
            try:
                node = NodeImpl(name=args.node, config_dir=args.config)
            except FatalException:
                Install(node=args.node).install()
                node = NodeImpl(name=args.node)
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            asyncio.run(Main(node, no_autoupdate=args.noupdate).run())
    except PermissionError:
        traceback.print_exc()
        exit(-2)
    except PidFileError:
        print(f"Process already running for node {args.node}! Exiting...")
        exit(-2)
    except KeyboardInterrupt:
        # restart again (old handling)
        exit(-1)
    except asyncio.CancelledError:
        traceback.print_exc()
        # do not restart again
        exit(-2)
    except (YAMLError, FatalException, psycopg.OperationalError) as ex:
        print(ex)
        # do not restart again
        exit(-2)
    except SystemExit as ex:
        exit(ex.code)
    except:
        traceback.print_exc()
        # restart on unknown errors
        exit(-1)
