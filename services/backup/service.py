import asyncio
import os
import subprocess
import time

from core import ServiceRegistry, Service, utils
from datetime import datetime
from discord.ext import tasks
from typing import Optional
from urllib.parse import urlparse
from zipfile import ZipFile

from ..servicebus.service import ServiceBus

__all__ = ["BackupService"]


@ServiceRegistry.register(plugin="backup", depends_on=[ServiceBus])
class BackupService(Service):
    def __init__(self, node):
        super().__init__(node=node, name="Backup")
        if not self.locals:
            self.log.debug("  - No backup.yaml configured, skipping backup service.")
            return
        if self._secure_password():
            self.save_config()

    def _secure_password(self):
        config = self.locals['backups'].get('database')
        if config and config.get("password"):
            utils.set_password("postgres", config.pop("password"), self.node.config_dir)
            return True
        return False

    async def start(self):
        if not self.locals:
            return
        await super().start()
        self.schedule.start()
        delete_after = self.locals.get('delete_after', 'never')
        if isinstance(delete_after, int) or delete_after.isnumeric():
            self.delete.start()

    async def stop(self, *args, **kwargs):
        if not self.locals:
            return
        self.schedule.cancel()
        delete_after = self.locals.get('delete_after', 'never')
        if isinstance(delete_after, int) or delete_after.isnumeric():
            self.delete.cancel()
        await super().stop()

    def mkdir(self) -> str:
        target = os.path.expandvars(self.locals.get('target'))
        directory = os.path.join(target, utils.slugify(self.node.name) + '_' + datetime.now().strftime("%Y%m%d"))
        os.makedirs(directory, exist_ok=True)
        return str(directory)

    @staticmethod
    def zip_path(zf: ZipFile, base: str, path: str):
        for root, dirs, files in os.walk(os.path.join(base, path)):
            for file in files:
                file_path = os.path.join(root, file)
                zf.write(file_path, arcname=os.path.relpath(file_path, base))

    async def backup_bot(self):
        await asyncio.to_thread(self._backup_bot)

    def _backup_bot(self) -> bool:
        self.log.info("Backing up DCSServerBot ...")
        target = self.mkdir()
        config = self.locals['backups'].get('bot')
        filename = "bot_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"
        zf = ZipFile(os.path.join(target, filename), mode="w")
        try:
            for directory in config.get('directories', [self.node.config_dir, 'reports']):
                self.zip_path(zf, "", directory)
            self.log.info("Backup of DCSServerBot complete.")
            return True
        except Exception:
            self.log.error(f'Backup of DCSServerBot failed.', exc_info=True)
            return False
        finally:
            zf.close()

    async def backup_servers(self):
        await asyncio.to_thread(self._backup_servers)

    def _backup_servers(self) -> bool:
        target = self.mkdir()
        config = self.locals['backups'].get('servers')
        rc = True

        for server_name, server in ServiceRegistry.get(ServiceBus).servers.items():
            if server.is_remote:
                continue
            self.log.info(f'Backing up server "{server_name}" ...')
            filename = f"{server.instance.name}_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"
            with ZipFile(os.path.join(target, filename), mode="w") as zf:
                try:
                    root_dir = server.instance.home
                    directories = config.get('directories', ['Config', 'Scripts'])
                    missions = next((directory for directory in directories if directory.lower() == 'missions'), None)
                    if missions:
                        mission_dir = os.path.normpath(server.instance.missions_dir).rstrip(os.sep)
                        self.zip_path(zf, os.path.dirname(mission_dir), os.path.basename(mission_dir))
                        directories.remove(missions)
                    for directory in directories:
                        self.zip_path(zf, root_dir, directory)
                    self.log.info(f'Backup of server "{server_name}" complete.')
                except Exception:
                    self.log.error(f'Backup of server "{server_name}" failed.', exc_info=True)
                    rc = False
        return rc

    async def backup_database(self):
        await asyncio.to_thread(self._backup_database)

    def _backup_database(self) -> bool:
        target = self.mkdir()
        config = self.locals['backups'].get('database')
        cmd = os.path.join(os.path.expandvars(config['path']), "pg_dump.exe")
        if not os.path.exists(cmd):
            raise FileNotFoundError(cmd)

        cpool_url = self.node.config.get("database", self.node.locals.get('database'))['url']
        lpool_url = self.node.locals.get("database", self.node.config.get('database'))['url']

        databases = [
            (
                urlparse(lpool_url),
                utils.get_password('database', self.node.config_dir),
                f"db_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".tar"
            )
        ]
        if cpool_url != lpool_url:
            databases.append(
                (
                    urlparse(cpool_url),
                    utils.get_password('clusterdb', self.node.config_dir),
                    f"clusterdb_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".tar"
                )
            )

        ret = True
        for url, password, filename in databases:
            try:
                password = utils.get_password('postgres', self.node.config_dir)
                username = config.get('username', 'postgres')
            except ValueError:
                username = url.username
                password = password
            database = url.path.strip('/')
            args = [
                '--no-owner',
                '--no-privileges',
                '-U', username,
                '-F', 't',
                '-f', os.path.join(target, filename),
                '-d', database,
                '-h', url.hostname
            ]
            try:
                os.environ['PGPASSWORD'] = password
            except ValueError:
                self.log.error(f"Backup of database {database} failed. No password set.")
                return False
            self.log.info(f'Backing up database "{database}" ...')
            process = subprocess.run([os.path.basename(cmd), *args], executable=cmd,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            rc = process.returncode
            if rc != 0:
                ret = False
                error_message = process.stderr.strip()
                self.log.info(f"Backup of database {database} failed. Code: {rc}. Error: {error_message}")
                continue
            else:
                self.log.info(f"Backup of database {database} complete.")

        return ret

    def recover_database(self, date: str):
        ...
#        target = os.path.expandvars(self.locals.get('target'))
#        path = os.path.join(target, f"{self.node.name.lower()}_{date}", f"db_{date}_*.tar")
#        filename = glob.glob(path)[0]
#        os.execv(sys.executable, [os.path.basename(sys.executable), 'recover.py', '-f', filename] + sys.argv[1:])

    def recover_bot(self, filename: str):
        ...

    def recover_server(self, filename: str):
        ...

    @staticmethod
    def can_run(config: Optional[dict] = None):
        if not config or 'schedule' not in config:
            return False
        now = datetime.now()
        if utils.is_match_daystate(now, config['schedule']['days']):
            for _time in config['schedule']['times']:
                if utils.is_in_timeframe(now, _time):
                    return True
        return False

    @tasks.loop(minutes=1)
    async def schedule(self):
        try:
            if self.node.master:
                if self.can_run(self.locals['backups'].get('bot')):
                    asyncio.create_task(self.backup_bot())
                if self.can_run(self.locals['backups'].get('database')):
                    asyncio.create_task(self.backup_database())
            if self.can_run(self.locals['backups'].get('servers')):
                asyncio.create_task((self.backup_servers()))
        except Exception as ex:
            self.log.exception(ex)

    @tasks.loop(hours=24)
    async def delete(self):
        try:
            path = os.path.expandvars(self.locals['target'])
            if not os.path.exists(path):
                return
            delete_after = int(self.locals['delete_after'])
            threshold_time = time.time() - delete_after * 86400
            for file in os.listdir(path):
                file_path = os.path.join(path, file)
                if os.path.getctime(file_path) < threshold_time:
                    self.log.debug(f"  => {file} is older then {delete_after} days, deleting ...")
                    utils.safe_rmtree(file_path)
        except Exception as ex:
            self.log.exception(ex)
