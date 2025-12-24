import asyncio
import glob
import os
import re
import shutil
import subprocess
import sys
import time

from core import ServiceRegistry, Service, utils
from datetime import datetime
from discord.ext import tasks
from pathlib import Path, PureWindowsPath
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
            utils.set_password(config.get("username", "postgres"), config.pop("password"), self.node.config_dir)
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
    def get_postgres_installations() -> list[dict]:
        import winreg

        postgres_installations = []
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\PostgreSQL\Installations") as key:
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            install_location = winreg.QueryValueEx(subkey, "Base Directory")[0]
                            version = winreg.QueryValueEx(subkey, "Version")[0]
                            postgres_installations.append({
                                'version': version,
                                'location': install_location
                            })
                        i += 1
                    except WindowsError:
                        break
        except WindowsError:
            pass
        return postgres_installations

    async def get_postgres_installation(self) -> str | None:
        if sys.platform == 'win32':
            # check the registry
            installations = self.get_postgres_installations()
            if len(installations) == 1 and os.path.exists(installations[0]['location']):
                return installations[0]['location']

        # we could not find the installation in the registry, so ask the database itself
        async with self.node.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT 
                    version(),
                    setting as data_directory
                FROM pg_settings 
                WHERE name = 'data_directory';
            """)
            version, location = await cursor.fetchone()
        if os.path.exists(location):
            # we need to remove the "data" directory
            return os.path.dirname(location)

        # check the file system itself
        else:
            # TODO: read that from the registry if available
            pg_path = Path(r"C:\Program Files\PostgreSQL")
            if not pg_path.exists():
                return None

            if os.path.exists(pg_path / str(version)):
                return str(pg_path / str(version))

            # Filter for integer-only directory names and convert to int for comparison
            versions = []
            for item in pg_path.iterdir():
                if item.is_dir() and item.name.isdigit():
                    versions.append(int(item.name))

            if versions:
                latest = max(versions)
                return str(pg_path / str(latest))
            return None

    @staticmethod
    def zip_path(zf: ZipFile, base: str, path: str):
        for root, dirs, files in os.walk(os.path.join(base, path)):
            for file in files:
                file_path = os.path.join(root, file)
                zf.write(file_path, arcname=os.path.relpath(file_path, base))

    async def backup_bot(self) -> bool:
        return await asyncio.to_thread(self._backup_bot)

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

    async def backup_servers(self) -> bool:
        return await asyncio.to_thread(self._backup_servers)

    def _backup_servers(self) -> bool:
        target = self.mkdir()
        config = self.locals['backups'].get('servers')
        rc = True

        for server_name, server in ServiceRegistry.get(ServiceBus).servers.items():
            if server.is_remote:
                continue
            self.log.info(f'Backing up server "{server_name}" ...')
            filename = f"{server.instance.name}_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"
            directories = config.get('directories', ['Config', 'Scripts'])
            root_dir = server.instance.home
            with ZipFile(os.path.join(target, filename), mode="w") as zf:
                try:
                    for directory in directories:
                        parts = PureWindowsPath(directory).parts
                        if parts[0].lower() == 'missions':
                            mission_dir = os.path.normpath(server.instance.missions_dir).rstrip(os.sep)
                            to_backup = os.path.join(os.path.dirname(mission_dir), directory)
                            if not os.path.exists(to_backup):
                                self.log.warning(f"{self.name}: Directory {to_backup} not found, skipping.")
                                continue
                            self.zip_path(zf, os.path.dirname(mission_dir), directory)
                        else:
                            to_backup = os.path.join(root_dir, directory)
                            if not os.path.exists(to_backup):
                                self.log.warning(f"{self.name}: Directory {to_backup} not found, skipping.")
                                continue
                            self.zip_path(zf, root_dir, directory)
                    self.log.info(f'Backup of server "{server_name}" complete.')
                except Exception:
                    self.log.error(f'Backup of server "{server_name}" failed.', exc_info=True)
                    rc = False
        return rc

    async def backup_database(self) -> bool:
        path = self.locals['backups']['database'].get('path')
        if not path:
            installation = await self.get_postgres_installation()
            if installation:
                path = os.path.join(installation, "bin")
            else:
                self.log.error("Could not find PostgreSQL installation. Please set the path in the backup.yaml.")
                return False
        return await asyncio.to_thread(self._backup_database, path)

    def _backup_database(self, path: str) -> bool:
        target = self.mkdir()
        config = self.locals['backups'].get('database')
        cmd = os.path.join(path, "pg_dump.exe")
        if not os.path.exists(cmd):
            raise FileNotFoundError(cmd)

        _, lpool_url = self.node.get_database_urls()
        url = urlparse(lpool_url)
        filename = f"db_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".tar"

        try:
            username = config.get('username', 'postgres')
            password = utils.get_password(username, self.node.config_dir)
        except ValueError:
            username = url.username
            password = url.password

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
        os.environ['PGPASSWORD'] = password
        self.log.info(f'Backing up database "{database}" ...')
        process = subprocess.run([os.path.basename(cmd), *args], executable=cmd,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        rc = process.returncode
        if rc != 0:
            error_message = process.stderr.strip()
            self.log.info(f"Backup of database {database} failed. Code: {rc}. Error: {error_message}")
            return False
        else:
            self.log.info(f"Backup of database {database} complete.")
        return True

    async def restart(self):
        for node in self.node.all_nodes.values():
            if not node or node == self.node:
                continue
            await node.restart()
        await self.node.restart()

    async def restore_database(self, date: str):
        if not self.node.master:
            raise RuntimeError(f"Cannot restore database on non-master node {self.node.name}")

        target = os.path.expandvars(self.locals.get('target'))
        path = os.path.join(target, f"{self.node.name.lower()}_{date}", f"db_{date}_*.tar")
        filename = glob.glob(path)[0]
        os.makedirs('restore', exist_ok=True)
        shutil.copy2(filename, 'restore')
        await self.restart()

    async def restore_bot(self, date: str):
        if not self.node.master:
            raise RuntimeError(f"Cannot restore DCSServerBot configuration on non-master node {self.node.name}")
        target = os.path.expandvars(self.locals.get('target'))
        path = os.path.join(target, f"{self.node.name.lower()}_{date}", f"bot_{date}_*.zip")
        filename = glob.glob(path)[0]
        os.makedirs('restore', exist_ok=True)
        shutil.copy2(filename, 'restore')
        await self.restart()

    async def restore_instances(self, date: str):
        target = os.path.expandvars(self.locals.get('target'))
        path = os.path.join(target, f"{self.node.name.lower()}_{date}")
        for file in Path(path).glob('*'):
            if file.name.startswith('db_') or file.name.startswith('bot_'):
                continue
            instance = re.match(r'^(.+?)_(?=\d{8}_\d{6}\.zip$)', file.name).group(1)
            copied = False
            if self.node.locals.get('instances', {}).get(instance):
                shutil.copy2(file, 'restore')
                copied = True
            if copied:
                await self.restart()

    @staticmethod
    def can_run(config: dict | None = None):
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
            tasks = []
            if self.node.master:
                if self.can_run(self.locals['backups'].get('bot')):
                    tasks.append(asyncio.create_task(self.backup_bot()))
                if self.can_run(self.locals['backups'].get('database')):
                    tasks.append(asyncio.create_task(self.backup_database()))
            if self.can_run(self.locals['backups'].get('servers')):
                tasks.append(asyncio.create_task((self.backup_servers())))
            ret = await asyncio.gather(*tasks, return_exceptions=True)
            for r in ret:
                if isinstance(r, Exception):
                    self.log.error("Backup task failed: ", exc_info=r)
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
