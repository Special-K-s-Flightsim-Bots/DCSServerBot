import os
import shlex
import subprocess
from core import Plugin, DCSServerBot, TEventListener, utils, Server, Status, Report
from discord.ext import commands
from discord.ext.commands import Command
from typing import Type, Optional


class Commands(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.commands: dict[str, dict] = dict()
        self.register_commands()

    def cog_unload(self):
        self._unregister_commands()

    @staticmethod
    async def execute(ctx: commands.Context, config: dict, **kwargs) -> Optional[dict]:
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
                await ctx.send(ex.__str__())
                return
            output = p.stdout.decode('cp1252', 'ignore')
            if not output:
                await ctx.send('Done')
                return
            tmp = '```'
            for line in output.splitlines():
                if len(tmp) + len(line) > 1997:
                    tmp += '```'
                    await ctx.send(tmp)
                    tmp = '```'
                else:
                    tmp += line + '\n'
            if len(tmp) > 3:
                tmp += '```'
                await ctx.send(tmp)
        else:
            subprocess.Popen(cmd, executable=os.path.expandvars(config['cwd']) + os.path.sep + config['cmd'])
            await ctx.send('Done.')

    async def event(self, ctx: commands.Context, config: dict, **kwargs) -> list[dict]:
        if 'sync' in config:
            if 'server' in kwargs:
                server = kwargs['server']
                if server.status != Status.SHUTDOWN:
                    return [await server.sendtoDCSSync(config)]
                else:
                    return []
            else:
                ret = []
                for server in self.bot.servers.values():
                    if server.status != Status.SHUTDOWN:
                        ret.append(await server.sendtoDCSSync(config))
                return ret
        elif 'sync' not in config:
            if 'server' in kwargs:
                server = kwargs['server']
                if server.status != Status.SHUTDOWN:
                    server.sendtoDCS(config)
                    await ctx.send(f'Event sent to {server.name}.')
                else:
                    await ctx.send(f'Server {server.name} is {server.status.name}.')
            else:
                for server in self.bot.servers.values():
                    if server.status != Status.SHUTDOWN:
                        server.sendtoDCS(config)
                        await ctx.send(f'Event sent to {server.name}.')
                    else:
                        await ctx.send(f'Server {server.name} is {server.status.name}.')
                await ctx.send('Done.')

    async def exec_command(self, ctx: commands.Context, *args):
        config = self.commands[ctx.command.name]
        if 'server' in config:
            server: Server = self.bot.servers[config['server']]
        else:
            server: Server = await self.bot.get_server(ctx)
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
            await ctx.send(embed=env.embed)
        elif data:
            if len(data) > 1:
                for ret in data:
                    await ctx.send(f"{ret['server_name']}: {ret['value']}")
            else:
                await ctx.send(data[0]['value'])

    def register_commands(self):
        prefix = self.bot.config['BOT']['COMMAND_PREFIX']
        for cmd in self.locals['commands']:
            try:
                checks = []
                if 'roles' in cmd:
                    checks.append(utils.has_roles(cmd['roles']).predicate)
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
                self.log.info(f"  - Custom command \"{prefix}{cmd['name']}\" registered.")
            except commands.CommandRegistrationError as ex:
                self.log.info(f"  - Custom command \"{prefix}{cmd['name']}\" NOT registered: {ex}")

    def _unregister_commands(self):
        for cmd in self.commands.keys():
            self.bot.remove_command(cmd)
            self.log.info(f"  - Custom command \"{self.bot.config['BOT']['COMMAND_PREFIX']}{cmd}\" unregistered.")


async def setup(bot: DCSServerBot):
    await bot.add_cog(Commands(bot))
