"""The DCS update dialog's FIELDS, per node shape — and the copy it carries.

Three node shapes decide what the dialog draws, all read from ``nodes.yaml`` with no node call:

* credentials + an ``announce`` block — the branch pick, the version pick, the warning time and the
  announce box, with the branch pick naming the INSTALLED branch and the box ticked;
* no ``DCS.user`` — the branch pick is ABSENT (the update then states no branch and the action
  resolves the installed one), and the body the dialog can send carries no ``branch``;
* no ``DCS.announce`` block — the announce box is ABSENT and the action is called with
  ``announce=False``, never the declaration's ``True``.

The route's read is pinned against the ACTUAL action call (a spy on ``pages.actions.call_action``),
not only against the markup, because a field the dialog omits must not come back through the body.
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest

from services.webservice import dcs_update
from services.webservice.pages import actions as actions_page
from test_webui_node_dcs_update import (  # noqa: F401 - fixtures imported on purpose
    CONFIRM, DCS_PATH, DCS_VERSION_FIELD, HARNESS, NODE, NODES, _inline_scripts,
    _rendered_entries, app_with, config_dir, fleet, requires_node, versions_url,
)
from test_webui_write_pause import client, open_page, sign_in

TICKED = ["0", "1"]
UNTICKED = ["0"]

#: the shortened detail — asserted whole, never as a fragment
DETAIL = ("Every server on the node is warned, then stopped, the installation is updated to the "
          "version you choose and the servers come back on it.")

#: the dialog's own chrome fields, which no node shape changes
CHROME = {"node", "_csrf_token", "_confirm_token",
          getattr(actions_page, "ORIGIN_FIELD", "origin"), getattr(actions_page, "TAB_FIELD", "tab")}

_FIELD = re.compile(r"<(select|input|textarea)\b([^>]*)>", re.I)


@pytest.fixture(autouse=True)
def cached_facts():
    """No test inherits another's cached check: every render starts from a known cache."""
    dcs_update.reset()
    yield
    dcs_update.reset()


@pytest.fixture
def action_calls(monkeypatch):
    """Every call the dialog's route made to the action layer, its parameters recorded verbatim."""
    calls: list[tuple[str, dict]] = []
    real = actions_page.call_action

    async def spy(qualname, ctx, **params):
        calls.append((qualname, dict(params)))
        return await real(qualname, ctx, **params)

    monkeypatch.setattr(actions_page, "call_action", spy)
    return calls


def seed(*, branch="release", version="2.9.0.1", latest="2.9.9.9"):
    """Stand in for the poller: ONE cached check, written before the render."""
    dcs_update.record(NODE, has_dcs=True, branch=branch, version=version, latest=latest,
                      available=True)


def dialog_form(markup: str) -> str:
    """The dialog's own posting form."""
    match = re.search(r'<form[^>]*id="dlg-form"[^>]*>.*?</form>', markup, re.S)
    assert match, "the dialog drew no posting form"
    return match.group(0)


def drawn_fields(markup: str) -> list[dict]:
    """Every field the dialog DRAWS (chrome excluded), in document order."""
    found: list[dict] = []
    for match in _FIELD.finditer(dialog_form(markup)):
        tag, attrs = match.group(1).lower(), match.group(2)
        name = re.search(r'\bname="([^"]*)"', attrs)
        if name is None or name.group(1) in CHROME:
            continue
        kind = re.search(r'\btype="([^"]*)"', attrs)
        found.append({"name": name.group(1),
                      "type": kind.group(1) if kind else tag,
                      "attrs": attrs})
    return found


def drawn_names(markup: str) -> list[str]:
    """The NAMES the dialog's fields carry, chrome excluded, each name once."""
    names: list[str] = []
    for field in drawn_fields(markup):
        if field["name"] not in names:
            names.append(field["name"])
    return names


def options_of(markup: str, field_name: str) -> list[dict]:
    """``(value, label)`` of a pick the dialog drew, in order."""
    block = re.search(rf'<select id="{field_name}-dialog".*?</select>', dialog_form(markup), re.S)
    assert block, f"the dialog drew no {field_name} pick"
    return [{"value": value, "label": label}
            for value, label in re.findall(r'<option value="([^"]*)">([^<]*)</option>',
                                           block.group(0))]


def box_of(markup: str, field_name: str) -> dict:
    """The CHECKBOX the dialog drew for *field_name* (not its hidden OFF companion)."""
    for field in drawn_fields(markup):
        if field["name"] == field_name and field["type"] == "checkbox":
            return field
    raise AssertionError(f"the dialog drew no {field_name} checkbox")


def body_as_drawn(markup: str) -> dict[str, list[str]]:
    """The body a browser submits for the dialog AS DRAWN — one entry per drawn control, in order.

    A checked checkbox submits its companion's OFF value then its own ON value (the pair is rendered
    OFF-then-ON); an unchecked one submits only the companion. A pick submits its selected entry, or
    its first entry when none is marked.
    """
    body: dict[str, list[str]] = {}
    for field in drawn_fields(markup):
        name, kind, attrs = field["name"], field["type"], field["attrs"]
        values = body.setdefault(name, [])
        if kind == "checkbox":
            continue
        if kind == "select":
            block = re.search(rf'<select id="{name}-dialog".*?</select>', dialog_form(markup), re.S)
            assert block, f"no {name} pick"
            chosen = re.search(r'<option value="([^"]*)" selected>', block.group(0)) \
                or re.search(r'<option value="([^"]*)">', block.group(0))
            assert chosen, f"the {name} pick offers nothing"
            values.append(chosen.group(1))
            continue
        value = re.search(r'\bvalue="([^"]*)"', attrs)
        values.append(value.group(1) if value else "")
    # the checkbox(es) come LAST in their own pair: OFF companion already recorded, then ON
    for field in drawn_fields(markup):
        if field["type"] == "checkbox":
            on = re.search(r'\bvalue="([^"]*)"', field["attrs"])
            if "checked" in field["attrs"]:
                body[field["name"]].append(on.group(1) if on else "1")
    return body


async def open_dialog(http, *, node_name: str = NODE, headers: dict | None = None) -> str:
    """The control's dialog, rendered by the real application for *node_name*."""
    page = await open_page(http, NODES)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)
    dialog = await http.post(DCS_PATH + CONFIRM, data={"node": node_name, "_csrf_token": token},
                             headers=headers or {})
    assert dialog.status_code == 200, dialog.text
    return dialog.text


async def submit(http, markup: str, *, node_name: str = NODE, data: dict | None = None):
    """POST the dialog exactly as a browser would minus the fields it did not draw."""
    csrf = re.search(r'name="_csrf_token" value="([^"]+)"', markup).group(1)
    confirm = re.search(r'name="_confirm_token" value="([^"]+)"', markup).group(1)
    body: dict = {"node": node_name, "_csrf_token": csrf, "_confirm_token": confirm}
    body.update(data or {})
    return await http.post(DCS_PATH, data=body)


# ------------------------------------------------- 1. credentials + announce: both fields are drawn

@pytest.mark.asyncio
async def test_a_node_with_credentials_and_an_announce_block_draws_both_fields(config_dir, fleet):
    """Both picks, the warning time and the box — the box ticked, the branch pick naming the branch."""
    seed()
    app = app_with(config_dir, fleet.declare(NODE).cluster())
    async with client(app) as http:
        await sign_in(http, "specialk")
        markup = await open_dialog(http)
    fields = drawn_fields(markup)
    print("\n[credentials+announce] " + repr([(f["name"], f["type"]) for f in fields]))
    assert drawn_names(markup) == ["branch", "version", "warn_time", "announce"]
    assert box_of(markup, "announce")["type"] == "checkbox"
    assert "checked" in box_of(markup, "announce")["attrs"], "the announce box is not ticked"
    branch = options_of(markup, "branch")
    assert branch[0] == {"value": "", "label": "Installed branch: release (no change)"}, branch[0]
    assert [option["label"] for option in branch[1:]] == ["Release", "Testing", "Nightly"]


@pytest.mark.asyncio
async def test_that_shape_posts_true_and_the_installed_branch(config_dir, fleet, action_calls):
    """Left ticked, both halves of the body reach the action: ``announce=True``, no branch named."""
    seed()
    cluster = fleet.declare(NODE).cluster()
    node = cluster.nodes[NODE]
    app = app_with(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        markup = await open_dialog(http)
        await submit(http, markup, data={"announce": TICKED})
    assert [(qualname, params) for qualname, params in action_calls] == [
        ("update_dcs", {"node_name": NODE, "branch": None, "version": None, "warn_time": 60,
                        "announce": True})], action_calls
    assert node.calls == [{"branch": "release", "version": "2.9.9.9", "warn_times": [60],
                           "announce": True}]


# --------------------------------------------------------- 2. no credentials: no branch field

@pytest.mark.asyncio
async def test_a_node_without_credentials_draws_no_branch_field(config_dir, fleet):
    """The pick is ABSENT — not disabled, not empty — and the rest of the dialog still works."""
    seed()
    declarer = fleet.declare(NODE)
    declarer.blocks[NODE].pop("user")
    app = app_with(config_dir, declarer.cluster())
    async with client(app) as http:
        await sign_in(http, "specialk")
        markup = await open_dialog(http)
    print("\n[no credentials] " + repr([(f["name"], f["type"]) for f in drawn_fields(markup)]))
    assert drawn_names(markup) == ["version", "warn_time", "announce"]
    assert 'name="branch"' not in dialog_form(markup)


@pytest.mark.asyncio
async def test_a_node_without_credentials_posts_no_branch_and_the_update_still_runs(
        config_dir, fleet, action_calls):
    """No branch in the body; the action receives ``branch=None`` and resolves the installed one."""
    seed()
    declarer = fleet.declare(NODE)
    declarer.blocks[NODE].pop("user")
    cluster = declarer.cluster()
    node = cluster.nodes[NODE]
    app = app_with(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        markup = await open_dialog(http)
        body = body_as_drawn(markup)
        print("\n[no credentials body] " + repr(body))
        assert "branch" not in body, "the dialog's own body carries a branch"
        await submit(http, markup, data=body)
    assert action_calls == [("update_dcs",
                             {"node_name": NODE, "branch": None, "version": None, "warn_time": 60,
                              "announce": True})], action_calls
    # the ACTION resolved the unnamed branch to the installed one, so the update runs as usual
    assert node.calls == [{"branch": "release", "version": "2.9.9.9", "warn_times": [60],
                           "announce": True}]


# ------------------------------------------------------- 3. no announce block: no box, False

@pytest.mark.asyncio
async def test_a_node_without_an_announce_block_draws_no_box(config_dir, fleet):
    """With no announcement configured the box would promise one that cannot happen."""
    seed()
    app = app_with(config_dir, fleet.declare(NODE, announce=False).cluster())
    async with client(app) as http:
        await sign_in(http, "specialk")
        markup = await open_dialog(http)
    print("\n[no announce] " + repr([(f["name"], f["type"]) for f in drawn_fields(markup)]))
    assert drawn_names(markup) == ["branch", "version", "warn_time"]
    assert 'name="announce"' not in dialog_form(markup), "a hidden OFF companion was drawn"


@pytest.mark.asyncio
async def test_a_node_without_an_announce_block_calls_the_action_with_false(
        config_dir, fleet, action_calls):
    """The missing field must not fall back to the declaration's ``default=True``."""
    seed()
    cluster = fleet.declare(NODE, announce=False).cluster()
    node = cluster.nodes[NODE]
    app = app_with(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        markup = await open_dialog(http)
        await submit(http, markup)
    assert action_calls == [("update_dcs",
                             {"node_name": NODE, "branch": None, "version": None, "warn_time": 60,
                              "announce": False})], action_calls
    assert node.calls[0]["announce"] is False


# ------------------------------------------------------------- 4. with a block: ticked / unticked

@pytest.mark.asyncio
async def test_an_announce_block_sends_true_when_ticked_and_false_when_unticked(config_dir, fleet,
                                                                                action_calls):
    """Both directions of the ONE checkbox, asserted as the exact action call."""
    for body, expected in ((TICKED, True), (UNTICKED, False)):
        seed()
        cluster = fleet.declare(NODE).cluster()
        node = cluster.nodes[NODE]
        app = app_with(config_dir, cluster)
        async with client(app) as http:
            await sign_in(http, "specialk")
            markup = await open_dialog(http)
            await submit(http, markup, data={"announce": body})
        assert [(qualname, params["announce"]) for qualname, params in action_calls][-1] == \
            ("update_dcs", expected), action_calls
        assert node.calls and node.calls[0]["announce"] is expected, node.calls
    assert [params["announce"] for _qualname, params in action_calls] == [True, False], action_calls


# --------------------------------------------------------------------- 5. the shortened detail

@pytest.mark.asyncio
async def test_the_detail_is_exactly_the_short_sentence(config_dir, fleet):
    """Whole string, not a fragment: the declaration's copy and BOTH rendered languages."""
    action = next(item for item in actions_page.NODE_ACTIONS
                  if item.key == actions_page.DCS_UPDATE_KEY)
    assert action.detail == DETAIL
    app = app_with(config_dir, fleet.declare(NODE).cluster())
    async with client(app) as http:
        await sign_in(http, "specialk")
        english = await open_dialog(http)
        german = await open_dialog(http, headers={"accept-language": "de"})
    assert DETAIL in english
    from services.webservice import i18n
    translated = i18n.translations_for("de").gettext(DETAIL)
    assert translated != DETAIL, "German serves the English sentence"
    assert translated in german, translated
    print(f"\n[de] {translated}")


# --------------------------------------------------- 6. the branch marker with an unknown cache

@pytest.mark.asyncio
async def test_the_branch_marker_keeps_the_plain_word_when_the_cache_knows_no_branch(config_dir,
                                                                                    fleet):
    """A cold cache invents no branch: the entry keeps today's wording, its wire value empty."""
    app = app_with(config_dir, fleet.declare(NODE).cluster())
    async with client(app) as http:
        await sign_in(http, "specialk")
        markup = await open_dialog(http)
    assert options_of(markup, "branch")[0] == {"value": "",
                                                "label": "Installed branch (no change)"}


# ------------------------------- 7. the version pick still fills on a dialog with no branch pick

def _drive_without_branch(tmp_path, markup: str, answer: dict) -> dict:
    """Run the shipped dialog script with NO branch pick in the form, and report what it asked."""
    scripts = [source for source in _inline_scripts(markup) if "installed_branch" in source]
    assert scripts, "the rendered dialog carries no version-pick script"
    script = tmp_path / "dcs_versions_no_branch.js"
    script.write_text(scripts[-1], encoding="utf-8")
    answer_file = tmp_path / "answer.json"
    answer_file.write_text(json.dumps(answer), encoding="utf-8")
    initial_file = tmp_path / "initial.json"
    initial_file.write_text(json.dumps(_rendered_entries(markup, DCS_VERSION_FIELD)),
                            encoding="utf-8")
    done = subprocess.run(["node", str(HARNESS), str(script), str(answer_file), str(initial_file),
                           "--no-branch"], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.asyncio
@requires_node
async def test_the_version_pick_still_fills_when_the_form_has_no_branch_pick(config_dir, fleet,
                                                                             tmp_path):
    """A node without credentials keeps a USABLE version pick: the read runs for the cached branch."""
    seed()
    declarer = fleet.declare(NODE)
    declarer.blocks[NODE].pop("user")
    app = app_with(config_dir, declarer.cluster())
    async with client(app) as http:
        await sign_in(http, "specialk")
        markup = await open_dialog(http)
    assert 'name="branch"' not in dialog_form(markup)
    seen = _drive_without_branch(tmp_path, markup, {"versions": ["2.9.9.9", "2.9.0.1", "2.9.0.0"]})
    print("\n[no branch pick] " + json.dumps(seen["onOpen"]))
    assert seen["onOpen"]["asked"] == [versions_url(NODE) + "?branch=release"]
    assert [option["label"] for option in seen["onOpen"]["options"]] == [
        "Newest build on the branch", "2.9.9.9 — newest on this branch", "2.9.0.1 — installed now",
        "2.9.0.0"]
    assert seen["onOpen"]["value"] == ""
