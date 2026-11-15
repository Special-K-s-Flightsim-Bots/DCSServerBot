"""Step 9's WRITES — what "What do you run?" turns into on disk, through the shipped commit.

Four things are exercised against the real code, in a temporary config directory:

* ``main.yaml``'s ``opt_plugins``: the optional plugins the ticked templates switch on, unioned with
  what the key already lists, with no default plugin ever added and no name listed twice;
* the per-plugin seed files: the shipped sample written when the target is absent, nothing written
  when the sample is missing, and an operator's own file never replaced;
* the nested ``nodes.yaml`` merge: the SRS / Tacview / LotAtC installation paths and each instance's
  SRS port, filling in only what the file does not carry yet;
* the ``WriteReport``: written, skipped and failed are three distinguishable outcomes, and a skipped
  seed does not hold the closing file back.

UNVERIFIED here: the machine's own app probes (``apps.detect_app`` is stubbed, as in
``test_webui_setup_plugins.py`` — the real ones read the Windows registry) and the browser screen; the
step's own rendering is covered by ``test_webui_setup_plugins.py``.
"""
from __future__ import annotations

import pathlib
import types

import pytest
from ruamel.yaml import YAML

from services.webservice.setup import apps as apps_step
from services.webservice.setup import database as db_step
from services.webservice.setup import review as review_step
from services.webservice.setup.state import SetupDraft

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = REPO_ROOT / "samples" / "plugins"
NODE = "TestNode"
GUILD = 111111111111111111
OWNER = 222222222222222222
CONSOLE_HASH = "pbkdf2_sha256$600000$cafe$beef"

#: The shipped sample files this suite rearranges; they are never modified, only copied.
PUNISHMENT_SAMPLE = SAMPLES / "punishment.yaml"


@pytest.fixture(autouse=True)
def _node_arguments(monkeypatch):
    """The schema validator reads ``COMMAND_LINE_ARGS.node``; pytest is not ``run.py``."""
    import core.commandline as commandline
    from core.utils import validators
    argv = types.SimpleNamespace(node=NODE, config="config")
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)


def load(path) -> dict:
    return YAML(typ="safe").load(pathlib.Path(path).read_text(encoding="utf-8"))


def fake_app(config_dir):
    """The application surface this module reads: recorded directory, node name and port."""
    return types.SimpleNamespace(state=types.SimpleNamespace(
        webui_config_dir=str(config_dir), webui_setup_node=NODE, webui_setup_port=9876))


def detect_at(monkeypatch, **paths) -> None:
    """Make the app probes answer *paths* (``SRS=``, ``Tacview=``, ``LotAtC=``)."""
    monkeypatch.setattr(apps_step, "detect_app",
                        lambda spec: paths.get(spec.key))


def complete_draft(config_dir, *, instances: tuple[str, ...] = ("DCS.openbeta",),
                   plugins: dict | None = None, discord: bool = True) -> SetupDraft:
    """A draft with every step answered — the state the Review step is reached in."""
    draft = SetupDraft(step=9, language="en", bot_language=True, autoupdate=True, discord=discord)
    draft.database = {"path": db_step.CREATE, "host": "127.0.0.1", "port": 5432,
                      "master_database": "postgres", "master_user": "postgres",
                      "database": "dcsserverbot", "user": "dcsserverbot",
                      "user_password_attempts": 0, "verified": True, "signature": "sig"}
    draft.node = {"listen_port": 10042, "use_upnp": False, "no_dcs": False,
                  "dcs_installation": "/tmp/dcs", "public_ip": None,
                  "dgsa_dcs": False, "dgsa_discord": False}
    draft.servers = {"scanned": True,
                     "detected": {instance: f"Server {index + 1}"
                                  for index, instance in enumerate(instances)},
                     "rows": {instance: {"manage": True, "name": f"Server {index + 1}",
                                         "status": str(100 + index), "chat": "-1", "admin": "-1",
                                         "autostart": True}
                              for index, instance in enumerate(instances)}}
    draft.discord_bot = {"guild_id": GUILD, "owner": OWNER, "privileged_intents": True}
    draft.roles = {"roles": {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["@everyone"]},
                   "audit_channel": "", "admin_channel": "", "central_admin": False}
    draft.console = {"enabled": False, "username": "admin", "password_hash": CONSOLE_HASH}
    draft.plugins = dict(plugins or {})
    draft.save(config_dir)
    return draft


def commit(tmp_path, draft, monkeypatch, *, fail_on: str | None = None) -> review_step.WriteReport:
    """Run the shipped plan and write over *tmp_path*, optionally refusing one file by name."""
    from core.utils import configwrite

    plan = review_step.plan(draft, app=fake_app(tmp_path))
    if fail_on is not None:
        real = configwrite.save_config_file

        def wrapper(path, data, **kwargs):
            if pathlib.Path(path).name == fail_on:
                raise configwrite.ConfigWriteError(f"{fail_on} refused by the test", path=path)
            return real(path, data, **kwargs)

        monkeypatch.setattr(configwrite, "save_config_file", wrapper)
    return review_step.write(plan, str(tmp_path))


def outcome_for(report, relpath: str) -> list:
    return [outcome for outcome in report.outcomes if outcome.target.relpath == relpath]


# ------------------------------------------------------------------------------- opt_plugins

def test_opt_plugins_lists_the_optional_names_of_the_ticked_templates(tmp_path):
    """Every plugin a ticked template switches on lands in ``opt_plugins`` unless it is a default.

    ``public-server`` enables ``punishment`` and ``carrier`` enables ``greenieboard`` — both optional.
    The defaults are never listed — ``main.yaml``'s absent ``plugins`` key already loads them.
    """
    draft = complete_draft(tmp_path, plugins={"templates": ["carrier", "public-server"],
                                              "radio": {"DCS.openbeta": "srs"}})
    document = review_step._main_document(draft, fake_app(tmp_path))
    # the tile order of the rail, then each template's own plugin order
    assert document["opt_plugins"] == ["punishment", "greenieboard"]
    assert "creditsystem" not in document["opt_plugins"]
    assert "userstats" not in document["opt_plugins"]


def test_opt_plugins_is_the_union_of_what_main_yaml_lists_and_the_new_names(tmp_path):
    """An entry the file already carries is kept, in place, and a shared name is not repeated."""
    draft = complete_draft(tmp_path, plugins={"templates": ["public-server", "carrier"],
                                              "radio": {"DCS.openbeta": "srs"}})
    from core.utils import configwrite
    configwrite.save_config_file(tmp_path / "main.yaml",
                                 {"guild_id": GUILD, "opt_plugins": ["music", "punishment"]},
                                 schemas=None)
    document = review_step._main_document(draft, fake_app(tmp_path))
    assert document["opt_plugins"] == ["music", "punishment", "greenieboard"]


def test_opt_plugins_alone_keeps_the_default_plugins_out(tmp_path):
    """A draft that ticks nothing optional writes no ``opt_plugins`` key at all."""
    draft = complete_draft(tmp_path, plugins={"templates": [], "radio": {"DCS.openbeta": "srs"}})
    document = review_step._main_document(draft, fake_app(tmp_path))
    assert "opt_plugins" not in document


def test_main_yaml_is_still_the_last_entry_of_the_plan(tmp_path, monkeypatch):
    """The closing file stays last, with every step 9 write before it."""
    detect_at(monkeypatch, SRS="/opt/srs", Tacview="/opt/tacview")
    draft = complete_draft(tmp_path, plugins={
        "templates": ["public-server", "carrier"], "radio": {"DCS.openbeta": "srs"},
        "tacview": True})
    plan = review_step.plan(draft, app=fake_app(tmp_path))
    assert plan[-1].target is review_step.MAIN
    assert [item.target.relpath for item in plan].count("main.yaml") == 1
    relpaths = [item.target.relpath for item in plan]
    assert relpaths.index("plugins/punishment.yaml") < relpaths.index("main.yaml")
    assert relpaths.index("nodes.yaml") < relpaths.index("main.yaml")
    assert any(item.target is review_step.EXTENSIONS for item in plan)


# ---------------------------------------------------------------------------- the plugin seeds

def test_a_seeded_plugin_file_is_the_shipped_sample(tmp_path, monkeypatch):
    """A ticked template seeds ``config/plugins/<plugin>.yaml`` from the shipped sample."""
    draft = complete_draft(tmp_path, plugins={"templates": ["public-server", "carrier"],
                                              "radio": {"DCS.openbeta": "srs"}})
    report = commit(tmp_path, draft, monkeypatch)

    for plugin in ("punishment", "greenieboard"):
        seeded = tmp_path / "plugins" / f"{plugin}.yaml"
        assert seeded.is_file(), plugin
        assert load(seeded) == load(SAMPLES / f"{plugin}.yaml"), plugin
        outcome = outcome_for(report, f"plugins/{plugin}.yaml")
        assert outcome and outcome[0].state == "written", plugin


def test_a_plugin_with_no_sample_writes_no_file(tmp_path, monkeypatch):
    """``cas-logistics`` names ``flightplan`` and ``logistics``; neither ships a sample."""
    assert not (SAMPLES / "flightplan.yaml").exists()
    assert not (SAMPLES / "logistics.yaml").exists()
    draft = complete_draft(tmp_path, plugins={"templates": ["cas-logistics"],
                                              "radio": {"DCS.openbeta": "srs"}})
    report = commit(tmp_path, draft, monkeypatch)

    assert not (tmp_path / "plugins" / "flightplan.yaml").exists()
    assert not (tmp_path / "plugins" / "logistics.yaml").exists()
    assert outcome_for(report, "plugins/flightplan.yaml") == []
    assert outcome_for(report, "plugins/logistics.yaml") == []


def test_an_existing_plugin_file_is_left_untouched_and_reported_skipped(tmp_path, monkeypatch):
    """The operator's own file survives the commit, and the report says it was skipped."""
    operator_file = tmp_path / "plugins" / "punishment.yaml"
    operator_file.parent.mkdir(parents=True)
    operator_file.write_text("# mine\nDEFAULT:\n  foo: 1\n", encoding="utf-8")

    draft = complete_draft(tmp_path, plugins={"templates": ["public-server"],
                                              "radio": {"DCS.openbeta": "srs"}})
    report = commit(tmp_path, draft, monkeypatch)

    assert operator_file.read_text(encoding="utf-8") == "# mine\nDEFAULT:\n  foo: 1\n"
    skipped = outcome_for(report, "plugins/punishment.yaml")[0]
    assert skipped.state == "skipped"
    assert skipped.ok is False and skipped.skipped is True
    assert "already exists" in skipped.note
    assert skipped.error == ""
    # a skipped seed is not a refusal: the closing file is still written
    assert report.main_written is True


# ------------------------------------------------- the nested nodes.yaml merge + the SRS ports

def test_the_extensions_merge_fills_in_only_the_keys_nodes_yaml_lacks(tmp_path, monkeypatch):
    """The merge adds the detected paths and the ports, and keeps every value already on disk."""
    from core.utils import configwrite
    nodes = tmp_path / "nodes.yaml"
    configwrite.save_config_file(nodes, {NODE: {
        "listen_port": 10042,
        "extensions": {"SRS": {"installation": "/operator/srs"}},
        "instances": {"DCS.openbeta": {
            "bot_port": 6666, "home": "/tmp/dcs.openbeta",
            "extensions": {"SRS": {"port": 6000}}}},
    }}, schemas=None)

    detect_at(monkeypatch, SRS="/detected/srs", Tacview="/detected/tacview")
    draft = complete_draft(tmp_path, plugins={
        "templates": [], "radio": {"DCS.openbeta": "srs"}, "tacview": True})
    plan = [item for item in review_step.plan(draft, app=fake_app(tmp_path))
            if item.target is review_step.EXTENSIONS]
    assert len(plan) == 1
    report = review_step.write(plan, str(tmp_path))

    assert [outcome.state for outcome in report.outcomes] == ["written"]
    document = load(nodes)
    assert document[NODE]["listen_port"] == 10042
    assert document[NODE]["extensions"]["SRS"]["installation"] == "/operator/srs"   # kept
    assert document[NODE]["extensions"]["Tacview"]["installation"] == "/detected/tacview"
    # the instance row the file already carries keeps its own port
    assert document[NODE]["instances"]["DCS.openbeta"]["extensions"]["SRS"]["port"] == 6000
    assert document[NODE]["instances"]["DCS.openbeta"]["home"] == "/tmp/dcs.openbeta"


def test_two_srs_instances_get_5002_and_5004(tmp_path, monkeypatch):
    """The installer's numbering: the first SRS instance 5002, then +2 for each following one."""
    detect_at(monkeypatch, SRS="/detected/srs")
    draft = complete_draft(tmp_path, instances=("DCS.openbeta", "DCS.release"),
                           plugins={"templates": [],
                                    "radio": {"DCS.openbeta": "srs", "DCS.release": "srs"}})
    report = commit(tmp_path, draft, monkeypatch)

    assert report.main_written is True
    instances = load(tmp_path / "nodes.yaml")[NODE]["instances"]
    assert instances["DCS.openbeta"]["extensions"]["SRS"]["port"] == 5002
    assert instances["DCS.release"]["extensions"]["SRS"]["port"] == 5004


def test_the_voice_chat_choice_writes_the_switch_and_no_srs_port(tmp_path, monkeypatch):
    """A VoiceChat row writes ``advanced.voice_chat_server`` and no port; the SRS row keeps 5002."""
    detect_at(monkeypatch, SRS="/detected/srs")
    draft = complete_draft(tmp_path, instances=("DCS.openbeta", "DCS.release"),
                           plugins={"templates": [],
                                    "radio": {"DCS.openbeta": "voicechat", "DCS.release": "srs"}})
    report = commit(tmp_path, draft, monkeypatch)
    assert report.main_written is True

    nodes = load(tmp_path / "nodes.yaml")[NODE]
    assert "extensions" not in nodes["instances"]["DCS.openbeta"]
    assert nodes["instances"]["DCS.release"]["extensions"]["SRS"]["port"] == 5002

    servers = load(tmp_path / "servers.yaml")
    assert servers["Server 1"]["serverSettings"]["advanced"] == {"voice_chat_server": True}
    assert "serverSettings" not in servers["Server 2"]


def test_an_srs_row_with_a_detected_installation_gets_the_first_port(tmp_path, monkeypatch):
    """A detected SRS installation is what makes a port writable, and the first one is the installer's 5002."""
    detect_at(monkeypatch, SRS="/detected/srs")
    draft = complete_draft(tmp_path, plugins={"templates": [], "radio": {"DCS.openbeta": "srs"}})
    commit(tmp_path, draft, monkeypatch)

    nodes = load(tmp_path / "nodes.yaml")[NODE]
    assert nodes["instances"]["DCS.openbeta"]["extensions"]["SRS"]["port"] == 5002


def test_an_srs_row_without_a_detected_installation_writes_no_port_and_says_so(tmp_path, monkeypatch):
    """No SRS installation means no ``extensions.SRS`` block, so the row gets no port — and is reported.

    Nothing else is ticked either, so the extension document is empty at the node level: NO
    ``nodes.yaml`` merge is planned, and the sentence travels on the node write that does land.
    """
    detect_at(monkeypatch, SRS=None)
    draft = complete_draft(tmp_path, plugins={"templates": [], "radio": {"DCS.openbeta": "srs"}})
    app = fake_app(tmp_path)
    keys = [item.target.key for item in review_step.plan(draft, app=app)]
    assert "extensions" not in keys

    report = commit(tmp_path, draft, monkeypatch)

    assert report.main_written is True
    instance = load(tmp_path / "nodes.yaml")[NODE]["instances"]["DCS.openbeta"]
    assert "extensions" not in instance

    node = outcome_for(report, "nodes.yaml")[0]
    assert node.state == "written"
    assert "DCS.openbeta" in node.note
    assert "SRS was not found" in node.note
    assert node.unported == ("DCS.openbeta",)


def test_an_srs_row_without_a_detected_installation_still_gets_a_port_when_nodes_yaml_has_srs(
        tmp_path, monkeypatch):
    """An existing installation path in ``nodes.yaml`` is an installation this run knows about."""
    from core.utils import configwrite
    configwrite.save_config_file(tmp_path / "nodes.yaml", {NODE: {
        "listen_port": 10042,
        "extensions": {"SRS": {"installation": "/operator/srs"}},
    }}, schemas=None)

    detect_at(monkeypatch, SRS=None)
    draft = complete_draft(tmp_path, plugins={"templates": [], "radio": {"DCS.openbeta": "srs"}})
    plan = [item for item in review_step.plan(draft, app=fake_app(tmp_path))
            if item.target is review_step.EXTENSIONS]
    assert len(plan) == 1
    report = review_step.write(plan, str(tmp_path))

    assert [outcome.state for outcome in report.outcomes] == ["written"]
    assert not report.outcomes[0].note
    document = load(tmp_path / "nodes.yaml")
    assert document[NODE]["instances"]["DCS.openbeta"]["extensions"]["SRS"]["port"] == 5002


def test_the_srs_port_base_starts_above_the_ports_nodes_yaml_already_carries(tmp_path, monkeypatch):
    """A re-run that already holds 5002 and 5006 hands the next instance 5008, never 5004."""
    from core.utils import configwrite
    configwrite.save_config_file(tmp_path / "nodes.yaml", {NODE: {
        "listen_port": 10042,
        "instances": {
            "DCS.old1": {"bot_port": 6666, "home": "/tmp/old1",
                         "extensions": {"SRS": {"port": 5002}}},
            "DCS.old2": {"bot_port": 6667, "home": "/tmp/old2",
                         "extensions": {"SRS": {"port": 5006}}},
        },
    }}, schemas=None)

    detect_at(monkeypatch, SRS="/detected/srs")
    draft = complete_draft(tmp_path, instances=("DCS.new",),
                           plugins={"templates": [], "radio": {"DCS.new": "srs"}})
    plan = [item for item in review_step.plan(draft, app=fake_app(tmp_path))
            if item.target is review_step.EXTENSIONS]
    assert len(plan) == 1
    review_step.write(plan, str(tmp_path))

    document = load(tmp_path / "nodes.yaml")
    assert document[NODE]["instances"]["DCS.new"]["extensions"]["SRS"]["port"] == 5008
    # the instances already on disk keep their own ports
    assert document[NODE]["instances"]["DCS.old1"]["extensions"]["SRS"]["port"] == 5002
    assert document[NODE]["instances"]["DCS.old2"]["extensions"]["SRS"]["port"] == 5006


# ------------------------------------------------------------------------- the write report


def test_a_validator_crash_in_a_plain_write_carries_its_reason(tmp_path, monkeypatch):
    """A validator raising during the write reaches the operator with what went wrong."""
    from core.utils import configwrite
    reason = "'Namespace' object has no attribute 'node'"
    real = configwrite.save_config_file

    def wrapper(path, data, **kwargs):
        if pathlib.Path(path).name == "servers.yaml":
            raise AttributeError(reason)
        return real(path, data, **kwargs)

    draft = complete_draft(tmp_path, plugins={"templates": [], "radio": {"DCS.openbeta": "srs"}})
    plan = review_step.plan(draft, app=fake_app(tmp_path))
    monkeypatch.setattr(configwrite, "save_config_file", wrapper)
    report = review_step.write(plan, str(tmp_path))

    failed = outcome_for(report, "servers.yaml")[0]
    assert failed.state == "failed"
    assert reason in failed.error


def test_a_validator_crash_in_a_merge_carries_its_reason(tmp_path, monkeypatch):
    """The joining merge's funnel appends the exception text the same way."""
    from core.utils import configwrite
    reason = "'Namespace' object has no attribute 'node'"
    detect_at(monkeypatch, SRS="/detected/srs")
    draft = complete_draft(tmp_path, plugins={"templates": [], "radio": {"DCS.openbeta": "srs"}})
    plan = [item for item in review_step.plan(draft, app=fake_app(tmp_path))
            if item.target is review_step.EXTENSIONS]
    assert len(plan) == 1

    def boom(*args, **kwargs):
        raise AttributeError(reason)

    monkeypatch.setattr(configwrite, "patch_config_file", boom)
    report = review_step.write(plan, str(tmp_path))

    failed = report.outcomes[0]
    assert failed.state == "failed"
    assert reason in failed.error


def test_the_report_distinguishes_written_skipped_and_failed(tmp_path, monkeypatch):
    """Three targets, three states — and only the REFUSED one holds the closing file back."""
    operator_file = tmp_path / "plugins" / "greenieboard.yaml"
    operator_file.parent.mkdir(parents=True)
    operator_file.write_text("DEFAULT:\n  foo: 1\n", encoding="utf-8")

    draft = complete_draft(tmp_path, plugins={"templates": ["public-server", "carrier"],
                                              "radio": {"DCS.openbeta": "srs"}})
    report = commit(tmp_path, draft, monkeypatch, fail_on="nodes.yaml")

    seeded = outcome_for(report, "plugins/punishment.yaml")[0]
    skipped = outcome_for(report, "plugins/greenieboard.yaml")[0]
    failed = outcome_for(report, "nodes.yaml")[0]

    assert seeded.state == "written" and seeded.ok is True
    assert skipped.state == "skipped" and skipped.skipped is True and skipped.error == ""
    assert failed.state == "failed" and failed.skipped is False and failed.error
    assert {outcome.state for outcome in report.outcomes} == {"written", "skipped", "failed"}
    assert seeded in report.written and skipped in report.skipped and failed in report.failed
    # a REFUSED target is a refusal, not a skip: the closing file is held back and reported so
    assert report.main_written is False
    assert outcome_for(report, "main.yaml")[0].state == "skipped"
    assert not (tmp_path / "main.yaml").exists()
    # the refused file itself never landed, and the operator's own file is untouched
    assert not (tmp_path / "nodes.yaml").exists()
    assert operator_file.read_text(encoding="utf-8") == "DEFAULT:\n  foo: 1\n"


def test_a_skipped_closing_file_is_never_reported_as_written(tmp_path, monkeypatch):
    """A refused servers.yaml leaves main.yaml unwritten, and the report says exactly that."""
    draft = complete_draft(tmp_path, plugins={"templates": [], "radio": {"DCS.openbeta": "srs"}})
    report = commit(tmp_path, draft, monkeypatch, fail_on="servers.yaml")

    main = outcome_for(report, "main.yaml")[0]
    assert main.ok is False and main.skipped is True
    assert report.main_written is False and report.completed is False
    assert not (tmp_path / "main.yaml").exists()


# ------------------------------------------------------------------------------ the screen

@pytest.mark.asyncio
async def test_the_review_screen_lists_every_file_the_commit_will_write(tmp_path, monkeypatch):
    """The "will be written" list is the plan itself: the seeded plugin files appear on it."""
    from conftest import asgi_client
    from services.webservice.setup import discord as discord_step
    from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
    from services.webservice.setup.tour import STEP_PATH
    from services.webservice.setup.window import SetupWindow

    detect_at(monkeypatch, SRS="/detected/srs")
    draft = complete_draft(tmp_path, plugins={"templates": ["public-server", "carrier"],
                                              "radio": {"DCS.openbeta": "srs"}})
    app = create_setup_app(SetupWindow(code="ABCD2345"), config_dir=tmp_path,
                           listen=SETUP_LISTEN, node=NODE)
    discord_step.token_store(app)[discord_step.TOKEN_KEY] = "a-bot-token"

    async with asgi_client(app) as client:
        assert (await client.get(SETUP_PATH, params={"code": "ABCD2345"})).status_code == 303
        await client.get(SETUP_PATH)
        page = await client.get(STEP_PATH, params={"n": "10"})

    html = page.text
    for relpath in ("plugins/punishment.yaml", "plugins/greenieboard.yaml"):
        assert f"config/{relpath}" in html, relpath
    assert "config/nodes.yaml" in html
    planned = [item.target.relpath for item in review_step.plan(draft, app=app)]
    # one row per FILE: nodes.yaml is planned twice (the node document, then step 9's extension
    # merge) and the screen folds the second entry into the same row
    assert html.count('class="path"') == len(dict.fromkeys(planned))



def test_one_nodes_yaml_read_per_plan_build(tmp_path, monkeypatch):
    """The bot port AND both SRS rules of one build come from ONE read of the pre-run ``nodes.yaml``.

    The ports are asserted as values, not only as a call count: one document must yield the SRS port
    above the highest one on disk and the bot port above the highest ``bot_port`` (the pins above hold
    the same numbers for a document that is read by each reader).
    """
    from core.utils import configwrite
    configwrite.save_config_file(tmp_path / "nodes.yaml", {NODE: {
        "listen_port": 10042,
        "extensions": {"SRS": {"installation": "/operator/srs"}},
        "instances": {"DCS.old": {"bot_port": 6666, "home": "/tmp/old",
                                  "extensions": {"SRS": {"port": 5002}}}},
    }}, schemas=None)

    reads: list = []
    real = review_step._nodes_on_disk

    def counting(app=None):
        reads.append(app)
        return real(app)

    monkeypatch.setattr(review_step, "_nodes_on_disk", counting)
    detect_at(monkeypatch, SRS=None)
    draft = complete_draft(tmp_path, instances=("DCS.new",),
                           plugins={"templates": [], "radio": {"DCS.new": "srs"}})
    plan = review_step.plan(draft, app=fake_app(tmp_path))
    assert len(reads) == 1

    nodes = next(item for item in plan if item.target is review_step.NODES)
    assert nodes.document[NODE]["instances"]["DCS.new"]["bot_port"] == 6667
    report = review_step.write(plan, str(tmp_path))
    assert report.main_written is True
    document = load(tmp_path / "nodes.yaml")
    assert document[NODE]["instances"]["DCS.new"]["extensions"]["SRS"]["port"] == 5004


@pytest.mark.asyncio
async def test_the_done_screen_names_the_instances_that_got_no_srs_port(tmp_path, monkeypatch):
    """A FULLY successful commit says on its Done screen which instance got no SRS port.

    The Review step's results are the FAILED-commit screen, so a successful commit would otherwise
    never mention the miss. The control is the same walk on a machine where SRS was found: no note, no
    line.
    """
    from conftest import asgi_client
    from services.webservice.setup.tour import STEP_PATH
    from test_webui_setup_review import build, enter, seed_secrets, token_of

    async def done_screen(config_dir) -> str:
        app, _ = build(config_dir)
        seed_secrets(app)                 # the create path stores its own database password
        async with asgi_client(app) as client:
            await enter(client)
            review = await client.get(STEP_PATH, params={"n": "10"})
            done = await client.post(STEP_PATH, data={"_csrf_token": token_of(review.text),
                                                      "n": "10", "action": "write"})
        assert done.status_code == 200
        return done.text

    def srs_row_answers() -> dict:
        return {"templates": [], "radio": {"DCS.openbeta": "srs"}}

    # no SRS on this machine: the commit succeeds and the Done screen names the instance
    detect_at(monkeypatch, SRS=None)
    complete_draft(tmp_path, plugins=srs_row_answers())
    html = await done_screen(tmp_path)
    assert "Setup is done" in html
    assert "No SRS port was set for DCS.openbeta" in html
    assert "SRS was not found" in html

    # the control: SRS found, so a port was allocated and the screen says nothing about ports — even
    # though the report DOES carry another note (a seeded plugin file the operator already had)
    control = tmp_path / "srs-found"
    control.mkdir()
    detect_at(monkeypatch, SRS="/opt/srs")
    existing = control / "plugins" / "punishment.yaml"
    existing.parent.mkdir(parents=True)
    existing.write_text("DEFAULT: {}\n", encoding="utf-8")
    complete_draft(control, plugins={"templates": ["public-server"],
                                     "radio": {"DCS.openbeta": "srs"}})
    other = await done_screen(control)
    assert "Setup is done" in other
    assert "No SRS port was set" not in other
    assert load(control / "nodes.yaml")[NODE]["instances"]["DCS.openbeta"][
        "extensions"]["SRS"]["port"] == 5002
    assert existing.read_text(encoding="utf-8") == "DEFAULT: {}\n"
