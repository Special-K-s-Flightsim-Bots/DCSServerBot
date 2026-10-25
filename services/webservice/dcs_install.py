"""A node's DCS installation, and the ONE path rule for a file inside it.

The installation root is ``nodes.<name>.locals.DCS.installation`` — the root every DCS-relative path
in this tree is resolved against (``core/mizfile.py`` reads the terrains folder the same way). Two
reads of the console resolve a path under it, and they must resolve it the SAME way:

* the per-server preset THUMBNAIL (``pages/server_detail``), whose name arrives out of a running
  mission's ``clouds.preset.thumbnailName``;
* the per-node cloud-preset LIST (``pages/presets``), whose path is a constant relative path.

THIS MODULE IS THE ONE HOME of that rule: the installation root lookup, the name-shape refusal that
keeps a served file inside the installation, and the 4 MiB read cap. A path that fails the shape test
is REFUSED (``None``/``''``) before anything is opened, which is what keeps a route that hands node
files back from becoming a general file reader.

The path join writes FORWARD slashes even on Windows: the path is opened on the NODE, which may be the
other platform, and both Windows and POSIX accept ``/`` — while a backslash is a literal character in
a POSIX filename, so a name carrying one is read as a separator instead.
"""
from __future__ import annotations

import os
import re

__all__ = ["BACKSLASH", "PRESET_IMAGE_MAX_BYTES", "PRESET_IMAGE_MEDIA_TYPE",
           "installation_root", "installation_file", "preset_image_file"]

#: one backslash, by codepoint — a hostile name may use either separator.
BACKSLASH = chr(92)

#: The most this console hands over as a DCS installation file. A DCS preset picture is a small PNG
#: (tens of KB); the cap is generous so a replaced asset still shows, and it refuses a reported file
#: that is not a picture — never streaming an arbitrary binary out of the DCS installation.
PRESET_IMAGE_MAX_BYTES = 4 * 1024 * 1024
PRESET_IMAGE_MEDIA_TYPE = "image/png"

#: an absolute POSIX path or a Windows drive letter: a name shaped like this names a file OUTSIDE the
#: installation, so it is refused before anything is opened.
_ABSOLUTE = re.compile(r"^[A-Za-z]:")


def _join(root: str, parts: list[str]) -> str:
    """*parts* joined under *root*, FORWARD-slashed (the path is opened on the node's own platform)."""
    install = os.path.normpath(os.path.expandvars(root)).replace(os.sep, "/").rstrip("/")
    return f"{install}/{'/'.join(parts)}"


def installation_root(node) -> str:
    """The DCS installation directory ON *node*, or ``''``.

    ``locals`` is a node's own merged configuration, so this is a tolerant read: a node that has not
    reported one yields ``''`` and the caller refuses rather than guessing a directory.
    """
    node_locals = getattr(node, "locals", None)
    if not isinstance(node_locals, dict):
        return ""
    dcs = node_locals.get("DCS")
    root = dcs.get("installation") if isinstance(dcs, dict) else None
    return str(root) if root else ""


def installation_file(node, relative: str) -> str:
    """The node-relative *relative* path under *node*'s installation, or ``''`` when it cannot be used.

    An absent installation root, an empty name, an absolute name, a drive letter and any ``..``
    component yield ``''``: the caller refuses plainly rather than reading something else.
    """
    root = installation_root(node)
    reported = str(relative or "")
    if not root or not reported:
        return ""
    if reported.startswith("/") or reported.startswith(BACKSLASH) or _ABSOLUTE.match(reported):
        return ""
    parts = [part for part in reported.replace(BACKSLASH, "/").split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        return ""
    return _join(root, parts)


def preset_image_file(node, thumbnail: str) -> str | None:
    """The thumbnail's path ON *node*, or ``None`` when the reported name cannot be used.

    The name comes from the MISSION (``clouds.preset.thumbnailName``), so it is operator data rather
    than a request parameter — and it is still REFUSED rather than resolved when it is not a plain
    relative PNG path. An absolute path, a Windows drive, a UNC share or any ``..`` component would
    let a mission file name a file outside the DCS installation, which is what keeps the thumbnail
    route from being a general file reader; the shape test is therefore on the REPORTED name, not on
    a normalized comparison alone.
    """
    root = installation_root(node)
    reported = str(thumbnail or "")
    if not root or not reported:
        return None
    if reported.startswith("/") or reported.startswith(BACKSLASH) or _ABSOLUTE.match(reported):
        return None
    parts = [part for part in reported.replace(BACKSLASH, "/").split("/") if part not in ("", ".")]
    if not parts or ".." in parts or not parts[-1].lower().endswith(".png"):
        return None
    return _join(root, parts)
