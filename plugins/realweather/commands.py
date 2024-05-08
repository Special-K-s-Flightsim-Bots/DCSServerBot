import asyncio
import discord
import json
import os
import shutil
import subprocess
import tempfile

from core import Plugin, command, utils, Status, Server, PluginInstallationError, MizFile, UnsupportedMizFileException
from discord import app_commands
from services import DCSServerBot


class RealWeather(Plugin):
    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.installation = self.node.locals.get('extensions', {}).get('RealWeather', {}).get('installation')
        if not self.installation:
            raise PluginInstallationError(
                plugin='RealWeather',
                reason=f"No configuration found for RealWeather for node {self.node.name} in nodes.yaml"
            )

    async def change_weather(self, server: Server, filename: str, airbase: dict) -> str:
        config = {
            "metar": {
                "icao": airbase['code']
            },
            "options": {
                "update-time": True,
                "update-weather": True
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
        new_filename = utils.create_writable_mission(filename)
        shutil.copy2(tmpname, new_filename)
        os.remove(tmpname)
        return new_filename

    @command(description='Modify mission with a preset')
    @app_commands.guild_only()
    @app_commands.describe(idx='Select airport as reference')
    @app_commands.autocomplete(idx=utils.airbase_autocomplete)
    @utils.app_has_role('DCS Admin')
    async def realweather(self, interaction: discord.Interaction,
                          server: app_commands.Transform[Server, utils.ServerTransformer(
                              status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                          idx: int):
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
        if not server.node.config.get('mission_rewrite', True) and server.status != Status.STOPPED:
            await server.stop()
            startup = True
        filename = await server.get_current_mission_file()
        try:
            new_filename = await self.change_weather(server, filename, airbase)
            self.log.info(f"Realweather applied on server {server.name}.")
        except (FileNotFoundError, UnsupportedMizFileException):
            await msg.edit(content='Could not apply weather due to an error in RealWeather.', ephemeral=ephemeral)
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
