"""Service discovery must not depend on a package ``__init__`` import side effect.

``services/webservice/__init__.py`` was made import-safe, which removed the eager ``from .service
import WebService``. ``core/services/registry.py`` then found nothing, because the walk imported
only the entries ``pkgutil.walk_packages`` reports as packages; every other service re-exported its
class from its own ``__init__``, so the decorator had run only by that convention. Importing the
package no longer executed ``service.py``, the registrar never ran, and the console silently did not
come up.

The fix imports every module the walk yields. NOTE FOR A FUTURE EDITOR: do not "optimise" the walker
back to packages-only, and do not put a service's registration behind a package-level import side
effect. The walk is observed in a subprocess (``tests/_service_discovery_probe.py``) because ``core``
is unimportable without psycopg/lupa; where ``core`` imports cleanly the tests check all twelve
service packages."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE = Path(__file__).resolve().parent / "_service_discovery_probe.py"
TIMEOUT = 120

#: every ``services/<pkg>/service.py`` a bot install has. If one is not requested, its package must
#: be reported unimportable in this environment -- that is the whole point of the pairing.
SERVICE_PACKAGES = (
    "services.backup",
    "services.bot",
    "services.cleanup",
    "services.cron",
    "services.dashboard",
    "services.firewall",
    "services.mcpservice",
    "services.modmanager",
    "services.monitoring",
    "services.music",
    "services.servicebus",
    "services.ups",
    "services.webservice",
)


def _diagnosis(proc: subprocess.CompletedProcess) -> str:
    return f"exit={proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"


@pytest.fixture(scope="module")
def discovery() -> dict:
    """Run the discovery walk once, in its own interpreter, and return what it saw."""
    proc = subprocess.run([sys.executable, str(PROBE)], cwd=REPO_ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT)
    assert proc.returncode == 0, _diagnosis(proc)
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
 # Anti-vacuity: a probe that recorded nothing would satisfy "nothing is missing".
    assert payload["requested"], f"the probe recorded no import requests at all: {payload}"
    return {"yielded": set(payload["yielded"]),
            "requested": set(payload["requested"]),
            "unimportable": set(payload["unimportable"])}


# --------------------------------------------------------------------------- (a) the regression

def test_discovery_requests_every_module_the_walk_yields(discovery: dict) -> None:
    """Every module ``pkgutil.walk_packages`` yields must reach ``importlib.import_module``."""
    yielded, requested = discovery["yielded"], discovery["requested"]

 # Floor: the walk must actually have produced modules, or the "nothing missing" below is a
 # statement about an empty set.
    assert yielded, "the walk yielded no modules at all -- the probe is broken"
    assert "services.webservice.service" in yielded, (
        "the walk did not even reach services/webservice/service.py; the probe is measuring "
        f"something else: {sorted(yielded)}")

    missing = sorted(yielded - requested)
    assert not missing, (
        "discovery never imports these modules, so a service registered only by importing them "
        f"can never start: {missing}")


def test_discovery_requests_the_webservice_service_module(discovery: dict) -> None:
    """The exact regression: ``WebService`` is registered by importing ``services.webservice.service``.

    ``services/webservice/__init__.py`` is (correctly) lazy, so nothing else imports that module on
    a normal install -- if the walker does not, ``WebService`` is never registered and the console
    never starts, silently.
    """
    assert "services.webservice.service" in discovery["requested"], (
        "services.webservice.service is not requested by dynamic_import('services'), so importing "
        "services.webservice no longer registers WebService (services/webservice/__init__.py is "
        "lazy) and the admin web console silently never starts")


def test_every_service_package_service_module_is_requested_or_its_package_is_unimportable(
        discovery: dict) -> None:
    """Each service package must have its ``service`` module requested -- unless its package could
    not be imported here (recorded, with its reason, in ``unimportable``)."""
    requested, unimportable = discovery["requested"], discovery["unimportable"]

    unexplained = [pkg for pkg in SERVICE_PACKAGES
                   if f"{pkg}.service" not in requested and pkg not in unimportable]
    assert not unexplained, (
        "these service packages import cleanly here, yet discovery never requests their service "
        f"module: {unexplained}")


def test_discovery_still_requests_the_packages_themselves(discovery: dict) -> None:
    """The walk must keep importing packages: their ``__init__`` is still where other services
    import their class (and where a package without a ``service`` module registers anything)."""
    requested = discovery["requested"]
    assert "services" in requested, "the top-level 'services' package was never imported"
    assert "services.backup" in requested, (
        "packages are no longer imported by discovery -- the module walk must not have replaced it")


# --------------------------------------------------------------------------- (b) registration

def test_webservice_is_registered_and_master_only() -> None:
    """After ``dynamic_import('services')``, ``WebService`` is in the registry and master-only.

    SKIPS on a box where ``core`` cannot be imported (this one: psycopg/lupa are missing), because
    the registry and ``services/webservice/service.py`` both import ``core``. It runs on the
    deployment -- which is where the console actually starts.
    """
    try:
        from core.services.base import Service
        from core.services.registry import ServiceRegistry
        from core.utils.helper import dynamic_import
    except Exception as ex:  # pragma: no cover - environment-dependent
        pytest.skip(f"core is unimportable in this environment ({type(ex).__name__}: {ex}); "
                    "this test needs the real registry. It runs where the bot runs.")

    dynamic_import("services")

    registered = ServiceRegistry.services()
    webservice = next((key for key in registered if key.__name__ == "WebService"), None)
    assert webservice is not None, (
        "WebService is not registered after dynamic_import('services') -- the admin web console "
        f"will never start. Registered services: {sorted(k.__name__ for k in registered)}")
    assert issubclass(webservice, Service), f"{webservice!r} is not a Service subclass"
    assert ServiceRegistry.master_only(webservice) is True, (
        "WebService was registered without master_only=True; it would be offered on agents too")
