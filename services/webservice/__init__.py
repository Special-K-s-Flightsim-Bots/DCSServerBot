"""The DCSServerBot admin web service: package marker with LAZY exports.

Importing this package must stay safe for any caller, because the modules under
``services/webservice`` are also entry points:

* ``services/webservice/auth/local.py`` is runnable (``python -m services.webservice.auth.local
  --hash``) and imports ``services.webservice, services.webservice.auth`` on its way in;
* ``session.py``, ``templating.py`` and ``registry.py`` import nothing but FastAPI/Jinja2/Starlette.
  That is the whole list of light modules: the ``auth`` package, ``scope.py`` and ``readmodels``
  reach the bot's ``core`` (see the paragraph after the next one).

An eager ``from .service import WebService`` here executed ``service.py`` -> the bot's ``core`` ->
``core/commandline.py``, whose module-level parser ran on the REAL ``sys.argv`` at import time.
Under ``python -m services.webservice.auth.local --hash ...`` Python's runpy imports this parent
package while ``sys.argv[0]`` is still ``'-m'``, so that parser used to reject the CLI's flags and
exit before ``main()`` ran::

    usage: -m [-h] [-c CONFIG]
    -m: error: unrecognized arguments: --hash ...

The exports are therefore resolved on first attribute access (PEP 562), so
``from services.webservice import WebService`` keeps working for the bot and plugins without
executing ``service.py`` -- uvicorn, and the bot's ``core`` with it -- on the way in. This module's
laziness still has a reason of its own: it keeps ``service.py`` out of the import path of every
caller that only wants the FastAPI/Jinja2 helpers.

THE CONSOLE MAY USE THE BOT'S LIBRARIES. It runs in the bot's OWN environment (the same venv as the
bot), so a web module importing ``core`` is allowed, and that is what happens today: ``scope.py``
imports the shared ``managed_by`` rule from ``core/utils/discord.py`` -- the rule LIVES there and the
console CONSUMES it, one rule serving both, pinned by ``tests/test_layering_direction.py`` -- and
every importer of ``..scope`` (the whole ``auth`` package) plus those importing them
(``readmodels/source.py``) reach ``core`` with it. The lighter promise this package once carried (an
interpreter with neither psycopg nor lupa, a CLI that "runs without a bot") was given up
deliberately: the console needs the bot's core anyway, and one shared rule beats two copies.

WHAT MAKES THE DOCUMENTED CLI WORK, pinned as BEHAVIOUR rather than as an import graph: a foreign
program's argv is never strict-parsed (``core/commandline.py`` validates strictly only for the bot's
OWN entry points and takes its defaults for anything else, so ``-m``/``uvicorn``/a module path
cannot be killed by the bot's parser), and ``auth/local.py`` additionally detaches its own argv
above its imports. ``tests/test_webui_auth_cli.py`` runs the documented command with the bot's real
parser entering that chain, and asserts that importing this package leaves the host process's argv
alone.
"""
from __future__ import annotations

__all__ = [
    "WebService",
    "WebServiceBase",
]

_LAZY_EXPORTS = frozenset(__all__)


def __getattr__(name: str):
    # Resolved on first access, never at import: see the module docstring.
    if name == "WebService":
        from .service import WebService
        return WebService
    if name == "WebServiceBase":
        from .base import WebServiceBase
        return WebServiceBase
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _LAZY_EXPORTS)
