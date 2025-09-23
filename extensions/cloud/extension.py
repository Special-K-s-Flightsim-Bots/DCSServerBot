import aiohttp
import asyncio
import certifi
import os
import shutil
import ssl

from core import Extension, Server, DEFAULT_TAG
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Optional, Any

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML(typ='safe')


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
        config_file = os.path.join(self.node.config_dir, 'plugins', 'cloud.yaml')
        if not os.path.exists(config_file):
            self.log.info('No cloud.yaml found, copying the sample.')
            shutil.copyfile('samples/plugins/cloud.yaml', config_file)
        return yaml.load(Path(config_file).read_text(encoding='utf-8'))

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
            async with self.session.post(
                    url,
                    json=element,
                    proxy=self.node.proxy,
                    proxy_auth=self.node.proxy_auth,
                    raise_for_status=False,
                    timeout=aiohttp.ClientTimeout(total=30)  # Add reasonable timeout
            ) as response:
                if response.status > 299:
                    body = await response.text()
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=(),
                        status=response.status,
                        message=f"{HTTPStatus(response.status).phrase}: {body}",
                        headers=response.headers
                    )
                return await response.json()

        try:
            if isinstance(data, list):
                tasks = [send(line) for line in data]
                return await asyncio.gather(*tasks, return_exceptions=True)
            else:
                return await send(data)
        except asyncio.TimeoutError:
            raise aiohttp.ClientError("Request timed out")

    async def cloud_register(self):
        # we do not send cloud updates if we are not allowed and for non-public servers
        if (not self.server.current_mission or not self.config.get('register', True) or
                not self.server.settings.get('isPublic', True)):
            return
        payload = {}
        try:
            payload = {
                "guild_id": self.node.guild_id,
                "server_name": self.server.name,
                "port": self.server.instance.dcs_port,
                "password": (self.server.settings.get('password', '') != ''),
                "theatre": self.server.current_mission.map,
                "dcs_version": self.node.dcs_version,
                "num_players": len(self.server.get_active_players()) + 1,
                "max_players": int(self.server.settings.get('maxPlayers', 16)),
                "mission": self.server.current_mission.name,
                "date": self.server.current_mission.date.strftime("%Y-%m-%d") if isinstance(self.server.current_mission.date, datetime) else self.server.current_mission.date,
                "start_time": int(self.server.current_mission.start_time),
                "time_in_mission": int(self.server.current_mission.mission_time),
                "time_to_restart": int((self.server.restart_time - datetime.now(tz=timezone.utc)).total_seconds()) if self.server.restart_time else -1,
            }
            # noinspection PyUnresolvedReferences
            await self.post('register_server', payload)
            self.log.debug(f"Server {self.server.name} registered with the cloud.")
        except aiohttp.ClientError:
            self.log.warning(f"Could not register server {self.server.name} with the cloud.")
            self.log.debug(payload)

    async def cloud_unregister(self):
        payload = {}
        try:
            payload = {
                "guild_id": self.node.guild_id,
                "server_name": self.server.name
            }
            # noinspection PyUnresolvedReferences
            await self.post('unregister_server', payload)
            self.log.debug(f"Server {self.server.name} unregistered from the cloud.")
        except aiohttp.ClientError as ex:
            self.log.warning(f"Could not unregister server {self.server.name} from the cloud.", exc_info=ex)
            self.log.debug(payload)

    async def startup(self) -> bool:
        self.loop.create_task(self.cloud_register())
        return await super().startup()

    def shutdown(self) -> bool:
        self.loop.create_task(self.cloud_unregister())
        return super().shutdown()
