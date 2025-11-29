import discord
import re

from abc import ABC, abstractmethod
from core import utils, Pagination, ReportEnv, const
from datetime import datetime, timezone
from discord import app_commands
from services.bot import DCSServerBot
from typing import Any, Type


class StatisticsFilter(ABC):
    def __init__(self, period: str | None = None):
        self._period = period

    @property
    def period(self) -> str:
        return self._period

    @staticmethod
    @abstractmethod
    def list(bot: DCSServerBot) -> list[str]:
        ...

    @staticmethod
    @abstractmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        ...

    @abstractmethod
    def filter(self, bot: DCSServerBot) -> str:
        ...

    @abstractmethod
    def format(self, bot: DCSServerBot) -> str:
        ...

    @staticmethod
    def detect(bot: DCSServerBot, period: str) -> Any:
        if MissionFilter.supports(bot, period):
            return MissionFilter(period)
        elif MissionIDFilter.supports(bot, period):
            return MissionIDFilter(period)
        elif TheatreFilter.supports(bot, period):
            return TheatreFilter(period)
        elif MonthFilter.supports(bot, period):
            return MonthFilter(period)
        elif CampaignFilter.supports(bot, period):
            return CampaignFilter(period)
        elif SquadronFilter.supports(bot, period):
            return SquadronFilter(period)
        elif PeriodFilter.supports(bot, period):
            return PeriodFilter(period)
        return None


class PeriodFilter(StatisticsFilter):

    @staticmethod
    def list(bot: DCSServerBot) -> list[str]:
        """
        All period names that can be passed directly to the filter.
        """
        return [
            'all',
            'today',
            'yesterday',
            'day',
            'week',
            'month',
            'quarter',
            'halfyear',
            'year',
        ]

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        """
        A period is supported if:
        • it starts with 'period:'  (custom range syntax)
        • it is one of the names in .list()
        • it contains a hyphen and the hyphen syntax is valid
        """
        return (
            (period and period.startswith('period:')) or
            period in PeriodFilter.list(bot) or
            '-' in period
        )

    @staticmethod
    def parse_date(date_str: str) -> datetime:
        """
        Accepts YYYYMMDD, YYYYMMDD HH, etc.  The format list is unchanged.
        """
        formats = [
            "%Y%m%d %H:%M:%S",
            "%Y%m%d %H:%M",
            "%Y%m%d %H",
            "%Y%m%d",
            "%Y%m",
            "%Y"
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        raise ValueError(f"Date format {date_str} is not supported")

    @staticmethod
    def _interval_from_period(period: str) -> str:
        """
        Convert a public period name into a Postgres interval literal.
        Unsupported names fall back to the literal itself (e.g. '1 week').
        """
        mapping = {
            'quarter': '3 months',
            'halfyear': '6 months'
        }
        return mapping.get(period, f"1 {period}")

    def filter(self, bot: DCSServerBot) -> str:
        # Normalize the period string
        period = self.period[7:].strip() if self.period and self.period.startswith('period:') else self.period

        # ------------------------------------------------------------------
        # 1  Handle the “all” case – no filtering
        # ------------------------------------------------------------------
        if period in (None, 'all'):
            return '1 = 1'

        # ------------------------------------------------------------------
        # 2  Special dates (today / yesterday)
        # ------------------------------------------------------------------
        if period == 'yesterday':
            return "DATE_TRUNC('day', s.hop_on) = current_date - 1"
        elif period == 'today':
            return "DATE_TRUNC('day', s.hop_on) = current_date"

        # ------------------------------------------------------------------
        # 3  One‑step intervals: day, week, month, quarter, year
        # ------------------------------------------------------------------
        if period in PeriodFilter.list(bot):
            # Translate friendly name → Postgres intervals
            interval_lit = PeriodFilter._interval_from_period(period)
            return f"s.hop_on > ((now() AT TIME ZONE 'utc') - interval '{interval_lit}')"

        # ------------------------------------------------------------------
        # 4  Custom “start‑end” syntax
        # ------------------------------------------------------------------
        if '-' in period:
            start, end = [p.strip() for p in period.split('-', 1)]

            # Pattern for “X unit” (e.g. “2 week”, “5 days”)
            pattern = re.compile(r'^\d+\s+(year|month|week|day|hour|minute|quarter|halfyear)s?$')
            if pattern.match(end):
                return f"s.hop_on > ((now() AT TIME ZONE 'utc') - interval '{end}')"

            # Otherwise treat both sides as dates
            start_dt = self.parse_date(start) if start else datetime(year=1970, month=1, day=1)
            end_dt   = self.parse_date(end)   if end   else datetime.now(tz=timezone.utc)

            return (
                f"s.hop_on >= '{start_dt.strftime('%Y-%m-%d %H:%M:%S')}'::TIMESTAMP "
                f"AND COALESCE(s.hop_off, (now() AT TIME ZONE 'utc')) <= "
                f"'{end_dt.strftime('%Y-%m-%d %H:%M:%S')}'"
            )

        # ------------------------------------------------------------------
        # 5  Fallback – no filtering
        # ------------------------------------------------------------------
        return "1 = 1"

    def format(self, bot: DCSServerBot) -> str:
        period = self.period[7:] if self.period and self.period.startswith('period:') else self.period

        if period in (None, 'all'):
            return 'Overall '
        elif period in ('today', 'yesterday'):
            return period.capitalize() + 's '
        elif period == 'day':
            return 'Daily'
        elif period in ('week', 'month', 'year', 'quarter'):
            # The last part of every name is turned into an adjective
            return period.capitalize() + 'ly '
        elif period == 'halfyear':
            return period.capitalize() + ' '
        elif '-' in period:
            return period + '\n'

        return period


class CampaignFilter(StatisticsFilter):
    @staticmethod
    def list(bot: DCSServerBot) -> list[str]:
        return [f"campaign:{x}" for x in utils.get_all_campaigns(bot)]

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period and (period.lower().startswith('campaign:') or period.casefold() in [
            x.casefold() for x in utils.get_all_campaigns(bot)
        ])

    def filter(self, bot: DCSServerBot) -> str:
        if self.period and self.period.startswith('campaign:'):
            period = self.period[9:].strip()
        else:
            period = self.period
        period = utils.sanitize_string(period)
        return f"""
            tsrange(s.hop_on, s.hop_off) && (
                SELECT tsrange(start, stop) FROM campaigns 
                WHERE name ILIKE '{period}'
            ) 
            AND m.server_name in (
                SELECT server_name FROM campaigns_servers s, campaigns c
                WHERE c.id = s.campaign_id AND c.name ILIKE '{period}'
            )
        """

    def format(self, bot: DCSServerBot) -> str:
        if self.period and self.period.lower().startswith('campaign:'):
            period = self.period[9:]
        else:
            period = self.period
        return f'Campaign "{period.capitalize()}"\n'


class MissionFilter(StatisticsFilter):
    @staticmethod
    def list(bot: DCSServerBot) -> list[str]:
        with bot.pool.connection() as conn:
            rows = conn.execute("SELECT DISTINCT mission_name FROM missions").fetchall()
            return [f"mission:{row[0]}" for row in rows]

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period and period.lower().startswith('mission:')

    def filter(self, bot: DCSServerBot) -> str:
        name = utils.sanitize_string(self.period[8:].strip())
        return f"m.mission_name ILIKE '%%{name}%%'"

    def format(self, bot: DCSServerBot) -> str:
        return f'Missions containing "{self.period[8:].strip().title()}"\n'


class MissionIDFilter(StatisticsFilter):
    @staticmethod
    def list(bot: DCSServerBot) -> list[str]:
        with bot.pool.connection() as conn:
            rows = conn.execute("SELECT id FROM missions ORDER BY id DESC").fetchall()
            return [row[0] for row in rows]

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period and period.startswith('mission_id:')

    def filter(self, bot: DCSServerBot) -> str:
        mission_id = utils.sanitize_string(self.period[11:].strip())
        return f"m.id = {mission_id}"

    def format(self, bot: DCSServerBot) -> str:
        return f'Mission '


class TheatreFilter(StatisticsFilter):
    @staticmethod
    def list(bot: DCSServerBot) -> list[str]:
        with bot.pool.connection() as conn:
            rows = conn.execute("SELECT DISTINCT mission_theatre FROM missions ORDER BY 1").fetchall()
            return [row[0] for row in rows]

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period and (period.startswith('theatre:') or period.startswith('terrain:'))

    def filter(self, bot: DCSServerBot) -> str:
        theatre = utils.sanitize_string(self.period[8:].strip())
        return f"m.mission_theatre ILIKE '{theatre.lower()}'"

    def format(self, bot: DCSServerBot) -> str:
        return f'Missions on theatre "{self.period[8:].strip().title()}"\n'


class MonthFilter(StatisticsFilter):
    @staticmethod
    def list(bot: DCSServerBot) -> list[str]:
        return [f"month:{const.MONTH[i]}" for i in range(1, 13)]

    @staticmethod
    def get_month(period: str):
        for i in range(1, 13):
            if period.casefold() in const.MONTH[i].casefold():
                return i
        return -1

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period and period.startswith('month:')

    def filter(self, bot: DCSServerBot) -> str:
        month = MonthFilter.get_month(self.period[6:].strip())
        return f"""
            DATE_PART('month', s.hop_on) = {month} AND 
            DATE_PART('year', s.hop_on) = DATE_PART('year', CURRENT_DATE)
        """

    def format(self, bot: DCSServerBot) -> str:
        month = MonthFilter.get_month(self.period[6:].strip())
        return f'Month "{const.MONTH[month]}" '


class SquadronFilter(StatisticsFilter):
    @staticmethod
    def list(bot: DCSServerBot) -> list[str]:
        with bot.pool.connection() as conn:
            rows = conn.execute("SELECT name FROM squadrons").fetchall()
            return [f"squadron:{row[0]}" for row in rows]

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period and period.lower().startswith('squadron:')

    def filter(self, bot: DCSServerBot) -> str:
        name = utils.sanitize_string(self.period[9:].strip())
        return f"""
            s.player_ucid IN (
                SELECT player_ucid 
                FROM squadron_members sm, squadrons ss 
                WHERE ss.id = sm.squadron_id AND ss.name = '{name}'
            )
        """

    def format(self, bot: DCSServerBot) -> str:
        return f'Squadron "{self.period[9:].strip().title()}"\n'


class MissionStatisticsFilter(PeriodFilter):

    def filter(self, bot: DCSServerBot) -> str:
        # Normalize the period string
        period = self.period[7:].strip() if self.period and self.period.startswith('period:') else self.period

        # ------------------------------------------------------------------
        # 1  Handle the “all” case – no filtering
        # ------------------------------------------------------------------
        if period in (None, 'all'):
            return '1 = 1'

        # ------------------------------------------------------------------
        # 2  Special dates (today / yesterday)
        # ------------------------------------------------------------------
        if period == 'yesterday':
            return "DATE_TRUNC('day', time) = current_date - 1"
        elif period == 'today':
            return "DATE_TRUNC('day', time) = current_date"

        # ------------------------------------------------------------------
        # 3  One‑step intervals: day, week, month, quarter, year
        # ------------------------------------------------------------------
        if period in PeriodFilter.list(bot):
            # Translate friendly name → Postgres intervals
            interval_lit = PeriodFilter._interval_from_period(period)
            return f"time > ((now() AT TIME ZONE 'utc') - interval '{interval_lit}')"

        # ------------------------------------------------------------------
        # 4  Custom “start‑end” syntax
        # ------------------------------------------------------------------
        if '-' in period:
            start, end = [p.strip() for p in period.split('-', 1)]

            # Pattern for “X unit” (e.g. “2 week”, “5 days”)
            pattern = re.compile(r'^\d+\s+(year|month|week|day|hour|minute|quarter)s?$')
            if pattern.match(end):
                return f"time > ((now() AT TIME ZONE 'utc') - interval '{end}')"

            # Otherwise treat both sides as dates
            start_dt = self.parse_date(start) if start else datetime(year=1970, month=1, day=1)
            end_dt   = self.parse_date(end)   if end   else datetime.now(tz=timezone.utc)

            return (
                f"time >= '{start_dt.strftime('%Y-%m-%d %H:%M:%S')}'::TIMESTAMP "
                f"AND time <= '{end_dt.strftime('%Y-%m-%d %H:%M:%S')}'"
            )

        # ------------------------------------------------------------------
        # 5  Fallback – no filtering
        # ------------------------------------------------------------------
        return "1 = 1"


class StatsPagination(Pagination):
    def __init__(self, env: ReportEnv):
        super().__init__(env)
        self.apool = env.bot.apool
        self.log = env.bot.log

    async def values(self, period: str, **kwargs) -> list[str]:
        async with self.apool.connection() as conn:
            async with conn.cursor() as cursor:
                if period in [None, 'all', 'day', 'week', 'month', 'year', 'today', 'yesterday']:
                    await cursor.execute('SELECT DISTINCT server_name FROM missions')
                else:
                    await cursor.execute('SELECT DISTINCT s.server_name FROM campaigns c, campaigns_servers s '
                                         'WHERE c.id = s.campaign_id AND c.name ILIKE %s', (period,))
                return [x[0] async for x in cursor]


class PeriodTransformer(app_commands.Transformer):
    def __init__(self, *, flt: list[Type[StatisticsFilter]]):
        super().__init__()
        self.filter: list[Type[StatisticsFilter]] = flt

    async def transform(self, interaction: discord.Interaction, value: str) -> StatisticsFilter | None:
        for flt in self.filter:
            if flt.supports(interaction.client, value):
                return flt(value)
        return PeriodFilter()

    async def autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        try:
            if not current and PeriodFilter in self.filter:
                return [
                    app_commands.Choice(name=x.title(), value=x) for x in PeriodFilter.list(interaction.client)
                ]
            periods = []
            for flt in self.filter:
                periods.extend(flt.list(interaction.client))
            return [
                app_commands.Choice(name=x.title(), value=x)
                for x in periods
                if not current or current.casefold() in x.casefold()
            ][:25]
        except Exception as ex:
            interaction.client.log.exception(ex)
            return []
