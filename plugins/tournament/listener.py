import asyncio
import discord
import re

from core import EventListener, event, Server, utils, get_translation, Coalition, DataObjectFactory, PersistentReport, \
    Player
from datetime import datetime, timedelta
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json
from trueskill import Rating
from typing import TYPE_CHECKING, Optional

from .const import TOURNAMENT_PHASE
from .utils import calculate_point_multipliers
from ..competitive.commands import Competitive
from ..creditsystem.squadron import Squadron
from ..userstats.filter import CampaignFilter

if TYPE_CHECKING:
    from .commands import Tournament

_ = get_translation(__name__.split('.')[1])


COALITION_FORMATS = {
    Coalition.BLUE: "```ansi\n\u001b[0;34mBLUE {}```",
    Coalition.RED: "```ansi\n\u001b[0;31mRED {}```",
    Coalition.ALL: "```ansi\n\u001b[0;32m{}```"
}


class TournamentEventListener(EventListener["Tournament"]):

    def __init__(self, plugin: "Tournament"):
        super().__init__(plugin)
        self.tournaments: dict[str, dict] = {}
        self.ratings: dict[int, Rating] = {}
        self.squadron_credits: dict[int, int] = {}
        self.round_started: dict[str, bool] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    async def audit(self, server: Server, message: str):
        config = self.get_config(server)
        channel_id = config.get('channels', {}).get('admin')
        if channel_id:
            channel = self.bot.get_channel(channel_id)
        else:
            channel = self.bot.get_admin_channel(server)
        await channel.send(message)

    async def inform_squadrons(self, server, *, message: str):
        match_id = await self.get_active_match(server)
        match = await self.plugin.get_match(match_id)
        for side in ['blue', 'red']:
            squadron = utils.get_squadron(self.node, squadron_id=match[f'squadron_{side}'])
            channel = await self.plugin.get_squadron_channel(match_id, side)
            await channel.send(self.bot.get_role(squadron['role']).mention + " " + message)

    async def inform_streamer(self, server: Server, message: str, coalition: Coalition = Coalition.ALL):
        config = self.get_config(server)
        channel = self.bot.get_channel(config.get('channels', {}).get('streamer', -1))
        if channel:
            await channel.send(COALITION_FORMATS[coalition].format(message))

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

    async def render_highscore(self, server: Server):
        config = self.get_config(server)
        channel = self.bot.get_channel(config.get('channels', {}).get('info', -1))
        if not channel:
            return

        tournament = self.tournaments.get(server.name)
        report = PersistentReport(self.bot, self.plugin_name, 'highscore.json',
                                  embed_name=f"tournament_{tournament['tournament_id']}_highscore",
                                  channel_id=channel.id)
        await report.render(interaction=None, server_name=None, flt=CampaignFilter(period=tournament['name']))

    async def processEvent(self, name: str, server: Server, data: dict) -> None:
        try:
            if name == 'registerDCSServer' or server.name in self.tournaments:
                await super().processEvent(name, server, data)
        except Exception as ex:
            self.log.exception(ex)

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        tournament_id = await self.get_active_tournament(server)
        if not tournament_id:
            self.tournaments.pop(server.name, None)
            return

        self.tournaments[server.name] = await self.plugin.get_tournament(tournament_id)
        match_id = await self.get_active_match(server)
        match = await self.plugin.get_match(match_id)
        # if no match is running, disable the tournament plugin
        if not match:
            self.tournaments.pop(server.name, None)
            return
        if not data['channel'].startswith('sync-'):
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
            self.round_started[server.name] = False
        else:
            self.round_started[server.name] = True

    async def countdown_with_warnings(self, server: Server, delayed_start: int):
        start_time = datetime.now()
        end_time = start_time + timedelta(seconds=delayed_start)
        last_minute_warning = None
        ten_second_warning_sent = False

        self.round_started[server.name] = False
        while True:
            remaining = int((end_time - datetime.now()).total_seconds())

            if remaining <= 0:
                break

            # Minute warnings
            current_minute = (remaining + 59) // 60
            if current_minute != last_minute_warning and remaining > 10:
                if current_minute > 0:  # Only show minute warnings if there's at least 1 minute
                    await server.sendPopupMessage(
                        Coalition.ALL, _("The round will start in {} minute{}.\n"
                                         "If you takeoff/engage before this time is over, you will be disqualified."
                                         ).format(current_minute, 's' if current_minute != 1 else ''))
                last_minute_warning = current_minute

            # Single 10-second warning
            if remaining <= 10 and not ten_second_warning_sent:
                await server.sendPopupMessage(Coalition.ALL, _("The round will start in 10 seconds."))
                ten_second_warning_sent = True

            await asyncio.sleep(1)

        self.round_started[server.name] = True
        await server.sendPopupMessage(Coalition.ALL, _("You are now allowed to takeoff. Happy fighting!"))

    @event(name="onSimulationResume")
    async def onSimulationResume(self, server: Server, data: dict) -> None:
        config = self.get_config(server)
        if 'delayed_start' in config:
            self.tasks[server.name] = asyncio.create_task(self.countdown_with_warnings(server, config['delayed_start']))
        else:
            self.round_started[server.name] = True

    async def disqualify(self, server: Server, player: Player, reason: str) -> None:
        await server.kick(player, reason)
        asyncio.create_task(self.inform_streamer(server, _("{} player {}: {}").format(
            player.coalition.value.title(), player.display_name, reason), coalition=player.coalition))

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        if not self.round_started[server.name]:
            if data['eventName'] in ['S_EVENT_RUNWAY_TAKEOFF', 'S_EVENT_TAKEOFF']:
                reason = _('Disqualified due to early takeoff.')
            elif data['eventName'] in ['S_EVENT_SHOT', 'S_EVENT_HIT', 'S_EVENT_KILL'] and data['target']:
                reason = _('Disqualified due to early engagement.')
            else:
                reason = None

            if reason:
                player = server.get_player(name=data['initiator']['name'])
                asyncio.create_task(self.disqualify(server, player, reason))

        if data['eventName'] == 'S_EVENT_BIRTH':
            tournament = self.tournaments[server.name]
            initiator = data['initiator']
            player = server.get_player(name=initiator['name'])

            # ignore multicrew members
            if player.sub_slot != 0:
                return

            # check if we have the necessary number of players
            num_planes = len([
                x for x in server.get_active_players()
                if x.sub_slot == 0 and x.unit_type not in [
                    '', 'instructor', 'forward_observer', 'observer', 'artillery_commander'
                ]
            ])
            if num_planes == tournament['num_players'] * 2:
                asyncio.create_task(server.current_mission.unpause())
                asyncio.create_task(self.inform_streamer(
                    server, _("All sides have occupied their units. The match can start now!"))
                )
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

        elif data['eventName'] == 'S_EVENT_SHOT':
            initiator = server.get_player(name=data['initiator']['name'])
            target = server.get_player(name=data['target']['name'])
            if target:
                asyncio.create_task(self.inform_streamer(server, _("{} player {} shot an {} at {} player {}").format(
                    initiator.coalition.value.title(), initiator.display_name, data['weapon']['name'],
                    target.coalition.value, target.display_name), coalition=initiator.coalition))

        elif data['eventName'] == 'S_EVENT_HIT':
            initiator = server.get_player(name=data['initiator']['name'])
            target = server.get_player(name=data['target']['name'])
            if target:
                asyncio.create_task(self.inform_streamer(server, _("{} player {} hit {} player {} with an {}").format(
                    initiator.coalition.value.title(), initiator.display_name, target.coalition.value,
                    target.display_name, data['weapon']['name']), coalition=initiator.coalition))

        elif data['eventName'] == 'S_EVENT_PLAYER_LEAVE_UNIT':
            if not data['initiator']:
                return
            player = server.get_player(name=data['initiator']['name'])
            if player:
                asyncio.create_task(self.inform_streamer(server, _("{} player {} is out!").format(
                    player.coalition.value.title(), player.display_name), coalition=player.coalition))

        elif data['eventName'] in ['S_EVENT_UNIT_LOST']:
            config = self.get_config(server)
            pattern = config.get('remove_on_death')
            initiator = server.get_player(name=data['initiator']['name'])
            if pattern and re.match(pattern, initiator.unit_name):
                match_id = await self.get_active_match(server)
                match = await self.plugin.get_match(match_id)
                squadron_id = match[f'squadron_{initiator.coalition.value}']
                async with self.apool.connection() as conn:
                    async with conn.transaction():
                        await conn.execute("""
                            INSERT INTO tm_persistent_choices (match_id, squadron_id, preset, config)
                            VALUES (%s, %s, %s, %s)
                        """, (match_id, squadron_id, 'disable_group', Json({"group": initiator.group_name})))
                asyncio.create_task(server.sendPopupMessage(
                    initiator.coalition, _("Unit {} is lost an will be permanently removed from the match.").format(
                        initiator.unit_name)))

    async def calculate_balance(self, server: Server, winner: str, winner_squadron: Squadron,
                                loser_squadron: Squadron) -> None:
        winner_coalition = Coalition.RED if winner == 'red' else Coalition.BLUE
        loser_coalition = Coalition.RED if winner == 'blue' else Coalition.BLUE
        winner_points = winner_squadron.points - self.squadron_credits[winner_squadron.squadron_id]
        loser_points = loser_squadron.points - self.squadron_credits[loser_squadron.squadron_id]
        self.log.debug(f"Winning squadron {winner_squadron.name} gained {winner_points} points during the round.")
        self.log.debug(f"Defeated squadron {loser_squadron.name} gained {loser_points} points during the round.")
        killer_multiplier, loser_multiplier = calculate_point_multipliers(self.ratings[winner_squadron.squadron_id],
                                                                          self.ratings[loser_squadron.squadron_id])
        self.log.debug(f"Mulipliers are as follows: Killer = {killer_multiplier} / Victim = {loser_multiplier}")
        winner_squadron.points -= winner_points  # remove the points first
        winner_squadron.points += winner_points * killer_multiplier  # add the points with the correct multiplier
        self.log.debug(f"Winner got {winner_points * killer_multiplier} points instead of {winner_points} points.")
        if winner_points * killer_multiplier > 0:
            await server.sendPopupMessage(
                winner_coalition, f"Squadron {winner_squadron.name}, you earned "
                                  f"{winner_points * killer_multiplier} points!")
        loser_squadron.points -= loser_points  # remove the points first
        loser_squadron.points += loser_points * loser_multiplier  # add the points with the correct multiplier
        self.log.debug(f"Loser got {loser_points * loser_multiplier} points instead of {loser_points} points.")
        if loser_points * loser_multiplier > 0:
            await server.sendPopupMessage(
                loser_coalition, f"Squadron {loser_squadron.name}, you earned "
                                 f"{loser_points * loser_multiplier} points!")

    async def check_match_finished(self, server: Server, match_id: int) -> None:
        config = self.get_config(server)
        tournament = self.tournaments.get(server.name)
        match = await self.plugin.get_match(match_id)

        # Calculate required wins for victory (best of N)
        required_wins = (tournament['rounds'] // 2) + 1
        total_rounds_played = match['round_number']

        winner_id = None
        # Check if either side has reached the required wins
        if match['squadron_blue_rounds_won'] >= required_wins:
            winner_id = match['squadron_blue']
        elif match['squadron_red_rounds_won'] >= required_wins:
            winner_id = match['squadron_red']
        # If we've played all rounds or more and still no winner
        elif total_rounds_played >= tournament['rounds']:
            if config.get('sudden_death', False):
                # If all rounds were draws, play one additional decisive round
                if match['squadron_blue_rounds_won'] == match['squadron_red_rounds_won']:
                    message = _("No winner found yet, playing one decisive round!")
                # Otherwise determine winner by who has more rounds won
                elif match['squadron_blue_rounds_won'] > match['squadron_red_rounds_won']:
                    winner_id = match['squadron_blue']
                elif match['squadron_red_rounds_won'] > match['squadron_blue_rounds_won']:
                    winner_id = match['squadron_red']
            else:
                message = _("We have found no winner yet, continuing the match with another round!")
        else:
            message = _("The match will continue with round {}.").format(total_rounds_played + 1)

        if winner_id:
            # update the database
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("UPDATE tm_matches SET winner_squadron_id = %s WHERE match_id = %s",
                                       (winner_id, match_id))
            # inform people
            squadron = utils.get_squadron(self.node, squadron_id=winner_id)
            asyncio.create_task(self.plugin.render_info_embed(tournament['tournament_id'],
                                                              phase=TOURNAMENT_PHASE.MATCH_FINISHED, match_id=match_id))
            message = _("Squadron {squadron} is the winner of the match!").format(squadron=squadron['name'])
            message += _("\nServer will be shut down in 60 seonds ...")
            asyncio.create_task(server.sendPopupMessage(Coalition.ALL, message, 60))
            await asyncio.sleep(60)
            asyncio.create_task(self.cleanup(server))
            asyncio.create_task(self.audit(
                server,f"Match {match_id} is now finished. Squadron {squadron['name']} won the match.\n"
                       f"Closing the squadron channels now."))
            asyncio.create_task(self.plugin.close_channel(match_id))
            # check if that was the last game to play
            asyncio.create_task(self.check_tournament_finished(tournament['tournament_id']))
        else:
            # inform people
            asyncio.create_task(self.inform_squadrons(server, message=message))
            asyncio.create_task(self.plugin.render_info_embed(tournament['tournament_id'],
                                                              phase=TOURNAMENT_PHASE.MATCH_RUNNING, match_id=match_id))
            asyncio.create_task(server.sendPopupMessage(Coalition.ALL, message))
            # play another round
            asyncio.create_task(self.next_round(server, match_id))

    async def check_tournament_finished(self, tournament_id: int) -> bool:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT stage, SUM(CASE WHEN winner_squadron_id IS NULL THEN 0 ELSE 1 END) AS NUM FROM tm_matches 
                WHERE tournament_id = %s
                GROUP BY stage
                ORDER BY stage DESC
				LIMIT 1
            """, (tournament_id,))
            num = (await cursor.fetchone())[1]
            if num == 1:
                asyncio.create_task(self.plugin.render_info_embed(tournament_id,
                                                                  phase=TOURNAMENT_PHASE.TOURNAMENT_FINISHED))
                return True
            return False

    @event(name="onMatchFinished")
    async def onMatchFinished(self, server: Server, data: dict) -> None:
        winner = data['winner'].lower()
        match_id = await self.get_active_match(server)
        if self.tasks.get(server.name):
            self.tasks.pop(server.name).cancel()

        # do we have a winner?
        if winner in ['blue', 'red']:
            loser = 'blue' if winner == 'red' else 'red'
            coalition = Coalition.RED if winner == 'red' else Coalition.BLUE
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    cursor = await conn.execute(f"""
                        UPDATE tm_matches 
                        SET squadron_{winner}_rounds_won = squadron_{winner}_rounds_won + 1
                        WHERE match_id = %s
                        RETURNING squadron_{winner}, squadron_{loser}, round_number
                    """, (match_id,))
                    winner_id, loser_id, round_number = await cursor.fetchone()
            winner_squadron = await self.plugin.get_squadron(match_id, winner_id)
            loser_squadron = await self.plugin.get_squadron(match_id, loser_id)
            message = _("Squadron {name} won round {round}!").format(name=winner_squadron.name, round=round_number)

            # calculate balance
            if self.get_config(server).get('balance_multiplier', False):
                await self.calculate_balance(server, winner, winner_squadron, loser_squadron)

        else:
            coalition = Coalition.ALL
            match = await self.plugin.get_match(match_id)
            message = _("Round {} was a draw!").format(match['round_number'])

        # inform players and people
        asyncio.create_task(server.sendPopupMessage(Coalition.ALL, message, 60))
        asyncio.create_task(self.inform_squadrons(server, message=message))
        asyncio.create_task(self.inform_streamer(server, message, coalition))

        # pause the server
        asyncio.create_task(server.current_mission.pause())

        # update the highscore
        asyncio.create_task(self.render_highscore(server))

        # check if the match is finished
        await self.check_match_finished(server, match_id)

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
            if time_to_choose - time in [300, 180, 60]:
                await self.inform_squadrons(
                    server,
                    message="## :warning: The next round will start in {}!".format(
                        utils.format_time(time_to_choose - time))
                )
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
        await utils.run_parallel_nofail(*tasks)
        await asyncio.sleep(1)
        # TODO: check squadron credits!
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
        new_mission = await self.plugin.prepare_mission(server, match_id)
        await server.sendPopupMessage(Coalition.ALL, _("The next round will start in 10s!"))
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
        await self.inform_streamer(server, _("Player {name} joined the match in their {unit}.").format(
            name=player.name, unit=player.unit_display_name), coalition=player.coalition)
