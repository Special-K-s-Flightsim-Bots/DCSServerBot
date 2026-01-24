import discord
from discord.ext import commands

from core import Plugin, command, utils, get_translation
from discord import app_commands
from services.bot import DCSServerBot
from services.cron.actions import purge_channel

_ = get_translation(__name__.split('.')[1])


class Discord(Plugin):

    async def on_ready(self):
        config = self.get_config()
        for role_id in config.get('roles', {}).keys():
            role = self.bot.get_role(role_id)
            if not role:
                self.log.warning(f"{self.__class__.__name__}: Role {role_id} not found.")

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


async def setup(bot: DCSServerBot):
    await bot.add_cog(Discord(bot))
