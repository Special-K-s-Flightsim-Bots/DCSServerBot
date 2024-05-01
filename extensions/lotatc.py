import json
import luadata
import os

from core import Extension, utils, Server, ServiceRegistry
from services import ServiceBus
from typing import Optional
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

ports: dict[int, str] = dict()


class LotAtc(Extension, FileSystemEventHandler):
    def __init__(self, server: Server, config: dict):
        self.home = os.path.join(server.instance.home, 'Mods', 'Services', 'LotAtc')
        super().__init__(server, config)
        self.observer: Optional[Observer] = None
        self.bus = ServiceRegistry.get(ServiceBus)
        self.gcis = {
            "blue": [],
            "red": []
        }

    def load_config(self) -> Optional[dict]:
        cfg = {}
        for path in [os.path.join(self.home, 'config.lua'), os.path.join(self.home, 'config.custom.lua')]:
            try:
                with open(path, mode='r', encoding='utf-8') as file:
                    content = file.read()
                content = content.replace('lotatc_inst.options', 'cfg')
                cfg |= luadata.unserialize(content)
            except FileNotFoundError:
                pass
        return cfg

    async def prepare(self) -> bool:
        global ports

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
        extension = self.server.extensions.get('SRS')
        if extension:
            # set SRS config
            config['srs_path'] = extension.get_inst_path()
            config['srs_server'] = '127.0.0.1'
            srs_port = extension.config.get('port', extension.locals['Server Settings']['SERVER_PORT'])
            config['srs_server_port'] = srs_port

        if len(config):
            self.locals = self.locals | config
            path = os.path.join(self.home, 'config.custom.lua')
            with open(path, mode='wb') as outfile:
                outfile.write((f"lotatc_inst.options = " + luadata.serialize(self.locals, indent='\t',
                                                                             indent_level=0)).encode('utf-8'))
            self.log.debug(f"  => New {path} written.")
        port = self.locals.get('port', 10310)
        if port in ports and ports[port] != self.server.name:
            self.log.error(f"  => {self.server.name}: {self.name} port {port} already in use by server {ports[port]}!")
            return False
        else:
            ports[port] = self.server.name
        return await super().prepare()

    # File Event Handlers
    def process_stats_file(self, path: str):
        try:
            with open(path, mode='r', encoding='utf-8') as stats:
                stats = json.load(stats)
                gcis = {
                    "blue": [],
                    "red": []
                }
                for coalition in ['blue', 'red']:
                    gcis[coalition] = [x['name'] for x in stats.get('clients', {}).get(coalition, [])]
                    for gci in set(self.gcis[coalition]) - set(gcis[coalition]):
                        self.bus.send_to_node({
                            "command": "onGCILeave",
                            "server_name": self.server.name,
                            "coalition": coalition,
                            "name": gci
                        })
                    for gci in set(gcis[coalition]) - set(self.gcis[coalition]):
                        self.bus.send_to_node({
                            "command": "onGCIJoin",
                            "server_name": self.server.name,
                            "coalition": coalition,
                            "name": gci
                        })
                self.gcis = gcis
        except Exception:
            pass

    def on_moved(self, event: FileSystemEvent):
        self.process_stats_file(event.dest_path)

    @property
    def version(self) -> str:
        return utils.get_windows_version(os.path.join(self.home, r'bin', 'lotatc.dll'))

    async def render(self, param: Optional[dict] = None) -> dict:
        if self.locals:
            host = self.config.get('host', self.node.public_ip)
            value = f"{host}:{self.locals.get('port', 10310)}"
            show_passwords = self.config.get('show_passwords', True)
            blue = self.locals.get('blue_password', '')
            red = self.locals.get('red_password', '')
            if show_passwords and (blue or red):
                value += f"\n🔹 Pass: {blue}\n🔸 Pass: {red}"
            return {
                "name": "LotAtc",
                "version": self.version,
                "value": value
            }
        else:
            return {}

    def is_installed(self) -> bool:
        if not self.config.get('enabled', True):
            return False
        if (not os.path.exists(os.path.join(self.home, 'bin', 'lotatc.dll')) or
                not os.path.exists(os.path.join(self.home, 'config.lua'))):
            self.log.error(f"  => {self.server.name}: Can't load extension, LotAtc not correctly installed.")
            return False
        return True

    async def startup(self) -> bool:
        await super().startup()
        path = os.path.join(self.home, 'stats.json')
        if os.path.exists(path):
            self.process_stats_file(path)
        self.observer = Observer()
        self.observer.schedule(self, path=self.home)
        self.observer.start()
        return True

    def shutdown(self) -> bool:
        super().shutdown()
        self.observer.stop()
        self.observer.join()
        self.observer = None
        return True

    def is_running(self) -> bool:
        return self.observer is not None
