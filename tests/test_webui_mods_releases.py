"""The download dialog's version pick: the release read the page asks for, and the page that asks it.

THE READ is ``/mods/<owner>/<target>/releases`` — a GET beside the dialogs on the same per-owner base,
gated on ``mods.manage`` (the capability the download door requires) and on the caller's own view of
the target. It answers ``{"versions": [...], "error": "<sentence>"}`` with ``Cache-Control: no-store``;
a GitHub failure is a 200 carrying ``error``, never a 500, and a completed answer is reused for one
``(node, url)`` pair for a minute.

THE PAGE cannot be rendered into its swapped state without a browser, so the dialog pins what the
SWAP NEEDS: the free-text input with the field's own name and the ``latest`` preset, the hint line
under it, and the form's data attributes (the route, the two field names, the preset and the
debounce). The script itself is parsed by ``test_webui_dialog_script_parses``.
"""
from __future__ import annotations

import gettext
import time
from pathlib import Path

import pytest

from core.services.registry import ServiceRegistry
from services.modmanager import ModManagerService
from services.webservice import i18n
from services.webservice.pages import mods as mods_page

from test_webui_mods_pages import (  # noqa: F401  (the fixtures are used by name)
    BLUE_FLAG, NODE, NODE_B, ROOT_ROWS, RecordingService, build, client, config_dir, fleet,
    no_service, sign_in, token_of,
)
# the service-level pins drive the SAME doubles the node-aware suite uses
from test_modmanager_node_aware import Bus, _Node, _service

REPO_ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "https://github.com/example/SRS_Radio_Standalone"
OTHER_REPOSITORY = "https://github.com/example/LotAtc_Client"
PLAIN_URL = "https://example.com/files/SRS_Radio_Standalone_v2.1.2.zip"
RELEASES = ["2.4.1", "2.2.0", "2.1.2"]


class ReleaseService(RecordingService):
    """The ``ModManagerService`` double, with the release read the version pick asks for."""

    def __init__(self, *, versions=(), raise_versions=None, rows=(), **kwargs):
        super().__init__(rows=rows, **kwargs)
        self.versions = list(versions)
        self.raise_versions = raise_versions

    async def list_repo_versions(self, node, url):
        self.calls.append(("list_repo_versions", getattr(node, "name", node), url))
        if self.raise_versions is not None:
            raise self.raise_versions
        return list(self.versions)

    @property
    def reads(self) -> list[tuple]:
        return [call for call in self.calls if call[0] == "list_repo_versions"]


@pytest.fixture(autouse=True)
def empty_cache():
    """Every case starts with an empty answer cache, so a window is never inherited."""
    mods_page._RELEASES.clear()
    yield
    mods_page._RELEASES.clear()


def register(monkeypatch, **kwargs) -> ReleaseService:
    """Register the release-reading double as the process's running service."""
    double = ReleaseService(**kwargs)
    monkeypatch.setitem(ServiceRegistry._singletons, ModManagerService, double)
    return double


async def read(http, owner: str, target: str, url: str):
    """Ask the release read one question."""
    return await http.get(mods_page.releases_url(owner, target),
                          params={mods_page.RELEASES_QUERY: url})


# ------------------------------------------------------------------ the service's own read

class _ReleaseResponse:
    """A GitHub releases answer: JSON built from a tag list, in the order GitHub would give it."""

    def __init__(self, tags):
        self._tags = list(tags)

    def raise_for_status(self):
        return None

    async def json(self):
        return [{"tag_name": tag} for tag in self._tags]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _ReleaseSession:
    """A stand-in for ``aiohttp.ClientSession`` answering the release tags it was built with."""

    tags: list[str] = []
    opened = 0

    def __init__(self, *args, **kwargs):
        type(self).opened += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        return _ReleaseResponse(type(self).tags)


class _UnreachableSession:
    """A session that must never be constructed: the read refuses before any request."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("a URL that is not a repository must not reach the network")


@pytest.mark.asyncio
async def test_the_release_read_keeps_githubs_order_and_strips_the_tag_prefix(monkeypatch):
    """Newest first is GITHUB's order, not a re-sort: ``2.4.1`` stays above ``2.10.0``."""
    from services.modmanager import service as service_module

    session = _ReleaseSession
    session.opened = 0
    # out of any lexical or semver sort, so a re-sort anywhere in the path is visible
    session.tags = ["v3.0.0", "v2.10.0", "v2.9.0", "v2.9.0"]
    monkeypatch.setattr(service_module, "ClientSession", session)
    service = _service(packages={"RootFolder": "/nowhere", "SavedGames": "/nowhere"})
    service.node.proxy = None
    service.node.proxy_auth = None

    versions = await service.list_repo_versions(_Node("node-local", remote=False), REPOSITORY)

    # neither a lexical nor a semantic sort produces this: it is GitHub's own answer, deduplicated
    assert versions == ["3.0.0", "2.10.0", "2.9.0"]
    assert session.opened == 1


@pytest.mark.asyncio
async def test_a_url_that_is_not_a_repository_is_answered_without_a_request(monkeypatch):
    """A plain file URL has no release list: the read answers ``[]`` and touches no network."""
    from services.modmanager import service as service_module

    monkeypatch.setattr(service_module, "ClientSession", _UnreachableSession)
    service = _service(packages={"RootFolder": "/nowhere", "SavedGames": "/nowhere"})
    service.node.proxy = None
    service.node.proxy_auth = None

    assert await service.list_repo_versions(_Node("node-local", remote=False), PLAIN_URL) == []
    assert await service.list_repo_versions(_Node("node-local", remote=False),
                                            "https://github.com/owner/repo/tree/main") == []


@pytest.mark.asyncio
async def test_the_release_read_on_a_remote_node_is_an_rpc():
    """The network call belongs to the answering node: a remote target is asked over the wire."""
    bus = Bus(answer=["2.4.1"])
    service = _service(packages={"RootFolder": "/nowhere", "SavedGames": "/nowhere"}, bus=bus)

    answer = await service.list_repo_versions(_Node("node-eu", remote=True), REPOSITORY)

    assert answer == ["2.4.1"]
    message, node, _timeout = bus.calls[0]
    assert node == "node-eu"
    assert message["method"] == "list_repo_versions"
    assert message["params"] == {"node": "node-eu", "url": REPOSITORY}


# ------------------------------------------------------------------ the read, through the app

@pytest.mark.asyncio
async def test_the_read_answers_a_repository_releases_newest_first(config_dir, monkeypatch):
    """The releases the service answered, in its order, with no error and no browser caching."""
    double = register(monkeypatch, versions=RELEASES, rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        response = await read(http, mods_page.NODE_OWNER, NODE, REPOSITORY)

    assert response.status_code == 200, response.text
    assert response.json() == {"versions": RELEASES, "error": "", }
    assert "no-store" in response.headers.get("cache-control", "")
    assert double.reads == [("list_repo_versions", NODE, REPOSITORY)]


@pytest.mark.asyncio
async def test_a_plain_url_is_answered_with_an_empty_list_and_a_reason(config_dir, monkeypatch):
    """A non-repository URL is a REAL answer: no versions, and the action says why."""
    double = register(monkeypatch, versions=RELEASES, rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        response = await read(http, mods_page.NODE_OWNER, NODE, PLAIN_URL)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["versions"] == []
    assert "not a GitHub repository" in body["error"]
    assert "no-store" in response.headers.get("cache-control", "")
    # the answer is the action's own, and the service is not asked to fetch a file listing
    assert double.reads == []


@pytest.mark.asyncio
async def test_a_failing_read_is_a_200_with_an_error_and_never_a_500(config_dir, monkeypatch):
    """GitHub raising keeps the dialog's text box: the answer states the failure, the route does not."""
    double = register(monkeypatch, raise_versions=RuntimeError("the node did not answer"),
                      rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        response = await read(http, mods_page.NODE_OWNER, NODE, REPOSITORY)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["versions"] == []
    assert "could not be read" in body["error"] and "the node did not answer" in body["error"]
    assert "no-store" in response.headers.get("cache-control", "")
    # a failed read is NOT cached: asking again reaches the service again
    async with client(app) as http:
        await sign_in(http)
        await read(http, mods_page.NODE_OWNER, NODE, REPOSITORY)
    assert len(double.reads) == 2


@pytest.mark.asyncio
async def test_a_caller_without_mods_manage_is_refused_the_read(config_dir, monkeypatch):
    """``mods.view`` does not read a link's releases: the gate is the download's own capability."""
    double = register(monkeypatch, versions=RELEASES, rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")                      # DCS Admin, no mods.manage
        response = await read(http, mods_page.NODE_OWNER, NODE, REPOSITORY)

    assert response.status_code == 403, response.status_code
    assert double.reads == [], "a refused read never reached the service"


@pytest.mark.asyncio
async def test_a_target_outside_the_callers_view_is_refused(config_dir, monkeypatch):
    """A node the caller's view does not carry is a 403, and nothing is asked about it."""
    double = register(monkeypatch, versions=RELEASES, rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        unknown = await read(http, mods_page.NODE_OWNER, NODE_B, REPOSITORY)
        foreign = await read(http, mods_page.SERVER_OWNER, "Not A Server", REPOSITORY)

    assert unknown.status_code == 403, unknown.status_code
    assert foreign.status_code == 403, foreign.status_code
    assert "outside your scope" in unknown.json()["error"]
    assert unknown.json()["versions"] == [] and foreign.json()["versions"] == []
    assert double.reads == [], "an unresolved target is never asked about"


@pytest.mark.asyncio
async def test_the_servers_owner_reads_through_its_server(config_dir, monkeypatch):
    """The server half resolves the SERVER's node, so both patterns of the read are live."""
    double = register(monkeypatch, versions=RELEASES, rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        response = await read(http, mods_page.SERVER_OWNER, BLUE_FLAG, REPOSITORY)

    assert response.status_code == 200, response.text
    assert response.json()["versions"] == RELEASES
    assert double.reads == [("list_repo_versions", NODE, REPOSITORY)]


@pytest.mark.asyncio
async def test_an_answer_is_cached_per_node_and_url_for_the_window(config_dir, monkeypatch):
    """A repeated ask is served from the cache; a different URL, a different node or an old stamp is not."""
    double = register(monkeypatch, versions=RELEASES, rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        first = await read(http, mods_page.NODE_OWNER, NODE, REPOSITORY)
        second = await read(http, mods_page.NODE_OWNER, NODE, REPOSITORY)
        other = await read(http, mods_page.NODE_OWNER, NODE, OTHER_REPOSITORY)
        # the SAME node and URL, but the stored answer is older than the window
        mods_page._RELEASES[(NODE, REPOSITORY)] = (
            time.monotonic() - mods_page.RELEASES_TTL_SECONDS - 1, list(RELEASES))
        stale = await read(http, mods_page.NODE_OWNER, NODE, REPOSITORY)

    assert first.json()["versions"] == second.json()["versions"] == stale.json()["versions"]
    assert other.json()["versions"] == RELEASES, other.text
    # one cached hit, one other URL, one expired answer — the second ask never reached the service
    assert len(double.reads) == 3, double.reads


# ------------------------------------------------------------------ the dialog the script drives

async def download_dialog(config_dir, monkeypatch, language: str | None = None) -> str:
    """Open the download dialog on the node page and return its rendered HTML."""
    register(monkeypatch, versions=RELEASES, rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        if language:
            http.cookies.set(i18n.COOKIE_NAME, language)
        page = await http.get(mods_page.node_page_url(NODE))
        assert page.status_code == 200, page.text
        reply = await http.post(mods_page.dialog_url(mods_page.NODE_OWNER, NODE,
                                                     mods_page.DOWNLOAD),
                                data={"_csrf_token": token_of(page.text)})
        assert reply.status_code == 200, reply.text
        return reply.text


@pytest.mark.asyncio
async def test_the_dialog_still_renders_the_free_text_version_box(config_dir, monkeypatch):
    """A URL-only download and a browser without JavaScript keep the field they always had."""
    html = await download_dialog(config_dir, monkeypatch)

    assert '<input id="ver" name="version" type="text" value="latest"' in html
    assert "aria-label=\"Version\"" in html
    assert '<label for="ver">Version</label>' in html
    assert "<select" not in html, "the pick is the script's, never the render's"


@pytest.mark.asyncio
async def test_the_dialog_carries_the_facts_the_script_reads(config_dir, monkeypatch):
    """The route, the two field names, the preset and the debounce — asserted, not assumed."""
    html = await download_dialog(config_dir, monkeypatch)

    assert f'data-releases-url="{mods_page.releases_url(mods_page.NODE_OWNER, NODE)}"' in html
    assert 'data-url-query="url"' in html
    assert 'data-url-field="url"' in html
    assert 'data-version-field="version"' in html
    assert 'data-latest="latest"' in html
    assert f'data-hint-id="{mods_page.VERSIONS_HINT_ID}"' in html
    assert f'data-delay-ms="{mods_page.RELEASES_DEBOUNCE_MS}"' in html


@pytest.mark.asyncio
async def test_the_dialog_renders_the_one_hint_line_hidden(config_dir, monkeypatch):
    """ONE line under the version field, hidden until the script has an answer it cannot use."""
    html = await download_dialog(config_dir, monkeypatch)

    assert f'<p class="dlg-hint" id="{mods_page.VERSIONS_HINT_ID}" hidden>' in html
    assert "The releases could not be read from this link — type the version." in html
    assert html.count(mods_page.VERSIONS_UNREADABLE) == 1, "one line, never a second copy"


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["de", "es", "ru", "cn"])
async def test_the_hint_line_renders_in_the_request_language(config_dir, monkeypatch, language):
    """The line is chrome: it renders from the COMPILED catalog, not as its English msgid."""
    translations = gettext.translation("webui", localedir=str(REPO_ROOT / "locale"),
                                       languages=[language])
    sentence = translations.gettext(mods_page.VERSIONS_UNREADABLE)
    assert sentence != mods_page.VERSIONS_UNREADABLE, language

    html = await download_dialog(config_dir, monkeypatch, language=language)
    assert sentence in html, language
    assert mods_page.VERSIONS_UNREADABLE not in html, language
