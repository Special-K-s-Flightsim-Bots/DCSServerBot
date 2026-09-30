from __future__ import annotations

import asyncio
import importlib
import logging
import os
from typing import TYPE_CHECKING, Type

from core import utils
from core.const import DEFAULT_TAG
from core.listener import EventListener
from core.plugin import Plugin

if TYPE_CHECKING:
    from core import NodeImpl
    from services.servicebus import ServiceBus

__all__ = ["PluginManager"]

logger = logging.getLogger(__name__)


class PluginManager:
    """Manages plugin loading on both master and agent nodes.

    On the master node, plugins are loaded as Discord cogs (with commands) and
    their event listeners are registered with the local ServiceBus.

    On agent nodes, only the event listeners are registered with the local
    ServiceBus — no Discord bot or commands are needed. Any Discord interactions
    from event listeners are automatically proxied to the master node via the
    intercom channel.
    """

    def __init__(self, node: NodeImpl):
        self.node = node
        self.log = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
        self._plugins: list[str] | None = None
        self._eventlisteners: list[EventListener] = []

    @property
    def plugins(self) -> list[str]:
        """The plugins loaded on this node.

        Before :meth:`load_plugins` ran, this falls back to the node's *configured* plugin list
        rather than to an empty one: `MCPService` builds its own `PluginManager` and reads
        ``.plugins`` to discover plugin actions, and an empty list there silently registers zero
        tools (the plugin layer would look wired while contributing nothing).
        """
        if self._plugins is not None:
            return list(self._plugins)
        return list(getattr(self.node, 'plugins', None) or [])

    async def load_plugins(self, plugin_list: list[str]) -> None:
        """Load the given list of plugins on this node.

        On master: loads full plugins as Discord cogs via BotService, and registers the plugins'
        admin web UI pages (if a WebService is running on this node).
        On agents: instantiates plugin event listeners only and registers them
                   with the local ServiceBus.

        Safe to call multiple times (e.g. during master/agent switches) —
        will unload previously loaded plugins first.
        """
        # Unload any previously loaded plugins first
        if self._eventlisteners:
            await self.unload_plugins()
        self._plugins = [plugin.lower() for plugin in plugin_list]
        if self.node.master:
            await self._load_plugins_master()
        else:
            await self._load_plugins_agent()
        await self._load_webui_pages()

    async def _load_plugins_master(self) -> None:
        """On the master, plugins are loaded by BotService.setup_hook().

        We just log — BotService handles everything via load_plugin() which
        loads the cog, and the cog's cog_load() registers the event listener.
        """
        self.log.info("- PluginManager: Master node — plugins loaded by BotService.")

    async def _load_plugins_agent(self) -> None:
        """On agent nodes, load plugin event listeners without Discord cogs.

        For each plugin, we:
        1. Import the plugin's commands module to get the Plugin class
        2. Import the plugin's listener module to get the EventListener class
        3. Create a lightweight plugin wrapper (no Discord bot needed)
        4. Instantiate the event listener and register it with ServiceBus
        """
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        bus = ServiceRegistry.get(ServiceBus)
        if not bus:
            self.log.error("ServiceBus not available, cannot load plugin event listeners.")
            return

        for plugin_name in self.plugins:
            try:
                await self._load_plugin_agent(plugin_name, bus)
            except Exception as ex:
                self.log.error(f"  - Failed to load plugin '{plugin_name}' on agent: {ex}",
                               exc_info=True)

    async def _load_plugin_agent(self, plugin_name: str, bus: ServiceBus) -> None:
        """Load a single plugin's event listener on an agent node."""
        # Import the plugin's commands module to get the Plugin class
        try:
            commands_mod = importlib.import_module(f"plugins.{plugin_name}.commands")
        except ModuleNotFoundError:
            self.log.debug(f"  - Plugin '{plugin_name}' has no commands module, skipping.")
            return

        PluginClass = None
        plugin_name_title = plugin_name.title()
        for attr_name in dir(commands_mod):
            if attr_name.lower() == plugin_name_title.lower():
                attr = getattr(commands_mod, attr_name)
                if (isinstance(attr, type) and
                        issubclass(attr, Plugin) and
                        attr is not Plugin and
                        attr.__module__ == commands_mod.__name__):
                    PluginClass = attr
                    break

        if PluginClass is None:
            self.log.debug(f"  - Plugin '{plugin_name}' has no Plugin class, skipping.")
            return

        # Import the plugin's listener module
        try:
            listener_mod = importlib.import_module(f"plugins.{plugin_name}.listener")
        except ModuleNotFoundError:
            self.log.debug(f"  - Plugin '{plugin_name}' has no listener module, skipping.")
            return

        EventListenerClass = None
        el_name = f"{plugin_name_title}EventListener"
        for attr_name in dir(listener_mod):
            if attr_name == el_name or attr_name.endswith("EventListener"):
                attr = getattr(listener_mod, attr_name)
                if (isinstance(attr, type) and
                        issubclass(attr, EventListener) and
                        attr is not EventListener and
                        attr.__module__ == listener_mod.__name__):
                    EventListenerClass = attr
                    break

        if EventListenerClass is None:
            self.log.debug(f"  - Plugin '{plugin_name}' has no EventListener class, skipping.")
            return

        # Create a lightweight plugin wrapper for the agent
        plugin_wrapper = _AgentPluginWrapper(
            node=self.node,
            plugin_name=plugin_name,
        )

        # Instantiate the event listener
        eventlistener = EventListenerClass(plugin_wrapper)
        bus.register_eventListener(eventlistener)
        self._eventlisteners.append(eventlistener)
        self.log.info(f"  - Plugin '{plugin_name}' event listener loaded on agent.")

    async def unload_plugins(self) -> None:
        """Unload all loaded plugins."""
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        # the plugins' web UI pages go first: a page whose plugin is gone must not keep answering
        # (the stale-route problem - on the pinned FastAPI a re-registration does not replace it)
        self._unload_webui_pages()

        bus = ServiceRegistry.get(ServiceBus)
        if bus:
            for listener in self._eventlisteners:
                try:
                    await listener.shutdown()
                    bus.unregister_eventListener(listener)
                except Exception as ex:
                    self.log.error(f"Error unloading event listener: {ex}", exc_info=True)
        self._eventlisteners.clear()

    # ------------------------------------------------------------------ plugin web UI pages

    def _get_webui_registrar(self):
        """The admin web UI registrar, or None when no WebService is running on this node.

        Resolved per call (the service can be stopped and started, and a master/agent switch
        rebuilds the application object, so a cached reference would go stale)."""
        try:
            from core.services.registry import ServiceRegistry
            from services.webservice.service import WebService
        except Exception:
            self.log.debug("WebService not importable, skipping plugin web UI registration.")
            return None
        service = ServiceRegistry.get(WebService)
        # a master_only service can also come back as a ServiceProxy: a proxy is not a registrar
        if service is None or not isinstance(service, WebService):
            return None
        return getattr(service, 'ui', None)

    def _webui_contributors(self) -> dict:
        """``{plugin_name: register_pages}`` for every loaded plugin that ships a ``webui.py``.

        Importing the module is what makes a plugin's capability declarations happen at load time;
        the returned function is what a registrar (this one or a later, rebuilt one) is handed.
        """
        found: dict = {}
        for plugin_name in self.plugins:
            module = self._import_webui(plugin_name)
            if module is None:
                continue
            register_pages = getattr(module, 'register_pages', None)
            if not callable(register_pages):
                self.log.debug(f"  - Plugin '{plugin_name}' has a webui.py without register_pages().")
                continue
            found[plugin_name] = register_pages
        return found

    async def _load_webui_pages(self) -> None:
        """Let every loaded plugin register its admin web UI pages.

        A plugin opts in by shipping ``plugins/<name>/webui.py`` with a module-level
        ``register_pages(ui)``; the plugin never receives the FastAPI app, only the registrar, so
        it can declare pages/nav/capabilities and nothing else. A missing module is not an error
        (most plugins have no pages yet); a module that raises is reported and skipped so one
        broken plugin cannot stop the others or the bot.

        RECORD FIRST, REGISTER SECOND (review defect D1). The contributors are recorded *before* a
        registrar is looked up, so this works in all three orders of "plugins load" and "the web
        service starts": a WebService that starts later (its config appeared afterwards, or it was
        restarted by a master/agent switch) rebuilds its application object, and
        ``install_shell()`` replays the recorded contributors onto the fresh registrar. Without
        that, registering into the live registrar only is enough to lose every plugin page
        silently on the first restart.
        """
        from services.webservice import contributors as webui_contributors

        webui_contributors.set_contributors(self._webui_contributors())

        registrar = self._get_webui_registrar()
        if registrar is None:
            self.log.debug("  - No admin web UI on this node: %d plugin page registration(s) "
                           "recorded and replayed when one starts.",
                           len(webui_contributors.contributors()))
            return
        for plugin_name, register_pages in webui_contributors.contributors().items():
            try:
                register_pages(registrar)
                self.log.info(f"  - Plugin '{plugin_name}' registered its admin web UI pages.")
            except Exception as ex:
                self.log.error(f"  - Plugin '{plugin_name}' could not register its admin web UI "
                               f"pages: {ex}", exc_info=True)

    def _unload_webui_pages(self) -> None:
        from services.webservice import contributors as webui_contributors

        for plugin_name in self.plugins:
            # drop the record as well as the routes: a rebuilt application replays the registry, so
            # an unloaded plugin would otherwise come back on the next stop()/start()
            webui_contributors.unregister_contributor(plugin_name)
        registrar = self._get_webui_registrar()
        if registrar is None:
            return
        for plugin_name in self.plugins:
            if registrar.unregister_owner(plugin_name):
                self.log.info(f"  - Plugin '{plugin_name}' unregistered its admin web UI pages.")

    @staticmethod
    def _import_webui(plugin_name: str):
        try:
            return importlib.import_module(f"plugins.{plugin_name}.webui")
        except ModuleNotFoundError:
            return None
        except Exception as ex:
            logger.error(f"Plugin '{plugin_name}': importing its webui.py failed: {ex}",
                         exc_info=True)
            return None


class _AgentPluginWrapper:
    """Lightweight plugin wrapper for agent nodes.

    Provides the same interface that EventListener expects from a Plugin,
    but without a Discord bot. Discord operations are proxied to the master
    via the ServiceBus intercom channel.
    """

    def __init__(self, node: NodeImpl, plugin_name: str):
        self.node = node
        self.plugin_name = plugin_name
        self.log = logging.getLogger(f"plugins.{plugin_name}")
        self.pool = node.pool
        self.apool = node.apool
        self.loop = asyncio.get_event_loop()
        self.locals = self._read_locals()
        self._config: dict[str, dict] = {}

    def _read_locals(self) -> dict:
        """Read plugin configuration from YAML (same logic as Plugin.read_locals)."""
        from ruamel.yaml import YAML
        yaml = YAML()
        from pathlib import Path

        config_dir = self.node.config_dir
        plugin_name = self.plugin_name
        new_file = os.path.join(config_dir, 'plugins', f'{plugin_name}.yaml')

        if os.path.exists(new_file):
            filename = new_file
        elif os.path.exists(f'./plugins/{plugin_name}/config/config.yaml'):
            filename = f'./plugins/{plugin_name}/config/config.yaml'
        else:
            return {}

        try:
            return yaml.load(Path(filename).read_text(encoding='utf-8'))
        except Exception as ex:
            self.log.warning(f"Could not read plugin config: {ex}")
            return {}

    def get_config(self, server=None, *, plugin_name: str | None = None,
                   use_cache: bool | None = True) -> dict:
        """Get plugin configuration (simplified version for agent)."""
        if not server:
            return self.locals.get(DEFAULT_TAG, {})
        default = dict(self.locals.get(DEFAULT_TAG, {}))
        specific = self.locals.get(server.node.name, {}).get(server.instance.name, {})
        return utils.deep_merge(default, specific)
