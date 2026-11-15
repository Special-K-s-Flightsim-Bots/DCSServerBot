"""Pins on the read-only website doctor (``/webadmin status``).

``status_rows`` is pure: the parsed configuration document and the service's runtime state go in,
one row per gate comes out, in the order an operator meets the gates. These pins drive it with no
bot and no running service, and they guard the two halves that must never drift — which gate
answers with which state, and that no credential value reaches the rendered embed.

The last pair drives the shipped callback against a temporary configuration directory: the embed
and the ephemeral answer, not the Discord registration (that needs a restarted bot).
"""
from __future__ import annotations

import asyncio
import ast
import gettext
import types

from pathlib import Path

import pytest

from core import DEFAULT_TAG
from plugins.admin.webadmin import (CONFIG_FILE, FAIL, OK, WARN, Facts, status_embed,
                                    status_rows, webadmin_group)

REPO = Path(__file__).resolve().parents[1]

FILE, FRONTEND, LOGIN, SESSION, ADDRESS, COOKIE, SERVICE, NEXT_STEP = range(8)

_ABSENT = object()

_URL = "https://dcs.example.com"
_SECRET = "s" * 40


def _auth(url=_URL, **backends):
    """An ``auth`` block: a public address plus the given backends, ``local`` on by default."""
    auth = {}
    if url is not _ABSENT:
        auth["public_base_url"] = url
    if not backends:
        backends = {"local": {"enabled": True}}
    auth.update(backends)
    return auth


def _document(*, frontend=True, auth=_ABSENT, session=_ABSENT):
    """A ``DEFAULT`` document; a keyword left out is absent from the block entirely."""
    block = {}
    if frontend is not _ABSENT:
        block["frontend"] = frontend
    if auth is not _ABSENT:
        block["auth"] = auth
    if session is not _ABSENT:
        block["session"] = session
    return {DEFAULT_TAG: block}


def _facts(**overrides):
    """The healthy runtime picture: the master node, the service running on its port."""
    values = dict(file_present=True, service_present=True, service_running=True, is_master=True,
                  port=9876, url=_URL)
    values.update(overrides)
    return Facts(**values)


def _text(embed) -> str:
    """Everything a reader sees of *embed*, title and fields."""
    parts = [embed.title or ""]
    for field in embed.fields:
        parts += [field.name, field.value]
    return "\n".join(parts)


def test_a_complete_configuration_reports_every_gate_green():
    rows = status_rows(_document(auth=_auth(), session={"secret": _SECRET}), _facts())
    assert len(rows) == 8
    assert [row.state for row in rows] == [OK] * 8
    assert len(status_embed(rows).fields) == 8


def test_a_missing_file_is_the_first_row_and_names_the_file():
    rows = status_rows(None, Facts())
    assert rows[FILE].state == FAIL
    assert CONFIG_FILE in rows[FILE].detail
    assert "/webadmin setup" in rows[FILE].detail


def test_the_file_checks_are_skipped_while_the_file_is_absent():
    rows = status_rows(None, Facts(file_present=False))
    assert len(rows) == 3
    assert CONFIG_FILE in rows[0].detail
    assert rows[-1].state == WARN


@pytest.mark.parametrize("frontend", [False, _ABSENT], ids=["off", "absent"])
def test_a_frontend_that_is_not_enabled_serves_the_rest_api_only(frontend):
    rows = status_rows(_document(frontend=frontend, auth=_auth(), session={"secret": _SECRET}),
                       _facts())
    assert rows[FRONTEND].state == FAIL
    assert "REST" in rows[FRONTEND].detail
    assert "frontend: true" in rows[FRONTEND].detail


@pytest.mark.parametrize("auth", [
    _ABSENT,
    {},
    {"public_base_url": _URL},
    _auth(local={"enabled": False}, discord={"enabled": False}, breakglass={"enabled": False}),
], ids=["no-block", "empty-block", "no-backend", "all-disabled"])
def test_no_enabled_backend_leaves_every_page_at_403(auth):
    rows = status_rows(_document(auth=auth, session={"secret": _SECRET}), _facts())
    assert rows[LOGIN].state == FAIL
    assert "403" in rows[LOGIN].detail


@pytest.mark.parametrize("backend,name", [
    ("local", "username and password"), ("discord", "Discord"), ("breakglass", "break-glass")])
def test_an_enabled_backend_is_named_on_the_login_row(backend, name):
    auth = _auth(**{backend: {"enabled": True}})
    rows = status_rows(_document(auth=auth, session={"secret": _SECRET}), _facts())
    assert rows[LOGIN].state == OK
    assert name in rows[LOGIN].detail


@pytest.mark.parametrize("session,state", [
    (_ABSENT, WARN),
    ({}, WARN),
    ({"secret": None}, WARN),
    ({"secret": ""}, WARN),
    ({"secret": "s" * 31}, WARN),
    ({"secret": "s" * 32}, OK),
    ({"secret": _SECRET}, OK),
], ids=["absent", "empty-block", "null", "blank", "31-chars", "32-chars", "40-chars"])
def test_the_session_secret_is_judged_by_presence_and_length(session, state):
    rows = status_rows(_document(auth=_auth(), session=session), _facts())
    assert rows[SESSION].state == state


def test_the_session_secret_value_never_reaches_the_embed():
    secret = "s3cr3t-session-value-0123456789abcdef"
    text = _text(status_embed(status_rows(_document(auth=_auth(), session={"secret": secret}),
                                          _facts())))
    assert secret not in text
    assert "session.secret" in text


@pytest.mark.parametrize("url,state", [
    (_ABSENT, FAIL),
    ("", FAIL),
    ("dcs.example.com", FAIL),
    ("ftp://dcs.example.com", FAIL),
    ("http://localhost:9876", WARN),
    ("http://127.0.0.1:9876", WARN),
    ("http://10.0.0.5:9876", OK),
    (_URL, OK),
], ids=["absent", "blank", "no-scheme", "wrong-scheme", "localhost", "loopback", "lan", "https"])
def test_the_public_address_is_absolute_and_not_the_local_machine(url, state):
    rows = status_rows(_document(auth=_auth(url=url), session={"secret": _SECRET}), _facts())
    assert rows[ADDRESS].state == state


def test_a_running_master_service_renders_its_port_and_the_url():
    text = _text(status_embed(status_rows(
        _document(auth=_auth(), session={"secret": _SECRET}), _facts())))
    assert "9876" in text
    assert _URL in text


def test_an_agent_node_is_told_the_website_runs_on_the_master_only():
    rows = status_rows(_document(auth=_auth(), session={"secret": _SECRET}),
                       _facts(service_present=True, service_running=False, is_master=False,
                              port=None))
    assert rows[SERVICE].state == WARN
    assert "master" in rows[SERVICE].detail.lower()


def test_a_service_that_is_loaded_but_not_running_is_a_failure():
    rows = status_rows(_document(auth=_auth(), session={"secret": _SECRET}),
                       _facts(service_running=False, port=None))
    assert rows[SERVICE].state == FAIL
    assert "restart" in rows[SERVICE].detail.lower()


def test_a_service_that_was_never_loaded_is_a_failure_naming_the_restart():
    rows = status_rows(_document(auth=_auth(), session={"secret": _SECRET}),
                       _facts(service_present=False, service_running=False, port=None))
    assert rows[SERVICE].state == FAIL
    assert CONFIG_FILE in rows[SERVICE].detail
    assert "restart" in rows[SERVICE].detail.lower()


def test_the_closing_row_names_the_url_and_the_restart_requirement():
    rows = status_rows(_document(auth=_auth(), session={"secret": _SECRET}), _facts())
    assert _URL in rows[NEXT_STEP].detail
    assert "next node restart" in rows[NEXT_STEP].detail
    assert "page reload" in rows[NEXT_STEP].detail


def test_the_closing_row_without_an_address_still_states_the_restart():
    rows = status_rows(None, Facts())
    assert rows[-1].state == WARN
    assert "next node restart" in rows[-1].detail


def test_no_credential_value_reaches_the_embed():
    secret = "S3CRET-abc"
    document = {DEFAULT_TAG: {
        "frontend": True,
        "auth": {
            "public_base_url": _URL,
            "local": {"enabled": True,
                      "users": [{"username": "operator",
                                 "password_hash": f"pbkdf2_sha256${secret}",
                                 "roles": ["Admin"]}]},
            "discord": {"enabled": True, "client_id": 1, "client_secret": secret},
        },
        "session": {"secret": secret * 2},
    }}
    text = _text(status_embed(status_rows(document, _facts())))
    assert secret not in text
    assert "client_secret" not in text
    assert "password_hash" not in text


def test_every_check_appears_exactly_once():
    rows = status_rows(_document(auth=_auth(), session={"secret": _SECRET}), _facts())
    labels = [row.label for row in rows]
    assert len(labels) == 8
    assert len(set(labels)) == len(labels)
    assert len(status_embed(rows).fields) == len(set(labels))


def test_the_group_carries_the_admin_only_doctor():
    assert webadmin_group.name == "webadmin"
    assert "status" in list(webadmin_group._children)
    command = webadmin_group.get_command("status")
    assert command is not None
    assert command.guild_only is True
    checks = {check.__qualname__ for check in command.checks}
    assert "restricted_check" in checks
    assert any("app_has_role" in name for name in checks), checks
    # no parameters: the whole picture comes from the node and the configuration file
    assert not command._params


class _RecordingResponse:
    """The interaction's response, recording what the command sends."""

    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class _Interaction:
    def __init__(self, node):
        self.client = types.SimpleNamespace(node=node)
        self.response = _RecordingResponse()


_CONFIGURATION = """\
DEFAULT:
  frontend: true
  port: 9876
  auth:
    public_base_url: https://dcs.example.com
    local:
      enabled: true
      users:
        - username: operator
          password_hash: pbkdf2_sha256$S3CRET-abc
          roles: [Admin]
  session:
    secret: 0123456789abcdef0123456789abcdef
"""


def _node(config_dir):
    return types.SimpleNamespace(config_dir=str(config_dir), master=True, claimed_master=False)


def test_the_command_answers_ephemerally_with_the_embed(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "webservice.yaml").write_text(_CONFIGURATION, encoding="utf-8")
    interaction = _Interaction(_node(tmp_path))
    asyncio.run(webadmin_group.get_command("status").callback(None, interaction))
    assert len(interaction.response.sent) == 1
    sent = interaction.response.sent[0]
    assert sent["ephemeral"] is True
    assert len(sent["embed"].fields) == 8
    assert "S3CRET-abc" not in _text(sent["embed"])


def test_the_command_reads_the_file_below_the_nodes_config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "webservice.yaml").write_text(_CONFIGURATION, encoding="utf-8")
    interaction = _Interaction(_node(tmp_path))
    asyncio.run(webadmin_group.get_command("status").callback(None, interaction))
    text = _text(interaction.response.sent[0]["embed"])
    assert CONFIG_FILE in text
    assert "REST" not in text


def test_the_command_reports_an_absent_file_without_raising(tmp_path):
    interaction = _Interaction(_node(tmp_path))
    asyncio.run(webadmin_group.get_command("status").callback(None, interaction))
    text = _text(interaction.response.sent[0]["embed"])
    assert CONFIG_FILE in text
    assert "/webadmin setup" in text


# translations that are deliberately the msgid, because the sentence is a product name or already
# reads the same in that language
_DELIBERATE_COPIES = {
    ("de", "Frontend"), ("de", "Login"), ("de", "Discord"), ("de", "Port"),
    ("es", "Discord"), ("es", "Manual"), ("ru", "Discord"), ("cn", "Discord"),
}


def _module_msgids() -> list[str]:
    """Every string the module hands to ``_()``, in source order."""
    tree = ast.parse((REPO / "plugins" / "admin" / "webadmin.py").read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_":
            if node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str) \
                    and node.args[0].value not in found:
                found.append(node.args[0].value)
    return found


def test_the_msgids_are_read_from_the_module_at_all():
    msgids = _module_msgids()
    assert len(msgids) >= 30
    assert "Website status" in msgids


@pytest.mark.parametrize("language", ["cn", "de", "es", "ru"])
def test_every_msgid_resolves_through_the_compiled_catalog(language):
    catalog = gettext.translation("admin", localedir=str(REPO / "locale"),
                                  languages=[language])
    for msgid in _module_msgids():
        resolved = catalog.gettext(msgid)
        assert resolved, msgid
        assert resolved != msgid or (language, msgid) in _DELIBERATE_COPIES, \
            f"{language} admin.mo answers {msgid!r} in English"
        assert resolved.count("{}") == msgid.count("{}"), \
            f"{language}: {msgid!r} translation drops a placeholder: {resolved!r}"
