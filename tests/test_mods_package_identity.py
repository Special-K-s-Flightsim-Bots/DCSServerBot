"""The package identity a request carries toward the filesystem: one leaf name, or a refusal.

Every mod write names a package and a version, and the service joins that pair onto a package-folder
path (``<folder>/<target>/<package>_v<version>``) before reading, writing and finally removing it
recursively. So the pair is an IDENTITY, not free text: it has to answer to a row the target actually
holds, and a request that carries a path instead must be refused at the seam rather than handed to
the service.

These pins drive the REAL application (the production factory, the shell, the local login backend,
the real ``modmanager`` actions and a stub cluster) over the in-process ASGI client, with a
RECORDING service double at the domain boundary — the shape a signed-in Admin's browser produces:
the removal dialog mints its one-shot token, and the form that follows carries whatever payload the
request chooses.
"""
from __future__ import annotations

import re

import pytest

# the console harness this surface is driven through — one app factory, two doubles, one login
from test_webui_mods_pages import (  # noqa: F401 - the fixtures are used as parameters
    ENTRIES,
    NODE,
    ROOT_ROWS,
    build,
    client,
    config_dir,
    fleet,
    no_service,
    open_node_page,
    service,
    sign_in,
    token_of,
)

from services.webservice.pages import mods as mods_page

INSTALLED_PACKAGE = "Community_A-4E-C"
INSTALLED_VERSION = "2.4.1"

#: a package name that is a LOCATION instead of one folder entry
TRAVERSING_PACKAGES = ("../../evil", "../x", "..", "/etc/passwd", "C:\\Windows\\System32\\cmd.exe",
                       "\\\\server\\share\\x", "mods/../../evil", "a/b")

#: the same shapes in the version half
TRAVERSING_VERSIONS = ("../x", "..", "/etc/passwd", "../../x", "1.0/../../x", "1.0\\..\\x")


async def _token(http, *, owner=mods_page.NODE_OWNER, target=NODE, verb=mods_page.REMOVE,
                 package=INSTALLED_PACKAGE, version=INSTALLED_VERSION, url=""):
    """``(confirm token, csrf token)`` minted by the REAL dialog of one verb."""
    page = await open_node_page(http)
    dialog = await http.post(mods_page.dialog_url(owner, target, verb),
                             data={"_csrf_token": token_of(page), "owner": owner, "target": target,
                                   "package": package, "version": version, "url": url})
    assert dialog.status_code == 200, dialog.text
    match = re.search(r'name="_confirm_token" value="([^"]*)"', dialog.text)
    assert match, "the dialog minted no one-shot token"
    return match.group(1), token_of(dialog.text)


async def _run(http, verb, token, csrf, *, owner=mods_page.NODE_OWNER, target=NODE, **fields):
    """POST one action route with the token its own dialog minted and *fields* as the payload."""
    data = {"_csrf_token": csrf, "_confirm_token": token, "owner": owner, "target": target}
    data.update(fields)
    return await http.post(mods_page.run_url(owner, target, verb), data=data)


# ---------------------------------------------------------------------- the removal's identity

@pytest.mark.asyncio
@pytest.mark.parametrize("package", TRAVERSING_PACKAGES)
async def test_a_removal_of_a_traversing_package_is_refused_and_reaches_no_service(
        config_dir, service, package):
    """A package that is a path is refused at the seam: a sentence, no service call, no 500."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        token, csrf = await _token(http)
        response = await _run(http, mods_page.REMOVE, token, csrf,
                              package=package, version=INSTALLED_VERSION)
        after = await open_node_page(http)
    assert response.status_code == 403, response.text
    assert double.writes == [], "a refused removal never reached the service"
    assert "Traceback" not in response.text and "Traceback" not in after
    assert "Nothing was removed" in after or "not installed" in after


@pytest.mark.asyncio
@pytest.mark.parametrize("version", TRAVERSING_VERSIONS)
async def test_a_removal_of_a_traversing_version_is_refused_and_reaches_no_service(
        config_dir, service, version):
    """The version half travels the same way and is refused the same way."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        token, csrf = await _token(http)
        response = await _run(http, mods_page.REMOVE, token, csrf,
                              package=INSTALLED_PACKAGE, version=version)
    assert response.status_code == 403, response.text
    assert double.writes == []


@pytest.mark.asyncio
async def test_a_removal_of_a_package_the_target_does_not_hold_is_refused(config_dir, service):
    """The re-resolution's OWN sentence: a pair that is not an INSTALLED row is nothing to remove."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        token, csrf = await _token(http)
        missing = await _run(http, mods_page.REMOVE, token, csrf,
                             package="Ghost_Mod", version="1.0")
        after_missing = await open_node_page(http)
        token, csrf = await _token(http)
        wrong_version = await _run(http, mods_page.REMOVE, token, csrf,
                                   package=INSTALLED_PACKAGE, version="9.9.9")
        after_version = await open_node_page(http)
    assert missing.status_code == 403 and wrong_version.status_code == 403
    assert double.writes == []
    assert "Ghost_Mod" in after_missing and "is not installed" in after_missing
    assert "is not installed" in after_version


@pytest.mark.asyncio
async def test_a_removal_with_an_empty_half_is_refused(config_dir, service):
    """An empty field is not a name: refused, with nothing reached."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        token, csrf = await _token(http)
        no_package = await _run(http, mods_page.REMOVE, token, csrf, package="",
                                version=INSTALLED_VERSION)
        no_version = await _run(http, mods_page.REMOVE, token, csrf, package=INSTALLED_PACKAGE,
                                version="")
    assert no_package.status_code == 403 and no_version.status_code == 403
    assert double.writes == []


@pytest.mark.asyncio
async def test_the_rows_the_target_holds_still_remove_with_their_own_identity(config_dir, service):
    """Every installed row of the fixture still removes, unchanged — the ordinary path stays open."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        for package, version, _stamp, _folder in ROOT_ROWS:
            token, csrf = await _token(http, package=package, version=version)
            response = await _run(http, mods_page.REMOVE, token, csrf,
                                  package=package, version=version)
            assert response.status_code == 303, (package, response.text)
    assert double.writes == [("uninstall_root_package", NODE, package, version)
                             for package, version, _stamp, _folder in ROOT_ROWS]


# ------------------------------------------------------------------------------ the download

@pytest.mark.asyncio
@pytest.mark.parametrize("version", TRAVERSING_VERSIONS)
async def test_a_download_with_a_traversing_version_is_refused_and_reaches_no_service(
        config_dir, service, version):
    """The download's version is a version, not a path: a traversing one never reaches the service.

    The refusal is the ACTION's own (the seam has no folder listing to re-resolve a download's
    version against), so it comes back as the console's refusal notice on the page the write returns
    to — a sentence, not a 500, and never a service call.
    """
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        token, csrf = await _token(http, verb=mods_page.DOWNLOAD)
        response = await _run(http, mods_page.DOWNLOAD, token, csrf,
                              url="https://example.invalid/SRS_Radio_Standalone_v2.1.2.zip",
                              version=version)
        after_response = await http.get(response.headers.get("location", mods_page.node_page_url(NODE)))
    assert response.status_code == 303, response.text
    assert double.writes == []
    assert "is not a version" in after_response.text
    assert "Traceback" not in after_response.text


@pytest.mark.asyncio
async def test_a_download_from_a_plain_url_takes_its_version_from_the_url(config_dir, service):
    """The preset version is not a file name: a plain URL's own name decides it, and the listing
    recognises the file the node writes."""
    double = service(rows=ROOT_ROWS, entries=ENTRIES)
    app = build(config_dir, fleet())
    url = "https://example.invalid/SRS_Radio_Standalone_v2.1.2.zip"
    async with client(app) as http:
        await sign_in(http)
        token, csrf = await _token(http, verb=mods_page.DOWNLOAD)
        response = await _run(http, mods_page.DOWNLOAD, token, csrf, url=url,
                              version=mods_page.DEFAULT_VERSION)
        after = await open_node_page(http)
    assert response.status_code == 303, response.text
    assert double.writes == [("download_package", NODE, url, "2.1.2")]
    assert "SRS_Radio_Standalone" in after


@pytest.mark.asyncio
async def test_a_downloaded_package_is_then_offered_by_the_pick_dialog(config_dir, service):
    """A package the download produced is a package the install dialog can pick."""
    service(rows=ROOT_ROWS, entries=ENTRIES)
    app = build(config_dir, fleet())
    url = "https://example.invalid/SRS_Radio_Standalone_v2.1.2.zip"
    async with client(app) as http:
        await sign_in(http)
        token, csrf = await _token(http, verb=mods_page.DOWNLOAD)
        await _run(http, mods_page.DOWNLOAD, token, csrf, url=url,
                   version=mods_page.DEFAULT_VERSION)
        page = await open_node_page(http)
        pick = await http.post(mods_page.dialog_url(mods_page.NODE_OWNER, NODE, mods_page.INSTALL),
                               data={"_csrf_token": token_of(page)})
    assert pick.status_code == 200, pick.text
    assert 'value="SRS_Radio_Standalone/2.1.2"' in pick.text


@pytest.mark.asyncio
async def test_a_download_of_a_url_that_names_no_version_is_refused(config_dir, service):
    """A URL whose file name carries no version cannot produce a package the listing recognises."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        token, csrf = await _token(http, verb=mods_page.DOWNLOAD)
        response = await _run(http, mods_page.DOWNLOAD, token, csrf,
                              url="https://example.invalid/package.zip",
                              version=mods_page.DEFAULT_VERSION)
    assert response.status_code == 303, response.text
    assert double.writes == [], "nothing is downloaded from a URL that names no version"
