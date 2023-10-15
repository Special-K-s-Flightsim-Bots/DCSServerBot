from __future__ import annotations
import discord
from contextlib import closing
from typing import TYPE_CHECKING, Tuple, Any, Optional
from discord import app_commands
from psycopg.rows import dict_row

if TYPE_CHECKING:
    from core import Server
    from services import DCSServerBot

__all__ = [
    "get_running_campaign",
    "get_all_campaigns",
    "get_campaign",
    "campaign_autocomplete"
]


def get_running_campaign(bot: DCSServerBot, server: Optional[Server] = None) -> Tuple[Any, Any]:
    with bot.pool.connection() as conn:
        with closing(conn.cursor()) as cursor:
            if server:
                cursor.execute("""
                    SELECT id, name FROM campaigns c, campaigns_servers s 
                    WHERE c.id = s.campaign_id AND s.server_name = %s 
                    AND NOW() BETWEEN c.start AND COALESCE(c.stop, NOW())
                """, (server.name,))
            else:
                cursor.execute("""
                    SELECT id, name FROM campaigns
                    WHERE NOW() BETWEEN start AND COALESCE(stop, NOW())
                """)
            if cursor.rowcount == 1:
                row = cursor.fetchone()
                return row[0], row[1]
            else:
                return None, None


def get_all_campaigns(self) -> list[str]:
    with self.pool.connection() as conn:
        return [x[0] for x in conn.execute('SELECT name FROM campaigns').fetchall()]


def get_campaign(self, campaign: str) -> dict:
    with self.pool.connection() as conn:
        with closing(conn.cursor(row_factory=dict_row)) as cursor:
            return cursor.execute("""
                SELECT id, name, description, start, stop 
                FROM campaigns 
                WHERE name = %s 
            """, (campaign, )).fetchone()


async def campaign_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    choices: list[app_commands.Choice[str]] = list()
    _, name = get_running_campaign(interaction.client)
    if name:
        choices.append(app_commands.Choice(name=name, value=name))
    choices.extend([
        app_commands.Choice(name=x, value=x)
        for x in get_all_campaigns(interaction.client)
        if x != name and current.casefold() in x.casefold()
    ])
    return choices[:25]
