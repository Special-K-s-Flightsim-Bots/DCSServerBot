from __future__ import annotations
import discord
from core import Coalition
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from core import Server
    from services import DCSServerBot

__all__ = [
    "get_sides"
]


def get_sides(bot: DCSServerBot, ctx: Union[discord.Interaction, discord.Message], server: Server) -> list[str]:
    if isinstance(ctx, discord.Interaction):
        user = ctx.user
    else:
        user = ctx.author
    channel = ctx.channel

    sides = []
    if 'coalitions' in server.locals:
        # TODO: cache that
        roles = {
            "All Blue": set(),
            "All Red": set(),
            "everyone": discord.Role,
            "DCS": discord.Role,
            "Blue": discord.Role,
            "Red": discord.Role,
        }
        da_roles = bot.roles['DCS Admin']
        gm_roles = bot.roles['GameMaster']
        # find all roles that are allowed to see red and blue
        for role in channel.guild.roles:
            if role.name == server.locals['coalitions']['blue_role']:
                roles['Blue'] = role
                roles['All Blue'].add(role.name)
            elif role.name == server.locals['coalitions']['red_role']:
                roles['Red'] = role
                roles['All Red'].add(role.name)
            elif role.name == bot.roles['DCS']:
                roles['DCS'] = role
            elif role.name == '@everyone':
                roles['everyone'] = role
            elif role.name in da_roles:
                roles['All Blue'].add(role.name)
                roles['All Red'].add(role.name)
            elif role.name in gm_roles:
                roles['All Blue'].add(role.name)
                roles['All Red'].add(role.name)
        # check, which coalition specific data can be displayed in the questioned channel by that user
        for role in user.roles:
            if (role.name in gm_roles or role.name in da_roles) and \
                    not channel.overwrites_for(roles['everyone']).read_messages and \
                    not channel.overwrites_for(roles['DCS']).read_messages and \
                    not channel.overwrites_for(roles['Blue']).read_messages and \
                    not channel.overwrites_for(roles['Red']).read_messages:
                sides = [Coalition.BLUE, Coalition.RED]
                break
            elif role.name in roles['All Blue'] \
                    and channel.overwrites_for(roles['Blue']).send_messages and \
                    not channel.overwrites_for(roles['Red']).read_messages:
                sides = [Coalition.BLUE]
                break
            elif role.name in roles['All Red'] \
                    and channel.overwrites_for(roles['Red']).send_messages and \
                    not channel.overwrites_for(roles['Blue']).read_messages:
                sides = [Coalition.RED]
                break
    else:
        sides = [Coalition.BLUE, Coalition.RED]
    return sides
