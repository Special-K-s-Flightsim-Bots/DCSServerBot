"""The Missions tab: the list (one row per LOGICAL mission) and the per-row download.

Slice 2 of the mission work, on top of M1's actions. The tab reads the list through the
``get_mission_list`` ACTION (never off ``server.settings.op`` or a mission file), so a mission that
exists as both a primary file and a ``.dcssb`` copy is ONE row tagged with which physical copy is
current — the action's newest-copy resolver is the ONE owner of that fact. The download is a GET
route that names the mission by INDEX or NAME (never a path) and streams the ``download_mission``
action's bytes with the logical ``.miz`` filename.

Pins here: the tab is offered by ``missions.view`` (server-page roles + a scope grant) and withheld —
with the console's own refusal — from a viewer without it; a manager sees their own server's missions
and NOT another hoster's; one row per logical mission with current/next correct; the download returns
the bytes under the logical name and refuses a request that names a path; the Configuration tab no
longer renders any mission block (its drift test still passes). Driven through the real app with the
real local login backend, the real mission actions and a stub cluster.
"""
from __future__ import annotations

import os
import re
import types

import pytest

from conftest import permissions
from core import Status, UploadStatus
from services.webservice.pages import actions as actions_page
from services.webservice.pages import server_detail as detail_page
from test_webui_tab_scope import narrow
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, sign_in, token_of,
)

SERVER_A = "SRS-1"
SERVER_B = "SRS-B"
#: host-spelled like the bot's own missions dir: "/missions" is not absolute on Windows, where
#: the bot's abspath/join then prepends or backslashes and no typed POSIX expectation can match
BASE_DIR = os.path.abspath("/missions")
#: the fixture the card asks for: ``Training`` exists as BOTH a primary file and a ``.dcssb`` copy,
#: and the resolver hands back ONE logical entry whose ``real`` is the newest (the ``.dcssb``) copy.
#: ``getAllMissionFiles`` is the bot's own newest-copy resolver (``core/data/impl/serverimpl.py``), so
#: a real server never returns two rows for one logical mission — the double mirrors that contract.
FILES = [
    (os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, ".dcssb", "Training.miz")),
    (os.path.join(BASE_DIR, "Rotated.miz"), os.path.join(BASE_DIR, "Rotated.miz")),
    (os.path.join(BASE_DIR, "Backup.miz"), os.path.join(BASE_DIR, ".dcssb", "Backup.miz.orig")),
]
CURRENT = os.path.join(BASE_DIR, ".dcssb", "Training.miz")
MISSION_BYTES = b"MIZ-BYTES-42"


def host_path(path: str) -> str:
    """*path* as the bot states a mission path ON THIS HOST.

    ``core/utils/dcs.py``'s one normaliser joins the components with ``os.sep``, so a mission list
    the bot writes back carries the host's own separators. An expectation built from ``BASE_DIR``
    has to be normalised the same way or it can only match on a POSIX host.
    """
    return os.path.normpath(path)



# ------------------------------------------------------------------------------- test doubles

class MissionNode:
    """The server's node: ``read_file`` is the ONE call the download makes; ``list_directory``
    answers the ADD picker; ``remove_file`` records a delete-from-disk."""

    def __init__(self, name, *, content=b"", read_error=None, list_files=None, list_base=None,
                 list_error=None):
        self.name = name
        self.master = True
        self.content = content
        self.read_error = read_error
        self.reads: list[str] = []
        self.removed: list[str] = []
        self.list_calls: list[tuple] = []
        self.list_files = list(list_files or [])
        self.list_base = list_base
        self.list_error = list_error

    async def read_file(self, path):
        self.reads.append(path)
        if self.read_error is not None:
            raise self.read_error
        return self.content

    async def remove_file(self, path):
        self.removed.append(path)

    async def list_directory(self, path, pattern=None, traverse=False, ignore=None):
        self.list_calls.append((path, pattern, traverse, tuple(ignore or ())))
        if self.list_error is not None:
            raise self.list_error
        return (self.list_base or path), list(self.list_files)


class MissionServer:
    """A ``Server`` double shaped where the scope, the read models and the mission actions read it."""

    def __init__(self, name, managed_by=None, *, status=Status.RUNNING, node_name="dcs-a-01",
                 instance_name="Host-A-Instance", files=None, current=None, start=1,
                 missions_dir=BASE_DIR, content=MISSION_BYTES, read_error=None, list_error=None,
                 configured=None, ignore_dirs=None, list_files=None):
        self.name = name
        self.display_name = name
        self.status = status
        self.maintenance = False
        self.node = MissionNode(node_name, content=content, read_error=read_error,
                                list_files=list_files, list_base=missions_dir,
                                list_error=list_error)
        self.instance = types.SimpleNamespace(name=instance_name)
        self.port = 10309
        self.current_mission = types.SimpleNamespace(name="Training", mission_time=10,
                                                     filename=current)
        self.players = {}
        self.is_remote = False
        self.locals = {"managed_by": list(managed_by)} if managed_by else {}
        if ignore_dirs:
            self.locals["ignore_dirs"] = list(ignore_dirs)
        self._files = list(files or [])
        self._current = current
        self._start = start
        self.missions_dir = missions_dir
        self.list_error = list_error
        #: the CONFIGURED ``missionList`` — the primary spelling of each resolver entry by default.
        self.configured = (list(configured) if configured is not None
                           else [logical for logical, _ in self._files])
        self.adds: list[tuple] = []
        self.deletes: list[int] = []
        self.set_lists: list[list[str]] = []
        self.indexes: list[int] = []
        self.loads: list[tuple] = []
        self.uploads: list[tuple] = []
        self.add_error = None

    async def get_missions_dir(self):
        return self.missions_dir

    async def uploadMission(self, filename, url, *, missions_dir=None, force=False):
        self.uploads.append((filename, url, force))
        self.configured.append(os.path.normpath(os.path.join(self.missions_dir, filename)))
        return UploadStatus.OK

    async def getAllMissionFiles(self):
        if self.list_error is not None:
            raise self.list_error
        return list(self._files)

    async def getMissionList(self):
        return list(self.configured)

    async def getStartIndex(self):
        return self._start

    async def get_current_mission_file(self):
        return self._current

    async def setStartIndex(self, mission_id):
        self.indexes.append(mission_id)

    async def loadMission(self, mission, modify_mission=True, **kwargs):
        self.loads.append((mission, modify_mission))
        return True

    async def addMission(self, path, *, idx=-1, autostart=False):
        if self.add_error is not None:
            raise self.add_error
        self.adds.append((str(path), autostart))
        self.configured.append(os.path.normpath(str(path)))
        return list(self.configured)

    async def deleteMission(self, mission_id):
        self.deletes.append(mission_id)
        self.configured.pop(mission_id - 1)
        return list(self.configured)

    async def setMissionList(self, missions):
        """The M5 full-list replacer, as the double: record the replacement and take it.

        The VALIDATION and the pointer recomputation are the real ``ServerImpl.setMissionList``'s
        (pinned in ``test_mission_reorder_bulk``); the double only has to accept the derived list so
        the ROUTE and the ACTION can be driven end to end.
        """
        self.set_lists.append([os.path.normpath(str(m)) for m in missions])
        self.configured = [os.path.normpath(str(m)) for m in missions]
        return list(self.configured)


def fleet(*, list_error=None):
    """Server A (Host A) with the mission fixture, and server B (Host B) — the scope's two sides."""
    a = MissionServer(SERVER_A, ["Host A"], files=FILES, current=CURRENT, start=1,
                      list_error=list_error)
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    return Cluster([a, b], nodes={},
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


async def missions_page(http, server=SERVER_A, *, expect=200):
    response = await http.get(f"/fleet/{server}?tab=missions")
    assert response.status_code == expect, (response.status_code, response.text[:200])
    return response.text


def download_links(html: str) -> list[str]:
    return re.findall(r'href="(/fleet/[^"]*/missions/download[^"]*)"', html)


# --------------------------------------------------------- 1. the tab, the capability, one row

def test_the_missions_capability_carries_the_server_pages_roles_and_a_scope_grant(config_dir):
    """``missions.view`` == the server page's read roles PLUS a scope grant (one predicate)."""
    from services.webservice.pages import servers as servers_page

    detail_page.capabilities()
    assert detail_page.MISSIONS_CAPABILITY == "missions.view"
    assert detail_page.MISSIONS_ROLES == servers_page.SERVERS_ROLES == ("Admin", "DCS Admin")
    assert permissions.roles_for_capability("missions.view") == detail_page.MISSIONS_ROLES
    assert permissions.scope_grants("missions.view") is True


@pytest.mark.asyncio
async def test_an_admin_sees_one_row_per_logical_mission_with_the_start_and_live_marks(config_dir,
                                                                                      clean_registry):
    """THE CARD'S CORE (M9): one row per logical mission, the START marker on the start row, the live
    dot on the running row, NO copy tag and NO numbering, and the missions-relative path."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await missions_page(http)

    # ONE row per logical mission — Training (both copies) is a single row, not two (one box, one
    # download link per row; the add/upload cards' own `.st-row cfg-add|cfg-upload` are not rows)
    assert html.count("data-sel-box") == 3, "one selection box per configured mission"
    assert len(download_links(html)) == 3, html
    for name in ("Training.miz", "Rotated.miz", "Backup.miz"):
        assert f"<b>{name}</b>" in html, name

    # THE START MARKER (amber `.tag.a`): listStartIndex=1, so EXACTLY one row wears it, and it is the
    # start row. THE LIVE MARK (`.dot.run`): the running row carries the console's own live dot.
    assert html.count('<span class="tag a">START</span>') == 1, "the START marker is not unique"
    assert html.count('st-row row-active') == 1, "one row wears the marker treatment"
    assert "<b>Training.miz</b> <span class=\"dot run\"" in html, "the running row's live mark is gone"

    # THE COPY TAG IS GONE from the list (primary/secondary/orig are not the operator's business), and
    # so is the `#N` numbering — the START marker and the live dot carry the facts instead.
    for copy in ("primary", "secondary", "orig"):
        assert f'<span class="tag">{copy}</span>' not in html, copy
    assert '<span class="tag">current</span>' not in html, "the old `current` tag survives"
    assert '<span class="tag">next</span>' not in html, "the dropped NEXT tag survives"
    assert ">#1<" not in html and ">#2<" not in html, "numbering survives"

    # the missions-directory-RELATIVE path, and NOT the absolute server path. Relative or not, it is
    # a path the bot prints with the host's own separator.
    assert host_path(".dcssb/Training.miz") in html
    assert host_path(os.path.join(BASE_DIR, ".dcssb", "Training.miz")) not in html, "an absolute server path leaked"
    assert host_path(os.path.join(BASE_DIR, "Training.miz")) not in html, "an absolute server path leaked"

    # one download link per row, naming the mission by its INDEX — never a path
    assert download_links(html) == [
        f"/fleet/{SERVER_A}/missions/download?mission=1",
        f"/fleet/{SERVER_A}/missions/download?mission=2",
        f"/fleet/{SERVER_A}/missions/download?mission=3",
    ]


@pytest.mark.asyncio
async def test_the_tab_offers_no_per_row_delete(config_dir, clean_registry):
    """THE PER-ROW TRASHCAN IS GONE: the tick plus the bar is the only removal path.

    The single-remove DIALOG route still exists (M3/M4 built it), but the tab renders no control into
    it — there is no ``delete`` form in a row and no trash glyph anywhere.
    """
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await missions_page(http)

    assert f'action="{actions_page.MISSIONS_DELETE_PATH}{actions_page.CONFIRM_SUFFIX}"' not in html, \
        "the per-row trashcan still opens the single-remove dialog"
    assert "trash" not in html, "a trashcan survived somewhere on the tab"


@pytest.mark.asyncio
async def test_the_tab_is_absent_and_refused_without_the_capability(config_dir, clean_registry):
    """A viewer the capability was narrowed away from gets no tab, and a crafted request is REFUSED.

    A HOSTER is used (not an Admin): narrowing ``missions.view`` to a role the hoster does not hold
    (and dropping its scope grant) leaves the server page reachable — through the hoster's SCOPE —
    while the tab is gone, so the test measures the tab's own predicate and nothing else.
    """
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "hoster")
        assert "?tab=missions" in await missions_page(http)

    # narrow ONLY missions.view (both axes): the page stays reachable, the tab does not
    narrow("missions.view", roles=("Admin",))
    async with client(app) as http:
        await sign_in(http, "hoster")
        page = await open_page(http, f"/fleet/{SERVER_A}")
        assert "?tab=missions" not in page, "the tab must be absent without missions.view"
        refused = await http.get(f"/fleet/{SERVER_A}?tab=missions")
    assert refused.status_code == 403
    assert "Not authorized" in refused.text


# --------------------------------------------------------------------------------- 2. the scope

@pytest.mark.asyncio
async def test_a_manager_sees_their_own_servers_missions_and_not_another_hosters(config_dir,
                                                                                 clean_registry):
    """A hoster of Host A reads SRS-1's missions; SRS-B (Host B) is refused, disclosed as nothing."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "hoster")
        mine = await missions_page(http, SERVER_A)
        assert "<b>Training.miz</b>" in mine, "the manager sees their own server's list"
        refused = await http.get(f"/fleet/{SERVER_B}?tab=missions")
    assert refused.status_code == 403, "another hoster's server must be refused"
    assert "SRS-B" not in refused.text, "the refusal must not disclose the target"


@pytest.mark.asyncio
async def test_a_manager_cannot_download_another_hosters_mission(config_dir, clean_registry):
    """The download route is scope-safe: a foreign server resolves to nothing, with a plain sentence."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "hoster")
        response = await http.get(f"/fleet/{SERVER_B}/missions/download?mission=1")
    assert response.status_code == 404
    assert "not found" in response.text.lower()
    assert response.content.strip(), "a refusal is never an empty body"


# ---------------------------------------------------------------- 3. the explained empty state

@pytest.mark.asyncio
async def test_the_tab_explains_when_the_action_cannot_answer(config_dir, clean_registry):
    """Server unreachable / missions dir missing: the ACTION's own sentence, never a stack trace."""
    app = build(config_dir, fleet(list_error=RuntimeError("the missions dir is gone")),
                plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await missions_page(http)
    assert "Failed to list missions" in html
    assert "the missions dir is gone" in html
    assert "missions/download" not in html, "no download control on an unavailable list"


# ------------------------------------------------------------------------------- 4. the download

@pytest.mark.asyncio
async def test_the_download_returns_the_bytes_under_the_logical_filename(config_dir, clean_registry):
    """The resolved file's bytes, with ``Content-Disposition: attachment`` and the logical ``.miz``."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/fleet/{SERVER_A}/missions/download?mission=1")
    assert reply.status_code == 200, reply.text
    assert reply.content == MISSION_BYTES
    assert reply.headers["content-disposition"] == 'attachment; filename="Training.miz"'
    assert reply.headers["content-type"].startswith("application/octet-stream")


@pytest.mark.asyncio
async def test_the_download_accepts_an_index_or_a_name(config_dir, clean_registry):
    """Both an index and the logical name resolve to the same mission — a NAME is fine, a PATH is not."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        by_index = await http.get(f"/fleet/{SERVER_A}/missions/download?mission=2")
        by_name = await http.get(f"/fleet/{SERVER_A}/missions/download?mission=Rotated.miz")
    assert by_index.status_code == by_name.status_code == 200
    assert by_index.content == by_name.content == MISSION_BYTES
    assert by_name.headers["content-disposition"] == 'attachment; filename="Rotated.miz"'


@pytest.mark.asyncio
@pytest.mark.parametrize("mission", ["/etc/passwd.miz", "../evil.miz", "sub/other.miz",
                                     "/missions/Training.miz", "Nope.miz", "99"])
async def test_the_download_refuses_a_path_or_unknown_mission(config_dir, clean_registry, mission):
    """A request that names a PATH (or an unknown mission) resolves to nothing — a plain 404."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/fleet/{SERVER_A}/missions/download",
                               params={"mission": mission})
    assert reply.status_code == 404, (mission, reply.status_code, reply.text)
    assert reply.content.strip(), "a refusal is never a zero-byte 200"
    assert "content-disposition" not in {key.lower() for key in reply.headers}


@pytest.mark.asyncio
async def test_the_download_requires_a_mission(config_dir, clean_registry):
    """No mission stated is its own sentence, not an empty file."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/fleet/{SERVER_A}/missions/download")
    assert reply.status_code == 400
    assert "required" in reply.text


@pytest.mark.asyncio
async def test_an_oversized_mission_is_refused_with_its_own_sentence(config_dir, clean_registry):
    """Above the limit the file is REFUSED whole, never served clipped."""
    limit = detail_page.MISSIONS_DOWNLOAD_MAX_BYTES
    cluster = fleet()
    cluster.servers[0].node.content = b"x" * (limit + 1)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/fleet/{SERVER_A}/missions/download?mission=1")
    assert reply.status_code == 413
    assert str(limit) in reply.text and str(limit + 1) in reply.text
    assert "content-disposition" not in {key.lower() for key in reply.headers}


@pytest.mark.asyncio
async def test_the_download_is_a_get_and_never_a_post(config_dir, clean_registry):
    """A read is a GET: the route answers no other method."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.post(f"/fleet/{SERVER_A}/missions/download?mission=1")
    assert reply.status_code == 405


@pytest.mark.asyncio
async def test_the_download_route_is_registered_under_missions_view(config_dir, clean_registry):
    """It is a real registered route, a NON-page, gated by the SAME capability the tab reads."""
    app = build(config_dir, fleet(), plugins=("mission",))
    registrar = app.state.webui_registrar
    routes = {getattr(route, "path", None): route for route in registrar.routes}
    assert detail_page.MISSIONS_DOWNLOAD_PATH in routes
    assert detail_page.MISSIONS_DOWNLOAD_PATH not in registrar.page_paths
    assert registrar.capability_for(route=routes[detail_page.MISSIONS_DOWNLOAD_PATH]) == "missions.view"


@pytest.mark.asyncio
async def test_the_mission_download_works_for_a_name_containing_a_slash(config_dir, clean_registry):
    """The ``{name:path}`` prefix still resolves: the download route is registered before the page."""
    from urllib.parse import quote

    odd = MissionServer("A/B server", ["Host A"], files=FILES, current=CURRENT, start=1)
    cluster = Cluster([odd], nodes={}, instances=[instance("Host-A-Instance", odd)])
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(
            f"/fleet/{quote('A/B server', safe='')}/missions/download?mission=1")
    assert reply.status_code == 200, reply.text
    assert reply.content == MISSION_BYTES
    assert reply.headers["content-disposition"] == 'attachment; filename="Training.miz"'


# --------------------------------------------------- 5. M2-fix: the download route's edge cases

@pytest.mark.asyncio
async def test_a_zero_byte_mission_is_refused_never_a_zero_byte_200(config_dir, clean_registry):
    """M2-fix 1: a successful read of an EMPTY ``.miz`` must not download like a real mission.

    A zero-byte payload is no usable content: it gets the SAME plain-text refusal as a non-bytes
    answer, with a sentence, never a zero-byte 200 that downloads like a complete (empty) mission.
    """
    cluster = fleet()
    cluster.servers[0].node.content = b""
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/fleet/{SERVER_A}/missions/download?mission=1")
    assert reply.status_code == 502, (reply.status_code, reply.text)
    assert reply.content.strip(), "a refusal is never a zero-byte 200"
    assert "no file content" in reply.text
    assert "content-disposition" not in {key.lower() for key in reply.headers}


@pytest.mark.asyncio
async def test_a_server_named_like_the_download_path_still_opens_its_page(config_dir, clean_registry):
    """M2-fix 2: a server whose own name ends in ``/missions/download`` is not swallowed by the route.

    Its page URL carries the SAME shape as a download URL for a shorter server, so a request with NO
    mission must be resolved against the LONGER name and render that server's PAGE — while its OWN
    download (a request that DOES state a mission) still hands over the bytes.
    """
    from urllib.parse import quote

    from services.webservice.readmodels.servers import server_url

    name = "Odd/missions/download"
    odd = MissionServer(name, ["Host A"], files=FILES, current=CURRENT, start=1)
    cluster = Cluster([odd], nodes={}, instances=[instance("Host-A-Instance", odd)])
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, server_url(name))
        reply = await http.get(f"/fleet/{quote(name, safe='')}/missions/download?mission=1")
    assert name in page, "the collision server's page was swallowed by the download route"
    assert "?tab=overview" in page, "the page did not render its tab strip"
    assert reply.status_code == 200, reply.text
    assert reply.content == MISSION_BYTES


@pytest.mark.asyncio
async def test_a_viewer_without_missions_view_is_refused_on_the_download_url(config_dir,
                                                                            clean_registry):
    """M2-fix 3: the download route's OWN gate refuses a caller without ``missions.view``.

    Proved live by the reviewer, pinned here: the gate is the route's, so the handler is never
    reached — no file is read — and the refusal is the console's 403.
    """
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "plain")            # DCS role, no scope: no ``missions.view``
        reply = await http.get(f"/fleet/{SERVER_A}/missions/download?mission=1")
    assert reply.status_code == 403, (reply.status_code, reply.text)
    assert cluster.servers[0].node.reads == [], "a refused caller must reach no mission file"


# ---------------------------------------------- 6. M3: the CONFIGURED list and its four writes

#: FIVE mission files on disk; only TWO of them configured — the operator's exact bug shape.
CONFIGURED_FILES = [
    (os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, ".dcssb", "Training.miz")),
    (os.path.join(BASE_DIR, "Rotated.miz"), os.path.join(BASE_DIR, "Rotated.miz")),
    (os.path.join(BASE_DIR, "Extra1.miz"), os.path.join(BASE_DIR, "Extra1.miz")),
    (os.path.join(BASE_DIR, "Extra2.miz"), os.path.join(BASE_DIR, "Extra2.miz")),
    (os.path.join(BASE_DIR, "Extra3.miz"), os.path.join(BASE_DIR, "Extra3.miz")),
]
CONFIGURED = [os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, "Rotated.miz")]


def configured_fleet():
    a = MissionServer(SERVER_A, ["Host A"], files=CONFIGURED_FILES, current=CURRENT, start=1,
                      configured=CONFIGURED, list_files=[os.path.join(BASE_DIR, "New.miz"),
                                                         os.path.join(BASE_DIR, "Training.miz")])
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    return Cluster([a, b], nodes={}, instances=[instance("Host-A-Instance", a),
                                                instance("Host-B-Instance", b)])


async def post_mission(http, action_path, server, token, **fields):
    data = {"server": server, "_csrf_token": token}
    data.update(fields)
    return await http.post(action_path, data=data)


@pytest.mark.asyncio
async def test_the_tab_shows_only_the_configured_missions(config_dir, clean_registry):
    """THE BUG: five files on disk, two configured — the tab renders ONLY the configured two."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await missions_page(http)

    assert html.count("data-sel-box") == 2, "only the configured missions appear"
    assert "<b>Training.miz</b>" in html and "<b>Rotated.miz</b>" in html
    assert "Extra1.miz" not in html and "Extra2.miz" not in html and "Extra3.miz" not in html


@pytest.mark.asyncio
async def test_a_configured_mission_whose_file_is_gone_is_tagged_missing(config_dir, clean_registry):
    """A configured entry the disk cannot resolve still renders, tagged ``missing``."""
    cluster = configured_fleet()
    cluster.servers[0]._files = [(os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, "Training.miz"))]
    cluster.servers[0].configured = [os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, "Gone.miz")]
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await missions_page(http)
    assert "Gone.miz" in html
    assert '<span class="tag">missing</span>' in html


@pytest.mark.asyncio
async def test_the_add_control_posts_the_picked_file(config_dir, clean_registry):
    """The ADD flow is DECISIVE: the card's button opens the dialog, the picker and the load boxes
    live there, and the chosen file is added with the load choice honoured (M4's one-step flow)."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        # the card opens the DIALOG (a plain form — no data-async, or the dialog never shows)
        assert f'action="{actions_page.MISSIONS_ADD_PATH}{actions_page.CONFIRM_SUFFIX}"' in page
        token = token_of(page)
        # step 1: the dialog carries the picker AND the load pair
        dialog = await http.post(f"{actions_page.MISSIONS_ADD_PATH}{actions_page.CONFIRM_SUFFIX}",
                                 data={"server": SERVER_A, "_csrf_token": token})
        assert dialog.status_code == 200, dialog.text
        assert 'name="path"' in dialog.text, "the picker is on the dialog"
        assert 'value="New.miz"' in dialog.text, "an unconfigured file is offered"
        assert 'name="autostart"' in dialog.text and 'name="load"' in dialog.text
        # step 2: choosing and confirming posts the picker's path back to the write route
        reply = await http.post(actions_page.MISSIONS_ADD_PATH,
                                data={"server": SERVER_A, "_csrf_token": token_of(dialog.text),
                                      "path": "New.miz", "autostart": "1"})
    assert reply.status_code == 303, (reply.status_code, reply.text)
    added = [path for path, _auto in cluster.servers[0].adds]
    assert added and added[0].endswith("New.miz"), added
    assert cluster.servers[0].adds[0][1] is True, "the 'load at next start' box rode addMission"


@pytest.mark.asyncio
async def test_the_load_control_posts_the_index(config_dir, clean_registry):
    """The per-row LOAD posts the 1-based index; the route converts it to ``load_mission``'s 0-based one."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        token = token_of(page)
        reply = await post_mission(http, "/actions/server/missions/load", SERVER_A, token, mission="2")
    assert reply.status_code == 303, (reply.status_code, reply.text)
    loads = cluster.servers[0].loads
    assert loads and str(loads[0][0]).endswith("Rotated.miz"), \
        "the 1-based row index (row 2) becomes load_mission's 0-based index -> the Rotated mission"


@pytest.mark.asyncio
async def test_the_remove_path_is_the_tick_and_the_bar(config_dir, clean_registry):
    """REMOVE is the SELECTION now (M9): a box per row belongs to the ONE selection form, the bar's
    button opens the console's one dialog, and confirming removes EXACTLY the ticked missions."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        # the selection form is the row boxes' owner; the bar submits it
        assert '<form id="missions-form"' in page
        assert 'form="missions-form"' in page, "the row box is not associated with the selection form"
        assert "data-sel-go" in page and "data-sel-count" in page
        token = token_of(page)
        dialog = await http.post(actions_page.MISSIONS_BULK_DELETE_PATH + actions_page.CONFIRM_SUFFIX,
                                 data={"server": SERVER_A, "_csrf_token": token,
                                       "mission": "2", "mission_name": "2=Rotated.miz"})
        assert dialog.status_code == 200, dialog.text
        assert "<li>Rotated.miz</li>" in dialog.text, "the dialog shows the chosen mission read-only"
        confirm = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
        reply = await http.post(actions_page.MISSIONS_BULK_DELETE_PATH,
                                data={"server": SERVER_A, "_csrf_token": token_of(dialog.text),
                                      "mission": "2", "mission_name": "2=Rotated.miz",
                                      "delete_from_disk": ["0", "1"], "_confirm_token": confirm})
    assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert server.deletes == [2]
    assert any(str(path).endswith("Rotated.miz") for path in server.node.removed), server.node.removed


@pytest.mark.asyncio
async def test_the_upload_control_posts_the_file(config_dir, clean_registry, monkeypatch):
    """UPLOAD posts a MULTIPART file; the action stages its BYTES and calls the ONE writer."""
    import plugins.mission.actions as mission_actions

    async def fake_stage(node, name, data):
        assert data == b"MIZ-UPLOAD"
        return 1

    async def fake_discard(node, row_id):
        return None

    monkeypatch.setattr(mission_actions, "_stage_mission_bytes", fake_stage)
    monkeypatch.setattr(mission_actions, "_discard_staged_mission", fake_discard)

    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        token = token_of(page)
        reply = await http.post(
            "/actions/server/missions/upload",
            data={"server": SERVER_A, "_csrf_token": token, "autostart": "1"},
            files={"file": ("Uploaded.miz", b"MIZ-UPLOAD", "application/octet-stream")})
    assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert server.uploads and server.uploads[0][0] == "Uploaded.miz", server.uploads
    assert server.indexes, "the 'load at next start' box armed the rotation"


@pytest.mark.asyncio
async def test_a_manager_cannot_write_missions_on_a_foreign_server(config_dir, clean_registry):
    """A hoster of Host A is refused on SRS-B — the scope, not the capability, is the gate."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "hoster")
        page = await missions_page(http, SERVER_A)
        token = token_of(page)
        reply = await post_mission(http, "/actions/server/missions/add", SERVER_B, token, path="X.miz")
    assert reply.status_code == 403, (reply.status_code, reply.text)
    assert cluster.servers[1].adds == [], "a foreign server must not be written"


@pytest.mark.asyncio
async def test_a_viewer_without_the_capability_is_refused_on_a_mission_write(config_dir,
                                                                            clean_registry):
    """A ``plain`` viewer (no scope, no role) is refused at the capability gate — nothing runs."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "plain")
        reply = await post_mission(http, "/actions/server/missions/add", SERVER_A, "x", path="X.miz")
    assert reply.status_code == 403, (reply.status_code, reply.text)
    assert cluster.servers[0].adds == []


@pytest.mark.asyncio
async def test_the_mission_writes_are_registered_under_their_own_capabilities(config_dir,
                                                                              clean_registry):
    """Each mission route is a real POST gated by its OWN capability — one predicate per write."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    registrar = app.state.webui_registrar
    expected = {
        "/actions/server/missions/add": "missions.add",
        "/actions/server/missions/delete": "missions.delete",
        "/actions/server/missions/load": "missions.load",
        "/actions/server/missions/upload": "missions.upload",
    }
    for path, capability in expected.items():
        assert registrar.capability_for(path=path) == capability, path


# ------------------------------------------- 7. M4-fix: the 1-based index, the cap, the empty upload


@pytest.mark.asyncio
async def test_a_zero_index_download_is_refused_with_the_one_based_sentence(config_dir,
                                                                           clean_registry):
    """``?mission=0`` says the indexes are 1-based, never "mission '0' not found".

    Same 404 as any other unresolved mission (the reader did ask for one), but the SENTENCE explains
    why 0 cannot name anything, and no mission file is read.
    """
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/fleet/{SERVER_A}/missions/download?mission=0")
    assert reply.status_code == 404, (reply.status_code, reply.text)
    assert reply.content.strip(), "a refusal is never a zero-byte 200"
    assert "1-based" in reply.text and "'0' is not a mission index" in reply.text
    assert cluster.servers[0].node.reads == [], "a refused index must reach no mission file"


@pytest.mark.asyncio
async def test_the_load_control_refuses_a_zero_index_with_the_same_sentence(config_dir,
                                                                            clean_registry):
    """``mission=0`` on the LOAD route is refused in the same 1-based sentence, not as index ``-1``.

    The route converts the tab's 1-based index to ``load_mission``'s 0-based one; ``0`` must be
    refused BEFORE that conversion, or the action answers about an index nobody typed.
    """
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        token = token_of(page)
        reply = await post_mission(http, "/actions/server/missions/load", SERVER_A, token, mission="0")
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert "1-based" in after, "the notice must explain that the indexes are 1-based"
    assert "is not a mission index" in after, "the notice must name what was wrong"
    assert cluster.servers[0].loads == [], "a refused index must load nothing"


def test_the_console_upload_cap_matches_the_actions():
    """The console's cap and the ACTION's cap are ONE number, and the refusal is ONE sentence.

    The console keeps its own copy (the generic web surface never imports a plugin module), so this
    pin is what keeps them from drifting.
    """
    import plugins.mission.actions as mission_actions

    assert actions_page.MISSIONS_UPLOAD_MAX_BYTES == mission_actions.MAX_MISSION_UPLOAD_BYTES
    assert actions_page._upload_cap_message() == mission_actions._over_cap_message()


@pytest.mark.asyncio
async def test_an_upload_over_the_cap_is_refused_without_reading_it(config_dir, clean_registry,
                                                                   monkeypatch):
    """A file the part DECLARES as over the cap is refused before its bytes are read at all."""
    monkeypatch.setattr(actions_page, "MISSIONS_UPLOAD_MAX_BYTES", 8)
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        token = token_of(page)
        reply = await http.post(
            "/actions/server/missions/upload",
            data={"server": SERVER_A, "_csrf_token": token},
            files={"file": ("Big.miz", b"X" * 64, "application/octet-stream")})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert cluster.servers[0].uploads == [], "an over-cap upload must never reach the writer"
    assert "larger than the" in after and "limit and was not uploaded" in after


@pytest.mark.asyncio
async def test_an_upload_body_over_the_cap_is_refused_before_it_is_parsed(config_dir, clean_registry,
                                                                         monkeypatch):
    """A DECLARED body that cannot fit the cap is refused with a 413 before ``request.form()``.

    Both the cap and the (generous) multipart envelope are shrunk to zero so the tiny test POST is
    over the line: the proof is that the response is the bare 413 and no writer was reached.
    """
    monkeypatch.setattr(actions_page, "MISSIONS_UPLOAD_MAX_BYTES", 0)
    monkeypatch.setattr(actions_page, "_MULTIPART_ENVELOPE_BYTES", 0)
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        token = token_of(page)
        reply = await http.post(
            "/actions/server/missions/upload",
            data={"server": SERVER_A, "_csrf_token": token},
            files={"file": ("Big.miz", b"X" * 8, "application/octet-stream")})
    assert reply.status_code == 413, (reply.status_code, reply.text)
    assert cluster.servers[0].uploads == [], "an over-cap body must never reach the writer"


@pytest.mark.asyncio
async def test_a_zero_byte_upload_from_the_console_is_refused_by_the_action(config_dir,
                                                                           clean_registry):
    """A chosen 0-byte ``.miz`` is handed to the ACTION, which refuses it in its own sentence.

    The console only owns the "no file part at all" case; an EMPTY file is the action's refusal, so
    the sentence an operator sees is the ONE the action owns (never a second copy in the web layer).
    """
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        token = token_of(page)
        reply = await http.post(
            "/actions/server/missions/upload",
            data={"server": SERVER_A, "_csrf_token": token},
            files={"file": ("Empty.miz", b"", "application/octet-stream")})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert "is empty and was not uploaded" in after, "the ACTION's own empty-upload sentence"
    assert cluster.servers[0].uploads == [], "the writer must never see an empty mission"


@pytest.mark.asyncio
async def test_an_upload_with_no_file_part_still_says_required(config_dir, clean_registry):
    """No file part at all keeps the console's own "required" sentence — the empty file is separate."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        token = token_of(page)
        reply = await http.post("/actions/server/missions/upload",
                                data={"server": SERVER_A, "_csrf_token": token})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert "is required to upload" in after
    assert cluster.servers[0].uploads == []


@pytest.mark.asyncio
async def test_the_streaming_read_stops_the_moment_the_cap_is_crossed():
    """``_read_upload_capped`` refuses an over-cap stream WITHOUT draining it.

    A body that lies about its length (or arrives chunked, with no ``Content-Length``) is caught by
    the running total: the read stops at the crossing chunk and ``None`` is returned, so the bytes
    are never accumulated whole.
    """
    class ChunkedUpload:
        """A stream that hands back at most 64 bytes per call, whatever size is asked for."""

        def __init__(self, data):
            self._data = data
            self.calls: list[int] = []

        async def read(self, size=-1):
            self.calls.append(size)
            chunk, self._data = self._data[:64], self._data[64:]
            return chunk

    small = ChunkedUpload(b"x" * 50)
    assert await actions_page._read_upload_capped(small, 100) == b"x" * 50

    big = ChunkedUpload(b"x" * 4096)
    assert await actions_page._read_upload_capped(big, 100) is None
    assert len(big.calls) == 2, "the read must stop at the crossing chunk, not drain the body"
    assert big._data, "the rest of the body was never pulled"
