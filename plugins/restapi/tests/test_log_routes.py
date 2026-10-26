"""
HTTP tests of the RestAPI log routes (/node/logs, /node/log, /instance/logs, /instance/log).

The RestAPI plugin is built without a bot: its routes are registered on a bare FastAPI app, the node
reads real files in a temporary directory with the same window semantics as NodeImpl, and the server
is a stand-in carrying only what the routes use. These tests need the bot's requirements (discord.py,
FastAPI); they are skipped where those are not installed.
"""
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("discord")
pytest.importorskip("fastapi")

from fastapi import FastAPI                               # noqa: E402
from fastapi.testclient import TestClient                 # noqa: E402

from plugins.restapi.commands import RestAPI              # noqa: E402

KEY = "test-key"


class DiskNode:
    """A node that reads real files, like NodeImpl does on the master."""

    def __init__(self, name: str, root: Path):
        self.name = name
        self.root = root
        self.locals = {}

    def _path(self, path: str) -> Path:
        return Path(path) if os.path.isabs(path) else self.root / path

    async def read_file_window(self, path, *, length, offset=None, behind=None):
        target = self._path(path)
        st = target.stat()
        size = st.st_size
        if behind is not None:
            end = max(0, min(behind, size))
            start = max(0, end - length)
        else:
            start = min(offset, size)
            end = min(size, start + length)
        with target.open("rb") as f:
            f.seek(start)
            return f.read(end - start), size, st.st_ctime

    async def list_files(self, path, *, pattern="*"):
        directory = self._path(path)
        found = {}
        for pat in [pattern] if isinstance(pattern, str) else pattern:
            for file in directory.glob(pat):
                st = file.stat()
                found[file.as_posix()] = (file.as_posix(), st.st_size, st.st_mtime)
        return sorted(found.values(), key=lambda e: e[2], reverse=True)


def make_api(tmp_path: Path, config: dict) -> tuple[TestClient, RestAPI, DiskNode]:
    node = DiskNode("node1", tmp_path)
    node.all_nodes = {"node1": node, "agent": None}
    node.config = {"logging": {"utc": True}}
    home = tmp_path / "instance"
    server = SimpleNamespace(name="My Server", node=node, instance=SimpleNamespace(home=str(home), locals={}))
    api = RestAPI.__new__(RestAPI)
    api.locals = {"DEFAULT": config}
    api.node = node
    api.bot = SimpleNamespace(servers={"My Server": server})
    api.bus = SimpleNamespace(node=SimpleNamespace(instances={}))
    api.log = logging.getLogger("test")
    api.app = FastAPI()
    api.router = None
    api.register_routes()
    return TestClient(api.app, client=("127.0.0.1", 50000)), api, node


def bot_line(n, level="INFO"):
    return f"2026-10-18 10:00:{n:02d}.000 {level}\tmessage {n}\n"


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "dcssb-node1.log").write_text(bot_line(1) + bot_line(2, "ERROR") + "Traceback\n" +
                                                       bot_line(3), encoding="utf-8")
    (tmp_path / "logs" / "dcssb-node1.log.1").write_text(bot_line(0), encoding="utf-8")
    logs = tmp_path / "instance" / "Logs"
    logs.mkdir(parents=True)
    (logs / "dcs.log").write_text("=== Log opened UTC 2026-10-18 10:00:00\n"
                                  "2026-10-18 10:00:01.000 INFO    ASYNCNET (Main): Login success.\n",
                                  encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP-SECRET-CONTENT", encoding="utf-8")
    return make_api(tmp_path, {"auth": {"api_key": KEY}})


H = {"X-API-Key": KEY}


class TestAuth:
    def test_logs_need_a_key_even_on_loopback(self, setup):
        client, _, _ = setup
        for path in ("/node/logs", "/node/log", "/instance/logs?server_name=My%20Server",
                     "/instance/log?server_name=My%20Server"):
            assert client.get(path).status_code == 403, path
            assert client.get(path, headers=H).status_code == 200, path

    def test_an_endpoint_rule_overrides_the_minimum(self, tmp_path, setup):
        _, api, node = setup
        client, _, _ = make_api(node.root, {"auth": {"api_key": KEY},
                                            "endpoints": {"node/log": {"security": {"local": "none"}}}})
        assert client.get("/node/log").status_code == 200
        assert client.get("/node/logs").status_code == 403

    def test_a_stricter_default_is_kept(self, setup):
        _, _, node = setup
        client, _, _ = make_api(node.root, {"auth": {"api_key": KEY},
                                            "security": {"default": {"local": "jwt"}}})
        assert client.get("/node/log", headers=H).status_code == 403

    def test_disabled_endpoint(self, setup):
        _, _, node = setup
        client, _, _ = make_api(node.root, {"auth": {"api_key": KEY},
                                            "endpoints": {"instance/log": {"enabled": False}}})
        assert client.get("/instance/log?server_name=My%20Server", headers=H).status_code in (404, 405)


class TestRoutes:
    def test_list_bot_logs(self, setup):
        client, _, _ = setup
        data = client.get("/node/logs", headers=H).json()
        assert data["log"] == "bot" and data["node"] == "node1"
        assert {(f["name"], f["live"]) for f in data["files"]} == {("dcssb-node1.log", True),
                                                                  ("dcssb-node1.log.1", False)}

    def test_read_bot_log_with_filter_and_follow(self, setup):
        client, _, node = setup
        data = client.get("/node/log?level=error", headers=H).json()
        assert [(l["level"], l["text"]) for l in data["lines"]] == [("ERROR", "message 2\nTraceback")]
        assert data["utc"] is True and data["rotated"] is False
        with (node.root / "logs" / "dcssb-node1.log").open("a", encoding="utf-8") as f:
            f.write(bot_line(4, "ERROR"))
        follow = client.get("/node/log", params={"level": "error", "cursor": data["cursor"]}, headers=H).json()
        assert [l["text"] for l in follow["lines"]] == ["message 4"]

    def test_read_an_archive(self, setup):
        client, _, _ = setup
        data = client.get("/node/log?file=dcssb-node1.log.1", headers=H).json()
        assert [l["text"] for l in data["lines"]] == ["message 0"]

    def test_read_dcs_log(self, setup):
        client, _, _ = setup
        data = client.get("/instance/log", params={"server_name": "My Server", "contains": "asyncnet"},
                          headers=H).json()
        assert data["file"] == "dcs.log" and data["server_name"] == "My Server" and data["node"] == "node1"
        assert [l["text"] for l in data["lines"]] == ["ASYNCNET (Main): Login success."]

    def test_paths_are_never_accepted(self, setup):
        client, _, node = setup
        for name in ("../secret.txt", "..\\secret.txt", str(node.root / "secret.txt"), "secret.txt"):
            response = client.get("/node/log", params={"file": name}, headers=H)
            assert response.status_code == 404, name
            assert "TOP-SECRET-CONTENT" not in response.text

    def test_errors(self, setup):
        client, _, _ = setup
        assert client.get("/node/log?node_name=nowhere", headers=H).status_code == 404
        assert client.get("/node/log?node_name=agent", headers=H).status_code == 503
        assert client.get("/instance/log?server_name=nope", headers=H).status_code == 404
        assert client.get("/node/log?since=999999d", headers=H).status_code == 400
        assert client.get("/node/log?level=loud", headers=H).status_code == 400
        assert client.get("/node/log?since=tomorrow", headers=H).status_code == 400
        assert client.get("/node/log?cursor=nonsense", headers=H).status_code == 400
        assert client.get("/node/log?lines=5000", headers=H).status_code == 422

    def test_no_dcs_log_yet(self, setup):
        client, _, node = setup
        (node.root / "instance" / "Logs" / "dcs.log").unlink()
        assert client.get("/instance/log?server_name=My%20Server", headers=H).status_code == 404
        assert client.get("/instance/logs?server_name=My%20Server", headers=H).json()["files"] == []
