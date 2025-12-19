from __future__ import annotations
import discord
from core import Coalition
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server
    from services.bot import DCSServerBot

__all__ = [
    "get_sides"
]


def get_sides(bot: DCSServerBot, ctx: discord.Interaction | discord.Message, server: Server) -> list[Coalition]:
    if 'coalitions' not in server.locals:
        return [Coalition.BLUE, Coalition.RED]

    if isinstance(ctx, discord.Interaction):
        user = ctx.user
    else:
        user = ctx.author
    channel = ctx.channel

    da_roles = [bot.get_role(x) for x in bot.roles['DCS Admin']]
    gm_roles = [bot.get_role(x) for x in bot.roles['GameMaster']]
    blue_role = bot.get_role(server.locals['coalitions']['blue_role'])
    red_role = bot.get_role(server.locals['coalitions']['red_role'])
    everyone = discord.utils.get(channel.guild.roles, name="@everyone")

    # check which coalition-specific data can be displayed in the questioned channel by that user
    for role in user.roles:
        if (role in gm_roles or role in da_roles) and \
                not channel.overwrites_for(everyone).read_messages and \
                not channel.overwrites_for(blue_role).read_messages and \
                not channel.overwrites_for(red_role).read_messages:
            return [Coalition.BLUE, Coalition.RED]
        elif (role in gm_roles or role in da_roles or role == blue_role) \
                and channel.overwrites_for(blue_role).send_messages and \
                not channel.overwrites_for(red_role).read_messages:
            return [Coalition.BLUE]
        elif (role in gm_roles or role in da_roles or role == red_role) \
                and channel.overwrites_for(red_role).send_messages and \
                not channel.overwrites_for(blue_role).read_messages:
            return [Coalition.RED]
    return []
