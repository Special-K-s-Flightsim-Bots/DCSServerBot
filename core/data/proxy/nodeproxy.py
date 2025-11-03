from __future__ import annotations

import os

from core.data.node import Node, UploadStatus, SortOrder
from core.data.proxy.instanceproxy import InstanceProxy
from core.services.registry import ServiceRegistry
from core.utils import async_cache, cache_with_expiration
from pathlib import Path
from typing import TYPE_CHECKING
from typing_extensions import override

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

if TYPE_CHECKING:
    from core import Instance, Server
    from core import NodeImpl

__all__ = ["NodeProxy"]


class NodeProxy(Node):
    def __init__(self, local_node: NodeImpl, name: str, public_ip: str, dcs_version: str):
        from services.servicebus import ServiceBus

        super().__init__(name, local_node.config_dir)
        self.local_node = local_node
        self.pool = self.local_node.pool
        self.apool = self.local_node.apool
        self.log = self.local_node.log
        self._public_ip = public_ip
        self.locals = self.read_locals()
        self.bus = ServiceRegistry.get(ServiceBus)
        self.slow_system = self.locals.get('slow_system', False)
        self.dcs_version = dcs_version
        self.is_remote = True

    @override
    @property
    def master(self) -> bool:
        return False

    @override
    @master.setter
    def master(self, value: bool):
        raise NotImplementedError()

    @override
    @property
    def public_ip(self) -> str:
        return self._public_ip

    @public_ip.setter
    def public_ip(self, public_ip: str):
        self._public_ip = public_ip

    @override
    @property
    def installation(self) -> str:
        raise NotImplementedError()

    @override
    def read_locals(self) -> dict:
        _locals = dict()
        config_file = os.path.join(self.config_dir, 'nodes.yaml')
        if os.path.exists(config_file):
            node: dict = yaml.load(Path(config_file).read_text(encoding='utf-8')).get(self.name)
            if not node:
                self.log.warning(f'No configuration found for node "{self.name}" in {config_file}!')
                return {}
            for name, element in node.items():
                if name == 'instances':
                    for _name, _element in element.items():
                        self.instances.append(InstanceProxy(name=_name, node=self, locals=_element))
                else:
                    _locals[name] = element
        return _locals

    @override
    async def shutdown(self, rc: int = -2):
        await self.bus.send_to_node({
            "command": "rpc",
            "object": "Node",
            "method": "shutdown",
            "params": {
                "rc": rc
            }
        }, node=self.name)

    @override
    async def restart(self):
        await self.bus.send_to_node({
            "command": "rpc",
            "object": "Node",
            "method": "restart"
        }, node=self.name)

    @override
    async def upgrade_pending(self) -> bool:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "upgrade_pending"
        }, node=self.name, timeout=timeout)
        return data['return']

    @override
    async def upgrade(self):
        await self.bus.send_to_node({
            "command": "rpc",
            "object": "Node",
            "method": "upgrade"
        }, node=self.name)

    @override
    async def dcs_update(self, branch: str | None = None, version: str | None = None,
                         warn_times: list[int] = None, announce: bool | None = True):
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "update",
            "params": {
                "warn_times": warn_times,
                "branch": branch or "",
                "version": version or "",
                "announce": announce
            }
        }, node=self.name, timeout=600)
        return data['return']

    @override
    async def dcs_repair(self, warn_times: list[int] = None, slow: bool | None = False,
                         check_extra_files: bool | None = False):
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "dcs_repair",
            "params": {
                "warn_times": warn_times,
                "slow": slow,
                "check_extra_files": check_extra_files
            }
        }, node=self.name, timeout=600)
        return data['return']

    @override
    @cache_with_expiration(expiration=60)
    async def get_dcs_branch_and_version(self) -> tuple[str, str]:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_dcs_branch_and_version"
        }, node=self.name, timeout=timeout)
        return data['return'][0], data['return'][1]

    @override
    async def handle_module(self, what: str, module: str) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "handle_module",
            "params": {
                "what": what,
                "module": module
            }
        }, node=self.name, timeout=3600)

    @override
    @cache_with_expiration(expiration=60)
    async def get_installed_modules(self) -> list[str]:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_installed_modules"
        }, node=self.name, timeout=timeout)
        return data['return']

    @override
    @cache_with_expiration(expiration=60)
    async def get_available_modules(self) -> list[str]:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_available_modules"
        }, node=self.name, timeout=timeout)
        return data['return']

    @override
    @cache_with_expiration(expiration=60)
    async def get_available_dcs_versions(self, branch: str) -> list[str] | None:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_available_dcs_versions",
            "params": {
                "branch": branch
            }
        }, node=self.name, timeout=timeout)
        return data['return']


    @override
    @cache_with_expiration(expiration=60)
    async def get_latest_version(self, branch: str) -> str | None:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_latest_version",
            "params": {
                "branch": branch
            }
        }, node=self.name, timeout=timeout)
        return data['return']

    @override
    async def shell_command(self, cmd: str, timeout: int = 60) -> tuple[str, str] | None:
        _timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "shell_command",
            "params": {
                "cmd": cmd,
                "timeout": timeout
            }
        }, timeout=timeout + _timeout, node=self.name)
        return data['return']

    @override
    async def read_file(self, path: str) -> bytes | int:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "read_file",
            "params": {
                "path": path
            }
        }, timeout=timeout, node=self.name)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                cursor = await conn.execute("SELECT data FROM files WHERE id = %s", (data['return'], ), binary=True)
                file = (await cursor.fetchone())[0]
                await conn.execute("DELETE FROM files WHERE id = %s", (data['return'], ))
        return file

    @override
    async def write_file(self, filename: str, url: str, overwrite: bool = False) -> UploadStatus:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "write_file",
            "params": {
                "filename": filename,
                "url": url,
                "overwrite": overwrite
            }
        }, timeout=timeout, node=self.name)
        return UploadStatus(data["return"])

    @override
    @cache_with_expiration(expiration=60)
    async def list_directory(self, path: str, *, pattern: str | list[str] = '*',
                             order: SortOrder = SortOrder.DATE,
                             is_dir: bool = False, ignore: list[str] = None, traverse: bool = False
                             ) -> tuple[str, list[str]]:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "list_directory",
            "params": {
                "path": path,
                "pattern": pattern,
                "order": order.value,
                "is_dir": is_dir,
                "ignore": ignore,
                "traverse": traverse
            }
        }, node=self.name, timeout=timeout)
        return data['return']

    @override
    async def create_directory(self, path: str):
        timeout = 60 if not self.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "create_directory",
            "params": {
                "path": path
            }
        }, node=self.name, timeout=timeout)

    @override
    async def remove_file(self, path: str):
        timeout = 60 if not self.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "remove_file",
            "params": {
                "path": path
            }
        }, node=self.name, timeout=timeout)

    @override
    async def rename_file(self, old_name: str, new_name: str, *, force: bool | None = False):
        timeout = 60 if not self.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "rename_file",
            "params": {
                "old_name": old_name,
                "new_name": new_name,
                "force": force
            }
        }, node=self.name, timeout=timeout)

    @override
    async def rename_server(self, server: Server, new_name: str, update_settings: bool | None = False):
        timeout = 60 if not self.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "rename_server",
            "params": {
                "server": server.name,
                "new_name": new_name,
                "update_settings": update_settings
            }
        }, node=self.name, timeout=timeout)

    @override
    async def add_instance(self, name: str, *, template: str = "") -> Instance:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "add_instance",
            "params": {
                "name": name,
                "template": template
            }
        }, node=self.name, timeout=timeout)
        instance = next((x for x in self.instances if x.name == name), None)
        if not instance:
            instance = InstanceProxy(name=data['return'], node=self)
            self.instances.append(instance)
        return instance

    @override
    async def delete_instance(self, instance: Instance, remove_files: bool) -> None:
        timeout = 60 if not self.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "delete_instance",
            "params": {
                "instance": instance.name,
                "remove_files": remove_files
            }
        }, node=self.name, timeout=timeout)
        self.instances.remove(instance)

    @override
    async def rename_instance(self, instance: Instance, new_name: str) -> None:
        timeout = 60 if not self.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "rename_instance",
            "params": {
                "instance": instance.name,
                "new_name": new_name
            }
        }, node=self.name, timeout=timeout)

    @override
    @cache_with_expiration(expiration=60)
    async def find_all_instances(self) -> list[tuple[str, str]]:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "find_all_instances"
        }, node=self.name, timeout=timeout)
        return data['return']

    @override
    async def migrate_server(self, server: Server, instance: Instance):
        timeout = 180 if not self.slow_system else 300
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "migrate_server",
            "params": {
                "server": server.name,
                "instance": instance.name
            }
        }, node=self.name, timeout=timeout)

    @override
    async def unregister_server(self, server: Server) -> None:
        timeout = 60 if not self.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "unregister_server",
            "params": {
                "server": server.name
            }
        }, node=self.name, timeout=timeout)

    @override
    async def install_plugin(self, plugin: str) -> bool:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "install_plugin",
            "params": {
                "plugin": plugin
            }
        }, node=self.name, timeout=timeout)
        return data['return']

    @override
    async def uninstall_plugin(self, plugin: str) -> bool:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "uninstall_plugin",
            "params": {
                "plugin": plugin
            }
        }, node=self.name, timeout=timeout)
        return data['return']

    @override
    @async_cache
    async def get_cpu_info(self) -> bytes | int:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_cpu_info"
        }, timeout=timeout, node=self.name)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                cursor = await conn.execute("SELECT data FROM files WHERE id = %s", (data['return'], ), binary=True)
                image = (await cursor.fetchone())[0]
                await conn.execute("DELETE FROM files WHERE id = %s", (data['return'], ))
        return image

    @override
    async def info(self) -> dict:
        timeout = 60 if not self.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "info"
        }, timeout=timeout, node=self.name)
        return data['return']
