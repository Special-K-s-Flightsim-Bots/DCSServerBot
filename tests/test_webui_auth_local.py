"""The local auth backend: password hashing, role resolution, and the two refusal classes.

The credential store is ``services/webservice.yaml`` (``auth.local.users``); ``bot.yaml`` is read
through ``node.config_dir`` for exactly one purpose -- to validate that every configured role NAME
is one the bot defines -- and never to derive a user's roles. Refusals carry a logged REASON and are
never echoed to the page; a correct password yields exactly the configured role set; an unknown role
name is refused loudly instead of silently granting Admin."""
from __future__ import annotations

import logging
import types
from pathlib import Path

import pytest
from fastapi import APIRouter, Request

from conftest import asgi_client, create_app, install_shell, permissions, simple_router
from services.webservice import auth
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"

#: `specialk` is an Admin and nothing else; `mod1` holds two roles. The headless meaning of
#: `bot.yaml -> roles:` maps a role name -> member names, so inverting it gives a member's roles.
BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
  Moderator:
    - mod1
"""


def _node(config_dir) -> types.SimpleNamespace:
    return types.SimpleNamespace(master=True, config_dir=str(config_dir))


def _hash(password: str = PASSWORD) -> str:
    """A LOW-iteration hash: the suite runs hundreds of comparisons and 600k iterations each."""
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(users, *, local_enabled: bool = True, breakglass: dict | None = None,
            cookie_name: str = COOKIE_NAME) -> dict:
    config: dict = {
        "session": {"secret": SESSION_SECRET, "cookie_name": cookie_name, "max_age": 600},
        "auth": {
            "public_base_url": "http://localhost:9876",
            "local": {"enabled": local_enabled, "users": users},
        },
    }
    if breakglass is not None:
        config["auth"]["breakglass"] = breakglass
    return config


def _user(username: str, roles: list[str], password: str = PASSWORD) -> dict:
    return {"username": username, "password_hash": _hash(password), "roles": roles}


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _build(config_dir, config):
    app = create_app()
    registrar = install_shell(app, node=_node(config_dir), config=config)
    return app, registrar


def _bot_yaml_roles_for(config_dir, member: str) -> set[str]:
    """The member's role names under the HEADLESS meaning of `bot.yaml -> roles:`.

    Parsed here independently of the product's reader so the expectation is not the thing under
    test: `roles:` maps role name -> member names, so the member's roles are the keys whose list
    contains them.
    """
    ruamel = pytest.importorskip("ruamel.yaml")
    document = ruamel.YAML(typ="safe").load((config_dir / "services" / "bot.yaml").read_text(encoding="utf-8"))
    return {role for role, members in document.get("roles", {}).items() if member in members}


# ------------------------------------------------------------------ password hashing

def test_password_hash_roundtrip_and_refusals():
    encoded = _hash()
    assert encoded.startswith("pbkdf2_sha256$")
    assert PASSWORD not in encoded, "the hash must not contain the password"
    assert local_auth.verify_password(PASSWORD, encoded) is True
    assert local_auth.verify_password("wrong", encoded) is False
    assert local_auth.verify_password("", encoded) is False


def test_each_hash_is_salted():
    assert _hash() != _hash()


# ---------------------------------------------- the accepted password LENGTH (authorisation review)
#
# PBKDF2-HMAC-SHA256 runs 600k iterations per verification and the posted password is
# attacker-controlled, so an unbounded length turns the login form into a CPU burn: a 1 MB
# password costs ~1 MB of key material per attempt. The cap is applied BEFORE any derivation and
# the password is never TRUNCATED to fit -- truncating would silently verify a different secret
# than the one the visitor typed.

def test_an_over_long_password_is_refused_without_deriving_anything(monkeypatch):
    """The cap is enforced before the KDF runs, for BOTH the known-user and the decoy path."""
 # hashes are built FIRST: the counting wrapper must only see the KDF work of the calls below
    encoded = _hash()
    decoy = local_auth._decoy_hash()
    calls: list[tuple] = []
    real_derive = local_auth._derive

    def counting_derive(*args, **kwargs):
        calls.append(args)
        return real_derive(*args, **kwargs)

    monkeypatch.setattr(local_auth, "_derive", counting_derive)
    over = "x" * (local_auth.MAX_PASSWORD_LENGTH + 1)

    assert local_auth.verify_password(over, encoded) is False
    assert calls == [], "the over-long password was fed to PBKDF2 anyway"
 #... and the decoy path (an unknown username) pays the same cost, so it must be capped too
    assert local_auth.verify_password(over, decoy) is False
    assert calls == [], "the over-long password was fed to PBKDF2 on the decoy path"

 # the limit is INCLUSIVE: a password exactly at the cap still verifies normally
    at_cap = "y" * local_auth.MAX_PASSWORD_LENGTH
    at_cap_hash = local_auth.hash_password(at_cap, iterations=local_auth.MIN_ITERATIONS)
    calls.clear()  # hashing above is the KDF too; count only the VERIFICATION below
    assert local_auth.verify_password(at_cap, at_cap_hash) is True
    assert len(calls) == 1, "the at-the-cap password must be derived once, not skipped"


def test_hash_password_refuses_an_over_long_password_by_name():
    """The helper refuses loudly and names the limit -- never a silent truncation."""
    over = "x" * (local_auth.MAX_PASSWORD_LENGTH + 1)
    with pytest.raises(ValueError) as raised:
        local_auth.hash_password(over)
    assert str(local_auth.MAX_PASSWORD_LENGTH) in str(raised.value)
    assert len(str(over)) != 0 and str(over) not in str(raised.value), \
        "the refusal must not echo the password"


def test_the_backend_logs_why_an_over_long_password_is_refused(config_dir, caplog):
    """The reason reaches the log in the module's own refusal style (one line, no password)."""
    app, _ = _build(config_dir, _config([_user("specialk", ["Admin"])]))
    over = "x" * (local_auth.MAX_PASSWORD_LENGTH + 1)
    with caplog.at_level(logging.WARNING):
        assert app.state.webui_auth.login(None, "specialk", over) is None
    ours = [r for r in caplog.records
            if r.name == "services.webservice.auth.local"
            and "refusing a local login" in r.getMessage()]
    assert len(ours) == 1, [r.getMessage() for r in ours]
    line = ours[0].getMessage()
    assert "specialk" in line and str(local_auth.MAX_PASSWORD_LENGTH) in line, line
    assert over not in line, "a refusal line must never carry the posted password"


@pytest.mark.parametrize("encoded", ["", "plaintext", "md5$1$aa$bb", "pbkdf2_sha256$notanint$aa$bb",
                                     "pbkdf2_sha256$1000$aabb$", "pbkdf2_sha256$1000$aabb$ccdd$ee"])
def test_a_malformed_hash_is_refused_and_never_raises(encoded):
    assert local_auth.verify_password("x", encoded) is False


def test_a_hash_with_too_few_iterations_is_refused():
    assert local_auth.verify_password(PASSWORD, f"pbkdf2_sha256$10$aa$bb") is False


# ---------------------------------------------------- acceptance 4: unknown role names

def test_known_role_names_are_read_through_the_given_config_dir(config_dir):
    names = local_auth.known_role_names(config_dir)
    assert {"Admin", "DCS Admin", "GameMaster", "Alert", "DCS"} <= names
    assert "Moderator" in names, "a role declared in THIS bot.yaml must be known"
    elsewhere = local_auth.known_role_names(config_dir / "nope")
    assert "Moderator" not in elsewhere, "a missing config dir must not invent role names"


def test_a_role_name_only_declared_in_bot_yaml_is_accepted(config_dir):
    app, _ = _build(config_dir, _config([_user("mod1", ["Moderator"])]))
    backend = app.state.webui_auth.backend("local")
    assert backend.users["mod1"].roles == ("Moderator",) or \
        set(backend.users["mod1"].roles) == {"Moderator"}


def test_an_unknown_role_name_is_refused_loudly_at_startup(config_dir):
    app = create_app()
    with pytest.raises(ValueError) as excinfo:
        install_shell(app, node=_node(config_dir), config=_config([_user("specialk", ["Superuser"])]))
    message = str(excinfo.value)
    assert "Superuser" in message and "specialk" in message
    assert getattr(app.state, "webui_auth", None) is None, \
        "a refused install must not leave a half-configured identity layer behind"


def test_a_user_without_a_password_hash_is_refused(config_dir):
    with pytest.raises(ValueError):
        _build(config_dir, _config([{"username": "specialk", "roles": ["Admin"]}]))


def test_a_plaintext_password_field_is_not_accepted_as_a_hash(config_dir):
    with pytest.raises(ValueError) as excinfo:
        _build(config_dir, _config([{"username": "specialk", "password_hash": PASSWORD,
                                     "roles": ["Admin"]}]))
    assert "password_hash" in str(excinfo.value)


def test_local_backend_can_be_disabled_entirely(config_dir):
    app, _ = _build(config_dir, _config([_user("specialk", ["Admin"])], local_enabled=False))
    manager = app.state.webui_auth
    assert manager.backends == (), "a disabled local backend must not be registered at all"


# --------------------------------------------- acceptance 2: the exact configured role set

def test_a_correct_password_yields_exactly_the_roles_mapped_to_that_user(config_dir):
    """The capability set, not "logged in": the identity's roles equal the configured set, which
    in this fixture is also what bot.yaml's headless mapping grants that member."""
    app, _ = _build(config_dir, _config([_user("specialk", ["Admin"])]))
    manager = app.state.webui_auth
    identity = manager.login(None, "specialk", PASSWORD)
    assert identity is not None
    assert identity.roles == frozenset({"Admin"})
    assert set(identity.roles) == _bot_yaml_roles_for(config_dir, "specialk"), \
        "the configured role set disagrees with bot.yaml's headless mapping for this member"


def test_a_wrong_password_yields_nothing(config_dir):
    app, _ = _build(config_dir, _config([_user("specialk", ["Admin"])]))
    assert app.state.webui_auth.login(None, "specialk", "wrong") is None


def test_an_unknown_username_yields_nothing(config_dir):
    app, _ = _build(config_dir, _config([_user("specialk", ["Admin"])]))
    assert app.state.webui_auth.login(None, "nobody", PASSWORD) is None


# ------------------------------------------------ acceptance 1: reasons are logged, not shown

@pytest.mark.asyncio
async def test_login_refusals_log_a_reason_the_page_never_echoes(config_dir, caplog):
    app, _ = _build(config_dir, _config([_user("specialk", ["Admin"])]))
    async with asgi_client(app) as client:
        token = await _csrf_token(client)
        unknown = await client.post(auth_routes.LOGIN_PATH,
                                    data={"username": "nobody", "password": PASSWORD,
                                          "_csrf_token": token})
        wrong = await client.post(auth_routes.LOGIN_PATH,
                                  data={"username": "specialk", "password": "wrong",
                                        "_csrf_token": token})
    assert (unknown.status_code, wrong.status_code) == (401, 401)
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "unknown user" in messages
    assert "bad password" in messages
    for text in (unknown.text, wrong.text):
        assert "unknown user" not in text and "bad password" not in text, \
            "the reason is a log line, never a page"


@pytest.mark.asyncio
async def test_a_cookieless_request_is_refused_with_a_reason_at_debug(config_dir, caplog):
    """A request with no session is an EXPECTED refusal: it is explained at DEBUG, and NEVER at
    INFO or WARNING — a dashboard left open on an expired session polls forever, and a record per
    poll is the flood """
    app, registrar = _build(config_dir, _config([_user("specialk", ["Admin"])]))
    permissions.declare_capability("probe.admin", ("Admin",))
    registrar.register_pages("probe", simple_router("/probe/admin"),
                             capabilities={"/probe/admin": "probe.admin"})
    with caplog.at_level(logging.DEBUG):
        async with asgi_client(app) as client:
            response = await client.get("/probe/admin")
    assert response.status_code == 403
    messages = [record.getMessage() for record in caplog.records]
    assert any("no session" in message for message in messages), \
        "an anonymous refusal must still explain WHY (no session), just not loudly"
    loud = [record for record in caplog.records
            if record.name in ("services.webservice.auth",
                               "services.webservice.auth.discord_oauth",
                               "services.webservice.permissions")
            if record.levelno >= logging.INFO]
    assert loud == [], "\n".join(f"{r.levelname} {r.name}: {r.getMessage()}" for r in loud)


# ------------------------------------------------- the roles are resolved live, never stored

@pytest.mark.asyncio
async def test_the_session_stores_a_reference_and_never_the_roles(config_dir):
    app, registrar = _build(config_dir, _config([_user("specialk", ["Admin"])]))
    router = APIRouter()

    @router.get("/probe/whoami")
    async def whoami(request: Request):
        return {"ref": auth.session_identity_ref(request),
                "roles": sorted(permissions.role_names_for(request))}

    registrar.register_pages("probe", router, capabilities={"/probe/whoami": permissions.PUBLIC})
    async with asgi_client(app) as client:
        token = await _csrf_token(client)
        await client.post(auth_routes.LOGIN_PATH,
                          data={"username": "specialk", "password": PASSWORD,
                                "_csrf_token": token})
        payload = (await client.get("/probe/whoami"))
        assert payload.status_code == 200, (payload.status_code, payload.text)
        payload = payload.json()
    assert payload["ref"] == {"backend": "local", "subject": "specialk"}
    assert payload["roles"] == ["Admin"]


@pytest.mark.asyncio
async def test_the_same_cookie_authorises_differently_under_a_different_role_config(config_dir):
    """The cookie carries no roles: the same signed session against a deployment where the user
    holds no roles grants nothing. A role stored in the cookie would survive the change."""
    permissions.declare_capability("probe.admin", ("Admin",))
    cookie: str | None = None
    for roles, expected in ((["Admin"], 200), ([], 403)):
        app, registrar = _build(config_dir, _config([_user("specialk", roles)]))
        registrar.register_pages("probe", simple_router("/probe/admin"),
                                 capabilities={"/probe/admin": "probe.admin"})
        async with asgi_client(app) as client:
            if cookie:
                client.cookies.set(COOKIE_NAME, cookie)
            else:
                token = await _csrf_token(client)
                await client.post(auth_routes.LOGIN_PATH,
                                  data={"username": "specialk", "password": PASSWORD,
                                        "_csrf_token": token})
            assert (await client.get("/probe/admin")).status_code == expected
            cookie = client.cookies.get(COOKIE_NAME)


# --------------------------------------------------------------------- break-glass

def test_breakglass_is_absent_unless_explicitly_enabled(config_dir):
    app, _ = _build(config_dir, _config([_user("specialk", ["Admin"])]))
    assert app.state.webui_auth.backend("breakglass") is None


def test_breakglass_enabled_without_a_credential_is_refused(config_dir):
    with pytest.raises(ValueError) as excinfo:
        _build(config_dir, _config([_user("specialk", ["Admin"])], breakglass={"enabled": True}))
    assert "breakglass" in str(excinfo.value)


def test_breakglass_grants_admin_and_nothing_else(config_dir):
    config = _config([_user("specialk", ["Admin"])],
                     breakglass={"enabled": True, "username": "emergency",
                                 "password_hash": _hash("letmein")})
    app, _ = _build(config_dir, config)
    manager = app.state.webui_auth
    assert manager.backend("breakglass") is not None
    assert manager.login(None, "emergency", "letmein").roles == frozenset({"Admin"})
    assert manager.login(None, "emergency", "nope") is None


def test_breakglass_false_is_disabled(config_dir):
    app, _ = _build(config_dir, _config([_user("specialk", ["Admin"])],
                                        breakglass={"enabled": False, "username": "emergency",
                                                    "password_hash": _hash("letmein")}))
    assert app.state.webui_auth.backend("breakglass") is None


# ------------------------------------------------------------------------ the CLI

def test_the_hash_helper_is_documented_and_reachable():
    """The generation command the config asks for: `python -m services.webservice.auth.local --hash`."""
    module = local_auth.__name__
    assert module == "services.webservice.auth.local"
    assert callable(local_auth.main)
    assert "--hash" in (local_auth.__doc__ or "")


def test_the_hash_helper_prints_a_hash_and_a_pasteable_yaml_block(monkeypatch, capsys):
    """``main()`` is driven here with an explicit argv; the DOCUMENTED entry point
    (``python -m services.webservice.auth.local --hash``, with the parent-package import chain
    running the bot's parser on the real ``sys.argv``) is covered in a real subprocess by
    ``tests/test_webui_auth_cli.py``."""
    passwords = iter(["hunter2", "hunter2"])
    # the CLI reads through _read_secret: getpass on a console, stdin when it is piped
    monkeypatch.setattr(local_auth, "_read_secret", lambda *a, **k: next(passwords))
    assert local_auth.main(["--hash", "--iterations", str(local_auth.MIN_ITERATIONS),
                            "--username", "specialk", "--roles", "Admin, DCS Admin"]) == 0
    out = capsys.readouterr().out
    encoded = out.splitlines()[0]
    assert local_auth.is_password_hash(encoded)
    assert local_auth.verify_password("hunter2", encoded)
    assert "hunter2" not in out, "the password must never be printed"
    assert "- username: specialk" in out
    assert "roles: [Admin, DCS Admin]" in out


def test_the_hash_helper_refuses_mismatched_passwords(monkeypatch, capsys):
    passwords = iter(["one", "two"])
    # the CLI reads through _read_secret: getpass on a console, stdin when it is piped
    monkeypatch.setattr(local_auth, "_read_secret", lambda *a, **k: next(passwords))
    assert local_auth.main(["--hash"]) == 1
    assert "do not match" in capsys.readouterr().err


def test_the_hash_helper_without_hash_prints_usage(capsys):
    assert local_auth.main([]) == 2
    assert "--hash" in capsys.readouterr().out


# ------------------------------------------------------- the schema and the sample config

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "services" / "webservice" / "schemas" / "webservice_schema.yaml"
SAMPLE_PATH = REPO_ROOT / "samples" / "services" / "webservice.yaml"


def _validate(text: str, tmp_path):
    """Validate *text* against the service schema.

    The real extension (`unique_port`) lives in ``core/utils/validators.py``, which imports
    ``core`` -> psycopg, so it cannot be loaded on this interpreter; the stand-in only needs to
    exist for the unrelated ``port`` rule.
    """
    from pykwalify.core import Core
    extension = tmp_path / "ext.py"
    extension.write_text("def unique_port(value, rule_obj, path):\n    return True\n",
                         encoding="utf-8")
    source = tmp_path / "webservice.yaml"
    source.write_text(text, encoding="utf-8")
    Core(source_file=str(source), schema_files=[str(SCHEMA_PATH)],
         extensions=[str(extension)]).validate()


def test_the_sample_config_validates_against_the_schema(tmp_path):
    """The sample IS the documentation for `auth:`; a key the schema does not know is rejected at
    startup by ``Service.read_locals`` (lazy mode: warned; strict: refused), so the two are pinned
    together here."""
    _validate(SAMPLE_PATH.read_text(encoding="utf-8"), tmp_path)


@pytest.mark.parametrize("body", [
    "  auth:\n    bogus_key: 1\n",
    "  auth:\n    local:\n      enabled: true\n      bogus_key: 1\n",
    "  auth:\n    local:\n      enabled: true\n      users:\n"
    "        - username: a\n          password_hash: h\n          roles: Admin\n",
    "  auth:\n    breakglass:\n      enabled: maybe\n",
])
def test_the_schema_refuses_an_auth_block_it_does_not_define(body, tmp_path):
    """The positive control for the sample pin: the same validator must REJECT a key, a type and a
    shape the auth block does not define -- otherwise "the sample validates" proves nothing."""
    from pykwalify.errors import SchemaError
    with pytest.raises(SchemaError):
        _validate(f"DEFAULT:\n  debug: false\n  listen: 0.0.0.0\n  port: 9876\n{body}", tmp_path)


def test_the_auth_keys_live_in_the_service_config_that_is_actually_read():
    """`Service.read_locals` keys the file off `self.name.lower()` (`core/services/base.py:177`), so
    a `webui.yaml` carrying the auth block would be read by nothing. The auth keys live in
    `webservice.yaml`, whose schema is this file."""
    assert SAMPLE_PATH.name == "webservice.yaml"
    assert SCHEMA_PATH.name == "webservice_schema.yaml"
    assert SCHEMA_PATH.parent.parent.name == "webservice", \
        "the schema must belong to the service whose name keys the config file"
    assert "auth:" in SAMPLE_PATH.read_text(encoding="utf-8")


async def _csrf_token(client) -> str:
    import re
    response = await client.get(auth_routes.LOGIN_PATH)
    assert response.status_code == 200
    match = re.search(r'name="_csrf_token" value="([^"]*)"', response.text)
    assert match, "the login form must render the CSRF field"
    assert match.group(1), "the rendered CSRF token must not be empty"
    return match.group(1)
