from core import EventListener, Server, event, chat_command, Player


class SampleEventListener(EventListener):
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
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        self.log.debug(f"I've received a registration event from server {server.name}!")

    @event(name="sample")
    async def sample(self, server: Server, data: dict):
        self.log.debug("I've received the sample event!")
        # this is a synchronous call, so we just return the data received
        return data

    @chat_command(name="sample", roles=['DCS Admin'], help="A sample command")
    async def sample(self, server: Server, player: Player, params: list[str]):
        player.sendChatMessage("This is a sample command!")
