"""Step 9 of the tour — "What do you run?": the template tiles and the extension rows.

TWO LAYERS, both over the shipped code:

* the TEMPLATES are read from the real ``setup/templates/`` directory, so the data contract (one
  YAML per template, plugin names that exist under ``plugins/``) is the shipped one;
* the SCREEN is driven through a real ASGI request against the shipped setup application, so the
  tiles, the per-instance radio rows, the detected extension paths, the rail row and the draft write
  are the shipped ones and not a description of them.

WHAT IS NOT COVERED HERE: the rendered page in a browser. The assertions read the response body,
never a layout engine, so a CSS regression on the tiles is invisible to this file.
"""

from __future__ import annotations

import ast
import gettext
import pathlib
import re

import pytest
from babel.messages import pofile
from ruamel.yaml import YAML

from conftest import asgi_client
from services.webservice import i18n
from services.webservice.setup import apps as apps_step
from services.webservice.setup import plugins as plugins_step
from services.webservice.setup import servers as servers_step
from services.webservice.setup import tour
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.tour import STEP_PATH
from services.webservice.setup.state import LAST_STEP, SetupDraft
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"
TEMPLATES_DIR = REPO_ROOT / "services" / "webservice" / "setup" / "templates"
#: the languages that ship a ``webui`` catalog (English is the source language and the msgid).
CATALOGS = ("cn", "de", "es", "ru")

#: The four templates the step offers, in tile order.
EXPECTED_TEMPLATES = ("public-server", "competitive", "carrier", "cas-logistics")

#: The two template fields the screen translates before showing them.
TEMPLATE_TEXT_FIELDS = ("label", "description")

#: Two configured instances, in the draft's own order.
INSTANCES = {"DCS.serverrelease": "SRS-1", "DCS.openbeta": "DCS Server"}
NODE_ANSWERED = {"listen_port": 10042, "no_dcs": False, "dcs_installation": "/opt/dcs"}


# ---------------------------------------------------------------------------------- the templates

def _template_documents() -> dict[str, dict]:
    """Every template file as (name, parsed YAML)."""
    documents = {}
    for path in sorted(TEMPLATES_DIR.glob("*" + plugins_step.TEMPLATE_SUFFIX)):
        documents[path.name] = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    return documents


def _shipped_plugins() -> set[str]:
    """The loadable plugins: a folder under ``plugins/`` carrying its ``commands.py``."""
    return {path.name for path in PLUGINS_DIR.iterdir()
            if (path / "commands.py").is_file()}


def test_every_template_file_parses_and_carries_the_four_keys():
    documents = _template_documents()
    assert documents, "no template file was found"
    for name, data in documents.items():
        assert isinstance(data, dict), name
        for key in plugins_step._TEMPLATE_KEYS:
            assert data.get(key), f"{name} carries no {key!r}"
        assert isinstance(data["plugins"], list), name
        assert data["id"] == pathlib.Path(name).stem, name


def test_every_template_names_only_plugins_that_exist():
    shipped = _shipped_plugins()
    for name, data in _template_documents().items():
        for plugin in data["plugins"]:
            assert plugin in shipped, f"{name} names the missing plugin {plugin!r}"


def test_no_template_names_a_default_plugin():
    """A template lists what the operator has to ADD; a default plugin loads without being named."""
    from core.data.impl.nodeimpl import DEFAULT_PLUGINS

    defaults = set(DEFAULT_PLUGINS)
    assert defaults, "the default plugin list is empty"
    for name, data in _template_documents().items():
        for plugin in data["plugins"]:
            assert plugin not in defaults, f"{name} names the default plugin {plugin!r}"


def test_the_step_offers_the_four_agreed_templates_in_their_order():
    templates = plugins_step.load_templates()
    assert tuple(template.id for template in templates) == EXPECTED_TEMPLATES
    by_id = {template.id: template for template in templates}
    assert by_id["public-server"].plugins == ("punishment",)
    assert by_id["competitive"].plugins == ("competitive", "punishment")
    assert by_id["carrier"].plugins == ("greenieboard",)
    assert by_id["cas-logistics"].plugins == ("flightplan", "logistics")


def test_a_template_file_added_to_the_directory_shows_without_a_code_change(tmp_path):
    (tmp_path / "mine.yaml").write_text(
        "id: mine\norder: 1\nlabel: Mine\ndescription: A template the code never heard of\n"
        "plugins:\n  - motd\n", encoding="utf-8")
    (tmp_path / "broken.yaml").write_text("id: broken\n", encoding="utf-8")
    templates = plugins_step.load_templates(tmp_path)
    assert [template.id for template in templates] == ["mine"]
    body = plugins_step.screen_body(plugins_step.PluginAnswers(), [], {}, str,
                                    token="tok", templates=templates)
    assert "A template the code never heard of" in body
    assert "motd" in body


def test_the_template_directory_ships_the_four_files():
    names = sorted(path.name for path in TEMPLATES_DIR.glob("*" + plugins_step.TEMPLATE_SUFFIX))
    assert names == sorted(f"{template}.yaml" for template in EXPECTED_TEMPLATES)


# ---------------------------------------------------------------------------------------- the rail

def test_the_step_route_is_the_tours_one_route():
    assert plugins_step.ROUTE == STEP_PATH


def test_step_nine_sits_before_the_review_step():
    rows = tour.rail_steps(True, True, True)
    assert rows == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert rows.index(plugins_step.STEP_ID) < rows.index(10)
    assert LAST_STEP == 10
    assert tour.STEP_LABELS[9] == "What do you run?"
    assert tour.STEP_LABELS[10] == "Review and write"


def test_the_rail_stays_correct_without_discord():
    rows = tour.rail_steps(False, True, True)
    assert rows == [1, 2, 3, 4, 5, 7, 8, 9, 10]
    assert rows.index(plugins_step.STEP_ID) < rows.index(10)


def test_the_rail_stays_correct_when_the_dcs_installation_is_declined():
    assert tour.rail_steps(True, False, True) == [1, 2, 3, 4, 5, 6, 7, 9, 10]
    assert tour.rail_steps(True, True, False) == [1, 2, 3, 4, 5, 6, 7, 9, 10]
    assert tour.rail_steps(False, False, True) == [1, 2, 3, 4, 5, 7, 9, 10]


def test_the_joining_rail_ends_on_the_review_step():
    assert tour.rail_steps(True, True, True, "join") == [3, 4, 8, 10]
    assert tour.rail_steps(True, False, True, "join") == [3, 4, 10]


# ------------------------------------------------------------------------------------- the draft

def test_the_plugins_block_survives_a_save_and_load(tmp_path):
    draft = SetupDraft(step=9)
    draft.plugins = {"templates": ["public-server", "carrier"],
                     "radio": {"SRS-1": "voicechat"},
                     "tacview": True, "lotatc": False}
    draft.save(tmp_path)
    loaded = SetupDraft.load(tmp_path)
    assert loaded.plugins == draft.plugins
    assert loaded.step == 9
    assert loaded.finished is False


def test_an_unknown_key_is_carried_through_a_plugins_save(tmp_path):
    draft = SetupDraft(step=8)
    draft.plugins = {"templates": ["carrier"], "radio": {}, "tacview": False, "lotatc": False,
                     "from_a_later_step": {"kept": True}}
    draft.extra = {"another_steps_block": {"kept": True}}
    draft.main_extra = {"music": {"enabled": True}}
    answers = plugins_step.resolve_answers({"template_2": "carrier"}, ["SRS-1", "DCS Server"])
    draft.plugins.update(plugins_step.non_secret_document(answers))
    draft.save(tmp_path)
    loaded = SetupDraft.load(tmp_path)
    assert loaded.plugins["from_a_later_step"] == {"kept": True}
    assert loaded.plugins["templates"] == ["carrier"]
    assert loaded.extra["another_steps_block"] == {"kept": True}
    assert loaded.main_extra["music"] == {"enabled": True}


def test_the_answers_default_every_instance_to_srs():
    answers = plugins_step.resolve_answers({}, ["DCS.serverrelease", "DCS.openbeta"])
    assert answers.radio == {"DCS.serverrelease": plugins_step.RADIO_SRS,
                             "DCS.openbeta": plugins_step.RADIO_SRS}
    assert answers.templates == []


def test_the_answers_read_one_radio_per_instance_and_the_two_ticks():
    answers = plugins_step.resolve_answers(
        {"template_0": "public-server", "template_2": "carrier",
         "radio_0": "voicechat", "radio_1": "srs", "tacview": "on"},
        ["DCS.serverrelease", "DCS.openbeta"])
    assert answers.templates == ["public-server", "carrier"]
    assert answers.radio == {"DCS.serverrelease": plugins_step.RADIO_VOICE_CHAT,
                             "DCS.openbeta": plugins_step.RADIO_SRS}
    assert answers.tacview is True
    assert answers.lotatc is False


def test_a_submitted_tile_value_names_the_template_it_carries():
    """The posted value is the ticked template; the index is the fallback, never a conflict."""
    # index 3 is ``cas-logistics`` in the shipped catalogue
    answers = plugins_step.resolve_answers({"template_3": "carrier"}, [])
    assert answers.templates == ["carrier"]
    assert plugins_step.resolve_answers({"template_3": "nope"}, []).templates == ["cas-logistics"]
    assert plugins_step.resolve_answers({"template_3": ""}, []).templates == ["cas-logistics"]


# --------------------------------------------------------------------------------------- the view

def build(config_dir):
    window = SetupWindow(code=CODE, wrong_delay=0.0)
    return create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN), window


async def enter(client, code: str = CODE):
    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    return await client.get(SETUP_PATH)


def token_of(html: str) -> str:
    found = TOKEN_RE.search(html)
    assert found, "the step form carries no CSRF token"
    return found.group(1)


def rail_rows(html: str) -> list[str]:
    block = re.search(r'<details class="rail".*?</details>', html, re.S)
    scope = block.group(0) if block else ""
    return re.findall(r'<span class="lbl">([^<]*)</span>', scope)


def seed(config_dir, *, step: int = 8, discord: bool = True, detected=None) -> SetupDraft:
    """A draft at the end of step 8, with two configured instances in the servers block."""
    draft = SetupDraft(step=step, discord=discord)
    draft.node = dict(NODE_ANSWERED)
    draft.servers = servers_step.non_secret_document(
        servers_step.answers_from_mapping({}, detected=INSTANCES if detected is None else detected))
    draft.save(config_dir)
    return draft


@pytest.mark.asyncio
async def test_the_screen_names_every_template_and_its_plugins(tmp_path):
    seed(tmp_path)
    app, _window = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "9"})).text
    rows = rail_rows(page)
    assert "What do you run?" in rows
    assert rows[-1] == "Review and write"
    # one checkbox per template data file, so the tile count follows the directory
    assert page.count('type="checkbox" name="template_') == len(EXPECTED_TEMPLATES)
    assert 'value="economy"' not in page
    for template, label, plugs in (("public-server", "Public server", "punishment"),
                                   ("competitive", "PvP / competitive", "competitive · punishment"),
                                   ("carrier", "Carrier wing", "greenieboard"),
                                   ("cas-logistics", "CAS / logistics", "flightplan · logistics")):
        start = page.index(f'value="{template}"')
        tile = page[start:page.index("</label>", start)]
        assert f"<b>{label}</b>" in tile, label
        assert f'<span class="plugs">{plugs}</span>' in tile, f"{label}: {plugs}"


@pytest.mark.asyncio
async def test_the_screen_offers_one_radio_per_configured_instance(tmp_path):
    seed(tmp_path)
    app, _window = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "9"})).text
    assert re.search(r'name="radio_0" value="srs"\s+checked', page)
    assert re.search(r'name="radio_1" value="srs"\s+checked', page)
    assert page.count('name="radio_0"') == 2
    assert page.count('name="radio_1"') == 2
    assert "Radio for SRS-1 — pick one" in page
    # the installer's "give it a name" case keeps the instance as the shown name
    assert "Radio for DCS.openbeta — pick one" in page
    assert 'name="tacview"' in page and 'name="lotatc"' in page


@pytest.mark.asyncio
async def test_the_extension_rows_show_the_detected_paths(tmp_path, monkeypatch):
    paths = {"SRS": "/opt/srs", "Tacview": "/opt/tacview", "LotAtC": None}
    monkeypatch.setattr(apps_step, "detect_app", lambda spec: paths[spec.key])
    seed(tmp_path)
    app, _window = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "9"})).text
    assert "/opt/srs" in page
    assert "/opt/tacview" in page
    assert "Not found on this machine." in page


@pytest.mark.asyncio
async def test_a_submission_writes_the_answers_to_the_draft(tmp_path):
    seed(tmp_path)
    app, _window = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "9"})).text
        response = await client.post(STEP_PATH,
                                     data={"n": "9", "_csrf_token": token_of(page),
                                           "template_0": "public-server",
                                           "template_3": "cas-logistics",
                                           "radio_0": "voicechat",
                                           "radio_1": "srs",
                                           "tacview": "on"},
                                     follow_redirects=False)
    assert response.status_code == 303
    draft = SetupDraft.load(tmp_path)
    assert draft.plugins["templates"] == ["public-server", "cas-logistics"]
    assert draft.plugins["radio"] == {"DCS.serverrelease": "voicechat",
                                      "DCS.openbeta": "srs"}
    assert draft.plugins["tacview"] is True
    assert draft.plugins["lotatc"] is False
    assert draft.step == 9
    # the draft is tour state only: no configuration file is written by this step
    assert not (tmp_path / "main.yaml").exists()
    assert not (tmp_path / "nodes.yaml").exists()


@pytest.mark.asyncio
async def test_the_screen_still_renders_with_no_configured_instance(tmp_path):
    """A machine that declined the DCS installation keeps the step: it asks for the templates only."""
    draft = SetupDraft(step=8, discord=True)
    draft.node = dict(NODE_ANSWERED, no_dcs=True)
    draft.save(tmp_path)
    app, _window = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "9"})).text
    assert "What do you run?" in rail_rows(page)
    assert "DCS servers" not in rail_rows(page)
    assert "Public server" in page
    assert 'name="radio_0"' not in page


# ----------------------------------------------------------------------------------------- i18n

def _module_msgids(path: pathlib.Path) -> list[str]:
    """The ``i18n._()`` literals of one module, read without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            found.append(node.args[0].value)
    return found


def test_every_plugins_string_is_shipped_in_every_catalog():
    msgids = _module_msgids(REPO_ROOT / "services/webservice/setup/plugins.py")
    assert len(msgids) >= 15, msgids
    for language in CATALOGS:
        catalog = pofile.read_po(open(
            REPO_ROOT / f"locale/{language}/LC_MESSAGES/webui.po", encoding="utf-8"))
        have = {message.id for message in catalog if message.id}
        fuzzy = {message.id for message in catalog if message.id and message.fuzzy}
        for msgid in msgids:
            assert msgid in have, f"'{msgid}' is missing from the {language} catalog"
            assert msgid not in fuzzy, f"'{msgid}' is fuzzy in the {language} catalog"


def _srs_help_msgids() -> list[str]:
    """The two SRS help lines of the step, told apart by what they say about the port."""
    return [msgid for msgid in _module_msgids(REPO_ROOT / "services/webservice/setup/plugins.py")
            if "SRS port" in msgid]


def test_the_srs_help_copy_names_no_port_number():
    """The second port is not a constant: the sentence must not claim one."""
    msgids = _srs_help_msgids()
    assert len(msgids) == 2, msgids
    for msgid in msgids:
        assert not re.search(r"\b50\d\d\b", msgid), msgid


def test_the_two_srs_help_lines_are_shipped_in_every_catalog():
    msgids = _srs_help_msgids()
    assert len(msgids) == 2, msgids
    for language in CATALOGS:
        entries = {message.id: message for message in pofile.read_po(open(
            REPO_ROOT / f"locale/{language}/LC_MESSAGES/webui.po", encoding="utf-8"))
            if message.id}
        for msgid in msgids:
            assert msgid in entries, f"'{msgid}' is missing from the {language} catalog"
            assert not entries[msgid].fuzzy, f"'{msgid}' is fuzzy in the {language} catalog"


def test_every_template_label_and_description_is_translated_in_every_catalog():
    """No tile may fall back to English on a translated console.

    A template's label and description are DATA in its YAML file, so Babel never extracts them: the
    tile reaches the screen through ``translate(template.label)``/``translate(template.description)``
    and gettext answers the msgid unchanged when the catalog has no entry, or an empty one, or a
    fuzzy one (gettext drops fuzzy entries at compile time) — the tile renders in English and nothing
    fails. Both fields are read from the template files and resolved through each language's
    compiled ``webui`` catalog, the ``.mo`` the screen actually reads, never the ``.po`` beside it.
    """
    documents = _template_documents()
    assert documents, "no template file was found"
    catalogs = {}
    for language in CATALOGS:
        catalogs[language] = i18n.translations_for(language)
        assert isinstance(catalogs[language], gettext.GNUTranslations), \
            f"the {language} webui catalog did not load — is {language}/webui.mo compiled?"
    unresolved = []
    for name, data in documents.items():
        for field in TEMPLATE_TEXT_FIELDS:
            msgid = data[field]
            for language, catalog in catalogs.items():
                if catalog.gettext(msgid) == msgid:
                    unresolved.append(
                        f"{name}: the {field} '{msgid}' is not translated in the {language} catalog")
    assert not unresolved, "\n".join(unresolved)
