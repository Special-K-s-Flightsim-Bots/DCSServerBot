import pandas as pd
from contextlib import closing
from core import report, ReportEnv, utils, Side, Coalition
from dataclasses import dataclass
from datetime import datetime
from plugins.userstats.filter import StatisticsFilter
from psycopg.rows import dict_row


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

    async def render(self, ucid: str, period: str, flt: StatisticsFilter) -> None:
        sql = """
            SELECT mission_id, init_type, init_cat, event, place, time 
            FROM missionstats s
            WHERE event IN ('S_EVENT_BIRTH', 'S_EVENT_TAKEOFF', 'S_EVENT_LAND', 'S_EVENT_UNIT_LOST', 'S_EVENT_PLAYER_LEAVE_UNIT')
        """
        self.env.embed.title = flt.format(self.env.bot, period) + ' ' + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot, period)
        sql += ' AND init_id = %s ORDER BY 6'

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                flight = Flight()
                mission_id = -1
                for row in cursor.execute(sql, (ucid, )):
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
                    self.add_field(name='No sorties found for this player.', value='_ _')
                else:
                    self.add_field(name='Module', value=planes)
                    self.add_field(name='Sorties', value=sorties)
                    self.add_field(name='Total Flighttime', value=times)
                    self.embed.set_footer(text='Flighttime is the time you were airborne from takeoff to landing / '
                                               'leave or\nairspawn to landing / leave.')


class MissionStats(report.EmbedElement):
    async def render(self, stats: dict, sql: str, mission_id: int, sides: list[Coalition]) -> None:
        if len(sides) == 0:
            self.add_field(name='Data can only be displayed in a private coalition channel!', value='_ _')
            return
        self.add_field(name='▬▬▬▬▬▬▬▬▬▬▬ Current Situation ▬▬▬▬▬▬▬▬▬▬▬', value='_ _', inline=False)
        self.add_field(
            name='_ _', value='Airbases / FARPs\nPlanes\nHelicopters\nGround Units\nShips\nStructures')
        for coalition in sides:
            coalition_data = stats['coalitions'][coalition.name]
            value = '{}\n'.format(len(coalition_data['airbases']))
            for unit_type in ['Airplanes', 'Helicopters', 'Ground Units', 'Ships']:
                value += '{}\n'.format(len(coalition_data['units'][unit_type])
                                       if unit_type in coalition_data['units'] else 0)
            value += '{}\n'.format(len(coalition_data['statics']))
            self.add_field(name=coalition.name, value=value)
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(sql, self.env.params)
                if cursor.rowcount > 0:
                    elements = {
                        Side.BLUE: {},
                        Side.RED: {}
                    }
                    self.add_field(name='▬▬▬▬▬▬▬▬▬▬▬ Achievements ▬▬▬▬▬▬▬▬▬▬▬▬', value='_ _', inline=False)
                    for row in cursor:
                        s = Side(int(row['init_side']))
                        for name, value in row.items():
                            if name == 'init_side':
                                continue
                            elements[s][name] = value
                    self.add_field(name='_ _', value='\n'.join(elements[Side.BLUE].keys()) or '_ _')
                    if Coalition.BLUE in sides:
                        self.add_field(name=Side.BLUE.name.capitalize(),
                                       value='\n'.join([str(x) for x in elements[Side.BLUE].values()]) or '_ _')
                    if Coalition.RED in sides:
                        self.add_field(name=Side.RED.name.capitalize(),
                                       value='\n'.join([str(x) for x in elements[Side.RED].values()]) or '_ _')


class ModuleStats1(report.EmbedElement):
    async def render(self, ucid: str, module: str, period: str, flt: StatisticsFilter) -> None:
        sql = """
            SELECT COUNT(*) as num, 
                   ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) as total, 
                   ROUND(AVG(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS average 
            FROM statistics s, missions m 
            WHERE s.mission_id = m.id AND s.player_ucid = %(ucid)s AND s.slot = %(module)s
        """
        self.env.embed.title = flt.format(self.env.bot, period) + ' ' + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot, period)

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                row = cursor.execute(sql, self.env.params).fetchone()
                self.add_field(name='Usages', value=str(row['num']))
                self.add_field(name='Total Playtime', value=utils.convert_time(row['total'] or 0))
                self.add_field(name='Average Playtime', value=utils.convert_time(row['average'] or 0))


class ModuleStats2(report.EmbedElement):
    async def render(self, ucid: str, module: str, period: str, flt: StatisticsFilter) -> None:
        weapons = hs_ratio = ks_ratio = ''
        category = None
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                for row in cursor.execute("""
                    SELECT y.target_cat, y.weapon, x.shots, y.hits, y.kills, y.kills::DECIMAL / x.shots AS kd 
                    FROM (
                        SELECT CASE WHEN COALESCE(m.weapon, '') = '' OR m.event = 'S_EVENT_SHOOTING_START' 
                                    THEN 'Gun' ELSE m.weapon 
                               END AS weapon, 
                               COALESCE(SUM(CASE WHEN m.event IN ('S_EVENT_SHOT', 'S_EVENT_SHOOTING_START') 
                                                 THEN 1 ELSE 0 
                                            END), 0) AS shots 
                        FROM missionstats m, statistics s 
                        WHERE m.mission_id = s.mission_id AND m.time BETWEEN s.hop_on and COALESCE(s.hop_off, NOW()) 
                        AND m.init_id = %(ucid)s AND m.init_type = %(module)s 
                        GROUP BY 1
                    )x, (
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
                        GROUP BY 1, 2 
                    ) y WHERE x.weapon = y.weapon AND x.shots <> 0 ORDER BY 1, 6 DESC
                """, self.env.params):
                    if row['weapon'] == 'Gun':
                        continue
                    if category != row['target_cat']:
                        if len(weapons) > 0:
                            self.add_field(name='Weapon', value=weapons)
                            self.add_field(name='Hits/Shot', value=hs_ratio)
                            self.add_field(name='Kills/Shot', value=ks_ratio)
                            weapons = hs_ratio = ks_ratio = ''
                        category = row['target_cat']
                        self.add_field(name=f"▬▬▬▬▬▬ Category {category} ▬▬▬▬▬▬", value='_ _', inline=False)
                    shots = row['shots']
                    hits = row['hits']
                    kills = row['kills']
                    weapons += row['weapon'] + '\n'
                    hs_ratio += f"{100*hits/shots:.2f}%\n"
                    ks_ratio += f"{100*kills/shots:.2f}%\n"
        if weapons:
            self.add_field(name='Weapon', value=weapons)
            self.add_field(name='Hits/Shot', value=hs_ratio)
            self.add_field(name='Kills/Shot', value=ks_ratio)


class Refuelings(report.EmbedElement):
    async def render(self, ucid: str, period: str, flt: StatisticsFilter) -> None:
        sql = "SELECT init_type, COUNT(*) FROM missionstats WHERE EVENT = 'S_EVENT_REFUELING_STOP'"
        if period:
            self.env.embed.title = flt.format(self.env.bot, period) + ' ' + self.env.embed.title
            sql += ' AND ' + flt.filter(self.env.bot, period)
        sql += ' AND init_id = %s GROUP BY 1 ORDER BY 2 DESC'

        modules = []
        numbers = []
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                for row in cursor.execute(sql, (ucid, )):
                    modules.append(row[0])
                    numbers.append(str(row[1]))
        if len(modules):
            self.add_field(name='Module', value='\n'.join(modules))
            self.add_field(name='Refuelings', value='\n'.join(numbers))
        else:
            self.add_field(name='No refuelings found for this user.', value='_ _')
