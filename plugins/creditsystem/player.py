from contextlib import closing

from core import Player, DataObjectFactory, utils, Plugin
from dataclasses import field, dataclass
from typing import cast


@dataclass
@DataObjectFactory.register("Player")
class CreditPlayer(Player):
    _points: int = field(compare=False, default=-1)
    deposit: int = field(compare=False, default=0)

    def __post_init__(self):
        super().__post_init__()
        if not self.active:
            return
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                # load credit points
                campaign_id, _ = utils.get_running_campaign(self.bot, self.server)
                if not campaign_id:
                    return
                cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                               (campaign_id, self.ucid))
                if cursor.rowcount == 1:
                    self._points = cursor.fetchone()[0]
                    self.server.send_to_dcs({
                        'command': 'updateUserPoints',
                        'ucid': self.ucid,
                        'points': self._points
                    })
                else:
                    self.log.debug(f'CreditPlayer: No entry found in credits table for player {self.name}({self.ucid})')

    @property
    def points(self) -> int:
        return self._points

    @points.setter
    def points(self, p: int) -> None:
        plugin = cast(Plugin, self.bot.cogs['CreditSystem'])
        config = plugin.get_config(self.server)
        if not config:
            self._points = p
            return
        if 'max_points' in config and p > int(config['max_points']):
            self._points = int(config['max_points'])
        elif p < 0:
            self._points = 0
        else:
            self._points = p
        campaign_id, _ = utils.get_running_campaign(self.bot, self.server)
        if not campaign_id:
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO credits (campaign_id, player_ucid, points) 
                    VALUES (%s, %s, %s) 
                    ON CONFLICT (campaign_id, player_ucid) DO UPDATE SET points = EXCLUDED.points
                """, (campaign_id, self.ucid, self._points))
                self.server.send_to_dcs({
                    'command': 'updateUserPoints',
                    'ucid': self.ucid,
                    'points': self._points
                })

    def audit(self, event: str, old_points: int, remark: str):
        campaign_id, _ = utils.get_running_campaign(self.bot, self.server)
        if not campaign_id:
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (campaign_id, event, self.ucid, old_points, self._points, remark))
