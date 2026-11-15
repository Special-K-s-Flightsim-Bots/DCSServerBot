"""The music page's PLAYER, its New Playlist door, and its live updates.

One section per acceptance criterion of the card, every one of them driven through the REAL
application (a request, not a hand-built page render):

* a card per radio of every ACTIVE server — on air AND idle — with the console's own state word, a
  one-sentence claim where nothing is playing, and no card for a radio whose server is down;
* the transport strip posts the SAME write the server's Music tab posts (path and field names),
  offers no pause verb (stop IS the pause), and is absent for a reader without ``music.control``;
* New Playlist lives in the playlists card header and is still gated;
* the two live endpoints are registered under ``music.view``, answer ``text/event-stream`` and JSON,
  open with a snapshot frame carrying both targets, re-emit a fragment only when its rendered HTML
  changed, and refuse a caller without the capability before any generator exists;
* the page passes ``live=`` only while live updates are on, and its two sections ARE the streamed
  fragments — the same partials, from the same context builder.

The domain boundary is ``test_webui_music``'s ``MusicService`` double, registered under the real
service class, so the real action layer runs against a service that answers facts.
"""
from __future__ import annotations

import re

import pytest

from conftest import create_app, install_shell
from core import Status
from core.services.registry import ServiceRegistry
from services.music.service import MusicService
from services.webservice import permissions
from services.webservice.auth import SESSION_IDENTITY_KEY
from services.webservice.pages import actions as actions_page
from services.webservice.pages import live as live_page
from services.webservice.pages import music as music_page

# the write suites' harness (the real app, the local login, the stub cluster) and the music double
from test_webui_music import (  # noqa: F401 - fixtures are imported on purpose
    PLAYLIST, music_registry, music_service, open_music,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures/helpers are imported on purpose
    Cluster, build, client, config_dir, fleet, instance, server as server_double, sign_in,
    token_of,
)

IDLE_RADIO = {"name": "Radio 2", "type": "srs", "frequency": "40.0", "modulation": "AM",
              "coalition": "1", "volume": "0.3", "display_name": "Radio 2", "mode": "shuffle",
              "on_air": False, "playlist": "", "tracks": 0, "current": {}}
#: a server whose DCS process is DOWN — the states ``MISSION_LIST_OFFLINE_STATES`` names, which is
#: what makes it NOT active (a merely ``STOPPED`` server still has DCS up)
OFFLINE_SERVER = "SRS-Q"
OFFLINE_RADIO = {"name": "Radio Q", "type": "srs", "frequency": "50.0", "modulation": "FM",
                 "coalition": "2", "volume": "0.4", "display_name": "Radio Q", "mode": "repeat",
                 "on_air": False, "playlist": "", "tracks": 0, "current": {}}
#: the role the read-only pins grant, and the account that holds it
MEDIA_ROLE = "Media Manager"
MEDIA_USER = "mm1"

#: the PURE MANAGER: no cluster role, a declared scope naming the servers they hold. Host B's radio
#: exists in the same service, so a scope that is not applied would draw it.
HOSTER_ROLE = "Hoster"
HOSTER_USER = "hoster"
HOST_B_RADIO = {"name": "Radio B", "type": "srs", "frequency": "60.0", "modulation": "FM",
                "coalition": "1", "volume": "0.2", "display_name": "Radio B", "mode": "repeat",
                "on_air": True, "playlist": PLAYLIST, "tracks": 1,
                "current": {"title": "Dawn Patrol", "artist": "Brian Eno", "album": "",
                            "file": "ambient/a.ogg", "duration": 200.0}}

_FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S)
_INPUT = re.compile(r"<input\b([^>]*)>")
_ATTR = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
_SELECT = re.compile(r"<select\b([^>]*)>(.*?)</select>", re.S)
_OPTION = re.compile(r'<option value="([^"]*)"')


# ------------------------------------------------------------------------------- small readers

def _forms(html: str) -> list[tuple[dict[str, str], str]]:
    """Every form on *html* as ``(attributes, body)``."""
    return [(dict(_ATTR.findall(match.group(1))), match.group(2))
            for match in _FORM.finditer(html)]


def _fields(body: str) -> dict[str, str]:
    """The hidden inputs a form body carries, as name -> value."""
    fields: dict[str, str] = {}
    for raw in _INPUT.finditer(body):
        attrs = dict(_ATTR.findall(raw.group(1)))
        if attrs.get("name"):
            fields[attrs["name"]] = attrs.get("value", "")
    return fields


def _control_forms(html: str) -> list[dict[str, str]]:
    """The transport forms of a fragment, each one as the fields it posts (path included)."""
    found: list[dict[str, str]] = []
    for attrs, body in _forms(html):
        if attrs.get("action") != actions_page.MUSIC_CONTROL_PATH:
            continue
        found.append(dict(_fields(body), action=attrs["action"]))
    return found


def _squeeze(html: str) -> str:
    """*html* with every run of whitespace collapsed — markup and text, never bytes."""
    return re.sub(r"\s+", " ", html).strip()


def _cards(html: str) -> dict[str, str]:
    """Every radio card of *html* as ``name -> the card's article body``.

    Keyed on the card's OWN name element (``_music_player.html``'s ``.pl-name``), so a pin reads the
    card a viewer would see rather than the presence of a word somewhere in the fragment.
    """
    cards: dict[str, str] = {}
    for chunk in html.split("<article")[1:]:
        body = chunk.split("</article>")[0]
        match = re.search(r'<b class="pl-name">(.*?)</b>', body, re.S)
        if match:
            cards[re.sub(r"\s+", " ", match.group(1)).strip()] = body
    return cards


def _wrapper(page: str, target: str) -> str:
    """The INNER html of one swap wrapper of a rendered page.

    Found by walking the divs: the wrapper is the first ``<div id="<target>">`` and its content
    ends at the matching close, so a nested ``</div>`` cannot fool the read.
    """
    match = re.search(r'<div id="%s">' % re.escape(target), page)
    assert match, f"the page draws no #{target} wrapper"
    body = page[match.end():]
    depth = 1
    for tag in re.finditer(r"<(/?)div\b[^>]*>", body):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return body[:tag.start()]
    raise AssertionError(f"#{target} is not closed")


async def _fragments(http) -> dict:
    """The JSON the polling fallback reads — the two fragments exactly as the stream renders them."""
    response = await http.get(music_page.MUSIC_FRAGMENTS_PATH,
                              headers={"accept": "application/json"})
    assert response.status_code == 200, response.text
    return response.json()


def _override(config_dir, block: str) -> None:
    """Write the access override the real install applies at startup."""
    (config_dir / "services" / "webservice.yaml").write_text(block, encoding="utf-8")


def _users_with_media():
    """The write suites' accounts plus one holding ``Media Manager`` (Host A)."""
    from services.webservice.auth import local as local_auth
    from test_webui_write_pause import PASSWORD, _users

    encoded = local_auth.hash_password(PASSWORD, iterations=local_auth.MIN_ITERATIONS)
    return _users() + [{"username": MEDIA_USER, "roles": [MEDIA_ROLE], "scope": ["Host A"],
                        "password_hash": encoded}]


def _grant_view_to_media(config_dir) -> None:
    """Declare the Media Manager role in the bot's model and grant it ``music.view`` alone.

    ``Admin`` cannot be removed from a capability (the access page refuses it), so a reader who may
    view but not write is made by ADDING a role that holds no music write — the shipped mechanism.
    """
    (config_dir / "services" / "bot.yaml").write_text(
        "roles:\n  Admin:\n    - specialk\n  DCS Admin:\n    - mod1\n  DCS:\n    - member1\n"
        "  Media Manager:\n    - mm1\n", encoding="utf-8")
    _override(config_dir, "DEFAULT:\n  access:\n    music.view:\n      add: [Media Manager]\n")


def _declare_hoster(config_dir) -> None:
    """Declare the pure-manager role in the bot's model and grant it ``music.view`` alone.

    A manager holds no cluster role, so the page is opened by the shipped grant mechanism and the
    servers they see come from their own scope — the two dimensions the console keeps apart.
    """
    (config_dir / "services" / "bot.yaml").write_text(
        "roles:\n  Admin:\n    - specialk\n  DCS Admin:\n    - mod1\n  DCS:\n    - member1\n"
        f"  {HOSTER_ROLE}:\n    - {HOSTER_USER}\n", encoding="utf-8")
    _override(config_dir, "DEFAULT:\n  access:\n    music.view:\n"
                          f"      add: [{HOSTER_ROLE}]\n")


def _hoster_users(scope=("Host A",)):
    """The write suites' accounts plus one PURE MANAGER whose declared scope is *scope*."""
    from services.webservice.auth import local as local_auth
    from test_webui_write_pause import PASSWORD, _users

    encoded = local_auth.hash_password(PASSWORD, iterations=local_auth.MIN_ITERATIONS)
    return [user for user in _users() if user["username"] != HOSTER_USER] + [
        {"username": HOSTER_USER, "roles": [HOSTER_ROLE], "scope": list(scope),
         "password_hash": encoded}]


def _app(config_dir, cluster=None, *, dashboard=None, users=None):
    """A real application around *cluster*, with the music plugin and an optional live block."""
    from test_webui_write_pause import _config, _users

    config = _config(users if users is not None else _users())
    config["dashboard"] = dict(dashboard or {})
    app = create_app()
    install_shell(app, node=type("Node", (), {"master": True, "config_dir": str(config_dir),
                                              "plugins": ["music"]})(),
                  config=config)
    app.state.webui_source_provider = (cluster or fleet()).source
    return app


def _app_without_plugins(config_dir):
    """A real application whose action registry carries nothing (the read-only shell)."""
    from test_webui_write_pause import _config, _users

    app = create_app()
    install_shell(app, node=type("Node", (), {"master": True, "config_dir": str(config_dir),
                                              "plugins": []})(),
                  config=_config(_users()))
    app.state.webui_source_provider = fleet().source
    return app


def offline_fleet():
    """Host A's running server, plus one of Host A's servers whose DCS process is DOWN."""
    running = server_double("SRS-1", ["Host A"])
    down = server_double(OFFLINE_SERVER, ["Host A"], status=Status.SHUTDOWN,
                         instance="Host-Q-Instance")
    return _fleet_of(running, down)


def all_down_fleet():
    """Both of Host A's servers in a DOWN state — no card can be drawn for either."""
    return _fleet_of(server_double("SRS-1", ["Host A"], status=Status.SHUTDOWN),
                     server_double(OFFLINE_SERVER, ["Host A"], status=Status.UNREGISTERED,
                                   instance="Host-Q-Instance"))


def _fleet_of(*servers):
    """A cluster of Host A's servers, with the node and instance rows the read models expect."""
    first = servers[0]
    node_a = type(first.node)(name="dcs-a-01", master=True,
                              instances={"Host-A-Instance": instance("Host-A-Instance", first)})
    return Cluster(list(servers), nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", first),
                              instance("Host-Q-Instance", servers[-1])])


# ------------------------------------------------- 1. every radio of every ACTIVE server

@pytest.mark.asyncio
async def test_an_idle_radio_of_a_running_server_is_drawn_with_its_state_word(
        config_dir, music_registry, music_service):
    """The card is drawn for an IDLE radio too, carrying the console's idle word."""
    music_service.radios["SRS-1"].append(dict(IDLE_RADIO))
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_music(http)
        fragments = await _fragments(http)

    radios = fragments[music_page.RADIOS_TARGET]
    assert "Radio 2" in radios, "an idle radio of a running server gets a card"
    assert "Radio 1" in radios, "the on-air radio keeps its card"
    idle_card = radios[radios.index("Radio 2"):].split("</article>")[0]
    assert music_page.IDLE_WORD in idle_card, "the state tag carries the idle word"
    assert music_page.NO_CURRENT_TRACK in idle_card, \
        "a radio with nothing playing says so in one sentence"
    air_card = radios[:radios.index("Radio 2")]
    assert music_page.ON_AIR_WORD in air_card, "the on-air card keeps the console's own word"
    assert "Radio 2" in _wrapper(page, music_page.RADIOS_TARGET)


@pytest.mark.asyncio
async def test_a_radio_of_a_server_whose_dcs_process_is_down_is_not_drawn(
        config_dir, music_registry, music_service):
    """The radios exist, but their server is down: no card, and no transport naming it."""
    music_service.radios[OFFLINE_SERVER] = [dict(OFFLINE_RADIO)]
    app = _app(config_dir, offline_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        fragments = await _fragments(http)

    radios = fragments[music_page.RADIOS_TARGET]
    assert "Radio 1" in radios, "the up server's radio is drawn"
    assert "Radio Q" not in radios, "a down server's radio gets no card"
    assert not [fields for fields in _control_forms(radios)
                if fields.get(actions_page.MUSIC_RADIO_FIELD) == OFFLINE_RADIO["name"]]


# --------------------------------------------------------- 2. the transport of one radio card

@pytest.mark.asyncio
async def test_the_section_says_why_no_card_is_drawn_when_no_server_is_up(
        config_dir, music_registry, music_service):
    """Every visible server is down: the radios exist and the section states the reason."""
    music_service.radios["SRS-1"] = []
    music_service.radios[OFFLINE_SERVER] = [dict(OFFLINE_RADIO)]
    app = _app(config_dir, all_down_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        fragments = await _fragments(http)

    radios = fragments[music_page.RADIOS_TARGET]
    assert "Radio 1" not in radios
    assert "Radio Q" not in radios
    assert music_page.NO_RADIO_SERVER in radios, "an empty section states why, not just nothing"
    assert music_page.NO_RADIOS not in radios, "the servers ARE configured — that is not the reason"


@pytest.mark.asyncio
async def test_a_manager_whose_scope_holds_no_server_is_told_that_not_that_none_is_running(
        config_dir, music_registry, music_service):
    """An empty view BECAUSE OF THE SCOPE gets its own sentence; a down fleet keeps the other one."""
    music_service.radios["SRS-B"] = [dict(HOST_B_RADIO)]
    _declare_hoster(config_dir)
    app = _app(config_dir, fleet(), users=_hoster_users(scope=("Host Z",)))
    async with client(app) as http:
        await sign_in(http, HOSTER_USER)
        page = await open_music(http)
        fragments = await _fragments(http)

    radios = fragments[music_page.RADIOS_TARGET]
    assert music_page.NO_SERVER_IN_SCOPE in radios, "the scope is the reason and it is stated"
    assert music_page.NO_RADIO_SERVER not in radios, "no server is running is not this viewer's fact"
    assert music_page.NO_RADIOS not in radios, "radios ARE configured on this installation"
    assert music_page.NO_SERVER_IN_SCOPE in page, "the page and the streamed frame say one thing"

    down = _app(config_dir, all_down_fleet())
    async with client(down) as http:
        await sign_in(http, "mod1")
        served = (await _fragments(http))[music_page.RADIOS_TARGET]

    assert music_page.NO_RADIO_SERVER in served, "servers held, none running: the other sentence"
    assert music_page.NO_SERVER_IN_SCOPE not in served


@pytest.mark.asyncio
async def test_the_card_posts_the_music_tab_s_own_control_write(config_dir, music_registry,
                                                                music_service):
    """Play, Stop, Skip and the mode pick post the tab's path with the tab's field names."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_music(http)
        fragments = await _fragments(http)

    radios = fragments[music_page.RADIOS_TARGET]
    forms = _control_forms(radios)
    assert len(forms) == 4, forms                       # play, stop, skip, mode
    assert [fields[actions_page.MUSIC_CONTROL_FIELD] for fields in forms] == \
        ["play", "stop", "skip", "mode"]
    for fields in forms:
        assert fields[actions_page.SERVER_CONFIG_FIELD] == "SRS-1"
        assert fields[actions_page.MUSIC_RADIO_FIELD] == "Radio 1"
        assert fields["_csrf_token"], "every write carries the session's token"
    # THE MODE IS ONE TOGGLE, NOT A PICK: the button posts the mode it will switch TO, in the body,
    # so the form states the other of the two words the action accepts — never a select
    mode_form = [fields for fields in forms if fields[actions_page.MUSIC_CONTROL_FIELD] == "mode"]
    assert len(mode_form) == 1, mode_form
    assert mode_form[0][actions_page.MUSIC_MODE_FIELD] in ("repeat", "shuffle"), mode_form
    assert not _SELECT.search(radios), "the mode is a toggle, not a pick"
    # the page carries the same strip: one render path, so the card cannot differ
    assert _squeeze(radios) in _squeeze(_wrapper(page, music_page.RADIOS_TARGET))


@pytest.mark.asyncio
async def test_no_pause_verb_is_offered_anywhere(config_dir, music_registry, music_service):
    """``control_radio`` accepts play/stop/skip/mode: stop IS the pause, so no pause exists."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_music(http)
        fragments = await _fragments(http)

    radios = fragments[music_page.RADIOS_TARGET]
    assert 'value="pause"' not in radios
    assert "Pause" not in radios
    for fields in _control_forms(radios):
        assert fields[actions_page.MUSIC_CONTROL_FIELD] in ("play", "stop", "skip", "mode")
    assert "Pause" not in _wrapper(page, music_page.RADIOS_TARGET)


@pytest.mark.asyncio
async def test_a_reader_without_the_control_capability_gets_the_card_and_no_controls(
        config_dir, music_registry, music_service):
    """Omitted, never drawn disabled: the card is there, the transport is absent.

    ``music.control`` carries the console's manager scope grant, so a scoped DCS Admin is a
    controller of their own servers by design. The reader here is an UNSCOPED DCS Admin after the
    access page has taken the role off that capability: they read every server's radios (the view
    capability is untouched) and drive none of them.
    """
    from services.webservice.auth import local as local_auth
    from test_webui_write_pause import PASSWORD, _users

    encoded = local_auth.hash_password(PASSWORD, iterations=local_auth.MIN_ITERATIONS)
    users = _users() + [{"username": "mod2", "roles": ["DCS Admin"], "password_hash": encoded}]
    _override(config_dir, "DEFAULT:\n  access:\n    music.control:\n"
                          "      remove: [DCS Admin]\n")
    app = _app(config_dir, users=users)
    async with client(app) as http:
        await sign_in(http, "mod2")
        page = await open_music(http)
        fragments = await _fragments(http)

    radios = fragments[music_page.RADIOS_TARGET]
    assert "Radio 1" in radios, "the card is still drawn"
    assert "Paint It Black" in radios
    assert actions_page.MUSIC_CONTROL_PATH not in radios, "no transport for a reader"
    assert actions_page.MUSIC_CONTROL_PATH not in page
    assert "disabled" not in radios, "a control withheld is absent, never drawn disabled"


# ------------------------------------- 2b. the card's POST reaches the action it names

@pytest.mark.asyncio
async def test_the_card_s_own_form_reaches_the_control_action(config_dir, music_registry,
                                                             music_service):
    """The rendered strip's fields, posted with its own token, run ``control_radio`` on that radio.

    The markup pins above prove the forms are DRAWN; this one proves the route they post to reaches
    the action with the verb and the target the card stated — the wiring no markup assertion covers.
    """
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        fragments = await _fragments(http)
        stop = [fields for fields in _control_forms(fragments[music_page.RADIOS_TARGET])
                if fields.get(actions_page.MUSIC_CONTROL_FIELD) == "stop"]
        assert len(stop) == 1, stop
        posted = dict(stop[0])
        path = posted.pop("action")
        assert posted[actions_page.SERVER_CONFIG_FIELD] == "SRS-1"
        assert posted[actions_page.MUSIC_RADIO_FIELD] == "Radio 1"
        reply = await http.post(path, data=posted)

    assert reply.status_code == 303, reply.text
    assert ("stop", ("SRS-1", "Radio 1")) in music_service.calls, music_service.calls


# ------------------------------------- 2c. the scoped manager, on all three doors

@pytest.mark.asyncio
async def test_a_scoped_manager_draws_their_radio_on_the_page_and_the_fragments_and_is_refused_the_other(
        config_dir, music_registry, music_service):
    """Host A's manager: A's card on the page and in the polling fallback, B refused by the write.

    One test, three doors: the rendered page, ``/api/music/fragments`` (the frame the stream sends, so
    a streamed tick cannot show what the page did not) and the control POST. Host B has a radio
    configured in the same service, so a page that skipped the scope would draw it.
    """
    music_service.radios["SRS-B"] = [dict(HOST_B_RADIO)]
    _declare_hoster(config_dir)
    app = _app(config_dir, fleet(), users=_hoster_users())
    async with client(app) as http:
        await sign_in(http, HOSTER_USER)
        page = await open_music(http)
        fragments = await _fragments(http)
        radios = fragments[music_page.RADIOS_TARGET]

        # 1. the page: ONE card, named for the radio, posting A's server — B appears nowhere
        assert set(_cards(radios)) == {"Radio 1"}, _cards(radios)
        card = _cards(radios)["Radio 1"]
        assert actions_page.MUSIC_CONTROL_PATH in card
        stated = {fields[actions_page.SERVER_CONFIG_FIELD] for fields in _control_forms(card)}
        assert stated == {"SRS-1"}, stated
        assert set(_cards(_wrapper(page, music_page.RADIOS_TARGET))) == {"Radio 1"}
        for name in ("Radio B", "SRS-B"):
            assert name not in page, f"{name} leaked onto the page"
            assert name not in radios, f"{name} leaked into the streamed frame"

        # 3. the write: the SAME caller's control POST for B's radio is refused, and writes nothing
        token = _control_forms(radios)[0]["_csrf_token"]
        refused = await http.post(actions_page.MUSIC_CONTROL_PATH,
                                  data={"_csrf_token": token,
                                        actions_page.SERVER_CONFIG_FIELD: "SRS-B",
                                        actions_page.MUSIC_RADIO_FIELD: "Radio B",
                                        actions_page.MUSIC_CONTROL_FIELD: "play"})
        assert refused.status_code == 403, refused.text
        assert "outside your scope" in refused.text, refused.text

        # the positive control: the caller's OWN radio does go through, so "refused" is the scope
        own = dict(_control_forms(radios)[1])
        own_path = own.pop("action")
        assert own[actions_page.MUSIC_CONTROL_FIELD] == "stop"
        allowed = await http.post(own_path, data=own)
        assert allowed.status_code == 303, allowed.text

    assert ("stop", ("SRS-1", "Radio 1")) in music_service.calls, music_service.calls
    assert not [call for call in music_service.calls if call[1][0] == "SRS-B"], \
        "the refused write reached the service"

    # the same target, taken straight to the action layer with this caller's own context: its OWN
    # sentence, so the refusal does not rest on the route alone
    from core.actions import call_action

    context = actions_page.context_for(_authenticated_request(app, subject=HOSTER_USER))
    answer = await call_action("control_radio", context, server_name="SRS-B",
                               radio_name="Radio B", control="play")
    assert answer.success is False
    assert answer.message == "Not authorized: server 'SRS-B' is outside your scope.", answer.message


# -------------------------------------------------------------- 3. New Playlist in the table

@pytest.mark.asyncio
async def test_new_playlist_lives_in_the_playlists_card_header(config_dir, music_registry,
                                                               music_service):
    """The door moved into the card's header band, with the same POST and the same token."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_music(http)

    playlists = _wrapper(page, music_page.PLAYLISTS_TARGET)
    header = re.search(r'<div class="hd">(.*?)</div>', playlists, re.S)
    assert header, "the playlists card draws its header band"
    band = header.group(1)
    assert music_page.PLAYLISTS_HEADING in band
    assert '<span class="spacer"></span>' in band
    assert f'action="{music_page.NEW_DIALOG_PATH}"' in band, \
        "the door is INSIDE the header band"
    assert 'class="page-actions"' not in page, "the page-level action band is gone"
    assert token_of(page), "the door still posts the session's token"


@pytest.mark.asyncio
async def test_new_playlist_is_still_gated_by_the_write_capability(config_dir, music_registry,
                                                                   music_service):
    """A caller who may read but not write gets the table and no New Playlist control."""
    _grant_view_to_media(config_dir)
    app = _app(config_dir, users=_users_with_media())
    async with client(app) as http:
        await sign_in(http, MEDIA_USER)
        page = await open_music(http)

    assert PLAYLIST in _wrapper(page, music_page.PLAYLISTS_TARGET), "the table is still drawn"
    assert music_page.NEW_DIALOG_PATH not in page
    assert music_page.dialog_url(PLAYLIST, "delete") not in page


# ------------------------------------------------------------------------- 4. the live plumbing

def test_the_two_live_paths_are_gated_with_the_page_s_capability(config_dir):
    """Registered under ``music.view``, not public, and declared like every other route."""
    app = _app_without_plugins(config_dir)
    registrar = app.state.webui_registrar
    for path in (music_page.MUSIC_STREAM_PATH, music_page.MUSIC_FRAGMENTS_PATH):
        assert registrar.capability_for(path=path) == music_page.CAPABILITY_VIEW, path
        assert registrar.capability_for(path=path) != permissions.PUBLIC, path
    registered = {getattr(route, "path", None) for route in registrar.routes}
    assert {music_page.MUSIC_STREAM_PATH, music_page.MUSIC_FRAGMENTS_PATH} <= registered
    assert permissions.undeclared_routes(app, registrar) == []


@pytest.mark.asyncio
async def test_the_stream_answers_text_event_stream_starting_with_a_snapshot(
        config_dir, music_registry, music_service):
    """Over the real app: the content type, and a first snapshot frame carrying BOTH targets.

    The stream is made finite by filling the configured single client slot — the documented cap
    behaviour: a client over the cap still receives its snapshot, and then the stream closes.
    """
    app = _app(config_dir, dashboard={"stream_clients": 1})
    registry = live_page.get_registry(app, live_page.dashboard_config(app))
    assert registry.try_acquire() is True, "the only slot is taken before the request"
    try:
        async with client(app) as http:
            await sign_in(http, "mod1")
            response = await http.get(music_page.MUSIC_STREAM_PATH,
                                      headers={"accept": "text/event-stream"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.text
        assert body.startswith("event: snapshot\n"), body[:120]
        assert f'"{music_page.RADIOS_TARGET}"' in body
        assert f'"{music_page.PLAYLISTS_TARGET}"' in body
        assert body.endswith("\n\n"), "SSE frames are terminated by a blank line"
        assert "event: state" not in body, "an over-cap client gets its snapshot and nothing more"
    finally:
        registry.release()


@pytest.mark.asyncio
async def test_the_fragments_endpoint_serves_both_sections_as_json(config_dir, music_registry,
                                                                   music_service):
    """The polling fallback reads this: the same two fragments, as JSON."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        payload = await _fragments(http)

    assert set(payload) == {music_page.RADIOS_TARGET, music_page.PLAYLISTS_TARGET}
    assert "Radio 1" in payload[music_page.RADIOS_TARGET]
    assert PLAYLIST in payload[music_page.PLAYLISTS_TARGET]
    assert f'id="{music_page.RADIOS_TARGET}"' not in payload[music_page.RADIOS_TARGET], \
        "a fragment is the partial alone: the wrapper owns the id"


@pytest.mark.asyncio
async def test_a_fragment_is_re_sent_only_when_its_html_changed(config_dir, music_registry,
                                                                music_service):
    """A tick that renders the same cards sends nothing; a changed track sends one frame."""
    app = _app(config_dir)
    frames = live_page.stream_frames(
        render=_renderer(app), config=music_page.music_live_config(app),
        registry=live_page.LiveRegistry(4),
        sleep=_scripted_sleep([lambda: None,
                               _new_track,          # the track changed -> one frame
                               lambda: None]),      # nothing changed -> nothing
        monotonic=lambda: 0.0)                      # no heartbeat can land in two ticks

    out = await _drive(frames)
    assert out[0].startswith("event: snapshot")
    assert len(out) == 2, out
    assert out[1].startswith("event: state")
    assert "Welcome to the Jungle" in out[1]
    assert "Paint It Black" not in out[1], "only the targets that changed are on the wire"


@pytest.mark.asyncio
async def test_the_music_cadence_is_its_own_and_the_switch_is_the_console_s(config_dir):
    """~3 s sampling, decided where the console decides whether it streams at all."""
    enabled = music_page.music_live_config(_app(config_dir))
    assert enabled.enabled is True
    assert enabled.refresh_seconds == music_page.MUSIC_REFRESH_SECONDS == 3
    off = music_page.music_live_config(_app(config_dir, dashboard={"live": False}))
    assert off.enabled is False


@pytest.mark.asyncio
async def test_the_page_passes_live_only_while_live_updates_are_on(config_dir, music_registry,
                                                                    music_service):
    """Live on: the shared script and both endpoints. Live off: a page complete on load."""
    on = _app(config_dir)
    async with client(on) as http:
        await sign_in(http, "mod1")
        live_html = await open_music(http)

    assert "/static/shell.js" in live_html
    assert f'data-stream="{music_page.MUSIC_STREAM_PATH}"' in live_html
    assert f'data-fragments="{music_page.MUSIC_FRAGMENTS_PATH}"' in live_html

    off = _app(config_dir, dashboard={"live": False})
    async with client(off) as http:
        await sign_in(http, "mod1")
        still = await open_music(http)

    assert "/static/shell.js" not in still, "dashboard.live: false must open no stream"
    assert "Radio 1" in still, "the page is still complete without it"


@pytest.mark.asyncio
async def test_the_page_draws_the_very_partials_the_stream_renders(config_dir, music_registry,
                                                                   music_service):
    """One render path: each section of the page IS the streamed fragment, to the character."""
    music_service.radios["SRS-1"].append(dict(IDLE_RADIO))
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_music(http)
        fragments = await _fragments(http)

    for target in (music_page.RADIOS_TARGET, music_page.PLAYLISTS_TARGET):
        assert _wrapper(page, target) == fragments[target], target


@pytest.mark.asyncio
async def test_a_caller_without_music_view_is_refused_before_any_stream(config_dir,
                                                                      music_registry,
                                                                      music_service):
    """An EventSource is answered with the API's 403 — never a page, never a broken stream."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "plain")                     # DCS → no music capability
        for path in (music_page.MUSIC_STREAM_PATH, music_page.MUSIC_FRAGMENTS_PATH):
            response = await http.get(path, headers={"accept": "text/event-stream"})
            assert response.status_code == 403, path
            assert response.headers.get("location") is None
            assert "text/html" not in response.headers.get("content-type", ""), path


def test_the_generator_itself_refuses_a_caller_without_music_view(config_dir):
    """The refusal happens BEFORE a generator exists — a refusal inside the body arrives too late.

    ``event_stream`` raises instead of returning a stream for a request whose identity holds no
    ``music.view``, and returns one for a caller that holds it (the positive control, without which
    "it refused" could just mean the function always raises).
    """
    from fastapi import HTTPException

    app = _app(config_dir)
    with pytest.raises(HTTPException) as raised:
        music_page.event_stream(_authenticated_request(app, subject="plain"))
    assert raised.value.status_code == 403
    assert raised.value.detail == ("Not authorized (needs one of: "
                                   f"{permissions.requirement_text(music_page.CAPABILITY_VIEW)})."), \
        "the stream's refusal says exactly what the access gate says"

    stream = music_page.event_stream(_authenticated_request(app, subject="mod1"))
    assert hasattr(stream, "__anext__"), "a permitted caller receives a real generator"


# -------------------------------------------------------------------------------- the harness

def _authenticated_request(app, path: str = music_page.MUSIC_STREAM_PATH, *, subject: str = "mod1"):
    """A Request carrying the local backend's identity reference — what a session holds."""
    from starlette.requests import Request

    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "app": app,
        "session": {SESSION_IDENTITY_KEY: {"backend": "local", "subject": subject}},
        "headers": [(b"accept", b"application/json")],
        "client": ("127.0.0.1", 51234), "server": ("testserver", 443),
    }
    return Request(scope)


def _renderer(app):
    """The production fragment renderer, on a request signed in as ``mod1``."""
    request = _authenticated_request(app)

    def render():
        return music_page.current_fragments(request)

    return render


def _new_track():
    """Move the service's current track — the change a live frame must carry."""
    radios = ServiceRegistry._singletons[MusicService].radios["SRS-1"]
    radios[0]["current"] = {"title": "Welcome to the Jungle", "artist": "Guns N' Roses",
                            "album": "", "file": "jungle/a.mp3", "duration": 241.0}


class _StopTicks(Exception):
    """Raised by a scripted sleep to end the stream after N ticks, with no timing involved."""


def _scripted_sleep(actions):
    """A ``sleep`` whose i-th call runs ``actions[i]`` and then raises after the last one."""
    state = {"n": 0}

    async def sleep(_seconds):
        n = state["n"]
        if n >= len(actions):
            raise _StopTicks()
        state["n"] += 1
        actions[n]()

    return sleep


async def _drive(frames) -> list[str]:
    """Every frame a generator yields, stopping at the scripted end of the ticks."""
    out: list[str] = []
    try:
        while True:
            out.append(await anext(frames))
    except (_StopTicks, StopAsyncIteration):
        pass
    return out
