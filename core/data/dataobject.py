from __future__ import annotations
from configparser import ConfigParser
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from logging import Logger
    from psycopg_pool import ConnectionPool

__all__ = [
    "DataObject",
    "DataObjectFactory"
]


@dataclass
class DataObject:
    node: Any = field(compare=False, repr=False)
    pool: ConnectionPool = field(compare=False, repr=False, init=False)
    log: Logger = field(compare=False, repr=False, init=False)
    config: ConfigParser = field(compare=False, repr=False, init=False)

    def __post_init__(self):
        self.pool = self.node.pool
        self.log = self.node.log
        self.config = self.node.config


class DataObjectFactory:
    _instance = None
    _registry = dict[str, DataObject]()

    def __new__(cls) -> DataObjectFactory:
        if cls._instance is None:
            cls._instance = super(DataObjectFactory, cls).__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, name: str) -> Callable:
        def inner_wrapper(wrapped_class: Any) -> Callable:
            cls._registry[name] = wrapped_class
            return wrapped_class

        return inner_wrapper

    @classmethod
    def new(cls, class_name: str, **kwargs) -> Any:
        return cls._registry[class_name](**kwargs)
