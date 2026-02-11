from __future__ import annotations
import discord
from contextlib import closing
from typing import TYPE_CHECKING, Any
from discord import app_commands
from psycopg.rows import dict_row

if TYPE_CHECKING:
    from core import Server, Node

__all__ = [
    "get_running_campaign",
    "get_all_campaigns",
    "get_campaign",
    "campaign_autocomplete"
]


def get_running_campaign(node: Node, server: Server | None = None) -> tuple[Any, Any]:
    with node.pool.connection() as conn:
        with closing(conn.cursor()) as cursor:
            if server:
                cursor.execute("""
                    SELECT id, name FROM campaigns c, campaigns_servers s 
                    WHERE c.id = s.campaign_id AND s.server_name = %s 
                    AND (now() AT TIME ZONE 'utc') BETWEEN c.start AND COALESCE(c.stop, now() AT TIME ZONE 'utc')
                """, (server.name,))
            else:
                cursor.execute("""
                    SELECT id, name FROM campaigns
                    WHERE (now() AT TIME ZONE 'utc') BETWEEN start AND COALESCE(stop, now() AT TIME ZONE 'utc')
                """)
            if cursor.rowcount == 1:
                row = cursor.fetchone()
                return row[0], row[1]
            else:
                return None, None


def get_all_campaigns(node: Node) -> list[str]:
    with node.pool.connection() as conn:
        return [x[0] for x in conn.execute('SELECT name FROM campaigns')]


async def get_campaign(node: Node, campaign: str) -> dict:
    async with node.apool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("""
                SELECT id, name, description, image_url, 
                       start AT TIME ZONE 'UTC' AS start, stop AT TIME ZONE 'UTC' AS stop 
                FROM campaigns 
                WHERE name = %s 
            """, (campaign, ))
            return await cursor.fetchone()


async def campaign_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        choices: list[app_commands.Choice[str]] = list()
        _, name = get_running_campaign(interaction.client.node)
        if name:
            choices.append(app_commands.Choice(name=name, value=name))
        choices.extend([
            app_commands.Choice(name=x, value=x)
            for x in get_all_campaigns(interaction.client.node)
            if x != name and current.casefold() in x.casefold()
        ])
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []
