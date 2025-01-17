import asyncio
import discord
import json
import os
import shutil
import subprocess
import tempfile
import tomli
import tomli_w

from core import Plugin, command, utils, Status, Server, PluginInstallationError, MizFile, UnsupportedMizFileException
from discord import app_commands
from services.bot import DCSServerBot
from typing import Optional


class RealWeather(Plugin):
    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.installation = self.node.locals.get('extensions', {}).get('RealWeather', {}).get('installation')
        if not self.installation:
            raise PluginInstallationError(
                plugin='RealWeather',
                reason=f"No configuration found for RealWeather for node {self.node.name} in nodes.yaml"
            )
        self.version = utils.get_windows_version(os.path.join(os.path.expandvars(self.installation), 'realweather.exe'))

    async def change_weather_1x(self, server: Server, filename: str, airbase: dict, config: dict) -> str:
        config = {
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
        rw_home = os.path.expandvars(self.installation)
        tmpfd, tmpname = tempfile.mkstemp()
        os.close(tmpfd)
        with open(os.path.join(rw_home, 'config.json'), mode='r', encoding='utf-8') as infile:
            cfg = json.load(infile)
        # create proper configuration
        for name, element in cfg.items():
            if name == 'files':
                element['input-mission'] = filename
                element['output-mission'] = tmpname
            if name in config:
                if isinstance(config[name], dict):
                    element |= config[name]
                else:
                    cfg[name] = config[name]
        cwd = await server.get_missions_dir()
        with open(os.path.join(cwd, 'config.json'), mode='w', encoding='utf-8') as outfile:
            json.dump(cfg, outfile, indent=2)

        def run_subprocess():
            subprocess.run([os.path.join(rw_home, 'realweather.exe')], cwd=cwd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        await asyncio.to_thread(run_subprocess)

        # check if DCS Real Weather corrupted the miz file
        # (as the original author does not see any reason to do that on his own)
        await asyncio.to_thread(MizFile, tmpname)

        # mission is good, take it
        # make an initial backup, if there is none
        if '.dcssb' not in filename and not os.path.exists(filename + '.orig'):
            shutil.copy2(filename, filename + '.orig')

        new_filename = utils.create_writable_mission(filename)
        shutil.copy2(tmpname, new_filename)
        os.remove(tmpname)
        return new_filename

    async def change_weather_2x(self, server: Server, filename: str, airbase: dict, config: dict) -> str:
        config = {
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
        rw_home = os.path.expandvars(self.installation)
        tmpfd, tmpname = tempfile.mkstemp()
        os.close(tmpfd)
        with open(os.path.join(rw_home, 'config.toml'), mode='rb') as infile:
            cfg = tomli.load(infile)
        # create proper configuration
        for name, element in cfg.items():
            if name == 'realweather':
                element['mission'] = {
                    "input": filename,
                    "output": tmpname
                }
            elif name in config:
                if isinstance(config[name], dict):
                    element |= config[name]
                else:
                    cfg[name] = config[name]
        cwd = await server.get_missions_dir()
        with open(os.path.join(cwd, 'config.toml'), mode='wb') as outfile:
            tomli_w.dump(cfg, outfile)

        def run_subprocess():
            subprocess.run([os.path.join(rw_home, 'realweather.exe')], cwd=cwd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        await asyncio.to_thread(run_subprocess)

        # check if DCS Real Weather corrupted the miz file
        # (as the original author does not see any reason to do that on his own)
        await asyncio.to_thread(MizFile, tmpname)

        # mission is good, take it
        # make an initial backup, if there is none
        if '.dcssb' not in filename and not os.path.exists(filename + '.orig'):
            shutil.copy2(filename, filename + '.orig')

        new_filename = utils.create_writable_mission(filename)
        shutil.copy2(tmpname, new_filename)
        os.remove(tmpname)
        return new_filename

    @command(description='Modify mission with a preset')
    @app_commands.guild_only()
    @app_commands.describe(idx='Select airport as reference')
    @app_commands.autocomplete(idx=utils.airbase_autocomplete)
    @app_commands.rename(idx="airport")
    @utils.app_has_role('DCS Admin')
    async def realweather(self, interaction: discord.Interaction,
                          server: app_commands.Transform[Server, utils.ServerTransformer(
                              status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                          idx: int, wind: Optional[bool] = False, clouds: Optional[bool] = False,
                          fog: Optional[bool] = False, dust: Optional[bool] = False,
                          temperature: Optional[bool] = False, pressure: Optional[bool] = False,
                          time: Optional[bool] = False):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status in [Status.PAUSED, Status.RUNNING]:
            question = 'Do you want to restart the server for a weather change?'
            if server.is_populated():
                result = await utils.populated_question(interaction, question, ephemeral=ephemeral)
            else:
                result = await utils.yn_question(interaction, question, ephemeral=ephemeral)
            if not result:
                return
        airbase = server.current_mission.airbases[idx]
        startup = False
        msg = await interaction.followup.send('Changing weather...', ephemeral=ephemeral)
        if not server.locals.get('mission_rewrite', True) and server.status != Status.STOPPED:
            await server.stop()
            startup = True
        filename = await server.get_current_mission_file()
        config = {
            "wind": wind,
            "clouds": clouds,
            "fog": fog,
            "dust": dust,
            "temperature": temperature,
            "pressure": pressure,
            "time": time
        }
        try:
            if self.version.split('.')[0] == '1':
                new_filename = await self.change_weather_1x(server, utils.get_orig_file(filename), airbase, config)
            else:
                new_filename = await self.change_weather_2x(server, utils.get_orig_file(filename), airbase, config)
            self.log.info(f"Realweather applied on server {server.name}.")
        except (FileNotFoundError, UnsupportedMizFileException):
            await msg.edit(content='Could not apply weather due to an error in RealWeather.')
            return
        message = 'Weather changed.'
        if new_filename != filename:
            self.log.info(f"  => New mission written: {new_filename}")
            await server.replaceMission(int(server.settings['listStartIndex']), new_filename)
        else:
            self.log.info(f"  => Mission {filename} overwritten.")
        if startup or server.status not in [Status.STOPPED, Status.SHUTDOWN]:
            await server.restart(modify_mission=False)
            message += '\nMission reloaded.'
        await self.bot.audit("changed weather", server=server, user=interaction.user)
        await msg.edit(content=message)


async def setup(bot: DCSServerBot):
    await bot.add_cog(RealWeather(bot))
