from __future__ import annotations

import logging

from abc import ABC
from configparser import ConfigParser
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Type, TypeVar, Generic, ClassVar, Any

if TYPE_CHECKING:
    from core import Node
    from logging import Logger
    from psycopg_pool import ConnectionPool, AsyncConnectionPool

__all__ = [
    "DataObject",
    "DataObjectFactory"
]


@dataclass
class DataObject(ABC):
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

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if isinstance(other, DataObject):
            return self.name == other.name
        return False


T = TypeVar("T", bound=DataObject)


class DataObjectFactory(Generic[T]):
    """
    A singleton factory for creating data objects with registration support.

    This class provides a central registry for data object types and a factory
    method for instantiating them. It uses a singleton pattern to ensure a single
    instance is shared across the application.

    Type Parameters:
        T: The base type for objects created by this factory

    Attributes:
        _instance: The singleton instance of this class
        _registry: Dictionary mapping object types to their implementation classes
    """
    _instance: DataObjectFactory[T] | None = None
    # Using class variable storage that's independent of the generic type parameter
    _registry: ClassVar[dict[Any, Any]] = {}

    def __new__(cls) -> DataObjectFactory[T]:
        """
        Creates a singleton instance of the factory.

        Returns:
            The singleton instance of DataObjectFactory
        """
        if cls._instance is None:
            cls._instance = super(DataObjectFactory, cls).__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, t: Type[T] | None = None) -> Callable[[Type[T]], Type[T]]:
        """
        Decorator for registering implementation classes with the factory.

        Args:
            t: Optional type to use as the registration key. If not provided,
               the implementation class itself is used as the key.

        Returns:
            A decorator function that registers the decorated class

        Example:
            @DataObjectFactory.register(BaseClass)
            class Implementation(BaseClass):
                pass
        """

        def inner_wrapper(wrapped_class: Type[T]) -> Type[T]:
            DataObjectFactory._registry[t or wrapped_class] = wrapped_class
            return wrapped_class

        return inner_wrapper

    @classmethod
    def new(cls, t: Type[T], **kwargs) -> T:
        """
        Creates a new instance of the registered implementation class.

        Args:
            t: The type to instantiate (must be registered)
            **kwargs: Arguments to pass to the constructor

        Returns:
            A new instance of the requested type

        Raises:
            KeyError: If the requested type is not registered
        """
        # noinspection PyArgumentList
        return DataObjectFactory._registry[t](**kwargs)
