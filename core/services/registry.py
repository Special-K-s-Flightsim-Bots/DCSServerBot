import asyncio
from core.services.base import Service
from typing import Callable, Any


class ServiceRegistry:
    _instance = None
    _main = None
    _registry: dict[str, Service] = dict[str, Service]()
    _singletons: dict[str, Service] = dict[str, Service]()

    def __new__(cls, main):
        if cls._instance is None:
            cls._instance = super(ServiceRegistry, cls).__new__(cls)
            cls._main = main
        return cls._instance

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()

    @classmethod
    def register(cls, name: str) -> Callable:
        def inner_wrapper(wrapped_class: Any) -> Callable:
            cls._registry[name] = wrapped_class
            return wrapped_class

        return inner_wrapper

    @classmethod
    def new(cls, name: str, *args, **kwargs) -> Service:
        instance = cls.get(name)
        if not instance:
            instance = cls._registry[name](main=cls._main, *args, **kwargs)
            cls._singletons[name] = instance
        return instance

    @classmethod
    def get(cls, name: str) -> Service:
        return cls._singletons.get(name)

    @classmethod
    async def run(cls):
        await asyncio.gather(*[service.start() for service in cls._singletons.values()])

    @classmethod
    async def shutdown(cls):
        for _, service in cls._singletons.items():
            await service.stop()
        cls._singletons.clear()
