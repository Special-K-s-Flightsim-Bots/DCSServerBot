"""THE ``+ Add`` ROW WRITES THE BLOCK AND ENABLES NOTHING; THE EXTENSION'S OWN PAGE FILLS IT.

An add is ONE control. It carries no fields: what a block still needs is entered on that extension's
own configuration page, where a scope the file does not configure yet renders its schema form and the
save CREATES the block through the same whole-document patch, the same scope guard, the same schema
validation and the same no-op rule as any other save.

WHAT THE ADD ITSELF WRITES. The extension's DECLARED defaults for that scope PLUS ``enabled: false`` —
``core/extension.py`` reads an ABSENT key as ON, so a block created without the flag would start the
extension on the node's next start. An add never enables anything.

THE EXTENSION PAGE'S OWN SAVE OBEYS THE SAME RULE. ``configgen.apply_form`` refuses to create a
``False`` bool on a creating save (it protects every file from invented keys), and the toggle's hidden
``"0"`` companion is dropped with it — so the page's CREATE path writes the flag itself: a save over a
scope with NO block creates it with ``enabled: false``, whatever the form posted. A save over an
EXISTING block never touches ``enabled`` at all: editing values never flips the switch — only the card's
switch and the enable/disable actions do. A CREATING save the schema refuses says so in its own words,
naming the field the operator must fix.

WHICH CONTROL THE ROW GETS IS DECIDED BY THE SCHEMA, never by an extension's name: a seeded block the
schema's own required checks accept is a one-click write; one they reject (GitHub's ``repo``, Olympus's
``(backend|server)``/``(frontend|client)`` sections) is a LINK to that extension's page for the SAME
scope, and the page creates the block on save.

Pinned here over the REAL app:
* no add row renders a field, for any extension, on either tab;
* an SRS add writes its dict defaults plus ``enabled: false``, key by key, and nothing else changes;
* the extension page for a scope WITHOUT a block renders the form, says the save creates it, and a save
  CREATES the block; a save the schema refuses leaves the file byte-identical; the scope guard still
  refuses a foreign node and an instance the node does not declare;
* Olympus on an INSTANCE tab is a LINK to that instance's tab, and saving both sections there creates
  the block nested under the chosen alternative names;
* GitHub on an instance tab is a LINK (its ``repo`` has no default), and its page creates the block;
* every page-created block carries ``enabled: false`` (Olympus, GitHub, SRS — the toggle checked,
  unchecked or absent), and a CREATING save the schema refuses names the field and writes nothing;
* CONTROL: a save over an EXISTING block leaves ``enabled`` exactly as it was, ``true`` and ``false``
  alike — editing values never flips the switch;
* CONTROL: a configured extension's card still switches, and a switch never creates a block for an
  extension the scope does not configure.
"""
from __future__ import annotations

import html
import re

import pytest
from ruamel.yaml import YAML

from conftest import asgi_client
from services.webservice.pages import node_settings as node_settings_page
from test_webui_dashboard import _login
from test_webui_node_page import _add_options, _app, _page_url, node_data, node_dir  # noqa: F401
from test_webui_settings_node_scope import _csrf, _revision

#: SRS's INSTANCE dict declares these with defaults (``CONFIG_DICT``); the add writes exactly them plus
#: the OFF flag. A key its instance SCHEMA does not declare (``installation``) is not written at all.
SRS_INSTANCE_SEED = {"port": 5002, "blue_password": "blue", "red_password": "red",
                     "gui_server": False, "autoconnect": True, "enabled": False}

#: SRS configured on Caucasus ONLY, carrying an explicit ``enabled`` value — the CONTROL fixture for
#: "a save over an EXISTING block never flips the switch".
EXISTING_SRS = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
    Caucasus:
      home: /home/a-caucasus
      bot_port: 6667
      webgui_port: 8087
      dcs_port: 10307
      extensions:
        SRS:
          port: 5003
          enabled: {flag}
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
"""

#: a FIELD inside an add row: any typed control, any render map marker, any classless form name.
_ROW_FIELD = re.compile(r'<input type="(?:text|number|url|email)"|data-cfg-regex|__KEY__|name="f\.')


async def _get(client, path: str) -> str:
    response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:400])
    return response.text


def _add_tile(page: str) -> str:
    body = re.search(r'<article class="st-tile ext add">(.*?)</article>', page, re.DOTALL)
    assert body is not None, "the + Add tile did not render"
    return body.group(1)


def _row(page: str, name: str) -> str:
    """The ``<li>`` of the ``+ Add`` row whose name is *name*."""
    for chunk in _add_tile(page).split('<li class="st-add-item">')[1:]:
        row = chunk.split("</li>")[0]
        if re.search(rf'<div class="st-add-name"[^>]*>{re.escape(name)}</div>', row):
            return row
    raise AssertionError(f"no add row for {name} in {_add_options(page)}")


def _control(row: str) -> dict:
    """The row's ONE control: a one-click ``form`` (its action and posted fields) or a ``link``."""
    form = re.search(r'<form class="st-swform" method="post" action="([^"]*)"[^>]*>(.*?)</form>',
                     row, re.DOTALL)
    if form is not None:
        fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form.group(2)))
        return {"kind": "form", "action": html.unescape(form.group(1)),
                "fields": {name: html.unescape(value) for name, value in fields.items()}}
    link = re.search(r'<a class="st-sw[^"]*" href="([^"]*)"', row)
    assert link is not None, f"the row carries no control at all: {row[:300]}"
    return {"kind": "link", "href": html.unescape(link.group(1))}


def _document(text: str):
    return YAML(typ="safe").load(text)


def _written(directory, *keys):
    """The block at *keys* of ``nodes.yaml``, as plain data."""
    node = _document((directory / "nodes.yaml").read_text(encoding="utf-8"))
    for key in keys:
        node = node[key]
    return node


def _extension_url(node: str, name: str, tab: int | None = None) -> str:
    url = node_settings_page.node_extension_url(node, name)
    return url if tab is None else f"{url}?tab={tab}"


def _scope_url(node: str, name: str, instance: str | None) -> str:
    """The ``+ Add`` link's own URL carrier: the SCOPE, never a tab position."""
    value = (node_settings_page.NODE_PAGE_SCOPE_DEFAULT if instance is None
             else node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + instance)
    return f"{node_settings_page.node_extension_url(node, name)}?scope={value}"


def _note(page: str) -> str:
    match = re.search(r'<div class="st-note st-create"[^>]*>(.*?)</div>', page, re.DOTALL)
    return html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip() if match else ""


# ============================================================ 1. the row carries no field at all

@pytest.mark.asyncio
async def test_no_add_row_renders_a_field_for_any_extension(node_dir, node_data):  # noqa: F811
    """SRS (one click), GitHub (a link) and Olympus (a link on an instance tab) are all ONE control:
    no typed input, no rendered section, no ``f.`` name — on the Node-wide tab AND on an instance tab.
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        pages = {tab: await _get(client, _page_url("dcs-a-01", tab)) for tab in (0, 1)}
    for name in ("SRS", "GitHub", "Olympus"):
        for tab, page in pages.items():
            row = _row(page, name)
            assert _ROW_FIELD.search(row) is None, f"{name} on tab {tab} renders a field: {row[:400]}"
            assert _control(row)["kind"] in ("form", "link")
            assert row.count("<form") + row.count("<a class=\"st-sw") == 1, row[:400]


# ============================================ 2. an add writes the defaults AND never enables

@pytest.mark.asyncio
async def test_an_srs_add_writes_its_defaults_and_enabled_false_key_by_key(node_dir, node_data):  # noqa: F811
    """The instance add of SRS writes its ``CONFIG_DICT`` defaults PLUS ``enabled: false`` — and no
    other key: the node's own extension block, the sibling instances and the other node stay untouched.
    """
    path = node_dir / "nodes.yaml"
    before = _document(path.read_text(encoding="utf-8"))
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        control = _control(_row(page, "SRS"))
        assert control["kind"] == "form", "SRS cannot be added in one click"
        reply = await client.post(control["action"],
                                  data={name: [value] for name, value in control["fields"].items()})
    assert reply.status_code == 303, reply.text
    after = _document(path.read_text(encoding="utf-8"))
    block = after["dcs-a-01"]["instances"]["Syria"]["extensions"]["SRS"]
    assert set(block) == set(SRS_INSTANCE_SEED), f"the seed carries unexpected keys: {block}"
    for key, value in SRS_INSTANCE_SEED.items():
        assert block[key] == value, f"{key} was not seeded as declared: {block}"
    assert block["enabled"] is False, "an add enabled the extension"
    assert after["dcs-a-01"]["instances"]["Caucasus"] == before["dcs-a-01"]["instances"]["Caucasus"]
    assert after["dcs-b-01"] == before["dcs-b-01"]
    assert after["dcs-a-01"]["extensions"] == before["dcs-a-01"]["extensions"]


# ================================== 3. the extension page CREATES a block for an unconfigured scope

@pytest.mark.asyncio
async def test_the_page_of_a_scope_without_a_block_renders_the_form_and_says_it_creates_it(
        node_dir, node_data):  # noqa: F811
    """Caucasus configures no SRS. Its tab on SRS's own page renders the INSTANCE form addressed at
    THAT instance and carries the explicit "not configured yet — saving creates the block" sentence.
    It must NOT render the old "nothing to edit here" refusal.
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _extension_url("dcs-a-01", "SRS", 2))
    assert _note(page) == node_settings_page.NODE_EXTENSION_CREATE_NOTE, _note(page)
    assert 'name="f.dcs-a-01.instances.Caucasus.extensions.SRS.port"' in page
    assert 'name="f.dcs-a-01.instances.Syria.extensions.SRS.port"' not in page
    assert html.unescape(node_settings_page.NODE_EXTENSION_UNCONFIGURED) not in html.unescape(page)


@pytest.mark.asyncio
async def test_a_page_save_CREATES_the_block_in_that_scope(node_dir, node_data):  # noqa: F811
    """Saving the Caucasus tab creates ``instances.Caucasus.extensions.SRS`` with what was entered, and
    nothing else in the file changes. The block is created SWITCHED OFF even though the post carried a
    checked toggle: a creating save never enables anything (only the switch does).
    """
    path = node_dir / "nodes.yaml"
    before = _document(path.read_text(encoding="utf-8"))
    app = _app(node_dir)
    url = _extension_url("dcs-a-01", "SRS", 2)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.dcs-a-01.instances.Caucasus.extensions.SRS.port": "5003",
            "f.dcs-a-01.instances.Caucasus.extensions.SRS.enabled": "1",
        })
        reopened = await _get(client, url)
    assert reply.status_code == 303, reply.text
    after = _document(path.read_text(encoding="utf-8"))
    assert after["dcs-a-01"]["instances"]["Caucasus"]["extensions"]["SRS"] == \
        {"port": 5003, "enabled": False}
    assert after["dcs-a-01"]["instances"]["Syria"] == before["dcs-a-01"]["instances"]["Syria"]
    assert after["dcs-a-01"]["extensions"] == before["dcs-a-01"]["extensions"]
    assert after["dcs-b-01"] == before["dcs-b-01"]
    # the created block is now the page's editable state, not the "creates it" one
    assert _note(reopened) == ""
    assert 'value="5003"' in reopened


@pytest.mark.asyncio
async def test_a_page_save_the_schema_refuses_leaves_the_file_byte_identical(node_dir, node_data):  # noqa: F811
    """``port`` is an int: a save carrying ``notaport`` is refused by the SAME validation every save
    runs, and the file is untouched — the creating door is not a relaxed one. The refusal NAMES the
    field instead of printing the validator's own sentence.
    """
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    url = _extension_url("dcs-a-01", "SRS", 2)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.dcs-a-01.instances.Caucasus.extensions.SRS.port": "notaport",
        })
        shown = await _get(client, url)
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a refused page save wrote the block"
    sentence = html.unescape(shown)
    assert node_settings_page.NODE_EXTENSION_CREATE_REJECTED % {"fields": "port"} in sentence, \
        "the creating refusal does not name the field"
    assert "is not of type" not in sentence, "the validator's own sentence leaked into the notice"


@pytest.mark.asyncio
async def test_a_creating_save_refused_for_a_missing_required_key_names_that_key(node_dir,
                                                                               node_data):  # noqa: F811
    """GitHub's ``repo`` is required and carries no default: a creating save that leaves it blank is
    refused with a sentence naming ``repo``, and nothing is written."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    url = _extension_url("dcs-a-01", "GitHub", 1)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.dcs-a-01.instances.Syria.extensions.GitHub.target": "/tmp",
        })
        shown = await _get(client, url)
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a refused page save wrote the block"
    assert node_settings_page.NODE_EXTENSION_CREATE_REJECTED % {"fields": "repo"} \
        in html.unescape(shown)


@pytest.mark.asyncio
async def test_the_scope_guard_still_refuses_a_foreign_node_and_an_undeclared_instance(
        node_dir, node_data):  # noqa: F811
    """A field naming ANOTHER NODE, or an INSTANCE the node's block does not declare, is refused with
    nothing written — the save tree addresses instances by a regex, which would otherwise create one.
    """
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    url = _extension_url("dcs-a-01", "SRS", 2)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        foreign_node = await client.post(url, data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.dcs-b-01.extensions.SRS.port": "5003",
        })
        assert foreign_node.status_code == 303, foreign_node.text
        assert path.read_text(encoding="utf-8") == before, "a foreign node's field was written"
        # a FULL block for an instance the node does not declare: with the guard off the save tree's
        # ``instances`` regex would CREATE that instance, and it would pass validation as a real one
        ghost = await client.post(url, data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.dcs-a-01.instances.Ghost.home": "/home/a-ghost",
            "f.dcs-a-01.instances.Ghost.bot_port": "6670",
            "f.dcs-a-01.instances.Ghost.webgui_port": "8090",
            "f.dcs-a-01.instances.Ghost.dcs_port": "10310",
            "f.dcs-a-01.instances.Ghost.extensions.SRS.port": "5003",
        })
    assert ghost.status_code == 303, ghost.text
    assert path.read_text(encoding="utf-8") == before, "an undeclared instance was created"
    assert "Ghost" not in path.read_text(encoding="utf-8")


# ==================================================== 4. the add that cannot write becomes a LINK

@pytest.mark.asyncio
async def test_olympus_on_an_instance_tab_is_a_link_to_that_instance_s_tab(node_dir, node_data):  # noqa: F811
    """Olympus's instance block cannot be seeded (both section containers are required and carry no
    default), so its row is a LINK — and the link addresses THIS instance's tab, computed from the same
    tab list the page renders.
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        control = _control(_row(page, "Olympus"))
        assert control["kind"] == "link", f"Olympus is offered as a one-click write: {control}"
        target = await _get(client, control["href"])
    assert control["href"] == _scope_url("dcs-a-01", "Olympus", "Syria"), control["href"]
    # the destination IS the Syria instance's scope, with both required sections offered as choices
    assert 'name="f.dcs-a-01.instances.Syria.extensions.Olympus.' in target
    assert _note(target) == node_settings_page.NODE_EXTENSION_CREATE_NOTE
    picks = re.findall(r'data-cfg-sectionpick data-cfg-all="([^"]+)"', target)
    assert picks == ["backend,server", "frontend,client"], picks


@pytest.mark.asyncio
@pytest.mark.parametrize("toggle", ["absent", "0", "1"])
async def test_a_page_save_that_CREATES_a_block_switches_it_off(node_dir, node_data, toggle):  # noqa: F811
    """A save over a scope WITHOUT a block CREATES it switched OFF — whatever the toggle posted.

    The toggle is a checkbox plus its hidden ``"0"`` companion, and ``configgen.apply_form`` refuses to
    create a ``False`` bool on a creating save, so nothing it posts can carry the flag into the new
    block. The creating save writes ``enabled: false`` itself, exactly as the Add row does: the loader
    reads an ABSENT key as ON, so a block created without the flag would start on the next node start.
    """
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    url = _extension_url("dcs-a-01", "SRS", 2)
    posted = {"f.dcs-a-01.instances.Caucasus.extensions.SRS.port": "5003"}
    if toggle != "absent":
        posted["f.dcs-a-01.instances.Caucasus.extensions.SRS.enabled"] = toggle
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, data={"_csrf_token": _csrf(page),
                                             "_revision": _revision(page), **posted})
    assert reply.status_code == 303, reply.text
    block = _written(node_dir, "dcs-a-01", "instances", "Caucasus", "extensions", "SRS")
    assert block == {"port": 5003, "enabled": False}, block
    assert path.read_text(encoding="utf-8")  # the block was written at all


@pytest.mark.asyncio
async def test_saving_the_olympus_page_creates_the_block_under_the_chosen_sections(
        node_dir, node_data):  # noqa: F811
    """Both sections written on the Olympus page create THAT instance's block, nested under the names
    the operator chose — the shape the extension's schema declares — and switched OFF, like every
    other door that creates a block.
    """
    path = node_dir / "nodes.yaml"
    before = _document(path.read_text(encoding="utf-8"))
    app = _app(node_dir)
    url = _extension_url("dcs-a-01", "Olympus", 1)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.dcs-a-01.instances.Syria.extensions.Olympus.backend.port": "4512",
            "f.dcs-a-01.instances.Syria.extensions.Olympus.backend.address": "127.0.0.1",
            "f.dcs-a-01.instances.Syria.extensions.Olympus.frontend.port": "3000",
        })
    assert reply.status_code == 303, reply.text
    after = _document(path.read_text(encoding="utf-8"))
    block = after["dcs-a-01"]["instances"]["Syria"]["extensions"]["Olympus"]
    assert block == {"backend": {"port": 4512, "address": "127.0.0.1"},
                     "frontend": {"port": 3000}, "enabled": False}, block
    assert after["dcs-a-01"]["instances"]["Caucasus"] == before["dcs-a-01"]["instances"]["Caucasus"]
    assert after["dcs-a-01"]["extensions"] == before["dcs-a-01"]["extensions"]


@pytest.mark.asyncio
async def test_github_on_an_instance_tab_is_a_link_and_its_page_creates_the_block(node_dir,
                                                                                  node_data):  # noqa: F811
    """GitHub's ``repo`` declares no default and is required, so its seeded block cannot pass the
    schema: the row is a LINK. Its page takes the two required values and creates the block."""
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        control = _control(_row(page, "GitHub"))
        assert control["kind"] == "link", f"GitHub is offered as a one-click write: {control}"
        assert control["href"] == _scope_url("dcs-a-01", "GitHub", "Syria"), control["href"]
        target = await _get(client, control["href"])
        page = await _get(client, control["href"])
        reply = await client.post(control["href"], data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.dcs-a-01.instances.Syria.extensions.GitHub.repo": "https://example.com/mist.git",
            "f.dcs-a-01.instances.Syria.extensions.GitHub.target": "/tmp",
        })
    assert 'name="f.dcs-a-01.instances.Syria.extensions.GitHub.repo"' in target
    assert reply.status_code == 303, reply.text
    block = _written(node_dir, "dcs-a-01", "instances", "Syria", "extensions", "GitHub")
    assert block["repo"] == "https://example.com/mist.git", block
    assert block["target"] == "/tmp", block
    assert block["enabled"] is False, block


@pytest.mark.asyncio
async def test_the_node_wide_scope_always_has_its_tab_so_a_node_level_add_has_a_door(node_dir,
                                                                                     node_data):  # noqa: F811
    """The node page offers EVERY name on the Node-wide tab (its card set is the union of both schema
    blocks), so that page must be able to CREATE it there: the extension page always carries the node
    scope first — for an INSTANCE-scoped name too, whose node block gets the other partial's shape.
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=0))
        control = _control(_row(page, "GitHub"))
        assert control["kind"] == "link"
        assert control["href"] == _scope_url("dcs-a-01", "GitHub", None), control["href"]
        target = await _get(client, control["href"])
    assert 'name="f.dcs-a-01.instances.Syria.extensions.GitHub.repo"' not in target
    assert _note(target) == node_settings_page.NODE_EXTENSION_CREATE_NOTE
    # the instance-scoped name's NODE block carries the instance shape at the node-wide path
    assert 'name="f.dcs-a-01.extensions.GitHub.repo"' in target


@pytest.mark.asyncio
@pytest.mark.parametrize("carried,posted", [("true", "0"), ("false", "1")])
async def test_control_saving_an_existing_block_never_touches_its_enabled_value(
        node_dir, node_data, carried, posted):  # noqa: F811
    """CONTROL: a save over an EXISTING block edits what the operator moved and leaves ``enabled``
    exactly as it was — a ``true`` block stays on with the toggle posted unchecked, a ``false`` block
    stays off with it posted checked. Only the switch and the enable/disable actions change it.
    """
    (node_dir / "nodes.yaml").write_text(EXISTING_SRS.format(flag=carried), encoding="utf-8")
    app = _app(node_dir)
    url = _extension_url("dcs-a-01", "SRS", 2)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.dcs-a-01.instances.Caucasus.extensions.SRS.port": "5004",
            "f.dcs-a-01.instances.Caucasus.extensions.SRS.enabled": posted,
        })
    assert reply.status_code == 303, reply.text
    block = _written(node_dir, "dcs-a-01", "instances", "Caucasus", "extensions", "SRS")
    assert block == {"port": 5004, "enabled": carried == "true"}, block


@pytest.mark.asyncio
async def test_control_the_add_row_still_creates_its_block_switched_off(node_dir, node_data):  # noqa: F811
    """CONTROL: the Add row's own write is unchanged — the SRS instance add still carries
    ``enabled: false`` beside its declared defaults.
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        control = _control(_row(page, "SRS"))
        assert control["kind"] == "form", control
        reply = await client.post(control["action"],
                                  data={name: [value] for name, value in control["fields"].items()})
    assert reply.status_code == 303, reply.text
    block = _written(node_dir, "dcs-a-01", "instances", "Syria", "extensions", "SRS")
    assert block == SRS_INSTANCE_SEED, block


# ============================== 5. the page OPENS on the scope that carries a block, not on an empty one

#: SRS configured on Caucasus ONLY — the node-wide scope has no block, and Syria has none either.
SRS_ON_CAUCASUS = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
    Caucasus:
      home: /home/a-caucasus
      bot_port: 6667
      webgui_port: 8087
      dcs_port: 10307
      extensions:
        SRS:
          port: 5003
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
"""


@pytest.mark.asyncio
async def test_the_page_opens_on_the_scope_that_carries_a_block(node_dir, node_data):  # noqa: F811
    """A cogwheel addresses the page with no ``?tab=``. The page then opens on the FIRST scope that
    already carries a block (the card the operator came from), never on the empty node-wide scope that
    merely CAN be created — an explicit ``?tab=0`` still shows that one, with its creating note.
    """
    (node_dir / "nodes.yaml").write_text(SRS_ON_CAUCASUS, encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        opened = await _get(client, _extension_url("dcs-a-01", "SRS"))
        node_scope = await _get(client, _extension_url("dcs-a-01", "SRS", 0))
    assert 'name="f.dcs-a-01.instances.Caucasus.extensions.SRS.port"' in opened
    assert _note(opened) == "", "the page opened on a scope that does not exist"
    # the explicit tab 0 IS the node-wide scope that does not exist yet
    assert "f.dcs-a-01.instances.Caucasus.extensions.SRS." not in node_scope
    assert _note(node_scope) == node_settings_page.NODE_EXTENSION_CREATE_NOTE


# =========================================================== 6. CONTROL: the switch is untouched
@pytest.mark.asyncio
async def test_control_a_configured_card_still_switches_and_a_switch_creates_nothing(node_dir,
                                                                                     node_data):  # noqa: F811
    """The enable/disable path is unchanged: a configured card's switch posts its own action and
    writes ``enabled`` while keeping every value; a switch posted for an extension the scope does not
    configure is still refused, with nothing written (that is the ADD's job, never a switch's)."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        assert 'title="Turn off"' in page, "the configured card lost its switch"
        off = await client.post(_page_url("dcs-a-01", tab=1), data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            node_settings_page.NODE_PAGE_SCOPE_FIELD:
                node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria",
            node_settings_page.NODE_PAGE_EXTENSION_FIELD: "Trackfile",
            node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD:
                node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
        })
        assert off.status_code == 303, off.text
        after_switch = _document(path.read_text(encoding="utf-8"))
        block = after_switch["dcs-a-01"]["instances"]["Syria"]["extensions"]["Trackfile"]
        assert block == {"enabled": False, "target": "/opt/track"}, block
        # a switch for an extension this scope does not configure writes NOTHING
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        refused = await client.post(_page_url("dcs-a-01", tab=1), data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            node_settings_page.NODE_PAGE_SCOPE_FIELD:
                node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria",
            node_settings_page.NODE_PAGE_EXTENSION_FIELD: "Olympus",
            node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD:
                node_settings_page.NODE_PAGE_EXTENSION_ENABLE,
        })
    assert refused.status_code == 303, refused.text
    final = _document(path.read_text(encoding="utf-8"))
    assert "Olympus" not in final["dcs-a-01"]["instances"]["Syria"]["extensions"]
    assert final["dcs-b-01"] == _document(before)["dcs-b-01"]
