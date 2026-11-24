"""A headless (no_discord) install records audit entries in the cluster's audit table.

The channel announcement is the only part that needs Discord. An install without Discord used to
record NOTHING and log a misleading "Audit entry NOT recorded ... no bot that can record it is
available in this process" for every console action, while all ~174 ``bot.audit(...)`` call sites
across the plugins silently did nothing.

Each test drives a shipped body: ``DummyBot.audit``, ``DCSServerBot.audit`` (the other caller of the
shared helper), ``core.actions.audit_action`` and the console's ``audit_available``.
"""
import asyncio
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.action_results import ActionResult  # noqa: E402
from core.actions import AUDIT_NOT_RECORDED, AUDIT_RECORDED, ActionContext, audit_action, \
    can_record_audit  # noqa: E402
from services.bot.dummy import DummyBot, DummyMember  # noqa: E402

WRITES = []


class _Cursor:
    async def execute(self, sql, params=None):
        WRITES.append((' '.join(sql.split()), params))


class _Conn:
    async def __aenter__(self):
        return _Cursor()

    async def __aexit__(self, *exc):
        return False


class _Pool:
    """Stands in for psycopg's async pool: the shipped body does the writing."""

    def connection(self):
        return _Conn()


def _headless_bot():
    bot = DummyBot.__new__(DummyBot)
    bot.apool = _Pool()
    bot.node = type('Node', (), {'name': 'NODE-1'})()
    return bot


def _server(name='Special K - Development'):
    return type('S', (), {'name': name, 'locals': {}})()


class _DiscordBotSelf:
    """The attributes ``DCSServerBot.audit`` touches when there is no audit channel."""

    apool = _Pool()
    node = type('Node', (), {'name': 'NODE-1'})()
    audit_channel = None
    locals = {}
    member = None

    def get_channel(self, channel_id):
        return None


class _Result(ActionResult):
    pass


def _ctx(bot, backend='local', subject='specialk'):
    return ActionContext(node=bot.node, bus=None, _audit_backend=backend, _audit_subject=subject)


# --------------------------------------------------------------- the recorded row


def test_a_headless_bot_records_an_entry_with_its_actor():
    WRITES.clear()
    bot = _headless_bot()
    asyncio.run(bot.audit('Server started up.', server=_server(),
                          actor='[web:local/specialk]'))
    assert len(WRITES) == 1, WRITES
    sql, params = WRITES[0]
    assert 'INSERT INTO audit (node, event, server_name, actor, discord_id, ucid)' in sql, sql
    assert params == ('NODE-1', 'Server started up.',
                      'Special K - Development', '[web:local/specialk]', None, None), params


def test_the_sentence_carries_no_actor_and_the_channel_render_still_does():
    """The row keeps the actor in its column; the Discord embed keeps the leading token."""
    from services.bot.dcsserverbot import DCSServerBot

    sent = []

    class _Channel:
        async def send(self, content=None, embed=None, allowed_mentions=None):
            sent.append(embed.to_dict())

    class _Self(_DiscordBotSelf):
        audit_channel = _Channel()
        member = None

    WRITES.clear()
    asyncio.run(DCSServerBot.audit(_Self(), 'Server started up.', actor='[web:local/specialk]'))
    assert WRITES[0][1][1] == 'Server started up.', WRITES[0]
    assert WRITES[0][1][3] == '[web:local/specialk]', WRITES[0]
    assert sent and sent[0]['description'] == '[web:local/specialk] Server started up.', sent


def test_the_discord_bot_writes_the_same_row_through_the_shared_helper():
    """The other caller of ``core.utils.audit.record_audit`` must not drift from it."""
    from services.bot.dcsserverbot import DCSServerBot

    WRITES.clear()
    asyncio.run(DCSServerBot.audit(_DiscordBotSelf(), 'server started',
                                   user='0f6e9d8c4b1a4c2e8f7d6c5b4a392817'))
    assert len(WRITES) == 1, WRITES
    assert WRITES[0][1] == ('NODE-1', 'server started', None, None, None,
                            '0f6e9d8c4b1a4c2e8f7d6c5b4a392817'), WRITES[0]


def test_a_ucid_and_a_member_id_land_in_their_own_columns():
    WRITES.clear()
    bot = _headless_bot()
    asyncio.run(bot.audit('a', user='0f6e9d8c4b1a4c2e8f7d6c5b4a392817'))
    asyncio.run(bot.audit('b', user=110000000000000001))
    asyncio.run(bot.audit('c', user=DummyMember('0f6e9d8c4b1a4c2e8f7d6c5b4a392817')))
    # a ucid string, and a DummyMember whose id IS a ucid, go to ucid; a numeric id to discord_id
    assert WRITES[0][1][5] == '0f6e9d8c4b1a4c2e8f7d6c5b4a392817', WRITES[0]
    assert WRITES[1][1][4] == 110000000000000001, WRITES[1]
    assert WRITES[2][1][5] == '0f6e9d8c4b1a4c2e8f7d6c5b4a392817', WRITES[2]


# --------------------------------------------------------------- the gate that logged the ERROR


def test_a_headless_bot_can_record_an_audit_entry():
    assert can_record_audit(_headless_bot()) is True


def test_a_process_with_no_bot_cannot():
    assert can_record_audit(None) is False
    assert can_record_audit(type('NoSink', (), {})()) is False


def test_the_console_action_is_recorded_on_a_headless_install():
    """This is the call that produced "Audit entry NOT recorded ... no bot ... is available"."""
    WRITES.clear()
    bot = _headless_bot()
    result = _Result(success=True, message='Server started up.')
    out = asyncio.run(audit_action(_ctx(bot), result, server=_server(), bot=bot))
    assert out.data['audit'] == AUDIT_RECORDED, out.data
    assert len(WRITES) == 1, WRITES
    # the actor travels in its own column, not only inside the event text
    assert WRITES[0][1][3] == '[web:local/specialk]', WRITES[0]


def test_a_process_with_no_bot_still_reports_the_gap():
    """The ONE case the sentence is still true for: nothing here can write the entry."""
    result = _Result(success=True, message='x')
    out = asyncio.run(audit_action(_ctx(_headless_bot()), result, bot=object()))
    assert out.data['audit'] == AUDIT_NOT_RECORDED, out.data


def test_the_console_stops_disclosing_a_hole_it_no_longer_has():
    from services.webservice import config_apply

    assert config_apply.audit_available(_headless_bot()) is True


# --------------------------------------------------------------- schema


def _audit_block(sql: str) -> str:
    found = re.search(r'CREATE TABLE IF NOT EXISTS audit\s*\((.*?)\);', sql, re.S)
    assert found, 'the audit table is not in sql/tables.sql'
    return found.group(1)


def test_a_fresh_install_gets_the_actor_column_and_so_does_an_upgrade():
    sql = (REPO_ROOT / 'sql' / 'tables.sql').read_text(encoding='utf-8')
    assert 'actor TEXT' in _audit_block(sql), 'sql/tables.sql declares no actor column'

    migrations = sorted((REPO_ROOT / 'sql').glob('update_v*.sql'),
                        key=lambda path: [int(part) for part in re.findall(r'\d+', path.stem)])
    assert migrations, 'the install has no migration to upgrade with'
    # the chain must reach the version a fresh install declares, or an upgraded install sits behind
    final = re.search(r"UPDATE version SET version='([^']+)'",
                      migrations[-1].read_text(encoding='utf-8'))
    assert final, f'{migrations[-1].name} never sets the schema version'
    assert f"VALUES ('{final.group(1)}')" in sql
    # the actor column is added by the chain (whatever file carries it), idempotently
    alters = [path for path in migrations
              if 'ALTER TABLE audit ADD COLUMN IF NOT EXISTS actor TEXT' in
              path.read_text(encoding='utf-8')]
    assert alters, 'no migration adds the audit actor column'


def test_every_column_the_writer_names_exists_in_the_schema():
    """The insert lives in one module; a column added there must be added to the schema too."""
    schema = _audit_block((REPO_ROOT / 'sql' / 'tables.sql').read_text(encoding='utf-8'))
    columns = {part.strip().split()[0] for part in schema.split(',') if part.strip()}
    writer = (REPO_ROOT / 'core' / 'utils' / 'audit.py').read_text(encoding='utf-8')
    named = re.search(r'INSERT INTO audit \(([^)]*)\)', writer)
    assert named, 'the audit writer no longer names its columns'
    wanted = {column.strip() for column in named.group(1).split(',')}
    assert wanted <= columns, sorted(wanted - columns)
    # and the writer is the ONLY place that inserts, so the two cannot drift
    inserts = [path for path in list((REPO_ROOT / 'core').rglob('*.py'))
               + list((REPO_ROOT / 'services').rglob('*.py'))
               if 'INSERT INTO audit' in path.read_text(encoding='utf-8', errors='ignore')]
    assert inserts == [REPO_ROOT / 'core' / 'utils' / 'audit.py'], inserts
