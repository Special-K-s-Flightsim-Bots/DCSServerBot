import os
import shlex
import subprocess
from core import Plugin, DCSServerBot, TEventListener, utils
from discord.ext import commands
from discord.ext.commands import Command
from typing import Type


class Commands(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.commands: dict[str, dict] = dict()
        self._register_commands()

    def cog_unload(self):
        self._unregister_commands()

    async def exec_command(self, ctx: commands.Context, *args):
        config = self.commands[ctx.command.name]
        if 'params' in config:
            kwargs = dict(zip(config['params'], args))
        else:
            kwargs = dict()
        cmd: list[str] = [config['cmd']['exe']]
        if 'args' in config['cmd']:
            cmd.extend([utils.format_string(x, **kwargs) for x in shlex.split(config['cmd']['args'])])
        if 'shell' in config['cmd']:
            if 'cwd' in config['cmd']:
                cwd = os.path.expandvars(config['cmd']['cwd'])
            else:
                cwd = None
            try:
                p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, timeout=300)
            except Exception as ex:
                await ctx.send(ex)
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
            subprocess.Popen(cmd, executable=os.path.expandvars(config['cmd']['cwd']) + os.path.sep + config['cmd']['exe'])
            await ctx.send('Done.')

    def _register_commands(self):
        for cmd in self.locals['commands']:
            try:
                c = Command(self.exec_command, name=cmd['name'])
                params: dict[str, commands.Parameter] = dict()
                if 'params' in cmd:
                    for name in cmd['params']:
                        params[name] = commands.Parameter(name, commands.Parameter.POSITIONAL_OR_KEYWORD, annotation=str)
                    c.params = params
                self.bot.add_command(c)
                self.commands[cmd['name']] = cmd
                self.log.info(f"  - Custom command \"{self.bot.config['BOT']['COMMAND_PREFIX']}{cmd['name']}\" registered.")
            except commands.CommandRegistrationError as ex:
                self.log.info(f"  - Custom command \"{self.bot.config['BOT']['COMMAND_PREFIX']}{cmd['name']}\" NOT registered: {ex}")

    def _unregister_commands(self):
        for cmd in self.commands.keys():
            self.bot.remove_command(cmd)
            self.log.info(f"  - Custom command \"{self.bot.config['BOT']['COMMAND_PREFIX']}{cmd['name']}\" unregistered.")


async def setup(bot: DCSServerBot):
    await bot.add_cog(Commands(bot))
