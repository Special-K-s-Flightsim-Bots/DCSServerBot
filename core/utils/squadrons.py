import discord

from core.data.node import Node
from discord import app_commands
from psycopg.rows import dict_row
from typing import Optional

from .discord import get_interaction_param, check_roles

_all_ = [
    'squadron_autocomplete',
    'get_squadron',
    'squadron_users_autocomplete',
    'get_squadron_admins',
    'squadron_role_check'
]


async def squadron_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    async with interaction.client.apool.connection() as conn:
        cursor = await conn.execute("SELECT id, name FROM squadrons WHERE name ILIKE %s", ('%' + current + '%', ))
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=row[1], value=row[0])
            async for row in cursor
        ]
        return choices[:25]


async def squadron_autocomplete_admin(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    async with interaction.client.apool.connection() as conn:
        # DCS Admins can access any squadron
        if not check_roles(interaction.client.roles["DCS Admin"], interaction.user):
            ucid = await interaction.client.get_ucid_by_member(interaction.user)
            sql = f"""
                SELECT DISTINCT s.id, s.name
                FROM squadrons s JOIN squadron_members m ON s.id = m.squadron_id
                WHERE m.player_ucid = '{ucid}' AND m.admin IS TRUE
                AND s.name ILIKE %s
                ORDER BY s.name LIMIT 25
                  """
        else:
            sql = "SELECT id, name FROM squadrons WHERE name ILIKE %s ORDER BY name LIMIT 25"
        cursor = await conn.execute(sql, ('%' + current + '%', ))
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=row[1], value=row[0])
            async for row in cursor
        ]
        return choices


def get_squadron(node: Node, *, name: Optional[str] = None, squadron_id: Optional[int] = None) -> Optional[dict]:
    sql = "SELECT * FROM squadrons"
    if name:
        sql += " WHERE name = %(name)s"
    elif squadron_id:
        sql += " WHERE id = %(squadron_id)s"
    else:
        return None
    with node.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, {"name": name, "squadron_id": squadron_id})
            return cursor.fetchone()


async def squadron_users_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        squadron_id = get_interaction_param(interaction, 'squadron')
        if not squadron_id:
            return []
        async with interaction.client.apool.connection() as conn:
            choices: list[app_commands.Choice[str]] = [
                app_commands.Choice(name=row[0], value=row[1])
                async for row in await conn.execute("""
                    SELECT p.name, s.player_ucid FROM squadron_members s, players p
                    WHERE s.player_ucid = p.ucid AND s.squadron_id = %s 
                    AND p.name ILIKE %s 
                """, (squadron_id, f'%{current}%'))
            ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


def get_squadron_admins(node: Node, squadron_id: int) -> list[int]:
    with node.pool.connection() as conn:
        return [x[0] for x in conn.execute("""
            SELECT p.discord_id FROM players p JOIN squadron_members s 
            ON p.ucid = s.player_ucid AND p.manual IS TRUE
            AND s.squadron_id = %s AND s.admin IS TRUE 
        """, (squadron_id,)).fetchall()]


def squadron_role_check():
    def predicate(interaction: discord.Interaction) -> bool:
        squadron_id = get_interaction_param(interaction, 'squadron')
        if isinstance(squadron_id, int):
            admins = get_squadron_admins(interaction.client.node, squadron_id)
            if interaction.user.id in admins:
                return True
        return check_roles(interaction.client.roles["DCS Admin"], interaction.user)

    return app_commands.check(predicate)
