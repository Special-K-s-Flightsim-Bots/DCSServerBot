import discord

from . import Sink


class RemoteSink(Sink):
    async def play(self, file: str) -> None:
        pass

    async def skip(self) -> None:
        pass

    async def stop(self) -> None:

    def render(self) -> discord.Embed:
        pass

    def edit(self) -> Modal:
        pass
