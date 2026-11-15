"""The launch dialog's first paint already carries the weather mode.

The dialog draws the whole temporary-weather block and the preset pick in the MARKUP (with JavaScript
off they must stay visible), and ``dialog-preset.js`` hides the ones the mode does not show — as a
DEFERRED script, i.e. after the browser has painted. A real browser measured 2258 px at first paint
against 715 px once the script had run, which is the whole-dialog collapse an operator reports as a
flash.

The fix is a guard, and it has two halves that only work TOGETHER, so both are pinned here:

* ``confirm.html``'s head sets ``dlg-gate`` on ``<html>`` BEFORE the body (and therefore the dialog)
  is parsed, and drops it on ``DOMContentLoaded`` — which fires after the deferred scripts, so
  ``reveal()`` has already replaced the guard with the mode's own ``display``. The listener is also the
  FAILSAFE: a page whose gate script never loads loses the class and renders exactly as it did before
  the guard existed.
* ``shell.css`` hides the two gated blocks while that class is set.

What this file does NOT cover: the painted result. That is a browser measurement (first frame vs
settled), and the numbers above come from driving the real template, stylesheet and scripts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_webui_startup_temporary_controls import fleet_with
from test_webui_startup_weather_picker import PresetNode, open_dialog
from test_webui_write_pause import build, client, clean_registry, config_dir, sign_in  # noqa: F401

SHEET = Path(__file__).resolve().parents[1] / "services" / "webservice" / "static" / "shell.css"


def guarded(html: str) -> bool:
    """Whether the guard is really BEFORE the dialog in the served markup."""
    gate = html.find('classList.add("dlg-gate")')
    dialog = html.find('class="dlg"')
    return gate != -1 and dialog != -1 and gate < dialog


@pytest.mark.asyncio
async def test_the_dialog_sets_the_gate_before_the_markup_and_removes_it_at_domcontentloaded(
        config_dir, clean_registry):
    node = PresetNode(config_dir)
    (config_dir / "presets.yaml").write_text(
        "Summer:\n  modify:\n    weather:\n      clouds:\n        base: 1000\n", encoding="utf-8")
    async with client(build(config_dir, fleet_with(node))) as http:
        await sign_in(http, "specialk")
        dialog = await open_dialog(http, name="SRS-1")

    assert guarded(dialog.text), "the launch dialog does not hide its gated blocks before first paint"
    assert 'root.classList.remove("dlg-gate")' in dialog.text, \
        "the guard is never dropped, so a dialog whose gate script fails would show nothing"


def test_the_stylesheet_hides_both_gated_blocks_behind_the_gate():
    css = SHEET.read_text(encoding="utf-8")
    assert re.search(r"html\.dlg-gate \.dlg-temp\b[^{]*\{[^}]*display:\s*none", css), \
        "shell.css does not hide the temporary preset behind the first-paint gate"
    assert re.search(r"html\.dlg-gate \.dlg-pick\b[^{]*\{[^}]*display:\s*none", css), \
        "shell.css does not hide the preset pick behind the first-paint gate"
