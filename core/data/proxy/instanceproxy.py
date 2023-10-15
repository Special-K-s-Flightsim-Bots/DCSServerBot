from __future__ import annotations
from core import Instance
from dataclasses import field, dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import ServerProxy

__all__ = ["InstanceProxy"]


@dataclass
class InstanceProxy(Instance):
    _server: Optional[ServerProxy] = field(compare=False, repr=False, default=None, init=False)

    @property
    def server(self) -> Optional[ServerProxy]:
        return self._server

    @server.setter
    def server(self, server: Optional[ServerProxy]):
        self._server = server
