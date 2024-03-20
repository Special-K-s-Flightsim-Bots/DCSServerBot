import string

from typing import Literal

from core import EmbedElement, utils
from datetime import datetime
from psycopg.rows import dict_row

from .const import EMOJIS, StrafeQuality, BombQuality


class RangeBoard(EmbedElement):

    async def render(self, server_name: str, num_rows: int, sql1: str, sql2: str, what: Literal['strafe', 'bomb']):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                pilots = points = runs = ''
                max_time = datetime.fromisocalendar(1970, 1, 1)
                await cursor.execute(sql1, (num_rows, ))
                rows = await cursor.fetchall()
                for row in rows:
                    pilots += utils.escape_string(row['name']) + '\n'
                    points += f"{row['points']:.2f}\n"
                    await cursor.execute(sql2, (row['player_ucid'], ))
                    i = 0
                    runs += '**|'
                    async for run in cursor:
                        runs += EMOJIS[what][run['quality']] + '|'
                        i += 1
                    for i in range(i, 10):
                        runs += "⬛" + '|'
                    runs += '**\n'
                    if row['time'] > max_time:
                        max_time = row['time']

        # if there is nothing to plot, don't do it
        if not runs:
            return
        self.add_field(name='Pilot', value=pilots)
        self.add_field(name='Avg', value=points)
        self.add_field(name='|:one:|:two:|:three:|:four:|:five:|:six:|:seven:|:eight:|:nine:|:zero:|',
                       value=runs)
        footer = ''
        for value in StrafeQuality if what == 'strafe' else BombQuality:
            footer += EMOJIS[what][value.value] + '\t' + string.capwords(value.name.replace('_', ' ')) + '\n'

        if max_time:
            footer += f'\nLast recorded run: {max_time:%y-%m-%d %H:%M:%S}'
        self.embed.set_footer(text=footer)


class StrafeBoard(RangeBoard):

    async def render(self, server_name: str, num_rows: int, **kwargs):
        sql1 = """
            SELECT s.player_ucid, p.name, s.points, MAX(s.time) AS time 
            FROM (
                SELECT player_ucid, 
                       ROW_NUMBER() OVER w AS rn, 
                       AVG(quality) OVER w AS points, 
                       MAX(time) OVER w AS time 
                FROM strafe_runs
            """
        sql2 = 'SELECT quality FROM strafe_runs WHERE player_ucid = %s'
        if server_name:
            self.embed.description = utils.escape_string(server_name)
            sql1 += f" WHERE mission_id in (SELECT id FROM missions WHERE server_name = '{server_name}')"
            sql2 += f" AND mission_id in (SELECT id FROM missions WHERE server_name = '{server_name}')"
        sql1 += """
                WINDOW w AS (
                    PARTITION BY player_ucid ORDER BY ID DESC ROWS BETWEEN CURRENT ROW AND 9 FOLLOWING
                )
            ) s, players p 
            WHERE s.player_ucid = p.ucid 
            AND s.rn = 1 GROUP BY 1, 2, 3 ORDER BY 3 DESC LIMIT %s
        """
        sql2 += ' ORDER BY ID DESC LIMIT 10'
        await super().render(server_name, num_rows, sql1, sql2, 'strafe')


class BombBoard(RangeBoard):

    async def render(self, server_name: str, num_rows: int, **kwargs):
        sql1 = """
            SELECT b.player_ucid, p.name, b.points, MAX(b.time) AS time 
            FROM (
                SELECT player_ucid, 
                       ROW_NUMBER() OVER w AS rn, 
                       AVG(quality) OVER w AS points, 
                       MAX(time) OVER w AS time 
                FROM bomb_runs
            """
        sql2 = 'SELECT quality FROM bomb_runs WHERE player_ucid = %s'
        if server_name:
            self.embed.description = utils.escape_string(server_name)
            sql1 += f" WHERE mission_id in (SELECT id FROM missions WHERE server_name = '{server_name}')"
            sql2 += f" AND mission_id in (SELECT id FROM missions WHERE server_name = '{server_name}')"
        sql1 += """
                WINDOW w AS (
                    PARTITION BY player_ucid ORDER BY ID DESC ROWS BETWEEN CURRENT ROW AND 9 FOLLOWING
                )
            ) b, players p 
            WHERE b.player_ucid = p.ucid 
            AND b.rn = 1 GROUP BY 1, 2, 3 ORDER BY 3 DESC LIMIT %s
        """
        sql2 += ' ORDER BY ID DESC LIMIT 10'
        await super().render(server_name, num_rows, sql1, sql2, 'bomb')
