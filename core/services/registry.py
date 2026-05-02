from __future__ import annotations

import logging
import threading

from collections import defaultdict, deque
from core.data.node import FatalException
from core.services.base import Service
from core.utils.helper import dynamic_import
from typing import Type, TypeVar, Callable, TYPE_CHECKING, Generic, ClassVar, Any, cast, Iterable

if TYPE_CHECKING:
    from core import NodeImpl

__all__ = ["ServiceRegistry"]

T = TypeVar("T", bound=Service)


class ServiceRegistry(Generic[T]):
    _instance: ClassVar[ServiceRegistry | None] = None
    _lock = threading.Lock()    # make it thread-safe
    _node: ClassVar[NodeImpl | None] = None
    _registry: ClassVar[dict[Any, Any]] = {}
    _master_only: ClassVar[set[Any]] = set()
    _agent_only: ClassVar[set[Any]] = set()
    _plugins: ClassVar[dict[Any, str]] = {}
    _deps: ClassVar[dict[Any, set[Any]]] = defaultdict(set)
    _rev_deps: ClassVar[dict[Any, set[Any]]] = defaultdict(set)
    _singletons: ClassVar[dict[Any, Any]] = {}
    _log: ClassVar[logging.Logger | None] = None

    def __new__(cls, node: NodeImpl) -> ServiceRegistry[T]:
        if not cls._instance:
            with cls._lock:  # Double-checked locking pattern
                if not cls._instance:
                    cls._instance = super(ServiceRegistry, cls).__new__(cls)
                    cls._node = node
                    cls._log = logging.getLogger(f"{cls.__module__}.{cls.__name__}")

        return cast(ServiceRegistry[T], cls._instance)

    async def __aenter__(self):
        await self.run()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()

    @classmethod
    def register(
            cls,
            *,
            t: Type[T] | None = None,
            master_only: bool | None = False,
            agent_only: bool | None = False,
            plugin: str | None = None,
            depends_on: Iterable[Type[T]] | None = None
    ) -> Callable[[Type[T]], Type[T]]:
        def inner_wrapper(wrapped_class: Type[T]) -> Type[T]:
            ServiceRegistry._registry[t or wrapped_class] = wrapped_class
            if master_only:
                ServiceRegistry._master_only.add(t or wrapped_class)
            elif agent_only:
                ServiceRegistry._agent_only.add(t or wrapped_class)
            if plugin:
                ServiceRegistry._plugins[t or wrapped_class] = plugin
            # Add dependencies
            deps = depends_on or []
            cls._deps[t or wrapped_class] = set(deps)
            for d in deps:
                if d not in cls._registry:
                    raise KeyError(f"Dependency {d!r} not registered")
                cls._rev_deps[d].add(t or wrapped_class)
            # Check for cycles
            cls._detect_cycle()
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
        elif ServiceRegistry.agent_only(t) and ServiceRegistry._node.master:
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
    def agent_only(cls, t: Type[T]) -> bool:
        return t in ServiceRegistry._agent_only

    @classmethod
    def services(cls) -> dict[Type[T], Type[T]]:
        return ServiceRegistry._registry

    @classmethod
    def _detect_cycle(cls) -> None:
        """Raise RuntimeError if a cycle exists in the current graph."""
        visited, stack = set(), set()

        def visit(node: Any) -> None:
            if node in stack:
                raise RuntimeError(f"Cycle detected at {node!r}")
            if node in visited:
                return
            visited.add(node)
            stack.add(node)
            for dep in cls._deps.get(node, ()):
                visit(dep)
            stack.remove(node)

        for node in cls._registry:
            visit(node)

    @classmethod
    def _topo_sort(
        cls,
        nodes: Iterable[Type[T]],
        reverse: bool = False,
    ) -> list[Type[T]]:
        """
        Return a list of services that respects the dependency order.

        * reverse=False  → dependencies first (good for run)
        * reverse=True   → dependents first (good for shutdown)
        """
        needed: set[Type[T]] = set()

        # collect all reachable nodes
        def collect(node: Type[T]) -> None:
            if node in needed:
                return
            needed.add(node)
            edges = cls._rev_deps if reverse else cls._deps
            for e in edges.get(node, ()):
                collect(e)

        for n in nodes:
            if n not in cls._registry:
                raise KeyError(f"Unknown service {n!r}")
            collect(n)

        # Kahn’s algorithm
        indeg: dict[Type[T], int] = defaultdict(int)

        # indegree of a node = number of *incoming* edges
        #   • for normal order (reverse=False)  → number of dependencies
        #   • for reverse order (reverse=True) → number of dependents
        if not reverse:          # normal order
            for n in needed:
                indeg[n] = len(cls._deps.get(n, ()))
            adjacency = cls._rev_deps      # after popping a node, look at its dependents
        else:                    # reverse order
            for n in needed:
                indeg[n] = len(cls._rev_deps.get(n, ()))
            adjacency = cls._deps          # after popping a node, look at its dependencies

        # queue nodes that have no incoming edges
        q = deque([n for n in needed if indeg[n] == 0])
        order: list[Type[T]] = []

        while q:
            u = q.popleft()
            order.append(u)
            for v in adjacency.get(u, ()):
                if v not in needed:
                    continue
                indeg[v] -= 1
                if indeg[v] == 0:
                    q.append(v)

        if len(order) != len(needed):
            # should never happen if _detect_cycle() was already called
            raise RuntimeError("Cycle detected (should have been caught earlier)")

        return order

    @classmethod
    def _collect_dependents(cls, service: Type[T]) -> set[Type[T]]:
        if service not in cls._registry:
            raise KeyError(f"Unknown service {service!r}")

        visited: set[Type[T]] = set()
        stack: list[Type[T]] = [service]

        while stack:
            cur = stack.pop()
            for dep in cls._rev_deps.get(cur, ()):
                if dep not in visited:
                    visited.add(dep)
                    stack.append(dep)

        return visited

    @classmethod
    def _collect_dependencies(cls, service: Type[T]) -> set[Type[T]]:
        if service not in cls._registry:
            raise KeyError(f"Unknown service {service!r}")

        visited: set[Type[T]] = set()
        stack: list[Type[T]] = [service]

        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            stack.extend(cls._deps.get(cur, ()))

        return visited

    @classmethod
    async def start_service(cls, service: Type[T]) -> None:
        """
        Start the requested service and every service it depends on.
        Dependencies are started first, so that the requested service
        can safely access them during its own start routine.
        """
        cascade = cls._collect_dependencies(service)
        order = cls._topo_sort(cascade, reverse=False)

        for svc_type in order:
            if not cls.can_run(svc_type):
                continue

            svc = cls.new(svc_type)
            if svc.is_running():
                continue

            svc._in_registry_cascade = True
            try:
                await svc.start()
            except Exception:
                cls._singletons.pop(svc_type, None)
                raise
            finally:
                svc._in_registry_cascade = False

    @classmethod
    async def run(cls):
        cls._log.info("- Starting Services ...")
        dynamic_import('services')

        # ️Filter the services that are actually allowed to run
        candidates = [
            s for s in cls.services().keys() if cls.can_run(s)
        ]

        # Compute a safe start order
        start_order = cls._topo_sort(candidates, reverse=False)

        # Start them one-by-one through the same dependency-aware path
        # that is used for starting a single service.
        for svc_type in start_order:
            try:
                await cls.start_service(svc_type)
            except Exception as exc:
                svc = cls.get(svc_type)
                name = svc.name if svc else svc_type.__name__
                cls._log.error(f"  => Service {name} NOT started.", exc_info=exc)
                if isinstance(exc, FatalException):
                    raise
                # keep going – other services may still start

        cls._log.info("- Services started.")

    @classmethod
    async def stop_service(cls, service: Type[T]) -> None:
        """
        Stop the requested service *and* every other service that
        depends on it.  Dependents are stopped first, so that no
        service tries to talk to a bus that’s already gone.

        The routine is safe to call from anywhere – it uses the
        reverse‑dependency graph that we already maintain during start‑up.
        """
        # gather the full “cascade”
        cascade = cls._collect_dependents(service)

        # we want the *reverse* topological order (dependents → bus)
        order = cls._topo_sort(cascade, reverse=True)

        # actually stop them
        for svc_type in order:
            svc = cls.get(svc_type)
            if svc is None:
                # The singleton may have already been removed.
                continue

            # Skip services that are already dead – this keeps shutdown idempotent
            if not svc.is_running():
                continue

            await svc.stop()
            # Optional: remove the singleton so a future ``start`` creates a fresh instance
            cls._singletons.pop(svc_type, None)

        # finally stop the original service itself (if it survived the loop)
        orig = cls.get(service)
        if orig and orig.is_running():
            await orig.stop()
            cls._singletons.pop(service, None)

    @classmethod
    async def shutdown(cls):
        cls._log.info("- Stopping Services...")
        # Compute reverse order (dependents first)
        candidates = [
            s for s in cls.services().keys() if cls.can_run(s)
        ]
        stop_order = cls._topo_sort(candidates, reverse=True)

        for svc_type in stop_order:
            svc = cls.get(svc_type)
            if svc and svc.is_running():
                await svc.stop()

        cls._singletons.clear()
        cls._log.info("- Services stopped.")
