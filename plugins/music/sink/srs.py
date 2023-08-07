import asyncio
import discord
import subprocess
import os

from core import DCSServerBot, Server, Status, Coalition, utils
from discord import TextStyle
from discord.ui import Modal, TextInput
from typing import Optional

from . import SinkInitError, Sink
from ..utils import get_tag


class SRSSink(Sink):

    def __init__(self, bot: DCSServerBot, server: Server, config: dict, music_dir: str):
        super().__init__(bot, server, config, music_dir)
        self.process: Optional[subprocess.Popen] = None

    def render(self) -> discord.Embed:
        embed = discord.Embed(colour=discord.Colour.blue())
        embed.add_field(name="Frequency", value=self.config['frequency'] + " " + self.config['modulation'])
        embed.add_field(name="Coalition", value="Red" if self.config['coalition'] == 1 else "Blue")
        return embed

    async def play(self, file: str) -> None:
        self.log.debug(f"Playing {file} ...")
        if self.current and self.process:
            await self.skip()
        if self.server.status != Status.RUNNING:
            await self.stop()
            return
        try:
            try:
                srs_inst = os.path.expandvars(self.server.extensions['SRS'].config['installation'])
                srs_port = self.server.extensions['SRS'].locals['Server Settings']['SERVER_PORT']
            except KeyError:
                raise SinkInitError("You need to set the SRS path in your scheduler.json")
            self.current = file
            self.process = subprocess.Popen(
                [
                    "DCS-SR-ExternalAudio.exe",
                    "-f", self.config['frequency'],
                    "-m", self.config['modulation'],
                    "-c", self.config['coalition'],
                    "-v", self.config.get('volume', '1.0'),
                    "-p", srs_port,
                    "-n", self.config.get('name', 'DCSSB MusicBox'),
                    "-i", file
                ],
                executable=srs_inst + os.path.sep + "DCS-SR-ExternalAudio.exe",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if 'popup' in self.config:
                kwargs = self.config.copy()
                kwargs['song'] = get_tag(file).title or os.path.basename(file)
                self.server.sendPopupMessage(Coalition.ALL, utils.format_string(self.config['popup'], **kwargs))
            if 'chat' in self.config:
                kwargs = self.config.copy()
                kwargs['song'] = get_tag(file).title or os.path.basename(file)
                self.server.sendChatMessage(Coalition.ALL, utils.format_string(self.config['popup'], **kwargs))
            while self.process.poll() is None:
                await asyncio.sleep(1)
        except Exception as ex:
            self.log.exception(ex)
        finally:
            self.current = None

    async def skip(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.kill()
            self.current = None

    async def stop(self) -> None:
        if self.queue_worker.is_running():
            self.queue_worker.cancel()
            await self.skip()
            while self.queue_worker.is_running():
                await asyncio.sleep(0.5)

    def edit(self) -> Modal:
        class EditModal(Modal, title="Change Settings"):
            frequency = TextInput(label='Frequency (xxx.xx)', style=TextStyle.short, required=True, default=self.config['frequency'], min_length=4, max_length=6)
            modulation = TextInput(label='Modulation (AM | FM)', style=TextStyle.short, required=True, default=self.config['modulation'], min_length=2, max_length=2)
            volume = TextInput(label='Volume', style=TextStyle.short, required=True, default=self.config['volume'], min_length=1, max_length=3)
            coalition = TextInput(label='Coalition (1=red | 2=blue)', style=TextStyle.short, required=True, default=self.config['coalition'], min_length=1, max_length=2)
            name = TextInput(label='Radio Name', style=TextStyle.short, required=True, default=self.config['name'], min_length=3, max_length=30)

            async def on_submit(derived, interaction: discord.Interaction):
                await interaction.response.defer()
                self.config['frequency'] = derived.frequency.value
                if derived.modulation.value.upper() in ['AM', 'FM']:
                    self.config['modulation'] = derived.modulation.value.upper()
                else:
                    raise ValueError("Modulation must be one of AM | FM!")
                self.config['volume'] = derived.volume.value
                if derived.coalition.value.isnumeric() and int(derived.coalition.value) in range(1, 3):
                    self.config['coalition'] = derived.coalition.value
                else:
                    raise ValueError("Coalition must be 1 or 2!")
                self.config['name'] = derived.name.value
                await self.stop()
                await self.start()

            async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
                await interaction.followup.send(error.__str__(), ephemeral=True)

        return EditModal()
