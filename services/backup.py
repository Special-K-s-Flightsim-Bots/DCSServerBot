import os
import platform
import shlex
import shutil
import subprocess
import time
from typing import cast

from core import ServiceRegistry, Service, utils
from datetime import datetime
from discord.ext import tasks
from zipfile import ZipFile

from .servicebus import ServiceBus


@ServiceRegistry.register("Backup")
class BackupService(Service):
    def __init__(self, node, name: str):
        super().__init__(node, name)
        if not self.locals:
            self.log.debug("No backup.yaml configured, skipping backup service.")
            return
        self.bus: ServiceBus = cast(ServiceBus, ServiceRegistry.get("ServiceBus"))

    async def start(self):
        if not self.locals:
            return
        self.schedule.start()
        if self.locals['delete_after'].lower() != 'never':
            self.delete.start()

    async def stop(self, *args, **kwargs):
        if not self.locals:
            return
        self.schedule.stop()
        if self.locals['delete_after'].lower() != 'never':
            self.delete.stop()

    def mkdir(self) -> str:
        target = os.path.expandvars(self.locals.get('target'))
        directory = os.path.join(target, utils.slugify(platform.node()) + '_' + datetime.now().strftime("%Y%m%d"))
        os.makedirs(directory, exist_ok=True)
        return directory

    @staticmethod
    def zip_path(zf: ZipFile, base: str, path: str):
        for root, dirs, files in os.walk(os.path.join(base, path)):
            for file in files:
                zf.write(os.path.join(root, file), os.path.join(root.replace(base, ''), file))

    def backup_bot(self):
        self.log.info("Backing up DCSServerBot ...")
        target = self.mkdir()
        config = self.locals['backups'].get('bot')
        filename = "bot_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"
        zf = ZipFile(os.path.join(target, filename), mode="w")
        try:
            for directory in config.get('directories'):
                self.zip_path(zf, "", directory)
            self.log.info("Backup of DCSServerBot complete.")
        except Exception as ex:
            self.log.debug(ex)
            self.log.error("Backup of DCSServerBot failed. See logfile for details.")
        finally:
            zf.close()

    def backup_servers(self):
        target = self.mkdir()
        config = self.locals['backups'].get('servers')
        for server_name, server in self.bus.servers.items():
            self.log.info(f'Backing up server "{server_name}" ...')
            filename = f"{server.instance.name}_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"
            zf = ZipFile(os.path.join(target, filename), mode="w")
            try:
                root_dir = server.instance.home
                for directory in config.get('directories'):
                    self.zip_path(zf, root_dir, directory)
            except Exception as ex:
                self.log.debug(ex)
                self.log.error(f'Backup of server "{server_name}" failed. See logfile for details.')
            finally:
                zf.close()
            self.log.info(f'Backup of server "{server_name}" complete.')

    async def backup_database(self):
        try:
            target = self.mkdir()
            self.log.info("Backing up database...")
            config = self.locals['backups'].get('database')
            cmd = os.path.join(os.path.expandvars(config['path']), "pg_dump.exe")
            filename = f"db_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".tar"
            path = os.path.join(target, filename)
            database = f"{os.path.basename(self.node.config['database']['url'])}"
            exe = f'"{os.path.basename(cmd)}" -U postgres -F t -f "{path}" -d "{database}"'
            args = shlex.split(exe)
            os.environ['PGPASSWORD'] = config['password']
            subprocess.run(args, executable=cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            self.log.info("Backup of database complete.")
        except Exception as ex:
            self.log.debug(ex)
            self.log.error("Backup of database failed. See logfile for details.")

    @staticmethod
    def can_run(config: dict):
        now = datetime.now()
        if utils.is_match_daystate(now, config['schedule']['days']):
            for _time in config['schedule']['times']:
                if utils.is_in_timeframe(now, _time):
                    return True
        return False

    @tasks.loop(minutes=1)
    async def schedule(self):
        if self.node.master:
            if 'bot' in self.locals['backups'] and self.can_run(self.locals['backups']['bot']):
                await asyncio.to_thread(self.backup_bot)
            if 'database' in self.locals['backups'] and self.can_run(self.locals['backups']['database']):
                await self.backup_database()
        if 'servers' in self.locals['backups'] and self.can_run(self.locals['backups']['servers']):
            await asyncio.to_thread(self.backup_servers)

    @tasks.loop(hours=24)
    async def delete(self):
        try:
            path = os.path.expandvars(self.locals['target'])
            if not os.path.exists(path):
                return
            now = time.time()
            for f in [os.path.join(path, x) for x in os.listdir(path)]:
                if os.stat(f).st_mtime < (now - int(self.locals['delete_after']) * 86400):
                    if os.path.isfile(f):
                        os.remove(f)
                    else:
                        shutil.rmtree(f)
        except Exception as ex:
            self.log.exception(ex)
