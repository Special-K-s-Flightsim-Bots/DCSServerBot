"""``Node.proxy`` / ``Node.proxy_auth``: the node's proxy setting resolves to a URL STRING.

The node's config block in ``nodes.yaml`` may carry a ``proxy`` map (url/username/password), and it used to be
returned whole — aiohttp hands that to ``yarl.URL()``, which raises ``TypeError: Constructor parameter should be
str``. With no ``proxy`` block the fallback reads ``services/bot.yaml``, a file that only exists where the bot
service runs, so its absence means "no proxy" rather than an error.
"""
from __future__ import annotations

from core.data.node import Node


class _Holder:
    """The two attributes the properties read: the node's config block and the config directory.

    The three properties are taken from :class:`Node` itself, so their real bodies run; only the rest of the
    class is left out (they are abstract and irrelevant here).
    """

    _proxy_config = Node._proxy_config
    proxy = Node.proxy
    proxy_auth = Node.proxy_auth

    def __init__(self, config_dir, **block):
        self.locals = dict(block)
        self.config_dir = str(config_dir)


def _proxy(holder):
    return holder.proxy


def _proxy_auth(holder):
    return holder.proxy_auth


def test_proxy_block_yields_its_url_as_a_string(tmp_path):
    holder = _Holder(tmp_path, listen_address="0.0.0.0", listen_port=10042,
                     proxy={"url": "https://proxy.local:8080", "username": "admin"})
    proxy = _proxy(holder)
    assert proxy == "https://proxy.local:8080"
    assert isinstance(proxy, str)


def test_proxy_block_username_and_inline_password_make_auth(tmp_path):
    holder = _Holder(tmp_path, proxy={"url": "https://proxy.local:8080",
                                      "username": "admin", "password": "s3cret"})
    auth = _proxy_auth(holder)
    assert auth is not None
    assert auth.login == "admin"
    assert auth.password == "s3cret"


def test_proxy_block_without_credentials_yields_no_auth(tmp_path):
    holder = _Holder(tmp_path, proxy={"url": "https://proxy.local:8080"})
    assert _proxy(holder) == "https://proxy.local:8080"
    assert _proxy_auth(holder) is None


def test_legacy_plain_url_string_is_accepted(tmp_path):
    holder = _Holder(tmp_path, proxy="https://proxy.local:8080")
    assert _proxy(holder) == "https://proxy.local:8080"


def test_no_proxy_block_falls_back_to_bot_yaml(tmp_path):
    services = tmp_path / "services"
    services.mkdir()
    (services / "bot.yaml").write_text(
        "proxy:\n  url: 'http://proxy.lan:3128'\n  username: bob\n", encoding="utf-8")
    holder = _Holder(tmp_path, listen_address="0.0.0.0")
    assert _proxy(holder) == "http://proxy.lan:3128"


def test_missing_bot_yaml_means_no_proxy(tmp_path):
    holder = _Holder(tmp_path, listen_address="0.0.0.0")
    assert _proxy(holder) is None
    assert _proxy_auth(holder) is None
