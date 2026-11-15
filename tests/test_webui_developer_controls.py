"""The DEVELOPER.md control table must be a faithful reading of the declarations.

``services/webservice/DEVELOPER.md`` carries the console's WHOLE write surface as a markdown table
(``Row | Control | Capability | Action (__qualname__) | Confirms``). That table has drifted from the
code twice before anyone noticed, because nothing tied it to the declarations it summarizes. This
test makes ONE reading of the declarations (``SERVER_ACTIONS``, ``SERVER_NODE_ACTIONS``,
``SERVER_PAGE_ACTIONS``, ``PLAYER_ACTIONS``, ``NODE_ACTIONS``, ``SERVER_CONFIG_ACTIONS``,
``SERVER_CHANNEL_ACTIONS``, ``SERVER_COALITION_ACTIONS``, ``MISSION_ACTIONS``) and fails when:

* a declared control has no row in the table;
* a row names a control that no longer exists (or whose label/capability/qualname changed); or
* a row's ``Confirms`` cell disagrees with the declaration's ``confirm`` flag.

The four identity fields (row, label, capability, qualname) are compared together, so a label the
table spells differently from the code — the drift this guards — shows up as a missing row beside an
extra one.

The MODS writes are declared by the module that owns them (``pages/mods.py``) rather than in
``pages/actions.py``, so they are read the same way from their own two sources: the capability and the
route shape from the page module's declaration map, and the action's qualname from the plugin module
the write calls. Every mod write posts to its own dialog first and the action route consumes the
one-shot token that dialog minted, which is the shape this table's ``Confirms`` column reads as **yes**.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.webservice.pages import actions as A  # noqa: E402
from services.webservice.pages import mods as mods_page  # noqa: E402

DEVELOPER_MD = REPO_ROOT / "services" / "webservice" / "DEVELOPER.md"

#: ``(Row label, Control label, verb, qualname)`` for EVERY mods write — both owners, one column pair
#: each. The row labels are the table's own (the node's root half and the server's tab), the control
#: labels are the module's words for the controls, and the rest is read from the code below.
MODS_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("mods (node)", mods_page.INSTALL_LABEL, mods_page.INSTALL, "install_mod"),
    ("mods (node)", mods_page.DOWNLOAD_LABEL, mods_page.DOWNLOAD, "download_mod"),
    ("mods (node)", "Change version", mods_page.CHANGE, "change_mod_version"),
    ("mods (node)", "Remove", mods_page.REMOVE, "uninstall_mod"),
    ("mods (tab)", mods_page.INSTALL_LABEL, mods_page.INSTALL, "install_mod"),
    ("mods (tab)", mods_page.DOWNLOAD_LABEL, mods_page.DOWNLOAD, "download_mod"),
    ("mods (tab)", "Change version", mods_page.CHANGE, "change_mod_version"),
    ("mods (tab)", "Remove", mods_page.REMOVE, "uninstall_mod"),
)

#: the owner each row label belongs to.
MODS_OWNER = {"mods (node)": mods_page.NODE_OWNER, "mods (tab)": mods_page.SERVER_OWNER}

#: The table's own Row label, and the declaration tuple(s) that label covers. ``server`` covers BOTH
#: tuple families: the row-strip writes and the creation/removal pair (``delete`` is a row control).
DECLARATION_FAMILIES: tuple[tuple[str, tuple], ...] = (
    ("server", A.SERVER_ACTIONS),
    ("server", A.SERVER_NODE_ACTIONS),
    ("server (page)", A.SERVER_PAGE_ACTIONS),
    ("player", A.PLAYER_ACTIONS),
    ("player (watch)", A.WATCH_ACTIONS),
    ("node", A.NODE_ACTIONS),
    ("config (tab)", A.SERVER_CONFIG_ACTIONS),
    ("channels (tab)", A.SERVER_CHANNEL_ACTIONS),
    ("coalitions (tab)", A.SERVER_COALITION_ACTIONS),
    ("mission (tab)", A.MISSION_ACTIONS),
)

#: A floor on what a successful parse must have found, so a parser that silently matches nothing
#: cannot certify the table. The surface is 34 declarations; the floor stays below that so an
#: intentional removal does not need this number edited, but far above zero.
MIN_ROWS = 30


def _mods_declared() -> dict[tuple[str, str, str, str], bool]:
    """The mods writes, keyed the same way, from the page module's map and the plugin's actions.

    Each verb must resolve to EXACTLY ONE dialog path under its owner and one ``<path>/run`` sibling,
    both carrying the page module's own write capability — that pair IS the "it confirms" fact (the
    dialog mints the token the action route consumes), so the ``Confirms`` cell is read as ``yes``.
    """
    import plugins.modmanager.actions as mod_actions

    declared: dict[tuple[str, str, str, str], bool] = {}
    declared_caps = mods_page.capabilities()
    assert declared_caps[mods_page.NODE_PAGE_PATH] == mods_page.MODS_MANAGE_CAPABILITY, (
        "the node's root-mods page must be declared on the capability its door reads")
    for row_label, label, verb, qualname in MODS_ROWS:
        owner = MODS_OWNER[row_label]
        dialogs = sorted(path for path in declared_caps
                         if path.startswith(f"/mods/{owner}/") and path.endswith(f"/{verb}"))
        assert len(dialogs) == 1, f"{row_label}/{verb}: {dialogs}"
        assert dialogs[0] + "/run" in declared_caps, f"{row_label}/{verb} has no action route"
        assert declared_caps[dialogs[0]] == mods_page.MODS_MANAGE_CAPABILITY, dialogs[0]
        assert declared_caps[dialogs[0] + "/run"] == mods_page.MODS_MANAGE_CAPABILITY, dialogs[0]
        assert callable(getattr(mod_actions, qualname)), f"no action named {qualname!r}"
        key = (row_label, label, mods_page.MODS_MANAGE_CAPABILITY, qualname)
        assert key not in declared, f"duplicate declaration key {key!r}"
        declared[key] = True
    return declared


def _declared() -> dict[tuple[str, str, str, str], bool]:
    """Every declared control keyed by (row, label, capability, qualname) -> ``confirm``."""
    declared: dict[tuple[str, str, str, str], bool] = {}
    for row_label, family in DECLARATION_FAMILIES:
        for action in family:
            key = (row_label, action.label, action.capability, action.qualname)
            assert key not in declared, f"duplicate declaration key {key!r}"
            declared[key] = bool(action.confirm)
    for key, confirm in _mods_declared().items():
        assert key not in declared, f"duplicate declaration key {key!r}"
        declared[key] = confirm
    return declared


def _parse_table() -> dict[tuple[str, str, str, str], bool]:
    """The table's rows keyed the same way. Raises AssertionError if the table cannot be read."""
    lines = DEVELOPER_MD.read_text(encoding="utf-8").splitlines()
    header_at = next((i for i, line in enumerate(lines)
                      if line.startswith("| Row") and "| Control" in line and "| Confirms" in line),
                     None)
    assert header_at is not None, "the control table's header row was not found in DEVELOPER.md"
    rows: dict[tuple[str, str, str, str], bool] = {}
    body_lines: list[str] = []
    for line in lines[header_at + 1:]:
        if not line.startswith("|"):
            break
        if set(line) <= set("|-: \t"):  # the separator line under the header
            continue
        body_lines.append(line)
    for line in body_lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 5, f"unexpected cell count in table row: {line!r}"
        row_label, control, capability, qualname, confirms_cell = cells
        capability = capability.strip("`")
        qualname = qualname.strip("`")
        assert re.fullmatch(r"(?:\*\*yes\*\*|no)", confirms_cell), \
            f"unreadable Confirms cell {confirms_cell!r} in row {line!r}"
        key = (row_label, control, capability, qualname)
        assert key not in rows, f"duplicate table row {key!r}"
        rows[key] = confirms_cell == "**yes**"
    assert len(rows) >= MIN_ROWS, (
        f"the table parsed to only {len(rows)} rows (floor {MIN_ROWS}) — the parser or the table "
        "shape changed")
    return rows


def _describe(key: tuple[str, str, str, str]) -> str:
    row_label, label, capability, qualname = key
    return f"[{row_label}] {label!r} ({capability} -> {qualname})"


def test_table_covers_every_declared_control():
    declared = _declared()
    rows = _parse_table()

    missing = sorted(declared.keys() - rows.keys())
    extra = sorted(rows.keys() - declared.keys())
    assert not missing, (
        "DEVELOPER.md lists no row for these declared controls:\n"
        + "\n".join(f"  {_describe(k)}" for k in missing))
    assert not extra, (
        "DEVELOPER.md names controls the declarations do not have (renamed? removed?):\n"
        + "\n".join(f"  {_describe(k)}" for k in extra))


def test_table_confirm_column_matches_the_declarations():
    declared = _declared()
    rows = _parse_table()

    mismatched = sorted(k for k in declared.keys() & rows.keys() if declared[k] != rows[k])
    assert not mismatched, (
        "DEVELOPER.md's Confirms column disagrees with the declarations:\n"
        + "\n".join(
            f"  {_describe(k)}: table says {'yes' if rows[k] else 'no'}, "
            f"declaration says {'yes' if declared[k] else 'no'}"
            for k in mismatched))
