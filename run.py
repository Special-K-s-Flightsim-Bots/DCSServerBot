from __future__ import annotations

# Default imports
import asyncio
import certifi
import discord
import faulthandler
import logging
import os
import psycopg
import sys
import time
import traceback

from datetime import datetime
from pathlib import Path
from psycopg import OperationalError
from typing import Any, Coroutine

# DCSServerBot imports
try:
    from core import (
        NodeImpl, ServiceRegistry, ServiceInstallationError, utils, YAMLError, FatalException, COMMAND_LINE_ARGS,
        CloudRotatingFileHandler, wait_for_internet
)
    from pid import PidFile, PidFileError
    from rich import print
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.text import Text

    # ruamel YAML support
    from ruamel.yaml import YAML
    yaml = YAML()
except ModuleNotFoundError as ex:
    import subprocess

    print(f"Module {ex.name} is not installed, fixing ...")
    cmd = [
        sys.executable,
        '-m', 'piptools', 'sync', 'requirements.txt'
    ]
    if os.path.exists("requirements.local"):
        cmd.append('requirements.local')
    subprocess.run(cmd)
    exit(-1)

LOGLEVEL = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
    'FATAL': logging.FATAL
}


class Main:

    def __init__(self, node: NodeImpl, no_autoupdate: bool) -> None:
        self.node = node
        self.log = logging.getLogger(__name__)
        self.no_autoupdate = no_autoupdate

    @staticmethod
    def setup_logging(node: str, config_dir: str):
        def time_formatter(time: datetime, _: str = None) -> Text:
            return Text(time.strftime('%H:%M:%S'))

        # Setup console logger
        ch = RichHandler(rich_tracebacks=True, tracebacks_suppress=[discord], log_time_format=time_formatter)
        ch.setLevel(logging.INFO)

        # Setup file logging
        try:
            config = yaml.load(Path(os.path.join(config_dir, 'main.yaml')).read_text(encoding='utf-8'))['logging']
        except (FileNotFoundError, KeyError, YAMLError):
            config = {}
        os.makedirs('logs', exist_ok=True)
        fh = CloudRotatingFileHandler(os.path.join('logs', f'dcssb-{node}.log'), encoding='utf-8',
                                      maxBytes=config.get('logrotate_size', 10485760),
                                      backupCount=config.get('logrotate_count', 5))
        fh.setLevel(LOGLEVEL[config.get('loglevel', 'DEBUG')])
        formatter = logging.Formatter(fmt=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
        if config.get('utc', True):
            formatter.converter = time.gmtime
        fh.setFormatter(formatter)
        fh.doRollover()

        # Configure the root logger
        logging.basicConfig(level=LOGLEVEL[config.get('loglevel', 'DEBUG')], format="%(message)s", handlers=[ch, fh])

        # Change 3rd-party logging
        logging.getLogger(name='asyncio').setLevel(logging.WARNING)
        logging.getLogger(name='discord').setLevel(logging.ERROR)
        logging.getLogger(name="eye3d").setLevel(logging.ERROR)
        logging.getLogger(name='git').setLevel(logging.WARNING)
        logging.getLogger(name='matplotlib').setLevel(logging.ERROR)
        logging.getLogger(name="multipart").setLevel(logging.ERROR)
        logging.getLogger(name='PidFile').setLevel(logging.ERROR)
        logging.getLogger(name='PIL').setLevel(logging.INFO)
        logging.getLogger(name='psycopg.pool').setLevel(logging.WARNING)
        logging.getLogger(name='pykwalify').setLevel(logging.CRITICAL)

        # Performance logging
        perf_logger = logging.getLogger(name='performance_log')
        perf_logger.setLevel(LOGLEVEL[config.get('loglevel', 'DEBUG')])
        perf_logger.propagate = False
        pfh = CloudRotatingFileHandler(os.path.join('logs', f'perf-{node}.log'), encoding='utf-8',
                                       maxBytes=config.get('logrotate_size', 10485760),
                                       backupCount=config.get('logrotate_count', 5))
        pff = logging.Formatter('%(asctime)s\t%(levelname)s\t%(message)s')
        if config.get('utc', True):
            pff.converter = time.gmtime
        pfh.setFormatter(pff)
        pfh.doRollover()
        perf_logger.addHandler(pfh)

    @staticmethod
    def reveal_passwords(config_dir: str):
        print("[yellow]These are your hidden secrets:[/]")
        for file in utils.list_all_files(os.path.join(config_dir, '.secret')):
            if not file.endswith('.pkl'):
                continue
            key = file[:-4]
            print(f"{key}: {utils.get_password(key, config_dir)}")
        print("\n[red]DO NOT SHARE THESE SECRET KEYS![/]")

    async def run(self):
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
            cloud_drive = self.node.locals.get('cluster', {}).get('cloud_drive', True)
            if (cloud_drive and self.node.master) or not cloud_drive:
                await self.node.upgrade()
                if self.node.is_shutdown.is_set():
                    return
        elif self.node.master and await self.node.upgrade_pending():
            self.log.warning(
                "New update for DCSServerBot available!\nUse /node upgrade or enable autoupdate to apply it.")

        await self.node.register()
        db_available = True
        async with ServiceRegistry(node=self.node) as registry:
            self.log.info("DCSServerBot {} started.".format("MASTER" if self.node.master else "AGENT"))
            try:
                while True:
                    # wait until the master changes
                    while self.node.master == await self.node.heartbeat():
                        if self.node.is_shutdown.is_set():
                            return
                        await asyncio.sleep(5)
                    # switch master
                    self.node.master = not self.node.master
                    if self.node.master:
                        self.log.info("Taking over as the MASTER node ...")
                        # start all the master-only services
                        for cls in [x for x in registry.services().keys() if registry.master_only(x)]:
                            try:
                                await registry.new(cls).start()
                            except Exception as ex:
                                self.log.error(f"  - {ex.__str__()}")
                                self.log.error(f"  => {cls.__name__} NOT loaded.")
                        # now switch all others
                        for cls in [x for x in registry.services().keys() if not registry.master_only(x)]:
                            service = registry.get(cls)
                            if service:
                                await service.switch()
                    else:
                        self.log.info("Second MASTER found, stepping back to AGENT configuration.")
                        for cls in registry.services().keys():
                            if registry.master_only(cls):
                                await registry.get(cls).stop()
                            else:
                                service = registry.get(cls)
                                if service:
                                    await service.switch()
                    self.log.info(f"I am the {'MASTER' if self.node.master else 'AGENT'} now.")
            except OperationalError:
                db_available = False
                raise
            finally:
                self.log.warning("Aborting the main loop ...")
                if db_available:
                    await self.node.unregister()


def handle_exception(loop, context):
    # Extract exception details from context
    exception = context.get('exception')
    message = context.get('message')

    # Log detailed information
    if exception:
        log.error(f"Async error: {message}", exc_info=exception)
    else:
        log.error(f"Async error: {message}")

    # Write to async_errors.log with task information
    with open(os.path.join('logs', 'async_errors.log'), 'a', encoding='utf-8') as f:
        f.write(f"\n{'=' * 50}\n{datetime.now().isoformat()}: {message}\n")

        # Dump all running tasks
        f.write("\nRunning tasks:\n")
        for task in asyncio.all_tasks(loop):
            f.write(f"Task {task.get_name()}: {str(task)}\n")
            # Get task stack
            stack = task.get_stack()
            if stack:
                f.write('Stack:\n')
                f.write(''.join(traceback.format_stack(stack[-1])))
            f.write('\n')

        if exception:
            f.write("\nException details:\n")
            traceback.print_exception(type(exception), exception, exception.__traceback__, file=f)
        f.write(f"{'=' * 50}\n")


async def run_node(name, config_dir=None, no_autoupdate=False) -> int:
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(handle_exception)

    async with NodeImpl(name=name, config_dir=config_dir) as node:
        await Main(node, no_autoupdate=no_autoupdate).run()
        return node.rc

async def restore_node(name: str, config_dir: str, restarted: bool) -> int:
    from restore import Restore

    print("[blink][red]***********************\n"
          "*** RESTORE PROCESS ***\n"
          "***********************\n[/red][/blink]")
    print("")
    print("Processing ...")
    restore = Restore(name, config_dir, quiet=restarted)
    try:
        return await restore.run(Path('restore'), delete=True)
    finally:
        utils.safe_rmtree('restore')


def myasyncio_run(func: Coroutine[Any, Any, Any]) -> Any:
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        import selectors

        return asyncio.run(func, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        return asyncio.run(func)


if __name__ == "__main__":
    console = Console()

    if sys.platform == 'win32':
        # disable quick edit mode (thanks to Moots)
        utils.quick_edit_mode(False)
        if sys.version_info < (3, 14):
            # set the asyncio event loop policy
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # get the command line args from core
    args = COMMAND_LINE_ARGS

    # Setup the logging
    Main.setup_logging(args.node, args.config)
    log = logging.getLogger("dcsserverbot")
    # check if we should reveal the passwords
    utils.create_secret_dir(args.config)
    if args.secret:
        Main.reveal_passwords(args.config)
        exit(-2)

    # Require Python >= 3.10
    if sys.version_info < (3,10):
        print("ERROR: DCSServerBot requires Python >= 3.10.")
        sys.exit(-2)
    elif sys.version_info < (3,11):
        print(
"""
WARNING: DCSServerBot will drop support for Pyton 3.10 soon.
         Please upgrade to Python 3.11+
""")

    # Add certificates
    os.environ["SSL_CERT_FILE"] = certifi.where()

    # Call the DCSServerBot 2.x migration utility
    if os.path.exists(os.path.join(args.config, 'dcsserverbot.ini')):
        from migrate import migrate_3

        migrate_3(node=args.node)

    # Call the restore process
    if os.path.exists('restore'):
        rc = myasyncio_run(restore_node(name=args.node, config_dir=args.config, restarted=args.restarted))
        if rc:
            exit(rc)
        else:
            print("")

    fault_log = open(os.path.join('logs', 'fault.log'), 'w')
    if args.ping:
        # wait for an internet connection to be available (after system reboots)
        log.info("Checking internet connection ...")
        if not myasyncio_run(wait_for_internet(host="8.8.8.8", timeout=300.0)):
            print("Internet connection not available. Exiting.")
            exit(-1)
    try:
        # enable faulthandler
        faulthandler.enable(file=fault_log, all_threads=True)

        with PidFile(pidname=f"dcssb_{args.node}", piddir='.'):
            try:
                rc = myasyncio_run(run_node(name=args.node, config_dir=args.config, no_autoupdate=args.noupdate))
            except FatalException:
                from install import Install

                Install(node=args.node).install(config_dir=args.config, user='dcsserverbot', database='dcsserverbot')
                rc = myasyncio_run(run_node(name=args.node, config_dir=args.config, no_autoupdate=args.noupdate))
    except PermissionError as ex:
        log.error(f"There is a permission error: {ex}", exc_info=True)
        # do not restart again
        rc = -2
    except PidFileError:
        log.error(f"Process already running for node {args.node}!")
        log.error(f"If you are sure there is no 2nd process running, delete dcssb_{args.node}.pid and try again.")
        # do not restart again
        rc = -2
    except KeyboardInterrupt:
        # restart again (old handling)
        rc = -1
    except asyncio.CancelledError:
        log.warning("Main loop cancelled.")
        # do not restart again
        rc = -2
    except (YAMLError, FatalException) as ex:
        log.exception(ex)
        input("Press any key to continue ...")
        # do not restart again
        rc = -2
    except psycopg.OperationalError as ex:
        log.exception(ex)
        # try again on Database errors
        rc = -1
    except SystemExit as ex:
        rc = ex.code
        if rc not in [0, -1, -2]:
            log.exception(ex)
    except:
        console.print_exception(show_locals=True, max_frames=1)
        # do not restart on unknown errors
        rc = -2
    finally:
        log.info("DCSServerBot stopped.")
        fault_log.close()
    exit(rc)
