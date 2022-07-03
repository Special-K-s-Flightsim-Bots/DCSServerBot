from __future__ import annotations
import discord
from core import Coalition
from typing import TYPE_CHECKING
from . import config

if TYPE_CHECKING:
    from core import Server


def get_sides(message: discord.Message, server: Server) -> list[str]:
    sides = []
    if config.getboolean(server.installation, 'COALITIONS'):
        # TODO: cache that
        roles = {
            "All Blue": set(),
            "All Red": set(),
            "everyone": discord.Role,
            "DCS": discord.Role,
            "Blue": discord.Role,
            "Red": discord.Role,
        }
        da_roles = [x.strip() for x in config['ROLES']['DCS Admin'].split(',')]
        gm_roles = [x.strip() for x in config['ROLES']['GameMaster'].split(',')]
        # find all roles that are allowed to see red and blue
        for role in message.channel.guild.roles:
            if role.name == config['ROLES']['Coalition Blue']:
                roles['Blue'] = role
                roles['All Blue'].add(role.name)
            elif role.name == config['ROLES']['Coalition Red']:
                roles['Red'] = role
                roles['All Red'].add(role.name)
            elif role.name == config['ROLES']['DCS']:
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
        for role in message.author.roles:
            if (role.name in gm_roles or role.name in da_roles) and \
                    not message.channel.overwrites_for(roles['everyone']).read_messages and \
                    not message.channel.overwrites_for(roles['DCS']).read_messages and \
                    not message.channel.overwrites_for(roles['Blue']).read_messages and \
                    not message.channel.overwrites_for(roles['Red']).read_messages:
                sides = [Coalition.BLUE, Coalition.RED]
                break
            elif role.name in roles['All Blue'] \
                    and message.channel.overwrites_for(roles['Blue']).send_messages and \
                    not message.channel.overwrites_for(roles['Red']).read_messages:
                sides = [Coalition.BLUE]
                break
            elif role.name in roles['All Red'] \
                    and message.channel.overwrites_for(roles['Red']).send_messages and \
                    not message.channel.overwrites_for(roles['Blue']).read_messages:
                sides = [Coalition.RED]
                break
    else:
        sides = [Coalition.BLUE, Coalition.RED]
    return sides
