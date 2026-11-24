"""Step 5' of the tour — "Your community" on the no-Discord path: the UCID screen and its validation.

TWO LAYERS, both over the shipped code:

* the SCREEN is driven through a real ASGI request against the shipped setup application, so the
  explanation's POSITION above the rows, the per-value fields and the ``+ Add`` / remove controls are
  the shipped ones and not a description of them;
* the ANSWER RESOLUTION (:mod:`services.webservice.setup.community`) is driven directly, so the
  classification of an unusable UCID and the stored shape are pinned.

A UCID is exactly 32 characters — the DCS player id, not a Discord role. The validator is the
project's own :func:`core.utils.helper.is_ucid`, so these pins also cover the normalisation order:
lowercase FIRST, then validate (``is_ucid`` refuses anything that is not already lowercase).
"""

from __future__ import annotations

from html import unescape

import pytest

from conftest import asgi_client
from services.webservice.setup import community as community_step
from services.webservice.setup.state import SetupDraft
from services.webservice.setup.tour import STEP_PATH
# the harness the community screen is already driven with, imported by name rather than duplicated
from test_webui_setup_discord import build, enter, form, seed, token_of

#: 32 characters, lowercase letters and digits — what DCS hands out
UCID_A = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
UCID_B = "f0e1d2c3b4a5968778695a4b3c2d1e0f"
#: one character short and one character long, both otherwise well-formed
UCID_31 = UCID_A[:-1]
UCID_33 = UCID_A + "e"

#: the sentences the screen must carry (the heading, then the explanation the rows are read against)
UCID_HEAD = "These users are UCIDs, not Discord roles"
UCID_EXPLANATION = "A UCID is the 32-character player id DCS gives a user"


def shown(html: str) -> str:
    """The page as a reader sees it: an apostrophe is escaped in the markup, not in the sentence."""
    return unescape(html)


def answers_of(**fields) -> dict:
    """A resolution form: the group name plus one repeated field per role."""
    data = {"guild_name": "My Group"}
    data.update(fields)
    return data


@pytest.fixture(autouse=True)
def fixture_values_are_what_the_tests_claim():
    """The pins below are about lengths, so the lengths are asserted, not assumed."""
    assert len(UCID_A) == 32 and len(UCID_B) == 32
    assert len(UCID_31) == 31 and len(UCID_33) == 33
    assert UCID_A.isalnum() and UCID_A == UCID_A.lower()


# ------------------------------------------------------------------------------- the rendered screen

@pytest.mark.asyncio
async def test_the_explanation_is_rendered_above_the_first_role_row(tmp_path):
    """The position, not only the presence: the UCID sentence is read BEFORE the labels it explains."""
    seed(tmp_path, discord=False)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = shown((await client.get(STEP_PATH, params={"n": "5"})).text)
    assert UCID_HEAD in html, "the screen does not say that these are UCIDs"
    assert UCID_EXPLANATION in html
    assert "not a Discord role" in html
    heading = html.index(UCID_HEAD)
    explanation = html.index(UCID_EXPLANATION)
    first_row = html.index('<div class="role-row">')
    assert heading < explanation < first_row, \
        "the UCID explanation must sit above the first role row"
    # and it is NOT the old note below the rows: the sentence appears exactly once on the screen
    assert html.count(UCID_EXPLANATION) == 1


@pytest.mark.asyncio
async def test_every_role_row_carries_one_field_per_ucid_and_its_own_add_control(tmp_path):
    draft = seed(tmp_path, discord=False)
    draft.community = {"guild_name": "My Group",
                       "users": {"Admin": [UCID_A, UCID_B], "DCS Admin": []}}
    draft.save(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "5"})).text
    chunks = html.split('<div class="role-row">')[1:]
    assert len(chunks) == 2, "the screen no longer draws one row per internal role"
    admin, dcs_admin = chunks
    # one value per field, never the comma-joined single field it used to be
    assert admin.count('type="text" name="user_0"') == 2
    assert f'value="{UCID_A}"' in admin and f'value="{UCID_B}"' in admin
    assert f'value="{UCID_A}, {UCID_B}"' not in admin
    # an empty role still renders ONE field, so it can be filled in without scripting
    assert dcs_admin.count('type="text" name="user_1"') == 1
    for chunk, name in ((admin, "user_0"), (dcs_admin, "user_1")):
        assert f'class="add" data-name="{name}"' in chunk, f"{name} has no + Add control"
        assert 'class="rm"' in chunk, f"{name} has no removal control"
    # the removal control is labelled from the catalogs, never by a bare glyph
    assert 'aria-label="Remove"' in html
    # the add/remove behaviour is wired on THIS step (it used to be emitted on the roles step only)
    assert "button.rm" in html and "querySelectorAll(\"button.add\")" in html


# --------------------------------------------------------------------------- the UCID validation

def test_a_32_character_ucid_is_accepted_and_stored_lowercase():
    answers, failure = community_step.resolve_community(
        answers_of(user_0=UCID_A, user_1=UCID_B), community_step.CommunityAnswers(), validate=True)
    assert failure is None and answers is not None
    assert answers.users == {"Admin": (UCID_A,), "DCS Admin": (UCID_B,)}
    assert community_step.non_secret_document(answers)["users"] == {
        "Admin": [UCID_A], "DCS Admin": [UCID_B]}


def test_uppercase_input_is_normalised_before_it_is_validated():
    """``is_ucid`` refuses anything not already lowercase, so the lowercase step must come first."""
    answers, failure = community_step.resolve_community(
        answers_of(user_0=UCID_A.upper()), community_step.CommunityAnswers(), validate=True)
    assert failure is None and answers is not None
    assert answers.users["Admin"] == (UCID_A,)


@pytest.mark.parametrize("value", [UCID_31, UCID_33], ids=["31-chars", "33-chars"])
def test_a_ucid_that_is_not_32_characters_is_refused_with_the_role_and_the_value(value):
    answers, failure = community_step.resolve_community(
        answers_of(user_1=value), community_step.CommunityAnswers(), validate=True)
    assert answers is None
    assert failure == ("community_ucid_invalid", {"role": "DCS Admin", "value": value})


@pytest.mark.parametrize("value", [UCID_31, UCID_33], ids=["31-chars", "33-chars"])
@pytest.mark.asyncio
async def test_the_refusal_keeps_the_step_open_and_names_the_role_and_the_value(tmp_path, value):
    seed(tmp_path, discord=False)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        response = await client.post(STEP_PATH, data=form(
            token_of(page), 5, guild_name="My Group", user_1=value))
    assert response.status_code == 200, "the step advanced with a UCID the validator refuses"
    notice = shown(response.text)
    assert value in notice, "the notice does not name the offending value"
    assert "Check the DCS Admin entry." in notice, "the notice does not name the role"
    assert "not a UCID" in notice
    # the refusal is readable without scrolling past the rows it is about
    assert response.text.index('role="alert"') < response.text.index('<div class="role-row">')
    # the step did not advance, and the typed value is still in its field
    assert SetupDraft.load(tmp_path).step == 4
    assert f'value="{value}"' in response.text


def test_a_comma_separated_entry_is_still_read_as_several_ucids():
    """The installer's own input shape, and the one that works with scripting off."""
    answers, failure = community_step.resolve_community(
        answers_of(user_0=f"{UCID_A}, {UCID_B}"), community_step.CommunityAnswers(),
        validate=True)
    assert failure is None and answers is not None
    assert answers.users["Admin"] == (UCID_A, UCID_B)


def test_a_role_with_no_users_is_accepted():
    answers, failure = community_step.resolve_community(
        answers_of(), community_step.CommunityAnswers(), validate=True)
    assert failure is None and answers is not None
    assert answers.users == {"Admin": (), "DCS Admin": ()}
    assert community_step.roles_block(answers) == {"Admin": [], "DCS Admin": []}


@pytest.mark.asyncio
async def test_an_empty_role_list_submits_and_stores_empty_lists(tmp_path):
    seed(tmp_path, discord=False)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        response = await client.post(STEP_PATH, data=form(
            token_of(page), 5, guild_name="My Group", user_0="", user_1=""))
    assert response.status_code == 303
    assert SetupDraft.load(tmp_path).community == {
        "guild_name": "My Group", "users": {"Admin": [], "DCS Admin": []}}


@pytest.mark.asyncio
async def test_the_group_name_is_still_reported_before_the_ucids(tmp_path):
    """The name is the first failure: an operator fixing both reads one notice at a time."""
    seed(tmp_path, discord=False)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        response = await client.post(STEP_PATH, data=form(token_of(page), 5, user_0=UCID_31))
    assert "Enter the name of your DCS group." in response.text
    assert "not a UCID" not in response.text


# -------------------------------------------------------------------------------- the stored shape

def test_an_old_draft_with_a_comma_joined_string_still_loads():
    """A draft written before the rows became one value per field must still render and resolve."""
    answers = community_step.answers_from_mapping(
        {"guild_name": "My Group", "users": {"Admin": f"{UCID_A}, {UCID_B}"}})
    assert answers.users == {"Admin": (UCID_A, UCID_B), "DCS Admin": ()}


@pytest.mark.asyncio
async def test_an_old_comma_joined_draft_renders_as_separate_fields(tmp_path):
    draft = seed(tmp_path, discord=False)
    draft.community = {"guild_name": "My Group",
                       "users": {"Admin": f"{UCID_A}, {UCID_B}", "DCS Admin": ""}}
    draft.save(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "5"})).text
    assert html.count('type="text" name="user_0"') == 2
    assert f'value="{UCID_A}"' in html and f'value="{UCID_B}"' in html


def test_a_stored_ucid_list_survives_a_render_and_a_resubmit():
    """Round trip: what the draft holds is what the screen posts back, unchanged."""
    stored = community_step.non_secret_document(community_step.CommunityAnswers(
        guild_name="My Group", users={"Admin": (UCID_A,), "DCS Admin": (UCID_B,)}))
    assert stored == {"guild_name": "My Group",
                      "users": {"Admin": [UCID_A], "DCS Admin": [UCID_B]}}
    reloaded = community_step.answers_from_mapping(stored)
    assert reloaded.users == {"Admin": (UCID_A,), "DCS Admin": (UCID_B,)}
