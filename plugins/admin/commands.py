import asyncio
import discord
import os
import shutil

from core import utils, Plugin, Server, command, Node, UploadStatus, Group, Instance, Status, PlayerType, \
    PaginationReport
from discord import app_commands
from discord.app_commands import Range
from discord.ext import commands
from discord.ui import TextInput, Modal
from io import BytesIO
from services import DCSServerBot
from typing import Optional, Union, Literal
from zipfile import ZipFile, ZIP_DEFLATED

from .views import CleanupView
from ..scheduler.views import ConfigView

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


async def bans_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    choices: list[app_commands.Choice[int]] = [
        app_commands.Choice(name=f"{x['name']} ({x['ucid']})" if x['name'] else x['ucid'], value=x['ucid'])
        for x in interaction.client.bus.bans()
        if not current or (x['name'] and current.casefold() in x['name'].casefold()) or current.casefold() in x['ucid']
    ]
    return choices[:25]


async def watchlist_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    show_ucid = utils.check_roles(interaction.client.roles['DCS Admin'], interaction.user)
    async with interaction.client.apool.connection() as conn:
        cursor = await conn.execute("""
                        SELECT name, ucid FROM players WHERE watchlist IS TRUE AND (name ILIKE %s OR ucid ILIKE %s)
        """, ('%' + current + '%', '%' + current + '%'))
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=row[0] + (' (' + row[1] + ')' if show_ucid else ''), value=row[1])
            async for row in cursor
        ]
        return choices[:25]


async def available_modules_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        node = await utils.NodeTransformer().transform(interaction, utils.get_interaction_param(interaction, "node"))
        userid = node.locals['DCS'].get('dcs_user')
        password = node.locals['DCS'].get('dcs_password')
        available_modules = set(await node.get_available_modules(userid, password)) - set(await node.get_installed_modules())
        return [
            app_commands.Choice(name=x, value=x)
            for x in available_modules
            if not current or current.casefold() in x.casefold()
        ]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def installed_modules_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        node = await utils.NodeTransformer().transform(interaction, utils.get_interaction_param(interaction, "node"))
        available_modules = await node.get_installed_modules()
        return [
            app_commands.Choice(name=x, value=x)
            for x in available_modules
            if not current or current.casefold() in x.casefold()
        ]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def label_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(
            interaction, utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        config = interaction.client.cogs['Admin'].get_config(server)
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=x['label'], value=x['label']) for x in config['downloads']
            if ((not current or current.casefold() in x['label'].casefold()) and
                (not x.get('discord') or utils.check_roles(x['discord'], interaction.user)))
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def file_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(
            interaction, utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        label = utils.get_interaction_param(interaction, "what")
        config = interaction.client.cogs['Admin'].get_config(server)
        try:
            config = next(x for x in config['downloads'] if x['label'] == label)
        except StopIteration:
            return []
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=os.path.basename(x), value=os.path.basename(x))
            for x in await server.node.list_directory(config['directory'].format(server=server), config['pattern'])
            if not current or current.casefold() in x.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def plugins_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    return [
        app_commands.Choice(name=x, value=x)
        for x in interaction.client.cogs
        if not current or current.casefold() in x.casefold()
    ]


class Admin(Plugin):

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            self.log.info('  - No admin.yaml found, copying the sample.')
            shutil.copyfile('samples/plugins/admin.yaml', os.path.join(self.node.config_dir, 'plugins', 'admin.yaml'))
            config = super().read_locals()
        return config

    dcs = Group(name="dcs", description="Commands to manage your DCS installations")

    @dcs.command(description='Bans a user by name or ucid')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def ban(self, interaction: discord.Interaction,
                  user: Optional[app_commands.Transform[Union[discord.Member, str], utils.UserTransformer(
                      sel_type=PlayerType.PLAYER)]]):

        class BanModal(Modal):
            reason = TextInput(label="Reason", default="n/a", max_length=80, required=False)
            period = TextInput(label="Days (empty = forever)", required=False)

            def __init__(self, user: Union[discord.Member, str]):
                super().__init__(title="Ban Details")
                self.user = user

            async def on_submit(derived, interaction: discord.Interaction):
                days = int(derived.period.value) if derived.period.value else None
                if isinstance(derived.user, discord.Member):
                    ucid = await self.bot.get_ucid_by_member(derived.user)
                    if not ucid:
                        await interaction.response.send_message(f"Member {derived.user.display_name} is not linked!",
                                                                ephemeral=True)
                        return
                    name = derived.user.display_name
                else:
                    ucid = derived.user
                    # check if we should ban a member
                    name = await self.bot.get_member_or_name_by_ucid(ucid)
                    if isinstance(name, discord.Member):
                        name = name.display_name
                    elif not name:
                        name = ucid
                await self.bus.ban(ucid, interaction.user.display_name, derived.reason.value, days)
                await interaction.response.send_message(f"Player {name} banned on all servers" +
                                                        (f" for {days} days." if days else ""),
                                                        ephemeral=utils.get_ephemeral(interaction))
                await self.bot.audit(f'banned player {name} (ucid={ucid} with reason "{derived.reason.value}"' +
                                     (f' for {days} days.' if days else ' permanently.'), user=interaction.user)

            async def on_error(derived, interaction: discord.Interaction, error: Exception) -> None:
                self.log.exception(error)

        await interaction.response.send_modal(BanModal(user))

    @dcs.command(description='Unbans a user by name or ucid')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(ucid="user")
    @app_commands.autocomplete(ucid=bans_autocomplete)
    async def unban(self, interaction: discord.Interaction, ucid: str):
        await self.bus.unban(ucid)
        name = await self.bot.get_member_or_name_by_ucid(ucid)
        if isinstance(name, discord.Member):
            name = name.display_name
        elif not name:
            name = ucid
        await interaction.response.send_message(f"Player {name} unbanned on all servers.",
                                                ephemeral=utils.get_ephemeral(interaction))
        await self.bot.audit(f'unbanned player {name} (ucid={ucid})', user=interaction.user)

    @dcs.command(description='Shows active bans')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(user=bans_autocomplete)
    async def bans(self, interaction: discord.Interaction, user: str):
        try:
            ban = next(x for x in await self.bus.bans() if x['ucid'] == user)
        except StopIteration:
            await interaction.response.send_message(f"User with UCID {user} is not banned.", ephemeral=True)
            return
        embed = discord.Embed(title='Bans Information', color=discord.Color.blue())
        if ban['discord_id'] != -1:
            user = self.bot.get_user(ban['discord_id'])
        else:
            user = None
        embed.add_field(name=utils.escape_string(user.name if user else ban['name'] if ban['name'] else '<unknown>'),
                        value=ban['ucid'])
        if ban['banned_until'].year == 9999:
            until = 'never'
        else:
            until = ban['banned_until'].strftime('%y-%m-%d %H:%M')
        embed.add_field(name=f"Banned by: {ban['banned_by']}", value=f"Exp.: {until}")
        embed.add_field(name='Reason', value=ban['reason'])
        await interaction.response.send_message(embed=embed, ephemeral=utils.get_ephemeral(interaction))

    @dcs.command(description='Moves a player onto the watchlist')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def watch(self, interaction: discord.Interaction,
                    user: Optional[app_commands.Transform[Union[discord.Member, str], utils.UserTransformer(
                        sel_type=PlayerType.PLAYER)]]):
        if isinstance(user, discord.Member):
            ucid = await self.bot.get_ucid_by_member(user)
            if not ucid:
                await interaction.response.send_message(f"Member {user.display_name} is not linked.")
                return
        else:
            ucid = user
        for server in self.bus.servers.values():
            player = server.get_player(ucid=ucid)
            if player:
                if player.watchlist:
                    await interaction.response.send_message(f"Player {player.display_name} was already on the watchlist.",
                                                            ephemeral=utils.get_ephemeral(interaction))
                else:
                    player.watchlist = True
                    await interaction.response.send_message(f"Player {player.display_name} is now on the watchlist.",
                                                            ephemeral=utils.get_ephemeral(interaction))
                return
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE players SET watchlist = TRUE WHERE ucid = %s", (ucid, ))
        await interaction.response.send_message(
            "Player {} is now on the watchlist.".format(user.display_name if isinstance(user, discord.Member) else ucid),
            ephemeral=utils.get_ephemeral(interaction))

    @dcs.command(description='Removes a player from the watchlist')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(user=watchlist_autocomplete)
    async def unwatch(self, interaction: discord.Interaction, user: str):
        for server in self.bus.servers.values():
            player = server.get_player(ucid=user)
            if player:
                player.watchlist = False
                await interaction.response.send_message(f"Player {player.display_name} removed from the watchlist.",
                                                        ephemeral=utils.get_ephemeral(interaction))
                return
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE players SET watchlist = FALSE WHERE ucid = %s", (user, ))
        await interaction.response.send_message(f"Player {user} removed from the watchlist.",
                                                ephemeral=utils.get_ephemeral(interaction))

    @dcs.command(description='Shows the watchlist')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def watchlist(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)
        async with self.apool.connection() as conn:
            cursor = await conn.execute("SELECT ucid, name FROM players WHERE watchlist IS TRUE")
            watches = await cursor.fetchall()
            if not watches:
                await interaction.response.send_message("The watchlist is currently empty.", ephemeral=ephemeral)
                return
            embed = discord.Embed(colour=discord.Colour.blue())
            embed.description = "These players are currently on the watchlist:"
            names = ucids = ""
            for row in watches:
                ucids = row[0] + "\n"
                names += utils.escape_string(row[1]) + "\n"
            embed.add_field(name="UCIDs", value=ucids)
            embed.add_field(name="Names", value=names)
            await interaction.response.send_message(embed=embed)

    @dcs.command(description='Update your DCS installations')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(warn_time="Time in seconds to warn users before shutdown")
    async def update(self, interaction: discord.Interaction,
                     node: app_commands.Transform[Node, utils.NodeTransformer], warn_time: Range[int, 0] = 60):
        ephemeral = utils.get_ephemeral(interaction)
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        try:
            branch, old_version = await node.get_dcs_branch_and_version()
            new_version = await utils.getLatestVersion(branch,
                                                       userid=node.locals['DCS'].get('dcs_user'),
                                                       password=node.locals['DCS'].get('dcs_password'))
        except Exception:
            await interaction.followup.send("Can't get version information from ED, possible auth-server outage.",
                                            ephemeral=True)
            return
        if old_version == new_version:
            await interaction.followup.send(
                f'Your installed version {old_version} is the latest on branch {branch}.', ephemeral=ephemeral)
        elif new_version:
            if await utils.yn_question(interaction,
                                       f'Would you like to update from version {old_version} to {new_version}?\n'
                                       f'All running DCS servers will be shut down!', ephemeral=ephemeral) is True:
                await self.bot.audit(f"started an update of all DCS servers on node {node.name}.",
                                     user=interaction.user)
                msg = await interaction.followup.send(f"Updating DCS to version {new_version}, please wait ...",
                                                      ephemeral=ephemeral)
                try:
                    rc = await node.update(warn_times=[warn_time] or [120, 60])
                    if rc == 0:
                        await msg.edit(content=f"DCS updated to version {new_version} on node {node.name}.")
                        await self.bot.audit(f"updated DCS from {old_version} to {new_version} on node {node.name}.",
                                             user=interaction.user)
                    else:
                        await msg.edit(content=f"Error while updating DCS on node {node.name}, code={rc}")
                except (TimeoutError, asyncio.TimeoutError):
                    await msg.edit(content="The update takes longer than 10 minutes, please check back regularly, "
                                           "if it has finished.")
        else:
            await interaction.followup.send(
                f"Can't update branch {branch}. You might need to provide proper DCS credentials to do so.",
                ephemeral=ephemeral)

    @dcs.command(name='install', description='Install modules in your dcs server')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(module=available_modules_autocomplete)
    async def _install(self, interaction: discord.Interaction,
                       node: app_commands.Transform[Node, utils.NodeTransformer], module: str):
        ephemeral = utils.get_ephemeral(interaction)
        if not await utils.yn_question(interaction,
                                       f"Shutdown all servers on node {node.name} for the installation?",
                                       ephemeral=ephemeral):
            return
        await node.handle_module('install', module)
        await interaction.followup.send(f"Module {module} installed on node {node.name}", ephemeral=ephemeral)

    @dcs.command(name='uninstall', description='Uninstall modules from your server')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(module=installed_modules_autocomplete)
    async def _uninstall(self, interaction: discord.Interaction,
                         node: app_commands.Transform[Node, utils.NodeTransformer], module: str):
        ephemeral = utils.get_ephemeral(interaction)
        if not await utils.yn_question(interaction,
                                       f"Shutdown all servers on node {node.name} for the uninstallation?"):
            await interaction.followup.send("Aborted.", ephemeral=ephemeral)
            return
        await node.handle_module('uninstall', module)
        await interaction.followup.send(f"Module {module} uninstalled on node {node.name}", ephemeral=ephemeral)

    @command(description='Download files from your server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(what=label_autocomplete)
    @app_commands.autocomplete(filename=file_autocomplete)
    async def download(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer],
                       what: str, filename: str) -> None:
        ephemeral = utils.get_ephemeral(interaction)
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        config = next(x for x in self.get_config(server)['downloads'] if x['label'] == what)
        path = os.path.join(config['directory'].format(server=server), filename)
        file = await server.node.read_file(path)
        target = config.get('target')
        if target:
            target = target.format(server=server)
        if not filename.endswith('.zip') and not filename.endswith('.miz') and not filename.endswith('acmi') and \
                len(file) >= 25 * 1024 * 1024:
            zip_buffer = BytesIO()
            with ZipFile(zip_buffer, "a", ZIP_DEFLATED, False) as zip_file:
                zip_file.writestr(filename, file)
            file = zip_buffer.getvalue()
        if not target:
            dm_channel = await interaction.user.create_dm()
            for channel in [dm_channel, interaction.channel]:
                try:
                    await channel.send(file=discord.File(fp=BytesIO(file), filename=filename))
                    if channel == dm_channel:
                        await interaction.followup.send('File sent as a DM.', ephemeral=ephemeral)
                    else:
                        await interaction.followup.send('Here is your file:', ephemeral=ephemeral)
                    break
                except discord.HTTPException:
                    continue
            else:
                await interaction.followup.send('File too large. You need a higher boost level for your server.',
                                                ephemeral=ephemeral)
                return
        elif target.startswith('<'):
            channel = self.bot.get_channel(int(target[4:-1]))
            try:
                await channel.send(file=discord.File(fp=BytesIO(file), filename=filename))
            except discord.HTTPException:
                await interaction.followup.send('File too large. You need a higher boost level for your server.',
                                                ephemeral=ephemeral)
            if channel != interaction.channel:
                await interaction.followup.send('File sent to the configured channel.', ephemeral=ephemeral)
            else:
                await interaction.followup.send('Here is your file:', ephemeral=ephemeral)
        else:
            with open(os.path.expandvars(target), mode='wb') as outfile:
                outfile.write(file)
            await interaction.followup.send('File copied to the specified location.', ephemeral=ephemeral)
        await self.bot.audit(f"downloaded {filename}", user=interaction.user, server=server)

    @command(name='prune', description='Prune unused data in the database')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def _prune(self, interaction: discord.Interaction,
                     user: Optional[app_commands.Transform[Union[discord.Member, str], utils.UserTransformer(
                         sel_type=PlayerType.PLAYER)]] = None):
        ephemeral = utils.get_ephemeral(interaction)
        if not user:
            embed = discord.Embed(title=":warning: Database Prune :warning:")
            embed.description = "You are going to delete data from your database. Be advised.\n\n" \
                                "Please select the data to be pruned:"
            view = CleanupView()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral)
            try:
                await view.wait()
            finally:
                await interaction.delete_original_response()
            if view.cmd == "cancel":
                await interaction.followup.send('Aborted.', ephemeral=ephemeral)
                return
        elif not await utils.yn_question(interaction,
                                         f"We are going to delete all data of user {user}. Are you sure?"):
            await interaction.followup.send('Aborted.', ephemeral=ephemeral)
            return

        async with self.apool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cursor:
                    if user:
                        for plugin in self.bot.cogs.values():  # type: Plugin
                            await plugin.prune(conn, ucids=[user])
                            await cursor.execute('DELETE FROM players WHERE ucid = %s', (user, ))
                            await cursor.execute('DELETE FROM players_hist WHERE ucid = %s', (user, ))
                            await interaction.followup.send(f"Data of user {user} deleted.")
                            return
                    if view.what in ['users', 'non-members']:
                        sql = (f"SELECT ucid FROM players "
                               f"WHERE last_seen < (DATE((now() AT TIME ZONE 'utc')) - interval '{view.age} days')")
                        if view.what == 'non-members':
                            sql += ' AND discord_id = -1'
                        await cursor.execute(sql)
                        ucids = [row[0] async for row in cursor]
                        if not ucids:
                            await interaction.followup.send('No players to prune.', ephemeral=ephemeral)
                            return
                        if not await utils.yn_question(interaction, f"This will delete {len(ucids)} players incl. "
                                                                    f"their stats from the database.\n"
                                                                    f"Are you sure?", ephemeral=ephemeral):
                            return
                        for plugin in self.bot.cogs.values():  # type: Plugin
                            await plugin.prune(conn, ucids=ucids)
                        for ucid in ucids:
                            await cursor.execute('DELETE FROM players WHERE ucid = %s', (ucid, ))
                            await cursor.execute('DELETE FROM players_hist WHERE ucid = %s', (ucid,))
                        await interaction.followup.send(f"{len(ucids)} players pruned.", ephemeral=ephemeral)
                    elif view.what == 'data':
                        days = int(view.age)
                        if not await utils.yn_question(interaction, f"This will delete all data older than {days} "
                                                                    f"days from the database.\nAre you sure?",
                                                       ephemeral=ephemeral):
                            return
                        for plugin in self.bot.cogs.values():  # type: Plugin
                            await plugin.prune(conn, days=days)
                        await interaction.followup.send(f"All data older than {days} days pruned.",
                                                        ephemeral=ephemeral)
        await self.bot.audit(f'pruned the database', user=interaction.user)

    node_group = Group(name="node", description="Commands to manage your nodes")

    @node_group.command(name='stats', description='Statistics of your nodes')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def statistics(self, interaction: discord.Interaction,
                         node: Optional[app_commands.Transform[Node, utils.NodeTransformer]] = None,
                         period: Optional[Literal['Hour', 'Day', 'Week', 'Month']] = 'Hour'):
        report = PaginationReport(self.bot, interaction, self.plugin_name, 'nodestats.json')
        if not node:
            node = self.node
        await report.render(node=node.name, period=period)

    @node_group.command(name='list', description='Status of all nodes')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def _list(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed = discord.Embed(title=f"DCSServerBot Cluster Overview", color=discord.Color.blue())
        # TODO: there should be a list of nodes, with impls / proxies
        for name in self.node.all_nodes.keys():
            names = []
            instances = []
            status = []
            for server in [server for server in self.bus.servers.values() if server.node.name == name]:
                instances.append(server.instance.name)
                names.append(server.name)
                status.append(server.status.name)
            if names:
                title = f"**[{name}]**" if name == self.node.name else f"[{name}]"
                if await server.node.upgrade_pending():
                    embed.set_footer(text="🆕 Update available")
                    title += " 🆕"

                embed.add_field(name="▬" * 32, value=title, inline=False)
                embed.add_field(name="Instance", value='\n'.join(instances))
                embed.add_field(name="Server", value='\n'.join(names))
                embed.add_field(name="Status", value='\n'.join(status))
            else:
                embed.add_field(name="▬" * 32, value=f"_[{name}]_", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=utils.get_ephemeral(interaction))

    async def run_on_nodes(self, interaction: discord.Interaction, method: str, node: Optional[Node] = None):
        ephemeral = utils.get_ephemeral(interaction)
        if not node:
            msg = f"Do you want to {method} all nodes?\n"
        else:
            msg = f"Do you want to {method} node {node.name}?\n"
        if not await utils.yn_question(interaction, msg, ephemeral=ephemeral):
            await interaction.followup.send('Aborted.', ephemeral=ephemeral)
            return
        if method != 'upgrade' or node:
            for n in await self.node.get_active_nodes():
                if not node or n == node.name:
                    self.bus.send_to_node({
                        "command": "rpc",
                        "object": "Node",
                        "method": method
                    }, node=n)
                    await interaction.followup.send(f'Node {n} - {method} sent.', ephemeral=ephemeral)
        if not node or node.name == self.node.name:
            await interaction.followup.send(("All nodes are" if not node else "Master is") + f' going to {method} **NOW**.',
                                            ephemeral=ephemeral)
            if method == 'shutdown':
                await self.node.shutdown()
            elif method == 'upgrade':
                await self.node.upgrade()
            elif method == 'restart':
                await self.node.restart()

    @node_group.command(description='Deprecated!')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def exit(self, interaction: discord.Interaction,
                   node: Optional[app_commands.Transform[Node, utils.NodeTransformer]] = None):
        await interaction.response.send_message("`/node exit` is deprecated. Please use either `/node shutdown` or "
                                                "`/node restart` instead.", ephemeral=True)

    @node_group.command(description='Shuts a specific node down')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def shutdown(self, interaction: discord.Interaction,
                       node: Optional[app_commands.Transform[Node, utils.NodeTransformer]] = None):
        await self.run_on_nodes(interaction, "shutdown", node)

    @node_group.command(description='Restart a specific node')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def restart(self, interaction: discord.Interaction,
                      node: Optional[app_commands.Transform[Node, utils.NodeTransformer]] = None):
        await self.run_on_nodes(interaction, "restart", node)

    @node_group.command(description='Shuts down all servers on the respective node and sets them to maintenance mode')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def offline(self, interaction: discord.Interaction,
                      node: app_commands.Transform[Node, utils.NodeTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        for server in self.bus.servers.values():
            if server.node.name == node.name:
                server.maintenance = True
                asyncio.create_task(server.shutdown())
        await interaction.followup.send(f"Node {node.name} is now offline.")

    @node_group.command(description='Clears the maintenance mode for all servers on the respective node')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def online(self, interaction: discord.Interaction,
                     node: app_commands.Transform[Node, utils.NodeTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        for server in self.bus.servers.values():
            if server.node.name == node.name:
                server.maintenance = False
                asyncio.create_task(server.startup())
        await interaction.followup.send(f"Node {node.name} is now online.")

    @node_group.command(description='Upgrade DCSServerBot')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def upgrade(self, interaction: discord.Interaction,
                      node: Optional[app_commands.Transform[Node, utils.NodeTransformer]] = None):
        ephemeral = utils.get_ephemeral(interaction)
        await interaction.response.defer(ephemeral=ephemeral)
        if not node:
            node = self.node
            cluster = True
        else:
            cluster = False
        if not await node.upgrade_pending():
            await interaction.followup.send("There is no upgrade available for " +
                                            ("your cluster" if cluster else ("node " + node.name)),
                                            ephemeral=ephemeral)
            return
        if node and not node.master and not await utils.yn_question(
                interaction, "You are trying to upgrade an agent node in a cluster. Are you really sure?",
                ephemeral=ephemeral):
            await interaction.followup.send('Aborted', ephemeral=ephemeral)
            return
        await self.run_on_nodes(interaction, "upgrade", node if not cluster else None)

    @node_group.command(description='Run a shell command on a node')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def shell(self, interaction: discord.Interaction,
                    node: app_commands.Transform[Node, utils.NodeTransformer],
                    cmd: str):
        ephemeral = utils.get_ephemeral(interaction)
        await interaction.response.defer(ephemeral=ephemeral)
        stdout, stderr = await node.shell_command(cmd)
        embed = discord.Embed(colour=discord.Color.blue())
        if stdout:
            embed.description = "```" + stdout[:4090] + "```"
        if stderr:
            embed.set_footer(text=stderr[:2048])
        if not stdout and not stderr:
            embed.description = "```Command executed.```"
        await interaction.followup.send(embed=embed)

    @command(description='Reloads a plugin')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(plugin=plugins_autocomplete)
    async def reload(self, interaction: discord.Interaction, plugin: Optional[str]):
        ephemeral = utils.get_ephemeral(interaction)
        await interaction.response.defer(ephemeral=ephemeral)
        if plugin:
            if await self.bot.reload(plugin.lower()):
                await interaction.followup.send(f'Plugin {plugin} reloaded.', ephemeral=ephemeral)
            else:
                await interaction.followup.send(
                    f'Plugin {plugin} could not be reloaded, check the log for details.', ephemeral=ephemeral)
        else:
            if await self.bot.reload():
                await interaction.followup.send(f'All plugins reloaded.', ephemeral=ephemeral)
            else:
                await interaction.followup.send(
                    f'One or more plugins could not be reloaded, check the log for details.', ephemeral=ephemeral)
        # for server in self.bus.servers.values():
        #    if server.status == Status.STOPPED:
        #        server.send_to_dcs({"command": "reloadScripts"})

    @node_group.command(description="Add/create an instance")
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(name=utils.InstanceTransformer(unused=True).autocomplete)
    @app_commands.describe(name="Either select an existing instance or enter the name of a new one")
    @app_commands.describe(template="Take this instance configuration as a reference")
    async def add_instance(self, interaction: discord.Interaction,
                           node: app_commands.Transform[Node, utils.NodeTransformer], name: str,
                           template: Optional[app_commands.Transform[Instance, utils.InstanceTransformer]] = None):
        instance = await node.add_instance(name, template=template)
        if instance:
            await self.bot.audit(f"added instance {instance.name} to node {node.name}.", user=interaction.user)
            server: Server = instance.server
            view = ConfigView(self.bot, server)
            embed = discord.Embed(title="Instance created.\nDo you want to configure the server for this instance?",
                                  color=discord.Color.blue())
            try:
                await interaction.response.send_message(embed=embed, view=view)
            except Exception as ex:
                self.log.exception(ex)
            if not await view.wait() and not view.cancelled:
                config_file = os.path.join(self.node.config_dir, 'servers.yaml')
                with open(config_file, mode='r', encoding='utf-8') as infile:
                    config = yaml.load(infile)
                config[server.name] = {
                    "channels": {
                        "status": server.locals.get('channels', {}).get('status', -1),
                        "chat": server.locals.get('channels', {}).get('chat', -1)
                    }
                }
                if not self.bot.locals.get('admin_channel'):
                    config[server.name]['channels']['admin'] = server.locals.get('channels', {}).get('admin', -1)
                with open(config_file, mode='w', encoding='utf-8') as outfile:
                    yaml.dump(config, outfile)
                await server.reload()
                server.status = Status.SHUTDOWN
                await interaction.followup.send(f"Server {server.name} added to instance {instance.name}.")
            else:
                await interaction.followup.send(f"Instance {instance.name} created blank with no server assigned.")
            await interaction.followup.send(f"""
Instance {name} added to node {node.name}.
Please make sure you forward the following ports:
```
- DCS Port:    {instance.dcs_port}
- WebGUI Port: {instance.webgui_port}
```
            """)
        else:
            await interaction.response.send_message(f"Instance {name} could not be added to node {node.name}, see log.",
                                                    ephemeral=True)

    @node_group.command(description="Delete an instance")
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def delete_instance(self, interaction: discord.Interaction,
                              node: app_commands.Transform[Node, utils.NodeTransformer],
                              instance: app_commands.Transform[Instance, utils.InstanceTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        if instance.server:
            await interaction.response.send_message(f"The instance is in use by server \"{instance.server.name}\". "
                                                    f"Please migrate this server to another node first.",
                                                    ephemeral=ephemeral)
            return
        elif not await utils.yn_question(interaction, f"Do you really want to delete instance {instance.name}?",
                                         ephemeral=ephemeral):
            await interaction.followup.send('Aborted.', ephemeral=ephemeral)
            return
        remove_files = await utils.yn_question(interaction,
                                               f"Do you want to remove the directory {instance.home}?",
                                               ephemeral=ephemeral)
        await node.delete_instance(instance, remove_files)
        await interaction.followup.send(f"Instance {instance.name} removed from node {node.name}.", ephemeral=ephemeral)
        await self.bot.audit(f"removed instance {instance.name} from node {node.name}.", user=interaction.user)

    @node_group.command(description="Rename an instance\n")
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def rename_instance(self, interaction: discord.Interaction,
                              node: app_commands.Transform[Node, utils.NodeTransformer],
                              instance: app_commands.Transform[Instance, utils.InstanceTransformer], new_name: str):
        ephemeral = utils.get_ephemeral(interaction)
        if instance.server and instance.server.status != Status.SHUTDOWN:
            await interaction.response.send_message(f"Server {instance.server.name} has to be shut down before "
                                                    f"renaming the instance!", ephemeral=ephemeral)
            return
        if not await utils.yn_question(interaction, f"Do you really want to rename instance {instance.name}?",
                                       ephemeral=ephemeral):
            await interaction.followup.send('Aborted.', ephemeral=ephemeral)
            return
        old_name = instance.name
        await node.rename_instance(instance, new_name)
        await interaction.followup.send(f"Instance {old_name} renamed to {instance.name}.", ephemeral=ephemeral)
        await self.bot.audit(f"renamed instance {old_name} to {instance.name}.", user=interaction.user)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages or messages that do not contain yaml attachments
        if message.author.bot or not message.attachments or not message.attachments[0].filename.endswith('.yaml'):
            return
        # read the default config, if there is any
        config = self.get_config().get('uploads', {})
        # check, if upload is enabled
        if not config.get('enabled', True):
            return
        # check if the user has the correct role to upload, defaults to Admin
        if not utils.check_roles(config.get('discord', self.bot.roles['Admin']), message.author):
            return
        # check if the upload happens in the servers admin channel (if provided)
        server: Server = self.bot.get_server(message, admin_only=True)
        ctx = await self.bot.get_context(message)
        if not server:
            # check if there is a central admin channel configured
            if self.bot.locals.get('admin_channel', 0) == message.channel.id:
                try:
                    server = await utils.server_selection(
                        self.bus, ctx, title="To which server do you want to upload this configuration to?")
                    if not server:
                        await ctx.send('Aborted.')
                        return
                except Exception as ex:
                    self.log.exception(ex)
                    return
            else:
                return
        att = message.attachments[0]
        name = att.filename[:-5]
        if name in ['main', 'nodes', 'presets', 'servers']:
            target_path = self.node.config_dir
            plugin = False
        elif name in ['backup', 'bot']:
            target_path = os.path.join(self.node.config_dir, 'services')
            plugin = False
        elif name in self.node.plugins:
            target_path = os.path.join(self.node.config_dir, 'plugins')
            plugin = True
        else:
            return False
        target_file = os.path.join(target_path, att.filename)
        rc = await server.node.write_file(target_file, att.url, True)
        if rc != UploadStatus.OK:
            if rc == UploadStatus.WRITE_ERROR:
                await ctx.send(f'Error while uploading file to node {server.node.name}.')
                return
            elif rc == UploadStatus.READ_ERROR:
                await ctx.send('Error while reading file from discord.')
        if plugin:
            await self.bot.reload(name)
            await message.channel.send(f"Plugin {name.title()} re-loaded.")
        elif await utils.yn_question(ctx, 'Do you want to exit (restart) the bot?'):
            await message.channel.send('Bot restart initiated.')
            exit(-1)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self.bot.log.debug(f'Member {member.display_name} has joined guild {member.guild.name}')
        ucid = await self.bot.get_ucid_by_member(member)
        if ucid and self.bot.locals.get('autoban', False):
            await self.bus.unban(ucid)
        if self.bot.locals.get('greeting_dm'):
            channel = await member.create_dm()
            await channel.send(self.bot.locals['greeting_dm'].format(name=member.name, guild=member.guild.name))

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        self.bot.log.debug(f'Member {member.display_name} has left the discord')
        ucid = await self.bot.get_ucid_by_member(member)
        if ucid and self.bot.locals.get('autoban', False):
            self.bot.log.debug(f'- Banning them on our DCS servers due to AUTOBAN')
            await self.bus.ban(ucid, self.bot.member.display_name, 'Player left discord.')


async def setup(bot: DCSServerBot):
    await bot.add_cog(Admin(bot))
