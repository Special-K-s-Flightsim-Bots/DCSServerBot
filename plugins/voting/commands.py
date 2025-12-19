import discord

from core import Plugin, PluginInstallationError, Group, utils, get_translation, Server, Status
from discord import app_commands, SelectOption
from plugins.creditsystem.commands import CreditSystem
from services.bot import DCSServerBot
from typing import Type, Literal, cast

from .base import VotableItem
from .listener import VotingListener, VotingHandler

_ = get_translation(__name__.split('.')[1])


class Voting(Plugin[VotingListener]):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[VotingListener] = None):
        super().__init__(bot, eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.yaml file found!", plugin=self.plugin_name)

    # New command group "/vote"
    vote = Group(name="vote", description=_("Commands to manage votes"))

    @vote.command(name='list', description=_('Lists the current votes'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction):
        servers = []
        votes = []
        voters = []
        for server_name, handler in self.eventlistener._all_votes.items():
            servers.append(server_name)
            votes.append(repr(handler.item))
            voters.append(str(len(handler.votes)))
        if len(servers):
            embed = discord.Embed(color=discord.Color.blue())
            embed.add_field(name=_("Server"), value='\n'.join(servers))
            embed.add_field(name=_("Running Vote"), value='\n'.join(votes))
            embed.add_field(name=_("Voters"), value='\n'.join(voters))
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("No running votes found."), ephemeral=True)

    @vote.command(description=_('Create a vote'))
    @app_commands.guild_only()
    @app_commands.rename(_server='server')
    @utils.app_has_role('DCS')
    async def create(self, interaction: discord.Interaction,
                     _server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                     what: Literal['Restart', 'Mission Change', 'Weather Change']):
        config = self.get_config(_server)
        # Users with either the "creator" role or "DCS Admin" can use this command
        roles = set(config.get('creator', []) + self.bot.roles['DCS Admin'])
        if not utils.check_roles(roles, interaction.user):
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("You are not authorized to create a vote."))
            return
        is_admin = utils.check_roles(self.bot.roles['DCS Admin'], interaction.user)

        points = config.get('credits')
        credits = campaign_id = ucid = None
        if not is_admin and points:
            ucid = await self.bot.get_ucid_by_member(interaction.user)
            if not ucid:
                _mission = self.bot.cogs['Mission']
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_("Use {} to link your account.").format(
                    (await utils.get_command(self.bot, name=_mission.linkme.name)).mention
                ), ephemeral=True)
                return
            _creditssystem = cast(CreditSystem, self.bot.cogs['CreditSystem'])
            data = await _creditssystem.get_credits(ucid)
            campaign_id, campaign_name = utils.get_running_campaign(self.node, _server)
            credits = next((x['credits'] for x in data if x['id'] == campaign_id), 0)
            if credits < points:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(
                    _("You don't have enough credits to create a vote!"), ephemeral=True)
                return

        if self.eventlistener._all_votes.get(_server.name):
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('There is already a voting running on this server.'),
                                                    ephemeral=True)
            return

        if what == 'Mission Change' and config['options'].get('mission') is not None:
            message = _("Vote for a mission change on server {}").format(_server.name)
            element = 'mission'
        elif what == 'Restart' and config['options'].get('restart') is not None:
            message = _("Vote for a restart of server {}").format(_server.name)
            element = 'restart'
        elif what == 'Weather Change' and config['options'].get('preset') is not None:
            message = _("Vote for a weather change on server {}").format(_server.name)
            element = 'preset'
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Unknown vote type or vote type not configured."), ephemeral=True)
            return

        if not is_admin and points:
            message += "\n_" + _("This vote will cost you {} credits.").format(points) + "_"

        if not await utils.yn_question(interaction, question=_("Do you want to create a vote?"), message=message):
            await interaction.followup.send(_('Aborted.'))
            return

        class_name = f"plugins.voting.options.{element}.{element.title()}"
        item: VotableItem = utils.str_to_class(class_name)(
            _server, config['options'].get(element)
        )
        choices = await item.get_choices()
        if len(choices) > 2:
            rc = await utils.selection(interaction,
                                       title=_("Active players can vote for any of these options.\n"
                                               "Make your vote now!"),
                                       options=[
                                           SelectOption(label=x, value=str(idx + 2))
                                           for idx, x in enumerate(choices[1:])
                                           if idx < 25
                                       ])
            if rc is None:
                await interaction.followup.send(_("Aborted."))
                return
            vote = int(rc)
        else:
            vote = 2

        if not item.can_vote():
            await interaction.followup.send(_('This option is not available at the moment.'), ephemeral=True)
            return

        if _server.is_populated():
            handler = VotingHandler(listener=self.eventlistener, item=item, server=_server, config=config)
            self.eventlistener._all_votes[_server.name] = handler
            handler.votes[vote] = 1

            await interaction.followup.send(_('{} created. It is open for {}').format(
                repr(item), utils.format_time(config.get('time', 300))))
            await self.bot.audit(f"created a vote for {what}",
                                 user=interaction.user, server=_server)
        else:
            await item.execute(choices[vote-1])
            await interaction.followup.send(_('Your wish was executed immediately as the server was not populated.\n'))
            await self.bot.audit(f"created a vote for {what} which was executed already",
                                 user=interaction.user, server=_server)

        if not is_admin and points:
            async with self.apool.connection() as conn:
                await conn.execute("""
                    UPDATE credits SET points = %s WHERE campaign_id = %s AND player_ucid = %s
                """, (credits - points, campaign_id, ucid))
                await conn.execute("""
                    INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (campaign_id, 'vote', ucid, credits, credits - points, _("Paid for a vote")))

    @vote.command(description=_('Cancel a vote'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def cancel(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]):
        handler = self.eventlistener._all_votes.get(server.name)
        if not handler:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('There is no voting running on this server.'), ephemeral=True)
            return
        handler.cancel()
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Voting cancelled.'), ephemeral=utils.get_ephemeral(interaction))


async def setup(bot: DCSServerBot):
    await bot.add_cog(Voting(bot, VotingListener))
