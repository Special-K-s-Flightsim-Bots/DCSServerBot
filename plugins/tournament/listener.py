import asyncio
import discord

from core import EventListener, event, Server, utils, get_translation, Coalition, DataObjectFactory
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from trueskill import Rating
from typing import TYPE_CHECKING, Optional

from .utils import calculate_point_multipliers
from ..competitive.commands import Competitive
from ..creditsystem.squadron import Squadron

if TYPE_CHECKING:
    from .commands import Tournament

_ = get_translation(__name__.split('.')[1])


class TournamentEventListener(EventListener["Tournament"]):

    def __init__(self, plugin: "Tournament"):
        super().__init__(plugin)
        self.tournaments: dict[str, dict] = {}
        self.ratings: dict[int, Rating] = {}
        self.squadron_credits: dict[int, int] = {}

    async def audit(self, server: Server, message: str):
        config = self.get_config(server)
        channel_id = config.get('channels', {}).get('admin')
        if channel_id:
            channel = self.bot.get_channel(channel_id)
        else:
            channel = self.bot.get_admin_channel(server)
        await channel.send(message)

    async def announce(self, server: Server, message: str):
        await self.inform_streamer(server, message)
        config = self.get_config(server)
        channel = self.bot.get_channel(config.get('channels', {}).get('info', -1))
        if channel:
            await channel.send(message)

    async def inform_squadrons(self, server, *, message: str):
        match_id = await self.get_active_match(server)
        match = await self.plugin.get_match(match_id)
        for side in ['blue', 'red']:
            squadron = utils.get_squadron(self.node, squadron_id=match[f'squadron_{side}'])
            channel = await self.plugin.get_squadron_channel(match_id, side)
            await channel.send(self.bot.get_role(squadron['role']).mention + " " + message)

    async def inform_streamer(self, server: Server, message: str):
        config = self.get_config(server)
        channel = self.bot.get_channel(config.get('channels', {}).get('streamer', -1))
        if channel:
            await channel.send(message)

    async def get_active_tournament(self, server: Server) -> Optional[int]:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT t.tournament_id FROM tm_tournaments t
                JOIN campaigns c ON t.campaign = c.name
                JOIN campaigns_servers cs ON cs.campaign_id = c.id AND cs.server_name = %s
                WHERE c.start <= NOW() AT TIME ZONE 'UTC'
                AND COALESCE(c.stop, NOW() AT TIME ZONE 'UTC') >= NOW() AT TIME ZONE 'UTC'
            """, (server.name,))
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_active_match(self, server: Server) -> Optional[int]:
        tournament = self.tournaments.get(server.name)
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT match_id FROM tm_matches
                WHERE tournament_id = %s
                AND round_number > 0
                AND winner_squadron_id IS NULL
            """, (tournament['tournament_id'], ))
            row = await cursor.fetchone()
            return row[0] if row else None

    async def cleanup(self, server: Server):
        await server.shutdown()
        self.plugin.reset_serversettings(server)

    async def processEvent(self, name: str, server: Server, data: dict) -> None:
        try:
            if name == 'registerDCSServer' or server.name in self.tournaments:
                await super().processEvent(name, server, data)
        except Exception as ex:
            self.log.exception(ex)

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        tournament_id = await self.get_active_tournament(server)
        if tournament_id:
            self.tournaments[server.name] = await self.plugin.get_tournament(tournament_id)
            match_id = await self.get_active_match(server)
            match = await self.plugin.get_match(match_id)
            # store ratings before match for accelerator
            self.ratings[match['squadron_blue']] = await Competitive.trueskill_squadron(
                self.node, match['squadron_blue'])
            self.squadron_credits[match['squadron_blue']] = (
                await self.plugin.get_squadron(match_id, match['squadron_blue'])
            ).points
            self.ratings[match['squadron_red']] = await Competitive.trueskill_squadron(
                self.node, match['squadron_red'])
            self.squadron_credits[match['squadron_red']] = (
                await self.plugin.get_squadron(match_id, match['squadron_red'])
            ).points
        else:
            self.tournaments.pop(server.name, None)

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'S_EVENT_BIRTH':
            tournament = self.tournaments[server.name]
            initiator = data['initiator']
            player = server.get_player(name=initiator['name'])

            # ignore multicrew members
            if player.sub_slot != 0:
                return

            # check if we have the necessary number of players
            num_planes = len([x for x in server.get_active_players() if x.sub_slot == 0])
            if num_planes == tournament['num_players'] * 2:
                asyncio.create_task(server.current_mission.unpause())
                asyncio.create_task(self.announce(server,
                                                  _("All sides have occupied their units. The match is now on!")))
                messages = [_("The server is now unpaused!\n")]
                config = self.get_config(server, plugin_name='competitive')
                delayed_start = config.get('delayed_start', 0)
                if delayed_start:
                    messages.append(
                        _("You have {} to arm up your planes and get ready before the match starts.").format(
                            utils.format_time(delayed_start)))
                win_on = config.get('win_on', 'survival')
                if win_on == 'survival':
                    messages.append(_("The first party to lose all their units will be defeated, "
                                      "making the surviving party the winner of the match."))
                elif win_on in ['landing', 'rtb']:
                    messages.append(_("To win the match, a party must eliminate all enemy aircraft AND safely land "
                                      "at least one of their own planes."))
                else:
                    asyncio.create_task(self.audit(server, f"Win-method {win_on} is not supported yet!"))
                asyncio.create_task(server.sendPopupMessage(Coalition.ALL, '\n'.join(messages)))
            elif num_planes < tournament['num_players'] * 2:
                player = server.get_player(name=initiator['name'])
                asyncio.create_task(player.sendPopupMessage(
                    _("The server will be unpaused, if all players have chosen their slots!")))
            else:
                player = server.get_player(name=initiator['name'])
                await server.kick(player, "All seats are taken, you are not allowed to join anymore!")

    @event(name="onMatchFinished")
    async def onMatchFinished(self, server: Server, data: dict) -> None:
        winner = data['winner'].lower()
        match_id = await self.get_active_match(server)
        # do we have a winner?
        if winner in ['blue', 'red']:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    cursor = await conn.execute(f"""
                        UPDATE tm_matches 
                        SET squadron_{winner}_rounds_won = squadron_{winner}_rounds_won + 1
                        WHERE match_id = %s
                        RETURNING squadron_{winner}, round_number
                    """, (match_id,))
                    squadron_id, round_number = await cursor.fetchone()
            squadron = utils.get_squadron(self.node, squadron_id=squadron_id)
            message = _("Squadron {name} won round {round}!").format(name=squadron['name'], round=round_number)

            # calculate balance
            if self.get_config(server).get('balance_multiplier', False):
                match = await self.plugin.get_match(match_id)
                winner_id = squadron['id']
                loser_id = match['squadron_{}'.format('blue' if winner == 'red' else 'red')]
                winner_coalition = Coalition.RED if winner == 'red' else Coalition.BLUE
                loser_coalition = Coalition.RED if winner == 'blue' else Coalition.BLUE
                winner_squadron = await self.plugin.get_squadron(match_id, winner_id)
                loser_squadron = await self.plugin.get_squadron(match_id, loser_id)
                winner_points = winner_squadron.points - self.squadron_credits[winner_id]
                loser_points = loser_squadron.points - self.squadron_credits[loser_id]
                self.log.debug(f"Winning squadron {winner_squadron.name} gained {winner_points} points during the round.")
                self.log.debug(f"Defeated squadron {loser_squadron.name} gained {loser_points} points during the round.")
                killer_multiplier, loser_multiplier = calculate_point_multipliers(self.ratings[winner_id], self.ratings[loser_id])
                self.log.debug(f"Mulipliers are as follows: Killer = {killer_multiplier} / Victim = {loser_multiplier}")
                winner_squadron.points -= winner_points # remove the points first
                winner_squadron.points += winner_points * killer_multiplier # add the points with the correct multiplier
                self.log.debug(f"Winner got {winner_points * killer_multiplier} points instead of {winner_points} points.")
                await server.sendPopupMessage(
                    winner_coalition,f"Squadron {winner_squadron.name}, you earned "
                                     f"{winner_points * killer_multiplier} points!")
                loser_squadron.points -= loser_points # remove the points first
                loser_squadron.points += loser_points * loser_multiplier # add the points with the correct multiplier
                self.log.debug(f"Loser got {loser_points * loser_multiplier} points instead of {loser_points} points.")
                await server.sendPopupMessage(
                    loser_coalition,f"Squadron {loser_squadron.name}, you earned "
                                     f"{loser_points * loser_multiplier} points!")
        else:
            match = await self.plugin.get_match(match_id)
            message = _("Round {} was a draw!").format(match['round_number'])

        # inform players and people
        asyncio.create_task(server.sendPopupMessage(Coalition.ALL, message, 60))
        asyncio.create_task(self.inform_squadrons(server, message=message))
        asyncio.create_task(self.announce(server, message))

        # pause the server
        asyncio.create_task(server.current_mission.pause())

        # check if the match is finished
        winner_id = None
        tournament = self.tournaments.get(server.name)
        config = self.get_config(server)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute("SELECT * FROM tm_matches WHERE match_id = %s", (match_id,))
                    row = await cursor.fetchone()
                    # Calculate required wins for victory (best of N)
                    required_wins = (tournament['rounds'] // 2) + 1
                    total_rounds_played = row['round_number']

                    # Check if either side has reached the required wins
                    if row['squadron_blue_rounds_won'] >= required_wins:
                        winner_id = row['squadron_blue']
                    elif row['squadron_red_rounds_won'] >= required_wins:
                        winner_id = row['squadron_red']
                    # If we've played all rounds or more and still no winner
                    elif total_rounds_played >= tournament['rounds']:
                        if config.get('sudden_death', False):
                            # If all rounds were draws, play one additional decisive round
                            if row['squadron_blue_rounds_won'] == 0 and row['squadron_red_rounds_won'] == 0:
                                message = _("All rounds were draws! Playing one decisive round!")
                                asyncio.create_task(self.inform_squadrons(server, message=message))
                                asyncio.create_task(server.sendPopupMessage(Coalition.ALL, message))
                            # Otherwise determine winner by who has more rounds won
                            elif row['squadron_blue_rounds_won'] > row['squadron_red_rounds_won']:
                                winner_id = row['squadron_blue']
                            elif row['squadron_red_rounds_won'] > row['squadron_blue_rounds_won']:
                                winner_id = row['squadron_red']
                            # If equal wins but not all draws, play one decisive round
                            else:
                                message = _("Match is tied! Playing one decisive round!")
                                asyncio.create_task(self.inform_squadrons(server, message=message))
                                asyncio.create_task(server.sendPopupMessage(Coalition.ALL, message))
                        else:
                            message = _("We have no winner yet, continuing the match with another round!")
                            asyncio.create_task(self.inform_squadrons(server, message=message))
                            asyncio.create_task(server.sendPopupMessage(Coalition.ALL, message))

                    if not winner_id:
                        asyncio.create_task(self.next_round(server, match_id))
                        return

                    # update the database
                    await cursor.execute("UPDATE tm_matches SET winner_squadron_id = %s WHERE match_id = %s",
                                         (winner_id, match_id))

            # inform people
            squadron = utils.get_squadron(self.node, squadron_id=winner_id)
            message = _("Squadron {squadron} is the winner of the match!").format(squadron=squadron['name'])
            asyncio.create_task(self.announce(server, message=message))
            message += _("\nServer will be shut down in 60 seonds ...")
            asyncio.create_task(server.sendPopupMessage(Coalition.ALL, message, 60))
            await asyncio.sleep(60)
            asyncio.create_task(self.cleanup(server))
            asyncio.create_task(self.audit(
                server,f"Match {match_id} is now finished. Squadron {squadron['name']} won the match.\n"
                       f"Closing the squadron channels now."))
            asyncio.create_task(self.plugin.close_channel(match_id))

    async def wait_until_choices_finished(self, server: Server):
        config = self.get_config(server)
        match_id = await self.get_active_match(server)
        time_to_choose = config.get('time_to_choose', 600)
        time = 0
        finished: dict[str, bool] = {
            "red": False,
            "blue": False
        }
        while time < time_to_choose and (not finished["red"] or not finished["blue"]):
            async with self.apool.connection() as conn:
                for side in ['blue', 'red']:
                    async with conn.transaction():
                        cursor = await conn.execute(f"""
                            SELECT choices_{side}_ack FROM tm_matches WHERE match_id = %s
                        """, (match_id,))
                        row = await cursor.fetchone()
                        if not row:
                            raise ValueError("Match aborted!")
                        finished[side] = row[0]
            if time == int(time_to_choose / 2):
                await self.inform_squadrons(
                    server, message="The next round will start in {}!".format(utils.format_time(time_to_choose - time)))
            await asyncio.sleep(1)
            time += 1

    async def next_round(self, server: Server, match_id: int):
        await asyncio.create_task(server.sendPopupMessage(
            Coalition.ALL, _("You will be moved back to spectators in 60 seconds ...")))
        await asyncio.sleep(60)
        # move all players back to spectators
        tasks = []
        for player in server.get_active_players():
            tasks.append(server.move_to_spectators(
                player, reason=_("The round is over, please wait for the next one!")))
        await asyncio.gather(*tasks)
        await asyncio.sleep(1)
        await asyncio.create_task(server.sendPopupMessage(
            Coalition.ALL, _("Squadron admins, you can now choose your weapons for the next round!")))
        await self.inform_squadrons(server, message=_("You can now use {} to chose your customizations!").format(
                (await utils.get_command(self.bot, group=self.plugin.match.name,
                                         name=self.plugin.customize.name)).mention))
        try:
            await self.wait_until_choices_finished(server)
        except ValueError:
            await self.audit(server, f"Match {match_id} aborted!")
            return

        # Start the next round
        async with self.apool.connection() as conn:
            async with conn.transaction():
                cursor = await conn.execute("""
                    UPDATE tm_matches SET round_number = round_number + 1
                    WHERE match_id = %s
                    RETURNING round_number
                """, (match_id, ))
                row = await cursor.fetchone()
                round_number = row[0]
        new_mission = await self.plugin.prepare_mission(server, match_id, round_number=round_number)
        asyncio.create_task(server.sendPopupMessage(Coalition.ALL, _("The next round will start in 10s!")))
        await asyncio.sleep(10)
        await server.loadMission(new_mission, modify_mission=False, use_orig=False)
        await self.inform_squadrons(
            server, message=f"Round {round_number} is starting now! Please jump back into the server!")

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        if data.get('side', -1) not in [1, 2]:
            return
        player = server.get_player(ucid=data['ucid'])
        if player.squadron:
            return

        side = 'red' if data['side'] == 1 else 'blue'
        match_id = await self.get_active_match(server)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                config = self.get_config(server)
                if config.get('auto_join', False):
                    try:
                        async with conn.transaction():
                            cursor = await conn.execute(f"""
                                INSERT INTO squadron_members (squadron_id, player_ucid)
                                SELECT squadron_{side} AS squadron_id, '{player.ucid}'::TEXT 
                                FROM tm_matches WHERE match_id = %s
                                RETURNING squadron_id
                            """, (match_id, ))
                            squadron_id = (await cursor.fetchone())[0]
                            squadron = utils.get_squadron(self.node, squadron_id=squadron_id)
                            campaign_id, _ = utils.get_running_campaign(self.node, server)
                            # assign the squadron to the player
                            player.squadron = DataObjectFactory().new(Squadron, node=self.node, name=squadron['name'],
                                                                      campaign_id=campaign_id)
                            # we need to give the member the role
                            if player.member and 'role' in squadron:
                                try:
                                    await player.member.add_roles(self.bot.get_role(squadron['role']))
                                except discord.Forbidden:
                                    await self.bot.audit('permission "Manage Roles" missing.',
                                                         user=self.bot.member)
                    except UniqueViolation:
                        await server.kick(player, "You can only be in one squadron at a time!")
                else:
                    asyncio.create_task(server.kick(player, _("You are not a squadron member.\n"
                                                              "Please ask your squadron leader to add you.")))
                    asyncio.create_task(self.audit(server,
                                                   f"Unregistered player {player.name} ({player.ucid}) "
                                                   f"tried to join the running match on the {side} side."))
                    return
        await self.inform_streamer(server, _("Player {name} joined the match in a {unit} on the {side} side.").format(
            name=player.name, unit=player.unit_display_name, side=side))
