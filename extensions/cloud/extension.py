import aiohttp
import certifi
import os
import ssl

from aiohttp import BasicAuth
from core import Extension, Server, utils, DEFAULT_TAG
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


class Cloud(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.config = self.read_config()[DEFAULT_TAG]
        self._session = None
        self.client = None
        self.base_url = f"{self.config['protocol']}://{self.config['host']}:{self.config['port']}"

    def load_config(self) -> Optional[dict]:
        return yaml.load(Path(os.path.join(self.node.config_dir, 'services', 'bot.yaml')).read_text(encoding='utf-8'))

    def read_config(self):
        return yaml.load(Path(os.path.join(self.node.config_dir, 'plugins', 'cloud.yaml')).read_text(encoding='utf-8'))

    @property
    def proxy(self) -> Optional[str]:
        return self.locals.get('proxy', {}).get('url')

    @property
    def proxy_auth(self) -> Optional[BasicAuth]:
        username = self.locals.get('proxy', {}).get('username')
        try:
            password = utils.get_password('proxy', self.node.config_dir)
        except ValueError:
            return None
        if username and password:
            return BasicAuth(username, password)

    @property
    def session(self):
        if not self._session:
            headers = {
                "Content-type": "application/json"
            }
            if 'token' in self.config:
                headers['Authorization'] = f"Bearer {self.config['token']}"
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
                raise_for_status=True, headers=headers
            )
        return self._session

    async def post(self, request: str, data: Any) -> Any:
        async def send(element: dict):
            url = f"{self.base_url}/{request}/"
            async with self.session.post(url, json=element, proxy=self.proxy, proxy_auth=self.proxy_auth) as response:
                return await response.json()

        if isinstance(data, list):
            for line in data:
                await send(line)
        else:
            await send(data)

    async def cloud_register(self):
        # we do not send cloud updates if we are not allowed and for non-public servers
        if not self.config.get('register', True) or not self.server.settings['isPublic']:
            return
        try:
            # noinspection PyUnresolvedReferences
            await self.post('register_server', {
                "guild_id": self.node.guild_id,
                "server_name": self.server.name,
                "ipaddr": self.server.instance.dcs_host,
                "port": self.server.instance.dcs_port,
                "password": (self.server.settings['password'] != ""),
                "theatre": self.server.current_mission.map,
                "dcs_version": self.node.dcs_version,
                "num_players": len(self.server.get_active_players()) + 1,
                "max_players": int(self.server.settings.get('maxPlayers', 16)),
                "mission": self.server.current_mission.name,
                "date": self.server.current_mission.date.strftime("%Y-%m-%d") if isinstance(self.server.current_mission.date, datetime) else self.server.current_mission.date,
                "start_time": self.server.current_mission.start_time,
                "time_in_mission": int(self.server.current_mission.mission_time),
                "time_to_restart": int((self.server.restart_time - datetime.now(tz=timezone.utc)).total_seconds()) if self.server.restart_time else -1,
            })
            self.log.debug(f"Server {self.server.name} registered with the cloud.")
        except aiohttp.ClientError as ex:
            self.log.warning(f"Could not register server {self.server.name} with the cloud.")

    async def cloud_unregister(self):
        try:
            # noinspection PyUnresolvedReferences
            await self.post('unregister_server', {
                "guild_id": self.node.guild_id,
                "server_name": self.server.name,
            })
            self.log.debug(f"Server {self.server.name} unregistered from the cloud.")
        except aiohttp.ClientError as ex:
            self.log.warning(f"Could not unregister server {self.server.name} from the cloud.")

    async def startup(self) -> bool:
        await self.cloud_register()
        return await super().startup()

    def shutdown(self) -> bool:
        self.loop.create_task(self.cloud_unregister())
        return super().shutdown()
