import discord
from dataclasses import dataclass
from core import Server, Channel
from typing import Optional, Union


@dataclass
class ServerProxy(Server):
    agent: str = None

    def __post_init__(self):
        super().__post_init__()
        with self.pool.connection() as conn:
            # read persisted messages for this server
            self.agent = conn.execute('SELECT agent_host FROM servers WHERE server_name = %s',
                                      (self.name, )).fetchone()[0]

    @property
    def is_remote(self) -> bool:
        return True

    @property
    def missions_dir(self) -> str:
        return ""  # TODO

    @property
    def settings(self) -> dict:
        return {}  # TODO DICT

    @property
    def options(self) -> dict:
        return {}  # TODO DICT

    async def get_current_mission_file(self) -> Optional[str]:
        data = await self.sendtoDCSSync({
            "command": "intercom",
            "object": "Server",
            "method": "get_current_mission_file"
        })
        return data["return"]

    def sendtoDCS(self, message: dict):
        self.bot.sendtoBot(message, agent=self.agent)

    # TODO
    def rename(self, new_name: str, update_settings: bool = False) -> None:
        self.bot.sendtoBot({
            "command": "intercom",
            "object": "Server",
            "method": "rename",
            "params": {
                "new_name": new_name,
                "update_settings": update_settings
            }
        }, agent=self.agent)

    async def startup(self) -> None:
        self.bot.sendtoBot({"command": "intercom", "object": "Server", "method": "startup"}, agent=self.agent)

    async def shutdown(self, force: bool = False) -> None:
        self.bot.sendtoBot({
            "command": "intercom",
            "object": "Server",
            "method": "shutdown",
            "params": {
                "force": force
            }
        }, agent=self.agent)

    async def setEmbed(self, embed_name: str, embed: discord.Embed, file: Optional[discord.File] = None,
                       channel_id: Optional[Union[Channel, int]] = Channel.STATUS) -> None:
        async with self._lock:
            message = None
            channel = self.bot.get_channel(channel_id) if isinstance(channel_id, int) else self.get_channel(channel_id)
            if embed_name in self.embeds:
                if isinstance(self.embeds[embed_name],  discord.Message):
                    message = self.embeds[embed_name]
                else:
                    try:
                        message = await channel.fetch_message(self.embeds[embed_name])
                        self.embeds[embed_name] = message
                    except discord.errors.NotFound:
                        message = None
                    except discord.errors.DiscordException as ex:
                        self.log.warning(f"Discord error during setEmbed({embed_name}): " + str(ex))
                        return
            if message:
                try:
                    if not file:
                        await message.edit(embed=embed)
                    else:
                        await message.edit(embed=embed, attachments=[file])
                except discord.errors.NotFound:
                    message = None
                except Exception as ex:
                    self.log.warning(f"Error during update of embed {embed_name}: " + str(ex))
                    return
            if not message:
                message = await channel.send(embed=embed, file=file)
                self.embeds[embed_name] = message
                with self.pool.connection() as conn:
                    with conn.transaction():
                        conn.execute("""
                            INSERT INTO message_persistence (server_name, embed_name, embed) 
                            VALUES (%s, %s, %s) 
                            ON CONFLICT (server_name, embed_name) 
                            DO UPDATE SET embed=excluded.embed
                        """, (self.name, embed_name, message.id))
