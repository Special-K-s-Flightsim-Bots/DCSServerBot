from core import utils, Plugin
from core.data.dataobject import DataObjectFactory, DataObject
from core.services.registry import ServiceRegistry
from core.utils import get_squadron
from core.data.member import Member
from core.data.const import Status
from dataclasses import dataclass, field

from services.bot import DCSServerBot, BotService
from services.servicebus import ServiceBus
from typing import TYPE_CHECKING, Optional, cast

if TYPE_CHECKING:
    from .player import CreditPlayer


@dataclass
@DataObjectFactory.register()
class Squadron(DataObject):
    name: str
    campaign_id: int
    players: list[Member] = field(compare=False, default_factory=list)
    _points: int = field(compare=False, default=-1)
    squadron_id: int = field(compare=False, init=False)
    bot: DCSServerBot = field(compare=False, init=False)
    plugin: Plugin = field(compare=False, init=False)
    config: dict = field(compare=False, init=False)

    def __post_init__(self):
        super().__post_init__()
        self.bot = ServiceRegistry.get(BotService).bot
        self.plugin = cast(Plugin, self.bot.cogs['CreditSystem'])
        self.config = self.plugin.get_config().get('squadron', {})
        squadron = get_squadron(self.node, name=self.name)

        if squadron:
            self.squadron_id = squadron['id']
        else:
            self.squadron_id = -1

    @property
    def points(self) -> int:
        if self._points == -1:
            with self.pool.connection() as conn:
                cursor = conn.execute("SELECT points FROM squadron_credits WHERE campaign_id = %s AND squadron_id = %s",
                                      (self.campaign_id, self.squadron_id))
                row = cursor.fetchone()
                if row:
                    self._points = row[0]
                else:
                    self.points = self.config.get('initial_points', 0)
        return self._points

    @points.setter
    def points(self, p: int) -> None:
        if 'max_points' in self.config and p > int(self.config['max_points']):
            self._points = int(self.config['max_points'])
        else:
            self._points = p

        # make sure we never go below 0
        if self._points < 0:
            self._points = 0

        # persist points
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO squadron_credits (campaign_id, squadron_id, points) 
                    VALUES (%s, %s, %s) 
                    ON CONFLICT (campaign_id, squadron_id) DO UPDATE SET points = EXCLUDED.points
                """, (self.campaign_id, self.squadron_id, self._points))

        # send points to all active DCS-servers
        bus = ServiceRegistry.get(ServiceBus)
        for server in bus.servers.values():
            if server.status in [Status.PAUSED, Status.RUNNING]:
                bus.loop.create_task(server.send_to_dcs({
                    'command': 'updateSquadronPoints',
                    'squadron': self.name,
                    'points': self._points
                }))

    def audit(self, event: str, points: int, remark: str, player: Optional["CreditPlayer"] = None):
        if points == 0:
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO squadron_credits_log (
                        campaign_id, 
                        event, 
                        squadron_id, 
                        old_points, 
                        new_points, 
                        player_ucid, 
                        remark
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (self.campaign_id, event, self.squadron_id, self._points - points, self._points,
                      player.ucid if player else None, remark))
