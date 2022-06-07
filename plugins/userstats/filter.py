import psycopg2
import string
from abc import ABC, abstractmethod
from contextlib import closing
from core import DCSServerBot
from typing import Any


class StatisticsFilter(ABC):

    @abstractmethod
    def filter(self) -> str:
        pass

    @abstractmethod
    def format(self) -> str:
        pass

    @staticmethod
    def detect(bot: DCSServerBot, period: str) -> Any:
        if period is None:
            return None
        elif period in ['day', 'week', 'month', 'year']:
            return PeriodFilter(period)
        conn = bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT name FROM campaigns WHERE name = %s', (period, ))
                if cursor.rowcount > 0:
                    return CampaignFilter(period)
                else:
                    return None
        except psycopg2.DatabaseError as error:
            bot.log.exception(error)
        finally:
            bot.pool.putconn(conn)


class PeriodFilter(StatisticsFilter):
    def __init__(self, period: str):
        self.period = period

    def format(self) -> str:
        if self.period == 'day':
            return 'Daily'
        else:
            return string.capwords(self.period) + 'ly'

    def filter(self) -> str:
        return f'DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {self.period}\')'


class CampaignFilter(StatisticsFilter):
    def __init__(self, campaign: str):
        self.campaign = campaign

    def filter(self) -> str:
        return f"tsrange(s.hop_on, s.hop_off) && (SELECT tsrange(start, stop) FROM campaigns " \
               f"WHERE name='{self.campaign}')"

    def format(self) -> str:
        return f'Campaign "{self.campaign}"'
