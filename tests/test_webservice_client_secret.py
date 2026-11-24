"""The Discord client secret: moved out of the yaml at startup, and readable without a key name.

An inline ``auth.discord.client_secret`` is a plain-text secret in a hand-edited file. The webservice
moves it into the bot's secret store on the next start — the same treatment the bot token gets — and
then resolves it from the store, so the config needs to name neither ``client_secret`` nor
``client_secret_key``.

The startup path is driven through the real ``WebService.__init__`` against a temporary config
directory with ``validation: strict``; the resolution order is pinned on ``parse_discord_config``.
"""
from __future__ import annotations

import pickle
import types

from pathlib import Path

import pytest

from core.utils.yaml_helper import new_yaml
from services.webservice.auth import discord_oauth
from services.webservice.service import WebService

SECRET = "s3cr3t-inline-value"
STORED = "already-in-the-store"

CONFIG = """\
DEFAULT:
  listen: 127.0.0.1
  port: 9876
  auth:
    public_base_url: http://localhost:9876
    discord:
      enabled: true
      client_id: 123456789012345678
      client_secret: s3cr3t-inline-value
"""


@pytest.fixture(autouse=True)
def _bot_argv(monkeypatch):
    """``unique_port`` in the service schema reads ``COMMAND_LINE_ARGS.node``; pykwalify loads
    ``core/utils/validators.py`` as a fresh extension module, so the patch must sit on
    ``core.commandline``."""
    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(node="master", config="config"))


def _config_dir(tmp_path: Path, text: str | None = CONFIG) -> Path:
    (tmp_path / "plugins").mkdir(exist_ok=True)
    (tmp_path / "services").mkdir(exist_ok=True)
    if text is not None:
        (tmp_path / "services" / "webservice.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def _service(config_dir: Path) -> WebService:
    node = types.SimpleNamespace(config_dir=str(config_dir), pool=None, apool=None,
                                 config={"validation": "strict"}, name="master")
    return WebService(node)


def _document(config_dir: Path) -> dict:
    with open(config_dir / "services" / "webservice.yaml", encoding="utf-8") as handle:
        return new_yaml().load(handle)


def _stored(config_dir: Path, key: str = "discord_client_secret") -> str | None:
    path = config_dir / ".secret" / f"{key}.pkl"
    if not path.exists():
        return None
    with open(path, "rb") as handle:
        return pickle.load(handle)


# --------------------------------------------------------------- the startup migration

def test_an_inline_secret_is_moved_to_the_store_and_dropped_from_the_yaml(tmp_path):
    """The file is written back WITHOUT the secret, under the default key, and the service still
    starts (``validation: strict``)."""
    config_dir = _config_dir(tmp_path)
    _service(config_dir)

    assert _stored(config_dir) == SECRET
    discord = _document(config_dir)["DEFAULT"]["auth"]["discord"]
    assert "client_secret" not in discord
    assert discord["client_id"] == 123456789012345678
    assert discord["enabled"] is True
    assert "client_secret_key" not in discord, "the default key needs no entry in the yaml"


def test_the_migration_keeps_the_rest_of_the_file(tmp_path):
    """An operator's own keys and comments survive the write."""
    config_dir = _config_dir(tmp_path, "# my note\n" + CONFIG.replace(
        "DEFAULT:\n", "DEFAULT:\n  debug: false\n", 1))
    _service(config_dir)
    text = (config_dir / "services" / "webservice.yaml").read_text(encoding="utf-8")
    assert "# my note" in text
    block = _document(config_dir)["DEFAULT"]
    assert block["debug"] is False
    assert block["listen"] == "127.0.0.1"


def test_a_named_store_key_is_the_destination(tmp_path):
    """``client_secret_key`` keeps naming the destination it names — the secret just stops living
    in the yaml."""
    config_dir = _config_dir(tmp_path, CONFIG.replace(
        "client_secret:", "client_secret_key: my_key\n      client_secret:"))
    _service(config_dir)
    assert _stored(config_dir, "my_key") == SECRET
    discord = _document(config_dir)["DEFAULT"]["auth"]["discord"]
    assert discord["client_secret_key"] == "my_key"
    assert "client_secret" not in discord


def test_a_new_inline_secret_replaces_the_stored_one(tmp_path):
    """The yaml is the source of truth while it still carries the key — the bot token's own rule.

    A stored secret that silently ignores the value the operator just wrote is a login nobody can
    fix from the config."""
    config_dir = _config_dir(tmp_path)
    (config_dir / ".secret").mkdir()
    with open(config_dir / ".secret" / "discord_client_secret.pkl", "wb") as handle:
        pickle.dump(STORED, handle)

    _service(config_dir)

    assert _stored(config_dir) == SECRET
    assert "client_secret" not in _document(config_dir)["DEFAULT"]["auth"]["discord"]


def test_no_inline_secret_means_no_write(tmp_path):
    """A config that never carried one is not rewritten, and no store file appears."""
    text = CONFIG.replace(f"      client_secret: {SECRET}\n", "")
    config_dir = _config_dir(tmp_path, text)
    _service(config_dir)
    assert (config_dir / "services" / "webservice.yaml").read_text(encoding="utf-8") == text
    assert _stored(config_dir) is None


# --------------------------------------------------------------- the resolution order

def _enabled(**extra) -> dict:
    block = {"enabled": True, "client_id": "123456789012345678"}
    block.update(extra)
    return block


def test_the_default_store_key_is_read_when_the_yaml_names_nothing(tmp_path):
    """No ``client_secret``, no ``client_secret_key`` — the default key is read."""
    seen = {}

    def reader(key, config_dir):
        seen["key"] = key
        return "STORE-SECRET"

    settings = discord_oauth.parse_discord_config(
        _enabled(), config_dir=tmp_path, public_base_url="http://localhost:9876",
        secret_reader=reader)
    assert settings is not None
    assert seen["key"] == discord_oauth.DEFAULT_CLIENT_SECRET_KEY
    assert settings.client_secret == "STORE-SECRET"


def test_an_empty_default_store_is_still_a_loud_refusal(tmp_path):
    """The default key is a convenience, not a fallback that starts a login which cannot work."""
    with pytest.raises(ValueError) as excinfo:
        discord_oauth.parse_discord_config(
            _enabled(), config_dir=tmp_path, public_base_url="http://localhost:9876",
            secret_reader=lambda key, config_dir: "")
    assert discord_oauth.DEFAULT_CLIENT_SECRET_KEY in str(excinfo.value)


def test_the_inline_secret_still_wins_over_the_default_store(tmp_path):
    """A file that has not been migrated yet keeps using its own value."""
    seen = {}

    def reader(key, config_dir):  # pragma: no cover - must not be consulted
        seen["key"] = key
        return "STORE-SECRET"

    settings = discord_oauth.parse_discord_config(
        _enabled(client_secret=SECRET), config_dir=tmp_path,
        public_base_url="http://localhost:9876", secret_reader=reader)
    assert settings is not None
    assert settings.client_secret == SECRET
    assert seen == {}


def test_every_writer_and_the_runtime_agree_on_the_key():
    """One definition: the tour, the review step, ``/webadmin setup`` and the runtime all name the
    same store key, and the file the review step commits to is that key's own."""
    from plugins.admin import webadmin
    from services.webservice.setup import discord as discord_step
    from services.webservice.setup import review as review_step

    key = discord_oauth.DEFAULT_CLIENT_SECRET_KEY
    assert discord_step.CLIENT_SECRET_KEY == key
    assert webadmin.CLIENT_SECRET_KEY == key
    assert review_step.DISCORD_CLIENT_SECRET.key == key
    assert review_step.DISCORD_CLIENT_SECRET.relpath == f".secret/{key}.pkl"
