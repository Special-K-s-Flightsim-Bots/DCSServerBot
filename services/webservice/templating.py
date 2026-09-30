"""One Jinja2 environment for the whole admin web UI.

``Jinja2Templates(directory=...)`` builds a ``FileSystemLoader`` for a single directory and
therefore cannot serve plugin directories at all; two instances would mean two environments with
two sets of globals/filters, which diverges silently. So there is exactly ONE
:class:`~jinja2.Environment`, whose loader is a :class:`~jinja2.ChoiceLoader` over

* the shell's own ``templates/`` directory, and
* one namespaced loader for the directories plugins registered with the registrar.

A plugin template is addressed as ``<owner>/<path>`` (``userstats/dashboard.html``). Namespacing
is what stops two plugins' ``index.html`` from shadowing each other — with plain
``FileSystemLoader``s in a ``ChoiceLoader`` the first one wins for every plugin, silently.

``undefined`` is :class:`~jinja2.StrictUndefined`: a missing variable is a hard error. Rendering
it as ``''`` is how a CSRF field ships blank to every user while the tests stay green.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from jinja2 import (BaseLoader, ChoiceLoader, Environment, FileSystemLoader, StrictUndefined,
                    TemplateNotFound, select_autoescape)

__all__ = ["SHELL_TEMPLATES_DIR", "PluginTemplatesLoader", "build_environment", "render"]

log = logging.getLogger(__name__)

#: the shell's own templates (shipped in the repo, never under the user's config/ directory)
SHELL_TEMPLATES_DIR = Path(__file__).parent / "templates"


class PluginTemplatesLoader(BaseLoader):
    """Loads ``<namespace>/<template>`` from the directories the registrar holds.

    The mapping is read on every lookup, so a plugin that registers later (or after a reload) is
    served without rebuilding the environment."""

    def __init__(self, directories: Mapping[str, Path]):
        self._directories = directories

    def get_source(self, environment: Environment, template: str):
        namespace, sep, rest = template.partition("/")
        if not sep or not rest:
            raise TemplateNotFound(template)
        directory = self._directories.get(namespace)
        if directory is None:
            raise TemplateNotFound(template)
        loader = FileSystemLoader(str(directory))
        return loader.get_source(environment, rest)


def build_environment(registrar=None) -> Environment:
    """Build the single environment. ``registrar`` supplies the plugin template namespaces."""
    loaders: list[BaseLoader] = [FileSystemLoader(str(SHELL_TEMPLATES_DIR))]
    if registrar is not None:
        loaders.append(PluginTemplatesLoader(registrar.template_dirs))
    environment = Environment(
        loader=ChoiceLoader(loaders),
        undefined=StrictUndefined,
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
        auto_reload=True,
    )
    log.debug("Web UI templates: shell dir '%s', plugin namespaces %s",
              SHELL_TEMPLATES_DIR, sorted(registrar.template_dirs) if registrar else [])
    return environment


def render(registrar, name: str, **context: Any) -> str:
    """Render a template through the shell's environment (used by tests and by C3's pages)."""
    environment = getattr(registrar, "environment", None)
    if environment is None:  # pragma: no cover - install_shell always sets it
        raise RuntimeError("the web UI shell was not installed (no template environment)")
    return environment.get_template(name).render(**context)
