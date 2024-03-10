from contextlib import closing

from core import Player, DataObjectFactory, utils, Plugin
from dataclasses import field, dataclass
from typing import cast


@dataclass
@DataObjectFactory.register(Player)
class CreditPlayer(Player):
    _points: int = field(compare=False, default=-1)
    deposit: int = field(compare=False, default=0)

    @property
    def points(self) -> int:
        if self._points == -1:
            with self.pool.connection() as conn:
                with closing(conn.cursor()) as cursor:
                    # load credit points
                    campaign_id, _ = utils.get_running_campaign(self.bot, self.server)
                    if not campaign_id:
                        return -1
                    cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                                   (campaign_id, self.ucid))
                    if cursor.rowcount == 1:
                        self._points = cursor.fetchone()[0]
                    else:
                        self.log.debug(
                            f'CreditPlayer: No entry found in credits table for player {self.name}({self.ucid})')
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
        if campaign_id:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute("""
                        INSERT INTO credits (campaign_id, player_ucid, points) 
                        VALUES (%s, %s, %s) 
                        ON CONFLICT (campaign_id, player_ucid) DO UPDATE SET points = EXCLUDED.points
                    """, (campaign_id, self.ucid, self._points))
        else:
            self.log.debug("No campaign active, player points will vanish after a bot restart.")
        # sending points to DCS
        self.server.send_to_dcs({
            'command': 'updateUserPoints',
            'ucid': self.ucid,
            'points': self._points
        })

    def audit(self, event: str, old_points: int, remark: str):
        if old_points == self.points:
            return
        campaign_id, _ = utils.get_running_campaign(self.bot, self.server)
        if not campaign_id:
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (campaign_id, event, self.ucid, old_points, self._points, remark))
