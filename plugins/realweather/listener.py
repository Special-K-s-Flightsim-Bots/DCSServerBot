import re

from core import EventListener, chat_command, Server, Player, get_translation, Status
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import RealWeather

_ = get_translation(__name__.split('.')[1])


class RealWeatherEventListener(EventListener["RealWeather"]):

    @staticmethod
    def generate_config_1_0(icao: str) -> dict:
        return {
            "metar": {
                "icao": icao
            }
        }

    @staticmethod
    def generate_config_2_0(icao: str) -> dict:
        return {
            "options": {
                "weather": {
                    "enable": True,
                    "icao": icao
                }
            }
        }

    def generate_config(self, icao: str) -> dict:
        if self.plugin.version.split('.')[0] == '1':
            return self.generate_config_1_0(icao)
        else:
            return self.generate_config_2_0(icao)

    @chat_command(name="realweather", help=_("applies real weather"), roles=['DCS Admin'], usage="<icao|airport>")
    async def realweather(self, server: Server, player: Player, params: list[str]):
        if 'RealWeather' not in await server.list_extension():
            await player.sendChatMessage("RealWeather is not enabled in this server.")
            return

        if len(params):
            icao = next(
                (
                    airbase['code'] for airbase in server.current_mission.airbases
                    if params[0].casefold() in airbase['name'].casefold()
                ), None
            )
            if not icao:
                icao = params[0].upper()
                if not re.match('^[A-Z0-9]{4}$', icao):
                    await player.sendChatMessage(f"{icao} is not a valid ICAO!")
                    return
            config = self.generate_config(icao)
        else:
            config = {}
        filename = await server.get_current_mission_file()
        if not server.locals.get('mission_rewrite', True):
            await server.stop()
        new_filename = await server.run_on_extension('RealWeather', 'apply_realweather',
                                                     filename=filename, config=config)
        if new_filename != filename:
            await server.replaceMission(int(server.settings['listStartIndex']), new_filename)
        await server.restart(modify_mission=False)
        if server.status == Status.STOPPED:
            await server.start()
        await self.bot.audit(f"applied RealWeather", server=server, user=player.ucid)
