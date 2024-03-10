import asyncio

from core.services.base import Service
from typing import Type, Optional, TypeVar, Callable, Union

__all__ = ["ServiceRegistry"]

T = TypeVar("T", bound=Service)


class ServiceRegistry:
    _instance: Optional["ServiceRegistry"] = None
    _node: Optional["NodeImpl"] = None
    _registry: dict[Type[T], Type[T]] = {}
    _master_only: set[Type[T]] = set()
    _plugins: dict[Type[T], str] = {}
    _singletons: dict[Type[T], T] = {}

    def __new__(cls, node: "NodeImpl") -> "ServiceRegistry":
        if cls._instance is None:
            cls._instance = super(ServiceRegistry, cls).__new__(cls)
            cls._node = node
        return cls._instance

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()

    @classmethod
    def register(cls, *, t: Optional[Type[T]] = None, master_only: Optional[bool] = False,
                 plugin: Optional[str] = None) -> Callable[[Type[T]], Type[T]]:
        def inner_wrapper(wrapped_class: Type[T]) -> Type[T]:
            cls._registry[t or wrapped_class] = wrapped_class
            if master_only:
                cls._master_only.add(t or wrapped_class)
            if plugin:
                cls._plugins[t or wrapped_class] = plugin
            return wrapped_class

        return inner_wrapper

    @classmethod
    def new(cls, t: Type[T], *args, **kwargs) -> T:
        instance = cls.get(t)
        if not instance:
            # noinspection PyArgumentList
            instance = cls._registry[t](node=cls._node, *args, **kwargs)
            cls._singletons[t] = instance
        return instance

    @classmethod
    def get(cls, t: Union[str, Type[T]]) -> Optional[T]:
        if isinstance(t, str):
            for key, value in cls._singletons.items():
                if key.__name__ == t:
                    return value
            return None
        else:
            return cls._singletons.get(t)

    @classmethod
    def can_run(cls, t: Type[T]) -> bool:
        # check master only
        if cls.master_only(t) and not cls._node.master:
            return False
        # check plugin dependencies
        plugin = cls._plugins.get(t)
        if plugin and plugin not in cls._node.plugins:
            return False
        return True

    @classmethod
    def master_only(cls, t: Type[T]) -> bool:
        return t in cls._master_only

    @classmethod
    def services(cls) -> dict[Type[T], Type[T]]:
        return cls._registry

    @classmethod
    async def run(cls):
        await asyncio.gather(*[service.start() for service in cls._singletons.values()])

    @classmethod
    async def shutdown(cls):
        for _, service in cls._singletons.items():
            await service.stop()
        cls._singletons.clear()
