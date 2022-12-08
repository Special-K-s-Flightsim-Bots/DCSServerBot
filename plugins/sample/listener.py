from core import EventListener, Server


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

    async def registerDCSServer(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        self.log.debug(f"I've received a registration event from server {server.name}!")

    async def sample(self, data):
        self.log.debug("I've received the sample event!")
        # this is a synchronous call, so we just return the data received
        return data
