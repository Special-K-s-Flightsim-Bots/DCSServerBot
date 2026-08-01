from contextlib import closing

from core import Player, DataObjectFactory, utils, Plugin
from dataclasses import field, dataclass
from typing import cast
from typing_extensions import override

from .squadron import Squadron


@dataclass
@DataObjectFactory.register(Player)
class CreditPlayer(Player):
    _points: int = field(compare=False, default=-1)
    deposit: int = field(compare=False, default=0)
    plugin: Plugin = field(compare=False, init=False)
    config: dict = field(compare=False, init=False)
    squadron: Squadron | None = field(compare=False, init=False)

    def __post_init__(self):
        super().__post_init__()
        self.plugin = cast(Plugin, self.bot.cogs['CreditSystem'])
        self.config = self.plugin.get_config(self.server)

    @override
    async def prep(self) -> Player:
        await super().prep()
        campaign_id, _ = await utils.get_running_campaign_async(self.node, self.server)
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT s.name FROM squadrons s JOIN squadron_members sm 
                ON s.id = sm.squadron_id AND sm.player_ucid = %s
            """, (self.ucid,))
            # a squadron needs to be unambiguous to be linked to a player
            if cursor.rowcount == 1:
                row = await cursor.fetchone()
                if not campaign_id:
                    self.squadron = None
                else:
                    self.squadron = DataObjectFactory().new(Squadron, node=self.node, name=row[0],
                                                              campaign_id=campaign_id)
                    await self.squadron.prep()
            else:
                self.squadron = None

        # load credit points
        _ = await self.get_points()
        return self

    async def get_points(self) -> int:
        # load credit points
        campaign_id, _ = await utils.get_running_campaign_async(self.node, self.server)
        if not campaign_id:
            self._points = -1
            return -1
        async with self.apool.connection() as conn:
            cursor = await conn.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                                        (campaign_id, self.ucid))
            if cursor.rowcount == 1:
                row = await cursor.fetchone()
                self._points = row[0]
            else:
                self.log.debug(
                    f'CreditPlayer: No entry found in credits table for player {self.name}({self.ucid})')
                self._points = 0
        return self._points

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
                        self._points = 0
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

    async def audit(self, event: str, old_points: int, remark: str):
        if old_points == self.points:
            return
        campaign_id, _ = await utils.get_running_campaign_async(self.node, self.server)
        if not campaign_id:
            return
        async with self.apool.connection() as conn:
            await conn.execute("""
                INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (campaign_id, event, self.ucid, old_points, self._points, remark))

        if self.squadron and old_points < self.points:
            if self.config.get('squadron_credits', False):
                await self.squadron.audit(event, self._points - old_points, remark, self)
