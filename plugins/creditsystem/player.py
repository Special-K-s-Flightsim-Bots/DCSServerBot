import psycopg2
from contextlib import closing
from core import Player, DataObjectFactory
from dataclasses import field, dataclass


@dataclass
@DataObjectFactory.register("Player")
class CreditPlayer(Player):
    _points: int = field(compare=False, default=-1)
    deposit: int = field(compare=False, default=0)

    def __post_init__(self):
        super().__post_init__()
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # load credit points
                cursor.execute('SELECT points FROM credits WHERE campaign_id = (SELECT id FROM '
                               'campaigns WHERE server_name = %s AND NOW() BETWEEN start AND COALESCE(stop, '
                               'NOW())) AND player_ucid = %s', (self.server.name, self.ucid))
                if cursor.rowcount == 1:
                    self._points = cursor.fetchone()[0]
                    self.server.sendtoDCS({
                        'command': 'updateUserPoints',
                        'ucid': self.ucid,
                        'points': self._points,
                    })
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @property
    def points(self) -> int:
        return self._points

    @points.setter
    def points(self, p: int) -> None:
        plugin = self.bot.cogs['CreditSystemMaster' if 'CreditSystemMaster' in self.bot.cogs else 'CreditSystemAgent']
        config = plugin.get_config(self.server)
        if 'max_points' in config and p > config['max_points']:
            self._points = config['max_points']
        elif p < 0:
            self._points = 0
        else:
            self._points = p
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('INSERT INTO credits (campaign_id, player_ucid, points) SELECT id, %s, %s FROM '
                               'campaigns WHERE server_name = %s AND NOW() BETWEEN start AND COALESCE(stop, '
                               'NOW()) ON CONFLICT (campaign_id, player_ucid) DO UPDATE SET points = EXCLUDED.points',
                               (self.ucid, self._points, self.server.name))
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
