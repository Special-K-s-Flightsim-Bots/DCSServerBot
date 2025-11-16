import pandas as pd

from core import report, ReportEnv, utils, Side, Coalition, get_translation
from dataclasses import dataclass
from datetime import datetime
from plugins.userstats.filter import StatisticsFilter
from psycopg.rows import dict_row

_ = get_translation(__name__.split('.')[1])


@dataclass
class Flight:
    start: datetime = None
    end: datetime = None
    plane: str = None


class Sorties(report.EmbedElement):

    def __init__(self, env: ReportEnv) -> None:
        super().__init__(env)
        self.sorties = pd.DataFrame(columns=['plane', 'time'])

    def add_flight(self, flight: Flight) -> Flight:
        if flight.start and flight.end and flight.plane:
            self.sorties.loc[len(self.sorties.index)] = [flight.plane, flight.end - flight.start]
        return Flight()

    async def render(self, ucid: str, flt: StatisticsFilter) -> None:
        sql = """
            SELECT mission_id, init_type, init_cat, event, place, time 
            FROM missionstats s
            WHERE event IN ('S_EVENT_BIRTH', 'S_EVENT_TAKEOFF', 'S_EVENT_LAND', 'S_EVENT_UNIT_LOST', 
                            'S_EVENT_PLAYER_LEAVE_UNIT')
        """
        self.env.embed.title = flt.format(self.env.bot) + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot)
        sql += ' AND init_id = %s ORDER BY 6'

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                flight = Flight()
                mission_id = -1
                await cursor.execute(sql, (ucid, ))
                async for row in cursor:
                    if row['mission_id'] != mission_id:
                        mission_id = row['mission_id']
                        flight = self.add_flight(flight)
                    if not flight.plane:
                        flight.plane = row['init_type']
                    # air starts
                    if row['event'] == 'S_EVENT_BIRTH' and row['place'] is None:
                        if not flight.start:
                            flight.start = row['time']
                        else:
                            flight.end = row['time']
                            flight = self.add_flight(flight)
                            flight.start = row['time']
                    elif row['event'] == 'S_EVENT_TAKEOFF':
                        if not flight.start:
                            flight.start = row['time']
                        else:
                            flight.end = row['time']
                            flight = self.add_flight(flight)
                            flight.start = row['time']
                    elif row['event'] in ['S_EVENT_LAND', 'S_EVENT_UNIT_LOST', 'S_EVENT_PLAYER_LEAVE_UNIT']:
                        flight.end = row['time']
                        flight = self.add_flight(flight)
                df = self.sorties.groupby('plane').agg(
                    count=('time', 'size'), total_time=('time', 'sum')).sort_values(by=['total_time'],
                                                                                    ascending=False).reset_index()
                planes = sorties = times = ''
                for index, row in df.iterrows():
                    planes += row['plane'] + '\n'
                    sorties += str(row['count']) + '\n'
                    times += utils.convert_time(row['total_time'].total_seconds()) + '\n'
                if len(planes) == 0:
                    self.add_field(name=_('No sorties found for this player.'), value='_ _')
                else:
                    self.add_field(name=_('Module'), value=planes)
                    self.add_field(name=_('Sorties'), value=sorties)
                    self.add_field(name=_('Total Flighttime'), value=times)
                    self.embed.set_footer(
                        text=_('Flighttime is the time you were airborne from takeoff to landing / leave or\n'
                               'airspawn to landing / leave.'))


class MissionStats(report.EmbedElement):
    async def render(self, stats: dict, sql: str, mission_id: int) -> None:
        self.add_field(name='▬▬▬▬▬▬▬▬▬▬▬ {} ▬▬▬▬▬▬▬▬▬▬▬'.format(_('Current Situation')),
                       value='_ _', inline=False)
        self.add_field(
            name='_ _', value=_('Airbases / FARPs\nPlanes\nHelicopters\nGround Units\nShips\nStructures'))
        for coalition in [Coalition.BLUE, Coalition.RED]:
            coalition_data = stats['coalitions'][coalition.name]
            value = '{}\n'.format(len(coalition_data['airbases']))
            for unit_type in [_('Airplanes'), _('Helicopters'), _('Ground Units'), _('Ships')]:
                value += '{}\n'.format(len(coalition_data['units'][unit_type])
                                       if unit_type in coalition_data['units'] else 0)
            value += '{}\n'.format(len(coalition_data['statics']))
            self.add_field(name=coalition.name, value=value)
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, self.env.params)
                if cursor.rowcount > 0:
                    elements = {
                        Side.BLUE: {},
                        Side.RED: {}
                    }
                    self.add_field(name='▬▬▬▬▬▬▬▬▬▬▬ {} ▬▬▬▬▬▬▬▬▬▬▬▬'.format(_('Achievements')),
                                   value='_ _', inline=False)
                    async for row in cursor:
                        s = Side(int(row['init_side']))
                        for name, value in row.items():
                            if name == 'init_side':
                                continue
                            elements[s][name] = value
                    self.add_field(name='_ _', value='\n'.join(elements[Side.BLUE].keys()) or '_ _')
                    self.add_field(name=Side.BLUE.name.capitalize(),
                                   value='\n'.join([str(x) for x in elements[Side.BLUE].values()]) or '_ _')
                    self.add_field(name=Side.RED.name.capitalize(),
                                   value='\n'.join([str(x) for x in elements[Side.RED].values()]) or '_ _')


class ModuleStats1(report.EmbedElement):
    async def render(self, ucid: str, module: str, flt: StatisticsFilter) -> None:
        sql = """
            SELECT COUNT(*) as num, 
                   ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) as total, 
                   ROUND(AVG(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS average 
            FROM statistics s, missions m 
            WHERE s.mission_id = m.id AND s.player_ucid = %(ucid)s AND s.slot = %(module)s
        """
        self.env.embed.title = flt.format(self.env.bot) + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, self.env.params)
                row = await cursor.fetchone()
                self.add_field(name=_('Usages'), value=str(row['num']))
                self.add_field(name=_('Total Playtime'), value=utils.convert_time(row['total'] or 0))
                self.add_field(name=_('Average Playtime'), value=utils.convert_time(row['average'] or 0))


class ModuleStats2(report.EmbedElement):
    async def render(self, ucid: str, module: str, flt: StatisticsFilter) -> None:
        weapons = hs_ratio = ks_ratio = ''
        category = None
        inner_sql1 = """
            SELECT CASE WHEN COALESCE(m.weapon, '') = '' OR m.event = 'S_EVENT_SHOOTING_START' 
                        THEN 'Gun' ELSE m.weapon 
                   END AS weapon, 
                   COALESCE(SUM(CASE WHEN m.event IN ('S_EVENT_SHOT', 'S_EVENT_SHOOTING_START') 
                                     THEN 1 ELSE 0 
                                END), 0) AS shots 
            FROM missionstats m, statistics s 
            WHERE m.mission_id = s.mission_id AND m.time BETWEEN s.hop_on and COALESCE(s.hop_off, NOW()) 
            AND m.init_id = %(ucid)s AND m.init_type = %(module)s 
        """
        inner_sql1 += ' AND ' + flt.filter(self.env.bot)
        inner_sql1 += " GROUP BY 1"
        inner_sql2 = """
            SELECT CASE WHEN m.target_cat IN ('Airplanes', 'Helicopters') THEN 'Air' 
                        WHEN m.target_cat IN ('Ground Units', 'Ships', 'Structures') THEN 'Ground' 
                   END AS target_cat, 
                   CASE WHEN COALESCE(m.weapon, '') = '' THEN 'Gun' ELSE m.weapon 
                   END AS weapon, 
                   COALESCE(SUM(CASE WHEN m.event = 'S_EVENT_HIT' THEN 1 ELSE 0 END), 0) AS hits, 
                   COALESCE(SUM(CASE WHEN m.event = 'S_EVENT_KILL' THEN 1 ELSE 0 END), 0) AS kills 
            FROM missionstats m, statistics s 
            WHERE m.event IN ('S_EVENT_HIT', 'S_EVENT_KILL') 
            AND m.mission_id = s.mission_id AND m.time BETWEEN s.hop_on and COALESCE(s.hop_off, NOW()) 
            AND m.target_cat IS NOT NULL AND m.init_id = %(ucid)s AND m.init_type = %(module)s
            AND m.init_side <> m.target_side
        """
        inner_sql2 += ' AND ' + flt.filter(self.env.bot)
        inner_sql2 += " GROUP BY 1, 2"
        sql = f"""
                SELECT y.target_cat, y.weapon, x.shots, y.hits, y.kills, y.kills::DECIMAL / x.shots AS kd 
                FROM (
                    {inner_sql1}
                )x, (
                    {inner_sql2}
                ) y WHERE x.weapon = y.weapon AND x.shots <> 0 ORDER BY 1, 6 DESC
        """
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, self.env.params)
                async for row in cursor:
                    if row['weapon'] == _('Gun'):
                        continue
                    if category != row['target_cat']:
                        if len(weapons) > 0:
                            self.add_field(name=_('Weapon'), value=weapons)
                            self.add_field(name=_('Hits/Shot'), value=hs_ratio)
                            self.add_field(name=_('Kills/Shot'), value=ks_ratio)
                            weapons = hs_ratio = ks_ratio = ''
                        category = row['target_cat']
                        self.add_field(name="▬▬▬▬▬▬ {} ▬▬▬▬▬▬".format(_('Category {}').format(category)),
                                       value='_ _', inline=False)
                    shots = row['shots']
                    hits = row['hits']
                    kills = row['kills']
                    weapons += row['weapon'] + '\n'
                    hs_ratio += f"{100*hits/shots:.2f}%\n"
                    ks_ratio += f"{100*kills/shots:.2f}%\n"
        if weapons:
            self.add_field(name=_('Weapon'), value=weapons)
            self.add_field(name=_('Hits/Shot'), value=hs_ratio)
            self.add_field(name=_('Kills/Shot'), value=ks_ratio)


class Refuelings(report.EmbedElement):
    async def render(self, ucid: str, flt: StatisticsFilter) -> None:
        sql = "SELECT init_type, COUNT(*) FROM missionstats WHERE EVENT = 'S_EVENT_REFUELING_STOP'"
        self.env.embed.title = flt.format(self.env.bot) + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot)
        sql += ' AND init_id = %s GROUP BY 1 ORDER BY 2 DESC'

        modules = []
        numbers = []
        async with self.apool.connection() as conn:
            cursor = await conn.execute(sql, (ucid, ))
            async for row in cursor:
                modules.append(row[0])
                numbers.append(str(row[1]))
        if len(modules):
            self.add_field(name=_('Module'), value='\n'.join(modules))
            self.add_field(name=_('Refuelings'), value='\n'.join(numbers))
        else:
            self.add_field(name=_('No refuelings found for this user.'), value='_ _')


class Nemesis(report.EmbedElement):
    async def render(self, ucid: str, flt: StatisticsFilter) -> None:
        inner = flt.filter(self.env.bot)
        sql = f"""
            WITH nemesis_kills AS (
                SELECT
                    target_id AS nemesis_id,
                    COUNT(*) AS "Times killed Nemesis"
                FROM missionstats
                WHERE init_id   = %(ucid)s
                  AND target_id != %(ucid)s
                  AND event     = 'S_EVENT_KILL'
                  AND {inner}
                GROUP BY target_id
            )
            SELECT
                p.name AS "Nemesis name",
                COUNT(*) AS "Times killed by Nemesis",
                COALESCE(nk."Times killed Nemesis", 0) AS "Times killed Nemesis"
            FROM missionstats ms
            JOIN players p
                ON p.ucid = ms.init_id
            LEFT JOIN nemesis_kills nk
                ON nk.nemesis_id = ms.init_id
            WHERE ms.target_id = %(ucid)s
              AND ms.init_id  != '-1'
              AND ms.init_id  != %(ucid)s
              AND ms.event    = 'S_EVENT_KILL'
              AND {inner}
            GROUP BY ms.init_id, p.name, nk."Times killed Nemesis"
            ORDER BY "Times killed by Nemesis" DESC
            LIMIT 1;
        """
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"ucid": ucid})
                if cursor.rowcount == 0:
                    if flt.period and flt.period != 'all':
                        self.embed.description = "You have not been killed by anybody in this period."
                    else:
                        self.embed.description = "You have not been killed by anybody yet."
                    return
                row = await cursor.fetchone()
                for k,v in row.items():
                    self.embed.add_field(name=k, value=v)

class Antagonist(report.EmbedElement):
    async def render(self, ucid: str, flt: StatisticsFilter) -> None:
        inner = flt.filter(self.env.bot)
        sql = f"""
            WITH they_killed_you AS (
                SELECT
                    init_id AS killer_id,
                    COUNT(*) AS "Times they have killed you"
                FROM missionstats
                WHERE target_id = %(ucid)s
                  AND init_id  != %(ucid)s
                  AND event    = 'S_EVENT_KILL'
                  AND {inner}
               GROUP BY init_id
            )
            SELECT
                p.name AS "You are the Nemesis of",
                COUNT(*) AS "Times you killed them",
                COALESCE(tky."Times they have killed you", 0) AS "Times they have killed you"
            FROM missionstats ms
            JOIN players p
                ON p.ucid = ms.target_id
            LEFT JOIN they_killed_you tky
                ON tky.killer_id = ms.target_id
            WHERE ms.init_id   = %(ucid)s
              AND ms.target_id != '-1'
              AND ms.target_id != %(ucid)s
              AND ms.event     = 'S_EVENT_KILL'
              AND {inner}
            GROUP BY ms.target_id, p.name, tky."Times they have killed you"
            ORDER BY "Times you killed them" DESC
            LIMIT 5;
        """
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"ucid": ucid})
                if cursor.rowcount == 0:
                    if flt.period and flt.period != 'all':
                        self.embed.description = "You have not killed anybody in this period."
                    else:
                        self.embed.description = "You have not killed anybody yet."
                    return
                row = await cursor.fetchone()
                for k,v in row.items():
                    self.embed.add_field(name=k, value=v)
