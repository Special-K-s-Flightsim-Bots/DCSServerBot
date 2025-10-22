import aiohttp
import asyncio
import atexit
import certifi
import ctypes
import discord
import json
import luadata
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import xml.etree.ElementTree as ET

from core import Extension, utils, Server, ServiceRegistry, get_translation, InstallException
from discord.ext import tasks
from extensions.srs import SRS
from packaging.version import parse
from services.bot import BotService
from services.servicebus import ServiceBus
from typing import cast
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

_ = get_translation(__name__.split('.')[1])

UPDATER_CODE = '4dctdtna'

__all__ = [
    "LotAtc"
]


class LotAtc(Extension, FileSystemEventHandler):
    _ports: dict[int, str] = dict()

    CONFIG_DICT = {
        "port": {
            "type": int,
            "label": _("LotAtc Port"),
            "placeholder": _("Unique port number for LotAtc"),
            "required": True,
            "default": 10310
        }
    }

    def __init__(self, server: Server, config: dict):
        self.home = os.path.join(server.instance.home, 'Mods', 'Services', 'LotAtc')
        super().__init__(server, config)
        # check version incompatibility
        if self.version and parse(self.version) >= parse('2.5.0') and sys.platform == 'win32':
            winver = platform.win32_ver()
            if winver[1] == '10.0.14393' and 'Server' in winver[3]:
                raise InstallException("LotAtc 2.5+ does not run on Windows Server 2016 anymore!")

        self.observer: Observer | None = None
        self.bus = ServiceRegistry.get(ServiceBus)
        self.gcis = {
            "blue": {},
            "red": {}
        }
        atexit.register(self.stop_observer)

    def load_config(self) -> dict | None:
        cfg = {}
        for path in [os.path.join(self.home, 'config.lua'), os.path.join(self.home, 'config.custom.lua')]:
            try:
                with open(path, mode='r', encoding='utf-8') as file:
                    content = file.read()
                content = content.replace('lotatc_inst.options', 'cfg')
                cfg |= luadata.unserialize(content)
            except FileNotFoundError:
                pass
            except Exception:
                self.log.error(f"Error while parsing {path}!", exc_info=True)
        return cfg

    def get_inst_path(self) -> str:
        inst_path = os.path.join(
            os.path.expandvars(self.config.get('installation', os.path.join('%ProgramFiles%', 'LotAtc'))))
        if os.path.exists(inst_path):
            return inst_path
        else:
            raise InstallException(f"Can't find the {self.name} installation dir, "
                                   "please specify it manually in your nodes.yaml!")

    async def prepare(self) -> bool:
        await self.update_instance(False)
        config = self.config.copy()
        if 'enabled' in config:
            del config['enabled']
        if 'show_passwords' in config:
            del config['show_passwords']
        if 'host' in config:
            del config['host']
        # create the default config
        config['dedicated_mode'] = True
        config['dump_json_stats'] = True
        extension = cast(SRS, self.server.extensions.get('SRS'))
        if extension:
            # set SRS config
            config['srs_path'] = extension.get_inst_path()
            config['srs_server'] = '127.0.0.1'
            srs_port = extension.config.get('port', extension.locals['Server Settings']['SERVER_PORT'])
            config['srs_server_port'] = srs_port
            srs_transponder_port = extension.config.get('lotatc_export_port',
                                                        extension.locals['General Settings'].get('LOTATC_EXPORT_PORT',
                                                                                                 10712))
            if srs_transponder_port:
                config['srs_use_transponder'] = True
                config['srs_transponder_port'] = srs_transponder_port

        if len(config):
            self.locals = self.locals | config
            path = os.path.join(self.home, 'config.custom.lua')
            with open(path, mode='wb') as outfile:
                outfile.write((f"lotatc_inst.options = " + luadata.serialize(self.locals, indent='\t',
                                                                             indent_level=0)).encode('utf-8'))
            self.log.debug(f"  => New {path} written.")
        port = self.locals.get('port', 10310)
        if type(self)._ports.get(port, self.server.name) != self.server.name:
            self.log.error(
                f"  => {self.server.name}: {self.name} port {port} already in use by server {type(self)._ports[port]}!")
            return False
        else:
            type(self)._ports[port] = self.server.name
        return await super().prepare()

    # File Event Handlers
    def process_stats_file(self, path: str):
        try:
            with open(path, mode='r', encoding='utf-8') as stats:
                stats = json.load(stats)
                gcis = {
                    "blue": {},
                    "red": {}
                }
                for coalition in ['blue', 'red']:
                    gcis[coalition] = {x['name']: x['ip'] for x in stats.get('clients', {}).get(coalition, [])}
                    added = {k: v for k, v in gcis[coalition].items() if k not in self.gcis[coalition]}
                    for name, ip in added.items():
                        match = re.search(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', ip)
                        asyncio.run_coroutine_threadsafe(self.bus.send_to_node({
                            "command": "onGCIJoin",
                            "server_name": self.server.name,
                            "coalition": coalition,
                            "name": name,
                            "ipaddr": match.group()
                        }), self.loop)
                    removed = {k: v for k, v in self.gcis[coalition].items() if k not in gcis[coalition]}
                    for name in removed.keys():
                        asyncio.run_coroutine_threadsafe(self.bus.send_to_node({
                            "command": "onGCILeave",
                            "server_name": self.server.name,
                            "coalition": coalition,
                            "name": name
                        }), self.loop)
                self.gcis = gcis
        except PermissionError:
            pass
        except Exception as ex:
            self.log.exception(ex)
            pass

    def on_moved(self, event: FileSystemEvent):
        self.process_stats_file(event.dest_path)

    @property
    def version(self) -> str:
        return utils.get_windows_version(os.path.join(self.home, r'bin', 'lotatc.dll'))

    async def render(self, param: dict | None = None) -> dict:
        if not self.locals:
            raise NotImplementedError()

        host = self.config.get('host', self.node.public_ip)
        value = f"{host}:{self.locals.get('port', 10310)}"
        show_passwords = self.config.get('show_passwords', True)
        blue = self.locals.get('blue_password', '')
        red = self.locals.get('red_password', '')
        if show_passwords and (blue or red):
            value += f"\n🔹 Pass: {blue}\n🔸 Pass: {red}"
        return {
            "name": self.name,
            "version": self.version,
            "value": value
        }

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        if (not os.path.exists(os.path.join(self.home, 'bin', 'lotatc.dll')) or
                not os.path.exists(os.path.join(self.home, 'config.lua'))):
            self.log.error(f"  => {self.server.name}: Can't load extension, LotAtc not correctly installed.")
            return False
        return True

    async def startup(self, *, quiet: bool = False) -> bool:
        path = os.path.join(self.home, 'stats.json')
        if os.path.exists(path):
            self.process_stats_file(path)
        self.observer = Observer()
        self.observer.schedule(self, path=self.home)
        self.observer.start()
        return await super().startup()

    def stop_observer(self):
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=10)
            self.observer = None

    def shutdown(self, *, quiet: bool = False) -> bool:
        super().shutdown()
        self.stop_observer()
        return True

    def is_running(self) -> bool:
        return self.observer is not None

    def get_inst_version(self) -> tuple[str, str]:
        path = os.path.join(self.get_inst_path(), 'server')
        versions = os.listdir(path)
        major_version = max(versions, key=parse)
        path = os.path.join(path, major_version, 'Mods', 'services', 'LotAtc', 'bin')
        version = utils.get_windows_version(os.path.join(path, 'LotAtc.dll'))
        return major_version, version

    async def check_for_updates(self) -> str | None:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
            async with session.get(f"https://tinyurl.com/{UPDATER_CODE}", proxy=self.node.proxy,
                                   proxy_auth=self.node.proxy_auth) as response:
                if response.status in [200, 302]:
                    root = ET.fromstring(await response.text(encoding='utf-8'))
                    for package in root.findall('.//PackageUpdate'):
                        name = package.find('Name')
                        if name is not None and name.text == 'com.lotatc.server.server23':
                            version = package.find('Version')
                            if version is not None:
                                _, inst_version = self.get_inst_version()
                                if version.text != inst_version:
                                    return version.text
        return None

    def do_update(self):
        cwd = self.get_inst_path()
        exe_path = os.path.join(cwd, 'LotAtc_updater.exe')
        args = ['-c', 'up']
        if sys.platform == 'win32':
            # noinspection PyUnresolvedReferences
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe_path, ' '.join(args), None, 1)
        else:
            subprocess.run([exe_path] + args, cwd=cwd, shell=False, stderr=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL)

    async def update_instance(self, force: bool) -> bool:
        major_version, version = self.get_inst_version()
        if version != self.version:
            if force or self.config.get('autoupdate', False):
                await self.uninstall()
                await self.install()
                return True
            else:
                self.log.info(f"  => {self.name}: Instance {self.server.instance.name} is running version "
                              f"{self.version}, where {version} is available!")
        return False

    async def install(self):
        major_version, _ = self.get_inst_version()
        from_path = os.path.join(self.get_inst_path(), 'server', major_version)
        shutil.copytree(from_path, self.server.instance.home, dirs_exist_ok=True)
        self.locals = self.load_config()
        self.log.info(f"  => {self.name} {self.version} installed into instance {self.server.instance.name}.")

    async def uninstall(self):
        major_version, _ = self.get_inst_version()
        version = self.version
        from_path = os.path.join(self.get_inst_path(), 'server', major_version)
        for root, dirs, files in os.walk(from_path, topdown=False):
            for name in files:
                file_x = os.path.join(root, name)
                file_y = file_x.replace(from_path, self.server.instance.home)
                if os.path.exists(file_y) and not utils.is_junction(file_y):
                    os.remove(file_y)
            for name in dirs:
                dir_x = os.path.join(root, name)
                dir_y = dir_x.replace(from_path, self.server.instance.home)
                if os.path.exists(dir_y) and not utils.is_junction(dir_y):
                    try:
                        os.rmdir(dir_y)  # only removes empty directories
                    except OSError:
                        pass  # directory not empty
        self.log.info(f"  => {self.name} {version} uninstalled from instance {self.server.instance.name}.")

    @tasks.loop(minutes=30)
    async def schedule(self):
        if not self.config.get('autoupdate', False):
            return
        try:
            version = await self.check_for_updates()
            if version:
                self.log.info(f"A new LotAtc update is available. Updating to version {version} ...")
                await asyncio.to_thread(self.do_update)
                self.log.info("LotAtc updated.")
                bus = ServiceRegistry.get(ServiceBus)
                await bus.send_to_node({
                    "command": "rpc",
                    "service": BotService.__name__,
                    "method": "audit",
                    "params": {
                        "message": f"{self.name} updated to version {version} on node {self.node.name}."
                    }
                })
                config = self.config.get('announce')
                if config:
                    servers = []
                    for instance in self.node.instances:
                        if (instance.locals.get('extensions', {}).get(self.name) and
                                instance.locals['extensions'][self.name].get('autoupdate', True)):
                            servers.append(instance.server.display_name)
                    embed = discord.Embed(
                        colour=discord.Colour.blue(),
                        title=config.get(
                            'title', 'LotAtc has been updated to version {}!').format(version),
                        url="https://www.lotatc.com/changelog.html#{}".format(version.replace('.', '')))
                    embed.set_thumbnail(url="https://www.lotatc.com/signature.png")
                    embed.description = config.get('description',
                                                   'The following servers will be updated on the next restart:')
                    embed.add_field(name=_('Server'),
                                    value='\n'.join([f'- {x}' for x in servers]), inline=False)
                    embed.set_footer(
                        text=config.get('footer', 'Please make sure you update your LotAtc client also!'))
                    params = {
                        "channel": config['channel'],
                        "embed": embed.to_dict()
                    }
                    if 'mention' in config:
                        params['mention'] = config['mention']
                    await bus.send_to_node({
                        "command": "rpc",
                        "service": BotService.__name__,
                        "method": "send_message",
                        "params": params
                    })
        except Exception as ex:
            self.log.error(f"LotAtc update failed: {ex}")

    async def get_ports(self) -> dict:
        return {
            "LotAtc": self.locals.get('port', 10310)
        } if self.enabled else {}
