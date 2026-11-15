"""The instances a setup commit CREATES — the folder, the three Config files and the three ports.

A hand-added instance row used to leave ``nodes.yaml`` as the only trace, and the instance stayed
inert: the bot reads a server's NAME and its DCS port out of the instance's own
``Config/serverSettings.lua``. These tests drive the SHIPPED plan and write against a temporary
``Saved Games`` directory (``servers.SAVED_GAMES`` is the module-level import, so it is monkeypatched)
and pin, per case:

* an empty install with one hand-added row comes out USABLE — ``<home>/Config`` exists, the
  ``serverSettings.lua`` carries the row's NAME and port 10308, and ``options.lua`` / ``autoexec.cfg``
  carry the shapes the bot reads;
* two hand-added rows get the ports ``NodeImpl.add_instance`` mints — 10308/10318 and 8088/8090 — and
  each keeps its own name;
* a folder that already exists is left byte-untouched, ``serverSettings.lua``'s absence included (the
  deliberate "this server is unlinked" state), proven by hashing the tree on both sides of a commit;
* a config directory that carries its own ``serverSettings.lua`` wins: the copy is byte-identical, and
  only a key the copy LACKS is added;
* a second commit over the same draft writes nothing into the tree and reports "created: none";
* the outcome names every instance it created (and the files it seeded there), and one instance whose
  folder cannot be created is reported without stopping the others or the closing ``main.yaml``.

UNVERIFIED HERE, and left to the owner to settle on a real instalment: whether DCS itself accepts a
PARTIAL ``Config/serverSettings.lua`` (only ``name`` and ``port``) and a PARTIAL ``Config/options.lua``
(the four keys the bot setdefaults) and fills the remaining keys on the first start of the server. The
tests pin the bytes this commit writes, not what DCS makes of them on Windows.
"""
from __future__ import annotations

import hashlib
import pathlib
import types

import luadata
import pytest
from ruamel.yaml import YAML

from core.autoexec import Autoexec
from services.webservice.setup import database as db_step
from services.webservice.setup import review as review_step
from services.webservice.setup import servers as servers_step
from services.webservice.setup.state import SetupDraft

NODE = "TestNode"
GUILD = 111111111111111111
OWNER = 222222222222222222
CONSOLE_HASH = "pbkdf2_sha256$600000$cafe$beef"


@pytest.fixture(autouse=True)
def _node_arguments(monkeypatch):
    """The schema validator reads ``COMMAND_LINE_ARGS.node``; pytest is not ``run.py``."""
    import core.commandline as commandline
    from core.utils import validators
    argv = types.SimpleNamespace(node=NODE, config="config")
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)


@pytest.fixture(autouse=True)
def saved_games(tmp_path, monkeypatch):
    """The commit creates folders under ``servers.SAVED_GAMES``; point it at this test's own tree."""
    root = tmp_path / "Saved Games"
    monkeypatch.setattr(servers_step, "SAVED_GAMES", str(root))
    return root


def load(path) -> dict:
    return YAML(typ="safe").load(pathlib.Path(path).read_text(encoding="utf-8"))


def lua(path) -> dict:
    """A Lua settings file as a mapping, read by the same parser the bot reads it with."""
    document = luadata.read(str(path), encoding="utf-8")
    assert isinstance(document, dict), f"{path} is not a Lua table: {document!r}"
    return document


def fake_app(config_dir):
    """The application surface this module reads: recorded directory, node name and port."""
    return types.SimpleNamespace(state=types.SimpleNamespace(
        webui_config_dir=str(config_dir), webui_setup_node=NODE, webui_setup_port=9876))


def hand_added_draft(config_dir, *names: str, use_upnp: bool = False) -> SetupDraft:
    """A complete draft whose instances were ALL added by hand on an install that detected none.

    A hand-added row IS its name: the operator types one field, and :func:`servers.resolve_rows`
    makes it both the instance name and the server name.
    """
    draft = SetupDraft(step=9, language="en", bot_language=True, autoupdate=True, discord=True)
    draft.database = {"path": db_step.CREATE, "host": "127.0.0.1", "port": 5432,
                      "master_database": "postgres", "master_user": "postgres",
                      "database": "dcsserverbot", "user": "dcsserverbot",
                      "user_password_attempts": 0, "verified": True, "signature": "sig"}
    draft.node = {"listen_port": 10042, "use_upnp": use_upnp, "no_dcs": False,
                  "dcs_installation": "/tmp/dcs", "public_ip": None,
                  "dgsa_dcs": False, "dgsa_discord": False}
    draft.servers = {
        "scanned": True,
        "detected": {},
        "rows": {name: {"manage": True, "name": name, "status": str(100 + index),
                        "chat": "-1", "admin": "-1", "autostart": True}
                 for index, name in enumerate(names)},
        "added": list(names),
    }
    draft.discord_bot = {"guild_id": GUILD, "owner": OWNER, "privileged_intents": True}
    draft.roles = {"roles": {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["@everyone"]},
                   "audit_channel": "", "admin_channel": "", "central_admin": False}
    draft.console = {"enabled": False, "username": "admin", "password_hash": CONSOLE_HASH}
    draft.plugins = {}
    draft.save(config_dir)
    return draft


def commit(config_dir, draft) -> review_step.WriteReport:
    """The shipped plan and write over *config_dir*, against the temporary ``Saved Games`` tree."""
    plan = review_step.plan(draft, app=fake_app(config_dir))
    return review_step.write(plan, str(config_dir))


def nodes_outcome(report) -> review_step.WriteOutcome:
    outcomes = [outcome for outcome in report.outcomes if outcome.target is review_step.NODES]
    assert len(outcomes) == 1
    return outcomes[0]


def tree_hash(root: pathlib.Path) -> dict[str, str]:
    """Every file under *root* with its sha256 — the whole tree, so a stray write cannot hide."""
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def instance_home(saved_games, name: str) -> pathlib.Path:
    return pathlib.Path(saved_games) / name


# ------------------------------------------------------------------- an empty install, one instance

def test_one_hand_added_instance_comes_out_usable(tmp_path, saved_games):
    """The folder, the three files and the three ports — the instance is bound at the bot's first boot."""
    draft = hand_added_draft(tmp_path, "DCS.one")
    report = commit(tmp_path, draft)
    assert report.main_written is True

    home = instance_home(saved_games, "DCS.one")
    config = home / "Config"
    assert config.is_dir()
    assert sorted(path.name for path in config.iterdir()) == sorted(
        review_step.INSTANCE_CONFIG_FILES)

    settings = lua(config / "serverSettings.lua")
    assert settings["name"] == "DCS.one"          # the row's name, which is servers.yaml's key
    assert settings["port"] == 10308

    options = lua(config / "options.lua")
    assert options["graphics"]["visibRange"] == "High"
    assert options["miscellaneous"]["autologin"] is True
    # luadata reads an empty Lua table back as an empty list: both are the same empty table
    assert not options["plugins"] and not options["difficulty"]

    # the autoexec carries add_instance's two writes, and the bot's own reader parses them back
    text = (config / "autoexec.cfg").read_text(encoding="utf-8")
    assert 'crash_report_mode = "silent"' in text
    assert "net.use_upnp = false" in text
    shim = types.SimpleNamespace(home=str(home))
    autoexec = Autoexec(instance=shim)
    assert autoexec.crash_report_mode == "silent"
    assert autoexec.net["use_upnp"] is False

    # the three port keys the instance schema requires, all written, all from one calculation
    instance = load(tmp_path / "nodes.yaml")[NODE]["instances"]["DCS.one"]
    assert instance["bot_port"] == 6666
    assert instance["webgui_port"] == 8088
    assert instance["dcs_port"] == 10308
    assert instance["home"] == str(home)

    # servers.yaml is keyed by the same name the instance now carries
    assert "DCS.one" in load(tmp_path / "servers.yaml")


def test_the_upnp_answer_on_writes_no_net_switch(tmp_path, saved_games):
    """``add_instance`` sets ``net.use_upnp`` only when the node step answered NO."""
    draft = hand_added_draft(tmp_path, "DCS.one", use_upnp=True)
    commit(tmp_path, draft)

    text = (instance_home(saved_games, "DCS.one") / "Config" / "autoexec.cfg").read_text(
        encoding="utf-8")
    assert text.splitlines() == ['crash_report_mode = "silent"']


# -------------------------------------------------------------------------------- two instances

def test_two_hand_added_instances_get_their_own_ports_and_names(tmp_path, saved_games):
    """``add_instance``'s numbering: DCS +10 and console +2 per instance, bot +1, no port twice."""
    draft = hand_added_draft(tmp_path, "DCS.one", "DCS.two")
    commit(tmp_path, draft)

    instances = load(tmp_path / "nodes.yaml")[NODE]["instances"]
    assert instances["DCS.one"]["dcs_port"] == 10308
    assert instances["DCS.two"]["dcs_port"] == 10318
    assert instances["DCS.one"]["webgui_port"] == 8088
    assert instances["DCS.two"]["webgui_port"] == 8090
    assert instances["DCS.one"]["bot_port"] == 6666
    assert instances["DCS.two"]["bot_port"] == 6667

    one = lua(instance_home(saved_games, "DCS.one") / "Config" / "serverSettings.lua")
    two = lua(instance_home(saved_games, "DCS.two") / "Config" / "serverSettings.lua")
    assert (one["name"], one["port"]) == ("DCS.one", 10308)
    assert (two["name"], two["port"]) == ("DCS.two", 10318)


# ---------------------------------------------------- a folder that exists is never written into

def test_a_pre_existing_folder_is_left_byte_untouched(tmp_path, saved_games):
    """``serverSettings.lua``'s absence in an EXISTING folder is the "this server is unlinked" state."""
    home = instance_home(saved_games, "DCS.one")
    (home / "Config").mkdir(parents=True)
    (home / "Missions").mkdir()
    (home / "Config" / "options.lua").write_text("options = {}\n", encoding="utf-8")
    (home / "Missions" / "a.miz").write_bytes(b"not a real mission")
    before = tree_hash(home)

    draft = hand_added_draft(tmp_path, "DCS.one")
    report = commit(tmp_path, draft)

    assert tree_hash(home) == before, "a pre-existing instance must not be seeded"
    assert not (home / "Config" / "serverSettings.lua").exists()
    assert not (home / "Config" / "autoexec.cfg").exists()

    outcome = nodes_outcome(report)
    assert outcome.ok is True
    assert outcome.created == ()
    assert "created: none" in outcome.note


# ------------------------------------------------------------- the config directory's own file wins

def test_a_config_dir_server_settings_is_copied_verbatim(tmp_path, saved_games):
    """The operator's own template is the file the instance boots with — byte for byte."""
    template = ('cfg = {\n\t["name"] = "Template Server",\n\t["port"] = 10500,\n'
                '\t["missionList"] = {}, -- keep\n}\n')
    (tmp_path / "serverSettings.lua").write_text(template, encoding="utf-8")

    draft = hand_added_draft(tmp_path, "DCS.one")
    commit(tmp_path, draft)

    seeded = instance_home(saved_games, "DCS.one") / "Config" / "serverSettings.lua"
    assert seeded.read_text(encoding="utf-8") == template
    document = lua(seeded)
    assert (document["name"], document["port"]) == ("Template Server", 10500)

    # the nodes.yaml port is still the port the commit minted: the row and the file never disagree
    # silently — the file decided its own port, so the row carries the minted one
    assert load(tmp_path / "nodes.yaml")[NODE]["instances"]["DCS.one"]["dcs_port"] == 10308


def test_a_copied_server_settings_only_gains_what_it_lacks(tmp_path, saved_games):
    """A template with no name gets the row's name; what it already holds is kept as it is."""
    (tmp_path / "serverSettings.lua").write_text(
        'cfg = {\n\t["port"] = 10500,\n\t["current"] = 1,\n}\n', encoding="utf-8")

    draft = hand_added_draft(tmp_path, "DCS.one")
    commit(tmp_path, draft)

    document = lua(instance_home(saved_games, "DCS.one") / "Config" / "serverSettings.lua")
    assert document["name"] == "DCS.one"          # added: the copy carried no name
    assert document["current"] == 1               # kept: the copy carried it
    assert int(document["port"]) == 10500         # kept: the copy carried its own port


def test_a_config_dir_file_is_never_the_row_port_of_a_second_instance(tmp_path, saved_games):
    """One template copied into two instances: each still gets its OWN name and port key."""
    (tmp_path / "serverSettings.lua").write_text('cfg = {\n\t["port"] = 10500,\n}\n',
                                                 encoding="utf-8")
    draft = hand_added_draft(tmp_path, "DCS.one", "DCS.two")
    commit(tmp_path, draft)

    names = [lua(instance_home(saved_games, name) / "Config" / "serverSettings.lua")["name"]
             for name in ("DCS.one", "DCS.two")]
    assert names == ["DCS.one", "DCS.two"]


# ------------------------------------------------------------------------------------ idempotent

def test_a_second_commit_writes_nothing_into_the_tree(tmp_path, saved_games):
    """The draft is still on disk for a retry: the folders now exist, so the seed is a no-op."""
    draft = hand_added_draft(tmp_path, "DCS.one")
    first = commit(tmp_path, draft)
    assert nodes_outcome(first).created
    before = tree_hash(pathlib.Path(saved_games))

    second = commit(tmp_path, draft)
    assert second.main_written is True
    assert tree_hash(pathlib.Path(saved_games)) == before

    outcome = nodes_outcome(second)
    assert outcome.created == ()
    assert "created: none" in outcome.note


# ------------------------------------------------------------------------------------ the report

def test_the_report_names_every_created_instance_and_its_files(tmp_path, saved_games):
    """The outcome carries the facts, so a screen names them without parsing the sentence."""
    draft = hand_added_draft(tmp_path, "DCS.one", "DCS.two")
    report = commit(tmp_path, draft)

    outcome = nodes_outcome(report)
    assert outcome.ok is True
    assert [entry.name for entry in outcome.created] == ["DCS.one", "DCS.two"]
    assert outcome.created[0].files == review_step.INSTANCE_CONFIG_FILES
    assert "DCS.one" in outcome.note and "DCS.two" in outcome.note
    assert "serverSettings.lua" in outcome.note
    assert outcome.error == "" and outcome.instance_errors == ()


def test_one_instance_failing_does_not_stop_the_others_or_the_closing_file(tmp_path, saved_games):
    """A folder that cannot be created is named on the outcome — the commit still finishes."""
    blocked = instance_home(saved_games, "DCS.two")
    blocked.parent.mkdir(parents=True)
    blocked.write_text("a file where the instance folder goes", encoding="utf-8")

    draft = hand_added_draft(tmp_path, "DCS.one", "DCS.two")
    report = commit(tmp_path, draft)

    outcome = nodes_outcome(report)
    assert outcome.ok is True
    assert [entry.name for entry in outcome.created] == ["DCS.one"]
    assert len(outcome.instance_errors) == 1
    assert "DCS.two" in outcome.instance_errors[0]
    assert "DCS.two" in outcome.note
    assert blocked.is_file() and blocked.read_text(encoding="utf-8").startswith("a file")

    # the other instance is complete, and main.yaml still closes the commit
    home = instance_home(saved_games, "DCS.one")
    assert (home / "Config" / "serverSettings.lua").is_file()
    assert (tmp_path / "main.yaml").is_file()
    assert report.completed is True
