import discord
import os
import psycopg2
import re
import shutil
import zipfile
from contextlib import closing, suppress
from core import Status, Plugin, DCSServerBot, PluginConfigurationError, utils
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

    async def after_upgrade(self):
        self.install_packages()

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

    def install_packages(self):
        if not self.locals or 'configs' not in self.locals:
            return
        for server_name, server in self.globals.items():
            config = self.get_config(server)
            if 'packages' not in config:
                return
            for package in config['packages']:
                version = package['version'] if package['version'] != 'latest' \
                    else self.get_latest_version(server, package['source'], package['name'])
                installed = self.check_package(server, package['source'], package['name'])
                if (not installed or installed != version) and server['status'] != Status.SHUTDOWN:
                    self.log.warning(f"  - Server {server['server_name']} needs to be shutdown to install packages.")
                    return
                maintenance = server['maintenance'] if 'maintenance' in server else None
                server['maintenance'] = True
                if not installed:
                    if self.install_package(server, package['source'], package['name'], version):
                        self.log.info(f"  - Package {package['name']}_v{version} installed.")
                    else:
                        self.log.warning(f"  - Package {package['name']}_v{version} not found!")
                elif installed != version:
                    if not self.uninstall_package(server, package['source'], package['name'], installed):
                        self.log.warning(f"  - Package {package['name']}_v{installed} could not be uninstalled!")
                    elif not self.install_package(server, package['source'], package['name'], version):
                        self.log.warning(f"  - Package {package['name']}_v{version} could not be installed!")
                    else:
                        self.log.info(f"  - Package {package['name']}_v{installed} updated to v{version}.")
                if maintenance:
                    server['maintenance'] = maintenance
                else:
                    del server['maintenance']

    def get_latest_version(self, server: dict, folder: str, package: str) -> str:
        def is_greater(v1: str, v2: str):
            parts1 = [int(x) for x in v1.split('.')]
            parts2 = [int(x) for x in v2.split('.')]
            for i in range(0, max(len(parts1), len(parts2))):
                if parts1[i] > parts2[i]:
                    return True
            return False

        config = self.get_config(server)
        path = os.path.expandvars(config[folder])
        available = [self.parse_filename(x) for x in os.listdir(path) if package in x]
        max_version = None
        for _, version in available:
            if not max_version or is_greater(version, max_version):
                max_version = version
        return max_version

    def check_package(self, server: dict, folder: str, package_name: str) -> Optional[str]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT version FROM ovgme_packages WHERE server_name = %s AND package_name = %s AND '
                               'folder = %s', (server['server_name'], package_name, folder))
                return cursor.fetchone()[0] if cursor.rowcount == 1 else None
        finally:
            self.pool.putconn(conn)

    def install_package(self, server: dict, folder: str, package_name: str, version: str, backup: Optional[bool] = True) -> bool:
        config = self.get_config(server)
        path = os.path.expandvars(config[folder])
        os.makedirs(os.path.join(path, '.' + server['installation']), exist_ok=True)
        target = os.path.expandvars(self.config['DCS']['DCS_INSTALLATION']) if folder == 'RootFolder' else \
            os.path.expandvars(self.config[server['installation']]['DCS_HOME'])
        for file in os.listdir(path):
            filename = os.path.join(path, file)
            if (os.path.isfile(filename) and file == package_name + '_v' + version + '.zip') or \
                    (os.path.isdir(filename) and file == package_name + '_v' + version):
                ovgme_path = os.path.join(path, '.' + server['installation'], package_name + '_v' + version)
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
                                       'VALUES (%s, %s, %s, %s)', (server['server_name'], package_name, version,
                                                                   folder))
                    conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)
                return True
        return False

    def uninstall_package(self, server: dict, folder: str, package_name: str, version: str) -> bool:
        config = self.get_config(server)
        path = os.path.expandvars(config[folder])
        ovgme_path = os.path.join(path, '.' + server['installation'], package_name + '_v' + version)
        target = os.path.expandvars(self.config['DCS']['DCS_INSTALLATION']) if folder == 'RootFolder' else \
            os.path.expandvars(self.config[server['installation']]['DCS_HOME'])
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
                               '%s AND version = %s', (server['server_name'], folder, package_name, version))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
        return True

    @staticmethod
    def format_packages(rows):
        embed = discord.Embed(title="List of installed Packages", color=discord.Color.blue())
        embed.add_field(name='Folder', value='\n'.join(x[0] for x in rows))
        embed.add_field(name='Package', value='\n'.join(x[1] for x in rows))
        embed.add_field(name='Version', value='\n'.join(x[2] for x in rows))
        return embed

    def get_installed_packages(self, server: dict, folder: str) -> list[Tuple[str, str]]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT * FROM ovgme_packages WHERE server_name = %s AND folder = %s',
                               (server['server_name'], folder))
                return [(x['package_name'], x['version']) for x in cursor.fetchall()]
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Display installed packages')
    @utils.has_roles(['Admin', 'DCS Admin'])
    @commands.guild_only()
    async def packages(self, ctx):
        server = await utils.get_server(self, ctx)
        if not server:
            return
        packages = []
        for folder in OVGME_FOLDERS:
            packages.extend([(folder, x, y) for x, y in self.get_installed_packages(server, folder)])
        if len(packages) > 0:
            await utils.pagination(self, ctx, packages, self.format_packages, 20)
        else:
            await ctx.send(f"No packages installed on {server['server_name']}.")

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
        server = await utils.get_server(self, ctx)
        if not server:
            return
        n = await utils.selection_list(self, ctx, OVGME_FOLDERS, self.format_folders)
        if n == -1:
            return
        config = self.get_config(server)
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
        if self.install_package(server, folder, files[n][0], files[n][1]):
            await ctx.send('Package installed.')
        else:
            await ctx.send('Package could not be installed.')

    @commands.command(description='Uninstall an OvGME package')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def remove_package(self, ctx):
        server = await utils.get_server(self, ctx)
        if not server:
            return
        n = await utils.selection_list(self, ctx, OVGME_FOLDERS, self.format_folders)
        if n == -1:
            return
        folder = OVGME_FOLDERS[n]
        installed = self.get_installed_packages(server, folder)
        n = await utils.selection_list(self, ctx, installed, self.format_files)
        if n == -1:
            return
        if self.uninstall_package(server, folder, installed[n][0], installed[n][1]):
            await ctx.send(f"Package {installed[n][0]} uninstalled.")
        else:
            await ctx.send(f"Package {installed[n][0]} could not be uninstalled.")


def setup(bot: DCSServerBot):
    bot.add_cog(OvGME(bot))
