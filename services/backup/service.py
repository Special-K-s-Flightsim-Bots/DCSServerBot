import asyncio
import os
import platform
import shlex
import shutil
import time
from typing import TYPE_CHECKING

from core import ServiceRegistry, Service, utils
from datetime import datetime
from discord.ext import tasks
from zipfile import ZipFile

if TYPE_CHECKING:
    from .. import ServiceBus

__all__ = ["BackupService"]


@ServiceRegistry.register("Backup", plugin="backup")
class BackupService(Service):
    def __init__(self, node, name: str):
        super().__init__(node, name)
        if not self.locals:
            self.log.debug("  - No backup.yaml configured, skipping backup service.")
            return
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")

    async def start(self):
        if not self.locals:
            return
        await super().start()
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

    async def backup_bot(self):
        self.log.info("Backing up DCSServerBot ...")
        target = self.mkdir()
        config = self.locals['backups'].get('bot')
        filename = "bot_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"
        zf = ZipFile(os.path.join(target, filename), mode="w")
        try:
            for directory in config.get('directories', ['config', 'reports']):
                self.zip_path(zf, "", directory)
            self.log.info("Backup of DCSServerBot complete.")
        finally:
            zf.close()

    async def backup_servers(self):
        target = self.mkdir()
        config = self.locals['backups'].get('servers')
        for server_name, server in self.bus.servers.items():
            self.log.info(f'Backing up server "{server_name}" ...')
            filename = f"{server.instance.name}_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"
            zf = ZipFile(os.path.join(target, filename), mode="w")
            try:
                root_dir = server.instance.home
                for directory in config.get('directories', ['Config', 'Missions', 'Scripts']):
                    self.zip_path(zf, root_dir, directory)
            finally:
                zf.close()
            self.log.info(f'Backup of server "{server_name}" complete.')

    async def backup_database(self):
        target = self.mkdir()
        config = self.locals['backups'].get('database')
        cmd = os.path.join(os.path.expandvars(config['path']), "pg_dump.exe")
        if not os.path.exists(cmd):
            raise FileNotFoundError(cmd)
        filename = f"db_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".tar"
        path = os.path.join(target, filename)
        database = f"{os.path.basename(self.node.config['database']['url'])}"
        args = shlex.split(f'-U postgres -F t -f "{path}" -d "{database}"')
        os.environ['PGPASSWORD'] = config['password']
        self.log.info("Backing up database...")
        process = await asyncio.create_subprocess_exec(cmd, *args, stdin=asyncio.subprocess.DEVNULL,
                                                       stdout=asyncio.subprocess.DEVNULL)
        await process.communicate()
        self.log.info("Backup of database complete.")

    @staticmethod
    def can_run(config: dict):
        if 'schedule' not in config:
            return False
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
                try:
                    await self.backup_bot()
                except Exception as ex:
                    self.log.debug(ex)
                    self.log.error("Backup of bot failed. See logfile for details.")
            if 'database' in self.locals['backups'] and self.can_run(self.locals['backups']['database']):
                try:
                    await self.backup_database()
                except Exception as ex:
                    self.log.debug(ex)
                    self.log.error("Backup of database failed. See logfile for details.")
        if 'servers' in self.locals['backups'] and self.can_run(self.locals['backups']['servers']):
            try:
                await self.backup_servers()
            except Exception as ex:
                self.log.debug(ex)
                self.log.error("Backup of servers failed. See logfile for details.")

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
