import os
import yaml

from core import ServiceRegistry
from core.data.node import Node
from core.data.proxy.instanceproxy import InstanceProxy
from pathlib import Path
from typing import Any, Union, Optional, Tuple


class NodeProxy(Node):
    def __init__(self, local_node: Any, name: str, public_ip: str):
        super().__init__(name)
        self.local_node = local_node
        self.pool = self.local_node.pool
        self.log = self.local_node.log
        self._public_ip = public_ip
        self.locals = self.read_locals()
        self.bus = ServiceRegistry.get("ServiceBus")

    @property
    def master(self) -> bool:
        return self.local_node.master

    @master.setter
    def master(self, value: bool):
        raise NotImplemented()

    @property
    def public_ip(self) -> str:
        return self._public_ip

    @public_ip.setter
    def public_ip(self, public_ip: str):
        self._public_ip = public_ip

    @property
    def installation(self) -> str:
        raise NotImplemented()

    @property
    def extensions(self) -> dict:
        raise NotImplemented()

    def read_locals(self) -> dict:
        _locals = dict()
        if os.path.exists('config/nodes.yaml'):
            node: dict = yaml.safe_load(Path('config/nodes.yaml').read_text(encoding='utf-8'))[self.name]
            for name, element in node.items():
                if name == 'instances':
                    for _name, _element in node['instances'].items():
                        instance = InstanceProxy(self.local_node, _name)
                        instance.locals = _element
                        self.instances.append(instance)
                else:
                    _locals[name] = element
        return _locals

    async def upgrade(self) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "upgrade"
        }, node=self.name)

    async def update(self, warn_times: list[int]):
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "update",
            "params": {
                "warn_times": warn_times
            }
        }, node=self.name)

    async def get_dcs_branch_and_version(self) -> Tuple[str, str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_dcs_branch_and_version"
        }, node=self.name)
        return data['return'][0], data['return'][1]

    async def handle_module(self, what: str, module: str) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "handle_module"
        }, node=self.name)

    async def get_installed_modules(self) -> set[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_installed_modules"
        }, node=self.name)
        return data['return']

    async def get_available_modules(self, userid: Optional[str] = None, password: Optional[str] = None) -> set[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_available_modules"
        }, timeout=60, node=self.name)
        return set(data['return'])

    async def read_file(self, path: str) -> Union[bytes, int]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "read_file",
            "params": {
                "path": path
            }
        }, timeout=60, node=self.name)
        with self.pool.connection() as conn:
            with conn.transaction():
                file = conn.execute("SELECT data FROM files WHERE id = %s", (data['return'], ),
                                    binary=True).fetchone()[0]
                conn.execute("DELETE FROM files WHERE id = %s", (data['return'], ))
        return file

    async def list_directory(self, path: str, pattern: str) -> list[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "list_directory",
            "params": {
                "path": path,
                "pattern": pattern
            }
        }, node=self.name)
        return data['return']