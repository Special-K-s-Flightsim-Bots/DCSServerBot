"""Channel ids on the DCS-servers step: only the reserved none, a positive id and empty may pass.

The three channel fields of a server row are validated in ONE place — ``servers.resolve_rows`` through
``servers.parse_channel`` — so both steps that ask for them (the first-run step and the joining step)
get the same rule. This module pins that rule:

* ``-1`` is the installer's reserved "no channel" id and is accepted;
* a POSITIVE id is accepted (a guild that cannot be read must still let an operator type one);
* ``0``, ``-0``, any other negative id and an id past the snowflake ceiling are NOT channel ids and are
  refused with the classified ``channel_invalid`` outcome the screens already render;
* an empty status is still ``status_required``.

The rule is proven three ways: on the helper (``parse_channel``), on the shared resolver
(``resolve_rows``) and THROUGH BOTH step routes, because a route is where the fields actually reach
disk. The write side is pinned with a real commit, so the accepted values landing in ``servers.yaml``
and the install-wide files staying byte-identical are both asserted on files, not on a plan.
"""

from __future__ import annotations

import pickle

from pathlib import Path

import pytest

from conftest import asgi_client
from core.utils import configwrite
from services.webservice.setup import join as join_step
from services.webservice.setup import review as review_step
from services.webservice.setup import servers as servers_step
from services.webservice.setup.state import SetupDraft
from services.webservice.setup.tour import STEP_PATH
from test_webui_setup_join import (_App, build as join_build, enter as join_enter, held_password,
                                   joined_draft, seed_install, sha256, token_of as join_token_of)
from test_webui_setup_servers import (build as first_build, enter as first_enter, row_form, scanned,
                                      seed, token_of as first_token_of)

#: Every value that is not a channel id: zero (either spelling), a negative id other than the
#: reserved -1, and an id past the 64-bit snowflake ceiling.
IMPOSSIBLE = ("0", "-0", "0000", "-5", "-9999", "999999999999999999999")

#: The values a channel field does accept: the reserved none, and a positive id (small and snowflake-
#: sized).
ACCEPTED = ("-1", "555", "1234567890", "1150000000000000000")

CHANNEL_INVALID_STATUS = "The status channel id for {name} must be a number, or -1 for none."


# ------------------------------------------------------------------------------- the shared helper

def test_parse_channel_reads_the_reserved_none_and_positive_ids_only():
    for value in ACCEPTED:
        expected = -1 if value == "-1" else int(value)
        assert servers_step.parse_channel(value) == expected, value
    assert servers_step.parse_channel("") is None
    assert servers_step.parse_channel("", none=-1) == -1
    for value in IMPOSSIBLE + ("abc", ""):
        assert servers_step.parse_channel(value) is None, value


# ------------------------------------------------------------- the shared resolver (both flows)

def _first_run_answers():
    """The rows of a first-run step with one named managed instance."""
    return servers_step.answers_from_mapping({"scanned": True}, detected={"i": "SRS-1"})


@pytest.mark.parametrize("value", IMPOSSIBLE)
def test_resolve_rows_refuses_an_impossible_status_id(value):
    resolved, failure = servers_step.resolve_rows(
        {"row_manage_0": "on", "row_name_0": "SRS-1", "row_status_0": value}, _first_run_answers(),
        validate=True, channels=True)
    assert resolved is None
    assert failure == ("channel_invalid", {"name": "SRS-1", "field": "status"})


@pytest.mark.parametrize("field", ["chat", "admin"])
@pytest.mark.parametrize("value", IMPOSSIBLE)
def test_resolve_rows_refuses_an_impossible_optional_channel_id(field, value):
    resolved, failure = servers_step.resolve_rows(
        {"row_manage_0": "on", "row_name_0": "SRS-1", "row_status_0": "555",
         f"row_{field}_0": value}, _first_run_answers(), validate=True, channels=True)
    assert resolved is None
    assert failure == ("channel_invalid", {"name": "SRS-1", "field": field})


@pytest.mark.parametrize("value", ACCEPTED)
def test_resolve_rows_accepts_the_reserved_none_and_a_positive_id(value):
    resolved, failure = servers_step.resolve_rows(
        {"row_manage_0": "on", "row_name_0": "SRS-1", "row_status_0": value,
         "row_chat_0": value, "row_admin_0": value}, _first_run_answers(),
        validate=True, channels=True)
    assert failure is None and resolved is not None
    assert resolved.rows[0].status == value


def test_resolve_rows_still_requires_a_status_channel_when_it_is_empty():
    """``""`` keeps its own classified outcome — it is missing, not invalid."""
    resolved, failure = servers_step.resolve_rows(
        {"row_manage_0": "on", "row_name_0": "SRS-1", "row_status_0": ""}, _first_run_answers(),
        validate=True, channels=True)
    assert resolved is None
    assert failure == ("status_required", {"name": "SRS-1"})


# ------------------------------------------------------------------- the first-run step route

@pytest.mark.asyncio
async def test_the_first_run_step_route_refuses_an_impossible_status_id(tmp_path):
    """The first-run step's OWN write path: a -5 status is reported ON the step, nothing advances."""
    seed(tmp_path, servers=scanned())
    app, _ = first_build(tmp_path)
    async with asgi_client(app) as client:
        await first_enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        refused = await client.post(STEP_PATH, data=row_form(
            first_token_of(page), row_manage_0="on", row_name_0="SRS-1", row_status_0="-5",
            row_chat_0="666", row_admin_0="-1",
            row_manage_1="on", row_name_1="Server 2", row_status_1="42"))
    assert refused.status_code == 200
    assert CHANNEL_INVALID_STATUS.format(name="SRS-1") in refused.text
    assert 'value="-5"' in refused.text              # the typed value comes back
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 7                            # the step did not advance
    assert draft.servers["rows"]["DCS.serverrelease"]["status"] == "-5"


# ----------------------------------------------------------------------- the joining step route

def join_draft(config_dir, *, status: str = "1234567890", chat: str = "-1", admin: str = "-1",
               name: str = "My Server", instance: str = "DCSserverTwo") -> SetupDraft:
    """The joining draft at step 8, with the channel answers an operator would have typed."""
    draft = joined_draft(config_dir)
    draft.step = 4
    draft.join = {join_step.ROUTE_KEY: join_step.ROUTE_NEW}
    draft.servers = {
        "scanned": True,
        "detected": {instance: name},
        "rows": {instance: {"manage": True, "name": name, "status": status, "chat": chat,
                            "admin": admin, "autostart": True}},
    }
    draft.save(config_dir)
    return draft


async def seed_joining_step_eight(client, config_dir) -> str:
    """Choose the add route, seed the step-8 draft and return the step's CSRF token."""
    screen = await join_enter(client)
    await client.post(STEP_PATH, data={"_csrf_token": join_token_of(screen.text),
                                       "action": "join_new"})
    join_draft(config_dir)
    page = await client.get(STEP_PATH, params={"n": "8"})
    assert page.status_code == 200
    return join_token_of(page.text)


@pytest.mark.asyncio
@pytest.mark.parametrize("value, expected", [
    ("-5", "channel_invalid"),
    ("-0", "channel_invalid"),
    ("0", "channel_invalid"),
    ("999999999999999999999", "channel_invalid"),
    ("", "status_required"),
])
async def test_the_joining_step_route_refuses_an_impossible_or_missing_status(tmp_path, value,
                                                                              expected):
    """The joining step's own write path: the shared rule, reached from the joining route."""
    seed_install(tmp_path)
    app, _ = join_build(tmp_path)
    async with asgi_client(app) as client:
        token = await seed_joining_step_eight(client, tmp_path)
        refused = await client.post(STEP_PATH, data={
            "_csrf_token": token, "n": "8", "row_manage_0": "on",
            "row_name_0": "My Server", "row_status_0": value})
    assert refused.status_code == 200
    if expected == "status_required":
        assert "Enter the status channel id for My Server." in refused.text
    else:
        assert CHANNEL_INVALID_STATUS.format(name="My Server") in refused.text
    assert SetupDraft.load(tmp_path).step == 4        # the joining step did not advance


@pytest.mark.asyncio
async def test_the_joining_step_route_advances_on_a_channel_it_accepts(tmp_path):
    """A positive status and the reserved none still pass the joining route."""
    seed_install(tmp_path)
    app, _ = join_build(tmp_path)
    async with asgi_client(app) as client:
        token = await seed_joining_step_eight(client, tmp_path)
        advanced = await client.post(STEP_PATH, data={
            "_csrf_token": token, "n": "8", "row_manage_0": "on",
            "row_name_0": "My Server", "row_status_0": "555", "row_chat_0": "-1",
            "row_admin_0": "-1"})
    assert advanced.status_code == 303
    row = SetupDraft.load(tmp_path).servers["rows"]["DCSserverTwo"]
    assert (row["status"], row["chat"], row["admin"]) == ("555", "-1", "-1")


# --------------------------------------------------------------------------------- the write side

#: The install-wide files a joining commit must NEVER touch. All five are seeded, so "unchanged" is a
#: byte-for-byte claim rather than "still absent".
NEVER_WRITTEN = ("main.yaml", "services/bot.yaml", "services/webservice.yaml", "plugins/cloud.yaml",
                 ".secret/token.pkl")


def seed_never_written(config_dir) -> dict:
    (config_dir / "services" / "webservice.yaml").write_text("DEFAULT:\n  port: 8080\n",
                                                             encoding="utf-8")
    (config_dir / "plugins" / "cloud.yaml").write_text("DEFAULT:\n  enabled: false\n",
                                                       encoding="utf-8")
    secret = Path(config_dir) / ".secret"
    secret.mkdir(parents=True, exist_ok=True)
    with open(secret / f"{join_step.TOKEN_KEY}.pkl", "wb") as handle:
        pickle.dump("MTIzNDU2Nzg5.channel-ids.AAAA", handle)
    return {name: sha256(Path(config_dir) / name) for name in NEVER_WRITTEN}


def test_a_valid_pick_lands_in_servers_yaml_and_never_touches_install_wide_files(tmp_path):
    seed_install(tmp_path)
    before = seed_never_written(tmp_path)
    app = _App(tmp_path)
    held_password(app)
    draft = join_draft(tmp_path, status="555", chat="666", admin="777")
    plan = review_step.plan(draft, app=app, mode="join")
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert report.completed is True
    servers = configwrite.load_config_file(tmp_path / "servers.yaml")
    assert servers["My Server"] == {"channels": {"status": 555, "chat": 666, "admin": 777}}
    assert servers["OtherServer"] == {"channels": {"status": 111, "chat": 222}}   # carried, untouched
    for name, digest in before.items():
        assert sha256(Path(tmp_path) / name) == digest, name
