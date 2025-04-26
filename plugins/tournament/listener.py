import asyncio

from core import EventListener, event, Server, utils
from psycopg.rows import dict_row
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .commands import Tournament


class TournamentEventListener(EventListener["Tournament"]):

    def __init__(self, plugin: "Tournament"):
        super().__init__(plugin)
        self.tournaments: dict[str, dict] = {}

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
                AND round_number BETWEEN 1 AND %s
                AND winner_squadron_id IS NULL
            """, (tournament['tournament_id'], tournament['num_rounds']))
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
            tournament = self.tournaments.get(server.name)
            if len(server.get_active_players()) == tournament['num_players'] * 2:
                asyncio.create_task(server.current_mission.unpause())

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
        time_to_chose = config['time_to_chose']
        time = 0
        finished: dict[str, bool] = {
            "red": False,
            "blue": False
        }
        while time < time_to_chose and (not finished["red"] or not finished["blue"]):
            async with self.apool.connection() as conn:
                for side in ['blue', 'red']:
                    async with conn.transaction():
                        cursor = await conn.execute(f"""
                            SELECT choices_{side}_ack FROM tm_matches WHERE match_id = %s
                        """, (match_id,))
                        row = cursor.fetchone()
                        finished[side] = row[0]
            if time == int(time_to_chose / 2):
                await self.inform_squadrons(
                    server, message="The next round will start in {}!".format(utils.format_time(time_to_chose - time)))
            await asyncio.sleep(1)
            time += 1

    async def next_round(self, server: Server, match_id: int):
        await server.stop()
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
        await self.inform_squadrons(server, message="You can now use {} to chose your customizations!".format(
                (await utils.get_command(self.bot, group=self.plugin.tournament.name,
                                         name=self.plugin.customize.name)).mention))
        await self.wait_until_choices_finished()
        await self.inform_squadrons(server, message="Your choice will be applied to the next round.")
        await self.plugin.prepare_mission(server, match_id)
        await server.start()
        await self.inform_squadrons(server,
                                    message=f"Round {round_number} is starting now! Please jump into the server!")

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'mission_end':
            tournament = self.tournaments.get(server.name)
            match_id = await self.get_active_match(server)
            # do we have a winner?
            if data['arg1'] != 'TODO':
                side = data['arg1'].lower()
                async with self.apool.connection() as conn:
                    async with conn.transaction():
                        await conn.execute(f"""
                            UPDATE tm_matches 
                            SET squadron_{side}_rounds_won = squadron_{side}_rounds_won + 1
                            WHERE match_id = %s
                        """, (match_id,))
            # check if the match is finished
            winner_id = None
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor(row_factory=dict_row) as cursor:
                        await cursor.execute()
                        row = await cursor.fetchone("SELECT * FROM tm_matches WHERE match_id = %s", (match_id,))
                        if row['round_number'] == tournament['num_rounds']:
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
