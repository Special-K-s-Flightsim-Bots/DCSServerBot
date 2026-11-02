"""Assemble the documentation site from the repository's markdown and metadata.

Reads the checkout read-only. Writes the assembled tree to ``build/`` and the
rendered site to ``site/``. Nothing outside this directory is modified.

Generated pages, and where their content comes from:

* ``reference/config/*.md``      -- ``schemas/*.yaml`` (desc/example per field)
* ``reference/commands.md``      -- command decorators in ``plugins/*/commands.py``
* ``reference/database.md``      -- ``sql/tables.sql`` and ``plugins/*/db/tables.sql``
* ``plugins/index.md``           -- one row per ``plugins/*/README.md``

Hand-written pages live in ``src/``; component pages are the in-repo READMEs,
copied unchanged so there is one source per page.
"""

from __future__ import annotations

import argparse
import ast
import os
import posixpath
import re
import shutil
import sys
from pathlib import Path

import yaml

DOCS = Path(__file__).resolve().parent.parent
REPO = DOCS.parent
BUILD = DOCS / "build"
SITE = DOCS / "site"
SRC = DOCS / "src"
ASSETS = DOCS / "assets"

COMPONENT_DIRS = ("plugins", "services", "extensions")
# Directories whose documents are published; only COMPONENT_DIRS get a nav section.
PUBLISH_DIRS = COMPONENT_DIRS + ("core", "reports")


# --------------------------------------------------------------------------- utils

def strip_h1(text: str) -> tuple[str, str]:
    """Return (title, body): the first level-1 heading and the text after it."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("# "):
            return line[2:].strip(), "\n".join(lines[index + 1:]).lstrip("\n")
    return "", text


def first_paragraph(text: str) -> str:
    """The first prose block, used as a one-line summary in index tables."""
    for block in text.split("\n\n"):
        block = block.strip()
        if block and not block.startswith(("#", ">", "-", "*", "!", "|", "`", "[")):
            return " ".join(block.split())
    return ""


def cell(text: str) -> str:
    """Escape a value for a markdown table cell."""
    text = (text or "").replace("|", "\\|").strip()
    return "<br>".join(line.replace("\t", "    ") for line in text.splitlines())


def slug(name: str) -> str:
    return name.lower().replace("_", "-")


# ------------------------------------------------------------------- component pages

def site_url(page: str) -> str:
    """The URL directory a built page is served from."""
    return "/" + page[: -len(".md")] + "/" if page.endswith(".md") else "/" + page.strip("/") + "/"


def to_site_page(rel: Path) -> str | None:
    """Site page for a repo-relative markdown path, or None when not published."""
    parts = rel.parts
    if len(parts) == 1 and rel.suffix == ".md":
        return f"{slug(rel.stem)}.md"
    if parts and parts[0] in PUBLISH_DIRS and rel.suffix == ".md":
        if len(parts) == 2 and parts[1] == "README.md":
            return f"{parts[0]}/index.md"
        if len(parts) == 3 and parts[2] == "README.md":
            return f"{parts[0]}/{slug(parts[1])}.md"
        # Any other document keeps its directory as a prefix, so two files of
        # the same name in different component folders cannot collide.
        flat = "-".join(slug(part) for part in parts[1:-1] + (rel.stem,))
        return f"{parts[0]}/{flat}.md"
    return None


LINK_RE = re.compile(r"(!?)\[([^\]]+)\]\(([^)\s]+)\)")


def rewrite_links(text: str, source: Path, page: str, repo: Path | None = None) -> str:
    """Point a component README's relative links at the site's own pages.

    The READMEs are written for GitHub's directory layout, where
    ``../lotatc/README.md`` is a sibling; on the site the same page lives at a
    different path, so each link is resolved against the repo and mapped. A link
    to a source file (``i18n.py``) becomes plain text: a site cannot serve it.
    """
    repo = repo or REPO

    def replace(match: re.Match) -> str:
        prefix, text, target = match.group(1), match.group(2), match.group(3)
        if target.startswith(("http", "#", "mailto")):
            return match.group(0)
        path, _, anchor = target.partition("#")
        resolved = Path(os.path.normpath(source.parent / path))
        if prefix == "!":
            # Images live under docs/assets, which the site serves as assets/.
            asset = resolved.parts[2:] if resolved.parts[:2] == ("docs", "assets") else None
            if asset:
                link = posixpath.relpath("assets/" + "/".join(asset), posixpath.dirname(page))
                return f"![{text}]({link})"
            return match.group(0)
        mapped = to_site_page(resolved)
        if not mapped:
            if (repo / resolved).is_file():
                return f"`{text}`"
            return match.group(0)
        link = posixpath.relpath(site_url(mapped).rstrip("/"), site_url(page))
        return f"[{text}]({link}/{('#' + anchor) if anchor else ''})"

    return LINK_RE.sub(replace, text)


def component_pages(repo: Path, build: Path, settings: dict) -> dict[str, list[tuple[str, str]]]:
    """Copy every component README into the build tree; return its nav entries."""
    excluded = {kind: set(names) for kind, names in (settings.get("exclude") or {}).items()}
    nav: dict[str, list[tuple[str, str]]] = {kind: [] for kind in PUBLISH_DIRS}
    for kind in PUBLISH_DIRS:
        root = repo / kind
        if not root.is_dir():
            continue
        for readme in sorted(root.glob("*/README.md")):
            name = readme.parent.name
            if name in excluded.get(kind, set()):
                continue
            page = f"{kind}/{slug(name)}.md"
            target = build / page
            target.parent.mkdir(parents=True, exist_ok=True)
            text = rewrite_links(readme.read_text(), readme.relative_to(repo), page)
            text += "\n".join(component_config(repo, kind, name, settings, text))
            target.write_text(text)
            nav[kind].append((name, page))
        # Secondary documents of a component (API.md, DEVELOPER.md, I18N.md, a
        # directory's own README) are published too: links point at them.
        for doc in sorted(root.glob("**/*.md")):
            rel = doc.relative_to(repo)
            if len(rel.parts) == 3 and rel.name == "README.md":
                continue  # already published as the component's own page
            page = to_site_page(rel)
            if not page:
                continue
            target = build / page
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rewrite_links(doc.read_text(), rel, page))
    return nav


def grouped_nav(entries: list[tuple[str, str]], groups: dict[str, list[str]],
                label) -> list:
    """Turn flat component entries into named groups, keeping the group order.

    A component that appears in no group is collected under ``Other`` so nothing
    silently disappears from the sidebar when the list is edited.
    """
    by_name = {name: page for name, page in entries}
    nav: list = []
    used: set[str] = set()
    for group, members in (groups or {}).items():
        items = [{label(name): by_name[name]} for name in members if name in by_name]
        if items:
            nav.append({group: items})
            used.update(name for name in members if name in by_name)
    rest = [{label(name): page} for name, page in entries if name not in used]
    if rest:
        nav.append({"Other": rest})
    return nav


def component_index(repo: Path, build: Path, kind: str, entries: list[tuple[str, str]]) -> None:
    """Build the overview page for one component kind from its READMEs."""
    rows = [
        "| Name | Summary |",
        "|:-----|:--------|",
    ]
    for name, page in entries:
        text = (repo / kind / name / "README.md").read_text()
        _, body = strip_h1(text)
        summary = rewrite_links(cell(first_paragraph(body)), Path(kind) / name / "README.md",
                                f"{kind}/index.md")
        rows.append(f"| [{name.title()}]({slug(name)}.md) | {summary} |")
    header = f"# {kind.title()}\n\nEvery {kind[:-1]} ships a page of its own; this list is generated from the READMEs.\n\n"
    (build / kind / "index.md").write_text(header + "\n".join(rows) + "\n")


# ----------------------------------------------------------------- config reference

def load_schema(document: dict) -> tuple[dict, dict]:
    """Split a schema file into (root node, named definitions).

    A pykwalify file may hold named ``schema;<name>`` blocks only — then each is
    a group — or those blocks plus one real root, in which case the root is what
    the file describes and the named blocks are its definitions.
    """
    definitions: dict[str, dict] = {}
    root: dict = {}
    for key, value in (document or {}).items():
        name = str(key)
        if name.startswith("schema;"):
            # Keyed by the name an `include:` refers to, which carries the
            # suffix; groups strip it for display only.
            definitions[name[len("schema;"):]] = value or {}
        else:
            root[name] = value
    if root:
        return root, definitions
    labels = {n[:-len("_schema")] if n.endswith("_schema") else n: dict(v, _group=True)
              for n, v in definitions.items()}
    if len(definitions) == 1:
        return next(iter(definitions.values())), definitions
    return {"type": "map", "mapping": labels}, definitions


def schema_root(document: dict) -> dict:
    """The root node of a schema file, named definitions expanded or grouped."""
    return load_schema(document)[0]


def flatten(node: dict, prefix: str = "", top: str = "",
            definitions: dict | None = None, seen: frozenset = frozenset()) -> list[dict]:
    """Flatten a pykwalify schema into one row per addressable key.

    ``top`` carries the first-level group so a regex key such as ``regex;(.+)``
    stays usable as a section heading. ``include: <name>`` is resolved against
    the file's named definitions; a definition that includes itself stops at the
    repeat instead of recursing.
    """
    definitions = definitions or {}
    rows: list[dict] = []
    mapping = node.get("mapping") or {}
    for key, child in mapping.items():
        key = str(key)
        include = child.get("include")
        if include and include in definitions:
            if include in seen:
                continue
            overrides = {k: v for k, v in child.items() if k != "include"}
            child = dict(definitions[include])
            child.update(overrides)
            seen = seen | {include}
        # A named group (a schema file holding several schemas) is a heading,
        # not a key: its members are rendered one level up.
        if child.get("_group"):
            rows.extend(flatten(child, prefix, key, definitions, seen))
            continue
        label = key
        if key.startswith("regex;"):
            label = f"<{key[len('regex;'):]}>"
        path = f"{prefix}.{label}" if prefix else label
        group = top or path
        row = _row(path, child)
        row["top"] = group
        rows.append(row)
        rows.extend(flatten(child, path, group, definitions, seen))

    sequence = node.get("sequence")
    if isinstance(sequence, dict):
        rows.extend(flatten(sequence, f"{prefix}[]", top, definitions, seen))
    elif isinstance(sequence, list):
        for element in sequence:
            if isinstance(element, dict) and element.get("mapping"):
                rows.extend(flatten(element, f"{prefix}[]", top, definitions, seen))
    return rows


def _row(path: str, node: dict) -> dict:
    kind = node.get("type", "")
    if kind == "seq":
        kind = "list"
    elif kind == "map":
        kind = "map"
    constraints = []
    if node.get("enum"):
        constraints.append("one of: " + ", ".join(str(e) for e in node["enum"]))
    rng = node.get("range")
    if isinstance(rng, dict) and rng:
        constraints.append("range: " + ", ".join(f"{k} {v}" for k, v in rng.items()))
    return {
        "path": path,
        "type": kind + (" (required)" if node.get("required") else ""),
        "desc": node.get("desc", ""),
        "extra": "; ".join(constraints),
        "example": node.get("example", ""),
    }


def schema_markdown(repo: Path, document: dict, definitions: dict, name: str,
                    settings: dict, level: int = 2) -> list[str]:
    """Render one schema as headings and key tables.

    ``level`` is the heading depth of the first section, so the same rendering
    works as a page of its own (2) or inside a component page (3).
    """
    block_notes = settings.get("block_notes") or {}
    rows = flatten(document, definitions=definitions)
    if not rows:
        return []
    # A schema whose root key is a regex describes a file of repeated blocks:
    # the placeholder is the block name, not something to type.
    is_block = rows[0]["path"].startswith("<")
    sections: dict[str, list[dict]] = {}
    for row in rows:
        # A key with nested keys gets a heading of its own; a flat key joins the
        # page-level table, so a schema of plain keys is one table.
        nested = "." in row["path"] or "[]" in row["path"]
        sections.setdefault(row["top"] if nested else "", []).append(row)

    heading = "#" * level
    out: list[str] = []
    if document.get("desc"):
        out += [document["desc"], ""]
    for head, head_rows in sections.items():
        placeholder = next(
            (match for match in (re.match(r"^(<[^>]+>)\.", row["path"]) for row in head_rows)
             if match), None)
        section_prefix = placeholder.group(1) + "." if placeholder else ""
        if is_block and section_prefix:
            note = block_notes.get(name) or "Each top-level key is one block."
            out += [f"{heading} Blocks", "", note, ""]
            sample = repo / "samples" / f"{name}.yaml"
            if sample.is_file():
                excerpt = "\n".join(sample.read_text().splitlines()[:16])
                out += ["```yaml", excerpt, "```", "",
                        f"The full commented sample is `samples/{name}.yaml`.", ""]
        else:
            out += ([f"{heading} `{head}`", ""] if head else [])
            if section_prefix:
                out += [f"One entry per name, keyed by that name "
                        f"(`{section_prefix.rstrip('.')}`).", ""]
        out += ["| Key | Type | Description | Example |",
                "|:----|:-----|:------------|:--------|"]
        for row in head_rows:
            path = row["path"]
            if section_prefix:
                if path == section_prefix.rstrip("."):
                    continue  # the container itself, described above
                path = path[len(section_prefix):]
            description = cell(row["desc"])
            if row["extra"]:
                description += f" *({cell(row['extra'])})*"
            out.append(
                f"| `{cell(path)}` | {cell(row['type'])} | {description} "
                f"| <code>{cell(row['example'])}</code> |"
            )
        out.append("")
    return out


def config_pages(repo: Path, build: Path, settings: dict) -> list[tuple[str, str]]:
    """One page per root schema file."""
    skip = set(settings.get("skip_schemas") or [])
    nav = []
    for schema_file in sorted((repo / "schemas").glob("*.yaml")):
        name = schema_file.stem.replace("_schema", "")
        if name in skip:
            continue
        document, definitions = load_schema(yaml.safe_load(schema_file.read_text()) or {})
        body = schema_markdown(repo, document, definitions, name, settings, level=2)
        if not body:
            continue
        out = [f"# {name.replace('_', ' ').title()} reference", ""] + body
        page = build / "reference" / "config" / f"{name}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("\n".join(out))
        nav.append((name.replace("_", " ").title(), f"reference/config/{name}.md"))
    return nav


def component_config(repo: Path, kind: str, name: str, settings: dict, text: str = "") -> list[str]:
    """The configuration section of a component page, from its own schema."""
    schema_dir = repo / kind / name / "schemas"
    if not schema_dir.is_dir():
        return []
    out: list[str] = []
    for schema_file in sorted(schema_dir.glob("*.yaml")):
        document, definitions = load_schema(yaml.safe_load(schema_file.read_text()) or {})
        stem = schema_file.stem.replace("_schema", "")
        out += schema_markdown(repo, document, definitions, stem, settings, level=3)
    if not out:
        return []
    heading = "## Configuration reference"
    if heading in text.splitlines():
        heading = "## Settings"
    return ["", "---", "", heading, ""] + out


# ---------------------------------------------------------------- command reference

def _call_string(node: ast.AST) -> str:
    """The literal text of a string, ``_('x')`` marker, or an f-string placeholder."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and node.args:
        return _call_string(node.args[0])
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "{…}" for part in node.values
        )
    return ""


def _command_decorator(decorator: ast.AST) -> tuple[str, str, str] | None:
    """(group, name, description) for a command decorator, else None."""
    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
        method = decorator.func.attr
        if method != "command":
            return None
        owner = decorator.func.value
        if isinstance(owner, ast.Name):
            group = owner.id
        elif isinstance(owner, ast.Attribute):
            group = owner.attr
        else:
            group = "?"
        if group in {"app_commands", "commands", "hybrid"}:
            group = ""
        kwargs = {kw.arg: kw.value for kw in decorator.keywords if kw.arg}
        name = _call_string(kwargs["name"]) if "name" in kwargs else ""
        description = _call_string(kwargs["description"]) if "description" in kwargs else ""
        return group, name, description
    return None


def _roles_of(function: ast.AST) -> str:
    roles = []
    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
            if decorator.func.attr == "app_has_role":
                roles += [_call_string(arg) for arg in decorator.args if _call_string(arg)]
    return ", ".join(roles)


def _type_of(annotation: ast.AST | None) -> str:
    """The option's type as a reader would recognise it.

    ``app_commands.Transform[Server, utils.ServerTransformer(...)]`` is a
    ``Server`` to the person typing the command, not a transformer signature.
    """
    if annotation is None:
        return ""
    raw = ast.unparse(annotation)
    if "Transform[" in raw:
        inner = raw.split("Transform[", 1)[1]
        depth = 0
        for index, char in enumerate(inner):
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif char == "," and depth == 0:
                inner = inner[:index]
                break
        return inner.replace("discord.", "").replace("utils.", "").strip()
    if "Range[" in raw:
        bounds = re.search(r"Range\[([^\]]*)\]", raw)
        return "number" + (f" ({bounds.group(1)})" if bounds else "")
    raw = raw.replace("discord.", "").replace("app_commands.", "").replace("utils.", "")
    return raw.strip()


def _options_of(function: ast.AST) -> str:
    arguments = function.args
    positional = arguments.args[2:]  # skip self and interaction
    defaults = [None] * (len(positional) - len(arguments.defaults)) + list(arguments.defaults)
    options = []
    for arg, default in zip(positional, defaults):
        kind = _type_of(arg.annotation)
        text = f"`{arg.arg}`" + (f" ({kind})" if kind else "")
        if default is not None:
            text += f" = {ast.unparse(default)}"
        options.append(text)
    return ", ".join(options)


def command_rows(module: Path) -> list[dict]:
    tree = ast.parse(module.read_text())
    rows = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for function in node.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in function.decorator_list:
                found = _command_decorator(decorator)
                if not found:
                    continue
                group, explicit_name, description = found
                name = explicit_name or function.name.replace("_", "-")
                label = f"/{group} {name}".replace("  ", " ") if group else f"/{name}"
                rows.append({
                    "command": label,
                    "roles": _roles_of(function),
                    "description": description or (ast.get_docstring(function) or "").split("\n")[0],
                    "options": _options_of(function),
                })
                break
    return rows


def command_page(repo: Path, build: Path) -> None:
    out = ["# Command reference", "",
           "Generated from the command decorators in `plugins/*/commands.py`.", ""]
    for module in sorted((repo / "plugins").glob("*/commands.py")):
        rows = command_rows(module)
        if not rows:
            continue
        out += [f"## {module.parent.name.title()}", "",
                "| Command | Roles | Description | Options |",
                "|:--------|:------|:------------|:--------|"]
        for row in rows:
            out.append(
                f"| `{cell(row['command'])}` | {cell(row['roles'])} | "
                f"{cell(row['description'])} | {cell(row['options'])} |"
            )
        out.append("")
    page = build / "reference" / "commands.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("\n".join(out))


# --------------------------------------------------------------- database reference

TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\(", re.I)


def table_definitions(text: str):
    """Yield (table, column block) for every CREATE TABLE in a SQL text.

    A statement is delimited by its parentheses, not by a closing ``);`` on its
    own line: a one-line table would otherwise swallow the statements after it.
    """
    for match in TABLE_RE.finditer(text):
        depth, start = 0, match.end() - 1
        for index in range(start, len(text)):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    yield match.group(1), text[start + 1:index]
                    break


def database_page(repo: Path, build: Path) -> None:
    sources = [(repo / "sql" / "tables.sql", "core")]
    for module_sql in sorted((repo / "plugins").glob("*/db/tables.sql")):
        sources.append((module_sql, module_sql.parent.parent.name))

    out = ["# Database reference", "",
           "Generated from the table definitions in `sql/tables.sql` and "
           "`plugins/*/db/tables.sql`. Plugin tables are created by the plugin's own migration.", ""]
    for path, owner in sources:
        if not path.is_file():
            continue
        for table, body in table_definitions(path.read_text()):
            out += [f"## `{table}`", "", f"Defined by: {owner}", "",
                    "| Column | Type | Notes |", "|:-------|:-----|:------|"]
            for line in body.splitlines():
                line = line.strip().rstrip(",")
                if not line:
                    continue
                if line.upper().startswith(("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CONSTRAINT")):
                    out.append(f"| | | {cell(line)} |")
                    continue
                parts = line.split(None, 1)
                column = parts[0]
                rest = parts[1] if len(parts) > 1 else ""
                kind = rest.split()[0] if rest else ""
                note = rest[len(kind):].strip()
                out.append(f"| `{cell(column)}` | {cell(kind)} | {cell(note)} |")
            out.append("")
    page = build / "reference" / "database.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("\n".join(out))


# ------------------------------------------------------------------------- assembly

# GitHub's alert syntax is not Markdown: Material renders it as a quote with a
# literal "[!NOTE]" in it. Each block becomes an admonition of the same meaning,
# titled with the emoji a reader recognises from GitHub.
ACRONYMS = {"dks", "srs", "grpc", "ups", "motd", "dsmc"}

ALERTS = {
    "NOTE": ("info", "Note"),
    "TIP": ("tip", "Tip"),
    "IMPORTANT": ("important", "Important"),
    "WARNING": ("warning", "Warning"),
    "CAUTION": ("danger", "Caution"),
}
ALERT_RE = re.compile(r"^>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$")


def convert_alerts(path: Path) -> bool:
    """Convert GitHub alert blockquotes to admonitions. Returns True when changed."""
    lines = path.read_text().splitlines()
    out: list[str] = []
    changed = False
    index = 0
    fence = None
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        if stripped.startswith("```"):
            fence = None if fence else stripped
        match = ALERT_RE.match(line) if not fence else None
        if not match:
            out.append(line)
            index += 1
            continue
        kind, title = ALERTS[match.group(1)]
        body: list[str] = [match.group(2).strip()] if match.group(2).strip() else []
        index += 1
        while index < len(lines) and lines[index].lstrip().startswith(">"):
            body.append(lines[index].lstrip()[1:].removeprefix(" "))
            index += 1
        while body and not body[-1]:
            body.pop()
        out.append(f'!!! {kind} "{title}"')
        out.append("")
        out += [f"    {row}" if row else "" for row in body]
        out.append("")
        changed = True
    if changed:
        path.write_text("\n".join(out) + "\n")
    return changed


def label(name: str) -> str:
    """Sidebar label for a component directory name."""
    if name in ACRONYMS:
        return name.upper()
    return name.replace("_", " ").title()


def page_title(path: Path) -> str:
    """The first level-1 heading of a page, used as its sidebar label."""
    for line in path.read_text().splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem.replace("_", " ").title()


def authored_nav(build: Path, folder: str) -> list:
    """Sidebar entries for an authored folder, labelled by each page's H1."""
    directory = build / folder
    if not directory.is_dir():
        return []
    return [{page_title(page): f"{folder}/{page.name}"}
            for page in sorted(directory.glob("*.md")) if page.name != "index.md"]


def build_site(repo: Path, mkdocs: str | None = None) -> int:
    build = DOCS / "build"
    site = DOCS / "site"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)

    settings = {}
    settings_file = DOCS / "site.yml"
    if settings_file.is_file():
        settings = yaml.safe_load(settings_file.read_text()) or {}

    shutil.copytree(SRC, build, dirs_exist_ok=True)
    if ASSETS.is_dir():
        shutil.copytree(ASSETS, build / "assets", dirs_exist_ok=True)

    # Root-level documents of the repo (architecture, multi-node, …) are pages
    # of their own; README.md is the landing page and comes from src/.
    titles = settings.get("guide_titles") or {}
    guides = []
    for doc in sorted(repo.glob("*.md")):
        if doc.stem.lower() == "readme":
            continue
        page = f"{slug(doc.stem)}.md"
        (build / page).write_text(rewrite_links(doc.read_text(), doc.relative_to(repo), page))
        guides.append({titles.get(page) or page_title(build / page): page})

    component_nav = component_pages(repo, build, settings)
    for kind, entries in component_nav.items():
        if entries:
            component_index(repo, build, kind, entries)

    config_nav = config_pages(repo, build, settings)
    command_page(repo, build)
    database_page(repo, build)

    guide_entries = guides + authored_nav(build, "guides")
    nav = [
        {"Home": "index.md"},
        {"Getting started": list(settings.get("getting_started") or ["install.md", "configure.md"])},
    ]
    if guide_entries:
        nav.append({"Guides": guide_entries})
    for kind, entries in component_nav.items():
        if not entries:
            continue
        overview = {f"{kind.title()} overview": f"{kind}/index.md"}
        if kind == "plugins":
            groups = grouped_nav(entries, settings.get("plugins") or {}, label)
            nav.append({kind.title(): [overview] + groups})
        else:
            flat = [{label(name): page} for name, page in entries]
            nav.append({kind.title(): [overview] + flat})
    nav.append({"Reference": [
        {"Configuration": [{name: page} for name, page in config_nav]},
        "reference/commands.md",
        "reference/database.md",
    ]})
    for section in ("developers", "community"):
        entries = authored_nav(build, section)
        if entries:
            nav.append({section.title(): entries})

    title = f"{repo.name} documentation (generated)"
    config = {
        "site_name": title,
        "site_description": "Manage your DCS World servers",
        "docs_dir": str(build),
        "site_dir": str(site),
        "use_directory_urls": True,
        "theme": {
            "name": "material",
            "language": "en",
            "favicon": "assets/favicon.ico",
            "features": ["navigation.indexes", "navigation.top", "navigation.tracking",
                         "search.suggest", "search.highlight", "content.code.copy", "toc.follow"],
            "palette": [{"scheme": "slate", "primary": "indigo", "accent": "indigo"}],
        },
        "markdown_extensions": ["admonition", "tables", "attr_list", "def_list",
                                "pymdownx.details", "pymdownx.superfences", "pymdownx.tabbed"],
        "extra_css": ["assets/css/extra.css"],
        "nav": nav,
    }
    config_path = DOCS / "mkdocs.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))

    pages = len(list(build.rglob("*.md")))
    alerts = sum(convert_alerts(page) for page in build.rglob("*.md"))
    print(f"assembled {pages} pages in {build} ({alerts} with alerts converted)")
    if mkdocs:
        import subprocess
        result = subprocess.run([mkdocs, "build", "-q", "-f", str(config_path)],
                                capture_output=True, text=True)
        print(result.stdout.strip() or result.stderr.strip())
        return result.returncode
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", nargs="?", default=str(REPO), help="path to the DCSServerBot checkout")
    parser.add_argument("--mkdocs", help="mkdocs executable; builds the site when given")
    args = parser.parse_args(argv)
    return build_site(Path(args.repo).resolve(), args.mkdocs)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
