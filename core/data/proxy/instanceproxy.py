from __future__ import annotations
from core import Instance
from dataclasses import field, dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import ServerProxy


@dataclass
class InstanceProxy(Instance):
    _server: Optional[ServerProxy] = field(compare=False, repr=False, default=None, init=False)
