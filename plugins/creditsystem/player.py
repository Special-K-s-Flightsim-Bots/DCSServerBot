from contextlib import closing
from core import Player, DataObjectFactory, utils, Plugin
from dataclasses import field, dataclass
from typing import cast, Optional

from .squadron import Squadron


@dataclass
@DataObjectFactory.register(Player)
class CreditPlayer(Player):
    _points: int = field(compare=False, default=-1)
    deposit: int = field(compare=False, default=0)
    plugin: Plugin = field(compare=False, init=False)
    config: dict = field(compare=False, init=False)
    squadron: Optional[Squadron] = field(compare=False, init=False)

    def __post_init__(self):
        super().__post_init__()
        self.plugin = cast(Plugin, self.bot.cogs['CreditSystem'])
        self.config = self.plugin.get_config(self.server)
        with self.pool.connection() as conn:
            row = conn.execute("""
                SELECT s.name FROM squadrons s JOIN squadron_members sm 
                ON s.id = sm.squadron_id AND sm.player_ucid = %s
            """, (self.ucid,)).fetchone()
            if row:
                campaign_id, _ = utils.get_running_campaign(self.node, self.server)
                self.squadron = DataObjectFactory().new(Squadron, node=self.node, name=row[0], campaign_id=campaign_id)
            else:
                self.squadron = None

    @property
    def points(self) -> int:
        if self._points == -1:
            with self.pool.connection() as conn:
                with closing(conn.cursor()) as cursor:
                    # load credit points
                    campaign_id, _ = utils.get_running_campaign(self.node, self.server)
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
        if p == self._points:
            return
        old_points = self.points

        if 'max_points' in self.config and p > int(self.config['max_points']):
            self._points = int(self.config['max_points'])
        else:
            self._points = p

        # make sure we never go below 0
        if self._points < 0:
            self._points = 0

        campaign_id, _ = utils.get_running_campaign(self.node, self.server)
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

        if self.squadron and self.sub_slot == 0 and old_points < self._points:
            if self.config.get('squadron_credits', False):
                self.squadron.points += self._points - old_points

        # sending points to DCS
        self.bot.loop.create_task(self.server.send_to_dcs({
            'command': 'updateUserPoints',
            'ucid': self.ucid,
            'points': self._points
        }))

    def audit(self, event: str, old_points: int, remark: str):
        if old_points == self.points:
            return
        campaign_id, _ = utils.get_running_campaign(self.node, self.server)
        if not campaign_id:
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (campaign_id, event, self.ucid, old_points, self._points, remark))

        if self.squadron and old_points < self.points:
            if self.config.get('squadron_credits', False):
                self.squadron.audit(event, self._points - old_points, remark, self)
