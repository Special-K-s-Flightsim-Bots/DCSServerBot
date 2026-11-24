"""A NEW preset file: what the check decides is the value the WRITER receives.

:func:`presets_page.preset_new_file` answers the destination of a new preset file, and the save hands
that answer straight to ``configwrite.patch_config_file``. Two ends resolved for the check but the
unresolved path handed on leaves the write free to follow a different route than the one that was
judged — a swapped link between the check and the write lands outside the config directory. The answer
is therefore ``core.utils.resolve_within``'s own value, pinned here by the path the writer was given
(not by the page answering 303).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import needs_symlinks, symlinks_available

from services.webservice.pages import presets as presets_page
from test_webui_presets_new_file import preset_file, post_new, wizard_url  # noqa: F401
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, fleet, sign_in,
)


# --------------------------------------------------------------- the answer is the resolved path


@needs_symlinks
def test_a_new_file_below_a_link_inside_the_config_dir_is_the_resolved_path(tmp_path):
    """``presets/`` as a link that stays inside: accepted, and the answer is the file it really names."""
    config = tmp_path / "config-dir"
    config.mkdir()
    real = config / "presets-real"
    real.mkdir()
    (config / "presets").symlink_to(real, target_is_directory=True)

    path, refusal, _params = presets_page.preset_new_file(config, "config/presets/night.yaml")

    assert refusal == "", refusal
    assert path == Path(os.path.realpath(real / "night.yaml")), path
    assert path != config / "presets" / "night.yaml", (
        "the link component is still in the path the writer would receive")


@needs_symlinks
def test_a_new_file_below_a_link_out_of_the_config_dir_is_refused(tmp_path):
    """The negative control on the same shape: a link that leaves the tree still refuses."""
    outside = tmp_path / "outside"
    outside.mkdir()
    config = tmp_path / "config-dir"
    config.mkdir()
    (config / "presets").symlink_to(outside, target_is_directory=True)

    path, refusal, _params = presets_page.preset_new_file(config, "config/presets/night.yaml")

    assert path is None
    assert refusal == presets_page.PRESETS_REFUSED_NEW_FILE_ESCAPE
    assert not (outside / "night.yaml").exists()


@pytest.mark.asyncio
@needs_symlinks
async def test_the_step_names_the_file_as_typed_not_the_links_target(config_dir, clean_registry):
    """The resolved path is for the writer; the page keeps naming the file the operator typed."""
    real = config_dir / "presets-real"
    real.mkdir()
    (config_dir / "presets").symlink_to(real, target_is_directory=True)
    preset_file(config_dir)

    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await http.get(wizard_url("Night", step="review",
                                             new_file="config/presets/night.yaml"))
        html = response.text

    named = presets_page.WIZARD_DEST_NEW_ONE.replace("%(file)s", "config/presets/night.yaml")
    assert named in html, "the step does not name the file it will create"
    assert "presets-real" not in html, "the step printed the link's target instead of the file"
    assert not (real / "night.yaml").exists(), "the GET creates nothing"


# -------------------------------------------------------- the value the save's writer received


def _spy(monkeypatch) -> list[Path]:
    """Record every path handed to the ONE write, then perform the real write."""
    seen: list[Path] = []
    writer = presets_page.configwrite.patch_config_file

    def spy(path, *args, **kwargs):
        seen.append(Path(path))
        return writer(path, *args, **kwargs)

    monkeypatch.setattr(presets_page.configwrite, "patch_config_file", spy)
    return seen


@pytest.mark.asyncio
@needs_symlinks
async def test_the_write_receives_the_resolved_path(config_dir, clean_registry, monkeypatch):
    """Through the real app: ``patch_config_file`` is called with the RESOLVED destination."""
    real = config_dir / "presets-real"
    real.mkdir()
    (config_dir / "presets").symlink_to(real, target_is_directory=True)
    preset_file(config_dir)
    seen = _spy(monkeypatch)

    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_new(http, "Night", new_file="config/presets/night.yaml",
                               change={"temperature": "3"})

    assert reply.status_code == 303, reply.text
    assert seen, "the save never reached the writer"
    for path in seen:
        assert Path(os.path.realpath(path)) == path, f"the writer was handed an unresolved path: {path}"
    written = real / "night.yaml"
    assert written.is_file(), "the resolved destination is where the file landed"
    assert seen[-1] == written, seen


@pytest.mark.asyncio
async def test_an_offered_file_is_still_written_where_it_lives(config_dir, clean_registry,
                                                               monkeypatch):
    """The control: a destination the offer names keeps writing the file the offer named."""
    path = preset_file(config_dir)
    seen = _spy(monkeypatch)

    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_new(http, "Night", file="config/presets.yaml",
                               change={"temperature": "3"})

    assert reply.status_code == 303, reply.text
    assert seen and seen[-1] == path, seen
