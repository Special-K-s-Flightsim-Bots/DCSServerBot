import discord
from copy import deepcopy
from core import Plugin, PluginRequiredError, Server, Player, TEventListener, PluginInstallationError, DEFAULT_TAG
from discord.ext import commands
from services import DCSServerBot
from typing import Optional, Type
from .listener import SlotBlockingListener


class SlotBlocking(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener=eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.yaml file found!", plugin=self.plugin_name)

    def get_config(self, server: Server, plugin_name: str = None) -> Optional[dict]:
        if plugin_name:
            return super().get_config(server, plugin_name)
        if server.instance.name not in self._config:
            vips = self.locals.get(server.instance.name, {}).get('VIP')
            if vips:
                vips |= self.locals.get(DEFAULT_TAG, {}).get('VIP', {})
            self._config[server.instance.name] = \
                deepcopy(self.locals.get(DEFAULT_TAG, {}) | self.locals.get(server.instance.name, {}))
            if vips:
                self._config[server.instance.name]['VIP'] = vips
        return self._config[server.instance.name]

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # did a member change its roles?
        if before.roles != after.roles:
            for server in self.bot.servers.values():
                player: Player = server.get_player(discord_id=after.id)
                if player:
                    player.member = after
                else:
                    ucid = self.bot.get_ucid_by_member(after)
                    if not ucid:
                        return
                    roles = [
                        discord.utils.get(self.bot.guilds[0].roles, name=x)
                        for x in self.get_config(server).get('VIP', {}).get('discord', [])
                    ]
                    if not roles:
                        return
                    for role in after.roles:
                        if role in roles:
                            server.sendtoDCS({
                                'command': 'uploadUserRoles',
                                'ucid': ucid,
                                'roles': [x.name for x in after.roles]
                            })
                            break


async def setup(bot: DCSServerBot):
    for plugin in ['mission', 'creditsystem']:
        if plugin not in bot.plugins:
            raise PluginRequiredError(plugin)
    await bot.add_cog(SlotBlocking(bot, SlotBlockingListener))
