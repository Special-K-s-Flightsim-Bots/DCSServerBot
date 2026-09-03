import aiohttp
import asyncio
import discord
import pandas as pd

from core import report, ReportEnv, utils, Side, Coalition, get_translation, df_to_table
from dataclasses import dataclass
from datetime import datetime
from plugins.missionstats import aar
from plugins.userstats.filter import StatisticsFilter
from psycopg.rows import dict_row

_ = get_translation(__name__.split('.')[1])


@dataclass
class Flight:
    start: datetime | None = None
    end: datetime | None = None
    plane: str | None = None
    death: bool = False

    def to_dict(self) -> dict:
        if self.start is None or self.end is None:
            # we never had a real flight – caller should skip this
            return {}
        return {
            "plane": self.plane,
            "start": self.start,
            "end": self.end,
            "time": (self.end - self.start).total_seconds(),
            "death": self.death,
        }


class Sorties(report.GraphElement):

    def __init__(self, env: ReportEnv, rows: int, cols: int, row: int | None = 0, col: int | None = 0,
                 colspan: int | None = 1, rowspan: int | None = 1, polar: bool | None = False):
        super().__init__(env, rows, cols, row, col, colspan, rowspan, polar)
        self.sorties = pd.DataFrame(columns=['plane', 'time', 'death'])

    @staticmethod
    def avg_survival_time(df: pd.DataFrame) -> pd.Series:
        """
        Return a Series indexed by plane with the *average survival time*.
        Missing planes (never died) are given an interval of 1, so the
        survival time equals the total flight time.
        """
        planes = df['plane'].unique()

        # Total time of all flights per plane
        survival_sum = df.groupby('plane')['time'].sum().reindex(planes, fill_value=0)

        # Number of deaths per plane
        death_counts = df[df['death']].groupby('plane').size().reindex(planes, fill_value=0)

        # Intervals = deaths – 1, but never less than 1
        intervals = (death_counts - 1).clip(lower=1)

        return survival_sum / intervals

    async def render(self, ucid: str, flt: StatisticsFilter) -> None:
        sql = f"""
            SELECT mission_id, init_type, init_cat, event, place, time 
            FROM missionstats s
            WHERE event IN (
                'S_EVENT_BIRTH', 
                'S_EVENT_TAKEOFF', 
                'S_EVENT_LAND', 
                'S_EVENT_CRASH', 
                'S_EVENT_EJECT',
                'S_EVENT_UNIT_LOST', 
                'S_EVENT_PLAYER_LEAVE_UNIT',
                'S_EVENT_DISCONNECT'
            )
            AND {flt.filter(self.env.bot)}
            AND init_id = %s 
            ORDER BY id
        """

        self.env.embed.title = flt.format(self.env.bot) + (self.env.embed.title or '')

        flight_records: list[dict] = []

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                flight = Flight()
                mission_id: int | None = None

                await cursor.execute(sql, (ucid,))
                async for row in cursor:

                    if row['mission_id'] != mission_id:
                        mission_id = row['mission_id']
                        d = flight.to_dict()
                        if d:  # finished flight from previous mission
                            flight_records.append(d)
                        flight = Flight()

                    if flight.plane is None:
                        flight.plane = row['init_type']

                    ev = row['event']

                    if ev == 'S_EVENT_BIRTH' and row['place'] is None:
                        if flight.start is None:
                            flight.start = row['time']

                    elif ev == 'S_EVENT_TAKEOFF':
                        if flight.start is None:
                            flight.start = row['time']

                    elif ev in (
                            'S_EVENT_LAND',
                            'S_EVENT_CRASH',
                            'S_EVENT_EJECT',
                            'S_EVENT_UNIT_LOST',
                            'S_EVENT_PLAYER_LEAVE_UNIT',
                            'S_EVENT_DISCONNECT',
                    ):
                        if flight.start is not None:
                            flight.end = row['time']
                            if ev != 'S_EVENT_LAND':
                                flight.death = True
                            flight_records.append(flight.to_dict())
                        flight = Flight()

                d = flight.to_dict()
                if d:
                    flight_records.append(d)

        self.sorties = pd.DataFrame(flight_records)

        if self.sorties.empty:
            self.axes.axis('off')
            self.axes.text(
                0.5, 0.5, _('No sorties found for this player.'),
                ha='center', va='center', rotation=45, size=15,
                transform=self.axes.transAxes
            )
            return

        stats = (
            self.sorties.groupby('plane')
            .agg(
                count=('time', 'size'),
                total_time=('time', 'sum'),
                avg_time=('time', 'mean')
            )
            .sort_values('total_time', ascending=False)
            .reset_index()
        )

        stats = stats.set_index('plane')
        stats['avg_survival'] = self.avg_survival_time(self.sorties)
        stats = stats.reset_index()

        # helper – works for any numeric column that may contain NaN
        def fmt(col_name: str, placeholder: str = '—'):
            return stats[col_name].apply(
                lambda s: utils.convert_time(int(round(s))) if not pd.isna(s) else placeholder
            )

        stats['total_time'] = fmt('total_time')
        stats['avg_time'] = fmt('avg_time')
        stats['avg_survival'] = fmt('avg_survival')

        self.axes = df_to_table(
            self.axes,
            stats[['plane', 'count', 'total_time', 'avg_time', 'avg_survival']],
            col_labels=[
                'Plane', 'Sorties', 'Total Flighttime',
                'Avg. Flighttime', 'Avg. Survivaltime'
            ]
        )

        self.env.embed.set_footer(
            text=_(
                'Flighttime is the time you were airborne from takeoff to '
                'landing / leave or airspawn to landing / leave.'
            )
        )


class MissionStats(report.EmbedElement):
    async def render(self, stats: dict, mission_id: int, sides: list[Coalition], **kwargs) -> None:
        self.add_field(name='▬▬▬▬▬▬▬▬▬▬▬ {} ▬▬▬▬▬▬▬▬▬▬▬'.format(_('Current Situation')),
                       value='_ _', inline=False)
        self.add_field(
            name='_ _', value=_('Airbases / FARPs\nPlanes\nHelicopters\nGround Units\nShips\nStructures'))
        for coalition in sides:
            coalition_data = stats['coalitions'][coalition.name]
            value = '{}\n'.format(len(coalition_data['airbases']))
            for unit_type in [_('Airplanes'), _('Helicopters'), _('Ground Units'), _('Ships')]:
                value += '{}\n'.format(len(coalition_data['units'][unit_type])
                                       if unit_type in coalition_data['units'] else 0)
            value += '{}\n'.format(len(coalition_data['statics']))
            self.add_field(name=coalition.name, value=value)

        # if no SQL was provided, do not print the actual achievements
        sql: str | None = kwargs.get('sql')
        if not sql:
            return
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
                    if Coalition.BLUE in sides:
                        self.add_field(name=Side.BLUE.name.capitalize(),
                                       value='\n'.join([str(x) for x in elements[Side.BLUE].values()]) or '_ _')
                    if Coalition.RED in sides:
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
        self.env.embed.title = flt.format(self.env.bot) + (self.env.embed.title or '')
        sql += ' AND ' + flt.filter(self.env.bot)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, self.env.params)
                row: dict | None = await cursor.fetchone()
                if not row:
                    return
                self.add_field(name=_('Usages'), value=str(row['num']))
                self.add_field(name=_('Total Playtime'), value=utils.convert_time(row['total'] or 0))
                self.add_field(name=_('Average Playtime'), value=utils.convert_time(row['average'] or 0))


class ModuleStats2(report.EmbedElement):
    async def render(self, ucid: str, module: str, flt: StatisticsFilter) -> None:
        weapons = hs_ratio = ks_ratio = ''
        category = None
        inner_sql1 = f"""
            SELECT CASE WHEN COALESCE(m.weapon, '') = '' OR m.event = 'S_EVENT_SHOOTING_START' 
                        THEN 'Gun' ELSE m.weapon 
                   END AS weapon, 
                   COALESCE(SUM(CASE WHEN m.event IN ('S_EVENT_SHOT', 'S_EVENT_SHOOTING_START') 
                                     THEN 1 ELSE 0 
                                END), 0
                   ) AS shots 
            FROM missionstats m
            JOIN statistics s ON m.mission_id = s.mission_id 
            WHERE m.init_id = s.player_ucid
              AND m.time BETWEEN s.hop_on and COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') 
              AND m.init_id = %(ucid)s 
              AND m.init_type = %(module)s 
              AND {flt.filter(self.env.bot)}
            GROUP BY 1
        """
        inner_sql2 = f"""
            WITH GroupedHits AS (
                -- Step 1: Filter and identify unique "hit windows"
                SELECT
                    m.mission_id,
                    s.hop_on,
                    COALESCE(s.hop_off, NOW()) AS hop_off_time,
                    m.init_id,
                    m.target_id, -- Assuming target_id is available or necessary for grouping
                    m.event,
                    -- CRITICAL CHANGE: Round the timestamp down to the desired interval (e.g., 1 second).
                    -- Adjust this function based on your database dialect (PostgreSQL, MySQL, SQL Server).
                    DATE_TRUNC('second', m.time) AS hit_window_start, 
                    CASE WHEN m.target_cat IN ('Airplanes', 'Helicopters') THEN 'Air' 
                         WHEN m.target_cat IN ('Ground Units', 'Ships', 'Structures') THEN 'Ground' 
                    END AS target_cat,
                    CASE WHEN COALESCE(m.weapon, '') = '' THEN 'Gun' ELSE m.weapon END AS weapon
                FROM missionstats m
                JOIN statistics s ON m.mission_id = s.mission_id
                WHERE 
                    m.event IN ('S_EVENT_HIT', 'S_EVENT_KILL') 
                    -- Filter for the relevant time window in the stats table
                    AND m.time BETWEEN s.hop_on AND COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC')
                    -- Existing filters
                    AND m.target_cat IS NOT NULL 
                    AND m.init_id = %(ucid)s
                    AND m.init_type = %(module)s
                    AND m.init_side <> m.target_side
                    AND {flt.filter(self.env.bot)}
            ),
            UniqueHitEvents AS (
                -- Step 2: Determine the distinct groups of hits for each target/source interaction
                SELECT DISTINCT
                    mission_id,
                    hit_window_start, -- This now defines a unique 'action' time window
                    init_id,
                    target_id,
                    target_cat,
                    weapon,
                    CASE WHEN event = 'S_EVENT_HIT' THEN 1 ELSE 0 END AS is_hit,
                    CASE WHEN event = 'S_EVENT_KILL' THEN 1 ELSE 0 END AS is_kill
                FROM GroupedHits
            )
            -- Step 3: Aggregate the unique events over the mission segment
            SELECT 
                target_cat, 
                weapon,
                -- Sum up the hits and kills grouped by the distinct (time window, init_id, target_id) combinations
                COALESCE(SUM(h.is_hit), 0) AS hits, 
                COALESCE(SUM(h.is_kill), 0) AS kills
            FROM UniqueHitEvents h
            GROUP BY 1, 2
        """
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


class ModuleStats3(report.EmbedElement):
    async def render(self, ucid: str, module: str, flt: StatisticsFilter) -> None:
        sql = f"""
            SELECT target_cat AS "Category", 
                   target_type AS "Unit Type", 
                   count(*) AS "Kills" 
            FROM missionstats m
            JOIN statistics s ON m.mission_id = s.mission_id 
            WHERE m.event = 'S_EVENT_KILL' 
              AND m.init_id = s.player_ucid
              AND m.time BETWEEN s.hop_on and COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') 
              AND m.init_id = %(ucid)s 
              AND m.init_type = %(module)s 
              AND {flt.filter(self.env.bot)}
              AND target_cat IS NOT NULL 
            GROUP BY 1, 2 
            ORDER BY 1,3 DESC       
        """
        categories = []
        target_types = []
        kills = []
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, self.env.params)
                async for row in cursor:
                    categories.append(row['Category'])
                    target_types.append(row['Unit Type'])
                    kills.append(str(row['Kills']))
        if categories:
            await report.Ruler(self.env).render(ruler_length=20)
            self.add_field(name=_("Category"), value="\n".join(categories))
            self.add_field(name=_("Unit Type"), value="\n".join(target_types))
            self.add_field(name=_("Kills"), value="\n".join(kills))


class Refuelings(report.EmbedElement):
    async def render(self, ucid: str, flt: StatisticsFilter) -> None:
        sql = f"""
              SELECT init_type, COUNT(*) 
              FROM missionstats 
              WHERE EVENT = 'S_EVENT_REFUELING_STOP'
              AND {flt.filter(self.env.bot)}
              AND init_id = %s 
              GROUP BY 1 
              ORDER BY 2 DESC
        """
        self.env.embed.title = flt.format(self.env.bot) + (self.env.embed.title or '')

        modules = []
        numbers = []
        async with self.apool.connection() as conn:
            cursor = await conn.execute(sql, (ucid,))
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
              AND ms.init_id  IS NOT NULL
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
                row: dict | None = await cursor.fetchone()
                if not row:
                    if flt.period and flt.period != 'all':
                        self.embed.description = "You have not been killed by anybody in this period."
                    else:
                        self.embed.description = "You have not been killed by anybody yet."
                    return
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
              AND ms.target_id IS NOT NULL
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
                row: dict | None = await cursor.fetchone()
                if not row:
                    if flt.period and flt.period != 'all':
                        self.embed.description = "You have not killed anybody in this period."
                    else:
                        self.embed.description = "You have not killed anybody yet."
                    return
                for k,v in row.items():
                    self.embed.add_field(name=k, value=v)


class AAR:
    """
    Builds a printable After Action Report (PDF) for a single pilot.

    All database access happens here, the (blocking) rendering is delegated to
    ``plugins.missionstats.aar`` and executed in a worker thread so the bot's
    event loop stays responsive.
    """

    def __init__(self, bot, ucid: str, name: str, flt: StatisticsFilter):
        self.bot = bot
        self.log = bot.log
        self.apool = bot.apool
        self.ucid = ucid
        self.name = name
        self.flt = flt

    async def _fetch_logo(self, url: str) -> bytes | None:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url, proxy=self.bot.node.proxy,
                                       proxy_auth=self.bot.node.proxy_auth) as response:
                    if response.status != 200:
                        return None
                    return await response.read()
        except Exception as ex:
            self.log.debug(f"AAR: could not load the squadron image: {ex}")
            return None

    async def _collect(self) -> dict | None:
        flt = self.flt.filter(self.bot)
        params = {'ucid': self.ucid}

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # sortie events (birth / takeoff / landing / loss)
                await cursor.execute(f"""
                    SELECT ms.mission_id, ms.event, ms.init_type, ms.init_side, ms.place, ms.time,
                           m.mission_name, m.mission_theatre, m.server_name
                    FROM missionstats ms
                    JOIN missions m ON m.id = ms.mission_id
                    WHERE ms.init_id = %(ucid)s
                      AND ms.event = ANY(%(events)s)
                      AND {flt}
                    ORDER BY ms.id
                """, params | {'events': aar.SORTIE_EVENTS})
                sorties, aborted = aar.reconstruct_sorties(await cursor.fetchall())

                # kills scored by this pilot
                await cursor.execute(f"""
                    SELECT ms.time, ms.mission_id, ms.init_type AS aircraft, ms.init_side,
                           ms.target_id, ms.target_type, ms.target_cat, ms.target_side, ms.weapon,
                           m.mission_name, m.mission_theatre, COALESCE(p.name, '') AS opponent
                    FROM missionstats ms
                    JOIN missions m ON m.id = ms.mission_id
                    LEFT JOIN players p ON p.ucid = ms.target_id
                    WHERE ms.init_id = %(ucid)s 
                    AND ms.event = 'S_EVENT_KILL' AND {flt}
                    AND ms.weapon != ms.init_type
                    ORDER BY ms.time
                """, params)
                kills = await cursor.fetchall()

                # kills scored against this pilot
                await cursor.execute(f"""
                    SELECT ms.time, ms.mission_id, ms.target_type AS aircraft, ms.target_side,
                           ms.init_id, ms.init_type AS threat, ms.init_cat AS threat_cat,
                           ms.init_side, ms.weapon, m.mission_name,
                           COALESCE(p.name, '') AS opponent
                    FROM missionstats ms
                    JOIN missions m ON m.id = ms.mission_id
                    LEFT JOIN players p ON p.ucid = ms.init_id
                    WHERE ms.target_id = %(ucid)s 
                    AND ms.event = 'S_EVENT_KILL' AND {flt}
                    AND ms.weapon != ms.init_type
                    ORDER BY ms.time
                """, params)
                deaths = await cursor.fetchall()

                # weapon employment
                await cursor.execute(f"""
                    SELECT CASE
                               WHEN COALESCE(weapon, '') = '' OR event = 'S_EVENT_SHOOTING_START'
                               THEN 'Gun' ELSE weapon
                           END AS weapon,
                           COUNT(*) FILTER (
                               WHERE event IN ('S_EVENT_SHOT', 'S_EVENT_SHOOTING_START')
                           ) AS shots,
                           COUNT(*) FILTER (WHERE event = 'S_EVENT_HIT') AS hits,
                           COUNT(*) FILTER (WHERE event = 'S_EVENT_KILL') AS kills
                    FROM missionstats
                    WHERE init_id = %(ucid)s
                      AND event IN ('S_EVENT_SHOT', 'S_EVENT_SHOOTING_START', 'S_EVENT_HIT',
                                    'S_EVENT_KILL')
                      AND COALESCE(target_side, '') <> COALESCE(init_side, '')
					  AND COALESCE(init_type, '') <> COALESCE(weapon, '')
                      AND {flt}
                    GROUP BY 1
                    ORDER BY 2 DESC, 4 DESC
                """, params)
                weapons = await cursor.fetchall()

                # air-to-air refuellings
                await cursor.execute(f"""
                    SELECT COUNT(*) AS refuelings
                    FROM missionstats
                    WHERE init_id = %(ucid)s AND event = 'S_EVENT_REFUELING_STOP' AND {flt}
                """, params)
                row = await cursor.fetchone()
                refuelings = row['refuelings'] if row else 0

                # squadron affiliation
                await cursor.execute("""
                    SELECT s.name, s.image_url
                    FROM squadrons s
                    JOIN squadron_members sm ON s.id = sm.squadron_id
                    WHERE sm.player_ucid = %(ucid)s
                    ORDER BY s.name
                """, params)
                squadrons = await cursor.fetchall()

        if not sorties and not kills and not deaths:
            return None

        aar.attribute_kills(sorties, kills)
        logo_url = next((x['image_url'] for x in squadrons if x['image_url']), None)
        return {
            'ucid': self.ucid,
            'pilot': self.name,
            'squadron': ', '.join(x['name'] for x in squadrons),
            'logo': await self._fetch_logo(logo_url) if logo_url else None,
            'period': self.flt.format(self.bot).replace('\n', ' ').strip(),
            'generated': datetime.now(),
            'sorties': sorties,
            'aborted': aborted,
            'kills': kills,
            'deaths': deaths,
            'weapons': weapons,
            'refuelings': refuelings
        }

    async def render(self) -> discord.File | None:
        data = await self._collect()
        if not data:
            return None
        buffer = await asyncio.to_thread(aar.build_pdf, data)
        return discord.File(fp=buffer, filename="AAR_{}_{}.pdf".format(
            aar.safe_filename(self.name), datetime.now().strftime('%Y%m%d')))
