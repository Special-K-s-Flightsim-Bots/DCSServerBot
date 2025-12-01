import discord
import os

from core import Plugin, command, utils, Status, Server, PluginInstallationError, UnsupportedMizFileException
from discord import app_commands
from services.bot import DCSServerBot
from typing import Type

from .listener import RealWeatherEventListener


class RealWeather(Plugin[RealWeatherEventListener]):
    def __init__(self, bot: DCSServerBot, listener: Type[RealWeatherEventListener] = None):
        super().__init__(bot, listener)
        self.installation = self.node.locals.get('extensions', {}).get('RealWeather', {}).get('installation')
        if not self.installation:
            raise PluginInstallationError(
                plugin='RealWeather',
                reason=f"No configuration found for RealWeather for node {self.node.name} in nodes.yaml"
            )
        self.version = utils.get_windows_version(os.path.join(os.path.expandvars(self.installation), 'realweather.exe'))

    @staticmethod
    def generate_config_1_0(airbase: dict, config: dict) -> dict:
        return {
            "metar": {
                "icao": airbase['code']
            },
            "options": {
                "update-weather": True,
                "update-time": config['time'],
                "fog": {
                    "enable": config['fog']
                },
                "dust": {
                    "enable": config['dust']
                }
            }
        }

    @staticmethod
    def generate_config_2_0(airbase: dict, config: dict) -> dict:
        return {
            "options": {
                "weather": {
                    "enable": True,
                    "icao": airbase['code'],
                    "wind": {
                        "enable": config['wind']
                    },
                    "clouds": {
                        "enable": config['clouds']
                    },
                    "temperature": {
                        "enable": config['temperature']
                    },
                    "pressure": {
                        "enable": config['pressure']
                    },
                    "fog": {
                        "enable": config['fog']
                    },
                    "dust": {
                        "enable": config['dust']
                    }
                },
                "time": {
                    "enable": config['time']
                }
            }
        }

    def generate_config(self, airbase: dict, config: dict) -> dict:
        if self.version.split('.')[0] == '1':
            return self.generate_config_1_0(airbase, config)
        else:
            return self.generate_config_2_0(airbase, config)

    @command(description='Modify mission with a preset')
    @app_commands.guild_only()
    @app_commands.describe(idx='Select airport as reference')
    @app_commands.autocomplete(idx=utils.airbase_autocomplete)
    @app_commands.rename(idx="airport")
    @utils.app_has_role('DCS Admin')
    async def realweather(self, interaction: discord.Interaction,
                          server: app_commands.Transform[Server, utils.ServerTransformer(
                              status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                          idx: int, use_orig: bool | None = True, wind: bool | None = False,
                          clouds: bool | None = False,
                          fog: bool | None = False, dust: bool | None = False,
                          temperature: bool | None = False, pressure: bool | None = False,
                          time: bool | None = False):
        ephemeral = utils.get_ephemeral(interaction)
        airbase = server.current_mission.airbases[idx]
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        msg = await interaction.followup.send('Changing weather...', ephemeral=ephemeral)
        status = server.status
        if not server.locals.get('mission_rewrite', True) and server.status in [Status.RUNNING, Status.PAUSED]:
            question = 'Do you want to restart the server for a weather change?'
            if server.is_populated():
                result = await utils.populated_question(interaction, question, ephemeral=ephemeral)
                if result:
                    result = 'yes'
            else:
                result = await utils.yn_question(interaction, question, ephemeral=ephemeral)
            if not result:
                return
            if result == 'yes':
                await server.stop()
        else:
            result = None
        try:
            config = self.generate_config(airbase, {
                "wind": wind,
                "clouds": clouds,
                "fog": fog,
                "dust": dust,
                "temperature": temperature,
                "pressure": pressure,
                "time": time
            })
            try:
                filename = await server.get_current_mission_file()
                new_filename = await server.run_on_extension('RealWeather', 'apply_realweather',
                                                             filename=filename, config=config, use_orig=use_orig)
            except ValueError:
                await msg.edit(content='Could not apply weather, RealWeather extension not loaded.')
                return
            except (FileNotFoundError, UnsupportedMizFileException):
                await msg.edit(content='Could not apply weather due to an error in RealWeather.')
                return
            message = 'Weather changed.'
            if new_filename != filename:
                self.log.info(f"  => New mission written: {new_filename}")
                await server.replaceMission(int(server.settings['listStartIndex']), new_filename)
            else:
                self.log.info(f"  => Mission {filename} overwritten.")

            if status == server.status and status in [Status.RUNNING, Status.PAUSED]:
                await server.restart(modify_mission=False)
                message += '\nMission reloaded.'
            elif result == 'later':
                server.on_empty = {
                    "method": "load",
                    "mission_file": new_filename,
                    "user": interaction.user
                }
                msg += 'Mission will restart, when server is empty.'

            await self.bot.audit("changed weather", server=server, user=interaction.user)
            await msg.edit(content=message)
        finally:
            if status in [Status.RUNNING, Status.PAUSED] and server.status == Status.STOPPED:
                await server.start()


async def setup(bot: DCSServerBot):
    await bot.add_cog(RealWeather(bot, RealWeatherEventListener))
