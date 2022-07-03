from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    import psycopg2.pool
    from core import DCSServerBot
    from logging import Logger


@dataclass
class DataObject:
    bot: DCSServerBot = field(compare=False, repr=False)
    pool: psycopg2.pool.ThreadedConnectionPool = field(compare=False, repr=False, init=False)
    log: Logger = field(compare=False, repr=False, init=False)

    def __post_init__(self):
        self.pool = self.bot.pool
        self.log = self.bot.log


class DataObjectFactory:
    _instance = None
    _registry = dict[str, Any]()

    def __new__(cls) -> DataObjectFactory:
        if cls._instance is None:
            cls._instance = super(DataObjectFactory, cls).__new__(cls)
            cls._methods = dict[str, DataObject]()
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
