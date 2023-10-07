import asyncio
import discord

from core import Plugin, PluginRequiredError, utils, Status, Server, Coalition, Channel, TEventListener, Group, Node, \
    Instance
from datetime import datetime, timedelta
from discord import app_commands
from discord.ext import tasks
from discord.ui import Modal, TextInput
from pathlib import Path
from services import DCSServerBot
from typing import Type, Optional, Literal

from .listener import SchedulerListener
from .views import ConfigView

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


class Scheduler(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.check_state.start()

    async def cog_unload(self):
        self.check_state.cancel()
        await super().cog_unload()

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            config = {}
            for instance in self.bus.node.instances:
                config[instance.name] = {}
            with open("config/plugins/scheduler.yaml", 'w') as outfile:
                yaml.dump(config, outfile)
        return config

    @staticmethod
    async def check_server_state(server: Server, config: dict) -> Status:
        if 'schedule' in config and not server.maintenance:
            warn_times: list[int] = Scheduler.get_warn_times(config) if server.is_populated() else [0]
            restart_in: int = max(warn_times)
            now: datetime = datetime.now()
            weekday = (now + timedelta(seconds=restart_in)).weekday()
            for period, daystate in config['schedule'].items():  # type: str, dict
                state = daystate[weekday]
                # check, if the server should be running
                if utils.is_in_timeframe(now, period) and state.upper() == 'Y' and server.status == Status.SHUTDOWN:
                    return Status.RUNNING
                elif utils.is_in_timeframe(now, period) and state.upper() == 'P' and \
                        server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED] and not server.is_populated():
                    return Status.SHUTDOWN
                elif utils.is_in_timeframe(now + timedelta(seconds=restart_in), period) and state.upper() == 'N' and \
                        server.status == Status.RUNNING:
                    return Status.SHUTDOWN
                elif utils.is_in_timeframe(now, period) and state.upper() == 'N' and \
                        server.status in [Status.PAUSED, Status.STOPPED]:
                    return Status.SHUTDOWN
        return server.status

    async def launch_dcs(self, server: Server, config: dict, member: Optional[discord.Member] = None):
        self.log.info(f'  => DCS server "{server.name}" starting up ...')
        try:
            await server.startup()
            if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                self.log.info(f'  => DCS server "{server.name}" NOT started.')
                return
            if not member:
                self.log.info(f'  => DCS server "{server.name}" started by '
                              f'{self.plugin_name.title()}.')
                await self.bot.audit(f"{self.plugin_name.title()} started DCS server", server=server)
            else:
                self.log.info(f'  => DCS server "{server.name}" started by '
                              f'{member.display_name}.')
                await self.bot.audit(f"started DCS server", user=member, server=server)
        except asyncio.TimeoutError:
            if server.status == Status.SHUTDOWN:
                self.log.warning(f'  => DCS server "{server.name}" was closed / crashed while launching!')
            else:
                self.log.warning(f'  => DCS server "{server.name}" timeout while launching.')
            raise

    @staticmethod
    def get_warn_times(config: dict) -> list[int]:
        return sorted(config.get('warn', {}).get('times', [0]), reverse=True)

    async def warn_users(self, server: Server, config: dict, what: str, max_warn_time: Optional[int] = None):
        if 'warn' in config:
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max_warn_time or max(warn_times)
            warn_text = config['warn'].get('text', '!!! {item} will {what} in {when} !!!')
            if what == 'restart_with_shutdown':
                what = 'restart'
                item = 'server'
            elif what == 'shutdown':
                item = 'server'
            else:
                item = 'mission'
            while restart_in > 0 and server.status == Status.RUNNING and not server.maintenance:
                for warn_time in warn_times:
                    if warn_time == restart_in:
                        server.sendPopupMessage(Coalition.ALL, warn_text.format(item=item, what=what,
                                                                                when=utils.format_time(warn_time)),
                                                server.locals.get('message_timeout', 10))
                        if 'sound' in config['warn']:
                            server.playSound(Coalition.ALL, utils.format_string(config['warn']['sound'],
                                                                                time=warn_time))
                        events_channel = self.bot.get_channel(server.channels[Channel.EVENTS])
                        if events_channel:
                            await events_channel.send(warn_text.format(item=item, what=what,
                                                                       when=utils.format_time(warn_time)))
                await asyncio.sleep(1)
                restart_in -= 1

    async def teardown_dcs(self, server: Server, member: Optional[discord.Member] = None):
        self.bot.bus.send_to_node({"command": "onShutdown", "server_name": server.name})
        await asyncio.sleep(1)
        await server.shutdown()
        if not member:
            self.log.info(
                f"  => DCS server \"{server.name}\" shut down by {self.plugin_name.title()}.")
            await self.bot.audit(f"{self.plugin_name.title()} shut down DCS server", server=server)
        else:
            self.log.info(
                f"  => DCS server \"{server.name}\" shut down by {member.display_name}.")
            await self.bot.audit(f"shut down DCS server", server=server, user=member)

    async def teardown(self, server: Server, config: dict):
        # if we should not restart populated servers, wait for it to be unpopulated
        populated = server.is_populated()
        if populated and not config.get('populated', True):
            return
        elif not server.restart_pending:
            server.restart_pending = True
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) else 0
            if restart_in > 0 and populated:
                self.log.info(f"  => DCS server \"{server.name}\" will be shut down "
                              f"by {self.plugin_name.title()} in {restart_in} seconds ...")
                await self.bot.audit(
                    f"{self.plugin_name.title()} will shut down DCS server in {utils.format_time(restart_in)}",
                    server=server)
                await self.warn_users(server, config, 'shutdown')
            # if the shutdown has been cancelled due to maintenance mode
            if not server.restart_pending:
                return
            await self.teardown_dcs(server)
            server.restart_pending = False

    async def restart_mission(self, server: Server, config: dict, rconf: dict, max_warn_time: int):
        # a restart is already pending, nothing more to do
        if server.restart_pending:
            return
        self.log.debug(f"Scheduler: restart_mission(server={server.name}, method={rconf['method']}) triggered.")
        method = rconf['method']
        # shall we do something at mission end only?
        if rconf.get('mission_end', False):
            self.log.debug(f"Scheduler: setting mission_end trigger.")
            server.on_mission_end = {'command': method}
            server.restart_pending = True
            return
        # check if the server is populated
        if server.is_populated():
            self.log.debug(f"Scheduler: Server is populated.")
            if not rconf.get('populated', True):
                if not server.on_empty:
                    server.on_empty = {'command': method}
                if 'max_mission_time' not in rconf:
                    self.log.debug("Scheduler: Setting on_empty trigger.")
                    server.restart_pending = True
                    return
                elif server.current_mission.mission_time <= (rconf['max_mission_time'] * 60 - max_warn_time):
                    self.log.debug("Scheduler: We have not reached max_mission_time yet, waiting.")
                    return
            server.restart_pending = True
            self.log.debug("Scheduler: Warning users ...")
            await self.warn_users(server, config, method, max_warn_time)
            # in the unlikely event that we did restart already in the meantime while warning users or
            # if the restart has been cancelled due to maintenance mode
            if not server.restart_pending:
                self.log.debug(f"Scheduler: After warning users: restart_pending={server.restart_pending}")
                return
            else:
                server.on_empty.clear()
        else:
            server.restart_pending = True

        if 'shutdown' in method:
            self.log.debug(f"Scheduler: Shutting down DCS Server {server.name}")
            await self.teardown_dcs(server)
        if method == 'restart_with_shutdown':
            try:
                self.log.debug(f"Scheduler: Starting DCS Server {server.name}")
                await self.launch_dcs(server, config)
            except asyncio.TimeoutError:
                await self.bot.audit(f"{self.plugin_name.title()}: Timeout while starting server",
                                     server=server)
        elif method == 'restart':
            self.log.debug(f"Scheduler: Restarting mission on server {server.name}")
            await server.restart(smooth=await server.apply_mission_changes())
            await self.bot.audit(f"{self.plugin_name.title()} restarted mission "
                                 f"{server.current_mission.display_name}", server=server)
        elif method == 'rotate':
            self.log.debug(f"Scheduler: Rotating mission on server {server.name}")
            # TODO: change this
            await server.loadNextMission()
            if await server.apply_mission_changes():
                await server.restart(smooth=True)
            await self.bot.audit(f"{self.plugin_name.title()} rotated to mission "
                                 f"{server.current_mission.display_name}", server=server)

    async def check_mission_state(self, server: Server, config: dict):
        def check_mission_restart(rconf: dict):
            # calculate the time when the mission has to restart
            if server.is_populated() and rconf.get('populated', 'True'):
                warn_times = Scheduler.get_warn_times(config)
            else:
                warn_times = [0]
            # we check the warn times in the opposite order to see which one fits best
            for warn_time in sorted(warn_times):
                if 'local_times' in rconf:
                    restart_time = datetime.now() + timedelta(seconds=warn_time)
                    for t in rconf['local_times']:
                        if utils.is_in_timeframe(restart_time, t):
                            asyncio.create_task(self.restart_mission(server, config, rconf, warn_time))
                            return
                elif 'mission_time' in rconf:
                    if (server.current_mission.mission_time + warn_time) >= rconf['mission_time'] * 60:
                        asyncio.create_task(self.restart_mission(server, config, rconf, warn_time))
                        return

        if 'restart' in config and not server.restart_pending:
            if isinstance(config['restart'], list):
                for r in config['restart']:
                    check_mission_restart(r)
            else:
                check_mission_restart(config['restart'])

    @tasks.loop(minutes=1.0)
    async def check_state(self):
        # check all servers
        for server_name, server in self.bot.servers.items():
            # only care about servers that are not in the startup phase
            if server.status in [Status.UNREGISTERED, Status.LOADING] or server.maintenance:
                continue
            config = self.get_config(server)
            # if no config is defined for this server, ignore it
            if config:
                try:
                    target_state = await self.check_server_state(server, config)
                    if target_state == Status.RUNNING and server.status == Status.SHUTDOWN:
                        asyncio.create_task(self.launch_dcs(server, config))
                    elif target_state == Status.SHUTDOWN and server.status in [
                        Status.STOPPED, Status.RUNNING, Status.PAUSED
                    ]:
                        asyncio.create_task(self.teardown(server, config))
                    elif server.status in [Status.RUNNING, Status.PAUSED]:
                        await self.check_mission_state(server, config)
                except Exception as ex:
                    self.log.exception(ex)

    @check_state.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()
        initialized = 0
        while initialized < len(self.bot.servers):
            initialized = 0
            for server_name, server in self.bot.servers.items():
                if server.status != Status.UNREGISTERED:
                    initialized += 1
            await asyncio.sleep(1)

    group = Group(name="server", description="Commands to manage a DCS server")

    @group.command(name='list', description='List of all registered servers')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction):
        if not self.bot.servers:
            await interaction.response.send_message("No servers registered.", ephemeral=True)
            return
        embed = discord.Embed(title=f"All Servers", color=discord.Color.blue())
        names = []
        status = []
        players = []
        for server in self.bot.servers.values():
            names.append(server.display_name)
            status.append(server.status.name.title())
            if server.status in [Status.RUNNING, Status.PAUSED]:
                players.append(f"{len(server.players) + 1}/{server.settings.get('maxPlayers', 0)}")
            else:
                players.append('-')
        if len(names):
            embed.add_field(name='Server', value='\n'.join(names))
            embed.add_field(name='Status', value='\n'.join(status))
            embed.add_field(name='Players', value='\n'.join(players))
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(description='Launches a DCS server')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def startup(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer]):
        config = self.get_config(server)
        if server.status == Status.STOPPED:
            await interaction.response.send_message(f"DCS server \"{server.display_name}\" is stopped.\n"
                                                    f"Please use /server start instead.", ephemeral=True)
            return
        if server.status == Status.LOADING:
            if not server.process.is_running():
                server.status = Status.SHUTDOWN
            else:
                await interaction.response.send_message(f"DCS server \"{server.display_name}\" is loading.\n"
                                                        f"Please wait or use /server shutdown force instead.",
                                                        ephemeral=True)
                return
        if server.status == Status.SHUTDOWN:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            msg = await interaction.followup.send(f"Starting DCS server \"{server.display_name}\", please wait ...",
                                                  ephemeral=True)
            # set maintenance flag to prevent auto-stops of this server
            server.maintenance = True
            try:
                await self.launch_dcs(server, config, interaction.user)
                await interaction.followup.send(
                    f"DCS server \"{server.display_name}\" started.\nServer is in maintenance mode now! "
                    f"Use `/scheduler clear` to reset maintenance mode.", ephemeral=True)
            except asyncio.TimeoutError:
                if server.status == Status.SHUTDOWN:
                    await interaction.followup.send(
                        f'Server {server.display_name} was closed / crashed while starting up!', ephemeral=True)
                else:
                    await interaction.followup.send(f'Timeout while launching DCS server "{server.display_name}".\n'
                                                    f'The server might be running anyway, check with /server list.',
                                                    ephemeral=True)
            finally:
                await msg.delete()

    @group.command(description='Shuts a DCS server down')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def shutdown(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(
                           status=[Status.RUNNING, Status.PAUSED, Status.STOPPED, Status.LOADING, Status.UNREGISTERED])],
                       force: Optional[bool]):
        async def do_shutdown(server: Server, force: bool = False):
            await interaction.followup.send(f"Shutting down DCS server \"{server.display_name}\", please wait ...",
                                            ephemeral=True)
            # set maintenance flag to prevent auto-starts of this server
            server.maintenance = True
            if force:
                await server.shutdown()
            else:
                await self.teardown_dcs(server, interaction.user)
            await interaction.followup.send(
                f"DCS server \"{server.display_name}\" shut down.\n"f"Server in maintenance mode now! "
                f"Use /scheduler clear to reset maintenance mode.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        if server.status in [Status.UNREGISTERED, Status.LOADING]:
            if force or await utils.yn_question(interaction, f"Server is in state {server.status.name}.\n"
                                                             f"Do you want to force a shutdown?"):
                await do_shutdown(server, True)
            else:
                return
        elif server.status != Status.SHUTDOWN:
            if not force:
                question = f"Do you want to shut down DCS server \"{server.display_name}\"?"
                if server.is_populated():
                    result = await utils.populated_question(interaction, question)
                else:
                    result = await utils.yn_question(interaction, question)
                if not result:
                    await interaction.followup.send('Aborted.', ephemeral=True)
                    return
                elif result == 'later':
                    server.on_empty = {"command": "shutdown", "user": interaction.user}
                    server.restart_pending = True
                    await interaction.followup.send('Shutdown postponed when server is empty.', ephemeral=True)
                    return
            await do_shutdown(server, force)
        else:
            await interaction.response.send_message(f"DCS server \"{server.display_name}\" is already shut down.",
                                                    ephemeral=True)

    @group.command(description='Starts a stopped DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def start(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer]):
        if server.status == Status.STOPPED:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                await server.start()
            except asyncio.TimeoutError:
                await interaction.followup.send(f"Timeout while trying to start server {server.name}.", ephemeral=True)
                return
            await interaction.followup.send(f"Server {server.display_name} started.", ephemeral=True)
            await self.bot.audit('started the server', server=server, user=interaction.user)
        elif server.status == Status.SHUTDOWN:
            await interaction.response.send_message(
                f"Server {server.display_name} is shut down. Use /server startup to start it up.", ephemeral=True)
        elif server.status in [Status.RUNNING, Status.PAUSED]:
            await interaction.response.send_message(f"Server {server.display_name} is already started.",
                                                    ephemeral=True)
        else:
            await interaction.response.send_message(
                f"Server {server.display_name} is still {server.status.name}, please wait ...", ephemeral=True)

    @group.command(description='Stops a running DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def stop(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(
                       status=[Status.RUNNING, Status.PAUSED])]):
        await interaction.response.defer(ephemeral=True)
        if server.is_populated() and \
                not await utils.yn_question(interaction, "People are flying on this server atm.\n"
                                                         "Do you really want to stop it?"):
            await interaction.followup.send("Aborted.", ephemeral=True)
            return
        msg = None
        try:
            msg = await interaction.followup.send(f"Stopping server {server.name} ...", ephemeral=True)
            await server.stop()
        except asyncio.TimeoutError:
            await interaction.followup.send(f"Timeout while trying to stop server {server.name}.", ephemeral=True)
            return
        finally:
            if msg:
                await msg.delete()
        await interaction.followup.send(f"Server {server.display_name} stopped.", ephemeral=True)
        await self.bot.audit('stopped the server', server=server, user=interaction.user)

    @group.command(description='Change the password of a DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def password(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer],
                       coalition: Optional[Literal['red', 'blue']] = None):
        class PasswordModal(Modal, title="Enter Password"):
            password = TextInput(label="New Password" + (f" for coalition {coalition}:" if coalition else ":"),
                                 style=discord.TextStyle.short, required=False)

            async def on_submit(derived, interaction: discord.Interaction):
                await interaction.response.defer()
                if coalition:
                    server.send_to_dcs({
                        "command": "setCoalitionPassword",
                        ("redPassword" if coalition == 'red' else "bluePassword"): derived.password.value or ''
                    })
                    with self.pool.connection() as conn:
                        with conn.transaction():
                            conn.execute('UPDATE servers SET {} = %s WHERE server_name = %s'.format(
                                'blue_password' if coalition == 'blue' else 'red_password'),
                                         (self.password, server.name))
                    await self.bot.audit(f"changed password for coalition {coalition}",
                                         user=interaction.user, server=server)
                else:
                    server.settings['password'] = derived.password.value or ''
                    await self.bot.audit(f"changed password", user=interaction.user, server=server)
                await interaction.followup.send("Password changed.", ephemeral=True)

        if not coalition and server.status in [Status.PAUSED, Status.RUNNING]:
            await interaction.response.send_message(f'Server "{server.display_name}" has to be stopped or shut down '
                                                    f'to change the password.', ephemeral=True)
            return
        elif coalition and server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            await interaction.response.send_message(f'Server "{server.display_name}" must not be shut down to change '
                                                    f'coalition passwords.', ephemeral=True)
            return
        await interaction.response.send_modal(PasswordModal())

    @group.command(description='Change the configuration of a DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def config(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer]):
        if server.status in [Status.RUNNING, Status.PAUSED]:
            if await utils.yn_question(interaction, question='Server has to be stopped to change its configuration.\n'
                                                             'Do you want to stop it?'):
                await server.stop()
            else:
                await interaction.response.send_message('Aborted.', ephemeral=True)
                return

        view = ConfigView(server)
        embed = discord.Embed(title=f'Do you want to change the configuration of server\n"{server.display_name}"?')
        if interaction.response.is_done():
            msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            msg = await interaction.original_response()
        try:
            await view.wait()
        finally:
            await msg.delete()

    @group.command(name='rename', description='Rename a DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def _rename(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.STOPPED, Status.SHUTDOWN])], new_name: str):
        old_name = server.name
        await server.rename(new_name, True)
        await interaction.response.send_message(f"Server {old_name} renamed to {new_name}.", ephemeral=True)

    @group.command(description="Migrate a server from one instance to another")
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def migrate(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer],
                      node: app_commands.Transform[Node, utils.NodeTransformer],
                      instance: app_commands.Transform[Instance, utils.InstanceTransformer]):
        if server.instance == instance:
            await interaction.response.send_message(
                f'Server "{server.name}" is already bound to instance "{instance.name}".')
            return
        if instance.server:
            if not await utils.yn_question(interaction, f"Instance {instance.name} is not empty.\n"
                                                        f"Do you want to unlink (and probably shutdown) server "
                                                        f"{instance.server.name} first?"):
                await interaction.followup.send("Aborted.", ephemeral=True)
        maintenance = server.maintenance
        running = False
        server.maintenance = True
        try:
            if server.status != Status.SHUTDOWN:
                if not await utils.yn_question(interaction,
                                               f"Do you want to shut down server {server.name} for migration?"):
                    await interaction.followup.send("Aborted", ephemeral=True)
                running = True
                await server.shutdown()
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            # prepare server for migration
            await server.persist_settings()
            if instance.server:
                await instance.server.persist_settings()
                if instance.server.status != Status.SHUTDOWN:
                    await instance.server.shutdown()
            await node.migrate_server(server, instance)
            await interaction.followup.send(f"DCS server {server.name} migrated to instance {instance.name}.",
                                                    ephemeral=True)
            await self.bot.audit(f"migrated DCS server to node {node.name} instance {instance.name}",
                                 user=interaction.user, server=server)
            if running:
                msg: discord.Message = await interaction.followup.send("Starting up ...", ephemeral=True)
                await server.startup()
                await msg.edit(content=f'DCS server "{server.display_name}" started.' +
                                       ('\nServer is in maintenance mode now! Use `/scheduler clear` '
                                        'to reset maintenance mode.' if maintenance else ''))
        finally:
            server.maintenance = maintenance

    # /scheduler commands
    scheduler = Group(name="scheduler", description="Commands to manage the Scheduler")

    @scheduler.command(description='Sets the servers maintenance flag')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def maintenance(self, interaction: discord.Interaction,
                          server: app_commands.Transform[Server, utils.ServerTransformer]):
        if not server.maintenance:
            if (server.restart_pending or server.on_empty or server.on_mission_end) and \
                    not await utils.yn_question(interaction, "Server is configured for a pending restart.\n"
                                                             "Setting the maintenance flag will abort this restart.\n"
                                                             "Are you sure?"):
                await interaction.followup.send("Aborted.", ephemeral=True)
                return
            server.maintenance = True
            server.restart_pending = False
            server.on_empty.clear()
            server.on_mission_end.clear()
            if interaction.response.is_done():
                await interaction.followup.send(f"Maintenance mode set for server {server.display_name}.",
                                                ephemeral=True)
            else:
                await interaction.response.send_message(f"Maintenance mode set for server {server.display_name}.",
                                                        ephemeral=True)
            await self.bot.audit("set maintenance flag", user=interaction.user, server=server)
        else:
            await interaction.response.send_message(f"Server {server.display_name} is already in maintenance mode.",
                                                    ephemeral=True)

    @scheduler.command(description='Clears the servers maintenance flag')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer]):
        if server.maintenance:
            server.maintenance = False
            await interaction.response.send_message(f"Maintenance mode cleared for server {server.display_name}.",
                                                    ephemeral=True)
            await self.bot.audit("cleared maintenance flag", user=interaction.user, server=server)
        else:
            await interaction.response.send_message(f"Server {server.display_name} is not in maintenance mode.",
                                                    ephemeral=True)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(Scheduler(bot, SchedulerListener))
