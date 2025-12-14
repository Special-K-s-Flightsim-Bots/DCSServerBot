
from core import Server, Player, Coalition
from plugins.voting.base import VotableItem


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
            await self.server.bus.ban(self.player.ucid, banned_by='Other players', reason=f"Annoying people on the server",
                                      days=duration)
            message = f"Player {self.player.name} banned for {duration} days."
        await self.server.sendChatMessage(Coalition.ALL, message)
        await self.server.sendPopupMessage(Coalition.ALL, message)
