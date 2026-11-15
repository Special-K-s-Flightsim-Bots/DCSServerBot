"""The public site's wiring documents must agree with what the build serves.

WHAT THIS SUITE PINS
--------------------
* every nav page id the sample config declares is a page this build serves, so a shipped sample
  cannot advertise a menu entry that answers 404;
* the sample states the two rules it exists to teach — an unknown slug answers 404, and enabling
  the block moves the console to ``/console`` while leaving the console dashboard at ``/`` when
  disabled;
* ``DEVELOPER.md`` lists every site route the page tree registers, and says no write route exists
  on that surface.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.webservice import site_pages  # noqa: E402

SAMPLE = REPO_ROOT / "samples" / "services" / "webservice.yaml"
DEVELOPER_MD = REPO_ROOT / "services" / "webservice" / "DEVELOPER.md"

#: a nav entry of the sample's commented ``nav:`` example, as the file writes it.
NAV_LINE = re.compile(r"^  #\s+- (overview|leaderboard|players|squadrons)\b", re.MULTILINE)


def nav_ids(sample_text: str) -> list[str]:
    """The page ids the sample's ``nav:`` example names, in its own order."""
    return NAV_LINE.findall(sample_text)


def test_every_sample_nav_entry_is_a_page_this_build_serves():
    declared = nav_ids(SAMPLE.read_text(encoding="utf-8"))
    assert declared, "the sample declares no nav entry at all"
    assert [page for page in declared if page not in site_pages.SERVED_NAV_IDS] == []


def test_the_sample_does_not_offer_the_unbuilt_squadrons_pages():
    assert "squadrons" not in nav_ids(SAMPLE.read_text(encoding="utf-8"))


def test_the_nav_reader_rejects_an_unserved_entry():
    """A sample that names the unbuilt page is detected, not read as fine."""
    broken = "  #     nav:\n  #       - overview\n  #       - squadrons\n"
    assert [page for page in nav_ids(broken) if page not in site_pages.SERVED_NAV_IDS] == [
        "squadrons"]


def test_the_sample_states_the_unknown_slug_rule_and_the_console_mount():
    text = SAMPLE.read_text(encoding="utf-8")
    assert "answers 404" in text
    assert "`/console`" in text
    assert "leaves the console dashboard at `/`" in text


def test_the_developer_document_lists_each_served_site_page():
    """Every page module's route is in the site table, under the site's own prefix."""
    document = DEVELOPER_MD.read_text(encoding="utf-8")
    for route in ("GET <site>/", "GET <site>/rankings", "GET <site>/players",
                  f"GET <site>{site_pages.PLAYER_PATH}/{{ucid}}", f"GET <site>{site_pages.ME_PATH}"):
        assert route in document, f"DEVELOPER.md does not list {route}"


def test_the_developer_document_says_the_surface_has_no_write_route():
    document = DEVELOPER_MD.read_text(encoding="utf-8")
    assert "There is NO write route on this surface." in document
