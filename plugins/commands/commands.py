import asyncio
import discord
import os
import re
import shlex
import subprocess
import traceback

from core import Plugin, utils, Server, Status, Report
from discord.ext import commands
from discord.ext.commands import Command
from services.bot import DCSServerBot


class Commands(Plugin):
    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.commands: dict[str, dict] = dict()
        self.prefix = self.bot.locals.get('command_prefix', '.')
        self.register_commands()

    async def cog_unload(self):
        self._unregister_commands()
        await super().cog_unload()

    @staticmethod
    async def execute(ctx: commands.Context, config: dict, **kwargs) -> None:
        cmd: list[str] = [config['cmd']]
        if 'args' in config:
            cmd.extend([utils.format_string(x, **kwargs) for x in shlex.split(config['args'])])
        if 'cwd' in config:
            cwd = os.path.expandvars(config['cwd'])
        else:
            cwd = None
        if config.get('shell', False):
            try:
                def run_cmd():
                    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=True)
                    return result.stdout, result.stderr

                stdout, stderr = await asyncio.to_thread(run_cmd)
            except Exception as ex:
                traceback.print_exc()
                await ctx.send(ex.__str__())
                return
            if not stdout:
                await ctx.send('Done')
                return
            lines = stdout.splitlines()
            messages = []
            current_message = '```'
            for line in lines:
                appended = current_message + line + '\n'
                if len(appended) > 1994:
                    current_message += '```'
                    messages.append(current_message)
                    current_message = '```' + line + '\n'
                else:
                    current_message = appended
            current_message += '```'
            messages.append(current_message)
            for message in messages:
                await ctx.send(message)
        else:
            def run_cmd():
                subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

            await asyncio.to_thread(run_cmd)
            await ctx.send('Done.')

    async def event(self, ctx: commands.Context, config: dict, **kwargs) -> list[dict]:
        async def do_send(server: Server):
            if config.get('sync', False):
                if server.status != Status.SHUTDOWN:
                    return await server.send_to_dcs_sync(config)
                else:
                    await ctx.send(f'Server {server.name} is {server.status.name}.')
                    return None
            else:
                if server.status != Status.SHUTDOWN:
                    await server.send_to_dcs(config)
                    await ctx.send(f'Event sent to {server.name}.')
                else:
                    await ctx.send(f'Server {server.name} is {server.status.name}.')
                return None

        ret = []
        if 'server' in kwargs:
            if isinstance(kwargs['server'], Server):
                rc = await do_send(kwargs['server'])
                if rc:
                    ret.append(rc)
            else:
                for server in kwargs['server']:  # type: Server
                    rc = await do_send(server)
                    if rc:
                        ret.append(rc)
        else:
            for server in self.bot.servers.values():
                rc = await do_send(server)
                if rc:
                    ret.append(rc)
        return ret

    async def exec_command(self, ctx: commands.Context, *args):
        config = self.commands[ctx.command.name]
        if 'server' in config:
            if isinstance(config['server'], str):
                server = self.bot.servers[config['server']]
            elif isinstance(config['server'], list):
                server = [self.bot.servers[x] for x in config['server']]
            else:
                self.log.error("server must be string or list in commands.json!")
                return
        else:
            server = self.bot.get_server(ctx.message)
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
            await self.execute(ctx, config['execute'], **kwargs)
        elif 'event' in config:
            data = await self.event(ctx, config['event'], **kwargs)
        elif 'sequence' in config:
            for seq in config['sequence']:
                if 'execute' in seq:
                    await self.execute(ctx, seq['execute'], **kwargs)
                elif 'event' in seq:
                    data.extend(await self.event(ctx, seq['event'], **kwargs))
        if 'report' in config:
            if len(data) == 1:
                kwargs.update(data[0])
            elif len(data) > 1:
                await ctx.send(f"Can't call commands {ctx.command.name} on multiple servers.")
                return
            report = Report(self.bot, self.plugin_name, config['report'])
            env = await report.render(**kwargs)
            await ctx.send(env.mention, embed=env.embed)
        elif data:
            if len(data) > 1:
                embed = discord.Embed(color=discord.Color.blue())
                for ret in data:
                    name = re.sub(self.bot.locals.get('filter', {}).get('server_name', ''), '',
                                  ret['server_name']).strip()
                    embed.add_field(name=name or '_ _', value=ret['value'] or '_ _', inline=False)
                await ctx.send(embed=embed)
            else:
                await ctx.send(data[0]['value'])

    def register_commands(self):
        if 'commands' not in self.locals:
            self.log.warning(f"No commands defined in {self.plugin_name}.yaml!")
            return
        for cmd in self.locals['commands']:
            try:
                checks = []
                if 'roles' in cmd:
                    checks.append(utils.has_roles(cmd['roles']).predicate)
#                    checks.append(utils.app_has_roles(cmd['roles']).predicate)
                hidden = cmd['hidden'] if 'hidden' in cmd else False
                c = Command(self.exec_command, name=cmd['name'], checks=checks, hidden=hidden,
                            description=cmd.get('description', ''))
                params: dict[str, commands.Parameter] = dict()
                if 'params' in cmd:
                    for name in cmd['params']:
                        params[name] = commands.Parameter(name, commands.Parameter.POSITIONAL_OR_KEYWORD,
                                                          annotation=str)
                    c.params = params
                self.bot.add_command(c)
                self.commands[cmd['name']] = cmd
                self.log.info(f"  - Custom command \"{self.prefix}{cmd['name']}\" registered.")
            except commands.CommandRegistrationError as ex:
                self.log.info(f"  - Custom command \"{self.prefix}{cmd['name']}\" NOT registered: {ex}")

    def _unregister_commands(self):
        for cmd in self.commands.keys():
            self.bot.remove_command(cmd)
            self.log.info(f"  - Custom command \"{self.prefix}{cmd}\" unregistered.")


async def setup(bot: DCSServerBot):
    await bot.add_cog(Commands(bot))
