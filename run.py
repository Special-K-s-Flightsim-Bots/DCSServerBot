from __future__ import annotations

import asyncio
import os
import platform
import psycopg
import sys

from core import (
    NodeImpl, ServiceRegistry, ServiceInstallationError, utils, YAMLError, FatalException, COMMAND_LINE_ARGS
)
from install import Install
from migrate import migrate
from pid import PidFile, PidFileError
from rich import print
from rich.console import Console

from services import Dashboard


class Main:

    def __init__(self, node: NodeImpl, no_autoupdate: bool) -> None:
        self.node = node
        self.log = node.log
        self.no_autoupdate = no_autoupdate
        utils.dynamic_import('services')

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
        elif self.node.master and await self.node.upgrade_pending():
            self.log.warning(
                "New update for DCSServerBot available! Use /node upgrade or enable autoupdate to apply it.")

        await self.node.register()
        async with ServiceRegistry(node=self.node) as registry:
            if registry.services():
                self.log.info("- Loading Services ...")
            for cls in registry.services().keys():
                if not registry.can_run(cls):
                    continue
                if cls == Dashboard:
                    if self.node.config.get('use_dashboard', True):
                        self.log.info("  => Dashboard started.")
                        dashboard = registry.new(Dashboard)
                        # noinspection PyAsyncCall
                        asyncio.create_task(dashboard.start())
                    continue
                else:
                    try:
                        # noinspection PyAsyncCall
                        asyncio.create_task(registry.new(cls).start())
                        self.log.debug(f"  => {cls.__name__} loaded.")
                    except ServiceInstallationError as ex:
                        self.log.error(f"  - {ex.__str__()}")
                        self.log.info(f"  => {cls.__name__} NOT loaded.")
                    except Exception as ex:
                        self.log.exception(ex)
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
                        for cls in registry.services().keys():
                            if registry.master_only(cls):
                                try:
                                    # noinspection PyAsyncCall
                                    asyncio.create_task(registry.new(cls).start())
                                except ServiceInstallationError as ex:
                                    self.log.error(f"  - {ex.__str__()}")
                                    self.log.info(f"  => {cls.__name__} NOT loaded.")
                    else:
                        self.log.info("Second Master found, stepping back to Agent configuration.")
                        if self.node.config.get('use_dashboard', True):
                            await dashboard.stop()
                        for cls in registry.services().keys():
                            if registry.master_only(cls):
                                await registry.get(cls).stop()
                    if self.node.config.get('use_dashboard', True):
                        await dashboard.start()
                    self.log.info(f"I am the {'MASTER' if self.node.master else 'AGENT'} now.")
            except Exception:
                self.log.warning("Aborting the main loop.")
                raise
            finally:
                await self.node.unregister()


def run_node(name, config_dir=None, no_autoupdate=False):
    with NodeImpl(name=name, config_dir=config_dir) as node:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(Main(node, no_autoupdate=no_autoupdate).run())


if __name__ == "__main__":
    console = Console()
    if int(platform.python_version_tuple()[0]) < 3 or int(platform.python_version_tuple()[1]) < 9:
        print("[red]You need Python 3.9 or higher to run DCSServerBot (3.11 recommended)![/]")
        exit(-2)
    elif int(platform.python_version_tuple()[1]) == 9:
        print("[yellow]Python 3.9 is outdated, you should consider upgrading it to 3.10 or higher.[/]")

    # disable quick edit mode (thanks to Moots)
    utils.quick_edit_mode(False)

    # get the command line args from core
    args = COMMAND_LINE_ARGS

    # Call the DCSServerBot 2.x migration utility
    if os.path.exists(os.path.join(args.config, 'dcsserverbot.ini')):
        migrate(node=args.node)
    try:
        with PidFile(pidname=f"dcssb_{args.node}", piddir='.'):
            try:
                run_node(name=args.node, config_dir=args.config, no_autoupdate=args.noupdate)
            except FatalException:
                Install(node=args.node).install()
                run_node(name=args.node, no_autoupdate=args.noupdate)
    except PermissionError:
        # do not restart again
        print(f"\n[red]There is a permission error.\n"
              f"Did you run DCSServerBot as Admin before? If yes, delete dcssb_{args.node} and try again.[/]")
        exit(-2)
    except PidFileError:
        print(f"\n[red]Process already running for node {args.node}! Exiting...[/]")
        # do not restart again
        exit(-2)
    except KeyboardInterrupt:
        # restart again (old handling)
        exit(-1)
    except asyncio.CancelledError:
        # do not restart again
        exit(-2)
    except (YAMLError, FatalException) as ex:
        print(f"\n[red]{ex}[/]")
        input("Press any key to continue ...")
        # do not restart again
        exit(-2)
    except psycopg.OperationalError as ex:
        print(f"\n[red]Database Error: {ex}[/]")
        input("Press any key to continue ...")
        # do not restart again
        exit(-2)
    except SystemExit as ex:
        exit(ex.code)
    except:
        console.print_exception(show_locals=True, max_frames=1)
        # restart on unknown errors
        exit(-1)
