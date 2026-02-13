import asyncio
import discord

from aiohttp import ClientError
from core import Channel, utils, Status, PluginError, Group, Node, DEFAULT_CHANNEL_PERMISSIONS, \
    SEND_ONLY_CHANNEL_PERMISSIONS, SEND_ONLY_WITH_EMBEDS_PERMISSIONS
from core.data.node import FatalException
from core.listener import EventListener
from core.services.registry import ServiceRegistry
from datetime import datetime, timezone
from discord import Thread, PrivilegedIntentsRequired
from discord.abc import PrivateChannel, GuildChannel
from discord.ext import commands
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from core import Server, NodeImpl

__all__ = ["DCSServerBot"]


class DCSServerBot(commands.Bot):

    def __init__(self, *args, **kwargs):
        from services.servicebus import ServiceBus

        super().__init__(*args, **kwargs)
        self.version: str = kwargs['version']
        self.sub_version: str = kwargs['sub_version']
        self.node: NodeImpl = kwargs['node']
        self.pool = self.node.pool
        self.apool = self.node.apool
        self.log = self.node.log
        self.locals = kwargs['locals']
        self.plugins = self.node.plugins
        self.bus = ServiceRegistry.get(ServiceBus)
        self.eventListeners: set[EventListener] = self.bus.eventListeners
        self.audit_channel = None
        self.member: discord.Member | None = None
        self.synced: bool = False
        self.tree.on_error = self.on_app_command_error
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._roles = None

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        self.synced: bool = False
        await super().start(token, reconnect=reconnect)

    async def connect(self, *, reconnect: bool = True) -> None:
        try:
            await super().connect(reconnect=reconnect)
        except PrivilegedIntentsRequired as ex:
            self.log.critical("You need to enable all priviledged intents in your Discord developer page!")
            exit(-2)

    async def close(self):
        try:
            await self.audit(message="Discord Bot stopped.")
        except Exception:
            pass
        self.log.info('- Unloading Plugins ...')
        await super().close()
        self.log.info("- Plugins unloaded.")

    @property
    def roles(self) -> dict[str, list[str | int]]:
        if not self._roles:
            self._roles = {
                "Admin": ["Admin"],
                "DCS Admin": ["DCS Admin"],
                "DCS": ["DCS"]
            } | self.locals.get('roles', {})
            if 'GameMaster' not in self._roles:
                self._roles['GameMaster'] = self._roles['DCS Admin']
            if 'Alert' not in self._roles:
                self._roles['Alert'] = self._roles['DCS Admin']
        return self._roles

    @property
    def filter(self) -> dict:
        return self.bus.filter

    @property
    def servers(self) -> dict[str, "Server"]:
        return self.bus.servers

    async def setup_hook(self) -> None:
        self.log.info('- Loading Plugins ...')
        # we need to keep the order for our default plugins...
        for plugin in self.plugins:
            await self.load_plugin(plugin.lower())
        # clean up remote servers (if any)
        for key in [key for key, value in self.bus.servers.items() if value.is_remote]:
            self.bus.servers.pop(key)

    async def load_plugin(self, plugin: str) -> bool:
        try:
            await self.load_extension(f'plugins.{plugin.lower()}.commands')
            return True
        except ModuleNotFoundError:
            self.log.error(f'  - Plugin "{plugin.title()}" not found!')
        except commands.ExtensionNotFound:
            self.log.error(f'  - No commands.py found for plugin "{plugin.title()}"!')
        except commands.ExtensionAlreadyLoaded:
            self.log.warning(f'  - Plugin "{plugin.title()} was already loaded"')
        except commands.ExtensionFailed as ex:
            if ex.original and isinstance(ex.original, PluginError):
                self.log.error(f'  - {ex.original}')
            else:
                exc = ex.original if ex.original else ex
                self.log.error(f'  - Plugin "{plugin.title()} not loaded! {ex.name}: {exc}', exc_info=exc)
        except Exception as ex:
            self.log.exception(ex)
        self.log.warning(f'  => {plugin.title()} NOT loaded.')
        return False

    async def unload_plugin(self, plugin: str) -> bool:
        try:
            await self.unload_extension(f'plugins.{plugin.lower()}.commands')
            return True
        except commands.ExtensionNotFound:
            self.log.debug(f'- No init.py found for plugin "{plugin}!"')
            pass
        except commands.ExtensionNotLoaded:
            pass
        return False

    async def reload_plugin(self, plugin: str) -> bool:
        if await self.unload_plugin(plugin):
            return await self.load_plugin(plugin)
        else:
            return False

    def check_roles(self, roles: Iterable[str | int]):
        for role in roles:
            if not self.get_role(role):
                self.log.error(f"  => Role {role} not found in your Discord!")

    @staticmethod
    def _channel_path(channel: discord.abc.GuildChannel) -> str:
        """
        Helper: return 'category/channel' if the channel has a category,
        otherwise just 'channel'.
        """
        # Use ASCII‑safe names in the log (just like the original code)
        channel_name = channel.name.encode("utf-8", "replace").decode()

        if channel.category:  # `channel.category` is a CategoryChannel or None
            cat_name = channel.category.name.encode("ascii", "replace").decode()
            return f"{cat_name}/{channel_name}"
        return channel_name

    def check_channel(
        self,
        channel_id: int,
        permissions: Iterable[str] | None = None,
    ) -> bool:
        """
        Verify that the bot has the *required* permissions on the given channel.

        Parameters
        ----------
        channel_id : int
            Discord channel ID. `-1` is treated as a “no‑check” marker.
        permissions : Iterable[str] | None
            Permission names to check for (e.g. ``'view_channel'``).
            If omitted, the default set in ``const.DEFAULT_CHANNEL_PERMISSIONS`` is used.

        Returns
        -------
        bool
            ``True`` if *all* requested permissions are present; otherwise ``False``.
        """
        if channel_id == -1:
            # A sentinel value – we purposely skip the check.
            return True

        channel = self.get_channel(channel_id)
        if not channel:
            self.log.error(f"No channel with ID {channel_id} found!")
            return False

        # Make a *copy* so that the caller can pass in a mutable list without
        # accidentally mutating the defaults.
        required_perms: set[str] = set(permissions or DEFAULT_CHANNEL_PERMISSIONS)

        channel_name = self._channel_path(channel)
        channel_perms = channel.permissions_for(self.member)

        # ------------------------------------------------------------------
        # Iterate over the permission names and flag missing ones.
        # ------------------------------------------------------------------
        has_all = True
        for perm_name in required_perms:
            # If the attribute does not exist on the Permission object we
            # raise a clear error – this is a programming mistake, not a
            # runtime Discord issue.
            if not hasattr(channel_perms, perm_name):
                raise AttributeError(
                    f"Permission object has no attribute '{perm_name}'. "
                    "Check the spelling against the discord.py docs."
                )

            if not getattr(channel_perms, perm_name):
                self.log.error(
                    f"  => Permission '{perm_name.replace('_', ' ').title()}' "
                    f"missing for channel '{channel_name}'"
                )
                has_all = False

        return has_all

    def get_channel(self, channel_id: int, /) ->  GuildChannel | Thread | PrivateChannel | None:
        if channel_id == -1:
            return None
        return super().get_channel(channel_id)

    def get_role(self, role: str | int) -> discord.Role | None:
        if isinstance(role, int):
            return discord.utils.get(self.guilds[0].roles, id=role)
        elif isinstance(role, str):
            if role.isnumeric():
                return self.get_role(int(role))
            else:
                return discord.utils.get(self.guilds[0].roles, name=role)
        else:
            return None

    def _check_server_channels(self, server: "Server"):
        channels = {
            'status': DEFAULT_CHANNEL_PERMISSIONS,
            'chat': SEND_ONLY_CHANNEL_PERMISSIONS
        }
        if not self.locals.get('channels', {}).get('admin'):
            channels['admin'] = DEFAULT_CHANNEL_PERMISSIONS
        if server.locals.get('coalitions'):
            channels |= {
                'red': SEND_ONLY_CHANNEL_PERMISSIONS,
                'blue': SEND_ONLY_CHANNEL_PERMISSIONS
            }
        for c, perms in channels.items():
            channel_id = int(server.channels[Channel(c)])
            if channel_id != -1:
                self.check_channel(channel_id, perms)

    async def on_ready(self):
        async def register_guild_name():
            async with self.node.cpool.connection() as conn:
                await conn.execute("UPDATE cluster SET guild_name = %s WHERE guild_id = %s",
                                   (self.guilds[0].name, self.guilds[0].id))

        try:
            await self.wait_until_ready()
            if not self.guilds:
                self.log.error("You need to invite your bot to a Discord server!")
                raise FatalException()
            asyncio.create_task(register_guild_name())
            if not self.synced:
                self.log.info(f'- Preparing Discord Bot "{self.user.name}" ...')
                if len(self.guilds) > 1:
                    self.log.warning('  => Your bot can only be installed in ONE Discord server!')
                    for guild in self.guilds:
                        self.log.warning(f'    - {guild.name}')
                    self.log.warning(f'  => Remove it from {len(self.guilds) - 1} Discord servers and restart the bot.')
                    raise FatalException()
                elif self.node.guild_id != self.guilds[0].id:
                    raise FatalException(f"Change your guild_id in main.yaml to {self.guilds[0].id}!")
                self.member = self.guilds[0].get_member(self.user.id)
                if not self.member:
                    raise FatalException("Can't access the bots user. Check your Discord server settings.")
                elif self.member.guild_permissions.administrator:
                    self.log.critical("DCSServerBot is running with administrative Discord-permissions! "
                                      "This is NOT recommended.")

                self.log.debug('  => Checking Roles & Channels ...')
                roles = set()
                for role in ['Admin', 'DCS Admin', 'Alert', 'DCS', 'GameMaster']:
                    roles |= set(self.roles[role])
                self.check_roles(roles)
                # check channels in bot.yaml
                channels = {
                    'audit': SEND_ONLY_WITH_EMBEDS_PERMISSIONS,
                    'admin': DEFAULT_CHANNEL_PERMISSIONS
                }
                for name, channel in self.locals.get('channels', {}).items():
                    self.check_channel(int(channel), channels.get(name, DEFAULT_CHANNEL_PERMISSIONS))
                # check channels in servers.yaml
                for server in self.servers.values():
                    if server.locals.get('coalitions'):
                        roles.clear()
                        roles.add(server.locals['coalitions']['blue_role'])
                        roles.add(server.locals['coalitions']['red_role'])
                        self.check_roles(roles)
                    try:
                        self._check_server_channels(server)
                    except KeyError:
                        self.log.error(f"  => Mandatory channel(s) missing for server {server.name} in servers.yaml!")

                self.log.info('  => Registering Discord Commands (this might take a bit) ...')
                self.tree.copy_global_to(guild=self.guilds[0])
                app_cmds = await self.tree.sync(guild=self.guilds[0])
                app_ids: dict[str, int] = {}
                for app_cmd in app_cmds:
                    app_ids[app_cmd.name] = app_cmd.id

                for cmd in self.tree.get_commands(guild=self.guilds[0]):
                    if isinstance(cmd, Group):
                        for inner in cmd.commands:
                            inner.mention = f"</{inner.qualified_name}:{app_ids[cmd.name]}>"
                    else:
                        cmd.mention = f"</{cmd.name}:{app_ids[cmd.name]}>"

                self.synced = True
                self.log.info('  => Discord Commands registered.')
                self.log.info('- Discord Bot started, accepting commands.')
                asyncio.create_task(self.audit(message="Discord Bot started."))
            else:
                self.log.warning('- Discord connection re-established.')
        except FatalException:
            raise
        except (discord.HTTPException, RuntimeError) as ex:
            self.log.warning(f"Discord connection error: {repr(ex)}")
            pass
        except Exception as ex:
            self.log.exception(ex)
            raise

    async def on_command_error(self, ctx: commands.Context, err: Exception):
        if isinstance(err, commands.CommandNotFound):
            pass
        elif isinstance(err, commands.NoPrivateMessage):
            await ctx.send(f"{ctx.command.name} can't be used in a DM.")
        elif isinstance(err, commands.MissingRequiredArgument):
            await ctx.send(f"Usage: {ctx.prefix}{ctx.command.name} {ctx.command.signature}")
        elif isinstance(err, commands.errors.CheckFailure):
            await ctx.send(f"You don't have the permission to use {ctx.command.name}!")
        elif isinstance(err, commands.DisabledCommand):
            pass
        elif isinstance(err, TimeoutError) or isinstance(err, asyncio.TimeoutError):
            await ctx.send('A timeout occurred. Is the DCS server running?')
        else:
            self.log.exception(err)
            await ctx.send("An unknown exception occurred.")

    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CommandNotFound):
            pass
        # noinspection PyUnresolvedReferences
        if interaction.response.is_done():
            send = interaction.followup.send
        else:
            # noinspection PyUnresolvedReferences
            send = interaction.response.send_message
        try:
            if isinstance(error, discord.app_commands.NoPrivateMessage):
                await send(f"{interaction.command.name} can't be used in a DM.")
            elif isinstance(error, discord.app_commands.CheckFailure):
                await send(f"You don't have the permission to use {interaction.command.name}!", ephemeral=True)
            elif isinstance(error, (TimeoutError, asyncio.TimeoutError)):
                await send('A timeout occurred. Is the DCS server running?', ephemeral=True)
            elif isinstance(error, discord.app_commands.TransformerError):
                await send(error, ephemeral=True)
            elif isinstance(error, discord.app_commands.CommandInvokeError):
                if error.original:
                    await send(repr(error.original), ephemeral=True)
                else:
                    await send(repr(error), ephemeral=True)
                self.log.exception(error)
            elif isinstance(error, discord.NotFound):
                await send("Command not found. Did you try it too early?", ephemeral=True)
            elif isinstance(error, discord.app_commands.AppCommandError):
                self.log.exception(error)
                await send(str(error))
            else:
                self.log.exception(error)
                await send("An unknown exception occurred.", ephemeral=True)
        except discord.NotFound:
            self.log.debug(f"Errormessage ignored, no interaction found: {error}")
            pass
        except Exception as ex:
            self.log.debug(f"Exception in on_app_command_error ignored: {ex}")
            pass

    async def reload(self, plugin: str | None = None) -> bool:
        if plugin:
            return await self.reload_plugin(plugin)
        else:
            rc = True
            for plugin in self.plugins:
                if not await self.reload_plugin(plugin):
                    rc = False
            return rc

    async def audit(self, message, *, user: discord.Member | str | None = None,
                    server: "Server | None" = None, node: Node | None = None,
                    mention: discord.Role | list[discord.Role] | None = None, **kwargs):
        # init node if not set
        if not node:
            node = self.node
        # init audit channel
        if not self.audit_channel:
            self.audit_channel = self.get_channel(self.locals.get('channels', {}).get('audit', -1))
        # if we have a server-specific audit channel, use this one
        if server and server.channels[Channel.AUDIT] != -1:
            audit_channel = self.get_channel(server.channels[Channel.AUDIT])
        else:
            audit_channel = self.audit_channel

        if audit_channel:
            if mention:
                if isinstance(mention, list):
                    content = ''.join([role.mention for role in mention])
                else:
                    content = mention.mention
            else:
                content = None
            if not user:
                member = self.member
            elif isinstance(user, str):
                member = self.get_member_by_ucid(user) if utils.is_ucid(user) else None
            else:
                member = user
            embed = discord.Embed(color=discord.Color.blue())
            if member:
                embed.set_author(name=member.display_name, icon_url=member.avatar)
                if 'error' in kwargs:
                    embed.set_thumbnail(url="https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/warning.png?raw=true")
                else:
                    embed.set_thumbnail(url=member.avatar)
                if member != self.member:
                    embed.description = f'<@{member.id}> ' + message
                else:
                    embed.description = message
            else:
                embed.description = message
            if isinstance(user, str):
                embed.add_field(name='UCID', value=user)
            if server:
                embed.add_field(name='Server', value=server.display_name)
            if kwargs:
                for name, value in kwargs.items():
                    embed.add_field(name=name.title(),
                                    value=value.mention if isinstance(value, discord.Member) else value,
                                    inline=False)
            embed.set_footer(text=datetime.now(timezone.utc).strftime("%y-%m-%d %H:%M:%S"))
            try:
                await audit_channel.send(content=content, embed=embed,
                                         allowed_mentions=discord.AllowedMentions(replied_user=False))
            except discord.errors.HTTPException as ex:
                # ignore rate limits
                if ex.code != 429:
                    raise
                self.log.warning("Audit message discarded due to Discord rate limits: " + message)
            except ClientError:
                self.log.warning("Audit message discarded due to connection issue: " + message)

        async with self.apool.connection() as conn:
            await conn.execute("""
                INSERT INTO audit (node, event, server_name, discord_id, ucid)
                VALUES (%s, %s, %s, %s, %s)
            """, (node.name, message, server.name if server else None,
                  user.id if isinstance(user, discord.Member) else None,
                  user if isinstance(user, str) else None))

    def get_admin_channel(self, server: "Server | None" = None) -> discord.TextChannel | None:
        admin_channel = self.locals.get('channels', {}).get('admin')
        if not admin_channel:
            if server:
                admin_channel = int(server.channels.get(Channel.ADMIN, -1))
            else:
                return None
        return self.get_channel(admin_channel)

    async def get_member_or_name_by_ucid(self, ucid: str, verified: bool = False) -> discord.Member | str | None:
        async with self.apool.connection() as conn:
            sql = 'SELECT discord_id, name FROM players WHERE ucid = %s'
            if verified:
                sql += ' AND discord_id <> -1 AND manual IS TRUE'
            cursor = await conn.execute(sql, (ucid, ))
            if cursor.rowcount == 1:
                row = await cursor.fetchone()
                return self.guilds[0].get_member(row[0]) or row[1]
            else:
                return None

    async def get_ucid_by_member(self, member: discord.Member, verified: bool | None = False) -> str | None:
        async with self.apool.connection() as conn:
            sql = 'SELECT ucid FROM players WHERE discord_id = %s AND LENGTH(ucid) = 32 '
            if verified:
                sql += 'AND manual IS TRUE '
            sql += 'ORDER BY last_seen DESC'
            cursor = await conn.execute(sql, (member.id, ))
            if cursor.rowcount >= 1:
                return (await cursor.fetchone())[0]
            else:
                return None

    # TODO: change to async (after change in DataClasses)
    def get_member_by_ucid(self, ucid: str, verified: bool | None = False) -> discord.Member | None:
        with self.pool.connection() as conn:
            sql = 'SELECT discord_id FROM players WHERE ucid = %s AND discord_id <> -1'
            if verified:
                sql += ' AND manual IS TRUE'
            cursor = conn.execute(sql, (ucid, ))
            if cursor.rowcount == 1:
                return self.guilds[0].get_member(cursor.fetchone()[0])
            else:
                return None

    def match_user(self, data: dict, rematch=False) -> discord.Member | None:
        if not rematch:
            member = self.get_member_by_ucid(data['ucid'])
            if member:
                return member
        return utils.match(data['name'], [x for x in self.get_all_members() if not x.bot])

    def get_servers(self, manager: discord.Member | None = None) -> dict[str, "Server"] | None:
        def check_server_roles(server: "Server") -> bool:
            if server.locals.get('managed_by') and not utils.check_roles(server.locals.get('managed_by'), manager):
                return False
            return True

        return {k: v for k,v in self.servers.items() if check_server_roles(v)}

    def get_server(self, ctx: commands.Context | discord.Interaction | discord.Message | str, *,
                   admin_only: bool | None = False) -> "Server | None":

        all_servers = self.get_servers(manager=ctx.user if isinstance(ctx, discord.Interaction) else ctx.author)
        if len(all_servers) == 1:
            server = next(iter(all_servers.values()))
            if admin_only:
                if ctx.channel.id in [
                    int(self.locals.get('channels', {}).get('admin', 0)),
                    int(server.locals.get('channels', {}).get('admin', 0))
                ]:
                    return server
                else:
                    return None
            else:
                return server
        for server_name, server in all_servers.items():
            if isinstance(ctx, commands.Context) or isinstance(ctx, discord.Interaction) \
                    or isinstance(ctx, discord.Message):
                if server.status == Status.UNREGISTERED:
                    continue
                for channel in [Channel.ADMIN, Channel.STATUS, Channel.EVENTS, Channel.CHAT,
                                Channel.COALITION_BLUE_EVENTS, Channel.COALITION_BLUE_CHAT,
                                Channel.COALITION_RED_EVENTS, Channel.COALITION_RED_CHAT]:
                    if int(server.locals.get('channels', {}).get(channel.value, -1)) != -1 and \
                            server.channels[channel] == ctx.channel.id:
                        return server
            else:
                if server_name == ctx:
                    return server
        return None

    async def fetch_embed(self, embed_name: str, channel: GuildChannel, server: "Server | None" = None):
        async with self.apool.connection() as conn:
            # check if we have a message persisted already
            cursor = await conn.execute("""
                SELECT embed, thread
                FROM message_persistence
                WHERE server_name IS NOT DISTINCT FROM %s 
                  AND embed_name = %s
            """, (server.name if server else None, embed_name))
            row = await cursor.fetchone()

        message = None
        if row:
            try:
                if channel.type == discord.ChannelType.forum:
                    thread = channel.get_thread(row[1])
                    if thread:
                        message = await thread.fetch_message(row[0])
                else:
                    message = await channel.fetch_message(row[0])
            except discord.errors.NotFound:
                pass
            except discord.errors.DiscordException as ex:
                self.log.warning(f"Error during update of embed {embed_name}: " + str(ex))
                raise
            except Exception as ex:
                self.log.exception(ex)
                raise
        return message

    async def setEmbed(self, *, embed_name: str, embed: discord.Embed, channel_id: Channel | int = Channel.STATUS,
                       file: discord.File | None = None, server: "Server | None" = None) -> discord.Message | None:
        lock = self._locks.setdefault((server.name if server else 'MASTER', embed_name), asyncio.Lock())
        async with lock:
            # do not update any embed if the session is closed already
            if self.is_closed():
                return None
            if server and isinstance(channel_id, Channel):
                channel_id = int(server.channels.get(channel_id, -1))
                # we should not write to this channel
                if channel_id == -1:
                    return None
            else:
                channel_id = int(channel_id)

            # find the channel
            channel = self.get_channel(channel_id)
            if not channel:
                try:
                    channel = await self.fetch_channel(channel_id)
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    self.log.error("No permission to fetch channels!")
                except Exception as ex:
                    self.log.exception(ex)
            if not channel:
                self.log.error(f"Channel {channel_id} not found, can't add or change an embed in there!")
                return None

            # try to read an already existing message
            try:
                message = await self.fetch_embed(embed_name, channel, server)
            except Exception:
                self.log.debug(f"Can't update embed {embed_name}, skipping.")
                return None

            if message:
                try:
                    if not file:
                        await message.edit(embed=embed, attachments=[])
                    else:
                        await message.edit(embed=embed, attachments=[file])
                    return message
                except Exception:
                    self.log.debug(f"Can't update embed {embed_name}, skipping.")
                    return None
            else:
                if channel.type == discord.ChannelType.forum:
                    for thread in channel.threads:
                        if thread.name.startswith(server.name):
                            message = await thread.send(embed=embed, file=file)
                            break
                    else:
                        thread = await channel.create_thread(name=server.name, auto_archive_duration=10080,
                                                             embed=embed, file=file)
                        message = thread.message
                        thread = thread.thread
                else:
                    message = await channel.send(embed=embed, file=file)
                    thread = None
                async with self.apool.connection() as conn:
                    await conn.execute("""
                        INSERT INTO message_persistence (server_name, embed_name, embed, thread) 
                        VALUES (%s, %s, %s, %s) 
                        ON CONFLICT ON CONSTRAINT uq_message_persistence_norm 
                        DO UPDATE SET embed=excluded.embed, thread=excluded.thread
                    """, (server.name if server else None, embed_name, message.id,
                          thread.id if thread else None))
                return message
