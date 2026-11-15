"""The sentences the mods and apply dialogs render with ``|safe`` escape their values.

A sentence record's msgid is source text; its PARAMS are config or form data (a server name, a node
name, a package label). The templates that render these records through ``say(...)|safe`` therefore
need the values escaped where the record is built — the rule ``_server_sentence`` already followed,
and the one the dialog builders now share.
"""
from __future__ import annotations

import types

from markupsafe import escape

from services.webservice import config_apply
from services.webservice.pages import mods as mods_page

HOSTILE = '<img src=x onerror="alert(1)">'


class _Request:
    """The little a sentence builder reads off a request: the session and the app state."""

    def __init__(self) -> None:
        self.session: dict = {}
        self.app = types.SimpleNamespace(state=types.SimpleNamespace())


def _failed_read():
    return types.SimpleNamespace(success=False, data={}, message="")


def test_the_pick_dialog_escapes_every_value_its_safe_sentences_carry():
    dialog = mods_page._pick_dialog(_Request(),  # type: ignore[arg-type]
                                    owner=mods_page.NODE_OWNER, target=HOSTILE,
                                    node_name=HOSTILE, result=_failed_read(), package=HOSTILE)
    assert dialog["what"], "the dialog always carries at least one sentence"
    for sentence in dialog["what"]:
        params = sentence["params"]
        assert str(params["target"]) == str(escape(HOSTILE))
        assert str(params["package"]) == str(escape(HOSTILE))
        assert str(params["node"]) == str(escape(HOSTILE))


def test_the_pick_dialog_keeps_the_raw_value_the_template_escapes_itself():
    """Title/lead/heading are autoescaped, so they must NOT receive the escaped form."""
    dialog = mods_page._pick_dialog(_Request(),  # type: ignore[arg-type]
                                    owner=mods_page.NODE_OWNER, target=HOSTILE,
                                    node_name=HOSTILE, result=_failed_read(), package=HOSTILE)
    assert dialog["page_title"]["params"]["target"] == HOSTILE
    assert dialog["lead"]["params"]["package"] == HOSTILE


def test_the_state_strip_sentence_escapes_the_server_name():
    sentence = mods_page._strip_sentence(mods_page.SERVER_OWNER, HOSTILE, ok=False)
    assert str(sentence["params"]["server"]) == str(escape(HOSTILE))


def test_the_apply_warning_escapes_the_node_name():
    dialog = config_apply.describe(_Request(),
                                   config_apply.ApplyTarget(config_apply.TARGET_RESTART,
                                                            node=HOSTILE))
    assert "<img" not in dialog["warning"]
    assert "&lt;img" in dialog["warning"]
    # the heading and the go label are autoescaped by the template and keep the raw name
    assert HOSTILE in dialog["heading"]
    assert HOSTILE in dialog["go"]
