from __future__ import annotations

import asyncio
import logging

from core.data.node import FatalException
from core.services.base import Service
from typing import Type, TypeVar, Callable, TYPE_CHECKING, Generic, ClassVar, Any

if TYPE_CHECKING:
    from core import NodeImpl

__all__ = ["ServiceRegistry"]

T = TypeVar("T", bound=Service)


class ServiceRegistry(Generic[T]):
    _instance: ClassVar[ServiceRegistry | None] = None
    _node: ClassVar[NodeImpl | None] = None
    _registry: ClassVar[dict[Any, Any]] = {}
    _master_only: ClassVar[set[Any]] = set()
    _plugins: ClassVar[dict[Any, str]] = {}
    _singletons: ClassVar[dict[Any, Any]] = {}
    _log: ClassVar[logging.Logger | None] = None

    def __new__(cls, node: NodeImpl) -> ServiceRegistry[T]:
        if cls._instance is None:
            cls._instance = super(ServiceRegistry, cls).__new__(cls)
            cls._node = node
            cls._log = logging.getLogger(f"{cls.__module__}.{cls.__name__}")
        return cls._instance

    async def __aenter__(self):
        await self.run()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()

    @classmethod
    def register(cls, *, t: Type[T] | None = None, master_only: bool | None = False,
                 plugin: str | None = None,
                 depends_on: list[Type[T]] | None = None) -> Callable[[Type[T]], Type[T]]:
        def inner_wrapper(wrapped_class: Type[T]) -> Type[T]:
            ServiceRegistry._registry[t or wrapped_class] = wrapped_class
            if master_only:
                ServiceRegistry._master_only.add(t or wrapped_class)
            if plugin:
                ServiceRegistry._plugins[t or wrapped_class] = plugin
            # Add dependencies to the service as an attribute
            wrapped_class.dependencies = depends_on or []
            return wrapped_class

        return inner_wrapper

    @classmethod
    def new(cls, t: Type[T], *args, **kwargs) -> T:
        instance = ServiceRegistry.get(t)
        if not instance:
            # noinspection PyArgumentList
            instance = ServiceRegistry._registry[t](node=ServiceRegistry._node, *args, **kwargs)
            ServiceRegistry._singletons[t] = instance
        return instance

    @classmethod
    def get(cls, t: str | Type[T]) -> T | None:
        if isinstance(t, str):
            for key, value in ServiceRegistry._singletons.items():
                if key.__name__ == t:
                    return value
            return None
        else:
            return ServiceRegistry._singletons.get(t, None)

    @classmethod
    def can_run(cls, t: Type[T]) -> bool:
        # check master only
        if ServiceRegistry.master_only(t) and not ServiceRegistry._node.master:
            return False
        # check plugin dependencies
        plugin = ServiceRegistry._plugins.get(t)
        if plugin and plugin not in ServiceRegistry._node.plugins:
            return False
        return True

    @classmethod
    def master_only(cls, t: Type[T]) -> bool:
        return t in ServiceRegistry._master_only

    @classmethod
    def services(cls) -> dict[Type[T], Type[T]]:
        return ServiceRegistry._registry

    @classmethod
    async def run(cls):
        ServiceRegistry._log.info("- Starting Services ...")
        services = [
            ServiceRegistry.new(service)
            for service in ServiceRegistry.services().keys()
            if ServiceRegistry.can_run(service)
        ]
        ret = await asyncio.gather(*[service.start() for service in services], return_exceptions=True)
        for idx in range(0, len(ret)):
            name = services[idx].name
            if isinstance(ret[idx], Exception):
                ServiceRegistry._log.error(f"  => Service {name} NOT started.", exc_info=ret[idx])
                if isinstance(ret[idx], FatalException):
                    raise
            else:
                ServiceRegistry._log.debug(f"  => Service {name} started.")
        ServiceRegistry._log.info("- Services started.")

    @classmethod
    async def shutdown(cls):
        ServiceRegistry._log.info("- Stopping Services...")
        for _, service in ServiceRegistry._singletons.items():
            await service.stop()
        ServiceRegistry._singletons.clear()
        ServiceRegistry._log.info("- Services stopped.")
