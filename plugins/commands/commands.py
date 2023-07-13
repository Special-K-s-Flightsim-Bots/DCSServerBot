import discord
import os
import shlex
import subprocess

from core import Plugin, TEventListener, utils, Server, Status, Report, Command
from discord.app_commands import locale_str
from discord.app_commands.transformers import CommandParameter
from discord.ext import commands
from functools import partial
from services import DCSServerBot
from typing import Type, Optional, Union, cast


class Commands(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.commands: dict[str, dict] = dict()

    async def cog_unload(self):
        await self._unregister_commands()

    async def on_ready(self):
        await self.register_commands()

    @staticmethod
    async def execute(interaction: discord.Interaction, config: dict, **kwargs: Optional[Union[int, str]]) -> Optional[dict]:
        cmd: list[str] = [config['cmd']]
        if 'args' in config:
            cmd.extend([utils.format_string(x, **kwargs) for x in shlex.split(config['args'])])
        if 'shell' in config:
            if 'cwd' in config:
                cwd = os.path.expandvars(config['cwd'])
            else:
                cwd = None
            try:
                p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, timeout=300)
            except Exception as ex:
                await interaction.followup.send(ex.__str__())
                return
            output = p.stdout.decode('cp1252', 'ignore')
            if not output:
                await interaction.followup.send('Done')
                return
            tmp = '```'
            for line in output.splitlines():
                if len(tmp) + len(line) > 1997:
                    tmp += '```'
                    await interaction.followup.send(tmp)
                    tmp = '```'
                else:
                    tmp += line + '\n'
            if len(tmp) > 3:
                tmp += '```'
                await interaction.followup.send(tmp)
        else:
            subprocess.Popen(cmd, executable=os.path.expandvars(config['cwd']) + os.path.sep + config['cmd'])
            await interaction.followup.send('Done.')

    async def event(self, interaction: discord.Interaction, config: dict, **kwargs: Optional[Union[int, str]]) -> list[dict]:
        if 'sync' in config:
            if 'server' in kwargs:
                server: Server = cast(Server, kwargs['server'])
                if server.status != Status.SHUTDOWN:
                    return [await server.send_to_dcs_sync(config)]
                else:
                    return []
            else:
                ret = []
                for server in self.bot.servers.values():
                    if server.status != Status.SHUTDOWN:
                        ret.append(await server.send_to_dcs_sync(config))
                return ret
        elif 'sync' not in config:
            if 'server' in kwargs:
                server: Server = cast(Server, kwargs['server'])
                if server.status != Status.SHUTDOWN:
                    server.send_to_dcs(config)
                    await interaction.followup.send(f'Event sent to {server.name}.')
                else:
                    await interaction.followup.send(f'Server {server.name} is {server.status.name}.')
            else:
                for server in self.bot.servers.values():
                    if server.status != Status.SHUTDOWN:
                        server.send_to_dcs(config)
                        await interaction.followup.send(f'Event sent to {server.name}.')
                    else:
                        await interaction.followup.send(f'Server {server.name} is {server.status.name}.')
                await interaction.followup.send('Done.')

    async def exec_command(self, interaction: discord.Interaction, *args):
        await interaction.response.defer()
        config = self.commands[interaction.command.name]
        if 'server' in config:
            server: Server = self.bot.servers[config['server']]
        else:
            server: Server = await self.bot.get_server(interaction)
        if 'server_only' in config and config['server_only'] and not server:
            return
        if 'params' in config:
            kwargs = dict(zip(config['params'], args))
        else:
            kwargs = dict()
        if server:
            kwargs['server'] = server
        data: list[dict] = list()
        if 'execute' in config:
            await self.execute(interaction, config['execute'], **kwargs)
        elif 'event' in config:
            data = await self.event(interaction, config['event'], **kwargs)
        elif 'sequence' in config:
            for seq in config['sequence']:
                if 'execute' in seq:
                    await self.execute(interaction, seq['execute'], **kwargs)
                elif 'event' in seq:
                    data.extend(await self.event(interaction, seq['event'], **kwargs))
        if 'report' in config:
            if len(data) == 1:
                kwargs.update(data[0])
            elif len(data) > 1:
                await interaction.followup.send(f"Can't call commands {interaction.command.name} on multiple servers.")
                return
            report = Report(self.bot, self.plugin_name, config['report'])
            env = await report.render(**kwargs)
            await interaction.followup.send(embed=env.embed)
        elif data:
            if len(data) > 1:
                for ret in data:
                    await interaction.followup.send(f"{ret['server_name']}: {ret['value']}")
            else:
                await interaction.followup.send(data[0]['value'])

    async def register_commands(self):
        for cmd in self.locals['commands']:
            try:
                callback = partial(self.exec_command, Commands, discord.Interaction)
                callback.__globals__ = self.exec_command.__globals__
                callback.__qualname__ = callback.__name__ = cmd['name']
                c = Command(name=cmd['name'], description=cmd.get('description', ''),
                            callback=callback)
                if 'roles' in cmd:
                    c.add_check(utils.app_has_roles(cmd['roles'].copy()))
                params: dict[str, CommandParameter] = dict()
                if 'params' in cmd:
                    for name in cmd['params']:
                        params[name] = CommandParameter(name=name, description=locale_str('...'), required=True,
                                                        type=discord.AppCommandOptionType.string)
                    c._params = params
                self.bot.tree.add_command(c, guild=self.bot.guilds[0], override=True)
                self.commands[cmd['name']] = cmd
                self.log.info(f"  - Custom command \"{cmd['name']}\" registered.")
            except commands.CommandRegistrationError as ex:
                self.log.info(f"  - Custom command \"{cmd['name']}\" NOT registered: {ex}")
        self.bot.tree.copy_global_to(guild=self.bot.guilds[0])
        await self.bot.tree.sync(guild=self.bot.guilds[0])

    async def _unregister_commands(self):
        for cmd in self.commands.keys():
            self.bot.tree.remove_command(cmd)
            self.log.info(f"  - Custom command \"{cmd}\" unregistered.")
        self.bot.tree.copy_global_to(guild=self.bot.guilds[0])
        await self.bot.tree.sync(guild=self.bot.guilds[0])


async def setup(bot: DCSServerBot):
    await bot.add_cog(Commands(bot))
