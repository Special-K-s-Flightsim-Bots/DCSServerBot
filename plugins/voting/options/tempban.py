
from core import Server, Player, Coalition
from datetime import datetime, timezone
from plugins.voting.base import VotableItem


def _ban_targets(server: Server, servers: dict) -> list[str]:
    """The servers a ban raised on *server* covers.

    Every server whose ``managed_by`` names one of *server*'s own; an empty list means *server*
    declares no ``managed_by`` and the ban is bot-wide.
    """
    managed_by = server.locals.get('managed_by') or []
    if not managed_by:
        return []
    return [name for name, srv in servers.items()
            if (srv.locals.get('managed_by') or [])
            and not set(srv.locals['managed_by']).isdisjoint(managed_by)]


class Tempban(VotableItem):

    def __init__(self, server: Server, config: dict, params: list[str] | None = None):
        super().__init__('mission', server, config, params)
        if not params or not len(params):
            raise TypeError("You have to provide a player name to tempban!")
        self.player: Player = server.get_player(name=' '.join(params))
        if not self.player:
            raise ValueError('Player "{}" not found.'.format(' '.join(params)))

    def __repr__(self) -> str:
        return f"Vote to temp ban player {self.player.name}"

    async def print(self) -> str:
        return (f"You can now vote to temporary ban player {self.player.name} for {self.config.get('duration', 3)} "
                f"days because of misbehaviour.")

    async def get_choices(self) -> list[str]:
        return [f"Don't ban {self.player.name}", f"Ban {self.player.name}"]

    async def execute(self, winner: str):
        if winner.startswith("Don't"):
            message = f"Player {self.player.name} not banned."
        else:
            duration = self.config.get('duration', 3)
            stamp = datetime.now(tz=timezone.utc)
            for target in _ban_targets(self.server, self.server.bus.servers) or [None]:
                await self.server.bus.ban(self.player.ucid, banned_by='Other players',
                                          reason=f"Annoying people on the server", days=duration,
                                          server_name=target, banned_at=stamp)
            message = f"Player {self.player.name} banned for {duration} days."
        await self.server.sendChatMessage(Coalition.ALL, message)
        await self.server.sendPopupMessage(Coalition.ALL, message)
