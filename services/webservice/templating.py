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

I18N is built in, not bolted on: every environment carries Jinja's own ``jinja2.ext.i18n``
extension and an installed translations object. ``build_environment`` installs
:class:`gettext.NullTranslations` by default (so ``_``/``gettext``/``{% trans %}`` are defined and
return the msgid — wrapping a string is a no-op until a translation exists), and a caller may pass
a real :func:`gettext.translation` for one language. That is how the per-language environments are
built (see :mod:`services.webservice.i18n`): ONE environment per language, each with its own
immutable translations object, so no shared state exists for a render in one language to mutate.

Every environment also carries the ``say`` GLOBAL (:data:`SENTENCE_GLOBAL`): the ONE guarded
interpolation the write dialog (``templates/confirm.html``) renders its per-request sentence records
through. It translates, interpolates, and — on a ``KeyError``/``IndexError``/``ValueError`` from a
malformed catalog line — degrades to the untranslated English sentence instead of raising into a 500
(see :func:`_sentence_global`).
"""
from __future__ import annotations

import gettext
import logging
from pathlib import Path
from typing import Any, Mapping

from jinja2 import (BaseLoader, ChoiceLoader, Environment, FileSystemLoader, StrictUndefined,
                    TemplateNotFound, select_autoescape)

__all__ = ["SHELL_TEMPLATES_DIR", "JINJA_I18N_EXTENSION", "SENTENCE_GLOBAL", "PluginTemplatesLoader",
           "build_environment", "render"]

log = logging.getLogger(__name__)

#: the shell's own templates (shipped in the repo, never under the user's config/ directory)
SHELL_TEMPLATES_DIR = Path(__file__).parent / "templates"

#: Jinja's own translation extension: it makes ``{% trans %}`` and the ``_``/``gettext`` functions
#: available to a template, reading whichever translations object was installed on the environment.
JINJA_I18N_EXTENSION = "jinja2.ext.i18n"

#: The name of the template global ``build_environment`` installs: the GUARDED interpolation of a
#: dialog sentence record (``{"msgid": …, "params": …, "label": …}``). ``confirm.html`` renders every
#: per-request sentence through it (``{{ say(record) }}``) instead of a bare ``.format``.
SENTENCE_GLOBAL = "say"


def _sentence_global(translations):
    """Build the ``say`` template global: one sentence record, translated and interpolated, GUARDED.

    A dialog sentence is a record ``{"msgid": <an ``i18n._``-marked English literal>, "params": {...}}``
    (and, for the console's composed title pattern, a top-level ``label`` that is itself translatable).
    ``confirm.html`` renders every such record through this ONE function — the single place a
    translation meets its placeholders — instead of six inline ``.format`` calls.

    THE GUARD: a translated ``msgstr`` carrying a placeholder the params do not supply (a stray
    ``{extra}``, a positional ``{0}``) makes ``str.format`` raise ``KeyError``/``IndexError``/
    ``ValueError``. Unguarded that error takes the WHOLE page down with a 500 — one bad catalog line
    would break every write dialog in the console. So the interpolation is contained: an error
    degrades to the UNTRANSLATED English sentence (``rec.msgid`` with the same params), which is
    complete and correctly escaped like any other rendered string. A bad catalog line becomes English,
    never an error. The ``label`` (when present) is translated the same way the template used to
    (``_(rec.label)``); on the English fallback it stays the operator's own English word.
    """
    def say(record: Mapping[str, Any]) -> str:
        msgid = record["msgid"]
        label = record.get("label")
        params = dict(record.get("params") or {})
        if label is not None:
            params["label"] = translations.gettext(label)  # type: ignore[attr-defined]
        try:
            return translations.gettext(msgid).format(**params)  # type: ignore[attr-defined]
        except (KeyError, IndexError, ValueError):
            english = dict(record.get("params") or {})
            if label is not None:
                english["label"] = label
            try:
                return msgid.format(**english)
            except (KeyError, IndexError, ValueError):  # pragma: no cover - msgid is source, not data
                return msgid
    return say


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


def _identity_text(text: str) -> str:
    """The ``schema_text`` global of an environment with no bound language: the schema's own text.

    English IS the schema prose (its descriptions are written in it), so an unbound environment is
    correct rather than degraded — the same "the msgid is the answer" rule ``_`` follows.
    """
    return text or ""


def build_environment(registrar=None, *, translations=None, language=None) -> Environment:
    """Build an environment. ``registrar`` supplies the plugin template namespaces.

    ``translations`` is the gettext object installed on the environment's i18n extension. Left
    unset it is :class:`gettext.NullTranslations`, so ``_`` returns the msgid and every wrapped
    string is a no-op — the English-is-the-msgid property the whole migration rests on. A caller
    that passes a language's :func:`gettext.translation` gets THAT language for every render on
    this environment, and nothing outside it: the object is per-environment and never global.

    ``language`` binds the environment's ``schema_text`` global to that language's SCHEMA catalog.
    Left unset the global is the identity, which is English — the same no-op rule, applied to the
    descriptions that live in the schemas instead of the templates.
    """
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
        extensions=[JINJA_I18N_EXTENSION],
    )
    # Installed UNCONDITIONALLY: without a translations object the i18n extension leaves ``_`` and
    # ``{% trans %}`` undefined and every render raises. NullTranslations makes the msgid the answer.
    # (the method is added to Environment by the extension itself, hence the ignore)
    installed = translations or gettext.NullTranslations()
    environment.install_gettext_translations(  # type: ignore[attr-defined]
        installed, newstyle=False)
    # The SCHEMA-prose global. A description lives in the schema, so a template cannot wrap it in
    # ``_(…)`` — the msgid is DATA. ``schema_text`` looks each one up in the SCHEMA catalog of THIS
    # environment's language, so no render can serve another language's prose, and a language without
    # a catalog (or without that entry) answers with the schema's own English text.
    if language is None:
        environment.globals["schema_text"] = _identity_text
    else:
        from . import i18n as i18n_module   # lazy: i18n imports this module for its markers
        environment.globals["schema_text"] = i18n_module.schema_text_for(language).gettext
    # The dialog sentence GUARD (``confirm.html``'s ``say``): ONE place a translation meets its
    # placeholders, degrading a bad catalog line to English rather than raising. It closes over the
    # SAME translations object the environment installed, so it is the environment's language.
    environment.globals[SENTENCE_GLOBAL] = _sentence_global(installed)
    # The config generator's UNIQUE view key (``configgen.view_id``): a regex container shares its
    # ``name`` with a sibling container in the same mapping, so the per-container view maps (rows,
    # customs, elements, choices, sections) are keyed by this id and the control macros look it up
    # with it. Imported LAZILY: ``i18n`` imports this module, so a top-level ``configgen`` import
    # would close a cycle.
    from . import configgen as _configgen
    environment.globals["cfg_id"] = _configgen.view_id
    # The field PATH encoder (``configgen.encode_path``): the "+ Add" picker's removal control posts a
    # field's path ENCODED, exactly as a form field name is (a free-text key's ``.`` rides as ``%2E``),
    # so the save resolves the same document path the page rendered it from.
    environment.globals["cfg_path"] = _configgen.encode_path
    # The BLOCKS an ``any_of`` ENTRY may carry (``configgen.entry_blocks``): a regex container whose
    # element is an exclusive chooser renders one fieldset per allowed block and lets the operator pick,
    # instead of merging the blocks' fields into one row. Empty for every other field.
    environment.globals["cfg_blocks"] = _configgen.entry_blocks
    log.debug("Web UI templates: shell dir '%s', plugin namespaces %s",
              SHELL_TEMPLATES_DIR, sorted(registrar.template_dirs) if registrar else [])
    return environment


def render(registrar, name: str, **context: Any) -> str:
    """Render a template through the shell's environment (used by tests and by the core pages)."""
    environment = getattr(registrar, "environment", None)
    if environment is None:  # pragma: no cover - install_shell always sets it
        raise RuntimeError("the web UI shell was not installed (no template environment)")
    return environment.get_template(name).render(**context)
