from __future__ import annotations

from core import Instance
from dataclasses import dataclass, field
from typing_extensions import override

__all__ = ["InstanceProxy"]


@dataclass
class InstanceProxy(Instance):
    _home: str = field(repr=False, init=False, default=None)

    @override
    def __post_init__(self):
        super().__post_init__()
        self.is_remote = True

    @override
    @property
    def home(self) -> str:
        return self._home

    @home.setter
    def home(self, home: str) -> None:
        self._home = home
