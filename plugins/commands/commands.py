import asyncio
import discord
import os
import psutil
import psycopg
import re
import shlex
import subprocess

from core import Plugin, utils, Server, Status, Report, Command, Instance, Node, DEFAULT_TAG, Group, get_translation, \
    ServiceRegistry
from discord import app_commands, AppCommandOptionType
from discord.utils import MISSING
from pathlib import Path
from services.bot import DCSServerBot, BotService
from typing import Any, Mapping

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = get_translation(__name__.split('.')[1])

TYPE_MAP: Mapping[str, str] = {
    "node": "app_commands.Transform[Node, utils.NodeTransformer]",
    "server": "app_commands.Transform[Server, utils.ServerTransformer]",
    "instance": "app_commands.Transform[Instance, utils.InstanceTransformer]",
    "user":   "app_commands.Transform[discord.Member | str, utils.UserTransformer]",
    "member": "discord.Member",
    "channel": "discord.Channel",
    "role": "discord.Role"
}

APP_COMMAND_TYPE_MAP: Mapping[str, AppCommandOptionType] = {
    "str":        AppCommandOptionType.string,
    "int":        AppCommandOptionType.integer,
    "bool":       AppCommandOptionType.boolean,
    "member":     AppCommandOptionType.user,
    "channel":    AppCommandOptionType.channel,
    "role":       AppCommandOptionType.role,
    "mentionable": AppCommandOptionType.mentionable,
    "number":     AppCommandOptionType.number,
    "attachment": AppCommandOptionType.attachment,
}


async def process_autocomplete(interaction: discord.Interaction, current: int) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    plugin = ServiceRegistry.get(BotService).bot.cogs['Commands']
    return [app_commands.Choice(name=f"{name} ({p.pid})", value=p.pid) for p, name in plugin.processes.items()]


class Commands(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.commands: dict[str, dict] = {}
        self.processes: dict[psutil.Process, str] = {}

    async def cog_load(self):
        await super().cog_load()
        self.register_commands()

    async def cog_unload(self):
        self._unregister_commands()
        # stop processes
        for process in self.processes:
            process.terminate()
            if process.is_running():
                try:
                    await asyncio.to_thread(process.wait, timeout=30)
                except TimeoutError:
                    process.kill()
        await super().cog_unload()

    async def migrate(self, new_version: str, conn: psycopg.AsyncConnection | None = None) -> None:
        if new_version == '3.1':
            config = Path(self.node.config_dir) / 'plugins' / 'commands.yaml'
            if not config.exists():
                return
            data = yaml.load(config.read_text(encoding='utf-8'))
            if DEFAULT_TAG in data:
                data.pop(DEFAULT_TAG)
            if not 'commands' in data or not isinstance(data['commands'], list):
                return
            data['new_commands'] = {}
            for command in data.get('commands', []):
                data['new_commands'][command['name']] = command
                command.pop('name')
                command.pop('hidden', None)
                if 'execute' in command:
                    exe = command['execute']
                    for p in ['args', 'cwd']:
                        for w in ['server', 'instance']:
                            if f'{{{w}}}' in exe.get(p, ""):
                                exe[p] = exe[p].replace(f'{{{w}}}', f'{{{w}.name}}')
                if 'params' in command:
                    command['new_params'] = {}
                    for param in command['params']:
                        command['new_params'][param] = {
                            'required': True
                        }
                        if param not in ['server', 'instance']:
                            command['new_params'][param]['type'] = 'str'
                    command.pop('params')
                    command['params'] = command.pop('new_params')
            data.pop('commands')
            data['commands'] = data.pop('new_commands')
            with open(config, 'w', encoding='utf-8') as outfile:
                yaml.dump(data, outfile)
            self.locals = self.read_locals()
            # remove potential command_prefix in bot.yaml
            if 'command_prefix' in self.bot.locals:
                config = Path(self.node.config_dir) / 'services' / 'bot.yaml'
                data = yaml.load(config.read_text(encoding='utf-8'))
                data.pop('command_prefix', None)
                with open(config, 'w', encoding='utf-8') as outfile:
                    yaml.dump(data, outfile)

    async def _send(
        self,
        interaction: discord.Interaction,
        *msgs: str,
        embed: discord.Embed | None = None,
    ):
        await interaction.followup.send(msgs[0], embed=embed)
        for msg in msgs[1:]:
            await interaction.followup.send(msg)

    async def execute(self, interaction: discord.Interaction, config: dict, **kwargs):
        cmd = [config["cmd"]]
        if "args" in config:
            cmd.extend([utils.format_string(a, **kwargs) for a in shlex.split(config["args"])])
        cwd = os.path.expandvars(utils.format_string(config.get("cwd", "."), **kwargs)) or None

        if config.get("shell", False):
            try:

                def run_cmd():
                    result = subprocess.run(
                        cmd,
                        cwd=cwd,
                        capture_output=True,
                        text=True,
                        shell=True,
                    )
                    return result.stdout, result.stderr

                stdout, stderr = await asyncio.to_thread(run_cmd)

            except Exception as ex:
                await self._send(interaction, str(ex))
                return

            if not stdout:
                await self._send(interaction, "Done")
                return

            # split stdout into 2‑K chunks wrapped in code blocks
            lines = stdout.splitlines()
            messages: list[str] = []
            cur = "```"
            for line in lines:
                appended = cur + line + "\n"
                if len(appended) > 1994:
                    cur += "```"
                    messages.append(cur)
                    cur = "```" + line + "\n"
                else:
                    cur = appended
            cur += "```"
            messages.append(cur)

            for m in messages:
                await self._send(interaction, m)

        else:  # no shell – just fire and forget
            try:
                def run_cmd() -> subprocess.Popen:
                    executable = os.path.join(cwd, cmd[0])
                    return subprocess.Popen(cmd, cwd=cwd, executable=executable, close_fds=True)

                p = await asyncio.to_thread(run_cmd)
                self.processes[psutil.Process(p.pid)] = cmd[0]
                await self._send(interaction, f"Process with PID {p.pid} started.")
            except Exception as ex:
                await self._send(interaction, str(ex))

    async def event(
        self,
        interaction: discord.Interaction,
        config: dict,
        **kwargs,
    ) -> list[dict]:
        async def do_send(server: Server):
            if config.get("sync", False):
                if server.status != Status.SHUTDOWN:
                    return await server.send_to_dcs_sync(config)
                else:
                    await self._send(
                        interaction,
                        f"Server {server.name} is {server.status.name}.",
                    )
                    return None
            else:
                if server.status != Status.SHUTDOWN:
                    await server.send_to_dcs(config)
                    await self._send(interaction, f"Event sent to {server.name}.")
                else:
                    await self._send(
                        interaction,
                        f"Server {server.name} is {server.status.name}.",
                    )
                return None

        # replace parameters in the event data
        for k, v in config.copy().items():
            config[k] = utils.format_string(v, **kwargs)

        ret: list[dict] = []
        if "server" in kwargs:
            if isinstance(kwargs["server"], Server):
                rc = await do_send(kwargs["server"])
                if rc:
                    ret.append(rc)
            else:  # list of servers
                for server in kwargs["server"]:  # type: Server
                    rc = await do_send(server)
                    if rc:
                        ret.append(rc)
        else:
            for server in self.bot.get_servers(manager=interaction.user).values():
                rc = await do_send(server)
                if rc:
                    ret.append(rc)
        return ret

    async def exec_slash_command(
            self,
            interaction: discord.Interaction,
            **kwargs: Any
    ) -> None:
        cfg = self.commands[interaction.command.name]

        # remove any missing args
        for arg in kwargs.copy():
            if kwargs[arg] == MISSING:
                del kwargs[arg]

        if "server" in cfg:
            if isinstance(cfg["server"], str):
                server = self.bot.servers[cfg["server"]]
            else:  # list of servers
                server = [self.bot.servers[s] for s in cfg["server"]]
        elif 'server' not in kwargs:
            server = self.bot.get_server(interaction)
            if server:
                kwargs["server"] = server
        else:
            server = kwargs["server"]

        if cfg.get("server_only", False) and not server:
            return

        data: list[dict] = []

        if "execute" in cfg:
            await self.execute(interaction, cfg["execute"], **kwargs)
        elif "event" in cfg:
            data = await self.event(interaction, cfg["event"], **kwargs)
        elif "sequence" in cfg:
            for seq in cfg["sequence"]:
                if "execute" in seq:
                    await self.execute(interaction, seq["execute"], **kwargs)
                elif "event" in seq:
                    data.extend(await self.event(interaction, seq["event"], **kwargs))

        if "report" in cfg:
            if len(data) == 1:
                kwargs.update(data[0])
            elif len(data) > 1:
                await self._send(
                    interaction,
                    f"Can't call commands {interaction.command.name} on multiple servers.",
                )
                return
            report = Report(self.bot, self.plugin_name, cfg["report"])
            env = await report.render(**kwargs)
            await self._send(interaction, env.mention, embed=env.embed)
        elif data:
            if len(data) > 1:
                embed = discord.Embed(color=discord.Color.blue())
                for ret in data:
                    name = re.sub(
                        self.bot.locals.get("filter", {}).get("server_name", ""),
                        "",
                        ret["server_name"],
                    ).strip()
                    embed.add_field(name=name or "_ _", value=ret["value"] or "_ _", inline=False)
                await self._send(interaction, embed=embed)
            else:
                await self._send(interaction, data[0]["value"])

    @staticmethod
    def annotated_params(params: dict) -> str:
        parts: list[str] = []
        for name, param in params.items():
            typ = TYPE_MAP.get(name, param.get('type', 'str'))
            min_value = param.get('min_value', None)
            max_value = param.get('max_value', None)
            if (min_value or max_value) and typ in ['int', 'float', 'str']:
                typ = f"app_commands.Range[{typ}, {min_value or 'None'}, {max_value or 'None'}]"
            required = param.get('required', False)
            if not required:
                typ = f"{typ} | None"
            part = f"{name}: {typ}"
            default = param.get('default')
            if default is not None:
                part += f" = {repr(default)}"
            elif not required:
                part += " = None"
            parts.append(part)

        return ", ".join(parts)

    def register_commands(self):
        if "commands" not in self.locals:
            self.log.warning(f"No commands defined in {self.plugin_name}.yaml!")
            return

        for name, cmd in self.locals["commands"].items():
            sanitized_name = utils.to_valid_pyfunc_name(name)
            try:
                checks: list = []
                if "roles" in cmd:
                    # noinspection PyUnresolvedReferences
                    checks.append(utils.cmd_has_roles(roles=cmd["roles"]).predicate)

                params = cmd.get("params", {})

                if params:
                    # keyword‑only params
                    kw_only = self.annotated_params(params)
                    kw_as_args = ", ".join(f"{n}={n}" for n in params.keys())
                    src = f"""
async def __{sanitized_name}_callback(interaction: discord.Interaction, {kw_only}):
   await interaction.response.defer()
   await self.exec_slash_command(interaction, {kw_as_args})
                    """
                else:
                    # no options – only interaction
                    src = f"""
async def __{sanitized_name}_callback(interaction: discord.Interaction):
    await interaction.response.defer()
    await self.exec_slash_command(interaction)
                    """

                # Execute the source string → brand‑new function
                local_ns: dict[str, Any] = {}
                exec(
                    src,
                    {
                        "self": self,
                        "discord": discord,
                        "app_commands": app_commands,
                        "utils": utils,
                        "Server": Server,
                        "Instance": Instance,
                        "Node": Node
                    },
                    local_ns,
                )
                _callback = local_ns[f"__{sanitized_name}_callback"]
                _callback.__module__ = self.__module__
                _callback.__discord_app_commands_guild_only__ = True
                _callback.__discord_app_commands_contexts__ = app_commands.AppCommandContext(guild=True)

                # ----- create the Command ----------------------------------------
                cmd_desc = cmd.get("description") or f"{name} command"
                slash_cmd = Command(
                    name=name,
                    description=cmd_desc,
                    callback=_callback,
                    parent=None,
                    nsfw=cmd.get("nsfw", False),
                    auto_locale_strings=True
                )

                # Apply role checks, if any
                if checks:
                    [slash_cmd.add_check(check) for check in checks]

                # update parameters
                for p_name, spec in params.items():
                    orig = slash_cmd._params.get(p_name)
                    if not orig:
                        continue
                    if 'description' in spec:
                        orig.description = spec['description']
                    if 'required' in spec:
                        orig.required = spec['required']
                    if 'type' in spec:
                        orig.type = APP_COMMAND_TYPE_MAP.get(spec['type'], AppCommandOptionType.string)
                    if 'min_value' in spec:
                        orig.min_value = spec['min_value']
                    if 'max_value' in spec:
                        orig.max_value = spec['max_value']
                    if 'choices' in spec:
                        orig.choices=[
                            app_commands.Choice(name=str(x), value=str(x))
                            for x in spec.get('choices', [])
                        ]

                # register the command
                self.bot.tree.add_command(slash_cmd)
                self.commands[name] = cmd  # keep the YAML data
                self.log.info(f"     - Custom command /{name} added.")

            except Exception:
                self.log.exception(f"Failed to register command `{name}`", exc_info=True)

    def _unregister_commands(self):
        for name in list(self.commands.keys()):
            self.bot.tree.remove_command(name, guild=self.bot.guilds[0])
        self.commands.clear()

    # New command group "/commands"
    cmds = Group(name="commands", description=_("Commands to manage custom commands"))

    @cmds.command(description=_('Show running processes'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def tasklist(self, interaction: discord.Interaction):
        for process, cmd in self.processes.copy().items():
            if not process.is_running():
                self.processes.pop(process, None)
        if self.processes:
            embed = discord.Embed(title="Running processes", color=discord.Color.blue())
            embed.add_field(name="PID", value='\n'.join([str(x.pid) for x in self.processes.keys()]))
            embed.add_field(name="CMD", value='\n'.join([x for x in self.processes.values()]))
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(embed=embed, ephemeral=utils.get_ephemeral(interaction))
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("No running processes."), ephemeral=True)

    @cmds.command(description=_('Terminate a running process'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(pid=process_autocomplete)
    @app_commands.rename(pid='process')
    async def terminate(self, interaction: discord.Interaction, pid: int):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        process = psutil.Process(pid)
        if not process.is_running() or not self.processes.get(process):
            await interaction.followup.send(_("No such process or process terminated."), ephemeral=True)
            return

        process.terminate()
        if process.is_running():
            try:
                await asyncio.to_thread(process.wait, timeout=30)
            except TimeoutError:
                process.kill()

        if not process.is_running():
            await interaction.followup.send(_("Process terminated."), ephemeral=ephemeral)
            self.processes.pop(process, None)
        else:
            await interaction.followup.send(_("Process NOT terminated."), ephemeral=ephemeral)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Commands(bot))
