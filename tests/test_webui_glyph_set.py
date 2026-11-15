"""Every row control draws a glyph that exists, and a ban's lift has a drawing of its own.

Two invariants, both read off the declarations and the one glyph set:

* **a declared control's glyph must be in the set** — a name the set does not carry renders an EMPTY
  ``<svg>``, so the control looks like a blank button and nothing reports it;
* **the ban row's lift must not wear another control's drawing.** ``unmute`` is the mute PAIR's own
  glyph on a player row and says nothing about a ban; the lift is a release, drawn as an OPEN padlock.
  The one intentional share in the set is ``startup`` / ``start`` (their states are disjoint, so no row
  ever shows the drawing twice) — everything else is one drawing per operation.
"""
import re
from pathlib import Path

from services.webservice.pages import actions as actions_page

TEMPLATES = Path(actions_page.__file__).parent.parent / "templates"
ICONS = (TEMPLATES / "_icons.html").read_text(encoding="utf-8")

#: The only drawing several names may share: the bare triangle means "start it", and the states it is
#: offered in are DISJOINT — no server row is ever SHUTDOWN and STOPPED at once, and a radio player's
#: transport strip is not a server row at all, so ``play`` can never sit beside the other two.
INTENTIONAL_SHARES = [("play", "start", "startup")]

#: The ROW tables — the ones whose controls are drawn as glyph buttons (``_strip.html``). A page-level
#: control (Add server, Save, the mission and ban dialogs' openers) renders its WORD, not a glyph, so it
#: is deliberately absent here.
DECLARED = {
    "SERVER_ACTIONS": actions_page.SERVER_ACTIONS,
    "SERVER_NODE_ACTIONS": actions_page.SERVER_NODE_ACTIONS,
    "PLAYER_ACTIONS": actions_page.PLAYER_ACTIONS,
    "NODE_ACTIONS": actions_page.NODE_ACTIONS,
    "NODE_MOVE_ACTIONS": actions_page.NODE_MOVE_ACTIONS,
    "BAN_ACTIONS": [action for action in actions_page.BAN_ACTIONS if getattr(action, "icon", "")],
}


def glyphs() -> dict:
    """Every glyph the set declares — ``{name: drawing}``."""
    return dict(re.findall(r"'([a-z_]+)':\s*'([^']*)'", ICONS))


def glyph_of(action) -> str:
    """The name a control draws: its own ``icon`` when it declares one, else its ``key``.

    A table entry may also be a bare glyph NAME (``NODE_MOVE_ACTIONS`` lists words, not records).
    """
    if isinstance(action, str):
        return action
    return getattr(action, "icon", "") or action.key


def test_every_declared_control_names_a_glyph_the_set_carries():
    """A name the set does not carry renders an empty ``<svg>`` — a blank button, silently.

    NO EXEMPTIONS: every declared row control, the node row's DCS update included, names a drawing
    the set carries.
    """
    known = glyphs()
    missing = []
    for group, actions in DECLARED.items():
        for action in actions:
            name = glyph_of(action)
            if name not in known:
                label = action if isinstance(action, str) else action.key
                missing.append(f"{group}.{label} -> {name!r}")
    assert missing == [], f"controls name glyphs the set does not carry: {missing}"


def test_the_ban_lift_draws_its_own_release():
    """The lift is an OPEN PADLOCK, not the mute pair's loudspeaker and not a taken shape."""
    known = glyphs()
    assert actions_page.BAN_LIFT_ACTION.icon == "unban"
    assert known["unban"].strip(), "the open padlock must have a drawing"

    taken = {name: drawing for name, drawing in known.items() if name != "unban"}
    shared = [name for name, drawing in taken.items() if drawing == known["unban"]]
    assert shared == [], f"the lift reuses another control's drawing: {shared}"
    assert known["unban"] != known["unmute"], "the mute pair's loudspeaker is not a ban lift"


def test_no_two_glyph_names_share_a_drawing_besides_the_disjoint_pair():
    """ONE DRAWING PER OPERATION: outside the startup/start pair, no drawing appears twice."""
    known = glyphs()
    counts: dict[str, list[str]] = {}
    for name, drawing in known.items():
        counts.setdefault(drawing, []).append(name)
    duplicated = {drawing: names for drawing, names in counts.items() if len(names) > 1}
    for drawing, names in duplicated.items():
        assert sorted(names) in [sorted(pair) for pair in INTENTIONAL_SHARES], \
            f"one drawing worn by {names}: {drawing[:60]}"
