import psycopg2
from contextlib import closing
from core import Player, DataObjectFactory, utils
from dataclasses import field, dataclass


@dataclass
@DataObjectFactory.register("Player")
class CreditPlayer(Player):
    _points: int = field(compare=False, default=-1)
    deposit: int = field(compare=False, default=0)

    def __post_init__(self):
        super().__post_init__()
        if not self.active:
            return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # load credit points
                campaign_id, _ = utils.get_running_campaign(self.server)
                if not campaign_id:
                    return
                cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                               (campaign_id, self.ucid))
                if cursor.rowcount == 1:
                    self._points = cursor.fetchone()[0]
                    self.server.sendtoDCS({
                        'command': 'updateUserPoints',
                        'ucid': self.ucid,
                        'points': self._points,
                    })
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @property
    def points(self) -> int:
        return self._points

    @points.setter
    def points(self, p: int) -> None:
        plugin = self.bot.cogs['CreditSystemMaster' if 'CreditSystemMaster' in self.bot.cogs else 'CreditSystemAgent']
        config = plugin.get_config(self.server)
        if not config:
            return
        if 'max_points' in config and p > config['max_points']:
            self._points = config['max_points']
        elif p < 0:
            self._points = 0
        else:
            self._points = p
        campaign_id, _ = utils.get_running_campaign(self.server)
        if not campaign_id:
            return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('INSERT INTO credits (campaign_id, player_ucid, points) VALUES (%s, %s, '
                               '%s) ON CONFLICT (campaign_id, player_ucid) DO UPDATE SET points = EXCLUDED.points',
                               (campaign_id, self.ucid, self._points))
            conn.commit()
            self.server.sendtoDCS({
                'command': 'updateUserPoints',
                'ucid': self.ucid,
                'points': self._points,
            })
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def audit(self, event: str, old_points: int, remark: str):
        campaign_id, _ = utils.get_running_campaign(self.server)
        if not campaign_id:
            return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, '
                               'remark) VALUES (%s, %s, %s, %s, %s, %s)',
                               (campaign_id, event, self.ucid, old_points, self._points, remark))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
