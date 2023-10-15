import asyncio
from core.services.base import Service
from typing import Callable, Any

__all__ = ["ServiceRegistry"]


class ServiceRegistry:
    _instance = None
    _node = None
    _registry: dict[str, Service] = dict[str, Service]()
    _master_only: set[str] = set[str]()
    _plugins: dict[str, str] = dict[str, str]()
    _singletons: dict[str, Service] = dict[str, Service]()

    def __new__(cls, node):
        if cls._instance is None:
            cls._instance = super(ServiceRegistry, cls).__new__(cls)
            cls._node = node
        return cls._instance

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()

    @classmethod
    def register(cls, name: str, master_only: bool = False, plugin: str = None) -> Callable:
        def inner_wrapper(wrapped_class: Any) -> Callable:
            cls._registry[name] = wrapped_class
            if master_only:
                cls._master_only.add(name)
            if plugin:
                cls._plugins[name] = plugin
            return wrapped_class

        return inner_wrapper

    @classmethod
    def new(cls, name: str, *args, **kwargs) -> Service:
        instance = cls.get(name)
        if not instance:
            instance = cls._registry[name](node=cls._node, name=name, *args, **kwargs)
            cls._singletons[name] = instance
        return instance

    @classmethod
    def get(cls, name: str) -> Service:
        return cls._singletons.get(name)

    @classmethod
    def can_run(cls, name: str) -> bool:
        # check master only
        if cls.master_only(name) and not cls._node.master:
            return False
        # check plugin dependencies
        plugin = cls._plugins.get(name)
        if plugin and plugin not in cls._node.plugins:
            return False
        return True

    @classmethod
    def master_only(cls, name: str) -> bool:
        return name in cls._master_only

    @classmethod
    def services(cls) -> dict[str, Service]:
        return cls._registry

    @classmethod
    async def run(cls):
        await asyncio.gather(*[service.start() for service in cls._singletons.values()])

    @classmethod
    async def shutdown(cls):
        for _, service in cls._singletons.items():
            await service.stop()
        cls._singletons.clear()
