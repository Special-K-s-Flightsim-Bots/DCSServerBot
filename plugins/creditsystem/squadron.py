from core import Server, utils
from core.data.dataobject import DataObjectFactory, DataObject
from core.services.registry import ServiceRegistry
from core.utils import get_squadron
from core.data.member import Member
from core.data.const import Status
from dataclasses import dataclass, field
from services.servicebus import ServiceBus
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .player import CreditPlayer


@dataclass
@DataObjectFactory.register()
class Squadron(DataObject):
    name: str
    server: Server
    players: list[Member] = field(compare=False, default_factory=list)
    _points: int = field(compare=False, default=-1)
    squadron_id: int = field(compare=False, init=False)

    def __post_init__(self):
        super().__post_init__()
        squadron = get_squadron(self.node, name=self.name)
        if squadron:
            self.squadron_id = squadron['id']
        else:
            self.squadron_id = -1

    @property
    def points(self) -> int:
        if self._points == -1:
            with self.pool.connection() as conn:
                campaign_id, _ = utils.get_running_campaign(self.node, self.server)
                if not campaign_id:
                    return -1
                cursor = conn.execute("SELECT points FROM squadron_credits WHERE campaign_id = %s AND squadron_id = %s",
                                      (campaign_id, self.squadron_id))
                row = cursor.fetchone()
                if row:
                    self._points = row[0]
                else:
                    self.points = 0
        return self._points

    @points.setter
    def points(self, p: int) -> None:
        self._points = p

        campaign_id, _ = utils.get_running_campaign(self.node, self.server)
        if campaign_id:
            # persist points
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute("""
                        INSERT INTO squadron_credits (campaign_id, squadron_id, points) 
                        VALUES (%s, %s, %s) 
                        ON CONFLICT (squadron_id) DO UPDATE SET points = EXCLUDED.points
                    """, (campaign_id, self.squadron_id, self._points))

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
        campaign_id, _ = utils.get_running_campaign(self.node, self.server)
        if not campaign_id:
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
                """, (campaign_id, event, self.squadron_id, self._points - points, self._points,
                      player.ucid or None, remark))
