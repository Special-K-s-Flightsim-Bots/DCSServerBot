from __future__ import annotations

import logging

from configparser import ConfigParser
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Type, Optional, TypeVar

if TYPE_CHECKING:
    from core import Node
    from logging import Logger
    from psycopg_pool import ConnectionPool, AsyncConnectionPool

__all__ = [
    "DataObject",
    "DataObjectFactory"
]


@dataclass
class DataObject:
    name: str
    node: Node = field(compare=False, repr=False)
    pool: ConnectionPool = field(compare=False, repr=False, init=False)
    apool: AsyncConnectionPool = field(compare=False, repr=False, init=False)
    log: Logger = field(compare=False, repr=False, init=False)
    config: ConfigParser = field(compare=False, repr=False, init=False)
    is_remote: bool = field(compare=False, repr=False, init=False)

    def __post_init__(self):
        self.pool = self.node.pool
        self.apool = self.node.apool
        self.log = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
        self.config = self.node.config


T = TypeVar("T", bound=DataObject)


class DataObjectFactory:
    _instance: Optional[DataObjectFactory] = None
    _registry: dict[Type[T], Type[T]] = {}

    def __new__(cls) -> DataObjectFactory:
        if cls._instance is None:
            cls._instance = super(DataObjectFactory, cls).__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, t: Optional[Type[T]] = None) -> Callable[[Type[T]], Type[T]]:
        def inner_wrapper(wrapped_class: Type[T]) -> Type[T]:
            cls._registry[t or wrapped_class] = wrapped_class
            return wrapped_class

        return inner_wrapper

    @classmethod
    def new(cls, t: Type[T], **kwargs) -> T:
        # noinspection PyArgumentList
        return cls._registry[t](**kwargs)
