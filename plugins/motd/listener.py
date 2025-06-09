import asyncio
import random

from core import EventListener, utils, Server, Report, Player, event
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import MOTD


class MOTDListener(EventListener["MOTD"]):

    async def on_join(self, config: dict, server: Server, player: Player) -> Optional[str]:
        if 'messages' in config:
            if config.get('random', False):
                cfg = random.choice(config['messages'])
                return await self.on_join(cfg, server, player)
            for cfg in config['messages']:
                message = await self.on_join(cfg, server, player)
                if message:
                    return message
            else:
                return None
        else:
            if 'recipients' in config:
                players = [p async for p in self.plugin.get_recipients(server, config)]
                if player not in players:
                    return None
            return utils.format_string(config['message'], server=server, player=player)

    async def on_birth(self, config: dict, server: Server, player: Player) -> tuple[Optional[str], Optional[dict]]:
        if 'messages' in config:
            if config.get('random', False):
                cfg = random.choice(config['messages'])
                return await self.on_birth(cfg, server, player)
            for cfg in config['messages']:
                message, _ = await self.on_birth(cfg, server, player)
                if message:
                    return message, cfg
            else:
                return None, None
        else:
            message = None
            if 'recipients' in config:
                players = [p async for p in self.plugin.get_recipients(server, config)]
                if player not in players:
                    return None, None
            if 'message' in config:
                message = utils.format_string(config['message'], server=server, player=player)
            elif 'report' in config:
                report = Report(self.bot, self.plugin_name, config['report'])
                env = await report.render(server=server, player=player,
                                          guild=self.bot.guilds[0] if self.bot.guilds else None)
                message = utils.embed_to_simpletext(env.embed)
            return message, config

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, _: dict) -> None:
        # make sure the config cache is re-read on mission changes
        self.plugin.get_config(server, use_cache=False)

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        async def _send_message(config: dict, server: Server, player: Player) -> None:
            await player.sendChatMessage(await self.on_join(config['on_join'], server, player))

        if data['id'] == 1 or 'ucid' not in data:
            return
        config = self.plugin.get_config(server)
        if config and 'on_join' in config:
            player: Player = server.get_player(ucid=data['ucid'])
            if player:
                asyncio.create_task(_send_message(config, server, player))

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config:
            return
        if data['eventName'] == 'S_EVENT_BIRTH' and 'name' in data['initiator'] and 'on_birth' in config:
            player: Player = server.get_player(name=data['initiator']['name'], active=True)
            if not player:
                # should never happen, just in case
                return
            message, cfg = await self.on_birth(config['on_birth'], server, player)
            if message:
                asyncio.create_task(self.plugin.send_message(message, server, cfg, player))
