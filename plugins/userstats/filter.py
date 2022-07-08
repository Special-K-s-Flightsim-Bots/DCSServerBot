import psycopg2
import string
from abc import ABC, abstractmethod
from contextlib import closing
from core import DCSServerBot, utils, Pagination, ReportEnv
from typing import Any, Optional


class StatisticsFilter(ABC):
    @staticmethod
    @abstractmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        pass

    @staticmethod
    @abstractmethod
    def filter(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        pass

    @staticmethod
    @abstractmethod
    def format(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        pass

    @staticmethod
    def detect(bot: DCSServerBot, period: str) -> Any:
        if PeriodFilter.supports(bot, period):
            return PeriodFilter
        elif CampaignFilter.supports(bot, period):
            return CampaignFilter
        elif MixedFilter.supports(bot, period):
            return MixedFilter
        return None


class PeriodFilter(StatisticsFilter):
    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period in ['all', 'day', 'week', 'month', 'year', 'yesterday']

    @staticmethod
    def filter(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        if period in [None, 'all']:
            return '1 = 1'
        elif period == 'yesterday':
            return "DATE_TRUNC('day', s.hop_on) = current_date - 1"
        else:
            return f'DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'

    @staticmethod
    def format(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        if period in [None, 'all']:
            return 'Overall'
        elif period == 'day':
            return 'Daily'
        elif period == 'yesterday':
            return 'Yesterdays'
        else:
            return string.capwords(period) + 'ly'


class CampaignFilter(StatisticsFilter):
    def __init__(self, campaign: str):
        self.campaign = campaign

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period in [x[0] for x in utils.get_all_campaigns(bot)]

    @staticmethod
    def filter(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        return f"tsrange(s.hop_on, s.hop_off) && (SELECT tsrange(start, stop) FROM campaigns " \
               f"WHERE name='{period}')"

    @staticmethod
    def format(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        return f'Campaign "{period}"'


class MixedFilter(StatisticsFilter):
    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return period is None

    @staticmethod
    def filter(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        if not server_name and len(bot.servers) == 1:
            server = bot.servers.values()[0]
        elif server_name in bot.servers:
            server = bot.servers[server_name]
        else:
            return PeriodFilter.filter(bot, period)
        campaign = utils.get_running_campaign(server)
        if campaign:
            return CampaignFilter.filter(bot, campaign, server_name)
        else:
            return PeriodFilter.filter(bot, period, server_name)

    @staticmethod
    def format(bot: DCSServerBot, period: str, server_name: Optional[str] = None) -> str:
        if not server_name and len(bot.servers) == 1:
            server = bot.servers.values()[0]
        elif server_name in bot.servers:
            server = bot.servers[server_name]
        else:
            return PeriodFilter.format(bot, period)
        campaign = utils.get_running_campaign(server)
        if campaign:
            return CampaignFilter.format(bot, campaign, server_name)
        else:
            return PeriodFilter.format(bot, period, server_name)


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
        return PeriodFilter.format(bot, server_name, period)


class StatsPagination(Pagination):
    def __init__(self, env: ReportEnv):
        super().__init__(env)
        self.pool = env.bot.pool
        self.log = env.bot.log

    def values(self, period: str, **kwargs) -> list[str]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if period in [None, 'all', 'day', 'week', 'month', 'year']:
                    cursor.execute('SELECT DISTINCT server_name FROM missions')
                else:
                    cursor.execute('SELECT DISTINCT server_name FROM campaigns WHERE name ILIKE %s', (period,))
                return [x[0] for x in cursor.fetchall()]
        except psycopg2.DatabaseError as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
