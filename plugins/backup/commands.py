import asyncio
import os
import platform
import shlex
import shutil
import time
from datetime import datetime
from zipfile import ZipFile
from discord.ext import tasks

from core import Plugin, DCSServerBot, utils, PluginInstallationError


class BackupAgent(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.json file found!", plugin=self.plugin_name)
        self.schedule.start()

    def cog_unload(self):
        self.schedule.stop()

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
        for server_name, server in self.bot.servers.items():
            self.log.info(f'Backing up server "{server_name}" ...')
            filename = f"{server.installation}_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"
            zf = ZipFile(os.path.join(target, filename), mode="w")
            try:
                rootdir = os.path.expandvars(self.bot.config[server.installation]['DCS_HOME'])
                for directory in config.get('directories'):
                    self.zip_path(zf, rootdir, directory)
            except Exception as ex:
                self.log.debug(ex)
                self.log.error(f'Backup of server "{server_name}" failed. See logfile for details.')
            finally:
                zf.close()
            self.log.info(f'Backup of server "{server_name}" complete.')

    @staticmethod
    def can_run(config: dict):
        now = datetime.now()
        if utils.is_match_daystate(now, config['schedule']['days']):
            for time in config['schedule']['times']:
                if utils.is_in_timeframe(now, time):
                    return True
        return False

    @tasks.loop(minutes=1)
    async def schedule(self):
        if 'bot' in self.locals['backups'] and self.can_run(self.locals['backups']['bot']):
            await asyncio.to_thread(self.backup_bot)
        if 'servers' in self.locals['backups'] and self.can_run(self.locals['backups']['servers']):
            await asyncio.to_thread(self.backup_servers)


class BackupMaster(BackupAgent):
    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        if self.locals['delete_after'].lower() != 'never':
            self.delete.start()

    async def cog_unload(self):
        super().cog_unload()
        if self.locals['delete_after'].lower() != 'never':
            self.delete.stop()

    async def backup_database(self):
        try:
            target = self.mkdir()
            self.log.info("Backing up database...")
            config = self.locals['backups'].get('database')
            cmd = os.path.join(os.path.expandvars(config['path']), "pg_dump")
            filename = f"db_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".tar"
            path = os.path.join(target, filename)
            database = f"{os.path.basename(self.bot.config['BOT']['DATABASE_URL'])}"
            exe = f'"{cmd}" -U postgres -F t -f "{path}" -d "{database}"'
            args = shlex.split(exe)
            os.environ['PGPASSWORD'] = config['password']
            process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                           stdout=asyncio.subprocess.DEVNULL)
            await process.wait()
            self.log.info("Backup of database complete.")
        except Exception as ex:
            self.log.debug(ex)
            self.log.error("Backup of database failed. See logfile for details.")

    @tasks.loop(minutes=1)
    async def schedule(self):
        await super().schedule()
        if 'database' in self.locals['backups'] and self.can_run(self.locals['backups']['database']):
            await self.backup_database()

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


async def setup(bot: DCSServerBot):
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(BackupMaster(bot))
    else:
        await bot.add_cog(BackupAgent(bot))
