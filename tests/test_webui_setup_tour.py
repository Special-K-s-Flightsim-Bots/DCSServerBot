"""The setup tour: the rail derived from the answers, steps 1 and 2, their writes, and resume.

Driven through a real ASGI request against the SHIPPED application, so the rail's state, the
progress statement (there is exactly one), the two steps' screens and the draft on disk are the
shipped ones and not a description of them.

WHAT THESE DO NOT COVER: no test here boots ``run.py`` or starts a real socket. The steps after these
are their own cards (3 Database, 4 This node, 5 Discord — the bot, 6 Discord — roles and channels,
7 the Web console, 8 DCS servers) and their screens are covered by their own files; what this file
pins is the rail these steps are drawn on, steps 1 and 2, and resume.
"""
from __future__ import annotations

import re

import pytest

from conftest import asgi_client
from services.webservice.setup.app import HEALTH_PATH, SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.state import DRAFT_FILENAME, SetupDraft, draft_path
from services.webservice.setup.tour import FLAGS_PATH, LANGUAGE_PATH, STEP_PATH, THEME_PATH
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')


def build(config_dir, *, code: str = CODE):
    window = SetupWindow(code=code, wrong_delay=0.0)
    return create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN), window


async def enter(client, code: str = CODE):
    """Walk through the gate and return the tour's first screen."""
    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    return await client.get(SETUP_PATH)


def token_of(html: str) -> str:
    found = TOKEN_RE.search(html)
    assert found, "the step form carries no CSRF token"
    return found.group(1)


def rail_rows(html: str) -> list[str]:
    """The rail's row labels, in order (each row is one ``.lbl``)."""
    return re.findall(r'<span class="lbl">([^<]*)</span>', html)


def counts(html: str) -> dict:
    return {"step_of": "step " in html.lower() and " of " in html.lower(),
            "progress": "progress" in html.lower(),
            "aria_current": 'aria-current="step"' in html}


# ------------------------------------------------------------------------------- the rail

@pytest.mark.asyncio
async def test_the_rail_has_ten_rows_with_discord_and_nine_without(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        step_one = await enter(client)
        assert rail_rows(step_one.text) == ["Language", "General", "Database", "This node",
                                            "Discord — the bot", "Discord — roles and channels",
                                            "Web console", "DCS servers", "What do you run?",
                                            "Review and write"]
        # answer step 1, then flip Discord OFF on step 2
        await client.post(STEP_PATH, data={"_csrf_token": token_of(step_one.text), "n": "1"})
        step_two = await client.get(STEP_PATH, params={"n": "2"})
        await client.post(STEP_PATH, data={"_csrf_token": token_of(step_two.text), "n": "2",
                                           "autoupdate": "on", "stay": "1"})
        community = await client.get(STEP_PATH, params={"n": "2"})
    rows = rail_rows(community.text)
    assert len(rows) == 9
    assert "Your community" in rows
    assert "Discord — the bot" not in rows and "Discord — roles and channels" not in rows
    assert rows[:5] == ["Language", "General", "Database", "This node", "Your community"]
    assert rows[-4:] == ["Web console", "DCS servers", "What do you run?", "Review and write"]


@pytest.mark.asyncio
async def test_the_rail_is_the_only_progress_statement(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        html = (await enter(client)).text
    assert "Nothing is written yet — the last screen writes your configuration." in html
    assert 'aria-current="step"' in html
    # one statement per fact: no "step n of m" text and no progress bar beside the rail
    assert "step 1 of" not in html.lower()
    assert "progress" not in html.lower()
    # a future row is inert text (no href), the current one is not a link either
    assert '<span class="row on" aria-current="step">' in html
    assert '<span class="row"><span class="n">3</span>' in html
    assert 'href="/setup/step?n=3"' not in html


@pytest.mark.asyncio
async def test_a_completed_row_is_a_link_back_and_going_back_keeps_the_answer(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        step_one = await enter(client)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(step_one.text), "n": "1",
                                           "bot_language": "on"})
        step_two = await client.get(STEP_PATH, params={"n": "2"})
        await client.post(STEP_PATH, data={"_csrf_token": token_of(step_two.text), "n": "2",
                                           "autoupdate": "on", "discord": "on"})
        # row 1 is now a LINK back, and re-opening it re-renders the saved answer
        back = (await client.get(STEP_PATH, params={"n": "1"})).text
    assert '<a class="row done" href="/setup/step?n=1">' in back
    assert 'name="bot_language" checked' in back


@pytest.mark.asyncio
async def test_a_future_or_crafted_step_renders_the_current_one(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        for crafted in ("9", "7", "abc", "-1", ""):
            html = (await client.get(STEP_PATH, params={"n": crafted})).text
            assert "Which language do you speak?" in html, crafted


# ------------------------------------------------------------------------------- step 1

@pytest.mark.asyncio
async def test_step_one_writes_the_bot_language_and_sets_the_console_cookie(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        html = (await enter(client)).text
        response = await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "1",
                                                      "bot_language": "on"})
        assert response.status_code == 303
        assert response.headers["location"] == f"{STEP_PATH}?n=2"
        # the console cookie is set to the operator's language (English here), like /language does
        assert "dcssb_lang" in response.headers.get("set-cookie", "")
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 1
    assert draft.language == "en"
    assert draft.bot_language is True
    body = draft_path(tmp_path).read_text(encoding="utf-8")
    assert "language: en" in body and "main:" in body
    # nothing else is written: main.yaml is what closes the setup window, and it is the LAST step's
    assert not (tmp_path / "main.yaml").exists()


@pytest.mark.asyncio
async def test_step_one_without_the_bot_language_leaves_main_language_out(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        html = (await enter(client)).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "1"})
    draft = SetupDraft.load(tmp_path)
    assert draft.bot_language is False
    assert "language" not in draft.as_document()["main"]


@pytest.mark.asyncio
async def test_the_flag_control_switches_the_tours_language_and_the_language_is_not_re_asked(
        tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        html = (await enter(client)).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "1"})
        # the flag control is in the top bar on a LATER step, and it switches the tour's language
        switched = await client.get(LANGUAGE_PATH, params={"lang": "de", "next": f"{STEP_PATH}?n=2"})
        assert switched.status_code == 303
        assert "dcssb_lang=de" in switched.headers.get("set-cookie", "")
        step_two = (await client.get(STEP_PATH, params={"n": "2"})).text
    assert "Allgemein" in step_two                      # the German rail/screen title
    assert 'class="mi on" role="menuitem" hreflang="de"' in step_two
    # the language step is behind the operator, not asked again
    assert "Welche Sprache sprichst Du?" not in step_two


@pytest.mark.asyncio
async def test_the_theme_control_sets_its_own_cookie(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        response = await client.get(THEME_PATH, params={"theme": "light", "next": SETUP_PATH})
        assert response.status_code == 303
        assert "dcssb_theme=light" in response.headers.get("set-cookie", "")
        assert 'data-theme="light"' in (await client.get(SETUP_PATH)).text


@pytest.mark.asyncio
async def test_step_one_prefills_the_console_language_from_the_browser(tmp_path):
    """No cookie and no main.yaml: the browser's Accept-Language decides the tour's language."""
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        assert (await client.get(SETUP_PATH, params={"code": CODE},
                                 headers={"Accept-Language": "de-DE,de;q=0.9"})).status_code == 303
        html = await client.get(SETUP_PATH, headers={"Accept-Language": "de-DE,de;q=0.9"})
        response = await client.post(STEP_PATH, data={"_csrf_token": token_of(html.text), "n": "1"},
                                     headers={"Accept-Language": "de-DE,de;q=0.9"})
    # the screen speaks the browser's language, the German switch is the marked one
    assert "Welche Sprache sprichst Du?" in html.text
    assert 'class="mi on" role="menuitem" hreflang="de"' in html.text
    # and the answer IS that language: the draft and the console cookie both carry it
    assert response.headers["location"] == f"{STEP_PATH}?n=2"
    assert "dcssb_lang=de" in response.headers.get("set-cookie", "")
    assert SetupDraft.load(tmp_path).language == "de"


# ------------------------------------------------------------------------------- step 2

@pytest.mark.asyncio
async def test_step_two_writes_auto_update_and_the_discord_decision(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        html = (await enter(client)).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "1"})
        step_two = (await client.get(STEP_PATH, params={"n": "2"})).text
        assert 'name="autoupdate" checked' in step_two and 'name="discord" checked' in step_two
        response = await client.post(STEP_PATH, data={"_csrf_token": token_of(step_two), "n": "2",
                                                      "autoupdate": "on"})
        assert response.status_code == 303
        assert response.headers["location"] == STEP_PATH
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 2 and draft.autoupdate is True and draft.discord is False
    assert draft.as_document()["main"]["autoupdate"] is True


@pytest.mark.asyncio
async def test_flipping_discord_off_and_back_on_keeps_the_answers_to_the_other_steps(tmp_path):
    """Answers to steps that LEAVE the rail are kept, not discarded when the rail changes."""
    # the steps' own blocks (6's Discord answers, 7's role mapping) and a key a LATER card writes:
    # the tour must carry all of them through a save
    planted = SetupDraft.load(tmp_path)
    planted.discord_bot = {"guild_id": 7, "owner": 42, "privileged_intents": True}
    planted.roles = {"roles": {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["@everyone"]}}
    planted.extra = {"a_later_step": {"kept": True}}
    planted.save(tmp_path)

    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        html = (await enter(client)).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "1"})
        step_two = (await client.get(STEP_PATH, params={"n": "2"})).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(step_two), "n": "2", "stay": "1"})
        off = await client.get(STEP_PATH, params={"n": "2"})
        assert "Your community" in off.text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(off.text), "n": "2",
                                           "discord": "on", "stay": "1"})
        back_on = await client.get(STEP_PATH, params={"n": "2"})
    assert "Discord — the bot" in back_on.text and "Your community" not in back_on.text
    document = SetupDraft.load(tmp_path).as_document()
    assert document["discord_bot"] == {"guild_id": 7, "owner": 42, "privileged_intents": True}
    assert document["roles"]["roles"]["Admin"] == ["Admin"]
    assert document["a_later_step"] == {"kept": True}


# ------------------------------------------------------------------------------- resume

@pytest.mark.asyncio
async def test_a_reload_resumes_at_the_first_incomplete_step(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        html = (await enter(client)).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "1"})
        reloaded = await client.get(SETUP_PATH)
    # step 2 is the current one (its own control is what tells the steps apart; "General" is on
    # every rail)
    assert 'name="autoupdate"' in reloaded.text
    assert "Which language do you speak?" not in reloaded.text


@pytest.mark.asyncio
async def test_a_restart_of_the_console_does_not_lose_the_progress(tmp_path):
    first, _ = build(tmp_path)
    async with asgi_client(first) as client:
        html = (await enter(client)).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "1",
                                           "bot_language": "on"})
    # a SECOND console boot: a new one-time code, so the old session is gone — the draft is not
    second, _ = build(tmp_path, code="ZZZZ9999")
    async with asgi_client(second) as client:
        assert (await client.get(SETUP_PATH)).status_code == 200          # the gate is back
        assert 'name="autoupdate"' not in (await client.get(SETUP_PATH)).text
        html = await enter(client, code="ZZZZ9999")
    # ... and behind the gate the tour resumes at the first incomplete step, answer intact
    assert 'name="autoupdate"' in html.text
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 1 and draft.bot_language is True
    assert "language: en" in draft_path(tmp_path).read_text(encoding="utf-8")


# ------------------------------------------------------------------------------- the guards

@pytest.mark.asyncio
async def test_every_tour_route_needs_the_accepted_code(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        assert (await client.get(STEP_PATH, params={"n": "1"})).status_code == 404
        assert (await client.post(STEP_PATH, data={"n": "1"})).status_code == 404
        assert (await client.get(HEALTH_PATH)).status_code == 200
        await enter(client)
        assert (await client.get(STEP_PATH, params={"n": "1"})).status_code == 200


@pytest.mark.asyncio
async def test_a_step_write_without_the_csrf_token_is_refused(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        html = (await enter(client)).text
        refused = await client.post(STEP_PATH, data={"n": "1"})
        assert refused.status_code == 403
        ok = await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "1"})
        assert ok.status_code == 303


@pytest.mark.asyncio
async def test_the_flag_assets_are_an_allow_list(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        assert (await client.get(f"{FLAGS_PATH}/de.svg")).status_code == 200
        assert (await client.get(f"{FLAGS_PATH}/globe.svg")).status_code == 200
        assert (await client.get(f"{FLAGS_PATH}/nope.svg")).status_code == 404


def test_the_draft_path_is_the_tour_state_not_a_config_file(tmp_path):
    assert draft_path(tmp_path).name == DRAFT_FILENAME
    assert DRAFT_FILENAME.startswith(".")
