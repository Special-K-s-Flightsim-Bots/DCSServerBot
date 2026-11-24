"""The Phase 0 refactor left ``install.py`` behaving exactly as before.

``install.py`` now calls the shared helpers (``core.utils.db_setup``, ``core.utils.setup_config``,
``core.utils.detect_dcs_installation``). This drives the installer's OWN interactive paths in a
temporary config dir -- scripted answers, a fake PostgreSQL driver -- and compares the files it
writes against the golden shapes captured from the pre-refactor installer
(``fixtures/install_parity``).

Nothing here needs a real PostgreSQL; the DDL statements the installer issues are pinned too.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'install_parity'


# --- a fake psycopg driver ------------------------------------------------------------

class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, *, user_exists=False, database_exists=False):
        self.user_exists = user_exists
        self.database_exists = database_exists
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        import psycopg
        text = str(query)
        self.statements.append(text)
        if 'server_version_num' in text:
            return _Cursor(('16.2',))
        if 'CREATE USER' in text and self.user_exists:
            raise psycopg.Error('role already exists')
        if 'CREATE DATABASE' in text and self.database_exists:
            raise psycopg.Error('database already exists')
        return _Cursor((None,))

    def matching(self, needle):
        return [s for s in self.statements if needle in s]


class Answers:
    def __init__(self, seq):
        self.seq = list(seq)
        self.log = []

    def take(self, prompt, kind):
        if not self.seq:
            raise AssertionError(f'ran out of answers at {kind}: {prompt!r}')
        value = self.seq.pop(0)
        self.log.append((kind, prompt, value))
        return value


def _load_install_module():
    spec = importlib.util.spec_from_file_location('install_mod', REPO_ROOT / 'install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_install(monkeypatch, tmp_path, build_answers, *, fake_conn, find_instances,
                 public_ip: str | None = '1.2.3.4', verify_login=None, node='NODE1'):
    """Prepare a CWD, drive ``Install.install`` with scripted answers, return the run's state."""
    work = tmp_path / 'work'
    (work / 'samples' / 'plugins').mkdir(parents=True)
    shutil.copy2(REPO_ROOT / 'samples' / 'plugins' / 'cloud.yaml',
                 work / 'samples' / 'plugins' / 'cloud.yaml')
    (work / 'locale').mkdir()
    dcs = work / 'dcs'
    (dcs / 'Scripts').mkdir(parents=True)
    (dcs / 'Scripts' / 'MissionScripting.lua').write_text("sanitizeModule('io')\n", encoding="utf-8")
    srs = work / 'srs'
    srs.mkdir()

    module = _load_install_module()
    ans = Answers(build_answers(work, dcs, srs))
    module.Prompt = type('P', (), {'ask': staticmethod(lambda prompt, **kw: ans.take(prompt, 'Prompt'))})
    module.Confirm = type('C', (), {'ask': staticmethod(lambda prompt, default=None, **kw: ans.take(prompt, 'Confirm'))})
    module.IntPrompt = type('I', (), {'ask': staticmethod(lambda prompt, **kw: ans.take(prompt, 'IntPrompt'))})

    monkeypatch.setattr(module.psycopg, 'connect', lambda url, **kw: fake_conn)
    monkeypatch.setattr(module.utils, 'is_open', lambda ip, port, **kw: True)
    monkeypatch.setattr(module.utils, 'is_upnp_available', lambda: False)
    monkeypatch.setattr(module.utils, 'findDCSInstances', lambda *a, **k: find_instances)
    # the win32 installation scan answers nothing, so step 4 takes the path prompt this file scripts
    # on a Windows host exactly as it does here — the scan reports the operator's own installs
    monkeypatch.setattr(module.utils, 'detect_dcs_installation', lambda: [])
    # the SRS probe expands %ProgramFiles%: on a Windows host that has SRS installed for real it
    # answers 'found', the prompt never fires and every later answer shifts by one
    monkeypatch.setenv('ProgramFiles', str(work / 'no-srs'))

    if public_ip is None:
        async def _timeout():
            raise TimeoutError
        monkeypatch.setattr(module.utils, 'get_public_ip', _timeout)
    else:
        async def _ip():
            return public_ip
        monkeypatch.setattr(module.utils, 'get_public_ip', _ip)

    if verify_login is not None:
        from core.utils import db_setup
        monkeypatch.setattr(db_setup, 'verify_login', verify_login)

    config_dir = work / 'config'
    cwd = os.getcwd()
    os.chdir(work)
    try:
        module.Install(node=node).install(config_dir=str(config_dir), user='dcssb', database='dcssb')
    finally:
        os.chdir(cwd)

    assert not ans.seq, f'unused answers: {ans.seq}'
    return work, config_dir, fake_conn, ans


PROFILE = os.path.expanduser("~")


def _fold(text: str) -> str:
    """*text* in the corpus' spelling: host separators forward, the profile unquoted.

    The corpus writes the profile as ``%USERPROFILE%`` because that is what the installer emits on a
    host that does not expand it, and a value starting with ``%`` has to be quoted for YAML. Where the
    profile IS expanded the tree carries the concrete path, unquoted — so both the separators and the
    quoting around the placeholder are folded away, and the comparison is of the document, not of the
    host that wrote it.
    """
    text = text.replace("\\", "/")
    # the quote PAIR belongs to the whole scalar, so it is removed around any value carrying the
    # placeholder — never by hunting for a quote next to the placeholder itself
    return re.sub(r"""(['"])([^'"\n]*%USERPROFILE%[^'"\n]*)\1""", lambda m: m.group(2), text)


def _normalize(text, work):
    """*text* written by this host, folded into the corpus' spelling."""
    text = text.replace(str(work), "<WORK>")
    if PROFILE and PROFILE not in ("~", "/"):
        text = text.replace(PROFILE, "%USERPROFILE%")
    return _fold(text)


def _read(config_dir, work, *parts):
    return _normalize(Path(config_dir).joinpath(*parts).read_text(encoding='utf-8'), work)


def _fixture(name):
    return _fold((FIXTURES / name).read_text(encoding='utf-8'))


# --- scenario 1: the full Discord + DCS + SRS + instance + cloud install --------------

def _discord_answers(work, dcs, srs):
    return [
        'EN',                                              # language
        True,                                              # prerequisites fulfilled
        True,                                              # autoupdate
        True,                                              # use_discord
        100000000000000001,                                # guild id
        'discordtok',                                      # token
        200000000000000002,                                # owner id
        False,                                             # >10000 members -> privileged intents
        'Admin', 'DCS Admin', '@everyone',                 # role map
        -1,                                                # audit channel
        -1,                                                # admin channel
        'postgres', 'postgres', 'mpw',                     # master db / user / password
        str(dcs),                                          # dcs installation (linux path)
        True,                                              # 1.2.3.4 is static
        True, True,                                        # DGSA banlist opt-ins
        True,                                              # DCS autoupdate
        str(srs),                                          # srs installation path
        True,                                              # manage this server
        300000000000000003, -1, 500000000000000005,        # status / chat / admin channels
        'y',                                               # autostart
    ]


def test_full_discord_install_writes_the_golden_files(monkeypatch, tmp_path):
    work, config_dir, conn, _ = _run_install(
        monkeypatch, tmp_path, _discord_answers, fake_conn=FakeConn(),
        find_instances={'Instance1': 'Server X'})

    assert _read(config_dir, work, 'main.yaml') == _fixture('main.yaml')
    assert _read(config_dir, work, 'nodes.yaml') == _fixture('nodes.yaml')
    assert _read(config_dir, work, 'servers.yaml') == _fixture('servers.yaml')
    assert _read(config_dir, work, 'services', 'bot.yaml') == _fixture('bot.yaml')
    assert _read(config_dir, work, 'plugins', 'scheduler.yaml') == _fixture('scheduler.yaml')
    assert _read(config_dir, work, 'plugins', 'cloud.yaml') == _fixture('cloud.yaml')


def test_full_install_issues_the_installers_ddl(monkeypatch, tmp_path):
    _, _, conn, _ = _run_install(
        monkeypatch, tmp_path, _discord_answers, fake_conn=FakeConn(),
        find_instances={'Instance1': 'Server X'})
    assert conn.matching('server_version_num')
    assert conn.matching('CREATE USER')
    assert conn.matching('CREATE DATABASE')
    assert conn.matching('GRANT ALL PRIVILEGES ON DATABASE')
    assert conn.matching('ALTER DATABASE')


def test_full_install_writes_the_secret_placeholder_not_the_password(monkeypatch, tmp_path):
    from core.utils import db_setup
    work, config_dir, _, _ = _run_install(
        monkeypatch, tmp_path, _discord_answers, fake_conn=FakeConn(),
        find_instances={'Instance1': 'Server X'})
    nodes = _read(config_dir, work, 'nodes.yaml')
    assert db_setup._PASSWORD_PLACEHOLDER in nodes
    # the real bot password lives in config/.secret, never in the YAML
    assert (Path(config_dir) / '.secret' / 'database.pkl').exists()


# --- scenario 2: the non-Discord install ----------------------------------------------

def _no_discord_answers(work, dcs, srs):
    return [
        'EN',                                              # language
        True,                                              # prerequisites
        True,                                              # autoupdate
        False,                                             # use_discord -> non-Discord install
        'My Group',                                        # group name
        'uc-admin',                                        # Admin users
        'uc-dcsadmin',                                     # DCS Admin users
        'postgres', 'postgres', 'mpw',                     # master db / user / password
        str(dcs),                                          # dcs installation
        True, True,                                        # DGSA banlists
        True,                                              # DCS autoupdate
        str(srs),                                          # srs path
    ]


def test_no_discord_install_shapes(monkeypatch, tmp_path):
    work, config_dir, _, _ = _run_install(
        monkeypatch, tmp_path, _no_discord_answers, fake_conn=FakeConn(),
        find_instances={}, public_ip=None)

    from ruamel.yaml import YAML
    y = YAML()

    main = y.load(_read(config_dir, work, 'main.yaml'))
    assert list(main) == ['guild_id', 'guild_name', 'autoupdate']
    assert main['guild_name'] == 'My Group'
    assert isinstance(main['guild_id'], int)

    bot = y.load(_read(config_dir, work, 'services', 'bot.yaml'))
    assert bot == {'no_discord': True,
                   'roles': {'Admin': ['uc-admin'], 'DCS Admin': ['uc-dcsadmin']}}

    # no servers (findDCSInstances returned none) and no scheduler entries
    servers = y.load(_read(config_dir, work, 'servers.yaml'))
    assert servers == {}
    # the scheduler file IS written (the node key exists) but holds no instances
    scheduler = y.load(_read(config_dir, work, 'plugins', 'scheduler.yaml'))
    assert scheduler == {'NODE1': {}}


# --- scenario 3: the existing-role recovery is wired through --------------------------

def _existing_user_answers(work, dcs, srs):
    return [
        'EN', True, True, True,
        100000000000000001, 'discordtok', 200000000000000002, False,
        'Admin', 'DCS Admin', '@everyone', -1, -1,
        'postgres', 'postgres', 'mpw',
        # existing-role recovery: three wrong attempts, then the installer resets the password
        'bad1', 'bad2', 'bad3',
        str(dcs), True, True, True, True, str(srs),
        True,                                          # manage this server
        300000000000000003, -1, 500000000000000005, 'y',
    ]


def test_existing_user_recovery_resets_the_password(monkeypatch, tmp_path):
    _, _, conn, ans = _run_install(
        monkeypatch, tmp_path, _existing_user_answers, fake_conn=FakeConn(user_exists=True),
        find_instances={'Instance1': 'Server X'}, verify_login=lambda *a, **k: False)
    assert conn.matching('ALTER USER')
    prompts = [p for _, p, _ in ans.log if 'password for user' in p]
    assert len(prompts) == 3


# --- the master user is validated at the installer's prompt ---------------------------

def test_the_installer_reasks_until_the_master_user_is_safe(monkeypatch):
    """The master user becomes a ``config/.secret`` file name and a URL userinfo component, so an
    unsafe one is refused and re-asked, never stored. The installer's prompt is the source."""
    module = _load_install_module()
    monkeypatch.setattr(module, '_', lambda text: text)
    asked = []

    class Scripted:
        values = iter(['postgres', 'bad/user', 'postgres'])

        @staticmethod
        def ask(prompt, **kw):
            value = next(Scripted.values)
            asked.append((prompt, value))
            return value

    monkeypatch.setattr(module, 'Prompt', Scripted)
    master_db, master_user = module._InstallIO().master_credentials()
    assert (master_db, master_user) == ('postgres', 'postgres')
    user_prompts = [p for p, _ in asked if 'master user name' in p]
    assert len(user_prompts) == 2, 'the unsafe master user was not re-asked'

