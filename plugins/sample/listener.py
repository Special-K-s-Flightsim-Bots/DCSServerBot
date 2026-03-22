from core import EventListener, Server, event, chat_command, Player
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Sample


class SampleEventListener(EventListener["Sample"]):
    """
    A class where your DCS events will be handled.

    Methods
    -------
    registerDCSServer(data)
        Called on registration of any DCS server.

    sample(data)
        Called whenever ".sample" is called in discord (see commands.py).
    """

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, _data: dict) -> None:
        self.log.debug(f"I've received a registration event from server {server.name}!")

    @event(name="sample")
    async def sample(self, _server: Server, data: dict):
        self.log.debug("I've received the sample event!")
        # this is a synchronous call, so we just return the data received
        return data

    @chat_command(name="sample", roles=['DCS Admin'], help="A sample command")
    async def sample(self, _server: Server, player: Player, _params: list[str]):
        await player.sendChatMessage("This is a sample command!")
