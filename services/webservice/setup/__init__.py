"""The setup console: the front end an UNCONFIGURED install serves before it can be a bot.

Four parts, one job each:

* :mod:`~services.webservice.setup.window` — the one-time code, with its single-use, expiry and
  per-address attempt bookkeeping. Process memory only; never written down.
* :mod:`~services.webservice.setup.app` — ``create_setup_app()``: the gate page, the health probe,
  the accepted session, and the dependency every later route must declare. No identity backend, no
  normal page, no ``/api`` route.
* :mod:`~services.webservice.setup.service` — ``SetupService``: the uvicorn front end that binds the
  loopback interface, prints the code once and opens the URL that carries it.
* :mod:`~services.webservice.setup.boot` — which boot may start any of it, and the refusal for every
  other boot.

The console is built ONLY on a boot where ``main.yaml`` is absent at process start (see
:func:`~services.webservice.setup.boot.should_run_setup`). Everything the normal console owns — its
access gate, its session, its identity backends, its pages, and the bot API — stays exactly as it is
and is unreachable from here.

This package exports nothing: import the submodule you need, so that importing the package itself
has no side effects and a configured install never executes a line of the console.
"""

from __future__ import annotations

__all__: list[str] = []
