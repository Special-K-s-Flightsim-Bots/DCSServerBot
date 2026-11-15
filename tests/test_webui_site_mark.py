"""The site's mark: the upload, the removal, and the resolver that prefers what was uploaded.

Each pin is a promise §E1 makes:

* an upload writes EXACTLY four PNGs, each at its own pixel size and RGBA — the four files
  :data:`services.webservice.sites.AVATAR_SIZES` names, through
  :func:`services.webservice.sites.avatar_path`;
* a 4000 x 3000 JPEG is accepted and its EXIF and ICC profile are GONE: the published file is what
  Pillow encoded, nothing else (no metadata, no trailing bytes);
* a text file with a ``.png`` name, a body over the byte cap and a decompression bomb are each
  refused with their own sentence, and NOTHING is written — not one file, and the writer is never
  reached;
* a caller without ``site.write`` gets 403 and a caller may not upload to a site outside their scope;
* ``logo_url`` answers the uploaded mark when the files exist and ``branding.logo`` when they do not,
  and the served address answers the 64 px file the bar draws;
* a removal deletes the four files and clears the flag, and a second removal is still a success.

Driven through the real application (the production factory, the shell, the local backend and a stub
cluster), with the page's own CSRF token, so the doors are the ones the operator meets.
"""
from __future__ import annotations

import io
import re
import struct
import types
import zlib
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PIL import Image

from conftest import REPO_ROOT
from services.webservice import configgen, site_pages, sites
from services.webservice.auth import local as local_auth
from services.webservice.pages import site_config as site_page
from test_webui_write_pause import Cluster, build, client, instance, server, sign_in  # noqa: F401

ADMIN = "specialk"
GUARDS_ADMIN = "guards-admin"
PLAIN = "plain"

BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS:
    - guards-admin
    - plain
"""

SERVERS_YAML = """\
SRS-1:
  managed_by:
    - 5th Guards
SRS-2:
  managed_by:
    - Other Token
"""

SITE_YAML = """\
sites:
  root:
    slug: ""
    branding:
      title: Community
  guards:
    group_role: "5th Guards"
    slug: guards
    branding:
      title: Guards
      logo: brand/guards.png
  others:
    group_role: "Other Token"
    slug: others
"""

#: the sentence each refusal is stated with — read off the page after the redirect, so the pin is the
#: words the operator sees and not a status code alone.
NOT_AN_IMAGE = "not an image this page accepts"
TOO_LARGE = "larger than the 8 MiB"
BOMB = "decodes to more pixels than the mark accepts"
NO_FILE = "settings file cannot be read"
OTHER_SITE = "only for your own site"
UPLOADED = "The mark was uploaded"
REMOVED = "The uploaded mark was removed"


def cluster():
    a = server("SRS-1", ["5th Guards"])
    b = server("SRS-2", ["Other Token"], node_name="dcs-b-01", instance="Host-B-Instance")
    master = types.SimpleNamespace(name="dcs-master-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-master-01": master}, node=master,
                   instances=[instance("Host-A-Instance", a)])


def _users() -> list[dict]:
    password = local_auth.hash_password("correct horse battery staple",
                                       iterations=local_auth.MIN_ITERATIONS)
    return [{"username": ADMIN, "roles": ["Admin"], "password_hash": password},
            {"username": GUARDS_ADMIN, "roles": ["DCS"], "scope": ["5th Guards"],
             "password_hash": password},
            {"username": PLAIN, "roles": ["DCS"], "password_hash": password}]


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (tmp_path / sites.SITE_FILE).write_text(SITE_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(config_dir):
    return build(config_dir, cluster(), users=_users())


@pytest.fixture
def site_file(config_dir):
    return config_dir / sites.SITE_FILE


@pytest.fixture
def assets(config_dir):
    """The folder a site's files live in — created by the upload itself, never by the fixture."""
    return config_dir / sites.ASSET_DIR


# ----------------------------------------------------------------------------- the harness
def page_path() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_CONFIG_PATH)


def save_path() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_CONFIG_SAVE_PATH)


def upload_path() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_AVATAR_UPLOAD_PATH)


def remove_path() -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_AVATAR_REMOVE_PATH)


async def open_page(http) -> str:
    response = await http.get(page_path())
    assert response.status_code == 200, f"the page answered {response.status_code}"
    return response.text


def token_of(html: str) -> str:
    match = re.search(r'name="_csrf_token" value="([^"]*)"', html)
    assert match, "the page rendered no CSRF field"
    return match.group(1)


def revision_of(html: str) -> str:
    match = re.search(r'name="_revision" value="([^"]*)"', html)
    assert match, "the page rendered no revision field"
    return match.group(1)


def notice_of(html: str) -> str:
    match = re.search(r'<span class="notice-msg">(.*?)</span>', html, re.S)
    return match.group(1) if match else ""


async def upload(http, html: str, key: str, payload: bytes, filename: str = "mark.png",
                 content_type: str = "image/png", revision: str | None = None):
    data = {"_csrf_token": token_of(html), "_site": key}
    if revision is not None:
        data["_revision"] = revision
    return await http.post(upload_path(), data=data,
                           files={site_page.SITE_AVATAR_FIELD: (filename, payload, content_type)})


async def remove(http, html: str, key: str):
    return await http.post(remove_path(),
                           data={"_csrf_token": token_of(html), "_site": key})


# ----------------------------------------------------------------------------- the images
def png_bytes(width: int, height: int, colour=(200, 30, 40, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(width: int, height: int) -> bytes:
    """A JPEG that carries both an EXIF block and an ICC profile, so their loss is observable."""
    image = Image.new("RGB", (width, height), (10, 120, 200))
    for x in range(0, width, 7):
        for y in range(0, height, 11):
            image.putpixel((x, y), (240, 240, 20))
    exif = Image.Exif()
    exif[0x010F] = "DCSServerBot"   # Make
    exif[0x0110] = "mark probe"     # Model
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, exif=exif,
               icc_profile=b"\x00\x00\x00\x0c" + b"sRGB" + b"\x00" * 12)
    return buffer.getvalue()


def bomb_png() -> bytes:
    """A PNG whose HEADER claims 30 000 x 30 000 — 900 megapixels, refused before it is decoded."""
    header = struct.pack(">IIBBBBB", 30000, 30000, 8, 6, 0, 0, 0)
    ihdr = struct.pack(">I", len(header)) + b"IHDR" + header \
        + struct.pack(">I", zlib.crc32(b"IHDR" + header) & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + ihdr + iend


def mark_files(assets: Path) -> list[str]:
    return sorted(path.name for path in assets.iterdir()) if assets.is_dir() else []


def no_writes(monkeypatch) -> list:
    """Record every call to the file writer, so "nothing was written" is not merely "no file exists"."""
    calls: list = []
    monkeypatch.setattr(site_page, "stage_mark_file",
                        lambda payload, target: calls.append((target, len(payload))))
    return calls


# ================================================================== 1. four files, four sizes
@pytest.mark.asyncio
async def test_a_png_upload_writes_exactly_four_rgba_files(app, config_dir, assets, site_file):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", png_bytes(800, 500))
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert mark_files(assets) == ["avatar-guards-128.png", "avatar-guards-256.png", "avatar-guards-32.png",
                                 "avatar-guards-64.png"]
    for size in sites.AVATAR_SIZES:
        with Image.open(assets / f"avatar-guards-{size}.png") as image:
            assert image.size == (size, size), f"the {size} file is {image.size}"
            assert image.mode == "RGBA"
    assert sites.load_site_block(config_dir)["guards"]["avatar"] is True
    assert UPLOADED in notice_of(landed)


# ============================================================ 2. a big JPEG, and its metadata is gone
@pytest.mark.asyncio
async def test_a_four_thousand_by_three_thousand_jpeg_is_reencoded_without_its_metadata(
        app, config_dir, assets):
    source = jpeg_bytes(4000, 3000)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", source, filename="mark.jpg",
                             content_type="image/jpeg")
        assert reply.status_code == 303, reply.text[:400]
    assert mark_files(assets) == ["avatar-guards-128.png", "avatar-guards-256.png", "avatar-guards-32.png",
                                 "avatar-guards-64.png"]
    for size in sites.AVATAR_SIZES:
        path = assets / f"avatar-guards-{size}.png"
        payload = path.read_bytes()
        with Image.open(path) as image:
            assert image.size == (size, size)
            assert image.mode == "RGBA"
            assert "exif" not in image.info, "the upload kept the EXIF block"
            assert "icc_profile" not in image.info, "the upload kept the ICC profile"
            assert not dict(image.getexif())
            # a 4:3 mark is contained and centred: the top-left corner is the transparent padding
            assert image.getpixel((0, 0))[3] == 0
        # the published file is exactly the PNG Pillow wrote: nothing trails the IEND chunk
        assert payload.endswith(b"IEND\xaeB`\x82"), "the file carries trailing bytes"
        assert len(payload) == payload.rindex(b"IEND\xaeB`\x82") + 8


# ======================================================================== 3. the three refusals
@pytest.mark.asyncio
async def test_a_text_file_named_png_is_refused_and_nothing_is_written(
        app, config_dir, assets, site_file, monkeypatch):
    calls = no_writes(monkeypatch)
    before = site_file.read_text(encoding="utf-8")
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", b"<?php echo 'not an image'; ?>")
        assert reply.status_code == 303
        landed = await open_page(http)
    assert calls == [], "the writer was reached for a refused upload"
    assert mark_files(assets) == []
    assert site_file.read_text(encoding="utf-8") == before
    assert NOT_AN_IMAGE in notice_of(landed)


@pytest.mark.asyncio
async def test_a_decodable_format_outside_the_three_is_refused(app, config_dir, assets, monkeypatch):
    """Pillow identifies a GIF perfectly well; the closed set of three is what refuses it."""
    calls = no_writes(monkeypatch)
    buffer = io.BytesIO()
    Image.new("P", (64, 64), 3).save(buffer, format="GIF")
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", buffer.getvalue(), filename="mark.png")
        assert reply.status_code == 303
        landed = await open_page(http)
    assert calls == []
    assert mark_files(assets) == []
    assert NOT_AN_IMAGE in notice_of(landed)


@pytest.mark.asyncio
async def test_a_body_over_the_byte_cap_is_refused_before_pillow_reads_it(
        app, config_dir, assets, monkeypatch):
    calls = no_writes(monkeypatch)
    oversize = b"\x89PNG\r\n\x1a\n" + b"\x00" * (site_page.SITE_AVATAR_MAX_BYTES + 4096)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", oversize)
        assert reply.status_code == 303
        landed = await open_page(http)
    assert calls == []
    assert mark_files(assets) == []
    assert TOO_LARGE in notice_of(landed)


@pytest.mark.asyncio
async def test_a_decompression_bomb_is_refused_not_decoded(app, config_dir, assets, monkeypatch):
    calls = no_writes(monkeypatch)
    payload = bomb_png()
    assert len(payload) < 1024, "the bomb must be small enough to pass the byte cap"
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", payload)
        assert reply.status_code == 303
        landed = await open_page(http)
    assert calls == []
    assert mark_files(assets) == []
    assert BOMB in notice_of(landed)


@pytest.mark.asyncio
async def test_an_image_over_the_pixel_cap_is_refused_with_its_count(app, config_dir, assets):
    """Between Pillow's own ceiling and this page's the refusal still names the pixels it saw."""
    limit = site_page.SITE_AVATAR_MAX_PIXELS
    width = int(limit ** 0.5) + 100
    payload = png_bytes(width, width)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", payload)
        assert reply.status_code == 303
        landed = await open_page(http)
    assert mark_files(assets) == []
    assert "pixels; the mark accepts at most" in notice_of(landed)


@pytest.mark.asyncio
async def test_a_missing_site_file_is_a_refusal_and_not_a_guessed_folder(app, config_dir, assets,
                                                                        site_file):
    """A mark write never CREATES the site file: the console answers instead of guessing a folder."""
    (config_dir / "services" / "webservice.yaml").write_text(
        "sites:\n  guards:\n    group_role: 5th Guards\n    slug: guards\n", encoding="utf-8")
    site_file.unlink()
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", png_bytes(64, 64))
        assert reply.status_code == 303
        landed = await open_page(http)
    assert mark_files(assets) == []
    assert not site_file.exists(), "the upload created the site file"
    assert NO_FILE in notice_of(landed)


# ============================================================ 4. the capability and the scope
@pytest.mark.asyncio
async def test_a_caller_without_the_write_capability_gets_403(app, config_dir, assets):
    async with client(app) as http:
        await sign_in(http, PLAIN)
        reply = await http.post(upload_path(), data={"_site": "guards"},
                                files={site_page.SITE_AVATAR_FIELD: ("m.png",
                                                                     png_bytes(64, 64),
                                                                     "image/png")})
    assert reply.status_code == 403
    assert mark_files(assets) == []


@pytest.mark.asyncio
async def test_a_group_admin_may_not_upload_to_another_site(app, config_dir, assets):
    async with client(app) as http:
        await sign_in(http, GUARDS_ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "others", png_bytes(64, 64))
        assert reply.status_code == 403
        landed_page = await http.get(page_path())
    assert OTHER_SITE in reply.text
    assert mark_files(assets) == []
    assert landed_page.status_code == 200


@pytest.mark.asyncio
async def test_a_group_admin_uploads_to_their_own_site(app, config_dir, assets):
    async with client(app) as http:
        await sign_in(http, GUARDS_ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", png_bytes(120, 120))
        assert reply.status_code == 303
    assert mark_files(assets) == ["avatar-guards-128.png", "avatar-guards-256.png", "avatar-guards-32.png",
                                 "avatar-guards-64.png"]


# ====================================================================== 5. the resolver, and the bytes
def test_the_resolver_prefers_the_uploaded_mark(config_dir, assets):
    catalog = sites.load_sites({}, config_dir=config_dir)
    guards = catalog.site_for_slug("guards")
    assert site_pages.logo_url(guards, config_dir) == "/guards/logo.png"
    assets.mkdir(parents=True, exist_ok=True)
    for size in sites.AVATAR_SIZES:
        (assets / f"avatar-guards-{size}.png").write_bytes(png_bytes(size, size))
    from dataclasses import replace

    uploaded = replace(guards, avatar=True)
    assert site_pages.logo_url(uploaded, config_dir) == "/guards/avatar.png"
    # the flag alone is not enough: a file that is gone falls back instead of serving a broken image
    (assets / "avatar-guards-64.png").unlink()
    assert site_pages.logo_url(uploaded, config_dir) == "/guards/logo.png"


def test_a_site_with_neither_declares_no_mark(config_dir, assets):
    catalog = sites.load_sites({}, config_dir=config_dir)
    root = catalog.resolve("")
    assert site_pages.logo_url(root, config_dir) is None


@pytest.mark.asyncio
async def test_the_public_page_draws_the_uploaded_mark_and_serves_the_bar_s_size(app, config_dir,
                                                                                 assets):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        before = await http.get("/guards")
        await upload(http, html, "guards", png_bytes(800, 800))
        after = await http.get("/guards")
        served = await http.get("/guards/avatar.png")
        legacy = await http.get("/guards/logo.png")
    assert 'src="/guards/logo.png"' in before.text
    assert 'src="/guards/avatar.png"' in after.text
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(served.content)) as image:
        assert image.size == (site_pages.AVATAR_MARK_SIZE, site_pages.AVATAR_MARK_SIZE)
    assert legacy.status_code == 404, "the declared logo file does not exist in this fixture"


# ============================================== 5b. a card save leaves the mark alone
@pytest.mark.asyncio
async def test_a_card_save_does_not_clear_the_uploaded_mark(app, config_dir, assets, site_file):
    """The flag and the files survive an ordinary save of the card: the form never posts ``avatar``."""
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        await upload(http, html, "guards", png_bytes(64, 64))
        html = await open_page(http)
        reply = await http.post(save_path(),
                                data={"_csrf_token": token_of(html),
                                      "_revision": revision_of(html),
                                      "_site": "guards",
                                      "f.sites.guards.branding.title": "Guards Wing"})
        assert reply.status_code == 303, reply.text[:400]
        landed = await http.get("/guards")
    assert sites.load_site_block(config_dir)["guards"]["avatar"] is True
    assert len(mark_files(assets)) == 4
    assert 'src="/guards/avatar.png"' in landed.text


# ======================================================================== 6. the removal
@pytest.mark.asyncio
async def test_a_removal_deletes_the_four_files_and_clears_the_flag(app, config_dir, assets,
                                                                   site_file):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        await upload(http, html, "guards", png_bytes(64, 64))
        assert len(mark_files(assets)) == 4
        html = await open_page(http)
        reply = await remove(http, html, "guards")
        assert reply.status_code == 303
        landed = await open_page(http)
    assert mark_files(assets) == []
    assert "avatar" not in sites.load_site_block(config_dir)["guards"]
    assert REMOVED in notice_of(landed)

    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        twice = await remove(http, html, "guards")
        assert twice.status_code == 303
        landed = await open_page(http)
    assert REMOVED in notice_of(landed), "a second removal is still a success"


# ================================================================ 7. the slug never reaches a path
def test_a_hostile_slug_cannot_address_a_file_outside_the_asset_folder(config_dir):
    """The name is built as ``avatar-<slug>-<size>.png``, so a slug can only ever stay inside.

    A slug carrying ``..`` segments can still not leave the folder — the fixed prefix and suffix keep
    the resolved file where it belongs — and the one shape that would leave is refused outright.
    """
    folder = (config_dir / sites.ASSET_DIR).resolve()
    for slug in ("../../etc", ".."):
        for path in site_page.avatar_paths(slug, config_dir):
            assert path.resolve().parent == folder, f"{slug!r} addressed {path.resolve()}"
    for slug in ("../", "a/b", "guards/../../.."):
        with pytest.raises(site_page.AvatarRefusal):
            site_page.avatar_paths(slug, config_dir)


def test_every_mark_path_sits_in_the_site_s_own_asset_folder(config_dir):
    folder = (config_dir / sites.ASSET_DIR).resolve()
    for path in site_page.avatar_paths("guards", config_dir):
        assert path.resolve().parent == folder
        assert path.name.startswith("avatar-guards-")


# ==================================== 8. the wall in front of the route: Starlette's 1 MiB default
#
# Starlette's multipart parser bounds every NON-FILE part at 1 MiB unless the caller says otherwise
# (``Request.form(max_part_size=…)``). The page's own ceiling is ``SITE_AVATAR_MAX_BYTES``, so that
# ceiling has to be the one that decides: an image between the two must reach the route, and a body
# the parser still refuses must be answered with the CARD's sentence, never the parser's text.
PARSER_TEXT = "Part exceeded maximum size"


def padded_png(size: int) -> bytes:
    """A valid PNG with zero bytes after ``IEND`` — Pillow reads the image and ignores the tail."""
    base = png_bytes(300, 300)
    assert len(base) < size, "the padding target must be larger than the image itself"
    return base + b"\x00" * (size - len(base))


@pytest.mark.asyncio
async def test_a_png_padded_past_one_mib_is_accepted_and_writes_the_four_files(
        app, config_dir, assets):
    """PIN 1: the 1 MiB parser bound no longer stands in front of the page's own 8 MiB one."""
    payload = padded_png(1200 * 1024)
    assert 1024 * 1024 < len(payload) <= site_page.SITE_AVATAR_MAX_BYTES
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", payload)
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert mark_files(assets) == ["avatar-guards-128.png", "avatar-guards-256.png", "avatar-guards-32.png",
                                 "avatar-guards-64.png"], "the padded image wrote no mark"
    assert UPLOADED in notice_of(landed)
    assert PARSER_TEXT not in landed, "the parser's own text reached the page"


@pytest.mark.asyncio
async def test_the_pages_own_byte_cap_is_the_bound_that_decides(app, config_dir, assets,
                                                               monkeypatch):
    """PIN 2: a file AT the cap is accepted; one byte over is refused with the CARD's sentence."""
    at_cap = padded_png(site_page.SITE_AVATAR_MAX_BYTES)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", at_cap)
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
        assert UPLOADED in notice_of(landed), (
            "a file of exactly the page's own ceiling was refused: "
            f"{notice_of(landed)!r}")
        assert len(mark_files(assets)) == 4, mark_files(assets)
        calls = no_writes(monkeypatch)
        html = await open_page(http)
        over = await upload(http, html, "guards", at_cap + b"\x00")
        assert over.status_code == 303, over.text[:400]
        landed = await open_page(http)
    assert len(mark_files(assets)) == 4, "a refused body rewrote the mark"
    assert calls == [], "the writer was reached for a body over the cap"
    assert TOO_LARGE in notice_of(landed), notice_of(landed)
    assert PARSER_TEXT not in landed and PARSER_TEXT not in notice_of(landed), (
        "the parser's own sentence answered an oversize upload")


@pytest.mark.asyncio
async def test_a_body_the_parser_refuses_is_answered_with_the_card_s_own_sentence(
        app, config_dir, assets, monkeypatch):
    """The parser can still refuse a body this page never sees — and the page still says its own words.

    A single form FIELD over the parser's bound is refused inside ``Request.form``; the answer must be
    the card's sentence, never Starlette's ``Part exceeded maximum size of …KB.``.
    """
    calls = no_writes(monkeypatch)
    huge_field = "x" * (site_page.SITE_AVATAR_MAX_BYTES + 4096)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await http.post(upload_path(),
                                data={"_csrf_token": token_of(html), "_site": "guards",
                                      site_page.SITE_AVATAR_FIELD: huge_field})
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert calls == [] and mark_files(assets) == []
    notice = notice_of(landed)
    assert notice, "an unreadable body left no sentence on the page"
    assert PARSER_TEXT not in reply.text and PARSER_TEXT not in notice, (
        f"the parser's own text answered the upload: {notice!r}")
    assert TOO_LARGE in notice or "could not be read" in notice, notice


@pytest.mark.asyncio
async def test_a_multipart_body_with_no_boundary_answers_the_card_s_form_sentence(
        app, config_dir, assets, monkeypatch):
    """The other parser refusal: data no parser can read is still answered in the page's own words."""
    calls = no_writes(monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await open_page(http)
        reply = await http.post(upload_path(), content=b"--x\r\nnot a form\r\n",
                                headers={"content-type": "multipart/form-data"})
        assert reply.status_code == 303, reply.text[:300]
        landed = await open_page(http)
    assert calls == [] and mark_files(assets) == []
    notice = notice_of(landed)
    assert "could not be read as a form" in notice, notice
    assert PARSER_TEXT not in notice


# ============================ 9. a failed re-upload leaves the mark in use exactly as it was
#
# The upload publishes FOUR files and a flag. Every one of them is staged first and renamed only
# after the config write landed, so a refusal of any kind is answered with the files the site was
# already drawing still in place — never deleted, never split between an old variant and a new one.
MARK_FILES = ["avatar-guards-128.png", "avatar-guards-256.png", "avatar-guards-32.png", "avatar-guards-64.png"]
STALE_REFUSED = "This page is stale"
WRITE_REFUSED = "the disk said no"


def mark_bytes(assets: Path) -> dict:
    return {size: (assets / f"avatar-guards-{size}.png").read_bytes()
            for size in sites.AVATAR_SIZES}


def staged_files(assets: Path) -> list[str]:
    """The staged variants a write left behind — a successful or refused write leaves none."""
    return sorted(path.name for path in assets.iterdir()
                  if path.name.endswith(site_page.SITE_MARK_STAGED_SUFFIX))


def mark_route(token: str, size: int) -> str:
    from services.webservice import mount

    return mount.url(site_page.SITE_MARK_PATH.format(token=token, size=size))


@pytest.mark.asyncio
async def test_a_stale_revision_refuses_a_re_upload_and_keeps_the_mark_in_use(
        app, config_dir, assets, site_file):
    """PIN 1: the second tab's stale revision is refused, and the mark the first upload published is
    still the mark the site draws — byte for byte, with no staged file left over."""
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        stale = revision_of(html)
        first = await upload(http, html, "guards", png_bytes(800, 800), revision=stale)
        assert first.status_code == 303, first.text[:400]
        landed = await open_page(http)
        assert UPLOADED in notice_of(landed)
        before = mark_bytes(assets)
        reply = await upload(http, html, "guards", png_bytes(400, 400, (250, 250, 10, 255)),
                             revision=stale)
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert STALE_REFUSED in notice_of(landed), notice_of(landed)
    assert mark_files(assets) == MARK_FILES, "the refused upload destroyed the mark in use"
    assert mark_bytes(assets) == before, "the refused upload replaced a file the site draws"
    assert staged_files(assets) == [], "the refused upload left a staged file behind"
    assert sites.load_site_block(config_dir)["guards"]["avatar"] is True


@pytest.mark.asyncio
async def test_a_refused_config_write_keeps_the_mark_in_use(app, config_dir, assets, monkeypatch):
    """PIN 2: the same promise for a writer that refuses — the three files are untouched."""
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        first = await upload(http, html, "guards", png_bytes(800, 800))
        assert first.status_code == 303, first.text[:400]
        before = mark_bytes(assets)
        html = await open_page(http)

        def refuse(path, mutate, **kwargs):
            raise site_page.configwrite.ConfigWriteError(WRITE_REFUSED)

        monkeypatch.setattr(site_page.configwrite, "patch_config_file", refuse)
        reply = await upload(http, html, "guards", png_bytes(400, 400, (250, 250, 10, 255)))
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert WRITE_REFUSED in notice_of(landed), notice_of(landed)
    assert mark_files(assets) == MARK_FILES, "the refused write destroyed the mark in use"
    assert mark_bytes(assets) == before, "the refused write replaced a file the site draws"
    assert staged_files(assets) == [], "the refused write left a staged file behind"


@pytest.mark.asyncio
async def test_a_successful_upload_publishes_the_new_files_and_leaves_no_temp(
        app, config_dir, assets):
    """PIN 3: the other half — a write that lands publishes all three, and stages nothing behind."""
    new_colour = (250, 250, 10, 255)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        await upload(http, html, "guards", png_bytes(64, 64, (10, 200, 30, 255)))
        before = mark_bytes(assets)
        html = await open_page(http)
        reply = await upload(http, html, "guards", png_bytes(500, 500, new_colour))
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert UPLOADED in notice_of(landed)
    assert mark_files(assets) == MARK_FILES, mark_files(assets)
    assert staged_files(assets) == [], "a published mark left a staged file behind"
    after = mark_bytes(assets)
    for size in sites.AVATAR_SIZES:
        assert after[size] != before[size], f"the {size} file is still the old image"
        with Image.open(assets / f"avatar-guards-{size}.png") as image:
            assert image.convert("RGBA").getpixel((size // 2, size // 2)) == new_colour


@pytest.mark.asyncio
async def test_a_failure_on_the_last_variant_leaves_the_four_old_files(app, config_dir, assets,
                                                                      monkeypatch):
    """PIN 4: the torn set is closed — a failure on the third variant publishes none of them."""
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        await upload(http, html, "guards", png_bytes(64, 64, (10, 200, 30, 255)))
        before = mark_bytes(assets)
        html = await open_page(http)
        real = site_page.tempfile.NamedTemporaryFile
        made: list = []

        def flaky(*args, **kwargs):
            if kwargs.get("suffix") != ".tmp":
                return real(*args, **kwargs)
            made.append(kwargs.get("dir"))
            if len(made) == 3:
                raise OSError(WRITE_REFUSED)
            return real(*args, **kwargs)

        monkeypatch.setattr(site_page.tempfile, "NamedTemporaryFile", flaky)
        reply = await upload(http, html, "guards", png_bytes(400, 400, (250, 250, 10, 255)))
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
    assert WRITE_REFUSED in notice_of(landed), notice_of(landed)
    assert mark_files(assets) == MARK_FILES, (
        f"a failure on the third variant left a torn set: {mark_files(assets)}")
    assert mark_bytes(assets) == before, "a failure on the third variant published a new file"
    assert staged_files(assets) == [], "a failed write left a staged file behind"


# ====================== 10. the console's previews serve only what the site itself would draw
@pytest.mark.asyncio
async def test_the_preview_route_serves_only_the_mark_the_site_draws(app, config_dir, assets,
                                                                     monkeypatch):
    """The route answers the SAME flag-plus-file question the public pages answer, or 404.

    Three states: flag set and the files there (the positive control a card's preview needs), flag
    set and a file gone, and the flag cleared while the files survive — a removal whose delete failed.
    The last two must answer 404 here exactly as ``/guards/avatar.png`` does.
    """
    token = configgen.encode_token("guards")
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await open_page(http)
        reply = await upload(http, html, "guards", png_bytes(64, 64))
        assert reply.status_code == 303, reply.text[:400]
        present = await http.get(mark_route(token, 64))
        assert present.status_code == 200, "the preview of a mark the site draws answered no file"

        (assets / "avatar-guards-64.png").unlink()
        gone = await http.get(mark_route(token, 64))
        assert gone.status_code == 404, "a flag whose file is gone was still previewed"

        html = await open_page(http)
        await upload(http, html, "guards", png_bytes(64, 64))
        assert mark_files(assets) == MARK_FILES
        monkeypatch.setattr(site_page, "_discard_marks", lambda paths: None)
        html = await open_page(http)
        removed = await remove(http, html, "guards")
        assert removed.status_code == 303, removed.text[:400]
        assert mark_files(assets) == MARK_FILES, "the fixture must leave the files behind"
        assert "avatar" not in sites.load_site_block(config_dir)["guards"]
        cleared = await http.get(mark_route(token, 64))
        public = await http.get("/guards/avatar.png")
    assert cleared.status_code == 404, "the preview served a mark the site does not draw"
    assert public.status_code == 404, "the public address still draws a cleared mark"


# ==================================== 11. the picker is ONE control, in the page's own words

SCOPE_CSS = REPO_ROOT / "services" / "webservice" / "static" / "settings-scope.css"
SITE_CONFIG_JS = REPO_ROOT / "services" / "webservice" / "static" / "site-config.js"

#: what the control states before a file is chosen: the server's own accept sentence, translated.
ACCEPT_LEAD = "PNG, JPEG or WebP"


class _Markup(HTMLParser):
    """Every element of a rendered document with its attributes and its own text.

    Read as a TREE, not as a substring: the pin is about one element wearing a class, carrying an
    attribute and being the target of another element's `for`, and a text pin would match the
    stylesheet that describes it just as happily.
    """

    VOID = frozenset({"input", "img", "br", "link", "meta", "hr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict] = []
        self._stack: list[dict] = []

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": dict(attrs), "text": ""}
        self.elements.append(element)
        if tag not in self.VOID:
            self._stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index]["tag"] == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        for element in self._stack:
            element["text"] += data

    def with_attr(self, name: str) -> list[dict]:
        return [element for element in self.elements if name in element["attrs"]]

    def by_attr(self, name: str, value: str) -> list[dict]:
        return [element for element in self.elements
                if element["attrs"].get(name) == value]

    def wearing(self, tag: str, css: str) -> list[dict]:
        return [element for element in self.elements if element["tag"] == tag
                and css in (element["attrs"].get("class") or "").split()]


def _card(html: str) -> _Markup:
    markup = _Markup()
    markup.feed(html)
    return markup


@pytest.mark.asyncio
async def test_the_mark_picker_is_one_control_in_the_page_s_own_words(app):
    """PIN 1: a clipped-but-focusable input, a `label[for]` wearing the console's button, and the
    no-JavaScript path still posting the file.

    The rendered markup decides the structure (one input, one trigger bound to it, one submit beside
    it, the mark's own form carrying `multipart`), and the stylesheet decides that the native widget
    is DRAWN NOT AT ALL without being taken out of the keyboard's reach.
    """
    async with client(app) as http:
        await sign_in(http, ADMIN)
        markup = _card(await open_page(http))

    inputs = markup.wearing("input", "sc-av-file")
    assert inputs, "the card draws no file input at all"
    assert len(inputs) == len({element["attrs"].get("id") for element in inputs}), (
        "two cards share one input id, so one card's label opens the other's picker")
    entry = inputs[0]
    assert entry["attrs"]["type"] == "file", entry["attrs"]
    ident = entry["attrs"]["id"]
    assert ident, "the input carries no id, so no label can be bound to it"

    labels = [element for element in markup.by_attr("for", ident) if element["tag"] == "label"]
    assert labels, "no `label[for]` is bound to the file input: the browser's own widget is the control"
    trigger = labels[0]
    assert "st-btn" in (trigger["attrs"].get("class") or "").split(), (
        f"the visible trigger wears {trigger['attrs'].get('class')!r}, not the console's button")
    assert trigger["text"].strip() == "Choose an image", (
        f"the trigger reads {trigger['text'].strip()!r}")

    # the line the control states itself with: the accepted formats until a file is chosen, and the
    # pattern the script fills with the chosen file's name
    status = markup.with_attr("data-sc-file-status")
    assert len(status) == len(inputs), (
        f"{len(inputs)} pickers carry {len(status)} status lines")
    assert ACCEPT_LEAD in status[0]["text"], (
        f"the status line states {status[0]['text'].strip()!r}, not what the page accepts")
    assert status[0]["attrs"].get("data-sc-file-chosen") == "Selected: %(name)s", (
        f"the chosen-file pattern is {status[0]['attrs'].get('data-sc-file-chosen')!r}")

    # CHOOSING IS NOT UPLOADING: the submit beside the trigger is still there and is not the trigger
    form_id = entry["attrs"].get("form")
    assert form_id, "the clipped input names no form, so a browser without scripts cannot post it"
    submits = [element for element in markup.elements if element["tag"] == "button"
               and element["attrs"].get("type") == "submit"
               and element["attrs"].get("form") == form_id]
    assert any(button["text"].strip() == "Upload an image" for button in submits), (
        f"no submit posts the mark's own form: {[b['text'].strip() for b in submits]}")
    assert trigger["tag"] != "button", "the trigger is a button, so choosing would submit the form"

    # the no-JavaScript path: the input posts the file to the mark's own route
    assert entry["attrs"].get("name") == site_page.SITE_AVATAR_FIELD
    forms = markup.by_attr("id", form_id)
    assert forms, f"the input names form {form_id!r}, which the page does not render"
    assert forms[0]["attrs"].get("enctype") == "multipart/form-data", forms[0]["attrs"]
    assert forms[0]["attrs"].get("action") == upload_path(), forms[0]["attrs"]

    # and the widget is drawn NOT AT ALL, without leaving the keyboard's reach
    sheet = SCOPE_CSS.read_text(encoding="utf-8")
    rule = re.search(r"\.sc-av-file \{[^}]*\}", sheet)
    assert rule, "settings-scope.css carries no .sc-av-file rule"
    body = rule.group(0)
    assert "clip-path: inset(50%)" in body, f"the input is not clipped: {body!r}"
    assert "width: 1px" in body and "height: 1px" in body, body
    assert "display: none" not in body and "visibility: hidden" not in body, (
        "the input is taken out of the keyboard's reach instead of being clipped")
    assert ".sc-av-file:focus-visible + .sc-av-pick" in sheet, (
        "the clipped input can draw no focus ring and the trigger carries none")
    # and the clipped box is POSITIONED in the row it is drawn in: `top`/`left` at the row's own
    # origin, with the row itself the containing block. A clipped box left at its static position
    # inside the page's scrolling pane is laid out against the initial containing block, lands a
    # pane-scroll away from the trigger, stretches the document past the viewport (a page scrollbar)
    # and becomes a focus-scroll target, so pressing the trigger scrolls the page.
    assert "position: absolute" in body, body
    assert "top: 0" in body and "left: 0" in body, (
        f"the clipped input is not pinned to its row's origin: {body!r}")
    acts = re.search(r"\.sc-av-acts \{[^}]*\}", sheet)
    assert acts, "settings-scope.css carries no .sc-av-acts rule"
    assert "position: relative" in acts.group(0), (
        f"the input's own row is not its containing block: {acts.group(0)!r}")


def test_the_picker_script_states_the_chosen_file():
    """The status line follows the input's own `change` — the file's name, never the browser's."""
    script = SITE_CONFIG_JS.read_text(encoding="utf-8")
    assert "data-sc-file-status" in script, "no script binds the picker's status line"
    assert "data-sc-file-chosen" in script, "the script does not read the pattern the page rendered"
    assert "input.files" in script and ".name" in script, (
        "the script never reads the chosen file's own name")


# ============================== 12. the box's address changes when the mark does (pin 2)

def preview_address(html: str) -> str:
    """The address the card's frame loads, out of the rendered markup."""
    match = re.search(r'<iframe src="([^"]*)"[^>]*data-sc-preview', html)
    assert match, "the card renders no preview frame"
    return match.group(1).replace("&amp;", "&")


def mark_stamp_of(config_dir, slug: str) -> str:
    path = sites.avatar_path(slug, site_pages.AVATAR_MARK_SIZE, config_dir)
    return str(int(path.stat().st_mtime_ns))


@pytest.mark.asyncio
async def test_the_preview_address_carries_the_mark_s_own_version(app, config_dir, assets):
    """PIN 2: the box's address is versioned by the mark, so a re-render is a NEW address.

    The frame loads the site's own preview document and that document draws the mark at an address
    of its own; neither carries a version, so without the stamp a browser may answer both from what
    it already holds. The stamp is the uploaded file's own modification time, `0` until there is one.
    """
    async with client(app) as http:
        await sign_in(http, ADMIN)
        first_page = await open_page(http)
        before = preview_address(first_page)
        assert before == f"{site_pages.CHROME_PREVIEW_PATH}?v=0", (
            f"a site with no uploaded mark points the box at {before!r}")

        reply = await upload(http, first_page, "root", png_bytes(64, 64, (210, 40, 90, 255)))
        assert reply.status_code == 303, reply.text[:400]
        landed = await open_page(http)
        stamp = mark_stamp_of(config_dir, "")
        assert preview_address(landed) == f"{site_pages.CHROME_PREVIEW_PATH}?v={stamp}", (
            f"the box's address is {preview_address(landed)!r}, the mark's stamp is {stamp}")

        reply = await upload(http, landed, "root", png_bytes(64, 64, (20, 120, 60, 255)))
        assert reply.status_code == 303, reply.text[:400]
        again = await open_page(http)
    assert preview_address(again) != preview_address(landed), (
        "a replaced mark left the box pointed at the address the browser already holds")
