"""THE STARTUP WARM-UP — the FIRST page after a boot must not pay the whole schema parse.

The caches (``configgen.load_schema`` / ``validation.validate_cached``) make a repeat render cheap, but
somebody is still first: measured on the shipped tree a cold node click took 781 ms against 39 ms warm.
``services/webservice/warmup.py`` therefore asks for exactly what a page will ask for, once, right after
the service starts — and ``service.py`` dispatches it in a THREAD so the boot path is untouched.

Pinned here:
1. after a warm-up a node render parses NOTHING and validates NOTHING (the two counters stay at zero);
2. a second warm-up parses nothing either — it is all cache hits;
3. a config that is INVALID is counted, never raised (its refusal is the notice the page will draw);
4. a warm-up that blows up never reaches the boot path — it is logged and dropped;
5. the dispatch is a THREAD (``asyncio.to_thread``), not inline work on the event loop.
"""
from __future__ import annotations

import asyncio
import logging
import types

import pytest

from conftest import asgi_client
from core.utils import configwrite
from services.webservice import configgen, warmup
from services.webservice.service import WebService
from test_webui_dashboard import _build, _hash, _login
from test_webui_settings_node_scope import BOT_YAML, NODES_YAML, SERVERS_YAML, _fleet_source


@pytest.fixture
def install_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def bound_install(install_dir, monkeypatch):
    """The bot's validator must see THIS install's cluster (the sibling node-page tests' binding)."""
    import core.commandline as commandline
    from core.utils import validators

    saved_argv = getattr(commandline, "COMMAND_LINE_ARGS", None)
    saved_instance = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(install_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved_instance
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", saved_argv, raising=False)


@pytest.fixture
def counts(monkeypatch):
    """Count what a render WOULD have cost: schema file reads and full validations."""
    seen = {"reads": 0, "validations": 0}
    original_read = configgen._read_partials
    original_validate = configwrite.validate_against_schema

    def read(path):
        seen["reads"] += 1
        return original_read(path)

    def validate(text, schemas, *, path=None):
        seen["validations"] += 1
        return original_validate(text, schemas, path=path)

    monkeypatch.setattr(configgen, "_read_partials", read)
    monkeypatch.setattr(configwrite, "validate_against_schema", validate)
    return seen


def _app(install_dir):
    app, registrar = _build(install_dir, users=[{"username": "staff", "password_hash": _hash(),
                                                 "roles": ["Admin"]}])
    app.state.webui_source_provider = _fleet_source
    return app, registrar


def _service_stub(config_dir, ui=None):
    return types.SimpleNamespace(log=logging.getLogger("webservice-warmup-test"),
                                 node=types.SimpleNamespace(config_dir=str(config_dir)), ui=ui)


# ------------------------------------------------------- 1. a warm install renders the page for free

@pytest.mark.asyncio
async def test_a_node_render_after_the_warm_up_parses_and_validates_nothing(install_dir, bound_install,
                                                                            counts):
    app, registrar = _app(install_dir)
    warmup.warm(install_dir, registrar.environment)

    async with asgi_client(app) as client:
        await _login(client, "staff")
        counts["reads"] = counts["validations"] = 0            # count only the render
        response = await client.get("/nodes/dcs-a-01")

    assert response.status_code == 200, response.text[:300]
    assert counts["reads"] == 0, f"the render re-read {counts['reads']} schema files"
    assert counts["validations"] == 0, f"the render re-validated {counts['validations']} times"


# --------------------------------------------------------------------------- 2. warming twice is free

def test_a_second_warm_up_parses_nothing(install_dir, bound_install, counts):
    warmup.warm(install_dir)
    counts["reads"] = counts["validations"] = 0
    parsed, checked, _templates, _seconds = warmup.warm(install_dir)
    assert parsed and checked, "the warm-up claimed to do nothing at all"
    assert counts["reads"] == 0, f"the second warm-up read {counts['reads']} schema files"
    assert counts["validations"] == 0, f"the second warm-up validated {counts['validations']} times"


def test_the_warm_up_compiles_the_page_templates(install_dir, bound_install):
    """Jinja compiles a template on first use and KEEPS it — so the compile is once per process too."""
    _app_, registrar = _app(install_dir)
    _parsed, _checked, templates, _seconds = warmup.warm(install_dir, registrar.environment)
    assert templates > 0, "no template was compiled"
    # Jinja's cache is keyed by (loader, name) — the name is what we warmed
    compiled = {key[1] for key in registrar.environment.cache}
    assert "node_page.html" in compiled, f"the node page template was not compiled: {sorted(compiled)}"


# ------------------------------------------------------------------- 3. an invalid config is not a crash

def test_an_invalid_config_is_counted_not_raised(install_dir, bound_install):
    (install_dir / "nodes.yaml").write_text(
        NODES_YAML.replace("listen_port: 10042", "listen_port: not-a-port"), encoding="utf-8")
    _parsed, checked, _templates, seconds = warmup.warm(install_dir)
    assert checked >= 1 and seconds >= 0, "the broken nodes.yaml was not checked"


# ------------------------------------------------------------------------ 4 + 5. the service's dispatch

def test_a_failing_warm_up_never_reaches_the_boot_path(monkeypatch, tmp_path):
    def boom(_config_dir):
        raise RuntimeError("no schemas today")

    monkeypatch.setattr(warmup, "warm", boom)
    asyncio.run(WebService._warm_render_caches(_service_stub(tmp_path)))   # must not raise


def test_the_warm_up_is_dispatched_in_a_thread(monkeypatch, tmp_path):
    calls = []

    async def fake_to_thread(func, *args, **kwargs):
        calls.append((func, args))
        return 3, 4, 5, 0.5

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    asyncio.run(WebService._warm_render_caches(_service_stub(tmp_path)))
    assert calls and calls[0][0] is warmup.warm, "the warm-up was not dispatched through a thread"
    assert calls[0][1] == (str(tmp_path), None), "the warm-up was handed the wrong install"
