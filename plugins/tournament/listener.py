import asyncio

from core import EventListener, event, Server, utils, get_translation, Coalition, Status
from psycopg.rows import dict_row
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .commands import Tournament

_ = get_translation(__name__.split('.')[1])


class TournamentEventListener(EventListener["Tournament"]):

    def __init__(self, plugin: "Tournament"):
        super().__init__(plugin)
        self.tournaments: dict[str, dict] = {}

    async def audit(self, server: Server, message: str):
        config = self.get_config(server)
        channel_id = config.get('channels', {}).get('admin')
        if channel_id:
            channel = self.bot.get_channel(channel_id)
        else:
            channel = self.bot.get_admin_channel(server)
        await channel.send(message)

    async def announce(self, server: Server, message: str):
        config = self.get_config(server)
        channel = self.bot.get_channel(config.get('channels', {}).get('info', -1))
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
        else:
            self.tournaments.pop(server.name, None)

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'S_EVENT_BIRTH':
            tournament = self.tournaments[server.name]
            initiator = data['initiator']
            if len(server.get_active_players()) == tournament['num_players'] * 2:
                asyncio.create_task(server.current_mission.unpause())
                asyncio.create_task(self.announce(server,
                                                  _("All sides have occupied their units. The match is now on!")))
                messages = [_("The server is now unpaused.")]
                config = self.get_config(server, plugin_name='competitive')
                win_on = config.get('win_on', 'survival')
                if win_on == 'survival':
                    messages.append(_("The first party to lose all their units will be defeated, "
                                      "making the surviving party the winner of the match."))
                elif win_on in ['landing', 'rtb']:
                    messages.append(_("To win the match, a party must eliminate all enemy aircraft AND safely land "
                                      "at least one of their own planes."))
                else:
                    asyncio.create_task(self.audit(server, f"Win-method {win_on} is not supported yet!"))
                asyncio.create_task(server.sendPopupMessage(Coalition.ALL, _("The server is now unpaused. Good luck!")))
            else:
                player = server.get_player(name=initiator['name'])
                asyncio.create_task(player.sendPopupMessage(
                    _("The server will be unpaused, if all players have chosen their slots!")))

    @event(name="onMatchFinished")
    async def onMatchFinished(self, server: Server, data: dict) -> None:
        winner = data['winner'].lower()
        tournament = self.tournaments.get(server.name)
        match_id = await self.get_active_match(server)
        # do we have a winner?
        if winner in ['red', 'blue']:
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
        else:
            match = await self.plugin.get_match(match_id)
            message = _("Round {} was a draw!").format(match['round_number'])
        # inform players and people
        asyncio.create_task(server.sendPopupMessage(Coalition.ALL, message))
        asyncio.create_task(self.inform_squadrons(server, message=message))
        asyncio.create_task(self.announce(server, message))

        # check if the match is finished
        winner_id = None
        async with self.apool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute("SELECT * FROM tm_matches WHERE match_id = %s", (match_id,))
                    row = await cursor.fetchone()
                    if row['round_number'] == tournament['rounds']:
                        if row['squadron_red_rounds_won'] < row['squadron_blue_rounds_won']:
                            winner_id = row['squadron_blue']
                        elif row['squadron_red_rounds_won'] > row['squadron_blue_rounds_won']:
                            winner_id = row['squadron_red']
                        if winner_id:
                            await cursor.execute(f"""
                                UPDATE tm_matches
                                SET winner_squadron_id = %s
                                WHERE match_id = %s
                            """, (winner_id, match_id))
        if not winner_id:
            asyncio.create_task(self.next_round(server, match_id))
        else:
            squadron = utils.get_squadron(self.node, squadron_id=winner_id)
            asyncio.create_task(self.inform_squadrons(
                server, message=f"Squadron {squadron['name']} is the winner of the match!"))
            asyncio.create_task(server.shutdown())

    async def inform_squadrons(self, server, *, message: str):
        config = self.get_config(server)
        for side in ['blue', 'red']:
            channel_id = config['channels'][side]
            channel = self.bot.get_channel(channel_id)
            # TODO: mention role / here
            await channel.send(message)

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
                        finished[side] = row[0]
            if time == int(time_to_choose / 2):
                await self.inform_squadrons(
                    server, message="The next round will start in {}!".format(utils.format_time(time_to_choose - time)))
            await asyncio.sleep(1)
            time += 1

    async def next_round(self, server: Server, match_id: int):
        await asyncio.create_task(server.sendPopupMessage(
            Coalition.ALL, _("You will now be moved back to spectators ...")))
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
        await self.wait_until_choices_finished(server)

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
        side = 'red' if data['side'] == 1 else 'blue'
        match_id = await self.get_active_match(server)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                cursor = await conn.execute(f"""
                    SELECT * FROM squadron_members sm 
                    JOIN tm_matches m ON m.squadron_{side} = sm.squadron_id
                    WHERE m.match_id = %s AND sm.player_ucid = %s
                """, (match_id, player.ucid))
                if cursor.rowcount == 0:
                    config = self.get_config(server)
                    if config.get('auto_join', False):
                        async with conn.transaction():
                            await conn.execute(f"""
                                INSERT INTO squadron_members (squadron_id, player_ucid)
                                SELECT squadron_{side} AS squadron_id, '{player.ucid}'::TEXT 
                                FROM tm_matches WHERE match_id = %s
                            """, (match_id, ))
                    else:
                        asyncio.create_task(server.kick(player, _("You are not a squadron member.\n"
                                                                  "Please ask your squadron leader to add you.")))
                        asyncio.create_task(self.audit(server,
                                                       f"Unregistered player {player.name} ({player.ucid}) "
                                                       f"tried to join the running match on the {side} side."))
                        return
