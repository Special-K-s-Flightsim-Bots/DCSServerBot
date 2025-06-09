import discord

from core import utils
from discord import TextStyle
from discord.ui import Modal, TextInput
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import UserStatistics


class SquadronModal(Modal):
    # noinspection PyTypeChecker
    description = TextInput(label="Enter a description for this squadron:", style=TextStyle.long, required=True)
    # noinspection PyTypeChecker
    image_url = TextInput(label="Squadron Image (URL):", style=TextStyle.short, required=False)

    def __init__(self, plugin: "UserStatistics", name: str, *, locked: bool = False, role: Optional[discord.Role] = None,
                 description: Optional[str] = None, image_url: Optional[str] = None,
                 channel: Optional[discord.TextChannel] = None):
        super().__init__(title=f"Description for Squadron {name}")
        self.plugin = plugin
        self.name = name
        self.locked = locked
        self.role = role
        self.description.default = description
        self.image_url.default = image_url
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)
        async with interaction.client.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO squadrons (name, description, role, image_url, channel, locked) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE
                    SET description = excluded.description, role = excluded.role, image_url = excluded.image_url, 
                        channel = excluded.channel, locked = excluded.locked
                """, (self.name, self.description.value, self.role.id if self.role else None, self.image_url.value,
                      self.channel.id if self.channel else None, self.locked))
                cursor = await conn.execute("SELECT id FROM squadrons WHERE name = %s", (self.name,))
                squadron_id = (await cursor.fetchone())[0]
                if self.role:
                    # might be a role change, so wipe the squadron first
                    await conn.execute("""
                        DELETE FROM squadron_members WHERE squadron_id = %s
                    """, (squadron_id, ))
                    for member in self.role.members:
                        ucid = await interaction.client.get_ucid_by_member(member, verified=True)
                        if ucid:
                            await conn.execute("""
                                INSERT INTO squadron_members VALUES (%s, %s)
                                ON CONFLICT (squadron_id, player_ucid) DO NOTHING
                            """, (squadron_id, ucid))
        if self.plugin.get_config().get('squadrons', {}).get('persist_list', False):
            await self.plugin.persist_squadron_list(squadron_id)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(f"Squadron {self.name} created/updated.", ephemeral=ephemeral)

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(f"Error while creating squadron: {error}", ephemeral=True)
