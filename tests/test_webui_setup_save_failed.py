"""A FAILED draft write is a classified notice on the step, never a raw 500.

The shared draft-save path (``SetupDraft.save`` → the ONE config-write primitive) is used by every
step from 2 onwards. When that write fails, the exception used to escape ``submit_step`` and the
operator got a bare 500 page — no notice, no statement of what was or was not written. These tests
patch the primitive to raise mid-submit and pin the classified outcome on the step: HTTP 200, the
notice present, no exception/driver string on the page, the draft on disk untouched, and the step
count unchanged. Two different steps are exercised, because one shared code path means one fix.

WHAT IS NOT COVERED: no real filesystem permission refusal is staged here (that needs an unwritable
config directory); the failure is raised by the primitive, exactly as the review reproduced it.
"""
from __future__ import annotations

import re
from unittest import mock

import pytest

from conftest import asgi_client
from core.utils import configwrite
from services.webservice.setup import discord as discord_step
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.state import SetupDraft, draft_path
from services.webservice.setup.tour import STEP_PATH, T_SAVE_FAILED
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')
#: The exception message a leaked failure would print — never on the page, never in the draft.
LEAK = "the draft file /srv/secret/.setup-draft.yaml could not be written: Permission denied"


@pytest.fixture(autouse=True)
def no_discord_network(monkeypatch):
    """A step-5 submit reads the guild; the suite must never touch discord.com."""
    async def fake(token, guild_id):
        return None, "discord_unreachable"

    monkeypatch.setattr(discord_step, "_read_guild", fake)


def build(config_dir, *, code: str = CODE):
    window = SetupWindow(code=code, wrong_delay=0.0)
    return create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN), window


async def enter(client, code: str = CODE):
    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    return await client.get(SETUP_PATH)


def token_of(html: str) -> str:
    found = TOKEN_RE.search(html)
    assert found, "the step form carries no CSRF token"
    return found.group(1)


def plant(config_dir, *, step: int, discord: bool = True, **blocks) -> bytes:
    """Write a real draft at *step* through the primitive, and return the file's bytes."""
    draft = SetupDraft.load(config_dir)
    draft.step = step
    draft.discord = discord
    for name, value in blocks.items():
        setattr(draft, name, value)
    draft.save(config_dir)
    return draft_path(config_dir).read_bytes()


def assert_classified(html: str) -> None:
    """HTTP-body checks shared by every step: the notice is there, the failure is not."""
    assert f'class="notice bad" role="alert"' in html
    assert T_SAVE_FAILED in html
    for leak in (LEAK, "ConfigWriteError", "Traceback", "Permission denied"):
        assert leak not in html, f"'{leak}' leaked onto the page"


@pytest.mark.asyncio
async def test_step_two_a_failed_save_is_a_notice_and_writes_nothing(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        html = await enter(client)
        # answer step 1 for real, so a draft exists on disk to compare against
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html.text), "n": "1"})
        before = draft_path(tmp_path).read_bytes()
        assert SetupDraft.load(tmp_path).step == 1

        step_two = (await client.get(STEP_PATH, params={"n": "2"})).text
        with mock.patch.object(configwrite, "save_config_file",
                               side_effect=configwrite.ConfigWriteError(LEAK)):
            response = await client.post(STEP_PATH, data={"_csrf_token": token_of(step_two),
                                                          "n": "2", "autoupdate": "on"})

    assert response.status_code == 200
    assert_classified(response.text)
    # the typed answer is kept on the step (the switch is still on)
    assert 'name="autoupdate" checked' in response.text
    # nothing was written: the draft on disk is byte-identical and the step did not advance
    assert draft_path(tmp_path).read_bytes() == before
    assert SetupDraft.load(tmp_path).step == 1


@pytest.mark.asyncio
async def test_a_filesystem_oserror_is_classified_the_same_way(tmp_path):
    """The primitive's OTHER failure mode — a raw OSError from the swap — is classified too.

    A real filesystem refusal is hard to stage here, so the primitive is patched to raise the
    OSError such a refusal would: ``SetupDraft.save`` raises it as its own config-write error, and
    the step renders the same notice instead of a 500.
    """
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        html = await enter(client)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html.text), "n": "1"})
        before = draft_path(tmp_path).read_bytes()
        step_two = (await client.get(STEP_PATH, params={"n": "2"})).text
        with mock.patch.object(configwrite, "save_config_file", side_effect=OSError(LEAK)):
            response = await client.post(STEP_PATH, data={"_csrf_token": token_of(step_two),
                                                          "n": "2", "autoupdate": "on"})

    assert response.status_code == 200
    assert_classified(response.text)
    assert draft_path(tmp_path).read_bytes() == before
    assert SetupDraft.load(tmp_path).step == 1


@pytest.mark.asyncio
async def test_step_five_a_failed_save_keeps_the_typed_values(tmp_path):
    """Step 5 (2e): the guild id / owner the operator typed are kept, the step does not advance."""
    plant(tmp_path, step=4)
    before = draft_path(tmp_path).read_bytes()
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        screen = (await client.get(STEP_PATH, params={"n": "5"})).text
        with mock.patch.object(configwrite, "save_config_file",
                               side_effect=configwrite.ConfigWriteError(LEAK)):
            response = await client.post(STEP_PATH, data={"_csrf_token": token_of(screen),
                                                          "n": "5", "guild_id": "12345",
                                                          "owner": "99", "token": "TOKEN-VALUE"})

    assert response.status_code == 200
    assert_classified(response.text)
    assert 'name="guild_id" value="12345"' in response.text
    assert 'name="owner" value="99"' in response.text
    # the token never renders back, on any path — including this one
    assert "TOKEN-VALUE" not in response.text
    assert draft_path(tmp_path).read_bytes() == before
    assert SetupDraft.load(tmp_path).step == 4


@pytest.mark.asyncio
async def test_step_seven_a_failed_save_keeps_the_typed_values(tmp_path):
    """Step 7 (the Web console step): the account and the address typed are kept."""
    plant(tmp_path, step=6)
    before = draft_path(tmp_path).read_bytes()
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        screen = (await client.get(STEP_PATH, params={"n": "7"})).text
        with mock.patch.object(configwrite, "save_config_file",
                               side_effect=configwrite.ConfigWriteError(LEAK)):
            response = await client.post(STEP_PATH, data={"_csrf_token": token_of(screen),
                                                          "n": "7",
                                                          "console_enabled": "on",
                                                          "console_public_base_url":
                                                              "https://console.example.org",
                                                          "console_username": "typed-admin",
                                                          "console_password": "some-password"})

    assert response.status_code == 200
    assert_classified(response.text)
    assert 'name="console_username" value="typed-admin"' in response.text
    assert 'name="console_public_base_url" value="https://console.example.org"' in response.text
    assert draft_path(tmp_path).read_bytes() == before
    assert SetupDraft.load(tmp_path).step == 6


@pytest.mark.asyncio
async def test_a_failed_scan_save_on_a_get_is_a_notice_not_a_500(tmp_path):
    """The GET path saves too (the once-per-tour scan); its failure renders the same notice."""
    plant(tmp_path, step=5, node={"listen_port": 3008, "no_dcs": False,
                                  "dcs_installation": "/opt/dcs"}, servers={"scanned": False})
    before = draft_path(tmp_path).read_bytes()
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        # accept the gate (the 303 is not followed), so the FIRST render of the tour is the patched
        # one — the scan and its failed save happen there, not on an earlier unpatched render
        assert (await client.get(SETUP_PATH, params={"code": CODE})).status_code == 303
        with mock.patch.object(configwrite, "save_config_file",
                               side_effect=configwrite.ConfigWriteError(LEAK)):
            response = await client.get(SETUP_PATH)

    assert response.status_code == 200
    assert_classified(response.text)
    assert draft_path(tmp_path).read_bytes() == before
