"""PRE-PAY THE FIRST RENDER — parse the schemas and run the notice checks once, off the request path.

WHY
---
The first page after a boot is slow because it pays the parse: a node page reads its schema set (21
YAML files, ~380 ms on the shipped tree) and validates ``nodes.yaml`` for its notice (~270 ms), and the
settings area reads one set per config file it lists. Measured here: **781 ms for the first node click,
39 ms for every one after** — the caches (``configgen.load_schema``, ``validation.validate_cached``)
make a repeat cheap, but somebody has to be first.

So the service asks for the very same things ONCE, shortly after it starts, from a thread (see
``service.py``): the caches are warm before an operator clicks, and the first click costs what the rest
cost. Nothing here is new logic — this module calls exactly what the pages call, on the same file sets
the pages derive (:func:`services.webservice.pages.settings.discover_files` is the ONE place the file
list is decided).

WHAT IT DOES NOT DO
-------------------
It never invents a file list, never writes anything, and never starts a request. A config that is
INVALID validates to a refusal the page will draw as a notice: that is a normal outcome here, not an
error, and it is counted rather than raised.
"""
from __future__ import annotations

import time
from pathlib import Path

from core.utils import configwrite

from . import configgen, templating, validation
from .pages import node_settings as node_page
from .pages import settings as settings_page

__all__ = ["warm"]


def _check(path: Path, schemas: list[str]) -> bool:
    """Run one file's notice check, exactly as a render would — True when it was asked for.

    A refusal (``ConfigValidationError``) is the ANSWER for a broken file, not a failure: the page
    will show it, so it is counted and not raised. Any other error is a real problem with this one
    file — it is reported as not warmed, and the render will hit it (and log it) as before.
    """
    try:
        validation.validate_cached(path, schemas)
    except configwrite.ConfigValidationError:
        return True
    except Exception:                                    # pragma: no cover - a page bug, not ours
        return False
    return True


def warm(config_dir: Path | str, environment=None) -> tuple[int, int, int, float]:
    """Warm every schema, notice check and template a page will ask for, and report what was done.

    Returns ``(schemas_parsed, files_checked, templates_compiled, seconds)``. The GLOBAL files come from
    the settings area's own discovery (main, services, plugins — same order, same schema sets), and the
    node page's set is added explicitly because that page derives its files itself (``nodes.yaml``
    against the node schema set).

    ``environment`` is the shell's Jinja environment (:meth:`render` reads it off the registrar). Jinja
    compiles a template the first time it is used and keeps the result, so the FIRST render of each page
    pays that compile — measured inside the first click. Compiling them here moves it off the request.
    """
    started = time.perf_counter()
    config_dir = Path(config_dir)
    parsed = checked = templates = 0

    for entry in settings_page.discover_files(config_dir):
        if entry.primary_schema:
            configgen.load_schema(entry.primary_schema, schema_files=list(entry.schema_files))
            parsed += 1
        if entry.schema_files and entry.path.is_file():
            checked += 1 if _check(entry.path, list(entry.schema_files)) else 0

    node_schemas = list(configgen.schema_files_for(node_page.NODES_SCHEMA_PATH))
    if node_schemas:
        configgen.load_schema(node_page.NODES_SCHEMA_PATH, schema_files=node_schemas)
        parsed += 1
        nodes_yaml = config_dir / node_page.NODES_YAML_NAME
        if nodes_yaml.is_file():
            checked += 1 if _check(nodes_yaml, node_schemas) else 0

    if environment is not None:
        # the SHELL's own templates, from the directory its loader reads (``FileSystemLoader`` names a
        # template by its path under that root). NOT ``environment.list_templates()``: the shell's
        # ``ChoiceLoader`` includes the plugin-template loader, which cannot enumerate — and a plugin
        # page pays its own compile. A template that will not compile is skipped here and fails, as it
        # should, on the page that uses it.
        for path in sorted(templating.SHELL_TEMPLATES_DIR.rglob("*.html")):
            name = path.relative_to(templating.SHELL_TEMPLATES_DIR).as_posix()
            try:
                environment.get_template(name)
            except Exception:
                continue
            templates += 1

    return parsed, checked, templates, time.perf_counter() - started
