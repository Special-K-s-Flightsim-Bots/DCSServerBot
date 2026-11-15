"""A ``?``/``#``/``/`` in the bot's user or database name survives the connection URL.

``urlparse`` hands its userinfo and path back still percent-encoded, so the builders must encode
them (``core.utils.db_setup.connection_url`` / ``config_connection_url``) and every site that
rebuilds a URL out of ``urlparse`` parts must go through the same helper
(``core.utils.db_setup.rebuild_url``). Otherwise the value mutates between the write and the boot
comparison that decides a federation from ``urlparse(...).path``.

No PostgreSQL is needed: these drive the shipped builders and the shipped rebuild sites only.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import utils  # noqa: E402
from core.data.node import Node  # noqa: E402
from core.utils import db_setup  # noqa: E402

HOST = "127.0.0.1"
PORT = 5432
HOSTILE_USER = "a@b:c/d"
HOSTILE_DB = "a?b#c/d"


def _assert_survives(url: str) -> None:
    """*url* keeps its user, its query and the whole database name as its PATH."""
    parsed = urlparse(url)
    assert parsed.hostname == HOST
    assert parsed.port == PORT
    assert parsed.fragment == ""
    assert parsed.query == "sslmode=prefer"          # the sslmode, never a slurped database name
    assert parsed.path == "/" + quote(HOSTILE_DB, safe="")
    assert unquote(parsed.path[1:]) == HOSTILE_DB    # the name that was typed, decoded back


def test_the_live_builder_survives_a_hostile_user_and_database():
    _assert_survives(db_setup.connection_url(HOSTILE_USER, "p/w?x#y", HOST, PORT, HOSTILE_DB))


def test_the_config_builder_survives_a_hostile_user_and_database():
    _assert_survives(db_setup.config_connection_url(HOSTILE_USER, HOST, PORT, HOSTILE_DB))


def test_a_slash_in_the_user_cannot_end_the_netloc_early():
    """``quote``'s default ``safe='/'`` would keep a ``/`` in the user, ending the netloc at ``a``."""
    for url in (db_setup.connection_url("a/b", "pw", HOST, PORT, "db"),
                db_setup.config_connection_url("a/b", HOST, PORT, "db")):
        parsed = urlparse(url)
        assert parsed.hostname == HOST and parsed.path == "/db"
        assert parsed.username == quote("a/b", safe="")


def test_a_normal_name_is_byte_identical_through_both_builders_and_the_rebuild():
    """Plain names must produce exactly the string the pre-fix code produced, on both passes."""
    live = db_setup.connection_url("dcsserverbot", "pw", HOST, PORT, "dcsserverbot")
    config = db_setup.config_connection_url("dcsserverbot", HOST, PORT, "dcsserverbot")
    assert live == f"postgres://dcsserverbot:pw@{HOST}:{PORT}/dcsserverbot?sslmode=prefer"
    assert config == f"postgres://dcsserverbot:SECRET@{HOST}:{PORT}/dcsserverbot?sslmode=prefer"
    # rebuild keeps the URL byte-identical
    assert db_setup.rebuild_url(urlparse(live), host=HOST, port=PORT) == live
    assert db_setup.rebuild_url(urlparse(config), host=HOST, port=PORT, password="SECRET") == config


@pytest.mark.parametrize("builder", [db_setup.connection_url, db_setup.config_connection_url])
def test_compose_parse_rebuild_parse_is_a_fixed_point(builder):
    """compose -> ``urlparse`` -> rebuild -> ``urlparse`` yields the same user, path and query."""
    if builder is db_setup.config_connection_url:
        composed = builder(HOSTILE_USER, HOST, PORT, HOSTILE_DB)
        password = "SECRET"
    else:
        composed = builder(HOSTILE_USER, "p/w", HOST, PORT, HOSTILE_DB)
        password = "p/w"
    first = urlparse(composed)
    rebuilt = db_setup.rebuild_url(first, host=HOST, port=PORT, password=password)
    second = urlparse(rebuilt)
    assert rebuilt == composed                       # the second pass changes nothing
    assert second.username == first.username
    assert second.path == first.path
    assert second.query == first.query
    assert second.fragment == "" and first.fragment == ""
    _assert_survives(rebuilt)


def test_rebuild_url_keeps_the_urls_own_password_by_default():
    """install.py's site re-dials with the URL's own (encoded) password when it is not a placeholder."""
    composed = db_setup.connection_url(HOSTILE_USER, "p/w?x", HOST, PORT, HOSTILE_DB)
    rebuilt = db_setup.rebuild_url(urlparse(composed), host=HOST, port=PORT)
    assert urlparse(rebuilt).password == "p%2Fw%3Fx"
    assert urlparse(rebuilt).username == quote(HOSTILE_USER, safe="")


def test_requote_is_the_identity_on_an_already_encoded_component():
    """``urlparse`` does not decode, so re-quoting an encoded component must not double-encode."""
    for raw in ("a@b:c/d", "a?b#c/d", "plain", "a b"):
        assert db_setup.requote(quote(raw, safe="")) == quote(raw, safe="")


def test_node_read_config_keeps_a_hostile_database_path(tmp_path):
    """The shipped ``Node.read_config`` rewrite keeps the user and the path it read back."""
    from ruamel.yaml import YAML

    config_dir = tmp_path / "config"
    (config_dir / ".secret").mkdir(parents=True)
    main = config_dir / "main.yaml"
    composed = db_setup.connection_url(HOSTILE_USER, "p/w", HOST, PORT, HOSTILE_DB)
    main.write_text("validation: off\ndatabase:\n  url: {}\n".format(composed), encoding="utf-8")

    class _Stub:
        def __init__(self):
            self.config_dir = str(config_dir)
            self.log = logging.getLogger("test")

    config = Node.read_config(_Stub(), str(main))  # type: ignore[arg-type]

    rewritten = config["database"]["url"]
    assert "SECRET" in rewritten
    _assert_survives(rewritten)
    # the real password was moved to the secret store, decoded
    assert utils.get_password("clusterdb", str(config_dir)) == "p/w"
    # the stub's own file was rewritten with the masked URL
    assert YAML().load(main.read_text(encoding="utf-8"))["database"]["url"] == rewritten


def test_the_three_rebuild_sites_delegate_to_the_shared_helper():
    """install.py, node.py and nodeimpl.py must not re-interpolate ``urlparse`` parts by hand."""
    for relative in ("install.py", "core/data/node.py", "core/data/impl/nodeimpl.py"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "rebuild_url(" in text, f"{relative} does not call rebuild_url"
        assert 'f"{url.scheme}://{url.username}:' not in text, \
            f"{relative} still re-composes the URL from raw urlparse parts"
