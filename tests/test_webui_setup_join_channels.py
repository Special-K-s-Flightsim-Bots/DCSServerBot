"""The joining server step: it OFFERS the status/chat/admin channels and WRITES what was picked.

The joining flow asks no Discord question of its own (steps 2, 5 and 6 are off its rail), so step 8
reads the EXISTING installation's Discord context out of its shared config instead — the token from
``.secret/token.pkl``, the guild from ``main.yaml``, the ``no_discord`` flag and the central admin
channel from ``services/bot.yaml`` (see :func:`setup.join.discord_context`).

TWO LAYERS, both over the shipped code:

* the VIEW is driven through a real ASGI request against the shipped setup application in JOIN mode,
  with the guild planted under exactly the ``(token, guild_id)`` key the shared config yields, so
  the pickers are the shipped ones and no test touches discord.com;
* the COMMIT is driven through the shipped :func:`setup.review.plan` / ``write``, so the assertion is
  on the file the commit produced and not on a description of the plan.
"""

from __future__ import annotations

import pickle

from html import escape as html_escape
from pathlib import Path

import pytest

from conftest import asgi_client
from core.utils import configwrite
from services.webservice.setup import discord as discord_step
from services.webservice.setup import join as join_step
from services.webservice.setup import review as review_step
from services.webservice.setup import servers as servers_step
from services.webservice.setup import tour
from services.webservice.setup.state import SetupDraft
from services.webservice.setup.tour import STEP_PATH
from test_webui_setup_join import (JOIN_NODE, _App, build, enter, held_password, joined_draft,
                                   seed_install, sha256, token_of)

TOKEN = "MTIzNDU2Nzg5.joining-channels.AAAA"
#: The guild ``main.yaml`` names in ``test_webui_setup_join`` (``MAIN_YAML``).
GUILD = 123456789012345678

GUILD_DATA = discord_step.GuildData(
    guild_id=GUILD, guild_name="Test Guild",
    channels=(discord_step.ChannelOption(id=555, name="bot-status", category="BOT"),
              discord_step.ChannelOption(id=666, name="general", category="")),
    roles=("Admin",),
    guilds=((GUILD, "Test Guild"),))


# --------------------------------------------------------------------------------------- the seed

def write_token(config_dir, token: str = TOKEN) -> None:
    """Store the installation's bot token the way ``install.py`` does.

    ``core.utils.os.set_password`` creates its secret DIRECTORY relative to the process's own
    ``config`` (not the passed one), so the store is written here in exactly the format it reads:
    a pickled string at ``<config_dir>/.secret/token.pkl``.
    """
    secret = Path(config_dir) / ".secret"
    secret.mkdir(parents=True, exist_ok=True)
    with open(secret / f"{join_step.TOKEN_KEY}.pkl", "wb") as handle:
        pickle.dump(token, handle)


def plant_guild(app, *, token: str = TOKEN, guild_id: int = GUILD, data=GUILD_DATA) -> None:
    """Put a guild read into the process cache under exactly the key the shared config yields."""
    discord_step.guild_cache(app)[(token, guild_id)] = data
    discord_step.guild_status(app).update({"key": (token, guild_id), "code": ""})


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


def write_bot_yaml(config_dir, body: str) -> None:
    (config_dir / "services" / "bot.yaml").write_text(body, encoding="utf-8")


NO_DISCORD_BOT_YAML = """\
no_discord: true
roles:
  Admin:
    - specialk
"""

CENTRAL_ADMIN_BOT_YAML = """\
roles:
  Admin:
    - specialk
channels:
  admin: 4242
"""


async def open_step_eight(config_dir, *, token: str | None = TOKEN):
    """Choose the add route, seed the step-8 draft, plant the guild and render the step."""
    app, _ = build(config_dir)
    async with asgi_client(app) as client:
        screen = await enter(client)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(screen.text),
                                           "action": "join_new"})
        join_draft(config_dir)
        if token:
            plant_guild(app, token=token)
        html = (await client.get(STEP_PATH, params={"n": "8"})).text
    return app, html


# ------------------------------------------------------------ the context, read from the shared config

def test_the_context_is_read_from_the_installations_own_config(tmp_path):
    seed_install(tmp_path)
    write_token(tmp_path)
    context = join_step.discord_context(tmp_path)
    assert context.discord is True
    assert context.token == TOKEN
    assert context.guild_id == GUILD            # main.yaml's guild_id
    assert context.central_admin is False       # bot.yaml has no channels block


def test_a_no_discord_installation_reports_no_discord_and_reads_the_admin_channel(tmp_path):
    seed_install(tmp_path)
    write_bot_yaml(tmp_path, NO_DISCORD_BOT_YAML)
    assert join_step.discord_context(tmp_path).discord is False
    write_bot_yaml(tmp_path, CENTRAL_ADMIN_BOT_YAML)
    context = join_step.discord_context(tmp_path)
    assert context.discord is True
    assert context.central_admin is True        # bot.yaml's channels.admin


def test_an_unreadable_token_store_is_no_token_not_a_failure(tmp_path):
    seed_install(tmp_path)                      # no .secret/token.pkl at all
    context = join_step.discord_context(tmp_path)
    assert context.token == ""
    assert context.guild_id == GUILD            # the guild id is still known
    # and an unknown config directory is the default context, never a crash
    assert join_step.discord_context(None).token == ""


# --------------------------------------------------------------------------- the offered controls

@pytest.mark.asyncio
async def test_the_joining_server_step_offers_the_real_channel_pickers(tmp_path):
    seed_install(tmp_path)
    write_token(tmp_path)
    _app, html = await open_step_eight(tmp_path)
    for name in ("row_status_0", "row_chat_0", "row_admin_0"):
        assert f'<select name="{name}">' in html, name
    # the guild's own channels are the options, and the no-channel one is offered
    assert '<option value="555"' in html and '<option value="666"' in html
    # the same channel guide the first-run step carries
    assert html_escape(tour.T_SRV_CHANNELS_INTRO) in html


@pytest.mark.asyncio
async def test_the_joining_server_step_degrades_to_typed_ids_without_a_readable_guild(tmp_path):
    seed_install(tmp_path)                      # no token file: nothing to read a guild with
    _app, html = await open_step_eight(tmp_path, token=None)
    for name in ("row_status_0", "row_chat_0", "row_admin_0"):
        assert f'name="{name}"' in html, name
        assert f'<select name="{name}">' not in html, name
    assert html_escape(tour.T_SRV_CHANNELS_INTRO) in html


@pytest.mark.asyncio
async def test_the_installations_central_admin_channel_suppresses_the_per_server_field(tmp_path):
    seed_install(tmp_path)
    write_token(tmp_path)
    write_bot_yaml(tmp_path, CENTRAL_ADMIN_BOT_YAML)
    _app, html = await open_step_eight(tmp_path)
    assert 'name="row_status_0"' in html and 'name="row_chat_0"' in html
    assert "row_admin" not in html              # not rendered at all, not merely disabled


# ------------------------------------------------------------------------------ the required rule

@pytest.mark.parametrize("status, advances", [("", False), ("1234567890", True)])
def test_the_joining_step_requires_a_status_channel_exactly_where_it_asks_for_one(tmp_path, status,
                                                                                  advances):
    seed_install(tmp_path)
    app, _ = build(tmp_path)
    draft = join_draft(tmp_path, status=status)
    answers = servers_step.answers_from_mapping(draft.servers)
    channels, _central = tour._servers_discord(_request(app), draft)
    assert channels is True                     # this installation talks to Discord
    resolved, failure = servers_step.resolve_rows(
        {"row_manage_0": "on", "row_name_0": "My Server", "row_status_0": status}, answers,
        validate=True, channels=channels)
    assert (failure is None) is advances
    if not advances:
        assert failure == ("status_required", {"name": "My Server"})


def _request(app):
    """A stand-in request carrying the application (only ``.app`` is read by the step helpers)."""
    import types
    return types.SimpleNamespace(app=app)


@pytest.mark.asyncio
async def test_the_joining_step_route_refuses_an_empty_status_and_advances_when_it_is_given(tmp_path):
    """The step's OWN write path: the same required rule, on the shipped route."""
    seed_install(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        screen = await enter(client)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(screen.text),
                                           "action": "join_new"})
        join_draft(tmp_path)
        page = await client.get(STEP_PATH, params={"n": "8"})
        token = token_of(page.text)
        # no status channel: refused ON THE STEP, nothing advances, the typed answer is kept
        refused = await client.post(STEP_PATH, data={"_csrf_token": token, "n": "8",
                                                     "row_manage_0": "on",
                                                     "row_name_0": "My Server", "row_status_0": ""})
        assert refused.status_code == 200
        assert "Enter the status channel id for" in refused.text
        assert SetupDraft.load(tmp_path).step == 4
        # the picked channels: the step advances and the draft carries them
        advanced = await client.post(STEP_PATH, data={"_csrf_token": token, "n": "8",
                                                      "row_manage_0": "on",
                                                      "row_name_0": "My Server",
                                                      "row_status_0": "555", "row_chat_0": "666",
                                                      "row_admin_0": "777"})
        assert advanced.status_code == 303
    row = SetupDraft.load(tmp_path).servers["rows"]["DCSserverTwo"]
    assert (row["status"], row["chat"], row["admin"]) == ("555", "666", "777")


# -------------------------------------------------------------------------------- the joining write

def test_the_joining_commit_writes_the_picked_channels(tmp_path):
    seed_install(tmp_path)
    app = _App(tmp_path)
    held_password(app)
    draft = join_draft(tmp_path, status="999", chat="888", admin="777")
    plan = review_step.plan(draft, app=app, mode="join")
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert report.completed is True
    servers = configwrite.load_config_file(tmp_path / "servers.yaml")
    assert servers["My Server"] == {"channels": {"status": 999, "chat": 888, "admin": 777}}
    assert servers["OtherServer"] == {"channels": {"status": 111, "chat": 222}}


def test_the_centrally_admined_joining_commit_writes_no_per_server_admin(tmp_path):
    seed_install(tmp_path)
    write_bot_yaml(tmp_path, CENTRAL_ADMIN_BOT_YAML)
    app = _App(tmp_path)
    held_password(app)
    draft = join_draft(tmp_path, status="999", chat="888", admin="777")
    plan = review_step.plan(draft, app=app, mode="join")
    review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    servers = configwrite.load_config_file(tmp_path / "servers.yaml")
    assert servers["My Server"] == {"channels": {"status": 999, "chat": 888}}


def test_a_joining_no_discord_installation_writes_the_empty_shape(tmp_path):
    seed_install(tmp_path)
    write_bot_yaml(tmp_path, NO_DISCORD_BOT_YAML)
    app = _App(tmp_path)
    held_password(app)
    draft = join_draft(tmp_path)
    plan = review_step.plan(draft, app=app, mode="join")
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert report.completed is True
    assert configwrite.load_config_file(tmp_path / "servers.yaml")["My Server"] == {}


# ------------------------------------------------------------------------- the collision guard holds

def test_a_server_another_node_owns_with_equal_channels_is_a_no_op(tmp_path):
    seed_install(tmp_path)
    (tmp_path / "servers.yaml").write_text(
        "# Server rows of the whole installation.\n"
        "OtherServer:\n  channels:\n    status: 999\n    chat: 888\n    admin: 777\n",
        encoding="utf-8")
    before = sha256(tmp_path / "servers.yaml")
    app = _App(tmp_path)
    held_password(app)
    draft = join_draft(tmp_path, status="999", chat="888", admin="777", name="OtherServer",
                       instance="DCSserverThree")
    plan = review_step.plan(draft, app=app, mode="join")
    assert [item.target.key for item in plan] == ["database_password", "servers", "scheduler", "nodes"]
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    servers = [o for o in report.outcomes if o.target.key == "servers"][0]
    assert servers.ok is True and servers.changed is False
    assert sha256(tmp_path / "servers.yaml") == before


def test_a_server_another_node_owns_with_different_channels_is_refused(tmp_path):
    seed_install(tmp_path)
    before = {"servers": sha256(tmp_path / "servers.yaml"),
              "scheduler": sha256(tmp_path / "plugins" / "scheduler.yaml"),
              "nodes": sha256(tmp_path / "nodes.yaml")}
    app = _App(tmp_path)
    held_password(app)
    draft = join_draft(tmp_path, status="999", chat="-1", admin="-1", name="OtherServer",
                       instance="DCSserverThree")
    plan = review_step.plan(draft, app=app, mode="join")
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert report.completed is False
    servers = [o for o in report.outcomes if o.target.key == "servers"][0]
    assert servers.ok is False
    assert "OtherServer" in servers.error and "servers.yaml" in servers.error
    assert JOIN_NODE not in configwrite.load_config_file(tmp_path / "nodes.yaml")
    assert sha256(tmp_path / "servers.yaml") == before["servers"]
    assert sha256(tmp_path / "nodes.yaml") == before["nodes"]
    # the scheduler target sits AFTER servers in the plan and is not the closing target, so the
    # shipped funnel still writes it — pinned here so the ordering cannot drift unnoticed
    assert configwrite.load_config_file(tmp_path / "plugins" / "scheduler.yaml")["DCSserverThree"] == \
        {"schedule": {"00-24": "YYYYYYY"}}


# ------------------------------------------------- the never-written set stays byte-identical

#: The files a joining commit must NEVER touch. All five are SEEDED here, so "unchanged" is a real
#: byte-for-byte claim rather than "still absent".
NEVER_WRITTEN = ("main.yaml", "services/bot.yaml", "services/webservice.yaml", "plugins/cloud.yaml",
                 ".secret/token.pkl")


def seed_never_written(config_dir) -> dict:
    (config_dir / "services" / "webservice.yaml").write_text("DEFAULT:\n  port: 8080\n",
                                                             encoding="utf-8")
    (config_dir / "plugins" / "cloud.yaml").write_text("DEFAULT:\n  enabled: false\n",
                                                       encoding="utf-8")
    write_token(config_dir)
    return {name: sha256(config_dir / name) for name in NEVER_WRITTEN}


def test_a_successful_joining_commit_leaves_every_install_wide_file_byte_identical(tmp_path):
    """The never-written set, pinned across a commit that really does land this node.

    A guard, not a RED: it holds before and after the channel change, which is exactly the point —
    the committing path must not start reaching install-wide files.
    """
    seed_install(tmp_path)
    before = seed_never_written(tmp_path)
    app = _App(tmp_path)
    held_password(app)
    draft = join_draft(tmp_path, status="999", chat="888", admin="777")
    plan = review_step.plan(draft, app=app, mode="join")
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert report.completed is True
    # not vacuous: this node really landed, so the commit did write something
    assert JOIN_NODE in configwrite.load_config_file(tmp_path / "nodes.yaml")
    for name, digest in before.items():
        assert sha256(tmp_path / name) == digest, name
