from dataclasses import dataclass
from typing import Any

from core.data.node import Node


@dataclass
class NodeProxy(Node):
    local_node: Any
    name: str

    def __post_init__(self):
        self.pool = self.local_node.pool
        self.log = self.local_node.log

    @property
    def master(self) -> bool:
        return self.local_node.master

    @master.setter
    def master(self, value: bool):
        raise NotImplemented()

    @property
    def public_ip(self) -> str:
        raise NotImplemented()

    @property
    def installation(self) -> str:
        raise NotImplemented()

    @property
    def extensions(self) -> dict:
        raise NotImplemented()
