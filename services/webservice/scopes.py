"""THE SCOPE MODEL — a config file's DEFAULT block plus one SCOPED BLOCK per node/instance.

WHY THIS EXISTS
---------------
A plugin's, a service's and an extension's configuration file all share ONE shape (the schemas prove
it — ``plugins/<name>/schemas/<name>_schema.yaml``, ``services/<name>/schemas/<name>_schema.yaml``,
``extensions/schemas/extensions_schema.yaml``):

* a top-level ``DEFAULT`` block — the scope every instance starts from; and
* zero or more OVERRIDE blocks — a node's block (whose keys are its instances) or an instance's own
  block — that change only that node or instance.

The old page showed the DEFAULT block and then crammed every override into one anonymous
"custom entries" control, so a file with a DEFAULT block and two node/instance blocks rendered
BADLY: the overrides were not addressable, and every field name carried a ``default.`` prefix.

This module turns that document into TABS. One tab per scope — ``DEFAULT`` first, then one per
node/instance block FOUND IN THE FILE — with the block's own fields, keyed RELATIVE to the tab (so
no field ever reads ``default.x.y.z`` again). The tabs are the model; the template renders them.

WHAT A SCOPE IS (exhaustive)
----------------------------
``build_scopes`` returns a list of dicts, DEFAULT first, the overrides in file order:

* ``label`` — the tab's name. ``DEFAULT`` for the shared block; ``<node> + <instance>`` for a node
  block when the install has SEVERAL nodes and plain ``<instance>`` when it has ONE (the brief's own
  rule, decided by the DATA — the node count — never invented);
* ``kind`` — ``default`` / ``node`` / ``instance``;
* ``node`` / ``instance`` — the names a save (or a "+ Add block") needs, or ``None``;
* ``base`` — the block's document path (the tab's field names are the names relative to it);
* ``fields`` — the block's TOP-LEVEL :class:`~services.webservice.configgen.Field` records, with
  CONCRETE paths (so the value/rows maps and the save resolve against the real document), for the
  template to render;
* ``present`` — whether the block exists in the file today;
* ``plain`` — ``True`` ONLY for a scope built from a schema with no override container (a flat
  ``backup``/``bot`` schema, or a bare ``DEFAULT`` block): the scope IS the whole file, so the page
  says so rather than claiming a node/instance story. Absent for every real scope.

A flat schema (``main_schema.yaml``) has no override container, so it yields ONE ``DEFAULT`` scope
over the whole root — the identical answer the old ``configgen.scopes`` gave, stated here too.

NO WRITE PATH HERE. This module only READS a schema tree and a document and builds a render model.
A save goes through the ONE primitive (``core.utils.configwrite``); the "+ Add block" route writes
the block it names through the same primitive (see ``pages.settings``).
"""
from __future__ import annotations

from dataclasses import replace

from . import configgen

__all__ = [
    "DEFAULT_TAG", "override_container", "element_fields", "rebase", "display_name",
    "document_blocks", "build_scopes", "ScopeView", "scope_view", "add_target",
    "extension_schema_parts", "extension_save_tree", "extension_blocks", "build_extension_scopes",
    "node_options", "scopes_for_node", "scope_keys", "document_slice", "merge_slice",
]

#: the shared block's key — the top-level ``DEFAULT`` every instance starts from.
DEFAULT_TAG = "DEFAULT"


def override_container(tree: configgen.Field) -> configgen.Field | None:
    """The root field that holds the node/instance blocks, or ``None`` for a flat schema.

    A plugin/service schema puts its overrides under a top-level ``regex;(.+)`` (an unbounded key: any
    node or instance name) whose element is a layout chooser between the element shape (an instance
    block) and the instance shape (a node's block). A flat schema (``main_schema.yaml``) has no such
    child, and then the whole root IS the one DEFAULT scope.
    """
    for child in tree.children:
        if child.kind == configgen.KIND_REGEX and child.element is not None:
            return child
    for child in tree.children:
        if child.kind == configgen.KIND_CHOICE:
            return child
    return None


def element_fields(container: configgen.Field) -> list[configgen.Field]:
    """The element shape's TOP-LEVEL fields — what ONE node/instance block holds.

    The override's element is usually a layout chooser (``element_schema`` | ``instance_schema``): the
    ELEMENT option (a node key's value is one, and an instance key's value is that same element) is
    the shape a block carries, so its children are the fields a tab renders. When the element is not a
    chooser it IS the element shape and its children are taken directly.
    """
    element = container.element
    if element is None:
        return []
    if element.kind == configgen.KIND_CHOICE:
        choice = next((child for child in element.children
                       if child.kind == configgen.KIND_MAP), None)
        if choice is None:
            choice = element.children[0] if element.children else None
        return list(choice.children) if choice is not None else []
    return list(element.children)


def rebase(field: configgen.Field, base: tuple) -> configgen.Field:
    """A copy of *field* with its placeholder first token replaced by the concrete *base* path.

    A scoped tree's fields all begin with ONE placeholder token that stands for the block being
    rendered — the element shape's ``*`` (an override's own key) or a frame's ``DEFAULT`` — and a
    concrete block replaces that token with its real path (the node + instance tokens, or the instance
    token, or a node's ``extensions.<name>`` run), so the control names and the document paths line up
    for THAT block. A nested ``*`` (a sequence/regex element deeper down) is left alone: only the
    LEADING placeholder is concrete.
    """
    def one(node: configgen.Field) -> configgen.Field:
        path = base + node.path[1:] if node.path else node.path
        return replace(
            node,
            path=path,
            children=tuple(one(child) for child in node.children),
            element=one(node.element) if node.element is not None else None,
        )

    return one(field)


def display_name(field: configgen.Field, base: tuple) -> str:
    """The key a tab PRINTS for *field* — its name relative to the tab's *base* (no ``default.``).

    A field that lives under the tab's base drops the base (``DEFAULT.messages.ban_username`` ->
    ``messages.ban_username``). A root-level literal shown on the DEFAULT tab (``commands``) is NOT
    under ``DEFAULT``, so it keeps its own full name.
    """
    if base and field.path[:len(base)] == base:
        return ".".join(field.path[len(base):])
    return field.name


def document_blocks(document, node_names) -> list[dict]:
    """Every override block the DOCUMENT carries, in file order — a reading, never a wish-list.

    For each top-level key ``K`` (other than ``DEFAULT`` and the file's own literal keys):

    * when ``K`` is one of the cluster's *node_names*, its value is a node's block — a mapping of
      instance name to element block — and each instance key becomes its own ``node`` block;
    * otherwise ``K`` is an INSTANCE name (the single-node reading): ``K`` becomes an ``instance``
      block whose value is the element directly.

    The node names are what decide the reading; with no node knowledge every key is read as an
    instance, which is the honest answer for a bare web test. Returns ``[{kind, node, instance,
    base}]``.
    """
    if not isinstance(document, dict):
        return []
    known = {str(name) for name in (node_names or ())}
    multiple = len(known) > 1
    blocks: list[dict] = []
    for key in document:
        name = str(key)
        if name == DEFAULT_TAG:
            continue
        value = document[key]
        if name in known and isinstance(value, dict):
            for instance in value:
                blocks.append({"kind": "node", "node": name, "instance": str(instance),
                               "base": (name, str(instance))})
        else:
            blocks.append({"kind": "instance", "node": None, "instance": name,
                           "base": (name,)})
    return blocks


def _label(kind: str, node, instance, multiple: bool) -> str:
    """A block's tab label — ``<node> + <instance>`` when several nodes, else the plain name."""
    if kind == "node" and multiple:
        return f"{node} + {instance}"
    return str(instance)


def build_scopes(tree: configgen.Field, document, node_names=()) -> list[dict]:
    """The TABS for *tree* as the *document* fills it — DEFAULT first, then one per block.

    A flat schema yields one ``DEFAULT`` scope over the whole root. A scoped schema yields the DEFAULT
    scope (its own block PLUS the file's root-level literals such as ``commands``/``chat_commands``,
    which the mockup shows inside the DEFAULT tab) and then one scope per node/instance block the
    document carries. Each override scope's fields are the element shape REBASED to that block, so no
    field name ever reads ``default.x.y.z``.

    A scope produced for a schema with NO override container carries ``plain`` = True: its ``fields``
    are the WHOLE file (there is no node/instance block to switch between), so the page can say so
    instead of claiming a ``DEFAULT``/instance story the file does not have. A scope over a schema
    that DOES declare the container never sets it.
    """
    container = override_container(tree)
    default_child = next((child for child in tree.children if child.key == DEFAULT_TAG), None)
    if container is None and default_child is None:
        return [{"label": DEFAULT_TAG, "kind": "default", "node": None, "instance": None,
                 "base": (), "present": True, "fields": list(tree.children), "plain": True}]
    if container is None:
        # a DEFAULT block but no override container: the DEFAULT scope plus its root literals
        assert default_child is not None
        fields = list(default_child.children) + [
            child for child in tree.children
            if child is not default_child and child is not container]
        return [{"label": DEFAULT_TAG, "kind": "default", "node": None, "instance": None,
                 "base": (DEFAULT_TAG,), "present": True, "fields": fields, "plain": True}]

    literals = [child for child in tree.children
                if child is not default_child and child is not container]
    default_fields = (list(default_child.children) if default_child is not None else []) + literals
    scopes = [{"label": DEFAULT_TAG, "kind": "default", "node": None, "instance": None,
               "base": (DEFAULT_TAG,), "present": default_child is not None,
               "fields": default_fields}]

    element = element_fields(container)
    known = {str(name) for name in (node_names or ())}
    multiple = len(known) > 1
    for block in document_blocks(document, node_names):
        fields = [rebase(field, block["base"]) for field in element]
        scopes.append({
            "label": _label(block["kind"], block["node"], block["instance"], multiple),
            "kind": block["kind"], "node": block["node"], "instance": block["instance"],
            "base": block["base"], "present": True, "fields": fields,
        })
    return scopes


def add_target(kind: str, node, instance, node_names=()) -> dict:
    """The block a "+ Add block" writes: its BASE path and the label the new tab will carry.

    Multi-node installs name a node AND an instance (``<node>: {<instance>: {}}``); a single-node
    install names only the instance (``<instance>: {}``). The base is what the write creates and what
    the tab is keyed by.
    """
    multiple = len({str(name) for name in (node_names or ())}) > 1
    node_name = str(node) if (multiple and node) else None
    instance_name = str(instance)
    base = (node_name, instance_name) if node_name else (instance_name,)
    return {"kind": "node" if node_name else "instance", "node": node_name,
            "instance": instance_name, "base": base}


class ScopeView:
    """The render maps for ONE scope: the values/rows/customs/elements/choices its fields need.

    Built from a SUBTREE rooted at the scope's own concrete fields, so the SAME generator helpers the
    full page uses (``collect_current``, ``rows_for``, ``custom_entries``, ``regex_value``,
    ``form_leaves``) answer for the block — the values are keyed by the field's FULL name (the form
    name too), and the template prints the name RELATIVE to the scope.
    """

    __slots__ = ("root", "values", "rows", "customs", "elements", "choices", "sections")

    def __init__(self, root, values, rows, customs, elements, choices, sections):
        self.root = root
        self.values = values
        self.rows = rows
        self.customs = customs
        self.elements = elements
        self.choices = choices
        #: for each FIXED-ALTERNATIVE regex container, ``{present: [...], missing: [...]}`` — the
        #: schema's own section names, split by whether the document already carries them.
        self.sections = sections


def scope_view(fields: list[configgen.Field], document) -> ScopeView:
    """The render maps for *fields* (one scope's top-level fields) against *document*."""
    root = configgen.Field(key="", kind=configgen.KIND_MAP, path=(), children=tuple(fields),
                           kind_label=configgen.KIND_LABELS[configgen.KIND_MAP])
    values = configgen.collect_current(root, document)
    for func_field in configgen.leaves(root):
        if func_field.kind == configgen.KIND_FUNC:
            func_value = values.get(func_field.name)
            if isinstance(func_value, (dict, list)):
                from core.utils import configwrite
                values[func_field.name] = configwrite.dump_config(func_value)
    flat = configgen.flatten(root)
    # A SEQ and a CHOICE field live at their OWN path, so their ``name`` is already unique — those maps
    # stay keyed by name. A REGEX container's path is its PARENT map's, so two regex containers in one
    # mapping share a ``name``; their maps are keyed by :func:`configgen.view_id` instead.
    rows = {field.name: configgen.rows_for(field, configgen.value_at(document, field.path))
            for field in flat if field.kind == configgen.KIND_SEQ}
    customs = {configgen.view_id(field): configgen.custom_entries(
        field, configgen.regex_value(root, field, document))
        for field in flat if field.kind == configgen.KIND_REGEX}
    elements = {}
    for field in flat:
        if field.kind == configgen.KIND_SEQ:
            elements[field.name] = configgen.form_leaves(field)
        elif field.kind == configgen.KIND_REGEX:
            elements[configgen.view_id(field)] = configgen.form_leaves(field)
    sections = {configgen.view_id(field): configgen.section_state(field, customs[configgen.view_id(field)])
                for field in flat if field.kind == configgen.KIND_REGEX and field.alternatives}
    choices = {}
    for field in flat:
        if field.kind != configgen.KIND_CHOICE:
            continue
        current = configgen.value_at(document, field.path)
        options = []
        for option in field.children:
            leaves = configgen.form_leaves(option)
            matches = ((isinstance(current, list)
                        and option.kind in (configgen.KIND_LIST, configgen.KIND_SEQ))
                       or (isinstance(current, dict) and option.kind == configgen.KIND_MAP))
            options.append({"key": option.key, "leaves": leaves, "matches": matches,
                            "values": {rel: configgen.value_at(document, leaf.path)
                                       for leaf, rel in leaves}})
        choices[field.name] = options
    return ScopeView(root, values, rows, customs, elements, choices, sections)


# --------------------------------------------------------- extensions (the nodes.yaml blocks)
#
# An extension is the SAME idea on a different file: its configuration lives inside ``nodes.yaml``,
# once on the NODE (``nodes.<node>.extensions.<Name>`` — the block its instances inherit) and once per
# INSTANCE (``nodes.<node>.instances.<instance>.extensions.<Name>``). The two schema blocks
# (``node_extensions_schema`` / ``instance_extensions_schema``) name the two partials, so the node
# block and the instance overrides each render from their own shape — a node's installation and API
# keys are NOT the instance's enabled/terrains keys.

def _repo_root():
    from pathlib import Path
    return Path(__file__).resolve().parents[2]


def extension_schema_parts(name: str):
    """``(schema, node_partial, instance_partial)`` for extension *name* — the two schema block NAMES.

    Read from ``extensions/schemas/extensions_schema.yaml``'s own ``mapping`` (never a table): the node
    block's ``<name>`` rule and the instance block's ``<name>`` rule each carry the ``include`` partial
    the shape comes from. Either is ``None`` when the extension is not declared in that block (a
    node-only or instance-only extension).
    """
    from . import catalog
    files = [str(catalog.EXTENSIONS_SCHEMA)]
    root = _repo_root() / "extensions"
    if root.is_dir():
        files += [str(path) for path in sorted(root.rglob("*_schema.yaml"))]
    schema = configgen.load_schema(catalog.EXTENSIONS_SCHEMA, schema_files=files)

    def partial(block_name: str) -> str | None:
        block = schema.partials.get(block_name)
        mapping = block.get("mapping") if isinstance(block, dict) else None
        rule = mapping.get(name) if isinstance(mapping, dict) else None
        if isinstance(rule, dict):
            include = rule.get("include")
            if include:
                return str(include)
        return None

    return schema, partial("node_extensions_schema"), partial("instance_extensions_schema")


def _frame_fields(schema, partial_name: "str | None") -> list:
    """The top-level fields a partial declares, on a ``DEFAULT`` frame (paths ``('DEFAULT', …)``)."""
    if not partial_name:
        return []
    frame = configgen.Schema(root={"type": "map", "mapping": {DEFAULT_TAG: {"include": partial_name}}},
                             partials=schema.partials, included_files=schema.included_files,
                             primary=schema.primary)
    tree = configgen.describe(frame)
    default = next((child for child in tree.children if child.key == DEFAULT_TAG), None)
    return list(default.children) if default is not None else []


def extension_save_tree(schema, name: str, node_partial, instance_partial):
    """The tree the extension page's SAVE resolves against — the node AND instance paths, one schema.

    It mirrors ``nodes.yaml``'s real structure around the extension (``<node>.extensions.<Name>`` and
    ``<node>.instances.<instance>.extensions.<Name>``) so ``apply_form``/``find_field`` place a posted
    field at its true document path. The frame trees are for RENDER; this one is for the write.

    THE SHAPE FALLS BACK, EXACTLY LIKE THE RENDER. ``node_partial or instance_partial`` and
    ``instance_partial or node_partial``: an extension declared in only ONE of the two schema blocks
    still has a block on the other path (the runtime honours a node-level block for an instance-scoped
    name — ``core/data/impl/serverimpl.py``'s ``load_extension`` merges it UNDER the instance block for
    ANY name), and that block carries the shape the one partial declares. Without the fallback the
    node-level path simply did not exist in the tree, so a filled-in node-wide block for an
    instance-only extension (``LogAnalyser``) could never be SAVED from the UI — the same scoping gap
    that made the fields render nowhere.
    """
    node_shape = node_partial or instance_partial
    instance_shape = instance_partial or node_partial
    node_mapping: dict = {}
    if node_shape:
        node_mapping["extensions"] = {"type": "map", "mapping": {name: {"include": node_shape}}}
    if instance_shape:
        node_mapping["instances"] = {"type": "map", "mapping": {"regex;(.+)": {"type": "map", "mapping": {
            "extensions": {"type": "map", "mapping": {name: {"include": instance_shape}}}}}}}
    root = {"type": "map", "mapping": {"regex;(.+)": {"type": "map", "mapping": node_mapping}}}
    composite = configgen.Schema(root=root, partials=schema.partials,
                                 included_files=schema.included_files, primary=schema.primary)
    return configgen.describe(composite)


def extension_blocks(document, name: str) -> list[dict]:
    """Every ``nodes.yaml`` block that configures extension *name*, in file order.

    One ``node`` block per node whose ``extensions`` carries the name, and one ``instance`` block per
    instance whose own ``extensions`` carries it — each with the node/instance names and its base path.
    """
    if not isinstance(document, dict):
        return []
    blocks: list[dict] = []
    for node, node_block in document.items():
        if not isinstance(node_block, dict):
            continue
        node_ext = node_block.get("extensions")
        if isinstance(node_ext, dict) and name in node_ext:
            blocks.append({"kind": "node", "node": str(node), "instance": None,
                           "base": (str(node), "extensions", name)})
        instances = node_block.get("instances")
        if isinstance(instances, dict):
            for instance, instance_block in instances.items():
                if not isinstance(instance_block, dict):
                    continue
                inst_ext = instance_block.get("extensions")
                if isinstance(inst_ext, dict) and name in inst_ext:
                    blocks.append({"kind": "instance", "node": str(node), "instance": str(instance),
                                   "base": (str(node), "instances", str(instance), "extensions", name)})
    return blocks


def build_extension_scopes(document, name: str, node_names=()):
    """The TABS for extension *name* plus the tree its save resolves against.

    Returns ``(scopes, save_tree)``. A node block's tab is the node's own name and renders the NODE
    partial; an instance block's tab is ``<node> + <instance>`` (or the plain instance name when the
    install has one node) and renders the INSTANCE partial.

    THE SHAPE FALLS BACK to the OTHER partial when the name is declared in only one of the two schema
    blocks. An instance-scoped-only extension (``LogAnalyser``) configured on the NODE — the operator
    switched it on centrally so every instance inherits the per-node default, which the runtime honours
    (``core/data/impl/serverimpl.py``'s ``load_extension`` merges the node-level block UNDER the
    instance block for ANY name) — carries the INSTANCE partial's fields addressed at the node-wide
    path (``<node>.extensions.<Name>``): the very shape the node page's own node-wide card renders and
    ``_node_wide_extensions`` offers. Without this the node block's tab rendered ZERO fields — the
    scoping gap that left the central override fillable only through Expert Mode YAML. The reverse
    (a node-scoped-only name on an instance) falls back the same way, so neither direction can render
    an empty block again.
    """
    schema, node_partial, instance_partial = extension_schema_parts(name)
    node_fields = _frame_fields(schema, node_partial or instance_partial)
    instance_fields = _frame_fields(schema, instance_partial or node_partial)
    multiple = len({str(item) for item in (node_names or ())}) > 1
    scopes = []
    for block in extension_blocks(document, name):
        if block["kind"] == "node":
            fields = [rebase(field, block["base"]) for field in node_fields]
            label = str(block["node"])
        else:
            fields = [rebase(field, block["base"]) for field in instance_fields]
            label = (f'{block["node"]} + {block["instance"]}' if multiple
                     else str(block["instance"]))
        scopes.append({"label": label, "kind": block["kind"], "node": block["node"],
                       "instance": block["instance"], "base": block["base"], "present": True,
                       "fields": fields})
    return scopes, extension_save_tree(schema, name, node_partial, instance_partial)


# --------------------------------------------------- the NODE selector and the per-node SLICE
#
# THE MECHANISM. A scoped file is edited ONE NODE at a time: the operator picks a node, sees only
# that node's blocks (its own block plus its instances' — the "1 + N" rule, never nodes x instances),
# and the Expert Mode editor shows only that node's slice of the file. The four helpers below are the
# WHOLE mechanism and are shared by every scoped view (a plugin's, a service's and an extension's
# file, and the per-node page for ``nodes.yaml``): nothing here is template-specific.
#
# WHY A SLICE IS A TOP-LEVEL-KEY SUB-MAPPING. Every scoped file is a top-level mapping whose keys
# PARTITION it by node: ``nodes.yaml`` is ``{<node>: {…}}``; a plugin/service file is
# ``{DEFAULT: …, <node>: {…}}``. A block's :func:`~services.webservice.scopes.build_scopes` record
# carries its ``base`` path, whose FIRST token is exactly that top-level key. So "the slice for a
# node" is well defined with no guessing: the sub-mapping holding the top-level keys the node's
# visible tabs address — ``{<node>: …}`` plus ``{DEFAULT: …}`` when the file carries a shared DEFAULT.
# Writing the slice back REPLACES only those keys, so every other top-level key (every other node's
# block) is left byte-for-byte as it was.

def node_options(scopes) -> list[str]:
    """The node names the scopes name, in first-seen order — the NODE SELECTOR's choices.

    A node-less scope (a plugin/service file's shared ``DEFAULT``, or a single-node reading where every
    key was read as an instance) contributes nothing, so the selector appears only where the file
    genuinely carries per-node blocks. A READING of the scopes, never a wish-list.
    """
    found: list[str] = []
    for scope in scopes:
        node = scope.get("node")
        if node is not None and str(node) not in found:
            found.append(str(node))
    return found


def scopes_for_node(scopes, node) -> list[dict]:
    """The scopes that belong to *node* — its own block and its instances' — plus the shared DEFAULT.

    This is the "1 + N" rule: one node's block (or, for a plugin/service file, the shared DEFAULT the
    node inherits) plus that node's instance blocks. A node-less install passes ``None``, and then the
    DEFAULT scopes come through untouched.
    """
    wanted = None if node is None else str(node)
    return [scope for scope in scopes
            if scope.get("kind") == "default"
            or (wanted is not None and str(scope.get("node") or "") == wanted)]


def scope_keys(scopes) -> list[str]:
    """The document's TOP-LEVEL keys the given scopes address, in document order and de-duplicated.

    A scope's top-level key is its ``base`` path's first token (``<node>``, or ``DEFAULT`` for the
    shared block). This is the exact, provable definition of a node's slice: the sub-mapping of the
    file the currently-shown tabs own.
    """
    keys: list[str] = []
    for scope in scopes:
        base = scope.get("base") or ()
        key = str(base[0]) if base else DEFAULT_TAG
        if key not in keys:
            keys.append(key)
    return keys


def document_slice(document, keys):
    """The sub-mapping of *document* holding only *keys*, in the given order, values kept as-is.

    Each value is the SAME round-trip object the file was parsed into (comments and formatting
    intact), so a slice dumped to YAML and parsed back round-trips. ``keys`` are the top-level keys
    from :func:`scope_keys`; a key the document does not carry is skipped.
    """
    if not isinstance(document, dict):
        return document
    from ruamel.yaml.comments import CommentedMap

    out = CommentedMap()
    for key in keys:
        if key in document:
            out[key] = document[key]
    return out


def merge_slice(document, slice_doc) -> None:
    """Write *slice_doc*'s top-level keys back into *document*, in place; every OTHER key is untouched.

    This is the write half of the mechanism: because the slice is a top-level-key sub-mapping, merging
    it changes exactly the keys it carries and leaves every other top-level key (every node the
    operator did not edit) byte-for-byte as the file held it. The caller REPLACES each named key's
    value with the slice's — comments the operator kept survive, since both sides are round-trip maps.
    """
    if not isinstance(document, dict) or not isinstance(slice_doc, dict):
        return
    for key in slice_doc:
        document[key] = slice_doc[key]
