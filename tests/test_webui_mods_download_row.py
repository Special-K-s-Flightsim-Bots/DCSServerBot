"""The download dialog's field row: the URL and its version sit on ONE line.

The two fields answer one question between them — which link, and which release of it — so the dialog
renders them inside ONE row element, the URL taking the width the version box leaves. What a headless
render can prove is the MARKUP contract: one row, its parent the form, exactly two field children in
that order, and both controls unchanged (the version keeps its id, its field name and its `latest`
value; the URL keeps `required`). The visual line-up itself is the browser's, not this suite's.

The hint line stays the row's next sibling, so it reads under both boxes and never inside the row.
"""
from __future__ import annotations

from html.parser import HTMLParser

import pytest

from services.webservice.pages import mods as mods_page
# the download dialog's own render helper, and the harness its fixtures come from
from test_webui_mods_releases import download_dialog
from test_webui_mods_pages import (  # noqa: F401 - fixtures are imported on purpose
    NODE, ROOT_ROWS, build, client, config_dir, fleet, sign_in, token_of,
)

#: elements the browser never closes, so the tree must not expect an end tag
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                  "param", "source", "track", "wbr"})


class Node:
    """One rendered element: its tag, its attributes, its parent and its children."""

    def __init__(self, tag: str, attrs: list, parent: "Node | None"):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children: list[Node] = []

    @property
    def classes(self) -> list[str]:
        return str(self.attrs.get("class") or "").split()

    def __repr__(self) -> str:  # a failing assertion prints the element, not an address
        return f"<{self.tag} class={' '.join(self.classes) or '-'} id={self.attrs.get('id', '-')}>"


class Tree(HTMLParser):
    """The rendered page as a tree, so a pin asserts STRUCTURE instead of a substring's luck."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", [], None)
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs, self.stack[-1]))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return


def walk(node: Node):
    """Every descendant of ``node`` in document order."""
    for child in node.children:
        yield child
        yield from walk(child)


def by_class(root: Node, name: str) -> list[Node]:
    return [node for node in walk(root) if name in node.classes]


def ancestors(node: Node) -> list[Node]:
    found, current = [], node.parent
    while current is not None:
        found.append(current)
        current = current.parent
    return found


async def rendered(config_dir, monkeypatch) -> Node:
    """The download dialog, rendered by the real application and parsed into a tree."""
    html = await download_dialog(config_dir, monkeypatch)
    tree = Tree()
    tree.feed(html)
    tree.close()
    return tree.root


@pytest.mark.asyncio
async def test_the_url_and_the_version_share_one_row_of_the_form(config_dir, monkeypatch):
    """ONE row holds both fields, in that order, as the form's own children."""
    root = await rendered(config_dir, monkeypatch)

    rows = by_class(root, "dlg-row")
    assert len(rows) == 1, f"exactly one field row, found {rows}"
    row = rows[0]

    # the row belongs to the dialog's form, which is what posts the two fields
    forms = [node for node in ancestors(row) if node.tag == "form"
             and node.attrs.get("id") == "dlg-form"]
    assert forms, "the row must live inside the download form"

    # its DIRECT children are the two fields, URL first — a stacked pair would be siblings of the form
    fields = [child for child in row.children if "dlg-field" in child.classes]
    assert [field.classes for field in fields] == [["dlg-field", "wide"], ["dlg-field", "narrow"]], \
        f"both fields are the row's own children, URL first, found {fields}"

    # nothing else is wrapped into the row: the hint and the buttons stay outside it
    assert not by_class(row, "dlg-hint"), "the hint line is the row's sibling, not its child"
    assert not by_class(row, "dlg-buttons"), "the buttons are outside the row"


@pytest.mark.asyncio
async def test_the_row_carries_the_unchanged_url_and_version_controls(config_dir, monkeypatch):
    """The controls keep their ids, names, `latest` value and `required` — only their parent moved."""
    root = await rendered(config_dir, monkeypatch)
    row = by_class(root, "dlg-row")[0]

    url_inputs = [node for node in walk(row) if node.tag == "input"
                  and node.attrs.get("id") == "url"]
    assert len(url_inputs) == 1, "the URL box is inside the row"
    url = url_inputs[0].attrs
    assert url.get("name") == mods_page.URL_FIELD
    assert url.get("type") == "text"
    assert "required" in url_inputs[0].attrs, "the URL stays required"

    version_inputs = [node for node in walk(row) if node.tag == "input"
                      and node.attrs.get("id") == "ver"]
    assert len(version_inputs) == 1, "the version box is inside the row"
    version = version_inputs[0].attrs
    assert version.get("name") == mods_page.VERSION_FIELD
    assert version.get("type") == "text"
    assert version.get("value") == mods_page.DEFAULT_VERSION, version
    assert version.get("aria-label") == "Version"
    assert "required" not in version_inputs[0].attrs, "the version falls back to the link"


@pytest.mark.asyncio
async def test_the_hint_line_follows_the_row_as_its_next_sibling(config_dir, monkeypatch):
    """The versions hint reads directly under both boxes, at the row's own level."""
    root = await rendered(config_dir, monkeypatch)
    row = by_class(root, "dlg-row")[0]

    siblings = row.parent.children if row.parent is not None else []
    position = siblings.index(row)
    following = [node for node in siblings[position + 1:] if node.tag != "input"]
    assert following, "something must follow the row"
    hint = following[0]
    assert hint.attrs.get("id") == mods_page.VERSIONS_HINT_ID, \
        f"the hint directly under the row, found {hint}"
    assert "dlg-hint" in hint.classes
    assert "hidden" in hint.attrs, "the hint stays hidden until the script has something to say"
