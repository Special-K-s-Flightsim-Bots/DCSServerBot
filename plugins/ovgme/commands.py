import discord
import os
import psycopg2
import re
import shutil
import zipfile
from contextlib import closing, suppress
from core import Status, Plugin, DCSServerBot, PluginConfigurationError, utils, Server
from discord.ext import commands
from typing import Optional, Tuple

OVGME_FOLDERS = ['RootFolder', 'SavedGames']


class OvGME(Plugin):
    def install(self):
        super().install()
        if self.locals and 'configs' in self.locals:
            config = self.locals['configs'][0]
            for folder in OVGME_FOLDERS:
                if folder not in config:
                    raise PluginConfigurationError(self.plugin_name, folder)
            self.install_packages()

    async def before_dcs_update(self):
        # uninstall all RootFolder-packages
        for server_name, server in self.bot.servers.items():
            for package_name, version in self.get_installed_packages(server, 'RootFolder'):
                self.uninstall_package(server, 'RootFolder', package_name, version)

    async def after_dcs_update(self):
        self.install_packages()

    def rename(self, old_name: str, new_name: str):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE ovgme_packages SET server_name = %s WHERE server_name = %s',
                               (new_name, old_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @staticmethod
    def parse_filename(filename: str) -> Optional[Tuple[str, str]]:
        if filename.endswith('.zip'):
            filename = filename[:-4]
        exp = re.compile('(?P<package>.*)_v(?P<version>.*)')
        match = exp.match(filename)
        if match:
            return match.group('package'), match.group('version')
        else:
            return None

    @staticmethod
    def is_greater(v1: str, v2: str):
        parts1 = [int(x) for x in v1.split('.')]
        parts2 = [int(x) for x in v2.split('.')]
        for i in range(0, max(len(parts1), len(parts2))):
            if parts1[i] > parts2[i]:
                return True
        return False

    def install_packages(self):
        if not self.locals or 'configs' not in self.locals:
            return
        for server_name, server in self.bot.servers.items():
            config = self.get_config(server)
            if 'packages' not in config:
                return
            for package in config['packages']:
                version = package['version'] if package['version'] != 'latest' \
                    else self.get_latest_version(package['source'], package['name'])
                installed = self.check_package(server, package['source'], package['name'])
                # If the bot is still starting up (default), we're trying to figure out the state of the DCS process
                p = utils.find_process('DCS.exe', server.installation)
                if (not installed or installed != version) and \
                        (p or server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]):
                    self.log.warning(f"  - Server {server.name} needs to be shutdown to install packages.")
                    break
                maintenance = server.maintenance
                server.maintenance = True
                try:
                    if not installed:
                        if self.install_package(server, package['source'], package['name'], version):
                            self.log.info(f"  - Package {package['name']}_v{version} installed.")
                        else:
                            self.log.warning(f"  - Package {package['name']}_v{version} not found!")
                    elif installed != version:
                        if self.is_greater(installed, version):
                            self.log.debug(f"  - Installed package {package['name']}_v{installed} is newer than the "
                                           f"configured version. Skipping.")
                            continue
                        if not self.uninstall_package(server, package['source'], package['name'], installed):
                            self.log.warning(f"  - Package {package['name']}_v{installed} could not be uninstalled!")
                        elif not self.install_package(server, package['source'], package['name'], version):
                            self.log.warning(f"  - Package {package['name']}_v{version} could not be installed!")
                        else:
                            self.log.info(f"  - Package {package['name']}_v{installed} updated to v{version}.")
                finally:
                    if maintenance:
                        server.maintenance = maintenance
                    else:
                        server.maintenance = False

    def get_latest_version(self, folder: str, package: str) -> str:
        config = self.locals['configs'][0]
        path = os.path.expandvars(config[folder])
        available = [self.parse_filename(x) for x in os.listdir(path) if package in x]
        max_version = None
        for _, version in available:
            if not max_version or self.is_greater(version, max_version):
                max_version = version
        return max_version

    def check_package(self, server: Server, folder: str, package_name: str) -> Optional[str]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT version FROM ovgme_packages WHERE server_name = %s AND package_name = %s AND '
                               'folder = %s', (server.name, package_name, folder))
                return cursor.fetchone()[0] if cursor.rowcount == 1 else None
        finally:
            self.pool.putconn(conn)

    def install_package(self, server: Server, folder: str, package_name: str, version: str) -> bool:
        config = self.get_config(server)
        path = os.path.expandvars(config[folder])
        os.makedirs(os.path.join(path, '.' + server.installation), exist_ok=True)
        target = os.path.expandvars(self.bot.config['DCS']['DCS_INSTALLATION']) if folder == 'RootFolder' else \
            os.path.expandvars(self.bot.config[server.installation]['DCS_HOME'])
        for file in os.listdir(path):
            filename = os.path.join(path, file)
            if (os.path.isfile(filename) and file == package_name + '_v' + version + '.zip') or \
                    (os.path.isdir(filename) and file == package_name + '_v' + version):
                ovgme_path = os.path.join(path, '.' + server.installation, package_name + '_v' + version)
                os.makedirs(ovgme_path, exist_ok=True)
                if os.path.isfile(filename) and file == package_name + '_v' + version + '.zip':
                    with open(os.path.join(ovgme_path, 'install.log'), 'w') as log:
                        with zipfile.ZipFile(filename, 'r') as zip:
                            for name in zip.namelist():
                                orig = os.path.join(target, name)
                                if os.path.exists(orig) and os.path.isfile(orig):
                                    log.write(f"x {name}\n")
                                    shutil.copy2(orig, os.path.join(ovgme_path, name))
                                else:
                                    log.write(f"w {name}\n")
                                zip.extract(name, target)
                else:
                    with open(os.path.join(ovgme_path, 'install.log'), 'w') as log:
                        def backup(p, names) -> list[str]:
                            dir = p[len(os.path.join(path, package_name + '_v' + version)):].replace('\\', '/').lstrip('/')
                            for name in names:
                                if len(dir):
                                    name = dir + '/' + name
                                orig = os.path.join(target, name)
                                if os.path.exists(orig) and os.path.isfile(orig):
                                    log.write(f"x {name}\n")
                                    shutil.copy2(orig, os.path.join(ovgme_path, name))
                                else:
                                    log.write(f"w {name}\n")
                            return []

                        shutil.copytree(filename, target, ignore=backup, dirs_exist_ok=True)
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('INSERT INTO ovgme_packages (server_name, package_name, version, folder) '
                                       'VALUES (%s, %s, %s, %s)', (server.name, package_name, version,
                                                                   folder))
                    conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)
                return True
        return False

    def uninstall_package(self, server: Server, folder: str, package_name: str, version: str) -> bool:
        config = self.get_config(server)
        path = os.path.expandvars(config[folder])
        ovgme_path = os.path.join(path, '.' + server.installation, package_name + '_v' + version)
        target = os.path.expandvars(self.bot.config['DCS']['DCS_INSTALLATION']) if folder == 'RootFolder' else \
            os.path.expandvars(self.bot.config[server.installation]['DCS_HOME'])
        if not os.path.exists(os.path.join(ovgme_path, 'install.log')):
            return False
        with open(os.path.join(ovgme_path, 'install.log')) as log:
            lines = log.readlines()
            # delete has to run reverse to clean the directories
            for i in range(len(lines) - 1, 0, -1):
                filename = lines[i][2:].strip()
                file = os.path.normpath(os.path.join(target, filename))
                if lines[i].startswith('w'):
                    if os.path.isfile(file):
                        os.remove(file)
                    elif os.path.isdir(file):
                        with suppress(Exception):
                            os.removedirs(file)
                elif lines[i].startswith('x'):
                    shutil.copy2(os.path.join(ovgme_path, filename), file)
        shutil.rmtree(ovgme_path)
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('DELETE FROM ovgme_packages WHERE server_name = %s AND folder = %s AND package_name = '
                               '%s AND version = %s', (server.name, folder, package_name, version))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
        return True

    def format_packages(self, data, marker, marker_emoji):
        embed = discord.Embed(title="List of installed Packages", color=discord.Color.blue())
        ids = packages = versions = ''
        flag = False
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            packages += data[i][1] + '\n'
            versions += data[i][2]
            latest = self.get_latest_version(data[i][0], data[i][1])
            if latest != data[i][2]:
                flag = True
                versions += ' ' + marker_emoji + '\n'
            else:
                versions += '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Package', value=packages)
        embed.add_field(name='Version', value=versions)
        if flag:
            footer = 'Press a number to update or uninstall.\n' + marker_emoji + ' update available'
        else:
            footer = 'Press a number to uninstall.'
        embed.set_footer(text=footer)
        return embed

    def get_installed_packages(self, server: Server, folder: str) -> list[Tuple[str, str]]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT * FROM ovgme_packages WHERE server_name = %s AND folder = %s',
                               (server.name, folder))
                return [(x['package_name'], x['version']) for x in cursor.fetchall()]
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Display installed packages')
    @utils.has_roles(['Admin', 'DCS Admin'])
    @commands.guild_only()
    async def packages(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        packages = []
        for folder in OVGME_FOLDERS:
            packages.extend([(folder, x, y) for x, y in self.get_installed_packages(server, folder)])
        if len(packages) > 0:
            n = await utils.selection_list(self, ctx, packages, self.format_packages, 5, -1, '🆕')
            if n == -1:
                return
            latest = self.get_latest_version(packages[n][0], packages[n][1])
            if latest != packages[n][2] and \
                    await utils.yn_question(self, ctx, f"Would you like to update package {packages[n][1]}?"):
                msg = await ctx.send('Updating ...')
                try:
                    if not self.uninstall_package(server, packages[n][0], packages[n][1], packages[n][2]):
                        await ctx.send(f"Package {packages[n][1]}_v{packages[n][2]} could not be uninstalled!")
                        return
                    elif not self.install_package(server, packages[n][0], packages[n][1], latest):
                        await ctx.send(f"Package {packages[n][1]}_v{latest} could not be installed!")
                        return
                    await ctx.send(f"Package {packages[n][1]} updated from version v{packages[n][2]} to v{latest}.")
                    return
                finally:
                    await msg.delete()
            elif await utils.yn_question(self, ctx, f"Would you like to uninstall package {packages[n][1]}?"):
                msg = await ctx.send('Uninstalling ...')
                try:
                    if self.uninstall_package(server, packages[n][0], packages[n][1], packages[n][2]):
                        await ctx.send(f"Package {packages[n][1]} uninstalled.")
                    else:
                        await ctx.send(f"Package {packages[n][1]} could not be uninstalled.")
                finally:
                    await msg.delete()
        else:
            await ctx.send(f"No packages installed on {server.name}.")

    @staticmethod
    def format_folders(data, marker, marker_emoji):
        embed = discord.Embed(title='Select a Folder', color=discord.Color.blue())
        ids = folders = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            folders += data[i] + '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Folder', value=folders)
        return embed

    @staticmethod
    def format_files(data, marker, marker_emoji):
        embed = discord.Embed(title='Available Plugins', color=discord.Color.blue())
        ids = files = versions = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            files += data[i][0] + '\n'
            versions += data[i][1] + '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='File', value=files)
        embed.add_field(name='Version', value=versions)
        return embed

    @commands.command(description='Install an OvGME package')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def add_package(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        config = self.get_config(server)
        if not config:
            await ctx.send(f"No plugin configuration found for server {server.name}.")
            return
        n = await utils.selection_list(self, ctx, OVGME_FOLDERS, self.format_folders)
        if n == -1:
            return
        folder = OVGME_FOLDERS[n]
        path = os.path.expandvars(config[folder])
        available = [self.parse_filename(x) for x in os.listdir(path) if not x.startswith('.')] or []
        installed = self.get_installed_packages(server, folder) or []
        files = list(set(available) - set(installed))
        if not len(files):
            await ctx.send(f"No available packages in folder {folder}.")
            return
        n = await utils.selection_list(self, ctx, files, self.format_files)
        if n == -1:
            return
        msg = await ctx.send('Installing ...')
        try:
            if self.install_package(server, folder, files[n][0], files[n][1]):
                await ctx.send(f"Package {files[n][0]} installed.")
            else:
                await ctx.send(f"Package {files[n][0]} could not be installed.")
        finally:
            await msg.delete()


def setup(bot: DCSServerBot):
    bot.add_cog(OvGME(bot))
