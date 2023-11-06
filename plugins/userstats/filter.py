from abc import ABC, abstractmethod
from contextlib import closing
from core import utils, Pagination, ReportEnv, const
from services import DCSServerBot
from typing import Any, Optional


class StatisticsFilter(ABC):
    @staticmethod
    @abstractmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        ...

    @staticmethod
    @abstractmethod
    def filter(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        ...

    @staticmethod
    @abstractmethod
    def format(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        ...

    @staticmethod
    def detect(bot: DCSServerBot, period: str) -> Any:
        if MissionFilter.supports(bot, period):
            return MissionFilter
        elif MonthFilter.supports(bot, period):
            return MonthFilter
        elif PeriodFilter.supports(bot, period):
            return PeriodFilter
        elif CampaignFilter.supports(bot, period):
            return CampaignFilter
        elif MixedFilter.supports(bot, period):
            return MixedFilter
        return None


class PeriodFilter(StatisticsFilter):
    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return (period and period.startswith('period:')) or period \
               in ['all', 'day', 'week', 'month', 'year', 'today', 'yesterday']

    @staticmethod
    def filter(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        if period and period.startswith('period:'):
            period = period[7:]
        if period in [None, 'all']:
            return '1 = 1'
        elif period == 'yesterday':
            return "DATE_TRUNC('day', s.hop_on) = current_date - 1"
        elif period == 'today':
            return "DATE_TRUNC('day', s.hop_on) = current_date"
        else:
            return f'DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'

    @staticmethod
    def format(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        if period and period.startswith('period:'):
            period = period[7:]
        if period in [None, 'all']:
            return 'Overall'
        elif period == 'day':
            return 'Daily'
        elif period == 'yesterday':
            return 'Yesterdays'
        else:
            return period.capitalize() + 'ly'


class CampaignFilter(StatisticsFilter):
    def __init__(self, campaign: str):
        self.campaign = campaign

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period and (period.startswith('campaign:') or period.casefold() in utils.get_all_campaigns(bot))

    @staticmethod
    def filter(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        if period and period.startswith('campaign:'):
            period = period[9:]
        return f"tsrange(s.hop_on, s.hop_off) && (SELECT tsrange(start, stop) FROM campaigns " \
               f"WHERE name ILIKE '{period}')"

    @staticmethod
    def format(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        if period and period.startswith('campaign:'):
            period = period[9:]
        return f'Campaign "{period.capitalize()}"'


class MixedFilter(StatisticsFilter):
    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period is None

    @staticmethod
    def filter(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        if not server_name and len(bot.servers) == 1:
            server = list(bot.servers.values())[0]
        elif server_name in bot.servers:
            server = bot.servers[server_name]
        else:
            return PeriodFilter.filter(bot, period)
        _, name = utils.get_running_campaign(bot, server)
        if name:
            return CampaignFilter.filter(bot, name, server_name)
        else:
            return PeriodFilter.filter(bot, period, server_name)

    @staticmethod
    def format(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        if not server_name and len(bot.servers) == 1:
            server = list(bot.servers.values())[0]
        elif server_name in bot.servers:
            server = bot.servers[server_name]
        else:
            return PeriodFilter.format(bot, period)
        _, name = utils.get_running_campaign(bot, server)
        if name:
            return CampaignFilter.format(bot, name, server_name)
        else:
            return PeriodFilter.format(bot, period, server_name)


class MissionFilter(StatisticsFilter):
    def __init__(self, campaign: str):
        self.campaign = campaign

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period and period.startswith('mission:')

    @staticmethod
    def filter(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        return f"m.mission_name ILIKE '%%{period[8:]}%%'"

    @staticmethod
    def format(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        return f'Missions containing "{period[8:].title()}"'


class MonthFilter(StatisticsFilter):
    def __init__(self, campaign: str):
        self.campaign = campaign

    @staticmethod
    def get_month(period: str):
        for i in range(1, 13):
            if period.casefold() in const.MONTH[i].casefold():
                return i
        return -1

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period and period.startswith('month:')

    @staticmethod
    def filter(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        month = MonthFilter.get_month(period[6:])
        return f"DATE_PART('month', s.hop_on) = {month} AND DATE_PART('year', s.hop_on) = DATE_PART('year', CURRENT_DATE)"

    @staticmethod
    def format(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        month = MonthFilter.get_month(period[6:])
        return f'Month "{const.MONTH[month]}"'


class MissionStatisticsFilter(StatisticsFilter):
    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return PeriodFilter.supports(bot, period)

    @staticmethod
    def filter(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        if period in [None, 'all', 'all']:
            return '1 = 1'
        else:
            return f'DATE(time) > (DATE(NOW()) - interval \'1 {period}\')'

    @staticmethod
    def format(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        return PeriodFilter.format(bot, period, server_name)


class StatsPagination(Pagination):
    def __init__(self, env: ReportEnv):
        super().__init__(env)
        self.pool = env.bot.pool
        self.log = env.bot.log

    def values(self, period: str, **kwargs) -> list[str]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                if period in [None, 'all', 'day', 'week', 'month', 'year', 'today', 'yesterday']:
                    cursor.execute('SELECT DISTINCT server_name FROM missions')
                else:
                    cursor.execute('SELECT DISTINCT s.server_name FROM campaigns c, campaigns_servers s WHERE '
                                   'c.id = s.campaign_id AND c.name ILIKE %s', (period,))
                return [x[0] for x in cursor.fetchall()]
