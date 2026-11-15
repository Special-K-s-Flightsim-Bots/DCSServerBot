"""The Access matrix: capability → role overrides, edited in the console (WEBSITE only).

One section per acceptance criterion of the card, each driven THROUGH the real application — a
request, not a template render — so the answer comes from the gate, the registry and the file, never
from a second reading of the same table:

* the block EXTENDS (``add``) and NARROWS (``remove``) a declaration, and the effective matrix is what
  ``permissions.allows`` — the gate's own predicate — reports;
* ``Admin`` cannot be removed, a ``managed_by`` grant cannot be moved on a machine- or
  installation-wide capability, an unknown capability or role is refused, and a save that would leave
  no role able to open the page is refused: four sentences, all on the page's one-shot notice;
* a save writes the ``access:`` block and NOTHING else — the rest of the file comes out byte-identical,
  comments included;
* after an override the sidebar and the route agree, and the bot's Discord decorators are untouched;
* the matrix's own data (groups, descriptions, the deny-list) is complete, so a capability added later
  cannot be silently missing from the page.

The harness (local-auth sign-in, CSRF token read out of a rendered form, the stub cluster) is the
write suites' own, imported rather than re-implemented.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from conftest import KEEPS_PROCESS_STATE
from services.webservice import permissions
from services.webservice.pages import access as access_page
from services.webservice.registry import NAV_GROUPS_BY_NAME

# the write suites' fixtures and helpers, reused so these pages run through the REAL app
from test_webui_write_pause import (  # noqa: F401 - fixtures/helpers are imported on purpose
    PASSWORD, build, client, fleet, sign_in, token_of,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
  Media Manager:
    - mm1
"""
USERS = (
    {"username": "specialk", "roles": ["Admin"]},
    {"username": "mod1", "roles": ["DCS Admin"], "scope": ["Host A"]},
    {"username": "mm1", "roles": ["Media Manager"], "scope": ["Host A"]},
)


def users():
    """The three accounts as the local backend needs them: each one carries a REAL hash."""
    from services.webservice.auth import local as local_auth

    encoded = local_auth.hash_password(PASSWORD, iterations=local_auth.MIN_ITERATIONS)
    return [dict(user, password_hash=encoded) for user in USERS]
MEDIA = "Media Manager"
CONFIG = "services/webservice.yaml"


@pytest.fixture(autouse=True)
def node_name_for_the_validator():
    """Give the bot's own config validator a node name for the duration of a test.

    A save is validated through ``core.utils.validators`` — the very call the bot's startup makes —
    and its ``unique_port`` rule reads the node name off the parsed command line. A test process never
    parsed one, so the attribute is set here (and removed again) rather than the save path skipping
    the validation it exists for.
    """
    from core.utils import validators

    args = getattr(validators, "COMMAND_LINE_ARGS", None)
    had = args is not None and hasattr(args, "node")
    if args is not None and not had:
        args.node = "TestNode"
    try:
        yield
    finally:
        if args is not None and not had:
            try:
                del args.node
            except AttributeError:      # pragma: no cover - already gone
                pass


@pytest.fixture
def config_dir(tmp_path):
    """A config tree with a role model that carries a CUSTOM role (``Media Manager``)."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(config_dir):
    """A real application built around *config_dir*."""
    return build(config_dir, fleet(), users=users())


def write_config(config_dir, text: str) -> Path:
    path = config_dir / CONFIG
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------- the form, as a browser sends it

#: every ``<input>`` on the page, as its attribute map — the page's form read the way a browser
#: submits it (a checkbox travels only when it is CHECKED, a hidden value always)
_INPUT = re.compile(r"<input\b([^>]*)>")
_ATTR = re.compile(r'([a-zA-Z-]+)="([^"]*)"')


def _inputs(html: str) -> list[tuple[str, dict[str, str]]]:
    """``(raw tag, attributes)`` for every ``<input>`` — the raw text is what carries ``checked``."""
    return [(match.group(1), dict(_ATTR.findall(match.group(1))))
            for match in _INPUT.finditer(html)]


def form_of(html: str) -> dict[str, list[str]]:
    """The form a browser would POST for this rendered page, plus the CSRF token and the revision."""
    form: dict[str, list[str]] = {"admin": [], "cell": [], "manager": [],
                                  access_page.FIELD_REVISION: [],
                                  "_csrf_token": [token_of(html)]}
    for raw, attributes in _inputs(html):
        name = attributes.get("name", "")
        if name not in form or "value" not in attributes:
            continue
        if attributes.get("type") == "checkbox" and not re.search(r"\bchecked\b", raw):
            continue
        form[name].append(attributes["value"])
    return form


async def open_page(http, path="/access"):
    response = await http.get(path)
    assert response.status_code == 200, response.text[:400]
    return response


async def save(http, form: dict[str, list[str]]):
    """Post the whole matrix — one field per cell, repeated, exactly as a browser sends a form."""
    return await http.post(access_page.SAVE_PATH, data=form)


def notice_of(html: str) -> str:
    match = re.search(r'<span class="notice-msg">(.*?)</span>', html, re.S)
    return match.group(1) if match else ""


# --------------------------------------------------------------------------------- the engine (unit)

def test_add_extends_the_declared_roles(app):
    """``add`` grants a role ON TOP of the declaration — and the gate's own predicate says so."""
    before = permissions.roles_for_capability("music.write")
    assert MEDIA not in before
    candidate, refusals = permissions.candidate_from_entries(
        {"music.write": {"add": [MEDIA]}}, roles_known={"Admin", "DCS Admin", MEDIA})
    assert refusals == []
    permissions.apply_role_overrides(candidate)
    assert MEDIA in permissions.roles_for_capability("music.write")
    assert permissions.allows("music.write", {MEDIA}) is True
    # the declaration itself is untouched: the base is what a later apply recomputes from
    assert permissions.base_declaration("music.write")[0] == before


def test_remove_narrows_the_declared_roles(app):
    """``remove`` takes a declared role away on the website — the point of the page."""
    candidate, refusals = permissions.candidate_from_entries(
        {"music.write": {"remove": ["DCS Admin"]}}, roles_known={"Admin", "DCS Admin", MEDIA})
    assert refusals == []
    permissions.apply_role_overrides(candidate)
    assert permissions.allows("music.write", {"DCS Admin"}) is False
    assert permissions.allows("music.write", {"Admin"}) is True
    assert permissions.base_declaration("music.write")[0] == ("Admin", "DCS Admin")


def test_applying_the_block_twice_does_not_stack(app):
    """A second apply recomputes from the BASE, so the first application cannot stack on itself."""
    entries = {"music.write": {"add": [MEDIA], "remove": ["DCS Admin"]}}
    for _ in range(3):
        candidate, refusals = permissions.candidate_from_entries(
            entries, roles_known={"Admin", "DCS Admin", MEDIA})
        assert refusals == []
        permissions.apply_role_overrides(candidate)
    assert permissions.roles_for_capability("music.write") == ("Admin", MEDIA)


def test_a_manager_grant_on_a_deny_listed_capability_is_refused(app):
    """Every name of the structural list is a refusal, whatever the capability's family."""
    for capability in ("logs.view", "statistics.view", "nodes.view", "nodes.settings",
                       "settings.view", "settings.file.write", "presets.write", "mods.manage",
                       "servers.add", "servers.delete", "servers.config.channels",
                       "access.view", "access.write"):
        assert permissions.manager_scope_allowed(capability) is False, capability
        # the EDIT is what is refused, whichever way it points: a capability whose declaration already
        # grants the scope is refused just as firmly as one that does not
        base = permissions.base_declaration(capability) or ((), False)
        candidate, _ = permissions.candidate_from_entries(
            {capability: {"manager": not base[1]}}, roles_known={"Admin", "DCS Admin"})
        kinds = [refusal.kind for refusal in
                 permissions.validate_candidate(candidate, roles_known={"Admin", "DCS Admin"},
                                                locked=access_page.STRUCTURAL,
                                                page=access_page.PAGE_CAPABILITY)]
        assert permissions.MANAGER_LOCKED in kinds or permissions.STRUCTURAL in kinds, capability
    # and a server-shaped capability still accepts one
    assert permissions.manager_scope_allowed("servers.restart") is True
    assert permissions.manager_scope_allowed("music.view") is True


# -------------------------------------------------------------------------------- the loader (startup)

def test_the_loader_applies_the_block(config_dir):
    """A stored block lands on the registry at startup, before the first request."""
    write_config(config_dir, "DEFAULT:\n  access:\n    music.write:\n      add: [Media Manager]\n")
    app = build(config_dir, fleet(), users=users())
    assert MEDIA in permissions.roles_for_capability("music.write")
    assert app.state.webui_registrar is not None


def test_the_loader_refuses_admin_removal(config_dir, caplog):
    """``remove: [Admin]`` is refused, logged, and the whole block is ignored."""
    write_config(config_dir, "DEFAULT:\n  access:\n    music.control:\n      remove: [Admin]\n")
    with caplog.at_level("ERROR"):
        build(config_dir, fleet(), users=users())
    assert "Admin cannot be removed" in caplog.text
    assert "Admin" in permissions.roles_for_capability("music.control")
    assert permissions.APPLIED_OVERRIDES == {}


def test_the_loader_refuses_an_unknown_capability_and_role(config_dir, caplog):
    """A typo is refused loudly and applied nowhere — it can never silently grant nothing."""
    write_config(config_dir,
                 "DEFAULT:\n  access:\n    music.nope:\n      add: [Media Manager]\n"
                 "    music.view:\n      add: [Media Managr]\n")
    with caplog.at_level("ERROR"):
        build(config_dir, fleet(), users=users())
    assert "music.nope is not a capability" in caplog.text
    assert "Media Managr is not a role" in caplog.text
    assert MEDIA not in permissions.roles_for_capability("music.view")


def test_a_visit_does_not_create_the_config_file(config_dir):
    """Opening the page writes nothing at all — the file appears only on a save."""
    import asyncio

    async def go():
        async with client(build(config_dir, fleet(), users=users())) as http:
            await sign_in(http, username="specialk")
            await open_page(http)

    asyncio.run(go())
    assert not (config_dir / CONFIG).exists()


# ------------------------------------------------------- a part of the block that was never read

def test_a_non_mapping_access_block_is_refused_loudly(config_dir, caplog):
    """``access: "junk"`` is NOT an absent block: the loader says so instead of booting on defaults.

    A hand-edit that says ``access:`` and then carries something this page cannot read is the
    operator's intent; showing the shipped matrix with no line about the file is the failure
    (an operator cannot tell "applied nothing" from "there was nothing to apply").
    """
    write_config(config_dir, "DEFAULT:\n  access: junk\n")
    with caplog.at_level("ERROR"):
        build(config_dir, fleet(), users=users())
    assert "'access' is not a mapping" in caplog.text, caplog.text
    assert permissions.APPLIED_OVERRIDES == {}
    assert permissions.roles_for_capability("music.write") == ("Admin", "DCS Admin")


def test_a_non_mapping_entry_is_skipped_and_the_rest_still_applies(config_dir, caplog):
    """One malformed entry does not discard the entries beside it, and it names the key it dropped."""
    write_config(config_dir, "DEFAULT:\n  access:\n    music.view: notamap\n"
                             "    music.write:\n      add: [Media Manager]\n")
    with caplog.at_level("ERROR"):
        build(config_dir, fleet(), users=users())
    assert "the entry for 'music.view' is not a mapping" in caplog.text, caplog.text
    assert MEDIA in permissions.roles_for_capability("music.write")
    assert permissions.roles_for_capability("music.view") == ("Admin", "DCS Admin")


def test_an_unknown_key_inside_an_entry_is_refused_loudly(config_dir, caplog):
    """An unknown KEY is refused where an unknown NAME already was — naming the key, keeping the rest."""
    write_config(config_dir, "DEFAULT:\n  access:\n    music.write:\n      add: [Media Manager]\n"
                             "      bogus: true\n")
    with caplog.at_level("ERROR"):
        build(config_dir, fleet(), users=users())
    assert "'bogus' is not a key this page reads" in caplog.text, caplog.text
    assert MEDIA in permissions.roles_for_capability("music.write")


def test_an_unreadable_value_inside_an_entry_is_refused_loudly(config_dir, caplog):
    """``add: 5`` names nothing; it is refused by name rather than silently granting nothing."""
    write_config(config_dir, "DEFAULT:\n  access:\n    music.write:\n      add: 5\n")
    with caplog.at_level("ERROR"):
        build(config_dir, fleet(), users=users())
    assert "'add' does not carry a readable value" in caplog.text, caplog.text
    assert permissions.roles_for_capability("music.write") == ("Admin", "DCS Admin")


def test_the_page_names_the_parts_of_the_block_it_skipped(config_dir):
    """The console itself says which keys were skipped — not only the startup log."""
    import asyncio

    write_config(config_dir, "DEFAULT:\n  access:\n    music.view: notamap\n")

    async def go():
        async with client(build(config_dir, fleet(), users=users())) as http:
            await sign_in(http, username="specialk")
            return (await open_page(http)).text

    html = asyncio.run(go())
    assert "the entry for &#39;music.view&#39; is not a mapping" in html, html[:2000]


# ------------------------------------------------- a SECOND install in ONE process (stop()/start())

#: the five capability tables, in the order a caller has to restore them
_CAPABILITY_TABLES = ("CAPABILITIES", "SCOPE_GRANTS", "BASE_CAPABILITIES", "BASE_SCOPE_GRANTS",
                      "APPLIED_OVERRIDES")


def _saved_tables() -> dict:
    return {name: dict(getattr(permissions, name)) for name in _CAPABILITY_TABLES}


def _restore_tables(saved: dict) -> None:
    for name, values in saved.items():
        table = getattr(permissions, name)
        table.clear()
        table.update(values)


@pytest.mark.keeps_process_state
def test_a_second_app_in_one_process_applies_the_stored_block(config_dir):
    """The production stop()/start() path: a stored override must not crash the second install.

    ``WebService.start()`` builds a NEW application in the SAME process after ``stop()`` dropped the
    old one (the master/agent switch). ``permissions.CAPABILITIES`` is process state and still holds
    the first install's APPLIED override when the second one re-declares the same base, so without
    restoring the declarations first the re-declaration raises and the WebService never starts.

    This test deliberately keeps the process state the first install leaves behind (the suite's
    ``clean_capability_state`` fixture would otherwise be the only thing defining what a rebuild
    sees), and restores it itself afterwards.
    """
    saved = _saved_tables()
    write_config(config_dir, "DEFAULT:\n  access:\n    music.write:\n      add: [Media Manager]\n")
    try:
        build(config_dir, fleet(), users=users())
        assert permissions.APPLIED_OVERRIDES["music.write"] == (
            ("Admin", "DCS Admin", MEDIA), False)

        second = build(config_dir, fleet(), users=users())     # the rebuild, in this process
        assert second.state.webui_registrar is not None
        # and the block is APPLIED to the rebuilt app, not merely survived
        assert permissions.roles_for_capability("music.write") == ("Admin", "DCS Admin", MEDIA)
        assert permissions.APPLIED_OVERRIDES["music.write"] == (
            ("Admin", "DCS Admin", MEDIA), False)
        assert second.state.webui_registrar.capability_for(
            path=access_page.ACCESS_PATH, route=None) == access_page.CAPABILITY_VIEW
    finally:
        _restore_tables(saved)


@pytest.mark.keeps_process_state
def test_a_second_install_with_no_stored_block_returns_to_the_declarations(config_dir):
    """A rebuild whose block is gone lands on the shipped matrix — nothing stacks across installs."""
    saved = _saved_tables()
    try:
        write_config(config_dir, "DEFAULT:\n  access:\n    music.write:\n      add: [Media Manager]\n")
        build(config_dir, fleet(), users=users())
        assert MEDIA in permissions.roles_for_capability("music.write")

        write_config(config_dir, "DEFAULT: {}\n")
        build(config_dir, fleet(), users=users())
        assert permissions.roles_for_capability("music.write") == ("Admin", "DCS Admin")
        assert permissions.APPLIED_OVERRIDES == {}
    finally:
        _restore_tables(saved)



# ---------------------------------------------------------------------------------- the page (a save)

async def _signed_in(config_dir, username="specialk"):
    app = build(config_dir, fleet(), users=users())
    http = client(app)
    await sign_in(http, username=username)
    return http


def test_the_page_lists_every_capability_with_its_group(app):
    """One row per declared capability, grouped by the console's OWN nav groups."""
    assert access_page.GROUPS
    for label, members in access_page.GROUPS:
        assert label in NAV_GROUPS_BY_NAME, label
        for capability in members:
            assert capabilities_has(capability), capability


def capabilities_has(capability: str) -> bool:
    """Whether the console declares *capability* right now (after the shell's own install)."""
    return capability in permissions.BASE_CAPABILITIES


def test_every_declared_capability_has_a_group_and_a_description(app, config_dir):
    """The ratchet: a capability added later must be placed AND explained on the page."""
    declared = [name for name in permissions.declared_capabilities()]
    grouped = {name for _, members in access_page.GROUPS for name in members}
    assert declared, "no capability was declared at all — the shell did not install"
    missing_group = [name for name in declared if name not in grouped]
    missing_why = [name for name in declared if not access_page.DESCRIPTIONS.get(name)]
    assert missing_group == [], f"no group for {missing_group}"
    assert missing_why == [], f"no description for {missing_why}"
    extra = sorted(grouped - set(declared))
    assert extra == [], f"the page names capabilities nobody declares: {extra}"


def test_the_page_groups_match_the_sidebar(app):
    """A page capability sits in the group its own nav item declares — the two cannot drift."""
    registrar = app.state.webui_registrar
    shipped = {item.capability: item.group for item in registrar.nav}
    declared = {name for _, members in access_page.GROUPS for name in members}
    for capability, group in shipped.items():
        assert capability in declared, capability
        for label, members in access_page.GROUPS:
            if capability in members:
                assert label == group, (capability, label, group)


def test_a_save_extends_and_narrows_and_takes_effect_on_the_next_request(config_dir):
    """The whole matrix is posted; the server computes the delta and the gate answers the new rule."""
    import asyncio

    async def go():
        app = build(config_dir, fleet(), users=users())
        async with client(app) as http:
            await sign_in(http, username="specialk")
            page = await open_page(http)
            form = form_of(page.text)
            # music.write: drop DCS Admin, grant Media Manager
            form["cell"] = [cell for cell in form["cell"]
                            if cell != "music.write|DCS Admin"] + ["music.write|Media Manager"]
            reply = await save(http, form)
            assert reply.status_code == 303
            after = await open_page(http)
            assert "Access updated" in notice_of(after.text)
            return app, after.text

    app, _ = asyncio.run(go())
    stored = (config_dir / CONFIG).read_text(encoding="utf-8")
    assert "access:" in stored
    assert "Media Manager" in stored
    assert permissions.roles_for_capability("music.write") == ("Admin", MEDIA)
    assert permissions.allows("music.write", {"DCS Admin"}) is False
    assert permissions.allows("music.write", {MEDIA}) is True


def test_a_save_leaves_the_rest_of_the_file_byte_identical(config_dir):
    """Only the ``access:`` block is written: comments, order and every other key survive."""
    import asyncio

    original = """\
# the service configuration — a hand-kept file, comments included
DEFAULT:
  # where the console listens
  listen: 0.0.0.0  # loopback only would be 127.0.0.1
  port: 9876
  frontend: true
  session:
    secret: a-hand-written-session-key-32-chars   # a session key someone chose
"""
    path = write_config(config_dir, original)

    async def go():
        app = build(config_dir, fleet(), users=users())
        async with client(app) as http:
            await sign_in(http, username="specialk")
            page = await open_page(http)
            form = form_of(page.text)
            form["cell"] = form["cell"] + ["music.view|Media Manager"]
            assert (await save(http, form)).status_code == 303

    asyncio.run(go())
    written = path.read_text(encoding="utf-8")
    assert "Media Manager" in written
    # the block is the LAST key of DEFAULT, so removing it must give the original back, byte for byte
    stripped = _strip_access_block(written)
    assert stripped == original


def _strip_access_block(text: str) -> str:
    """The file with its ``access:`` block removed (a text scan: what the save appended)."""
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    inside = False
    for line in lines:
        if not inside and re.match(r"^  access:\s*$", line):
            inside = True
            continue
        if inside:
            if line.strip() == "" or line.startswith("    ") or line.startswith("      "):
                continue
            inside = False
        kept.append(line)
    return "".join(kept)


def test_a_save_that_removes_admin_is_refused_with_its_sentence(config_dir):
    """Admin's column is a lock: a matrix that drops its marker is refused, and nothing is written."""
    import asyncio

    async def go():
        app = build(config_dir, fleet(), users=users())
        async with client(app) as http:
            await sign_in(http, username="specialk")
            page = await open_page(http)
            form = form_of(page.text)
            form["admin"] = [name for name in form["admin"] if name != "music.control"]
            reply = await save(http, form)
            assert reply.status_code == 303
            return (await open_page(http)).text

    html = asyncio.run(go())
    assert "Admin cannot be removed from music.control" in notice_of(html)
    assert not (config_dir / CONFIG).exists()


def test_a_manager_grant_on_a_deny_listed_capability_is_refused_by_the_page(config_dir):
    """The page refuses the cell the matrix draws as a dash — a crafted POST included."""
    import asyncio

    async def go():
        app = build(config_dir, fleet(), users=users())
        async with client(app) as http:
            await sign_in(http, username="specialk")
            page = await open_page(http)
            form = form_of(page.text)
            form["manager"] = form["manager"] + ["logs.view"]
            assert (await save(http, form)).status_code == 303
            return (await open_page(http)).text

    html = asyncio.run(go())
    assert "logs.view is machine- or installation-wide" in notice_of(html)
    assert not (config_dir / CONFIG).exists()


def test_an_unknown_capability_or_role_is_refused_by_the_page(config_dir):
    """A posted name nobody declares is refused, never stored."""
    import asyncio

    async def go():
        app = build(config_dir, fleet(), users=users())
        async with client(app) as http:
            await sign_in(http, username="specialk")
            page = await open_page(http)
            form = form_of(page.text)
            form["cell"] = form["cell"] + ["music.view|Nobody", "music.nope|Media Manager"]
            assert (await save(http, form)).status_code == 303
            return (await open_page(http)).text

    html = asyncio.run(go())
    notice = notice_of(html)
    assert "nobody declares" in notice or "is not a role" in notice
    assert not (config_dir / CONFIG).exists()


def test_a_lockout_save_is_refused_with_its_sentence(config_dir):
    """The one outcome a save must never produce: no role able to open this page."""
    import asyncio

    async def go():
        app = build(config_dir, fleet(), users=users())
        async with client(app) as http:
            await sign_in(http, username="specialk")
            page = await open_page(http)
            form = form_of(page.text)
            form["admin"] = [name for name in form["admin"] if name != access_page.CAPABILITY_VIEW]
            form["cell"] = [cell for cell in form["cell"]
                            if not cell.startswith(access_page.CAPABILITY_VIEW + "|")]
            assert (await save(http, form)).status_code == 303
            return (await open_page(http)).text

    html = asyncio.run(go())
    assert "would leave no role able to open this page" in notice_of(html)
    assert not (config_dir / CONFIG).exists()


def test_a_save_without_the_csrf_token_is_refused(config_dir):
    """The write is behind the session's token, like every other write in the console."""
    import asyncio

    async def go():
        app = build(config_dir, fleet(), users=users())
        async with client(app) as http:
            await sign_in(http, username="specialk")
            page = await open_page(http)
            form = form_of(page.text)
            form["_csrf_token"] = ["not-the-token"]
            return await save(http, form)

    reply = asyncio.run(go())
    assert reply.status_code == 403
    assert not (config_dir / CONFIG).exists()


def test_the_save_records_one_audit_entry(config_dir):
    """One entry per save, naming the capability and the roles that moved."""
    import asyncio
    import types

    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    calls: list[str] = []

    class RecordingBot:
        async def audit(self, message, **kwargs):
            calls.append(message)

    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=RecordingBot())
    try:
        async def go():
            app = build(config_dir, fleet(), users=users())
            async with client(app) as http:
                await sign_in(http, username="specialk")
                page = await open_page(http)
                form = form_of(page.text)
                form["cell"] = [cell for cell in form["cell"] if cell != "music.write|DCS Admin"]
                assert (await save(http, form)).status_code == 303

        asyncio.run(go())
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved

    assert len(calls) == 1
    assert "music.write" in calls[0]
    assert "DCS Admin" in calls[0]


# ------------------------------------------------------------------------------- the stale form

def test_the_form_carries_the_files_revision(config_dir):
    """The rendered matrix posts the revision it was built from — without it nothing can be checked."""
    import asyncio

    from core.utils import configwrite

    write_config(config_dir, "DEFAULT:\n  port: 9876\n")

    async def go():
        async with client(build(config_dir, fleet(), users=users())) as http:
            await sign_in(http, username="specialk")
            return form_of((await open_page(http)).text)

    form = asyncio.run(go())
    assert form[access_page.FIELD_REVISION] == [
        configwrite.config_revision(config_dir / CONFIG)]


def test_a_stale_form_is_refused_and_the_file_keeps_the_first_save(config_dir):
    """Two Admins, one page each: the second save is refused and the first grant is still there.

    Both forms are built from the SAME render, so the second one predates the first save — exactly the
    lost-update the revision check exists to stop.
    """
    import asyncio

    async def go():
        app = build(config_dir, fleet(), users=users())
        async with client(app) as http:
            await sign_in(http, username="specialk")
            view = form_of((await open_page(http)).text)      # a matrix built BEFORE save A
            first = dict(view, cell=view["cell"] + ["music.view|Media Manager"])
            assert (await save(http, first)).status_code == 303
            # the same pre-A view, now stale, asks for a different cell — and must lose
            second = dict(view, cell=view["cell"] + ["music.control|Media Manager"])
            assert (await save(http, second)).status_code == 303
            return (await open_page(http)).text

    html = asyncio.run(go())
    assert "the page was loaded before the configuration changed on disk" in notice_of(html)
    stored = (config_dir / CONFIG).read_text(encoding="utf-8")
    assert "music.view" in stored, "the first save was dropped from the file"
    assert "music.control" not in stored, "the stale form overwrote the file"
    assert MEDIA in permissions.roles_for_capability("music.view")
    assert MEDIA not in permissions.roles_for_capability("music.control")


# ------------------------------------------------------------------------ a refused save leaves a trail

def test_a_refused_save_is_audited_and_the_first_refusal_logged(config_dir, caplog, monkeypatch):
    """A legal save audits once; a refused escalation audits once more, at WARNING, and writes nothing.

    The audit entry is what an operator finds after an incident: it has to name the rule the attempt
    broke and the capability it named, and it has to exist even though the file was never touched.
    """
    import asyncio
    import types

    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    recorded: list[str] = []
    attempts: list[list[permissions.OverrideRefusal]] = []
    real_audit = access_page._audit

    async def spy(request, changes=(), **kwargs):
        attempts.append(list(kwargs.get("refusals") or ()))
        return await real_audit(request, changes, **kwargs)

    monkeypatch.setattr(access_page, "_audit", spy)

    class RecordingBot:
        async def audit(self, message, **kwargs):
            recorded.append(message)

    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=RecordingBot())
    try:
        async def go():
            app = build(config_dir, fleet(), users=users())
            async with client(app) as http:
                await sign_in(http, username="specialk")
                page = await open_page(http)
                form = form_of(page.text)
                form["cell"] = form["cell"] + ["music.view|Media Manager"]
                assert (await save(http, form)).status_code == 303
                legal = len(recorded)
                crafted = form_of((await open_page(http)).text)
                crafted["manager"] = crafted["manager"] + ["logs.view"]
                with caplog.at_level("WARNING"):
                    assert (await save(http, crafted)).status_code == 303
                refused = len(recorded)
                return legal, refused, (await open_page(http)).text

        legal, refused, html = asyncio.run(go())
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved

    assert legal == 1, "a legal save must record exactly one audit entry"
    assert refused == 2, "a refused save must record an audit entry of its own"
    assert len(attempts) == 2
    assert attempts[1], "the refused attempt was audited without naming the refusal"
    assert recorded[0].startswith("Access matrix updated")
    assert "music.view" in recorded[0] and MEDIA in recorded[0]
    assert recorded[1].startswith("Access matrix save refused"), recorded[1]
    assert permissions.MANAGER_LOCKED in recorded[1] and "logs.view" in recorded[1], recorded[1]
    assert "Access save refused" in caplog.text and "logs.view" in caplog.text
    assert "logs.view is machine- or installation-wide" in notice_of(html)
    assert "manager" not in (config_dir / CONFIG).read_text(encoding="utf-8")


# ------------------------------------------------------------------------------- the Admin invariant

def test_every_declared_capability_carries_admin_in_its_base(app):
    """The invariant behind the locked column: a capability the console declares includes Admin.

    The column draws a lock, so a declaration without Admin would be a row whose tick claims a grant
    that is not there — and every save would silently add one.
    """
    declared = permissions.declared_capabilities()
    assert declared, "no capability was declared at all — the shell did not install"

    def base_roles(name: str) -> tuple[str, ...]:
        return (permissions.base_declaration(name) or ((), False))[0]

    missing = [name for name in declared if "Admin" not in base_roles(name)]
    assert missing == [], f"declared without Admin: {missing}"


def test_a_capability_declared_without_admin_is_not_granted_it_and_draws_no_lock(config_dir):
    """The trap defused: such a row keeps its declaration, and the column does not pretend otherwise."""
    import asyncio

    permissions.declare_capability("probe.view", ("DCS Admin",))

    async def go():
        app = build(config_dir, fleet(), users=users())
        async with client(app) as http:
            await sign_in(http, username="specialk")
            page = await open_page(http)
            form = form_of(page.text)
            # a crafted post claiming the lock for the probe row as well as the shipped ones
            form["admin"] = form["admin"] + ["probe.view"]
            assert (await save(http, form)).status_code == 303
            return page.text

    html = asyncio.run(go())
    assert "Admin" not in (permissions.roles_for_capability("probe.view") or ())
    assert permissions.APPLIED_OVERRIDES.get("probe.view") is None

    def row_of(capability: str) -> str:
        start = html.index(f'data-cap="{capability}"')
        end = html.find('<tr class="acc-row', start + 1)
        return html[start:end if end > 0 else len(html)]

    probe = row_of("probe.view")
    assert 'name="admin"' not in probe, "a cell the base does not hold must not post an Admin marker"
    assert "box lock" not in probe, "the column drew a locked tick the base does not hold"
    assert "box no" in probe


# ------------------------------------------------------------------- the apply path is all-or-nothing

def test_apply_role_overrides_installs_nothing_when_it_refuses(app):
    """A refusal leaves the matrix EXACTLY as it was — no capability is left half-overridden."""
    names = list(permissions.declared_capabilities())
    assert len(names) >= 2, "the shell declared too little to walk a two-name apply"
    earlier, foreign = names[0], names[-1]
    # a value this engine did not install: neither the declaration nor an applied override
    permissions.CAPABILITIES[foreign] = ("Admin", "Somebody Else")
    before = dict(permissions.CAPABILITIES)

    candidate, refusals = permissions.candidate_from_entries(
        {earlier: {"add": [MEDIA]}}, roles_known={"Admin", "DCS Admin", MEDIA})
    assert refusals == []

    with pytest.raises(ValueError):
        permissions.apply_role_overrides(candidate)

    assert permissions.CAPABILITIES == before, \
        "an apply that refused left an earlier capability overridden"
    assert permissions.APPLIED_OVERRIDES == {}


# ------------------------------------------------------------------ navigation and the decorators

def test_after_an_override_the_sidebar_and_the_route_agree(config_dir):
    """A role narrowed here loses the entry AND the page — the same predicate answers both."""
    import asyncio

    write_config(config_dir,
                 "DEFAULT:\n  access:\n    music.view:\n      add: [Media Manager]\n"
                 "    music.write:\n      add: [Media Manager]\n      remove: [DCS Admin]\n")
    app = build(config_dir, fleet(), users=users())

    offered = permissions.visible_nav(app.state.webui_registrar.nav, {MEDIA})
    offered_urls = {item.url for item in offered}
    not_offered = permissions.visible_nav(app.state.webui_registrar.nav, {"DCS Admin"})

    assert "/music" in offered_urls
    assert "/music" in {item.url for item in not_offered}

    async def go():
        async with client(app) as http:
            await sign_in(http, username="mm1")
            music = await http.get("/music")
            presets = await http.get("/presets")
            return music.status_code, presets.status_code

    music_status, presets_status = asyncio.run(go())
    assert music_status in (200, 503), f"the sidebar offered a door the gate refused: {music_status}"
    assert presets_status == 403, "a role with no right to a page must still be refused there"


def test_a_manager_is_refused_the_access_page(app):
    """The page decides who reaches everything else: no role but Admin, and no MANAGER grant."""
    assert permissions.roles_for_capability(access_page.CAPABILITY_VIEW) == ("Admin",)
    assert permissions.roles_for_capability(access_page.CAPABILITY_WRITE) == ("Admin",)
    assert permissions.scope_grants(access_page.CAPABILITY_VIEW) is False
    assert permissions.scope_grants(access_page.CAPABILITY_WRITE) is False
    urls = {item.url for item in
            permissions.visible_nav(app.state.webui_registrar.nav, set(), manager=True)}
    assert "/access" not in urls
    assert permissions.allows(access_page.CAPABILITY_VIEW, set(), manager=True) is False


#: the cells a refused save posts: one grant, so the attempt is a real edit rather than an empty post
_REFUSED_FORM = {"admin": [], "cell": ["music.write|Media Manager"], "manager": [],
                 access_page.FIELD_REVISION: [""]}


def _refused_save(config_dir, username: str) -> tuple[int, str, int, int]:
    """POST the whole matrix as *username* with a VALID CSRF token.

    Returns ``(save status, save body, home status, access-page status)``. The token comes from the
    login form, which mints it into the same session that signs in: a 403 on the save therefore
    cannot be the CSRF check, and the refusal is the capability gate's own. The HOME status is the
    positive control that the identity is admitted *somewhere* — the dashboard is the scope-granted
    page — so a refusal here is not just "this session was never signed in".
    """
    import asyncio

    from services.webservice.auth import routes as auth_routes

    async def go() -> tuple[int, str, int, int]:
        async with client(build(config_dir, fleet(), users=users())) as http:
            token = token_of((await http.get(auth_routes.LOGIN_PATH)).text)
            await sign_in(http, username)
            home = await http.get("/")
            page = await http.get(access_page.ACCESS_PATH)
            reply = await http.post(access_page.SAVE_PATH,
                                    data=dict(_REFUSED_FORM, _csrf_token=[token]))
            return reply.status_code, reply.text, home.status_code, page.status_code

    return asyncio.run(go())


def test_a_non_admin_cannot_save_the_matrix(config_dir):
    """A role that may not open this page may not write it either — the save is refused, not just the GET."""
    status, body, home, page = _refused_save(config_dir, "mod1")      # DCS Admin, manages Host A
    assert home == 200, f"the probe's identity was not admitted anywhere ({home})"
    assert page == 403, "a DCS Admin must not open the Access page"
    assert status == 403, (status, body[:400])
    assert "Admin" in body, "the refusal must name the requirement it did not meet"
    assert "csrf" not in body.lower(), "the refusal was the CSRF check, not the gate"
    assert not (config_dir / CONFIG).exists(), "a refused save wrote the file"
    assert permissions.roles_for_capability("music.write") == ("Admin", "DCS Admin")


def test_a_manager_cannot_save_the_matrix(config_dir):
    """A manager scope is no way in — even though its SCOPE admits it to the dashboard."""
    status, body, home, page = _refused_save(config_dir, "mm1")       # Media Manager, manages Host A
    assert home == 200, ("the probe's identity holds no scope granted capability — the manager "
                         "claim below would be vacuous")
    assert page == 403
    assert status == 403, (status, body[:400])
    assert "Admin" in body
    assert not (config_dir / CONFIG).exists(), "a refused save wrote the file"
    assert permissions.roles_for_capability("music.write") == ("Admin", "DCS Admin")




def test_the_page_draws_no_editor_for_a_structural_cell(config_dir):
    """Admin is a lock, this page's own pair is a lock, and a machine-wide manager cell is a dash."""
    import asyncio

    async def go():
        async with client(build(config_dir, fleet(), users=users())) as http:
            await sign_in(http, username="specialk")
            return (await open_page(http)).text

    html = asyncio.run(go())

    def row_of(capability: str) -> str:
        start = html.index(f'data-cap="{capability}"')
        end = html.find('<tr class="acc-row', start + 1)
        return html[start:end if end > 0 else len(html)]

    admin_row = row_of("music.write")
    assert 'name="admin"' in admin_row          # the locked marker the matrix posts
    assert 'type="checkbox" name="admin"' not in admin_row.replace("\n", " ").replace("  ", " ")
    assert "box lock" in admin_row

    locked_row = row_of(access_page.CAPABILITY_WRITE)
    assert 'name="cell"' not in locked_row, "this page's own pair must not be editable"
    assert "box no" in locked_row

    machine_row = row_of("logs.view")
    assert 'name="manager"' not in machine_row, "a machine-wide capability must not take a grant"
    assert "box no" in machine_row


def test_the_change_bar_names_and_counts_a_moved_cell():
    """The shipped ``access.js`` against a DOM stub: one move is named and counted, an undo is not.

    The page works without it (the form posts the whole matrix and the server computes the delta), so
    what is pinned here is only the FEEDBACK — and it is pinned on the real served file, never a copy.
    """
    import json
    import shutil

    from conftest import run_node_harness

    if shutil.which("node") is None:                      # pragma: no cover - a machine without node
        pytest.skip("node is not installed")
    harness = Path(__file__).resolve().parent / "access_harness.js"
    script = REPO_ROOT / "services" / "webservice" / "static" / "access.js"
    result = run_node_harness(["node", str(harness), str(script)])
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)

    assert report["afterOne"]["count"] == "1"
    assert "Media Manager added" in report["afterOne"]["text"]
    assert report["afterOne"]["tag"] is True, "the count is not marked as a change"
    assert report["afterTwo"]["count"] == "2"
    assert "managed_by granted" in report["afterTwo"]["text"]
    # undoing one of two moves leaves ONE, and names the one that is still moved
    assert report["afterUndo"]["count"] == "1"
    assert "Media Manager added" not in report["afterUndo"]["text"]
    assert "managed_by granted" in report["afterUndo"]["text"]
    # the filter hides what does not match, and a group header follows its rows
    assert report["filtered"] == {"music": True, "servers": False, "group": False}


def test_the_matrix_governs_the_website_only():
    """No command decorator path reads the override: the bot's Discord roles are its own.

    Two directions, pinned together: nothing under ``plugins/`` or ``core/`` imports the console's
    permission module, and the decorator helper every command uses reads ``bot.roles`` — never the
    capability table — so a role narrowed on the page still acts in Discord.
    """
    # the override lives in ONE module; a plugin that imports anything else from the console is
    # fine (the read models, the source seam) — what must never be reachable from a command path is
    # the capability table itself
    forbidden = "services.webservice.permissions"
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "plugins").rglob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                if name == forbidden or name.startswith(forbidden + "."):
                    offenders.append(f"{path}: {name}")
    assert offenders == [], offenders

    decorator = (REPO_ROOT / "core" / "utils" / "discord.py").read_text(encoding="utf-8")
    assert "permissions" not in decorator, "a decorator path reads the console's capability table"
    assert "role_overrides" not in decorator
