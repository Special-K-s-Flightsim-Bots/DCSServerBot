from pathlib import Path
from typing import Any

import os
import yaml

from core.data.node import Node
from core.data.proxy.instanceproxy import InstanceProxy


class NodeProxy(Node):
    def __init__(self, local_node: Any, name: str, public_ip: str):
        super().__init__(name)
        self.local_node = local_node
        self.pool = self.local_node.pool
        self.log = self.local_node.log
        self.locals = self.read_locals()
        self._public_ip = public_ip

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
