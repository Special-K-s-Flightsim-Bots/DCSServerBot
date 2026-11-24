"""Two Mods sentences: the download note's version rule, and the plugin-off empty state.

Two claims are pinned here, each on a rendered page of the REAL application (production shell, local
login backend, the real ``modmanager`` actions, a stub cluster):

* the download dialog's note states which input decides the version — the FILE NAME for a plain URL
  (the version field is ignored), the FIELD for a repository download, where a version with no
  matching release asset is refused. The dialog still carries its version field;
* the installed card's empty sentence is the plugin's OWN "not installed" sentence while the service
  is absent from the process, and never the action layer's text, which names an internal qualname.
  A read that failed while the service IS present keeps its own sentence, so "the plugin is off" and
  "the read broke" cannot collapse into one message.
"""
from __future__ import annotations

import pytest

from plugins.modmanager.actions import SERVICE_MISSING
from core.services.registry import ServiceRegistry
from services.modmanager import ModManagerService
from services.webservice.pages import mods as mods_page
# the harness and doubles of the Mods pages, reused rather than copied
from test_webui_mods_pages import (  # noqa: F401 - fixtures are imported on purpose
    NODE, ROOT_ROWS, build, client, config_dir, fleet, service, sign_in, token_of,
)

DOWNLOAD = "download"

#: the rule's own wording, in the parts that must survive any rewording of the sentence
PLAIN_URL_RULE = "For a plain URL the file name decides the version and the version field is ignored"
REPOSITORY_RULE = "for a repository download the field selects the release"


class BrokenPool:
    """A connection pool whose connection cannot be opened — a read that RAISES."""

    def connection(self):
        raise RuntimeError("the database is gone")


@pytest.fixture(autouse=True)
def absent_unless_registered(monkeypatch):
    """The plugin-not-enabled state: no service instance unless a case registers one."""
    monkeypatch.delitem(ServiceRegistry._singletons, ModManagerService, raising=False)


async def open_node_page(http) -> str:
    """The node's root-mods page, asserted to render."""
    response = await http.get(mods_page.node_page_url(NODE))
    assert response.status_code == 200, response.text
    return response.text


# ------------------------------------------------------------------ 1. the download dialog's note

@pytest.mark.asyncio
async def test_the_download_dialog_note_names_the_plain_url_rule(config_dir, service):
    """The note states both halves of the rule, and the version field is still on the form."""
    service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        page = await open_node_page(http)
        reply = await http.post(
            mods_page.dialog_url(mods_page.NODE_OWNER, NODE, DOWNLOAD),
            data={"_csrf_token": token_of(page)})

    assert reply.status_code == 200, reply.text
    html = reply.text
    assert PLAIN_URL_RULE in html, "the note must say the file name decides the version of a plain URL"
    assert REPOSITORY_RULE in html, "the note must say the field selects a repository release"
    assert "no matching release asset is refused" in html
    assert "Nothing is installed by this step" in html, "the two-step meaning must survive"
    # the version field is NOT removed: it is what selects a repository release
    assert f'name="{mods_page.VERSION_FIELD}"' in html


# --------------------------------------------------------- 2. the installed card's empty sentence

@pytest.mark.asyncio
async def test_the_installed_card_says_the_plugin_is_off_when_the_service_is_absent(config_dir):
    """Plugin off: the card's empty sentence is the module's own, with no internal qualname."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http)
        html = await open_node_page(http)

    assert SERVICE_MISSING in html, "the card must show the plugin's own sentence"
    assert "list_installed_mods" not in html, "an internal qualname must never reach the page"
    assert "is not available in this installation" not in html


@pytest.mark.asyncio
async def test_a_failed_read_keeps_its_own_sentence_with_the_service_present(config_dir, service):
    """Service present, read raised: the failure keeps its own sentence, not the plugin-off one."""
    double = service(rows=ROOT_ROWS)
    double.apool = BrokenPool()
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        html = await open_node_page(http)

    assert "The installed mods could not be read: the database is gone" in html
    assert SERVICE_MISSING not in html, "a read failure must not read as 'the plugin is off'"
