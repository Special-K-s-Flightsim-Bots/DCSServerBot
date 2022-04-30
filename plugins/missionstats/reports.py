import discord
import pandas as pd
import psycopg2
import string
from contextlib import closing
from core import report, ReportEnv, utils, const
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Union


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

    def render(self, member: Union[discord.Member, str], period: Optional[str] = None) -> None:
        sql = "SELECT mission_id, init_type, event, time FROM missionstats WHERE event IN " \
              "('S_EVENT_TAKEOFF', 'S_EVENT_LAND', 'S_EVENT_UNIT_LOST', 'S_EVENT_PLAYER_LEAVE_UNIT')"
        if period:
            self.env.embed.title = utils.format_period(period) + ' ' + self.env.embed.title
            sql += f" AND DATE(time) > (DATE(NOW()) - interval '1 {period}')"
        if isinstance(member, discord.Member):
            sql += " AND init_id IN (SELECT ucid FROM players WHERE discord_id = %s)"
        else:
            sql += " AND init_id = %s"
        sql += " ORDER BY 4"

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member, ))
                flight = Flight()
                mission_id = -1
                for row in cursor.fetchall():
                    if row['mission_id'] != mission_id:
                        mission_id = row['mission_id']
                        flight = self.add_flight(flight)
                    if not flight.plane:
                        flight.plane = row['init_type']
                    if row['event'] == 'S_EVENT_TAKEOFF':
                        if not flight.start:
                            flight.start = row['time']
                        else:
                            flight = self.add_flight(flight)
                    elif row['event'] in ['S_EVENT_LAND', 'S_EVENT_UNIT_LOST', 'S_EVENT_PLAYER_LEAVE_UNIT']:
                        flight.end = row['time']
                        flight = self.add_flight(flight)
                    else:
                        flight.end = row['time']
                self.add_flight(flight)
                df = self.sorties.groupby('plane').agg(count=('time', 'size'), total_time=('time', 'sum')).sort_values(by=['total_time'], ascending=False).reset_index()
                planes = sorties = times = ''
                for index, row in df.iterrows():
                    planes += row['plane'] + '\n'
                    sorties += str(row['count']) + '\n'
                    times += utils.convert_time(row['total_time'].total_seconds()) + '\n'
                if len(planes) == 0:
                    self.add_field(name='No sorties found for this player.', value='_ _')
                else:
                    self.embed.add_field(name='Planes', value=planes)
                    self.embed.add_field(name='Sorties', value=sorties)
                    self.embed.add_field(name='Total Flighttime', value=times)

        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class MissionStats(report.EmbedElement):
    def render(self, stats: dict, sql: str, mission_id: int, sides: list[str]) -> None:
        if len(sides) == 0:
            self.embed.add_field(name='Data can only be displayed in a private coalition channel!', value='_ _')
            return
        self.embed.add_field(name='▬▬▬▬▬▬ Current Situation ▬▬▬▬▬▬', value='_ _', inline=False)
        self.embed.add_field(
            name='_ _', value='Airbases / FARPs\nPlanes\nHelicopters\nGround Units\nShips\nStructures')
        for coalition in sides:
            coalition_data = stats['coalitions'][coalition]
            value = '{}\n'.format(len(coalition_data['airbases']))
            for unit_type in ['Airplanes', 'Helicopters', 'Ground Units', 'Ships']:
                value += '{}\n'.format(len(coalition_data['units'][unit_type])
                                       if unit_type in coalition_data['units'] else 0)
            value += '{}\n'.format(len(coalition_data['statics']))
            self.embed.add_field(name=coalition, value=value)
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute(sql, self.env.params)
                if cursor.rowcount > 0:
                    elements = {
                        const.SIDE_BLUE: {},
                        const.SIDE_RED: {}
                    }
                    self.embed.add_field(name='▬▬▬▬▬▬ Achievements ▬▬▬▬▬▬▬', value='_ _', inline=False)
                    for row in cursor.fetchall():
                        s = int(row['init_side'])
                        for name, value in row.items():
                            if name == 'init_side':
                                continue
                            elements[s][name] = value
                    self.embed.add_field(name='_ _', value='\n'.join(elements[const.SIDE_BLUE].keys()) or '_ _')
                    if 'Blue' in sides:
                        self.embed.add_field(name=string.capwords(const.PLAYER_SIDES[const.SIDE_BLUE]),
                                             value='\n'.join([str(x) for x in elements[const.SIDE_BLUE].values()]) or '_ _')
                    if 'Red' in sides:
                        self.embed.add_field(name=string.capwords(const.PLAYER_SIDES[const.SIDE_RED]),
                                             value='\n'.join([str(x) for x in elements[const.SIDE_RED].values()]) or '_ _')
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class ModuleStats1(report.EmbedElement):
    def render(self, sql: str, ucid: str, module: str, period: Optional[str] = None) -> None:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute(sql, self.env.params)
                row = cursor.fetchone()
                self.add_field(name='Usages', value=row['num'])
                self.add_field(name='Total Playtime', value=utils.convert_time(row['total'] or 0))
                self.add_field(name='Average Playtime', value=utils.convert_time(row['average'] or 0))
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class ModuleStats2(report.EmbedElement):
    def render(self, sql: str, ucid: str, module: str, period: Optional[str] = None) -> None:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute(sql, self.env.params)
                if cursor.rowcount > 0:
                    weapons = hs_ratio = ks_ratio = ''
                    category = None
                    for row in cursor.fetchall():
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
                        if shots == 0:
                            shots = hits
                        if shots == 0 and hits == 0 and kills == 0:
                            continue
                        weapons += row['weapon'] + '\n'
                        hs_ratio += f"{100*hits/shots:.2f}%\n"
                        ks_ratio += f"{100*kills/shots:.2f}%\n"
                    if len(weapons) > 0:
                        self.add_field(name='Weapon', value=weapons)
                        self.add_field(name='Hits/Shot', value=hs_ratio)
                        self.add_field(name='Kills/Shot', value=ks_ratio)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
