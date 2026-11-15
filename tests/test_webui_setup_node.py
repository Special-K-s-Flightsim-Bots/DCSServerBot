"""Step 4 of the tour — This node: the DCS installation, the listen port, the static-IP switch and
the two DGSA banlist opt-ins.

TWO LAYERS, both over the shipped code:

* the VIEW is driven through a real ASGI request against the shipped setup application, so the
  DCS radio list, the typed-path row, the decline box, the port field, the static-IP switch and the
  DGSA block are the shipped ones and not a description of them;
* the ANSWER RESOLUTION (:func:`services.webservice.setup.tour._resolve_node`) and the config shapes
  (:mod:`services.webservice.setup.node`) are driven directly, so the classification of an invalid
  answer and the parity with the installer's own builders are pinned.

DETECTION READS THIS MACHINE. The DCS candidates come from the ``win32`` registry and are EMPTY on
this host (asserted for real); the renders therefore seed the detection on the application state, and
one test exercises the empty, real detection result. A REAL port conflict IS exercised: a listening
socket is bound and its port posted, so the "already listening" branch runs against a live socket.
"""

from __future__ import annotations

import ast
import pathlib
import re
import socket
import sys

from html import unescape

import pytest

from conftest import asgi_client
from core.utils import setup_config
from services.webservice.setup import node as node_step
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.state import SetupDraft, draft_path
from services.webservice.setup.tour import LANGUAGE_PATH, STEP_PATH, rail_steps
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

NO_DETECTION = node_step.Detection()
WITH_IP = node_step.Detection(public_ip="203.0.113.9")
CANDIDATES = (("DCS World", "/opt/dcs"), ("DCS World OpenBeta", "/opt/dcs.openbeta"))
WITH_CANDIDATES = node_step.Detection(candidates=CANDIDATES, public_ip="203.0.113.9")


def build(config_dir, *, code: str = CODE, detection: node_step.Detection | None = None):
    window = SetupWindow(code=code, wrong_delay=0.0)
    app = create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN)
    if detection is not None:
        app.state.setup_node_detection = detection
    return app, window


async def enter(client, code: str = CODE):
    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    return await client.get(SETUP_PATH)


def token_of(html: str) -> str:
    found = TOKEN_RE.search(html)
    assert found, "the step form carries no CSRF token"
    return found.group(1)


def rail_rows(html: str) -> list[str]:
    """The rail's row labels, in order — scoped to the rail, so a card field label is never one."""
    block = re.search(r'<details class="rail".*?</details>', html, re.S)
    scope = block.group(0) if block else ""
    return re.findall(r'<span class="lbl">([^<]*)</span>', scope)


def seed(config_dir, *, step: int = 3, discord: bool = True, node: dict | None = None):
    draft = SetupDraft(step=step, discord=discord)
    draft.node = dict(node or {})
    draft.save(config_dir)


def form(token: str, **fields) -> dict:
    data = {"_csrf_token": token, "n": "4"}
    data.update(fields)
    return data


# ------------------------------------------------------------------------------- the view

@pytest.mark.asyncio
async def test_the_card_shows_the_five_rows_and_the_installers_own_paragraph(tmp_path):
    from services.webservice.setup import tour

    seed(tmp_path)
    app, _ = build(tmp_path, detection=WITH_IP)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert "DCS World installation" in html
    assert "Continue without a DCS installation" in html
    assert "Where DCS World is installed. The bot writes Scripts/MissionScripting.lua there." in html
    assert "Other — I will enter the path" in html
    assert "listen port" in html
    assert "Other nodes of this installation talk to this one on this port." in html
    # the installer's own DGSA paragraph, verbatim
    assert tour.T_NODE_DGSA_INTRO in unescape(html)
    assert 'name="dgsa_dcs" checked' in html
    assert 'name="dgsa_discord" checked' in html
    # the static-IP switch names the detected address
    assert 'name="static_ip"' in html
    assert "Is 203.0.113.9 a static IP-address for this node?" in html


@pytest.mark.asyncio
async def test_the_listen_port_is_prefilled_with_the_derived_next_free_port(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path, detection=NO_DETECTION)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "4"})).text
    expected = node_step.next_free_listen_port()
    assert f'name="listen_port" value="{expected}"' in html


@pytest.mark.asyncio
async def test_the_detected_candidates_are_a_radio_list(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert 'name="dcs_choice" value="0"' in html
    assert 'name="dcs_choice" value="1"' in html
    assert 'name="dcs_choice" value="other"' in html
    assert "DCS World" in html and "/opt/dcs.openbeta" in html
    # the FIRST detected installation is preselected — the machine's own answer, never a blank list
    assert 'name="dcs_choice" value="0" checked' in html
    assert 'name="dcs_choice" value="1" checked' not in html
    assert 'name="dcs_choice" value="other" checked' not in html


@pytest.mark.asyncio
async def test_the_first_candidate_is_preselected_but_a_saved_answer_wins(tmp_path):
    """The default fills an EMPTY choice only: a picked row, or a draft's own path, stays marked."""
    seed(tmp_path)
    app, _ = build(tmp_path, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        await enter(client)
        fresh = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert 'name="dcs_choice" value="0" checked' in fresh

    # the operator's own stored row wins over the machine's default
    picked = tmp_path / "picked"
    seed(picked, node={"dcs_choice": "1", "dcs_installation": "/opt/dcs.openbeta"})
    app, _ = build(picked, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        await enter(client)
        stored = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert 'name="dcs_choice" value="1" checked' in stored
    assert 'name="dcs_choice" value="0" checked' not in stored

    # a draft written before the choice was stored derives the row from its own path
    legacy = tmp_path / "legacy"
    seed(legacy, node={"dcs_installation": "/opt/dcs.openbeta"})
    app, _ = build(legacy, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        await enter(client)
        derived = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert 'name="dcs_choice" value="1" checked' in derived
    assert 'name="dcs_choice" value="0" checked' not in derived


@pytest.mark.asyncio
async def test_choosing_a_candidate_writes_its_path(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        html = (await enter(client)).text
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        response = await client.post(STEP_PATH, data=form(token_of(page), dcs_choice="1",
                                                          listen_port="10042"))
    assert response.status_code == 303
    draft = SetupDraft.load(tmp_path)
    assert draft.node["dcs_installation"] == "/opt/dcs.openbeta"
    assert draft.node["no_dcs"] is False
    assert draft.step == 4


@pytest.mark.asyncio
async def test_the_other_row_reveals_the_path_input_and_validates_it(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path, detection=NO_DETECTION)
    real = tmp_path / "DCS World"
    real.mkdir()
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        assert 'name="dcs_choice" value="other" checked' in page
        assert 'name="dcs_path"' in page

        missing = await client.post(STEP_PATH, data=form(token_of(page), dcs_choice="other",
                                                         dcs_path="/does/not/exist",
                                                         listen_port="10042"))
        assert missing.status_code == 200
        assert "That folder does not exist: /does/not/exist." in missing.text

        ok = await client.post(STEP_PATH, data=form(
            token_of(missing.text), dcs_choice="other", dcs_path=str(real),
            listen_port="10042"))
    assert ok.status_code == 303
    assert SetupDraft.load(tmp_path).node["dcs_installation"] == str(real)


@pytest.mark.asyncio
async def test_the_other_row_sticks_and_reveals_with_candidates_present(tmp_path):
    """The operator's own case: candidates detected, the operator picks "other".

    The pick must survive the row's own re-render (the progressive-enhancement submit, ``stay``) with
    no path typed yet, and the path field must be revealed — the stored row, not the derived path, is
    what marks the choice.
    """
    seed(tmp_path)
    app, _ = build(tmp_path, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        # nothing is chosen yet, and the path field is not drawn
        assert 'name="dcs_choice" value="other" checked' not in page
        assert 'name="dcs_path"' not in page
        after = await client.post(STEP_PATH, data=form(token_of(page), dcs_choice="other",
                                                        stay="1", listen_port="10042"))
    assert after.status_code == 200
    assert 'name="dcs_choice" value="other" checked' in after.text
    assert 'name="dcs_choice" value="0" checked' not in after.text
    assert 'name="dcs_path"' in after.text


@pytest.mark.asyncio
async def test_other_with_a_path_keeps_both_the_choice_and_the_path(tmp_path):
    """A POST naming ``other`` plus a real path stores the choice AND the path."""
    seed(tmp_path)
    app, _ = build(tmp_path, detection=WITH_CANDIDATES)
    real = tmp_path / "DCS World"
    real.mkdir()
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        ok = await client.post(STEP_PATH, data=form(token_of(page), dcs_choice="other",
                                                    dcs_path=str(real), listen_port="10042"))
    assert ok.status_code == 303
    draft = SetupDraft.load(tmp_path)
    assert draft.node["dcs_choice"] == "other"
    assert draft.node["dcs_installation"] == str(real)


@pytest.mark.asyncio
async def test_other_with_no_path_refuses_in_the_operators_words(tmp_path):
    """A POST naming ``other`` with no path is refused on the step, names the path, keeps the row."""
    seed(tmp_path)
    app, _ = build(tmp_path, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        refused = await client.post(STEP_PATH, data=form(token_of(page), dcs_choice="other",
                                                         listen_port="10042"))
    assert refused.status_code == 200
    assert "Enter the path to your DCS World installation." in refused.text
    # the picked row is kept, so the path field is revealed for the next attempt
    assert 'name="dcs_choice" value="other" checked' in refused.text
    assert 'name="dcs_path"' in refused.text
    assert SetupDraft.load(tmp_path).node["dcs_choice"] == "other"


@pytest.mark.asyncio
async def test_a_detected_candidate_row_still_writes_its_path(tmp_path):
    """A candidate row keeps working: the re-render keeps it marked, writes its own path, no field."""
    seed(tmp_path)
    app, _ = build(tmp_path, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        after = await client.post(STEP_PATH, data=form(token_of(page), dcs_choice="0",
                                                       stay="1", listen_port="10042"))
        assert after.status_code == 200
        assert 'name="dcs_choice" value="0" checked' in after.text
        assert 'name="dcs_path"' not in after.text
        done = await client.post(STEP_PATH, data=form(token_of(after.text), dcs_choice="0",
                                                      listen_port="10042"))
    assert done.status_code == 303
    draft = SetupDraft.load(tmp_path)
    assert draft.node["dcs_choice"] == "0"
    assert draft.node["dcs_installation"] == "/opt/dcs"


@pytest.mark.asyncio
async def test_declining_removes_the_dcs_servers_row_and_keeps_the_rest(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path, detection=WITH_IP)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        assert "DCS servers" in rail_rows(page)
        response = await client.post(STEP_PATH, data=form(token_of(page), no_dcs="on",
                                                          dgsa_dcs="on", dgsa_discord="on",
                                                          listen_port="10042"))
        assert response.status_code == 303
        after = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert "DCS servers" not in rail_rows(after)
    rows = rail_rows(after)
    assert rows == ["Language", "General", "Database", "This node", "Discord — the bot",
                    "Discord — roles and channels", "Web console", "What do you run?",
                    "Review and write"]
    assert len(rail_steps(True, False)) == 9
    assert SetupDraft.load(tmp_path).node["no_dcs"] is True


@pytest.mark.asyncio
async def test_the_static_ip_switch_is_shown_only_when_an_address_was_detected(tmp_path):
    seed(tmp_path)
    without, _ = build(tmp_path, detection=NO_DETECTION)
    async with asgi_client(without) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert 'name="static_ip"' not in page
    assert "static IP-address" not in page

    seeded = tmp_path / "second"
    seed(seeded)
    with_ip, _ = build(seeded, detection=WITH_IP)
    async with asgi_client(with_ip) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        await client.post(STEP_PATH, data=form(token_of(page), static_ip="on", no_dcs="on",
                                               dgsa_dcs="on", listen_port="10042"))
    assert SetupDraft.load(seeded).node["public_ip"] == "203.0.113.9"


@pytest.mark.asyncio
async def test_the_dgsa_block_hides_the_discord_switch_without_discord_support(tmp_path):
    seed(tmp_path, discord=False)
    app, _ = build(tmp_path, detection=NO_DETECTION)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert 'name="dgsa_dcs"' in page
    assert 'name="dgsa_discord"' not in page


@pytest.mark.asyncio
async def test_an_invalid_port_is_reported_on_the_step_and_keeps_the_step(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path, detection=NO_DETECTION)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        bad = await client.post(STEP_PATH, data=form(token_of(page), no_dcs="on",
                                                     listen_port="not-a-port"))
    assert bad.status_code == 200
    assert "The listen port must be a whole number between 1 and 65535." in bad.text
    assert SetupDraft.load(tmp_path).step == 3


@pytest.mark.asyncio
async def test_a_port_already_listening_is_reported_on_the_step(tmp_path):
    seed(tmp_path)
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    busy_port = holder.getsockname()[1]
    try:
        app, _ = build(tmp_path, detection=NO_DETECTION)
        async with asgi_client(app) as client:
            await enter(client)
            page = (await client.get(STEP_PATH, params={"n": "4"})).text
            response = await client.post(STEP_PATH, data=form(token_of(page), no_dcs="on",
                                                              listen_port=str(busy_port)))
        assert response.status_code == 200
        assert f"Something is already listening on port {busy_port}." in response.text
        assert SetupDraft.load(tmp_path).step == 3
    finally:
        holder.close()


@pytest.mark.asyncio
async def test_the_answers_land_in_the_draft_and_never_in_main_yaml(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path, detection=WITH_IP)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        await client.post(STEP_PATH, data=form(token_of(page), no_dcs="on", static_ip="on",
                                               dgsa_dcs="on", dgsa_discord="on",
                                               listen_port="10042"))
    draft = SetupDraft.load(tmp_path)
    assert draft.node["listen_port"] == 10042
    assert draft.node["public_ip"] == "203.0.113.9"
    assert draft.node["dgsa_dcs"] is True and draft.node["dgsa_discord"] is True
    body = draft_path(tmp_path).read_text(encoding="utf-8")
    assert "listen_port: 10042" in body and "node:" in body
    assert not (tmp_path / "main.yaml").exists()


@pytest.mark.asyncio
async def test_the_card_is_translated_when_the_tour_language_is_german(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path, detection=NO_DETECTION)
    async with asgi_client(app) as client:
        await enter(client)
        await client.get(LANGUAGE_PATH, params={"lang": "de", "next": f"{STEP_PATH}?n=4"})
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert "Dieser Node" in rail_rows(page)
    assert "DCS-World-Installation" in page
    assert 'name="dgsa_dcs" checked' in page


@pytest.mark.asyncio
async def test_the_autoupdate_control_is_shown_checked_when_dcs_is_configured(tmp_path):
    """The control is the installer's own question, drawn checked by default."""
    seed(tmp_path, node={"dcs_choice": "0", "dcs_installation": "/opt/dcs"})
    app, _ = build(tmp_path, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert 'name="dcs_autoupdate" checked' in page
    assert "Do you want your DCS installation being auto-updated by the bot?" in page


@pytest.mark.asyncio
async def test_the_autoupdate_control_is_absent_without_a_dcs_installation(tmp_path):
    """Declining DCS removes the whole block — and the auto-update question with it."""
    seed(tmp_path, node={"no_dcs": True})
    app, _ = build(tmp_path, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert 'name="dcs_autoupdate"' not in page
    assert "being auto-updated by the bot" not in page


@pytest.mark.asyncio
async def test_posting_the_autoupdate_off_is_stored_in_the_draft(tmp_path):
    """The off case is the one a default cannot fake: an unchecked box must reach the draft."""
    seed(tmp_path, node={"dcs_choice": "0", "dcs_installation": "/opt/dcs"})
    app, _ = build(tmp_path, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        assert 'name="dcs_autoupdate" checked' in page
        response = await client.post(STEP_PATH, data=form(token_of(page), dcs_choice="0",
                                                          listen_port="10042"))
    assert response.status_code == 303
    assert SetupDraft.load(tmp_path).node["dcs_autoupdate"] is False


@pytest.mark.asyncio
async def test_posting_the_autoupdate_on_keeps_the_answer_true(tmp_path):
    seed(tmp_path, node={"dcs_choice": "0", "dcs_installation": "/opt/dcs"})
    app, _ = build(tmp_path, detection=WITH_CANDIDATES)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        response = await client.post(STEP_PATH, data=form(token_of(page), dcs_choice="0",
                                                          dcs_autoupdate="on",
                                                          listen_port="10042"))
    assert response.status_code == 303
    assert SetupDraft.load(tmp_path).node["dcs_autoupdate"] is True


# ------------------------------------------------------------------------------- the resolution

def test_resolve_node_requires_a_choice_when_candidates_exist():
    from services.webservice.setup.tour import _resolve_node

    answers, failure = _resolve_node({}, WITH_CANDIDATES, node_step.NodeAnswers(), discord=True,
                                     validate=True)
    assert answers is None and failure[0] == "dcs_choice_required"


def test_resolve_node_reports_a_missing_typed_path():
    from services.webservice.setup.tour import _resolve_node

    answers, failure = _resolve_node({"dcs_choice": "other", "dcs_path": "/nope"}, NO_DETECTION,
                                     node_step.NodeAnswers(), discord=True, validate=True)
    assert answers is None and failure[0] == "dcs_path_missing" and failure[1]["path"] == "/nope"


def test_resolve_node_forces_the_discord_banlist_off_without_discord_support():
    from services.webservice.setup.tour import _resolve_node

    answers, failure = _resolve_node({"no_dcs": "on", "dgsa_dcs": "on", "dgsa_discord": "on"},
                                     NO_DETECTION, node_step.NodeAnswers(), discord=False,
                                     validate=True)
    assert failure is None
    assert answers.dgsa_dcs is True and answers.dgsa_discord is False


# ------------------------------------------------------------------------------- the module

def test_the_config_shapes_match_the_installers_own_builders():
    answers = node_step.NodeAnswers(listen_port=10042, use_upnp=True, dcs_installation="/opt/dcs",
                                    public_ip="203.0.113.9", dgsa_dcs=True, dgsa_discord=False,
                                    dcs_autoupdate=True)
    assert node_step.node_block(answers) == setup_config.build_node(
        listen_port=10042, use_upnp=True, public_ip="203.0.113.9", dcs_installation="/opt/dcs",
        dcs_autoupdate=True)
    assert node_step.cloud_block(answers) == {"dcs": True, "discord": False}


def test_the_dcs_autoupdate_defaults_to_the_installers_own_true():
    """The installer's ``Confirm.ask(..., default=True)`` is the default carried in the draft."""
    assert node_step.NodeAnswers().dcs_autoupdate is True
    assert node_step.answers_from_mapping(None).dcs_autoupdate is True


def test_the_dcs_autoupdate_answer_round_trips_through_the_draft_shape():
    answers = node_step.answers_from_mapping({"dcs_autoupdate": False})
    assert answers.dcs_autoupdate is False
    document = node_step.non_secret_document(answers)
    assert document["dcs_autoupdate"] is False
    assert node_step.answers_from_mapping(document).dcs_autoupdate is False


def test_the_node_block_writes_the_dcs_autoupdate_only_when_true():
    """``build_node``'s contract: the key is present exactly when the answer is True."""
    on = node_step.NodeAnswers(dcs_installation="/opt/dcs", dcs_autoupdate=True)
    assert node_step.node_block(on)["DCS"]["autoupdate"] is True
    off = node_step.NodeAnswers(dcs_installation="/opt/dcs", dcs_autoupdate=False)
    assert "autoupdate" not in node_step.node_block(off)["DCS"]


def test_the_dcs_autoupdate_is_dropped_without_a_dcs_installation():
    """With no DCS installation ``build_node`` writes no ``DCS`` block at all, so the answer is
    never written — the installer's own ``if dcs_installation:`` guard, shared with the builder."""
    answers = node_step.NodeAnswers(no_dcs=True, dcs_installation="/opt/dcs", dcs_autoupdate=True)
    assert "DCS" not in node_step.node_block(answers)


def test_the_cloud_block_is_absent_when_both_banlists_are_off():
    answers = node_step.NodeAnswers(dgsa_dcs=False, dgsa_discord=False)
    assert node_step.cloud_block(answers) is None


def test_the_answers_default_to_the_installers_own():
    answers = node_step.answers_from_mapping(None)
    assert answers.listen_port == node_step.DEFAULT_LISTEN_PORT
    assert answers.no_dcs is False
    assert answers.dgsa_dcs is True and answers.dgsa_discord is True


def test_parse_listen_port_bounds():
    assert node_step.parse_listen_port("10042") == 10042
    assert node_step.parse_listen_port(65535) == 65535
    assert node_step.parse_listen_port("0") is None
    assert node_step.parse_listen_port("65536") is None
    assert node_step.parse_listen_port("abc") is None
    assert node_step.parse_listen_port(None) is None


def test_next_listen_port_follows_the_installer_formula():
    assert node_step.next_listen_port() == node_step.DEFAULT_LISTEN_PORT
    assert node_step.next_listen_port([{"listen_port": 10042}]) == 10043
    assert node_step.next_listen_port([{"listen_port": 10042}, {"listen_port": 10060}]) == 10061


def test_the_real_dcs_detection_runs_and_returns_a_list():
    """The installer's own detection runs here and answers a list of candidates.

    Off Windows there is no registry source, so the list is empty; on a Windows host it names the
    installs the registry holds. Either way it is the call the step makes, and it never raises.
    """
    candidates = node_step.detect_candidates()
    assert isinstance(candidates, list)
    if sys.platform != "win32":
        assert candidates == []      # no registry source off Windows


# ------------------------------------------------------------------------------- the entry point

@pytest.mark.asyncio
async def test_the_real_entry_point_serves_step_four_over_a_socket(tmp_path, monkeypatch):
    """``start_setup_console`` (the ``run.py`` boot path) up to a real Step 4 over a real socket."""
    import httpx

    from services.webservice.setup.boot import should_run_setup, start_setup_console

    async def fake_public_ip():
        return "203.0.113.9"

    monkeypatch.setattr(node_step, "detect_candidates", lambda: [("DCS World", str(tmp_path))])
    monkeypatch.setattr(node_step, "detect_public_ip", fake_public_ip)
    monkeypatch.setattr(node_step, "detect_upnp", lambda: False)

    seed(tmp_path)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    service = await start_setup_console(config_dir=tmp_path, open_browser=False, announce=False,
                                        port=port)
    try:
        assert service.bound, "the setup console never bound its port"
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                     follow_redirects=True, timeout=5.0) as client:
            entered = await client.get("/setup", params={"code": service.window.display_code})
            assert entered.status_code == 200
            page = await client.get(f"{STEP_PATH}", params={"n": "4"})
            assert page.status_code == 200
            assert "DCS World installation" in page.text
            assert 'name="dcs_choice" value="0"' in page.text
            assert "Is 203.0.113.9 a static IP-address for this node?" in page.text
    finally:
        await service.stop()

    # and the window is still open: nothing in this step writes main.yaml
    assert should_run_setup(tmp_path)


# ------------------------------------------------------------------------------- i18n coverage

def test_every_step_four_string_is_an_i18n_marked_literal():
    """The step's strings are all ``i18n._``-marked (so the catalog test covers them)."""
    source = (REPO_ROOT / "services/webservice/setup/tour.py").read_text(encoding="utf-8")
    marked = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "i18n" and node.args
                and isinstance(node.args[0], ast.Constant)):
            marked.add(node.args[0].value)
    for msgid in ("DCS World installation", "Continue without a DCS installation",
                  "Do you want your DCS installation being auto-updated by the bot?",
                  "Do you want to use the global banlist for DCS players?",
                  "Do you want to use the global banlist for Discord users?"):
        assert msgid in marked, msgid
