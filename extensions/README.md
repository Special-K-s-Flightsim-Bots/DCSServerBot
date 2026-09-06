# DCSServerBot Extension Development Guide

DCSServerBot is built on a modular architecture composed of **Plugins**, **Services**, and **Extensions**. 
While plugins handle user-facing Discord and DCS chat interactions and services provide node-level background 
infrastructure, **Extensions** manage third-party applications, mods, and mission-level utilities bound to individual 
DCS server instances.

---

## Table of Contents
1. [Architecture & Role of Extensions](#architecture--role-of-extensions)
   - [Extensions vs Plugins vs Services](#extensions-vs-plugins-vs-services)
   - [Per-Server Instance Scope](#per-server-instance-scope)
   - [DCS Hook & Mission Pipeline Integration](#dcs-hook--mission-pipeline-integration)
2. [Extension Directory Structure](#extension-directory-structure)
3. [Configuration Hierarchy (`nodes.yaml`)](#configuration-hierarchy-nodesyaml)
4. [Quick Start: Hello World Extension](#quick-start-hello-world-extension)
5. [Core Classes & Reference](#core-classes--reference)
   - [Base Extension (`core.Extension`)](#base-extension-coreextension)
   - [Installable Extension (`core.InstallableExtension`)](#installable-extension-coreinstallableextension)
   - [Exceptions](#exceptions)
6. [Extension Lifecycle in Detail](#extension-lifecycle-in-detail)
   - [1. Initialization & Configuration Loading](#1-initialization--configuration-loading)
   - [2. Installation & Availability Check](#2-installation--availability-check)
   - [3. Pre-Flight Preparation (`prepare`)](#3-pre-flight-preparation-prepare)
   - [4. Mission Pre-Load Interception (`beforeMissionLoad`)](#4-mission-pre-load-interception-beforemissionload)
   - [5. Startup & DCS Hook Registration (`startup`)](#5-startup--dcs-hook-registration-startup)
   - [6. Shutdown & DCS Hook Cleanup (`shutdown`)](#6-shutdown--dcs-hook-cleanup-shutdown)
   - [7. Dynamic Runtime Control (`enable` / `disable`)](#7-dynamic-runtime-control-enable--disable)
7. [Status Rendering & Discord Embeds](#status-rendering--discord-embeds)
8. [Port Tracking & Firewall Integration](#port-tracking--firewall-integration)
9. [Mission Modification with `MizFile`](#mission-modification-with-mizfile)
10. [Built-In Extensions Overview](#built-in-extensions-overview)
11. [Best Practices & Developer Checklist](#best-practices--developer-checklist)

---

## Architecture & Role of Extensions

### Extensions vs Plugins vs Services

| Component     | Scope                     | Execution Target                    | Primary Purpose                                                                                           |
|---------------|---------------------------|-------------------------------------|-----------------------------------------------------------------------------------------------------------|
| **Plugin**    | Bot Master Node           | Discord process & DCS Hook events   | Slash commands, Discord UI/reporting, in-game chat commands, game master tools.                           |
| **Service**   | Node-wide                 | Master, Agent, or both              | Autonomous background workers, shared subsystems, inter-node RPC, OS utilities.                           |
| **Extension** | Per DCS Server (`Server`) | Local Node hosting the DCS instance | External tool lifecycles (SRS, Tacview, LotAtc, Olympus), mission modification, DCS hook synchronization. |

### Per-Server Instance Scope
- Extensions are instantiated **per DCS server instance** (`Server`).
- If a node runs three DCS instances (Alpha, Bravo, Charlie), each server receives its own isolated instance of configured extensions (e.g., three separate `SRS` instances managing their respective ports and configuration files).

### DCS Hook & Mission Pipeline Integration
- **DCS World Hook Sync**: When an extension starts up, DCSServerBot informs the DCS Lua Hook environment (`addExtension`), making DCS scripts aware of the extension's availability. When shutting down, `removeExtension` is sent.
- **Mission Pre-Load Pipeline**: Extensions can intercept mission loading via `beforeMissionLoad()`, modifying weather (e.g. `RealWeather`), injecting triggers or Lua scripts (e.g. `MizEdit`, `DSMC`), or saving state before DCS launches the `.miz` file.

---

## Extension Directory Structure

Extensions live in the `extensions/<extension_name>/` directory:

```text
extensions/<extension_name>/
├── __init__.py           # Package exports (from .extension import *)
├── extension.py          # Main Extension or InstallableExtension class implementation
└── README.md             # Extension documentation and configuration reference
```

### Key File Roles
| File           | Mandatory  | Description                                                                           |
|----------------|------------|---------------------------------------------------------------------------------------|
| `__init__.py`  | **Yes**    | Package initializer exposing the extension class: `from .extension import *`          |
| `extension.py` | **Yes**    | Implements the class inheriting from `core.Extension` or `core.InstallableExtension`. |
| `README.md`    | **Yes**    | Human- and AI-readable documentation of extension options and setup instructions.     |

---

## Configuration Hierarchy (`nodes.yaml`)

Extensions are configured in `config/nodes.yaml`. 
Configuration cascades from the global/node level down to individual server instances:

```yaml
# config/nodes.yaml
MyNode:
  # Node-level defaults (applies to all instances on this node)
  extensions:
    SRS:
      installation: "C:\\Program Files\\DCS-SimpleRadio-Standalone"
      autoupdate: true
    MyCustomExt:
      api_key: "secret-node-key"

  instances:
    DCS.dcs_server1:
      # Instance-level overrides
      extensions:
        SRS:
          port: 5002
          blue_password: "blue"
        MyCustomExt:
          enabled: true
          debug_mode: true
```

### Configuration Resolution Order
When `server.load_extension(name)` initializes an extension:
1. Default built-in settings (`DEFAULT_EXTENSIONS`).
2. Node-level settings (`node.locals['extensions'][name]`).
3. Instance-level settings (`server.locals['extensions'][name]`).
4. Resulting dictionary is passed as `config` into the extension's `__init__(server, config)`.

---

## Quick Start: Hello World Extension

Here is a complete, minimal extension implementation.

### 1. `extensions/myext/__init__.py`
```python
from .extension import MyExtension

__all__ = ["MyExtension"]
```

### 2. `extensions/myext/extension.py`
```python
from typing import Optional
from core import Extension, Server
from discord.ext import tasks


class MyExtension(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.custom_option = self.config.get("custom_option", "default_value")

    def is_installed(self) -> bool:
        """Verify any required executables, mods, or directories exist."""
        return True

    async def prepare(self) -> bool:
        """Run pre-flight actions before the DCS server process launches."""
        await super().prepare()
        self.log.info(f"Preparing MyExtension for {self.server.name} with option '{self.custom_option}'")
        return True

    async def startup(self, *, quiet: bool = False) -> bool:
        """Start background processes or notify DCS when the server launches."""
        if not await super().startup(quiet=quiet):
            return False
        self.log.info(f"MyExtension successfully active on {self.server.name}")
        return True

    def shutdown(self, *, quiet: bool = False) -> bool:
        """Stop background processes when the DCS server stops."""
        self.log.info(f"MyExtension shutting down on {self.server.name}")
        return super().shutdown(quiet=quiet)

    def is_running(self) -> bool:
        """Return True if background process or service is currently active."""
        return self.running

    @property
    def version(self) -> Optional[str]:
        return "1.0.0"

    async def render(self, param: Optional[dict] = None) -> dict:
        """Provide embed fields for Discord server status cards."""
        return {
            "name": "MyExtension",
            "version": self.version or "1.0.0",
            "value": "Online" if self.is_running() else "Offline"
        }

    @tasks.loop(hours=24.0)
    async def schedule(self):
        """Optional scheduled background loop (started automatically on init)."""
        self.log.debug(f"Running periodic check for {self.name} on {self.server.name}")
```

### 3. Enabling in `config/nodes.yaml`
```yaml
MyNode:
  instances:
    DCS.dcs_server1:
      extensions:
        myext.MyExtension:
          enabled: true
          custom_option: "AlphaServer"
```

---

## Core Classes & Reference

### Base Extension (`core.Extension`)

Abstract base class for all extensions (`from core import Extension`).

#### Attributes
| Attribute      | Type                | Description                                                                   |
|----------------|---------------------|-------------------------------------------------------------------------------|
| `self.server`  | `Server`            | The DCS server instance hosting this extension.                               |
| `self.node`    | `Node`              | The node where the DCS instance is running (`self.server.node`).              |
| `self.config`  | `dict`              | Merged configuration dictionary from `nodes.yaml`.                            |
| `self.locals`  | `dict`              | Configuration loaded via `load_config()` (e.g. from `.cfg`, `.ini`, `.toml`). |
| `self.running` | `bool`              | Extension run state (`True` if active).                                       |
| `self.log`     | `logging.Logger`    | Scoped logger (`extensions.<module>.<ClassName>`).                            |
| `self.pool`    | `ConnectionPool`    | Synchronous PostgreSQL connection pool (`self.node.pool`).                    |
| `self.loop`    | `AbstractEventLoop` | Current asyncio event loop.                                                   |
| `self.bus`     | `ServiceBus`        | Inter-service and inter-node RPC service bus instance.                        |
| `self.bot`     | `BotService`        | Property accessing the Discord bot service singleton.                         |

#### Properties
- `name -> str`: Name of the extension (defaults to class name unless overridden by `config['name']`).
- `version -> str | None`: Extension version string (or `None`).
- `enabled -> bool`: Returns `self.config.get('enabled', True)`.
- `hidden -> bool`: Returns `self.config.get('hidden', False)`. When `True`, excluded from Discord status embeds.

#### Methods & Lifecycle Hooks

```python
class Extension(ABC):
    def load_config(self) -> dict:
        """Load external configuration files (e.g. INI, TOML, JSON) into self.locals."""
        return dict()

    def is_installed(self) -> bool:
        """Return True if required local files or executables are installed."""
        return True

    def is_available(self) -> bool:
        """Return True if the extension is ready to run."""
        return True

    def is_running(self) -> bool:
        """Return True if the extension is currently running."""
        return self.running

    async def install(self) -> bool:
        """Install or provision required files."""
        return True

    async def prepare(self) -> bool:
        """Run pre-flight checks and configuration updates before DCS startup."""
        if not self.is_available():
            raise InstallException(f"{self.name} is not installed.")
        return True

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        """
        Intercept mission file loading before DCS opens the .miz file.
        Returns: (new_filename, is_modified)
        """
        return filename, False

    async def startup(self, *, quiet: bool = False) -> bool:
        """Start external processes and register with DCS via 'addExtension'."""
        ...

    def shutdown(self, *, quiet: bool = False) -> bool:
        """Stop external processes and unregister from DCS via 'removeExtension'."""
        ...

    async def enable(self) -> bool:
        """Dynamically enable the extension at runtime."""
        ...

    async def disable(self) -> bool:
        """Dynamically disable the extension at runtime."""
        ...

    async def render(self, param: dict | None = None) -> dict:
        """Return status dictionary formatted for Discord status embeds."""
        ...

    def get_ports(self) -> dict[str, Port]:
        """Return network ports used by this extension for firewall and tracking."""
        return {}

    def rename_server(self, old_name: str, new_name: str):
        """Handle server rename events to update local paths or config sections."""
        pass
```

---

### Installable Extension (`core.InstallableExtension`)

Subclass of `Extension` providing built-in integration with `ModManagerService` for automated installation, uninstallation, package management, and GitHub release autoupdates.

```python
from core import InstallableExtension, Server


class MyMod(InstallableExtension):

    def __init__(self, server: Server, config: dict):
        super().__init__(
            server=server,
            config=config,
            repo="owner/repo-name",       # Optional GitHub repository (owner/repo)
            package_name="MyModPackage"   # ModManager package name in Saved Games
        )

    def is_installed(self) -> bool:
        return os.path.exists(os.path.join(self.server.instance.home, "Scripts", "MyMod.lua"))
```

#### Key Capabilities
- **`autoupdate`**: When `autoupdate: true` is configured in `nodes.yaml`, `prepare()` automatically checks for new versions on GitHub or local repositories and updates before DCS launch.
- **`install(version=None)`**: Automatically downloads and unpacks packages into `Saved Games` or target directories via `ModManagerService`.
- **`uninstall()`**: Cleans up installed packages and files.
- **`update(version=None)`**: Automates uninstallation followed by clean reinstallation of new versions.
- **`repair()`**: Reinstalls current version if corrupted.

---

### Exceptions
- `ExtensionException`: Base exception for extension errors.
- `InstallException`: Raised during `prepare()` or `install()` when prerequisites are missing or setup fails. Prevents DCS from launching in an invalid state.
- `UninstallException`: Raised during uninstallation failures.

---

## Extension Lifecycle in Detail

```text
[Bot/Server Init]
       │
       ├──> __init__(server, config)
       │         └── load_config()
       │         └── schedule (task loop start)
       │
[Server Startup Triggered]
       │
       ├──> is_installed() ──(False)──> install()
       │
       ├──> prepare() [Autoupdate, validate config, write config files]
       │
       ├──> [For each mission load]: beforeMissionLoad(miz_path) -> (new_path, dirty)
       │
       ├──> startup() [Launch process, send "addExtension" to DCS Hook]
       │
[DCS Server Running]
       │
       ├──> render() [Discord status embed updates]
       │
[Server Shutdown Triggered]
       │
       └──> shutdown() [Terminate process, send "removeExtension" to DCS Hook]
```

### 1. Initialization & Configuration Loading
When `server.init_extensions()` is called:
- The class is dynamically loaded (either from built-in `extensions.<name_lower>.extension.<Name>` or custom dotted module path).
- `load_config()` reads external settings files (e.g. `SRS.cfg`, `Tacview.ini`, `config.toml`).
- If a `@tasks.loop` method named `schedule` is present, it is started safely via `utils.safe_start()`.

### 2. Installation & Availability Check
Before DCS starts:
- `server.prepare_extensions()` checks `is_installed()`. If `False`, `install()` is invoked.
- `is_available()` validates whether all necessary binaries, licenses, or directories exist.

### 3. Pre-Flight Preparation (`prepare`)
- Runs just before DCS server startup.
- Config files are updated with instance-specific settings (e.g. binding SRS or Tacview ports to the specific DCS server port).
- If validation fails, raise `InstallException("Reason")`.

### 4. Mission Pre-Load Interception (`beforeMissionLoad`)
Called every time DCS is about to load a mission file (`.miz`):
```python
async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
    # Modify mission file or inject weather/triggers
    miz = MizFile(filename)
    # Modify mission content...
    new_filename = miz.save()
    return new_filename, True  # return (path_to_load, is_modified)
```

### 5. Startup & DCS Hook Registration (`startup`)
When the DCS server process is launched:
- `startup()` starts external processes (e.g. `psutil.Popen` or `ProcessManager`).
- DCSServerBot sends an async command to DCS World:
  ```json
  { "command": "addExtension", "extension": "<ExtensionName>" }
  ```
- Logs launch confirmation to console and Discord logs.

### 6. Shutdown & DCS Hook Cleanup (`shutdown`)
When the DCS server stops or restarts:
- Sends `removeExtension` to the DCS Lua Hook.
- Terminates background processes started by the extension.
- Releases any locks or file observers.

### 7. Dynamic Runtime Control (`enable` / `disable`)
Extensions can be enabled or disabled dynamically without restarting DCSServerBot:
- `await server.enable_extension("SRS")`: Installs, prepares, and starts the extension if DCS is currently running.
- `await server.disable_extension("SRS")`: Shuts down and disables the extension.

---

## Status Rendering & Discord Embeds

The `render()` method produces data displayed in Discord server status embeds:

```python
async def render(self, param: Optional[dict] = None) -> dict:
    return {
        "name": "SRS",
        "version": self.version or "n/a",
        "value": f"Port: {self.config.get('port', 5002)}" if self.is_running() else "Disabled"
    }
```

### Rendering Rules
- If `self.hidden` is `True`, `render()` raises `NotImplementedError()` and is automatically omitted from public embeds.
- Common return fields:
  - `name`: Display title in embed.
  - `version`: Version string.
  - `value`: Status description (e.g., URL, port number, operational state).

---

## Port Tracking & Firewall Integration

If your extension opens network ports (TCP/UDP), implement `get_ports()`:

```python
from core import Port, PortType


def get_ports(self) -> dict[str, Port]:
    port_number = self.config.get('port', 5002)
    return {
        "SRS Server": Port(port=port_number, port_type=PortType.UDP),
        "SRS Web": Port(port=port_number + 1, port_type=PortType.TCP)
    }
```
This enables `FirewallService` to automatically manage Windows Firewall rules for your extension.

---

## Mission Modification with `MizFile`

Extensions frequently modify mission files prior to launch. Use `core.MizFile` to safely parse and manipulate `.miz` packages:

```python
from core import Extension, MizFile


class WeatherInjector(Extension):

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        miz = MizFile(filename)
        theatre = miz.theatre  # e.g. "Caucasus", "PersianGulf", "Nevada"

        # Inspect or modify mission table
        mission_data = miz.mission
        mission_data['weather']['season']['temperature'] = 25.0

        # Save modified mission
        temp_mission = miz.save()
        return temp_mission, True
```

---

## Built-In Extensions Overview

| Extension       | Inherits From          | Target Software / Purpose                                                                   |
|-----------------|------------------------|---------------------------------------------------------------------------------------------|
| **SRS**         | `InstallableExtension` | Simple Radio Standalone server management, port assignment, password injection, autoupdate. |
| **Tacview**     | `InstallableExtension` | Tacview telemetry export, real-time recording, flight logs, password configuration.         |
| **RealWeather** | `InstallableExtension` | Real-world METAR weather injection into `.miz` files prior to mission start.                |
| **LotAtc**      | `InstallableExtension` | LotAtc radar/GCI server configuration, password synchronization, port tracking.             |
| **Olympus**     | `InstallableExtension` | DCS Olympus game master backend process management and client connectivity.                 |
| **Sneaker**     | `InstallableExtension` | ALICAT radar / live web-map visualization tool for DCS instances.                           |
| **MizEdit**     | `Extension`            | Dynamic mission modifier for presets, trigger injection, script modifications.              |
| **Cloud**       | `Extension`            | Syncs server status and operational telemetry with DCSServerBot cloud services.             |
| **LogAnalyser** | `Extension`            | Scans `dcs.log` in real-time for crashes, script errors, and mod warnings.                  |
| **DSMC**        | `InstallableExtension` | Dynamic Server Mission Campaign state tracking and persistence.                             |
| **DKS**         | `InstallableExtension` | DKS server utilities and mod management.                                                    |
| **HoundTTS**    | `InstallableExtension` | ATIS / automated voice radio broadcast integration.                                         |
| **Lardoon**     | `InstallableExtension` | Tactical data capture and SQLite telemetry recording.                                       |
| **SkyEye**      | `InstallableExtension` | Automated AWACS / GCI bot integration.                                                      |
| **VoiceChat**   | `Extension`            | DCS native Voice Chat configuration and port management.                                    |

---

## Best Practices & Developer Checklist

When developing or modifying an Extension, verify the following:

- [ ] **Class Location & Naming**: Extension placed under `extensions/<name_lower>/extension.py` and exported in `__init__.py`.
- [ ] **Inheritance Choice**: Inherit from `InstallableExtension` if managing third-party downloadable mods/software; otherwise inherit from `core.Extension`.
- [ ] **Cascading Super Calls**: Always call `await super().prepare()`, `await super().startup()`, and `super().shutdown()` to maintain DCS Hook synchronization.
- [ ] **Non-Blocking Execution**: Use `asyncio.to_thread()` or async libraries for disk/process I/O to avoid blocking the bot event loop.
- [ ] **Error Handling**: Raise `InstallException` during `prepare()` if prerequisites are missing, preventing corrupt DCS startup.
- [ ] **Status Embed Support**: Implement `render()` with concise information or mark `hidden: true` in configuration.
- [ ] **Port Declarations**: Implement `get_ports()` if the extension binds network sockets to ensure automatic firewall management.
- [ ] **Configuration Isolation**: Respect `self.config` and avoid hardcoding node-specific paths or instance ports.
