# DCSServerBot Service Development Guide

DCSServerBot is built on a modular architecture composed of **Plugins**, **Extensions**, and **Services**. 
While plugins handle user-facing Discord and DCS chat interactions and extensions manage third-party software per 
DCS server instance, **Services** provide persistent background workers, shared subsystem providers, and node-level 
infrastructure.

---

## Table of Contents
1. [Architecture & Role of Services](#architecture--role-of-services)
   - [Services vs Plugins vs Extensions](#services-vs-plugins-vs-extensions)
   - [Multi-Node Execution Model](#multi-node-execution-model)
   - [Consuming Services](#consuming-services)
2. [Service Directory Structure](#service-directory-structure)
3. [Service Registration & Dependency Management](#service-registration--dependency-management)
   - [The `@ServiceRegistry.register` Decorator](#the-serviceregistryregister-decorator)
   - [Lifecycle Cascades & Dependency Resolution](#lifecycle-cascades--dependency-resolution)
4. [Quick Start: Hello World Service](#quick-start-hello-world-service)
5. [Core Classes & Reference](#core-classes--reference)
   - [Service Base Class (`core.Service`)](#service-base-class-coreservice)
   - [Service Registry (`core.ServiceRegistry`)](#service-registry-coreserviceregistry)
   - [Service Proxy (`core.ServiceProxy`)](#service-proxy-coreserviceproxy)
6. [Cross-Node Execution & Remote Calls](#cross-node-execution--remote-calls)
   - [The `@proxy` Decorator](#the-proxy-decorator)
   - [Automatic Proxying via `ServiceRegistry.get()`](#automatic-proxying-via-serviceregistryget)
7. [Configuration & Schema Validation](#configuration--schema-validation)
   - [YAML Configuration Layout](#yaml-configuration-layout)
   - [PyKwalify Schema Validation](#pykwalify-schema-validation)
   - [Reading and Updating Configuration](#reading-and-updating-configuration)
8. [Built-In Services Overview](#built-in-services-overview)
9. [Best Practices & Developer Checklist](#best-practices--developer-checklist)

---

## Architecture & Role of Services

### Services vs Plugins vs Extensions

| Component     | Primary Location     | Execution Scope                          | Primary Purpose                                                                              |
|---------------|----------------------|------------------------------------------|----------------------------------------------------------------------------------------------|
| **Plugin**    | `plugins/<name>/`    | Master node (Discord bot process)        | Slash commands, DCS event listeners, Discord UI/reporting, in-game chat commands.            |
| **Extension** | `extensions/<name>/` | Bound to DCS server instances (`Server`) | Manages 3rd-party software processes and configs (e.g. SRS, Tacview, LotAtc, Olympus, Mist). |
| **Service**   | `services/<name>/`   | Master node, Agent nodes, or both        | Autonomous background workers, shared subsystems, hardware/OS operations, inter-node RPC.    |

### Multi-Node Execution Model
In DCSServerBot's multi-node architecture:
- **Master Node**: Runs the Discord bot (`BotService`), manages global state, coordinates agent nodes, and hosts Discord interactions.
- **Agent Nodes**: Run DCS server instances on separate physical or virtual machines, executing local OS tasks, audio streaming, backups, or mod management.
- **Node-Local Work**: Tasks requiring direct file system access, local audio loopback, or OS-level process management (e.g., `BackupService`, `MusicService`, `ModManagerService`) run directly as services on the respective node.

### Consuming Services
Any component in the bot (plugins, extensions, other services) can access a running service instance via `ServiceRegistry.get()`:

```python
from typing import cast
from core import ServiceRegistry
from services.bot import BotService

# Retrieve the singleton instance or remote proxy
bot_service = cast(BotService, ServiceRegistry.get(BotService))
await bot_service.alert(title="Server Warning", message="High memory usage detected!")
```

---

## Service Directory Structure

Each service is placed in its own folder under `services/<service_name>/`:

```text
services/<service_name>/
├── __init__.py           # Package exports (from .service import *)
├── service.py            # Main Service class definition
├── README.md             # Service documentation and YAML config reference
└── schemas/              # (Optional) PyKwalify YAML schema definitions
    └── <service_name>.yaml
```

### Key File Roles
| File / Directory  | Mandatory  | Description                                                                                    |
|-------------------|------------|------------------------------------------------------------------------------------------------|
| `__init__.py`     | **Yes**    | Exports the service class: `from .service import *`                                            |
| `service.py`      | **Yes**    | Implements the class inheriting from `core.Service` decorated with `@ServiceRegistry.register` |
| `README.md`       | **Yes**    | Human- and AI-readable documentation for the service                                           |
| `schemas/*.yaml`  | No         | PyKwalify validation schema for `config/services/<service_name>.yaml`                          |

---

## Service Registration & Dependency Management

### The `@ServiceRegistry.register` Decorator

Services must be registered with `ServiceRegistry` to enable automatic discovery, dependency resolution, and startup orchestration:

```python
from core import ServiceRegistry, Service
from services.servicebus import ServiceBus
from services.bot import BotService

@ServiceRegistry.register(
    master_only=False,          # Set True to only run on the master node
    agent_only=False,           # Set True to only run on agent nodes
    plugin="myplugin",          # Optional: only load if the specified plugin is active
    depends_on=[ServiceBus]     # List of service classes this service depends on
)
class MyService(Service):
    ...
```

#### Registration Parameters
| Parameter     | Type                              | Default  | Description                                                                                           |
|---------------|-----------------------------------|----------|-------------------------------------------------------------------------------------------------------|
| `t`           | `Type[T] \| None`                 | `None`   | Optional explicit type key (defaults to the decorated class).                                         |
| `master_only` | `bool`                            | `False`  | When `True`, the service only instantiates on the master node.                                        |
| `agent_only`  | `bool`                            | `False`  | When `True`, the service only instantiates on agent nodes.                                            |
| `plugin`      | `str \| None`                     | `None`   | Links service lifecycle to a plugin. If specified, the service loads only when the plugin is enabled. |
| `depends_on`  | `Iterable[Type[Service]] \| None` | `None`   | List of prerequisite service classes that must start before this service.                             |

> **Inherited Master-Only Constraint:** If a service declares a dependency on a `master_only` service (such as `BotService`), it automatically becomes `master_only`.

### Lifecycle Cascades & Dependency Resolution
`ServiceRegistry` enforces a directed acyclic graph (DAG) of dependencies:
- **Startup Order**: Dependencies are started first in topological order before dependent services.
- **Shutdown Order**: Dependent services are stopped first before their underlying dependencies.
- **Cycle Detection**: Circular dependencies raise an immediate error at registration time.

---

## Quick Start: Hello World Service

Here is a complete, minimal service implementation.

### 1. `services/greeter/__init__.py`
```python
from .service import GreeterService

__all__ = ["GreeterService"]
```

### 2. `services/greeter/service.py`
```python
import asyncio
from core import ServiceRegistry, Service, Server, proxy, ServiceInstallationError
from services.servicebus import ServiceBus


@ServiceRegistry.register(depends_on=[ServiceBus])
class GreeterService(Service):

    def __init__(self, node):
        super().__init__(node=node, name="Greeter")
        if not self.locals:
            self.log.info("No config/services/greeter.yaml found, using defaults.")

    async def start(self, *args, **kwargs):
        await super().start()
        self.log.info(f"GreeterService started on node {self.node.name} (master={self.node.master})")

    async def stop(self, *args, **kwargs):
        self.log.info("GreeterService stopping...")
        await super().stop()

    async def switch(self, master: bool):
        await super().switch(master)
        self.log.info(f"GreeterService role switched to master={master}")

    @proxy
    async def greet_server(self, server: Server, custom_message: str | None = None) -> str:
        """Executes on the node where `server` is hosted."""
        config = self.get_config(server)
        greeting = custom_message or config.get("greeting", "Hello from DCSServerBot!")
        self.log.info(f"Greeting server {server.name} on node {self.node.name}: {greeting}")
        return f"Node {self.node.name} greeted {server.name} with: {greeting}"
```

### 3. `services/greeter/schemas/greeter.yaml`
```yaml
type: map
mapping:
  DEFAULT:
    type: map
    mapping:
      greeting:
        type: str
        required: false
  regex;.*:
    type: map
    mapping:
      greeting:
        type: str
        required: false
```

### 4. `config/services/greeter.yaml`
```yaml
DEFAULT:
  greeting: "Welcome to the DCS Server!"

DCS.dcs_server1:
  greeting: "Welcome to Dedicated Server Alpha!"
```

---

## Core Classes & Reference

### Service Base Class (`core.Service`)

The abstract base class for all DCSServerBot services.

#### Class Attributes
| Attribute      | Type                  | Description                                                                           |
|----------------|-----------------------|---------------------------------------------------------------------------------------|
| `self.name`    | `str`                 | Service name (defaults to class name).                                                |
| `self.node`    | `NodeImpl`            | Current node instance (`self.node.name`, `self.node.master`, `self.node.config_dir`). |
| `self.running` | `bool`                | Current execution status (`True` if active).                                          |
| `self.log`     | `logging.Logger`      | Scoped logger (`services.<module>.<Class>`).                                          |
| `self.pool`    | `ConnectionPool`      | Synchronous PostgreSQL connection pool (`self.node.pool`).                            |
| `self.apool`   | `AsyncConnectionPool` | Asynchronous PostgreSQL connection pool (`self.node.apool`).                          |
| `self.config`  | `dict`                | Node configuration from `nodes.yaml`.                                                 |
| `self.locals`  | `dict`                | Parsed YAML content from `config/services/<service_name>.yaml`.                       |
| `self._config` | `dict[str, dict]`     | Cache for merged server/instance configurations.                                      |

#### Methods & Lifecycle Hooks

```python
class Service(ABC):
    async def start(self, *args, **kwargs):
        """Starts the service. Always call `await super().start()` to handle cascades."""
        ...

    async def stop(self, *args, **kwargs):
        """Stops the service. Always call `await super().stop()` to handle cascades."""
        ...

    async def switch(self, master: bool):
        """Called when a node failover occurs and master status toggles."""
        ...

    def is_running(self) -> bool:
        """Returns True if the service is currently running."""
        ...

    def read_locals(self) -> dict:
        """Loads and validates config/services/<service_name>.yaml."""
        ...

    def save_config(self):
        """Writes current self.locals back to config/services/<service_name>.yaml."""
        ...

    def get_config(self, server: Server | None = None, **kwargs) -> dict:
        """
        Retrieves service configuration.
        If `server` is provided, merges `DEFAULT` with the node/instance override section.
        """
        ...

    def reload(self):
        """Reloads self.locals from disk."""
        ...

    def get_ports(self) -> dict[str, Port]:
        """Returns a mapping of network port names to Port instances used by the service."""
        ...
```

---

### Service Registry (`core.ServiceRegistry`)

A thread-safe singleton managing service instances, registration, proxy creation, and dependency resolution.

#### Key Methods
- `ServiceRegistry.get(t: str | Type[T]) -> T | None`: Returns the active service instance. If the service is running on another node or is `master_only` while on an agent, automatically returns a `ServiceProxy`.
- `ServiceRegistry.register(master_only=..., agent_only=..., plugin=..., depends_on=...)`: Decorator to register a service class.
- `ServiceRegistry.services() -> dict[Type[T], Type[T]]`: Returns all registered services.
- `ServiceRegistry.can_run(cls: Type[T]) -> bool`: Checks whether a service is allowed to run on the current node (verifying master/agent flags and active plugins).

---

### Service Proxy (`core.ServiceProxy`)

When code on an agent node attempts to call a `master_only` service (or a service hosted remotely), `ServiceRegistry.get()` returns a `ServiceProxy` instance.

- Any method called on a `ServiceProxy` transparently marshals parameters into an RPC packet:
  ```python
  {
      "command": "rpc",
      "service": "<ServiceName>",
      "method": "<method_name>",
      "params": { ... }
  }
  ```
- The packet is transmitted synchronously across the `ServiceBus` to the hosting node and the result is returned.
- Object references (`Server`, `Node`, `Instance`, `Enum`) are automatically serialized to names/values during RPC dispatch.

---

## Cross-Node Execution & Remote Calls

### The `@proxy` Decorator

When a service method needs to operate on a DCS server or instance that may reside on a remote node, decorate the method with `@proxy`.

```python
from core import proxy, Server, Instance, Node

class ModManagerService(Service):

    @proxy(timeout=120)
    async def install_package(self, server: Server, folder: str, package_name: str, version: str) -> bool:
        # If `server` is local (server.is_remote == False): executes here immediately.
        # If `server` is remote (server.is_remote == True): automatically routed via RPC to the node hosting `server`.
        ...
```

#### How `@proxy` Works
1. Inspects arguments for a `server`, `instance`, or `node` parameter.
2. Identifies the target `Node`.
3. If `node.is_remote` is `False`, executes locally.
4. If `node.is_remote` is `True`, routes the call over `ServiceBus.send_to_node_sync()` to the remote node, waits for the response, and returns the result.

---

## Configuration & Schema Validation

### YAML Configuration Layout
Service configurations are stored in `config/services/<service_name>.yaml`.

```yaml
# config/services/music.yaml
DEFAULT:
  music_dir: "%USERPROFILE%/Music"
  volume: 80

NodeAlpha:
  DCS.server1:
    volume: 50
```

### PyKwalify Schema Validation
To enforce configuration validity, create a schema at `services/<service_name>/schemas/<service_name>.yaml`:

```yaml
type: map
mapping:
  DEFAULT:
    type: map
    mapping:
      music_dir:
        type: str
        required: true
      volume:
        type: int
        required: false
  regex;.*:
    type: map
    mapping:
      regex;.*:
        type: map
        mapping:
          volume:
            type: int
            required: false
```

- When `validation: strict` or `lazy` is set in `nodes.yaml`, `read_locals()` automatically validates the YAML file against the schema.
- Validation failures raise a `ServiceInstallationError`.

### Reading and Updating Configuration
```python
# Read default section
default_cfg = self.get_config()

# Read merged server configuration (DEFAULT + Node/Instance overrides)
server_cfg = self.get_config(server)

# Modify configuration in memory and persist back to disk
self.locals['DEFAULT']['volume'] = 90
self.save_config()
```

---

## Built-In Services Overview

| Service                 | Module                | Placement       | Dependencies               | Description                                                                                       |
|-------------------------|-----------------------|-----------------|----------------------------|---------------------------------------------------------------------------------------------------|
| **`BotService`**        | `services.bot`        | Master Only     | `ServiceBus`               | Hosts Discord bot instance, slash command registration, event loop, and discord alerts.           |
| **`ServiceBus`**        | `services.servicebus` | Master & Agents | None                       | Inter-process and inter-node RPC bus over UDP/TCP sockets and internal queues.                    |
| **`BackupService`**     | `services.backup`     | Master & Agents | `ServiceBus`               | Backs up Saved Games, PostgreSQL databases, and bot configurations.                               |
| **`ModManagerService`** | `services.modmanager` | Master & Agents | `ServiceBus`               | Automates mod/OvGME package installation, version tracking, and updates before/after DCS patches. |
| **`MonitoringService`** | `services.monitoring` | Master & Agents | `ServiceBus`               | Monitors CPU, RAM, disk usage, and DCS server process health.                                     |
| **`MusicService`**      | `services.music`      | Master & Agents | `ServiceBus`               | Streams MP3/audio files to DCS-SRS radio frequencies per node/server.                             |
| **`CleanupService`**    | `services.cleanup`    | Master & Agents | `ServiceBus`               | Automatically purges old log files, temp files, mission exports, and aged backups.                |
| **`CronService`**       | `services.cron`       | Master Only     | `ServiceBus`, `BotService` | Runs scheduled jobs (server restarts, mission rotation, server shutdowns, OS reboots).            |
| **`FirewallService`**   | `services.firewall`   | Master & Agents | `ServiceBus`               | Automatically configures OS firewall rules for DCS and extension ports.                           |
| **`DashboardService`**  | `services.dashboard`  | Master Only     | `BotService`, `ServiceBus` | Provides rich terminal monitoring UI and curses/CLI status dashboards.                            |
| **`WebService`**        | `services.webservice` | Master Only     | None                       | Provides an embedded aiohttp web server / REST API endpoint provider.                             |
| **`UPSService`**        | `services.ups`        | Master & Agents | `ServiceBus`               | Monitors UPS battery state and safely shuts down DCS instances on power loss.                     |
| **`FalconBMSService`**  | `services.falconbms`  | Master & Agents | `ServiceBus`               | Falcon BMS integration and server lifecycle management.                                           |
| **`MCPService`**        | `services.mcpservice` | Master & Agents | None                       | Model Context Protocol server exposing DCSServerBot capabilities.                                 |

---

## Best Practices & Developer Checklist

When creating or modifying a service, verify the following:

- [ ] **Registration**: Decorated with `@ServiceRegistry.register(...)` and imported in `services/<service_name>/__init__.py`.
- [ ] **Node-Locality Awareness**: Check `self.node.master` or `server.is_remote` before making assumptions about local files or processes.
- [ ] **Use `@proxy` for Cross-Node Methods**: Any method operating on a `Server` or `Instance` hosted across nodes must use `@proxy` or handle remote execution.
- [ ] **Graceful Lifecycle**: Always invoke `await super().start()` and `await super().stop()` to properly participate in the `ServiceRegistry` cascade.
- [ ] **Async PostgreSQL Pool**: Prefer `self.apool` (`async with self.apool.connection() as conn:`) for database interactions.
- [ ] **Fail-Safe Initialization**: Raise `ServiceInstallationError(self.name, "Reason")` if required configuration, binaries, or prerequisites are missing.
- [ ] **Schema Definition**: Include a PyKwalify schema under `services/<service_name>/schemas/<service_name>.yaml` for configuration validation.
- [ ] **Logging**: Use `self.log` rather than print statements to maintain structured logging across nodes.
