"""The Music tab on the server page: its two-fact gate, its body, and its three radio writes.

The tab is offered on TWO facts, both read without asking a node: the music plugin's read action is
registered in this process (``action_available``), and ``config/services/music.yaml`` states at least
one radio for THIS server — read master-side through the service's own ``radios_declared``, i.e. its
``get_config(server)`` with the server's block merged over ``DEFAULT``. The strip, the tab and the
crafted ``?tab=music`` read ONE predicate (``music_tab_gate``), so a link and a refusal cannot
disagree.

Pinned here: the tab's place in the strip on BOTH tuples; the gate in each of its three answers (no
action, no radios, offered) at the module level and through the REAL app; a save that posts through
the playwrite action with the session's CSRF token and writes only the values the form stated; the
transport's verb travelling in the body; the playlist assignment; a manager driving their own
server's radios (the scope grant) and being refused on a foreign one (the scope itself); a viewer
without ``music.control`` seeing the radios and NO control, with a crafted POST refused; and the
sentence that an edit saved in the playlist editor is not audible on an air radio.

Driven through the real app with the real local login backend and a stub cluster. The music SERVICE
is a double registered in the service registry — that is the seam the gate and the actions both read
— so the plugin's own action functions run against it unchanged.
"""
from __future__ import annotations

import sys

import pytest

from core.actions import action_registry
from core.services.registry import ServiceRegistry
from services.webservice import permissions, session
from services.webservice.pages import actions as actions_page
from services.webservice.pages import server_detail as detail_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, recording_bot, server,
    sign_in, token_of,
)

SERVER_A = "SRS-1"
SERVER_B = "SRS-B"

RADIO = "srs-alpha"


def radio(name=RADIO, *, on_air=False, playlist="Rock", mode="repeat", tracks=3,
          title="Fortunate Son", artist="Creedence", frequency="30.000"):
    """One radio entry shaped exactly as ``MusicService.read_radios`` returns it."""
    return {
        "name": name, "type": "SRS", "frequency": frequency, "modulation": "AM",
        "coalition": "2", "volume": "0.500", "display_name": "Alpha Radio", "mode": mode,
        "on_air": on_air, "playlist": playlist, "tracks": tracks,
        "current": ({"title": title, "artist": artist, "album": "", "file": f"{name}/01.mp3",
                     "duration": 183.0} if on_air else {}),
    }


class FakeMusicService:
    """The music service double: the reads the tab makes, and the writes its routes reach.

    Registered in the service registry, which is the seam BOTH readers use — the page's gate
    (``radios_declared``) and the plugin's own action functions (``read_radios``, the playlist read
    and the three writes) — so the tab is exercised end to end with the real action layer.
    """

    def __init__(self, radios_by_server, playlists=("Rock", "Jazz")):
        self._radios = {name: list(rows) for name, rows in radios_by_server.items()}
        self._playlists = list(playlists)
        self.config_writes: list[tuple[str, str, dict]] = []
        self.controls: list[tuple[str, str]] = []
        self.assignments: list[tuple[str, str, str]] = []

    # ── the reads the tab and the gate make ──────────────────────────────
    def radios_declared(self, server_obj):
        return {row["name"]: {"type": row["type"], "frequency": row["frequency"]}
                for row in self._radios.get(server_obj.name, [])}

    async def read_radios(self, server_obj, refresh=False):
        return [dict(row) for row in self._radios.get(server_obj.name, [])]

    async def playlist_usage(self):
        """The ``music_radios`` rows — one per (server, radio), which is what picks the read node."""
        return [{"server": name, "radio": row["name"], "playlist": row["playlist"]}
                for name, rows in self._radios.items() for row in rows if row["playlist"]]

    async def list_playlists(self, node, refresh=False):
        return [{"name": name, "tracks": 2, "seconds": 120.0, "missing": 0}
                for name in self._playlists]

    async def playlist_exists(self, name):
        return name in self._playlists

    # ── the writes the routes reach ──────────────────────────────────────
    async def assign_playlist(self, server_obj, radio_name, playlist):
        self.assignments.append((server_obj.name, radio_name, playlist))
        return ""

    async def start_radio(self, server_obj, radio_name):
        self.controls.append(("play", radio_name))
        return ""

    async def stop_radio(self, server_obj, radio_name):
        self.controls.append(("stop", radio_name))
        return ""

    async def skip_radio(self, server_obj, radio_name):
        self.controls.append(("skip", radio_name))
        return ""

    async def set_mode(self, server_obj, radio_name, mode):
        self.controls.append((f"mode:{mode.name.lower()}", radio_name))
        return ""

    async def set_radio_config(self, server_obj, radio_name, changes):
        self.config_writes.append((server_obj.name, radio_name, dict(changes)))
        return ""


def fleet(*, radios=True):
    """Server A (with a radio, hosted by ``Host A``) and server B (no radio, hosted by ``Host B``)."""
    a = server(SERVER_A, ["Host A"])
    b = server(SERVER_B, ["Host B"], node_name="dcs-b-01", instance="Host-B-Instance")
    node_a = types_simple_node("dcs-a-01", {"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a),
                              instance("Host-B-Instance", b)])


def types_simple_node(name, instances):
    import types

    return types.SimpleNamespace(name=name, master=True, instances=instances)


@pytest.fixture
def music_registry():
    """The ``@action`` registry, restored afterwards — and ``plugins.music.actions`` uncached.

    ``discover_actions`` populates the registry through decorators a module runs ON IMPORT, so a
    module left in the cache would make the second install observe an empty registry. Cleared BEFORE
    the test, so the "not registered" case cannot pass on a registry an earlier test filled.
    """
    from core import actions as actions_module

    saved = dict(actions_module._ACTIONS)
    actions_module._ACTIONS.clear()
    sys.modules.pop("plugins.music.actions", None)
    try:
        yield actions_module._ACTIONS
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.music.actions", None)


@pytest.fixture
def music_service():
    """Install a music service double, and hand it to the test."""
    from services.music import MusicService

    service = FakeMusicService({SERVER_A: [radio()]})
    saved = ServiceRegistry._singletons.get(MusicService)
    ServiceRegistry._singletons[MusicService] = service
    try:
        yield service
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(MusicService, None)
        else:
            ServiceRegistry._singletons[MusicService] = saved


# -------------------------------------------------------------------------------- 1. the declaration

def test_the_tab_sits_between_mods_and_log_on_both_tuples():
    """The ORDER is the declaration's: mods, then music, then log — on BOTH tuples.

    ``OPTIONAL_TAB_ORDER`` drives the strip and ``TAB_ORDER`` is the set ``normalise_tab`` accepts;
    a key in one and not the other is a tab that either cannot be reached or cannot be normalised.
    """
    for order in (detail_page.TAB_ORDER, detail_page.OPTIONAL_TAB_ORDER):
        assert (order.index(detail_page.MODS_TAB)
                < order.index(detail_page.MUSIC_TAB)
                < order.index(detail_page.LOG_TAB)), order
    assert detail_page.MUSIC_TAB == "music"
    assert detail_page.TAB_LABELS[detail_page.MUSIC_TAB] == "Music"


def test_the_tab_declares_no_capability_of_its_own():
    """Whoever may open the page may read its radios: the tab adds no gate beyond the page's.

    The WRITES are the gated half, on their own paths (``pages/actions.MUSIC_ACTIONS``) — which is
    why the read needs no capability of its own: the page already resolved this server through the
    scoped source.

    THE ONE EXCEPTION IS THE TAB'S LIVE PLAYER. A stream outlives the render that resolved the server,
    so its two paths re-check the MUSIC AREA's own ``music.view`` (the capability a playlist editor is
    opened on) and the server's scope, in the route. The tab's own key still declares nothing: no
    capability is keyed by ``MUSIC_TAB``.
    """
    assert detail_page.MUSIC_TAB not in detail_page.capabilities()
    view = detail_page.music_page_module().CAPABILITY_VIEW
    music_paths = {path: capability for path, capability in detail_page.capabilities().items()
                   if "music" in path}
    assert music_paths == {detail_page.MUSIC_TAB_STREAM_PATH: view,
                           detail_page.MUSIC_TAB_FRAGMENTS_PATH: view}, music_paths
    for action in actions_page.MUSIC_ACTIONS:
        assert actions_page.capabilities()[action.path] == actions_page.MUSIC_CONTROL_CAPABILITY


def test_the_three_radio_writes_take_music_control_with_the_scope_grant():
    """``music.control`` is the console's server-write rule: both roles, plus a manager's scope.

    Both halves together, or a manager of this server would see the radios and never touch them.
    """
    assert permissions.roles_for_capability(actions_page.MUSIC_CONTROL_CAPABILITY) == (
        "Admin", "DCS Admin")
    assert permissions.scope_grants(actions_page.MUSIC_CONTROL_CAPABILITY) is True
    assert {action.qualname for action in actions_page.MUSIC_ACTIONS} == {
        "control_radio", "set_radio_playlist", "set_radio_config"}


# -------------------------------------------------------------------------------------- 2. the gate

def test_the_gate_refuses_without_the_action(music_registry):
    """No music plugin here: no tab, and the refusal names the plugin (never a radio)."""
    server_obj = server(SERVER_A)
    offered, reason = detail_page.music_tab_gate(server_obj)
    assert offered is False
    assert reason == detail_page.MUSIC_NO_ACTIONS


def test_the_gate_refuses_a_server_the_file_configures_no_radio_for(music_registry, music_service):
    """The action is here and the file states a radio for A — B still gets no tab."""
    import plugins.music.actions  # noqa: F401 - the import IS the registration

    a = server(SERVER_A)
    b = server(SERVER_B)
    assert detail_page.music_tab_gate(a) == (True, "")
    offered, reason = detail_page.music_tab_gate(b)
    assert offered is False and reason == detail_page.MUSIC_NO_RADIOS


def test_the_gate_reads_the_radios_through_the_service_own_config(music_registry, music_service):
    """The second fact is the SERVICE's answer for THIS server, not a global flag."""
    calls = []
    original = music_service.radios_declared
    music_service.radios_declared = lambda srv: calls.append(srv.name) or original(srv)
    import plugins.music.actions  # noqa: F401

    detail_page.music_tab_gate(server(SERVER_B))
    assert calls == [SERVER_B]


def test_the_vocabulary_is_the_action_modules_own():
    """The picks the form draws are the sets the action validates against — one home."""
    from plugins.music import actions as music_actions

    words = detail_page.music_vocabulary()
    assert words["modulations"] == tuple(music_actions.MODULATIONS)
    assert words["coalitions"] == tuple(music_actions.COALITIONS)
    assert words["modes"] == tuple(music_actions.MODES)


# --------------------------------------------------------------------------- 3. the strip, in the app

@pytest.mark.asyncio
async def test_the_tab_is_offered_for_a_server_that_has_radios(
        config_dir, music_registry, music_service, clean_registry):
    """Server A has a radio: the strip carries the tab and the body renders its card."""
    app = build(config_dir, fleet(), plugins=("mission", "music"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, f"/fleet/{SERVER_A}")

    assert "?tab=music" in page, "the tab must be offered for a server the file configures a radio for"


@pytest.mark.asyncio
async def test_the_body_carries_the_radio_its_settings_and_its_track(
        config_dir, music_registry, music_service, clean_registry):
    """The card: the display name, what the radio is set to, its state and the current track."""
    app = build(config_dir, fleet(), plugins=("mission", "music"))
    ServiceRegistry._singletons[_service_key()] = FakeMusicService(
        {SERVER_A: [radio(on_air=True)]})
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, f"/fleet/{SERVER_A}?tab=music")

    assert "Alpha Radio" in page
    # THE TUNING CHIP: the frequency and the modulation, the console's own two values on one mark
    assert "30.000" in page and "AM" in page
    assert ">on air<" in page, "the state tag carries the console's own word, shared with /music"
    assert "Fortunate Son" in page and "Creedence" in page
    assert "Rock" in page
    # the editor link is built by the music area's own encoder
    assert 'href="/music/Rock"' in page


@pytest.mark.asyncio
async def test_a_server_with_no_radios_draws_no_tab_and_the_crafted_request_is_refused(
        config_dir, music_registry, music_service, clean_registry):
    """Server B: no tab in the strip, and ``?tab=music`` is REFUSED — never rendered empty."""
    app = build(config_dir, fleet(), plugins=("mission", "music"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, f"/fleet/{SERVER_B}")
        refused = await http.get(f"/fleet/{SERVER_B}?tab=music")

    assert "?tab=music" not in page
    assert refused.status_code == 403
    assert detail_page.MUSIC_NO_RADIOS in refused.text


@pytest.mark.asyncio
async def test_an_install_without_the_music_plugin_draws_no_tab_and_refuses(
        config_dir, music_registry, music_service, clean_registry):
    """The action is not registered: the plugin is off, so there is no tab and no body."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, f"/fleet/{SERVER_A}")
        refused = await http.get(f"/fleet/{SERVER_A}?tab=music")

    assert "?tab=music" not in page
    assert refused.status_code == 403
    assert detail_page.MUSIC_NO_ACTIONS in refused.text


# -------------------------------------------------------------------------- 4. the writes, in the app

def _service_key():
    from services.music import MusicService

    return MusicService


async def _open_tab(http, server_name=SERVER_A):
    page = await open_page(http, f"/fleet/{server_name}?tab=music")
    return page, token_of(page)


@pytest.mark.asyncio
async def test_a_save_posts_through_the_action_with_the_csrf_token(
        config_dir, music_registry, music_service, clean_registry, recording_bot):
    """One settings form, ONE action: only the values carrying text reach the service."""
    app = build(config_dir, fleet(), plugins=("mission", "music"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page, token = await _open_tab(http)
        assert f'action="{actions_page.MUSIC_SETTINGS_PATH}"' in page
        reply = await http.post(actions_page.MUSIC_SETTINGS_PATH,
                                data={"server": SERVER_A, "radio": RADIO,
                                      session.CSRF_FIELD: token,
                                      "frequency": "31.500", "modulation": "",
                                      "coalition": "", "volume": "", "display_name": ""},
                                follow_redirects=False)

    assert reply.status_code == 303
    assert reply.headers["location"] == f"/fleet/{SERVER_A}?tab=music"
    assert music_service.config_writes == [(SERVER_A, RADIO, {"frequency": "31.500"})]
    assert recording_bot.calls, "the action's own audit entry was not written"


@pytest.mark.asyncio
async def test_the_transport_and_the_playlist_post_their_own_words(
        config_dir, music_registry, music_service, clean_registry, recording_bot):
    """The verb rides the body, and the playlist write names the playlist — never a URL."""
    app = build(config_dir, fleet(), plugins=("mission", "music"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page, token = await _open_tab(http)
        assert f'action="{actions_page.MUSIC_CONTROL_PATH}"' in page
        assert f'action="{actions_page.MUSIC_PLAYLIST_PATH}"' in page
        for verb in ("play", "skip", "stop"):
            reply = await http.post(actions_page.MUSIC_CONTROL_PATH,
                                    data={"server": SERVER_A, "radio": RADIO,
                                          session.CSRF_FIELD: token,
                                          actions_page.MUSIC_CONTROL_FIELD: verb},
                                    follow_redirects=False)
            assert reply.status_code == 303, verb
        mode = await http.post(actions_page.MUSIC_CONTROL_PATH,
                               data={"server": SERVER_A, "radio": RADIO,
                                     session.CSRF_FIELD: token,
                                     actions_page.MUSIC_CONTROL_FIELD: "mode",
                                     actions_page.MUSIC_MODE_FIELD: "shuffle"},
                               follow_redirects=False)
        assigned = await http.post(actions_page.MUSIC_PLAYLIST_PATH,
                                   data={"server": SERVER_A, "radio": RADIO,
                                         session.CSRF_FIELD: token,
                                         actions_page.MUSIC_PLAYLIST_FIELD: "Jazz"},
                                   follow_redirects=False)

    assert mode.status_code == 303 and assigned.status_code == 303
    assert music_service.controls == [("play", RADIO), ("skip", RADIO), ("stop", RADIO),
                                      ("mode:shuffle", RADIO)]
    assert music_service.assignments == [(SERVER_A, RADIO, "Jazz")]


@pytest.mark.asyncio
async def test_a_manager_drives_the_radios_of_their_own_server(
        config_dir, music_registry, music_service, clean_registry, recording_bot):
    """The scope grant: a ``managed_by`` manager of THIS server holds ``music.control``."""
    app = build(config_dir, fleet(), plugins=("mission", "music"))
    async with client(app) as http:
        await sign_in(http, "hoster")                   # manages Host A (owns SRS-1)
        page, token = await _open_tab(http)
        assert f'action="{actions_page.MUSIC_CONTROL_PATH}"' in page
        reply = await http.post(actions_page.MUSIC_CONTROL_PATH,
                                data={"server": SERVER_A, "radio": RADIO,
                                      session.CSRF_FIELD: token,
                                      actions_page.MUSIC_CONTROL_FIELD: "play"},
                                follow_redirects=False)

    assert reply.status_code == 303
    assert music_service.controls == [("play", RADIO)]


@pytest.mark.asyncio
async def test_a_manager_of_another_server_is_refused_and_nothing_is_written(
        config_dir, music_registry, music_service, clean_registry, recording_bot):
    """The SCOPE half: the capability admits a manager, ``resolve_scoped_server`` narrows it."""
    app = build(config_dir, fleet(), plugins=("mission", "music"))
    async with client(app) as http:
        await sign_in(http, "hoster-b")                 # manages Host B, not SRS-1
        token = token_of(await open_page(http, "/fleet"))
        reply = await http.post(actions_page.MUSIC_CONTROL_PATH,
                                data={"server": SERVER_A, "radio": RADIO,
                                      session.CSRF_FIELD: token,
                                      actions_page.MUSIC_CONTROL_FIELD: "play"},
                                follow_redirects=False)

    assert reply.status_code == 403, "a foreign server's radio write must be refused"
    assert music_service.controls == []


@pytest.mark.asyncio
async def test_a_post_without_the_csrf_token_is_refused_and_nothing_runs(
        config_dir, music_registry, music_service, clean_registry, recording_bot):
    app = build(config_dir, fleet(), plugins=("mission", "music"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.post(actions_page.MUSIC_SETTINGS_PATH,
                                data={"server": SERVER_A, "radio": RADIO, "frequency": "32.0"},
                                follow_redirects=False)

    assert reply.status_code == 403
    assert music_service.config_writes == []


@pytest.mark.asyncio
async def test_a_viewer_without_the_capability_gets_no_control_and_a_crafted_post_is_refused(
        config_dir, music_registry, music_service, clean_registry, recording_bot, monkeypatch):
    """Whoever may open the page reads the radios; only a ``music.control`` holder sees a control.

    The shipped declaration admits exactly the page's own audience (both cluster roles, plus a
    manager's scope), so the narrow declaration below is what makes the case reachable: with
    ``music.control`` held by ``Admin`` alone, a DCS Admin opens the tab, reads the radios — and the
    card carries no transport, no pick and no save form, while a crafted POST is refused by the gate
    before any handler runs.
    """
    app = build(config_dir, fleet(), plugins=("mission", "music"))
    monkeypatch.setitem(permissions.CAPABILITIES, actions_page.MUSIC_CONTROL_CAPABILITY, ("Admin",))
    monkeypatch.setitem(permissions.SCOPE_GRANTS, actions_page.MUSIC_CONTROL_CAPABILITY, False)
    async with client(app) as http:
        await sign_in(http, "mod1")                     # DCS Admin: reads, may not control
        page, token = await _open_tab(http)
        reply = await http.post(actions_page.MUSIC_CONTROL_PATH,
                                data={"server": SERVER_A, "radio": RADIO,
                                      session.CSRF_FIELD: token,
                                      actions_page.MUSIC_CONTROL_FIELD: "play"},
                                follow_redirects=False)

    assert "Alpha Radio" in page, "the radios are still readable"
    for path in (actions_page.MUSIC_CONTROL_PATH, actions_page.MUSIC_PLAYLIST_PATH,
                 actions_page.MUSIC_SETTINGS_PATH):
        assert f'action="{path}"' not in page, f"a control was drawn for {path}"
    assert reply.status_code == 403
    assert music_service.controls == []


# --------------------------------------------------------------------------------- 5. the sentence

@pytest.mark.asyncio
async def test_the_tab_renders_in_the_request_language(
        config_dir, music_registry, music_service, clean_registry):
    """The card's chrome is translated at render; the ACTION's own sentences stay verbatim.

    Driven through the real app's per-language environment, so a msgid the catalogs carry but the
    compiled ``.mo`` does not would show here — English is the msgid, so its render is unchanged.
    """
    from services.webservice import i18n

    app = build(config_dir, fleet(), plugins=("mission", "music"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        english = await open_page(http, f"/fleet/{SERVER_A}?tab=music")
        http.cookies.set(i18n.COOKIE_NAME, "de")
        german = await open_page(http, f"/fleet/{SERVER_A}?tab=music")

    assert ">Music</a>" in english and ">Musik</a>" in german
    assert ">idle<" in english and ">inaktiv<" in german
    assert "Radio settings" in english and "Radio-Einstellungen" in german
    assert ">Frequency<" in english and ">Frequenz<" in german
    assert ">idle<" not in german and "Nothing is playing on this radio." not in german


@pytest.mark.asyncio
async def test_the_playlist_warning_stands_where_the_playlist_is_chosen(
        config_dir, music_registry, music_service, clean_registry):
    """An edit saved in the editor is not audible on an air radio — said at the pick, in words."""
    app = build(config_dir, fleet(), plugins=("mission", "music"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page, _token = await _open_tab(http)

    sentence = ("An edit made in the playlist editor does not reach a radio that is already on "
                "air: the radio reads its tracks when the playlist is assigned here again or the "
                "radio is restarted.")
    assert sentence in page
    pick = page.index(f'action="{actions_page.MUSIC_PLAYLIST_PATH}"')
    assert pick < page.index(sentence), "the warning must follow the pick it qualifies"
