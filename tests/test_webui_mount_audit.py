"""A ``Mount`` is an ungated surface, so its appearance must be loud.

``app.mount(...)`` answers 200 for an anonymous request with the access gate never running, so the
shell serves its assets through ``app.frontend()`` -- a ``Mount`` is not a gated surface -- and
contributors receive the registrar, never the app. What was missing is a runtime audit:
``permissions.audit_mounts()`` walks the real route table (the include wrappers and the frontend group
included) and logs an ERROR naming every mount outside the known set -- which is empty, because the
shell mounts nothing. The audit runs at shell install (every app the service builds) and from the gate
whenever the route table changed since the last audit, catching a mount added between install and
first request."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

from conftest import asgi_client, create_app, install_shell, permissions


def _plant_a_mount(app, path: str = "/evilstatic") -> str:
    directory = tempfile.mkdtemp()
    Path(directory, "m.txt").write_text("m", encoding="utf-8")
    app.mount(path, StaticFiles(directory=directory), name="planted")
    return directory


def _errors(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR]


def test_the_audit_reports_a_planted_mount_and_names_its_path(shell_app, caplog):
    app, _registrar = shell_app
    _plant_a_mount(app, "/evilstatic")
    with caplog.at_level(logging.ERROR):
        offenders = permissions.audit_mounts(app)
    assert offenders == ["/evilstatic"]
    assert any("evilstatic" in message for message in _errors(caplog)), \
        f"the audit must name the offending path, got {_errors(caplog)}"


def test_the_shell_itself_has_nothing_to_report(shell_app, caplog):
    """Silence is the healthy state: the shell owns its assets through `app.frontend()`, so a
    correct install logs no mount error at all."""
    app, _registrar = shell_app
    with caplog.at_level(logging.ERROR):
        assert permissions.audit_mounts(app) == []
    assert _errors(caplog) == []


def test_a_mount_inside_an_included_router_is_found(shell_app):
    """`include_router` puts one wrapper in `app.routes` and keeps the router's own routes inside
    it, so the walk has to open the wrapper — otherwise the audit is blind exactly where a mount
    is easiest to hide."""
    app, _registrar = shell_app
    directory = tempfile.mkdtemp()
    router = APIRouter()
    router.routes.append(Mount("/nested", app=StaticFiles(directory=directory)))
    app.include_router(router)
    assert permissions.audit_mounts(app) == ["/nested"]


def test_installing_the_shell_audits_mounts(caplog):
    """The startup call site: a mount that exists before the shell is installed is reported."""
    app = create_app()
    _plant_a_mount(app, "/before-install")
    with caplog.at_level(logging.ERROR):
        install_shell(app, node=None, config={})
    assert any("before-install" in message for message in _errors(caplog)), \
        f"install_shell() must audit mounts, got {_errors(caplog)}"


@pytest.mark.asyncio
async def test_the_first_request_audits_mounts_added_after_the_install(shell_app, caplog):
    """The log-time call site: a mount added between the install and the first request is the one
    a startup-only audit would miss."""
    app, _registrar = shell_app
    _plant_a_mount(app, "/after-install")
    with caplog.at_level(logging.ERROR):
        async with asgi_client(app) as client:
            assert (await client.get("/static/shell.css")).status_code == 200
    assert any("after-install" in message for message in _errors(caplog)), \
        f"the gate must audit mounts once per app, got {_errors(caplog)}"


@pytest.mark.asyncio
async def test_a_mount_added_after_the_first_request_is_still_audited(shell_app, caplog):
    """The registration window a one-shot "first request" audit leaves open.

    The plugin that holds the app object registers its routes *after* the service starts — the
    RestAPI plugin waits for the bot to be ready before it touches the app — so a mount can appear
    after the first request has already been audited. The audit therefore re-runs whenever the route
    table has changed, not only for the first request, or that mount is silently reachable forever.
    """
    app, _registrar = shell_app
    async with asgi_client(app) as client:
        assert (await client.get("/static/shell.css")).status_code == 200      # the audit ran
        _plant_a_mount(app, "/added-later")
        with caplog.at_level(logging.ERROR):
            assert (await client.get("/static/shell.css")).status_code == 200  # a later request
    assert any("added-later" in message for message in _errors(caplog)), \
        f"a mount added after the first request must still be loud, got {_errors(caplog)}"


def test_a_mount_inside_the_known_set_is_not_reported(shell_app, caplog, monkeypatch):
    """The filter's contract, in both directions at once: a declared mount is silent and an
    undeclared one planted in the same app is still named. `EXPECTED_MOUNTS` declares the one mount
    the install genuinely intends (``/mcp``), so this pin replaces the table to exercise both
    branches on mounts of its own."""
    app, _registrar = shell_app
    monkeypatch.setattr(permissions, "EXPECTED_MOUNTS", ("/known",))
    _plant_a_mount(app, "/known")
    _plant_a_mount(app, "/unknown")
    with caplog.at_level(logging.ERROR):
        offenders = permissions.audit_mounts(app)
    assert offenders == ["/unknown"], offenders
    assert any("'/unknown'" in message for message in _errors(caplog))
    assert not any("'/known'" in message for message in _errors(caplog)), \
        "a declared mount must not be named"


@pytest.mark.asyncio
async def test_the_gate_stays_quiet_for_the_shells_own_repeated_requests(shell_app, caplog):
    """Silence is the healthy state AT THE GATE'S CALL SITE too, not only for a direct call: the
    shell owns its assets through `app.frontend`, so requests over a clean app log no mount error —
    including the second and third request, which is where a re-auditing gate could go noisy."""
    app, _registrar = shell_app
    with caplog.at_level(logging.ERROR):
        async with asgi_client(app) as client:
            for _ in range(3):
                assert (await client.get("/static/shell.css")).status_code == 200
    assert _errors(caplog) == []
