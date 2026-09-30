"""The plugin-page contributor registry: what a rebuilt application replays (review defect D1).

WHY THIS EXISTS. `PluginManager` registers a plugin's web UI pages by calling
``plugins/<name>/webui.py::register_pages(registrar)``. That call needs a registrar, and a registrar
only exists while a `WebService` application does — so registering *into the live registrar* is not
enough, because the application object is replaced at runtime:

* ``WebService.stop()`` sets ``self.app``/``self.ui`` to ``None`` and ``start()`` builds a *fresh*
  application with a *fresh*, empty registrar (a master/agent switch stops and re-starts master-only
  services);
* on a fresh install the service configuration (and therefore the WebService) can appear *after*
  ``load_plugins()`` has already run, so at plugin-load time there is no registrar at all.

In both cases a registrar exists, with no plugin pages on it, and nothing calls the loader again —
every plugin page is silently missing, with no error anywhere.

THE FIX, AND WHY IT IS A REGISTRY RATHER THAN A RELOAD CALL. `PluginManager` *records* the
contributors when it loads the plugins — before it looks for a registrar — and `install_shell()`
replays them onto whatever registrar it just built. A ``reload_webui_pages()`` hook called from
``WebService.start()`` would only fix the first case: it needs a live `PluginManager`, which lives on
the bot object (``BotService.bot`` is legitimately ``None`` early and after a takeover), and it does
nothing for the install where the service starts after the plugin load. The registry fixes both, and
it keeps the replay independent of who is running the bot.

ORDERING IS THE POINT: recording must not depend on a registrar existing. `set_contributors()` is
called with the full set of loaded plugins on every load (replace, never merge, so an unloaded
plugin cannot linger) and the replay happens later, whenever a registrar appears.
"""
from __future__ import annotations

import logging
from typing import Callable, Mapping, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the module import-cheap
    from .registry import Registrar

__all__ = ["PageContributor", "set_contributors", "unregister_contributor", "contributors", "replay"]

log = logging.getLogger(__name__)

#: ``register_pages(registrar)`` — what ``plugins/<name>/webui.py`` exposes
PageContributor = Callable[["Registrar"], None]

#: owner (plugin name) -> its registration function. Process state on purpose: it is what makes a
#: fresh application object able to replay what the plugins registered into the previous one.
_CONTRIBUTORS: dict[str, PageContributor] = {}


def set_contributors(found: Mapping[str, PageContributor]) -> None:
    """Replace the registry with *found* (the plugins loaded right now).

    Replace rather than merge: the caller passes the complete set for this load, so a plugin that
    was unloaded — or dropped from the configuration — cannot linger and be replayed into the next
    application object.
    """
    _CONTRIBUTORS.clear()
    for owner, register_pages in found.items():
        if callable(register_pages):
            _CONTRIBUTORS[owner] = register_pages
    if _CONTRIBUTORS:
        log.debug("Web UI contributors recorded: %s", ", ".join(sorted(_CONTRIBUTORS)))


def unregister_contributor(owner: str) -> bool:
    """Drop one contributor (the plugin is being unloaded). Returns whether it existed."""
    return _CONTRIBUTORS.pop(owner, None) is not None


def contributors() -> dict[str, PageContributor]:
    """A copy of the registry, for the replay and for tests/logging."""
    return dict(_CONTRIBUTORS)


def replay(registrar: "Registrar") -> list[str]:
    """Register every recorded contributor on *registrar*; return the owners that were registered.

    Skips an owner that *this* registrar already holds, so replaying twice on one application is a
    no-op instead of the registrar's "already registered" refusal. A contributor that raises is
    logged and skipped: one broken plugin must not stop the shell (and therefore the bot) from
    starting, exactly as `PluginManager` treats it on the live path.
    """
    registered: list[str] = []
    for owner, register_pages in _CONTRIBUTORS.items():
        if owner in registrar.registrations:
            continue
        try:
            register_pages(registrar)
        except Exception as ex:
            log.error("Web UI contributor '%s' could not register its pages on a fresh "
                      "application: %s", owner, ex, exc_info=True)
            continue
        registered.append(owner)
    if registered:
        log.info("Admin web shell: replayed %d plugin page registration(s): %s",
                 len(registered), ", ".join(registered))
    return registered
