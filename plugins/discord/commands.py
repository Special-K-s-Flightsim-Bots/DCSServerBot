import discord

from core import Plugin, command, utils, get_translation
from datetime import timedelta
from discord import app_commands
from discord.ext import commands
from services.bot import DCSServerBot
from services.cron.actions import purge_channel

_ = get_translation(__name__.split('.')[1])


class Discord(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.reaction_message_id = None

    async def on_ready(self):
        config = self.get_config()
        for role_id in config.get('roles', {}).keys():
            role = self.bot.get_role(role_id)
            if not role:
                self.log.warning(f"{self.__class__.__name__}: Role {role_id} not found.")
        if 'ping_everyone' in config:
            if config['ping_everyone'].get('kick', False) and not self.bot.member.guild_permissions.kick_members:
                self.log.warning(f"{self.__class__.__name__}: Bot is missing permission to kick members.")
            if config['ping_everyone'].get('timeout', False) and not self.bot.member.guild_permissions.moderate_members:
                self.log.warning(f"{self.__class__.__name__}: Bot is missing permission to timeout members.")
        if 'reaction' in config:
            channel = self.bot.get_channel(config['reaction']['channel'])
            message = await self.bot.fetch_embed('reaction', channel)
            if not message:
                guild = self.bot.guilds[0]
                embed = discord.Embed(color=discord.Color.blue())
                embed.title = config['reaction'].get('title', 'Welcome to {guild}').format(
                    guild=utils.escape_string(guild.name))
                embed.set_thumbnail(url=self.bot.guilds[0].icon.url)
                embed.description = config['reaction'].get('message', 'Please react to give yourself a role!') + '\n'
                if config['reaction'].get('bot_trap', False):
                    embed.description += "🤖 | Bot Trap, DO NOT PRESS!\n"
                for emoji, desc in config['reaction']['roles'].items():
                    embed.description += f"{emoji} | {desc['message']}\n"
                message = await self.bot.setEmbed(embed_name='reaction', embed=embed, channel_id=channel.id)
                if config['reaction'].get('bot_trap', False):
                    await message.add_reaction('🤖')
                for emoji in config['reaction']['roles'].keys():
                    await message.add_reaction(emoji)
            # set the id to handle reactions
            self.reaction_message_id = message.id

    @command(name='clear', description=_('Clear Discord messages'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.describe(older_than=_('Delete messages older than x days (0 = all)'))
    @app_commands.describe(ignore=_('Messages from this member will be ignored'))
    async def clear(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
                    older_than: int | None = None, ignore: discord.Member | None = None,
                    after_id: str | None = None, before_id: str | None = None):
        if not channel:
            channel = interaction.channel
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True, ephemeral=utils.get_ephemeral(interaction))
        msg = await interaction.followup.send(_("Deleting messages ..."))
        await purge_channel(node=self.node, channel=channel.id, older_than=older_than,
                            ignore=ignore.id if ignore else None, after_id=int(after_id) if after_id else None,
                            before_id=int(before_id) if before_id else None)
        await msg.edit(content=_("All messages deleted."))

    @command(name='addrole', description=_('Adds a role to a member'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def addrole(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        ephemeral = utils.get_ephemeral(interaction)
        if role in member.roles:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Member {member} already has role {role}.").format(
                    member=member.display_name, role=role.name), ephemeral=True)
            return
        try:
            await member.add_roles(role)
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Role {role} added to {member}.").format(role=role.name, member=member.display_name),
                ephemeral=ephemeral)
        except discord.Forbidden:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("You don't have permission to add role {role} to {member}.").format(
                    role=role.name, member=member.display_name), ephemeral=True)

    @command(name='delrole', description=_('Removes a role from a member'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def delrole(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        ephemeral = utils.get_ephemeral(interaction)
        if role not in member.roles:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Member {member} doesn't have role {role}.").format(
                    member=member.display_name, role=role.name), ephemeral=True)
            return
        try:
            await member.remove_roles(role)
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Role {role} removed from {member}.").format(role=role.name, member=member.display_name),
                ephemeral=ephemeral)
        except discord.Forbidden:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("You don't have permission to remove role {role} from {member}.").format(
                    role=role.name, member=member.display_name), ephemeral=True)

    async def _send_message(self, member: discord.Member, config: dict):
        message = config['message']
        if 'mention' in config:
            mention_role = self.bot.get_role(config['mention'])
            if not mention_role:
                self.log.warning(
                    f"{self.__class__.__name__}: Mention role {config['mention']} not found.")
            else:
                message = mention_role.mention + ' ' + message
        channel_id = config.get('channel', -1)
        channel = self.bot.get_channel(channel_id) if channel_id != -1 else member
        await channel.send(message.format(name=member.display_name, mention=member.mention))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        config = self.get_config()
        if 'on_join' in config:
            await self._send_message(member, config['on_join'])

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        config = self.get_config()
        for role_id in config.get('roles', {}).keys():
            role = self.bot.get_role(role_id)
            if role and role in member.roles:
                config_role = config['roles'][role.id]
                if 'on_leave' in config_role:
                    await self._send_message(member, config_role['on_leave'])

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        before_roles = set(x.id for x in before.roles)
        after_roles = set(x.id for x in after.roles)

        config = self.get_config()
        for role_id in config.get('roles', {}).keys():
            role = self.bot.get_role(role_id)
            if not role:
                continue
            # check removed roles
            if role.id in (before_roles - after_roles):
                config_role = config['roles'][role.id]
                if 'on_remove' in config_role:
                    await self._send_message(after, config_role['on_remove'])
            # check added roles
            if role.id in (after_roles - before_roles):
                config_role = config['roles'][role.id]
                if 'on_add' in config_role:
                    await self._send_message(after, config_role['on_add'])

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # ignore ourselves adding the initial reactions
        if payload.message_id != self.reaction_message_id or payload.member.id == self.bot.user.id:
            return
        if payload.emoji.name == '🤖':
            self.log.warning(f"Member {payload.member.display_name} fell into the bot trap!")
            if payload.member.id != self.bot.owner_id:
                await self.bot.audit(f"Kicked for falling into the bot trap.", member=payload.member)
                await payload.member.kick(reason="Bot user")
            else:
                self.log.warning("You tried to kick yourself! Aborted.")
            return
        else:
            config = self.get_config()['reaction'].get('roles', {}).get(payload.emoji.name)
            if config:
                role = self.bot.get_role(config.get('role'))
                if role:
                    await payload.member.add_roles(role)
                    self.log.info(f"Added role {role.name} to {payload.member.display_name}")
                else:
                    self.log.warning(f"Role {config['role']} not found for emoji {payload.emoji.name}")
            else:
                message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
                await message.remove_reaction(payload.emoji, payload.member)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        # ignore reactions on other messages
        if payload.message_id != self.reaction_message_id:
            return

        config = self.get_config()['reaction'].get('roles', {}).get(payload.emoji.name)
        role = self.bot.get_role(config.get('role'))
        if role:
            member = self.bot.guilds[0].get_member(int(payload.user_id))
            await member.remove_roles(role)
            self.log.info(f"Removed role {role.name} from {member.display_name}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore my own messages
        if message.author.id == self.bot.user.id:
            return
        config = self.get_config().get('ping_everyone')
        if not config:
            return
        # only report members that do not have the permission to ping @everyone or @here
        if ('@everyone' in message.content or '@here' in message.content) and not message.mention_everyone:
            timeout = config.get('timeout', 60)
            if timeout:
                try:
                    await message.author.timeout(timedelta(minutes=1), reason="Mentioning everyone")
                except discord.Forbidden:
                    self.log.warning('DCSServerBot is missing permission "Time out members"!')
            elif config.get('kick', False):
                try:
                    await message.author.kick(reason="Mentioning everyone")
                except discord.Forbidden:
                    self.log.warning('DCSServerBot is missing permission "Kick, Approve and Reject Members"!')
            await message.delete()
            if config.get('report', True):
                mention = []
                for role_id in self.bot.roles['Admin']:
                    mention.append(self.bot.get_role(role_id))
                await self.bot.audit(f"tried to mention everyone or here: ```{message.content}```",
                                     user=message.author, mention=mention)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Discord(bot))
