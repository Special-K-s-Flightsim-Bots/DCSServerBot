"""The config a REST API installation gets when no ``services/webservice.yaml`` exists yet.

The service used to MOVE the whole legacy ``plugins/restapi.yaml`` block into its own config, which
left the REST API plugin (it reads its own file) without its ``auth``/``security``/``endpoints``,
and wrote a document its own schema refuses. What it writes now is the two keys that start the HTTP
server, and nothing else; the REST API's file is not touched at all.

The startup path is driven through the real ``WebService.__init__`` against a temporary config
directory, with ``validation: strict`` — so a created file that does not satisfy
``services/webservice/schemas/webservice_schema.yaml`` fails here instead of at an operator's
restart.
"""
from __future__ import annotations

import types

from pathlib import Path

import pytest

from core.utils.yaml_helper import new_yaml
from services.webservice.service import DEFAULT_LISTEN, DEFAULT_PORT, WebService

REPO = Path(__file__).resolve().parents[1]

#: A legacy REST API config: the plugin's own keys, plus the jwt block the DKS plugin writes.
LEGACY_RESTAPI = """\
DEFAULT:
  prefix: /stats
  security:
    default:
      local: none
      trusted: api_key
      remote: jwt
  endpoints:
    servers:
      filter:
      - (.*)[Tt]est(.*)
  auth:
    api_key: 12345
    jwt:
      jwks_url: https://www.digitalkneeboardsimulator.com/api/pubkey
      key: secret-key-from-dks
"""


@pytest.fixture(autouse=True)
def _bot_argv(monkeypatch):
    """The schema validator's ``unique_port`` reads ``COMMAND_LINE_ARGS.node``.

    A test process is not one of the bot's own entry points, so the parser's ``-n/--node`` attribute
    never exists. pykwalify loads ``core/utils/validators.py`` as a fresh extension module, so only
    the patch on ``core.commandline`` reaches it.
    """
    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(node="master", config="config"))


def _config_dir(tmp_path: Path, restapi: str | None = LEGACY_RESTAPI) -> Path:
    (tmp_path / "plugins").mkdir(exist_ok=True)
    (tmp_path / "services").mkdir(exist_ok=True)
    if restapi is not None:
        (tmp_path / "plugins" / "restapi.yaml").write_text(restapi, encoding="utf-8")
    return tmp_path


def _service(config_dir: Path) -> WebService:
    node = types.SimpleNamespace(config_dir=str(config_dir), pool=None, apool=None,
                                 config={"validation": "strict"}, name="master")
    return WebService(node)


def _document(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return new_yaml().load(handle)


# --------------------------------------------------------------- the REST API's file is not touched

def test_the_legacy_restapi_config_is_left_untouched(tmp_path):
    """The whole legacy block, byte for byte — the plugin reads this file and nothing here may
    rewrite it."""
    config_dir = _config_dir(tmp_path)
    before = (config_dir / "plugins" / "restapi.yaml").read_bytes()
    _service(config_dir)
    assert (config_dir / "plugins" / "restapi.yaml").read_bytes() == before
    assert "api_key: 12345" in (config_dir / "plugins" / "restapi.yaml").read_text(encoding="utf-8")


def test_the_created_config_carries_only_the_listener(tmp_path):
    """Two keys, no restapi settings: ``auth``/``security``/``endpoints``/``prefix`` are the
    plugin's, and the service schema refuses them."""
    config_dir = _config_dir(tmp_path)
    _service(config_dir)
    document = _document(config_dir / "services" / "webservice.yaml")
    assert document == {"DEFAULT": {"listen": DEFAULT_LISTEN, "port": DEFAULT_PORT}}
    block = document["DEFAULT"]
    for key in ("auth", "security", "endpoints", "prefix", "debug"):
        assert key not in block, f"{key} must not be written into the service config"


def test_the_created_config_installs_the_service(tmp_path):
    """``validation: strict`` ran inside the real ``__init__``: reaching an app means the file it
    wrote satisfies the service schema and the server was configured from it."""
    service = _service(_config_dir(tmp_path))
    assert service.app is not None
    assert service.server is not None
    assert service.server.config.host == DEFAULT_LISTEN
    assert service.server.config.port == DEFAULT_PORT


# --------------------------------------------------------------- when nothing is written at all

def test_no_restapi_config_means_no_file_and_no_service(tmp_path):
    """An installation without the REST API plugin keeps no service config and gets no app."""
    config_dir = _config_dir(tmp_path, restapi=None)
    service = _service(config_dir)
    assert not (config_dir / "services" / "webservice.yaml").exists()
    assert service.app is None


def test_an_existing_service_config_is_never_rewritten(tmp_path):
    """An operator's own file is the authority: a ``debug``/``port`` already there is kept, and the
    preset defaults are not stamped over it."""
    config_dir = _config_dir(tmp_path)
    existing = config_dir / "services" / "webservice.yaml"
    existing.write_text("DEFAULT:\n  debug: true\n  listen: 127.0.0.1\n  port: 8000\n",
                        encoding="utf-8")
    service = _service(config_dir)
    assert _document(existing) == {"DEFAULT": {"debug": True, "listen": "127.0.0.1", "port": 8000}}
    assert service.server.config.host == "127.0.0.1"
    assert service.server.config.port == 8000
