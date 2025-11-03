from __future__ import annotations

import aiofiles
import asyncio
import discord.errors
import inspect
import json
import logging
import os
import psycopg
import shutil
import sqlparse
import sys

from abc import ABC, ABCMeta
from copy import deepcopy
from core import utils
from core.services.registry import ServiceRegistry
from discord import app_commands, Interaction
from discord.app_commands import locale_str
from discord.app_commands.commands import CommandCallback, GroupT, P, T
from discord.ext import commands, tasks
from discord.utils import MISSING, _shorten
from packaging.version import parse
from pathlib import Path
from typing import Type, TYPE_CHECKING, Any, Callable, Generic

from .const import DEFAULT_TAG
from .listener import TEventListener
from .utils.helper import YAMLError

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()

if TYPE_CHECKING:
    from core import Server
    from services.bot import DCSServerBot

BACKUP_FOLDER = 'config/backup/{}'

__all__ = [
    "BACKUP_FOLDER",
    "command",
    "Command",
    "Group",
    "Plugin",
    "PluginError",
    "PluginConflictError",
    "PluginRequiredError",
    "PluginConfigurationError",
    "PluginInstallationError"
]


def command(
    *,
    name: str | locale_str = MISSING,
    description: str | locale_str = MISSING,
    nsfw: bool = False,
    auto_locale_strings: bool = True,
    extras: dict[Any, Any] = MISSING,
) -> Callable[[CommandCallback[GroupT, P, T]], Command[GroupT, P, T]]:
    """Creates an application command from a regular function.

    Parameters
    ------------
    name: :class:`str`
        The name of the application command. If not given, it defaults to a lower-case
        version of the callback name.
    description: :class:`str`
        The description of the application command. This shows up in the UI to describe
        the application command. If not given, it defaults to the first line of the docstring
        of the callback shortened to 100 characters.
    nsfw: :class:`bool`
        Whether the command is NSFW and should only work in NSFW channels. Defaults to ``False``.

        Due to a Discord limitation, this does not work on subcommands.
    auto_locale_strings: :class:`bool`
        If this is set to ``True``, then all translatable strings will implicitly
        be wrapped into :class:`locale_str` rather than :class:`str`. This could
        avoid some repetition and be more ergonomic for certain defaults such
        as default command names, command descriptions, and parameter names.
        Defaults to ``True``.
    extras: :class:`dict`
        A dictionary that can be used to store extraneous data.
        The library will not touch any values or keys within this dictionary.
    """

    def decorator(func: CommandCallback[GroupT, P, T]) -> Command[GroupT, P, T]:
        if not inspect.iscoroutinefunction(func):
            raise TypeError('command function must be a coroutine function')

        if description is MISSING:
            if func.__doc__ is None:
                desc = '…'
            else:
                desc = _shorten(func.__doc__)
        else:
            desc = description

        return Command(
            name=name if name is not MISSING else func.__name__,
            description=desc,
            callback=func,
            parent=None,
            nsfw=nsfw,
            auto_locale_strings=auto_locale_strings,
            extras=extras,
        )

    return decorator


class Command(app_commands.Command[GroupT, P, T]):

    def __init__(
        self,
        *,
        name: str | locale_str,
        description: str | locale_str,
        callback: CommandCallback[GroupT, P, T],
        nsfw: bool = False,
        parent: Group | None = None,
        guild_ids: list[int] | None = None,
        auto_locale_strings: bool = True,
        extras: dict[Any, Any] = MISSING,
    ):
        from services.bot import BotService

        super().__init__(name=name, description=description, callback=callback, nsfw=nsfw, parent=parent,
                         guild_ids=guild_ids, auto_locale_strings=auto_locale_strings, extras=extras)
        self.mention = ""
        bot = ServiceRegistry.get(BotService).bot
        # remove node parameter from slash commands if only one node is there
        nodes = len(bot.node.all_nodes)
        if 'node' in self._params and nodes == 1:
            del self._params['node']
        # remove server parameter from slash commands if only one server is there
        num_servers = len(bot.servers)
        if ('server' in self._params and
                ((num_servers == 1 and nodes == 1) or not bot.locals.get('channels', {}).get('admin'))):
            del self._params['server']

    async def _do_call(self, interaction: Interaction, params: dict[str, Any]) -> T:
        if 'node' in inspect.signature(self._callback).parameters and 'node' not in params:
            params['node'] = interaction.client.node
        if 'server' in inspect.signature(self._callback).parameters and 'server' not in params:
            server = interaction.client.get_server(interaction)
            if not server:
                if len(interaction.client.servers) > 0:
                    try:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(
                            'No server registered for this channel. '
                            'If the channel is correct, please try again in a bit, when the server has registered.',
                            ephemeral=True)
                        return None
                    except discord.errors.NotFound:
                        pass
                else:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message('No servers registered yet.', ephemeral=True)
                    return None
            params['server'] = server
        return await super()._do_call(interaction=interaction, params=params)


class Group(app_commands.Group):

    def command(
        self,
        *,
        name: str | locale_str = MISSING,
        description: str | locale_str = MISSING,
        nsfw: bool = False,
        auto_locale_strings: bool = True,
        extras: dict[Any, Any] = MISSING,
    ) -> Callable[[CommandCallback[GroupT, P, T]], Command[GroupT, P, T]]:
        """A decorator that creates an application command from a regular function under this group.

        Parameters
        ------------
        name: str | :class:`locale_str`
            The name of the application command. If not given, it defaults to a lower-case
            version of the callback name.
        description: str | :class:`locale_str`
            The description of the application command. This shows up in the UI to describe
            the application command. If not given, it defaults to the first line of the docstring
            of the callback shortened to 100 characters.
        nsfw: :class:`bool`
            Whether the command is NSFW and should only work in NSFW channels. Defaults to ``False``.
        auto_locale_strings: :class:`bool`
            If this is set to ``True``, then all translatable strings will implicitly
            be wrapped into :class:`locale_str` rather than :class:`str`. This could
            avoid some repetition and be more ergonomic for certain defaults such
            as default command names, command descriptions, and parameter names.
            Defaults to ``True``.
        extras: :class:`dict`
            A dictionary that can be used to store extraneous data.
            The library will not touch any values or keys within this dictionary.
        """

        def decorator(func: CommandCallback[GroupT, P, T]) -> Command[GroupT, P, T]:
            if not inspect.iscoroutinefunction(func):
                raise TypeError('command function must be a coroutine function')

            if description is MISSING:
                if func.__doc__ is None:
                    desc = '…'
                else:
                    desc = _shorten(func.__doc__)
            else:
                desc = description

            command = Command(
                name=name if name is not MISSING else func.__name__,
                description=desc,
                callback=func,
                nsfw=nsfw,
                parent=self,
                auto_locale_strings=auto_locale_strings,
                extras=extras,
            )
            self.add_command(command)
            return command

        return decorator


class PluginMeta(type(commands.Cog), ABCMeta):
    """Metaclass that satisfies both CogMeta and ABCMeta."""
    pass


class Plugin(commands.Cog, Generic[TEventListener], metaclass=PluginMeta):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None, name: str | None = None):
        from services.servicebus import ServiceBus

        super().__init__()
        self.plugin_name = name or type(self).__module__.split('.')[-2]
        self.plugin_version = getattr(sys.modules['plugins.' + self.plugin_name], '__version__')
        self.bot: DCSServerBot = bot
        self.node = bot.node
        self.bus = ServiceRegistry.get(ServiceBus)
        self.log = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
        self.pool = self.bot.pool
        self.apool = self.bot.apool
        self.loop = self.bot.loop
        self.locals = self.read_locals()
        if self.plugin_name != 'commands' and 'commands' in self.locals:
            self.change_commands(self.locals['commands'], {x.name: x for x in self.get_app_commands()})
        self._config = dict[str, dict]()
        self.eventlistener: TEventListener = eventlistener(self) if eventlistener else None
        self.wait_for_on_ready.start()

    async def cog_load(self) -> None:
        await self.install()
        if self.eventlistener:
            self.bus.register_eventListener(self.eventlistener)
        self.log.info(f'  => {self.__cog_name__} loaded.')

    async def cog_unload(self) -> None:
        if self.eventlistener:
            await self.eventlistener.shutdown()
            self.bus.unregister_eventListener(self.eventlistener)
        # delete a possible configuration
        self._config.clear()
        self.log.info(f'  => {self.__cog_name__} unloaded.')

    def change_commands(self, cmds: dict, all_cmds: dict) -> None:
        for name, params in cmds.items():
            for cmd_name, cmd in all_cmds.items():
                if cmd_name == name and isinstance(cmd, Group):
                    group_commands = {x.name: x for x in cmd.commands}
                    if isinstance(params, list):
                        for param in params:
                            self.change_commands(param, group_commands)
                    elif isinstance(params, dict):
                        self.change_commands(params, group_commands)
                    else:
                        self.log.warning(f"{self.__cog_name__} command {name} has no params!")
                    break
                elif cmd_name == name and isinstance(cmd, Command):
                    if not params:
                        self.log.warning(
                            f"{self.__cog_name__}: Command overwrite of /{cmd.qualified_name} with no parameters!")
                        break
                    if cmd.parent:
                        cmd.parent.remove_command(cmd.name)
                    if not params.get('enabled', True):
                        if not cmd.parent:
                            self.__cog_app_commands__.remove(cmd)
                        break
                    if 'name' in params:
                        cmd.name = params['name']
                    if 'description' in params:
                        cmd.description = params['description']
                    if 'roles' in params:
                        for idx, check in enumerate(cmd.checks.copy()):
                            if 'has_role' in check.__qualname__:
                                cmd.remove_check(check)
                        if len(params['roles']):
                            # noinspection PyUnresolvedReferences
                            cmd.add_check(utils.cmd_has_roles(params['roles'].copy()).predicate)
                    if cmd.parent:
                        cmd.parent.add_command(cmd)
                    break
            else:
                self.log.warning(
                    f"Command/group \"/{name}\" not found in plugin \"{self.__cog_name__}\", can't overwrite it!")

    async def install(self) -> bool:
        if await self._init_db():
            # create report directories for convenience
            source_path = f'./plugins/{self.plugin_name}/reports'
            if os.path.exists(source_path):
                target_path = f'./reports/{self.plugin_name}'
                if not os.path.exists(target_path):
                    os.makedirs(target_path)
            return True
        return False

    async def migrate(self, new_version: str, conn: psycopg.AsyncConnection | None = None) -> None:
        pass

    async def before_dcs_update(self) -> None:
        pass

    async def after_dcs_update(self) -> None:
        pass

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: str | None = None) -> None:
        pass

    async def _init_db(self) -> bool:
        async with self.apool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cursor:
                    await cursor.execute('SELECT version FROM plugins WHERE plugin = %s', (self.plugin_name,))
                    # first installation
                    if cursor.rowcount == 0:
                        tables_file = f'./plugins/{self.plugin_name}/db/tables.sql'
                        if os.path.exists(tables_file):
                            async with aiofiles.open(tables_file, mode='r') as tables_sql:
                                for query in [
                                    stmt.strip()
                                    for stmt in sqlparse.split(await tables_sql.read(), encoding='utf-8')
                                    if stmt.strip()
                                ]:
                                    self.log.debug(query.rstrip())
                                    await cursor.execute(query.rstrip())
                        await cursor.execute("""
                            INSERT INTO plugins (plugin, version) VALUES (%s, %s) 
                            ON CONFLICT (plugin) DO NOTHING
                        """, (self.plugin_name, self.plugin_version))
                        self.log.info(f'  => {self.__cog_name__} installed.')
                        return True
                    else:
                        installed = (await cursor.fetchone())[0]
                        # old variant, to be migrated
                        if installed.startswith('v'):
                            installed = installed[1:]
                        while parse(installed) < parse(self.plugin_version):
                            updates_file = f'./plugins/{self.plugin_name}/db/update_v{installed}.sql'
                            if os.path.exists(updates_file):
                                async with aiofiles.open(updates_file, mode='r') as updates_sql:
                                    for query in [
                                        stmt.strip()
                                        for stmt in sqlparse.split(await updates_sql.read(), encoding='utf-8')
                                        if stmt.strip()
                                    ]:
                                        self.log.debug(query.rstrip())
                                        await conn.execute(query.rstrip())
                                ver, rev = installed.split('.')
                                installed = ver + '.' + str(int(rev) + 1)
                            elif int(self.plugin_version[0]) == 3 and int(installed[0]) < 3:
                                installed = '3.0'
                            else:
                                ver, rev = installed.split('.')
                                installed = ver + '.' + str(int(rev) + 1)
                            await self.migrate(installed, conn)
                            self.log.info(f'  => {self.__cog_name__} migrated to version {installed}.')
                            await cursor.execute('UPDATE plugins SET version = %s WHERE plugin = %s',
                                                 (self.plugin_version, self.plugin_name))
                        return False

    @staticmethod
    def migrate_to_3(node: str, plugin_name: str):
        os.makedirs(BACKUP_FOLDER.format(node), exist_ok=True)
        old_file = f'config/{plugin_name}.json'
        new_file = f'config/plugins/{plugin_name}.yaml'
        with open(old_file, mode='r', encoding='utf-8') as infile:
            old = json.load(infile)
        if os.path.exists(new_file):
            all_new = yaml.load(Path(new_file).read_text(encoding='utf-8'))
            exists = True
        else:
            all_new = {}
            exists = False
        if 'configs' in old:
            new = all_new[node] = {}
            for config in old['configs']:
                if 'installation' in config:
                    instance = config['installation']
                    new[instance] = config
                    del new[instance]['installation']
                elif not exists:
                    # we only overwrite the default on the master
                    all_new[DEFAULT_TAG] = config
            if 'commands' in old:
                all_new['commands'] = old['commands']
            if not all_new[node]:
                del all_new[node]
        else:
            all_new = old
        with open(new_file, mode='w', encoding='utf-8') as outfile:
            yaml.dump(all_new, outfile)
        shutil.move(old_file, BACKUP_FOLDER)

    def read_locals(self) -> dict:
        old_file = os.path.join(self.node.config_dir, f'{self.plugin_name}.json')
        new_file = os.path.join(self.node.config_dir, 'plugins', f'{self.plugin_name}.yaml')
        if os.path.exists(old_file):
            self.log.info('  => Migrating old JSON config format to YAML ...')
            self.migrate_to_3(self.node.name, self.plugin_name)
            self.log.info(f'  => Config file {old_file} migrated to {new_file}.')
        if os.path.exists(new_file):
            filename = new_file
        elif os.path.exists(f'./plugins/{self.plugin_name}/config/config.yaml'):
            filename = f'./plugins/{self.plugin_name}/config/config.yaml'
        else:
            return {}
        self.log.debug(f'  => Reading plugin configuration from {filename} ...')
        try:
            validation = self.node.config.get('validation', 'lazy')
            path = f'./plugins/{self.plugin_name}/schemas'
            if os.path.exists(path) and validation in ['strict', 'lazy']:
                schema_files = [str(x) for x in Path(path).glob('*.yaml')]
                if schema_files:
                    schema_files.append('schemas/commands_schema.yaml')
                    utils.validate(filename, schema_files, raise_exception=(validation == 'strict'))
                else:
                    self.log.warning(f'  - No schema files found for plugin {self.plugin_name}.')

            return yaml.load(Path(filename).read_text(encoding='utf-8'))
        except MarkedYAMLError as ex:
            raise YAMLError(filename, ex)

    # get default and specific configs to be merged in derived implementations
    def get_base_config(self, server: Server) -> tuple[dict | None, dict | None]:
        def get_theatre() -> str | None:
            if server.current_mission:
                return server.current_mission.map
            else:
                return asyncio.run(server.get_current_mission_theatre())

        def get_mission() -> str | None:
            if server.current_mission:
                return server.current_mission.name
            else:
                return os.path.basename(asyncio.run(server.get_current_mission_file()))[:-4]

        def filter_element(element: dict) -> dict:
            full = deepcopy(element)
            if 'terrains' in element:
                theatre = get_theatre()
                if not theatre:
                    return full
                del full['terrains']
                for _theatre in element['terrains'].keys():
                    if theatre.casefold() == _theatre.casefold():
                        return full | element['terrains'][_theatre]
                return full
            elif 'missions' in element:
                mission = get_mission()
                if not mission:
                    return full
                del full['missions']
                for _mission in element['missions'].keys():
                    if mission.casefold() == _mission.casefold():
                        return full | element['missions'][_mission]
                return full
            else:
                return element

        default = deepcopy(filter_element(self.locals.get(DEFAULT_TAG) or {}))
        specific = deepcopy(filter_element(self.locals.get(server.node.name, self.locals).get(server.instance.name) or {}))
        return default, specific

    def get_config(self, server: Server | None = None, *, plugin_name: str | None = None,
                   use_cache: bool | None = True) -> dict:
        # retrieve the config from another plugin
        if plugin_name:
            for plugin in self.bot.cogs.values():  # type: Plugin
                if plugin.plugin_name == plugin_name:
                    return plugin.get_config(server, use_cache=use_cache)
        if not server:
            return self.locals.get(DEFAULT_TAG, {})
        if server.node.name not in self._config:
            self._config[server.node.name] = {}
        if server.instance.name not in self._config[server.node.name] or not use_cache:
            default, specific = self.get_base_config(server)
            self._config[server.node.name][server.instance.name] = utils.deep_merge(default, specific)
        return self._config[server.node.name][server.instance.name]

    async def rename(self, conn: psycopg.AsyncConnection, old_name: str, new_name: str) -> None:
        # this function has to be implemented in your own plugins, if a server rename takes place
        pass

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        # this function has to be implemented in your own plugins, if the ucid of a user changed (steam <=> standalone)
        pass

    async def on_ready(self) -> None:
        pass

    @tasks.loop(count=1)
    async def wait_for_on_ready(self):
        await self.on_ready()

    @wait_for_on_ready.before_loop
    async def before_on_ready(self):
        await self.bot.wait_until_ready()


class PluginError(Exception, ABC):
    ...


class PluginRequiredError(PluginError):
    def __init__(self, plugin: str):
        super().__init__(f'Required plugin "{plugin.title()}" is missing!')


class PluginConflictError(PluginError):
    def __init__(self, plugin1: str, plugin2: str):
        super().__init__(f'Plugin "{plugin1.title()}" conflicts with plugin "{plugin2.title()}"!')


class PluginConfigurationError(PluginError):
    def __init__(self, plugin: str, option: str):
        super().__init__(f'Option "{option}" missing in {plugin}.yaml!')


class PluginInstallationError(PluginError):
    def __init__(self, plugin: str, reason: str):
        super().__init__(f'Plugin "{plugin.title()}" could not be installed: {reason}')
