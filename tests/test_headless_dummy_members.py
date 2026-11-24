"""A headless (no_discord) install must resolve members and read the ban list without erroring.

Two reported failures, both real runs of the shipped bodies:

* ``plugins/userstats/highscore.py`` resolves member names through ``bot.get_members``; the
  headless ``DummyBot`` did not define it, so every highscore report died with AttributeError.
* ``plugins/cloud/commands.py`` pages the guild ban list with ``bans(limit=None)``; the
  headless ``DummyGuild.bans()`` took no arguments, so the cloud poller raised TypeError —
  and the block after it (``guild.ban``/``unban``) has nothing to act on headlessly anyway.
"""
import ast
import asyncio
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.bot.dummy import DummyBot, DummyGuild, DummyMember  # noqa: E402


def _guild(*member_keys):
    """A DummyGuild without its __init__ (that reads config/services/bot.yaml)."""
    guild = DummyGuild.__new__(DummyGuild)
    guild._id = 1
    guild._name = 'n/a'
    guild._roles = {}
    guild._members = {key: DummyMember(key) for key in member_keys}
    return guild


def _bot(guild=None):
    """A DummyBot without its __init__ (that needs a NodeImpl and a live ServiceBus)."""
    from services.bot.dummy.bot import DummyCommandTree, DummyIntents

    bot = DummyBot.__new__(DummyBot)
    bot.guilds = [guild or _guild()]
    bot.owner_id = -1
    bot.cogs = {}
    bot.bus = type('Bus', (), {'servers': {}})()
    bot.member = DummyMember("1", name="DCSServerBot")
    bot.user = bot.member
    bot.audit_channel = None
    bot.intents = DummyIntents()
    bot.tree = DummyCommandTree()
    return bot


# rows as the report SQL returns them: discord_id is a string, '-1' means "not linked".
ROWS = [
    {'discord_id': '110000000000000001', 'name': 'Linked', 'playtime': 3600},
    {'discord_id': '-1', 'name': 'NotLinked', 'playtime': 1800},
    {'discord_id': 'junk', 'name': 'Malformed', 'playtime': 900},
]


def test_headless_bot_resolves_members_by_id():
    bot = _bot(_guild('110000000000000001'))
    members = asyncio.run(bot.get_members(r['discord_id'] for r in ROWS if r['discord_id'] != '-1'))
    assert members == {110000000000000001: bot.guilds[0]._members['110000000000000001']}
    assert asyncio.run(bot.get_member(-1)) is None
    assert asyncio.run(bot.get_member('junk')) is None


def test_headless_highscore_falls_back_to_the_stored_name():
    """The body of plugins/userstats/highscore.py's label loop, against the headless bot."""
    bot = _bot()

    async def labels():
        members = await bot.get_members(r['discord_id'] for r in ROWS if r['discord_id'] != '-1')
        out = []
        for row in ROWS:
            try:
                member = members.get(int(row['discord_id']))
            except ValueError:
                member = None
            out.append(member.display_name if member else row['name'])
        return out

    assert asyncio.run(labels()) == ['Linked', 'NotLinked', 'Malformed']


def test_headless_guild_pages_an_empty_ban_list():
    bans = asyncio.run(_collect(_guild().bans(limit=None)))
    assert bans == []


async def _collect(iterator):
    return [entry async for entry in iterator]


def test_headless_guild_cannot_ban_so_the_cloud_guard_is_required():
    guild = _guild()
    assert not hasattr(guild, 'ban')
    assert not hasattr(guild, 'unban')


def test_cloud_poller_warns_instead_of_syncing_discord_bans(monkeypatch):
    """Run the shipped ``cloud_bans`` body with a headless bot and watch what it touches."""
    from plugins.cloud.commands import Cloud

    calls = []

    class _Log:
        def warning(self, msg):
            calls.append(('warning', msg))

        def error(self, msg):
            calls.append(('error', msg))

        def exception(self, ex):
            calls.append(('exception', ex))

    async def _get(path):
        calls.append(('get', path))
        return []

    class _Self:
        config = {'banlist': 'none', 'dcs-ban': False, 'watchlist_only': False, 'discord-ban': True}
        log = _Log()
        guild_bans = []
        _discord_ban_warned = False
        troublemakers = {}
        plugin_name = 'cloud-0'
        bot = _bot()

        get = staticmethod(_get)

    this = _Self()
    asyncio.run(Cloud.cloud_bans.coro(this))

    assert ('warning', 'discord-ban is configured, but this installation runs without Discord: '
                       'Discord bans cannot be synchronised.') in calls, calls
    assert not [c for c in calls if c[0] == 'exception'], calls
    # the ban list was never paged and the sync body never ran
    assert ('get', 'discord-bans') not in calls, calls
    assert this.guild_bans == []


def test_cloud_sync_body_would_die_on_a_headless_guild():
    """Why the guard exists: the Discord-ban sync has no guild surface to act on."""
    from plugins.cloud.commands import Cloud

    class _Self:
        guild_bans = []
        bot = _bot()

        async def get(self, path):
            return [{'discord_id': 110000000000000001, 'reason': 'cheating'}]

    # fetch_user resolves nothing headlessly, so the sync body fails on the very next statement
    with pytest.raises(AttributeError):
        asyncio.run(Cloud.sync_discord_bans(_Self()))


def test_headless_server_registration_checks_channels_without_raising():
    """``plugins/greenieboard/listener.py`` registerDCSServer, statement for statement."""
    bot = _bot()
    config = {'persistent_channel': '123456789', 'squadrons': [{'channel': '987654321'}, {}]}
    if 'persistent_channel' in config:
        bot.check_channel(int(config.get('persistent_channel')))
    for squadron in config.get('squadrons', []):
        if 'channel' in squadron:
            bot.check_channel(int(squadron['channel']))
    assert bot.check_channel(1234) is True


def test_headless_notify_and_lookup_surface():
    """The members listeners and reports reach for, with the shapes their callers expect."""
    bot = _bot(_guild('110000000000000001'))
    assert bot.mention_admin() == ''      # mission/listener and cloud/listener concatenate it
    assert bot.get_servers() == {}        # commands and gamemaster iterate it
    assert bot.get_cog('Cloud') is None   # mission/status guards on a falsy result
    assert list(bot.get_all_channels()) == []
    assert bot.get_user(110000000000000001) is bot.guilds[0].get_member('110000000000000001')
    assert asyncio.run(bot.fetch_embed('x', None)) is None
    assert asyncio.run(bot.change_presence(status=None)) is None
    assert bot.intents.members is False
    assert bot.intents.message_content is False
    assert bot.user.display_name == 'DCSServerBot'


def test_headless_command_tree_accepts_registration():
    """``plugins/commands`` registers custom slash commands during cog_load."""
    bot = _bot()
    cmd = type('Cmd', (), {'name': 'ping'})()
    bot.tree.add_command(cmd)
    assert bot.tree.get_command('ping') is cmd
    assert bot.tree.remove_command('ping', guild=bot.guilds[0]) is cmd
    assert asyncio.run(bot.tree.fetch_commands(guild=bot.guilds[0])) == []


#: Members ``DCSServerBot`` has that a headless install has no equivalent for, deliberately:
#: the audit poller, the role-config self-check, and the discord.py event hooks.
NO_HEADLESS_EQUIVALENT = {
    'audit_poll', 'before_audit_poll', 'check_roles', 'synced',
    'on_ready', 'on_error', 'on_command_error', 'on_app_command_error',
}


def _surface(path, cls):
    """Methods/properties of *cls* plus every attribute its body assigns on ``self``."""
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls)
    names = set()
    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            for target in ast.walk(stmt):
                if isinstance(target, ast.Name):
                    names.add(target.id)
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == 'self':
            if isinstance(n.ctx, ast.Store):
                names.add(n.attr)
    return {n for n in names if not n.startswith('_')}


def test_every_bot_member_plugins_call_exists_on_the_headless_bot():
    """A method the Discord bot grew without a headless answer must fail here, not on an install."""
    real = _surface(REPO_ROOT / 'services/bot/dcsserverbot.py', 'DCSServerBot')
    dummy = _surface(REPO_ROOT / 'services/bot/dummy/bot.py', 'DummyBot')
    missing = (real - dummy) - NO_HEADLESS_EQUIVALENT
    assert not missing, f'DummyBot is missing {sorted(missing)}'

    called = set()
    for path in (REPO_ROOT / 'plugins').rglob('*.py'):
        if '__pycache__' in path.parts:
            continue
        text = path.read_text(encoding='utf-8', errors='ignore')
        called |= set(re.findall(r'\b(?:self\.bot|\bbot)\.([A-Za-z_][A-Za-z0-9_]*)', text))
    # the exemption list may not go stale: a plugin calling one of those needs a real answer
    assert not (called & NO_HEADLESS_EQUIVALENT), sorted(called & NO_HEADLESS_EQUIVALENT)


def test_headless_bot_reports_no_audit_channel():
    """``services/bot/service.py`` ``send_message`` reads ``bot.audit_channel`` to fall back on.

    The attribute must exist on the class itself: the ratchet above proves ``__init__`` sets
    it, and ``None`` is what makes ``send_message`` return instead of raising."""
    src = (REPO_ROOT / 'services/bot/dummy/bot.py').read_text(encoding='utf-8')
    assert 'self.audit_channel = None' in src


def test_headless_send_message_returns_instead_of_raising():
    """Run the shipped ``BotService.send_message`` body with a headless bot and no channel."""
    from services.bot.service import BotService

    class _Self:
        bot = _bot()

    # channel=-1 and no audit channel: the body returns, where a missing attribute raised
    assert asyncio.run(BotService.send_message(_Self(), embed={'title': 'x'})) is None


def _mission_function(name):
    """The source of one ``Mission`` method, without importing the plugin (it pulls openpyxl).

    The shipped body runs unchanged; only the ``tasks.loop`` decorator is dropped, because a
    raw coroutine is what this test wants to drive."""
    path = REPO_ROOT / 'plugins/mission/commands.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'Mission':
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == name:
                    item.decorator_list = []
                    return ast.unparse(item)
    raise AssertionError(f'Mission.{name} not found')


def test_headless_status_channel_rename_skips_a_missing_channel():
    """``Mission.update_channel_name`` with no resolvable channel: skip the server, no raise."""
    import discord
    from core import Channel, Status

    namespace = {'discord': discord, 'Channel': Channel, 'Status': Status}
    exec(_mission_function('update_channel_name'), namespace)  # noqa: S102 - shipped body
    update_channel_name = namespace['update_channel_name']

    warnings = []

    class _Log:
        def warning(self, msg):
            warnings.append(msg)

        def debug(self, msg):
            ...

    class _Server:
        name = 'srv'
        status = Status.STOPPED
        channels = {Channel.STATUS: 1234}

    class _Bot:
        servers = {'srv': _Server()}
        member = DummyMember('1', name='DCSServerBot')

        def get_channel(self, channel_id):
            return None

        async def fetch_channel(self, channel_id):
            return None

    class _Self:
        bot = _Bot()
        log = _Log()

    asyncio.run(update_channel_name(_Self()))
    # a status channel IS configured, so the server must not be reported as misconfigured
    assert warnings == [], warnings
