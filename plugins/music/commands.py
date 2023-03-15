import aiohttp
import asyncio
import discord
import eyed3
import logging
import os
import sys
from core import Plugin, DCSServerBot, utils, Server, TEventListener
from discord import SelectOption
from discord.ext import commands
from discord.ui import View, Select, Button
from eyed3.id3 import Tag
from functools import lru_cache
from pathlib import Path
from typing import cast, Type, Optional

from .listener import MusicEventListener
from .sink import Sink, Mode


class Music(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.sinks: dict[str, Sink] = dict()
        logging.getLogger(name='eyed3.mp3.headers').setLevel(logging.FATAL)

    async def cog_unload(self):
        for sink in self.sinks.values():
            await sink.stop()
        await super().cog_unload()

    def get_music_dir(self, server: Server) -> str:
        music_dir = self.get_config(server)['music_dir']
        if music_dir.startswith('.'):
            music_dir = os.path.expandvars(self.bot.config[server.installation]['DCS_HOME'] + music_dir[1:])
        if not os.path.exists(music_dir):
            os.makedirs(music_dir)
        return music_dir

    class MusicPlayer(View):

        def __init__(self, songs: list[str], sink: Sink):
            super().__init__()
            self.sink = sink
            select: Select = cast(Select, self.children[0])
            self.titles = [self.get_tag(x).title for x in songs]
            self.songs = songs
            select.options = [
                SelectOption(label=x, value=str(idx)) for idx, x in enumerate(self.titles)
            ]
            self.children[1].emoji = "⏹️" if self.sink.queue_worker.is_running() else "▶️"
            self.children[3].emoji = "🔁" if self.sink.mode == Mode.ONCE else "🔂"

        @lru_cache(maxsize=None)
        def get_tag(self, file) -> Tag:
            return eyed3.load(file).tag

        def render_embed(self) -> discord.Embed:
            embed = self.sink.render()
            embed.title = "Music Player"
            if self.sink.current:
                tag = self.get_tag(self.sink.current)
                embed.add_field(name='▬' * 13 + " Now Playing " + '▬' * 13, value='_ _', inline=False)
                embed.add_field(name="Title", value=tag.title[:255] if tag.title else 'n/a')
                embed.add_field(name='Artist', value=tag.artist[:255] if tag.artist else 'n/a')
                embed.add_field(name='Album', value=tag.album[:255] if tag.album else 'n/a')
            embed.add_field(name='▬' * 14 + " Playlist " + '▬' * 14, value='_ _', inline=False)
            playlist = []
            for i in range(0, self.sink.queue.qsize()):
                playlist.append(f"{i+1}. - {self.titles[self.songs.index(self.sink.queue.queue[i])]}")
            embed.add_field(name='_ _', value='\n'.join(playlist) or '- empty -')
            footer = "▬" * 37 + "\n"
            if self.sink.queue_worker.is_running():
                footer += "⏹️ Stop"
            else:
                footer += "▶️ Play"
            footer += " | ⏩ Skip | "
            if self.sink.mode == Mode.ONCE:
                footer += "🔁 Repeat"
            else:
                footer += "🔂 Play once"
            embed.set_footer(text=footer)
            return embed

        @discord.ui.select(placeholder="Add a Song to the Playlist")
        async def callback(self, interaction: discord.Interaction, select: Select):
            await interaction.response.defer()
            await self.sink.add(self.songs[int(select.values[0])])
            await interaction.edit_original_response(view=self, embed=self.render_embed())

        @discord.ui.button(style=discord.ButtonStyle.primary)
        async def on_play_stop(self, interaction: discord.Interaction, button: Button):
            try:
                await interaction.response.defer()
                if self.sink.queue_worker.is_running():
                    await self.sink.stop()
                    self.children[1].emoji = "▶️"
                else:
                    await self.sink.start()
                    self.children[1].emoji.name = "⏹️"
                await interaction.edit_original_response(view=self, embed=self.render_embed())
            except Exception as ex:
                print(ex)

        @discord.ui.button(style=discord.ButtonStyle.primary, emoji="⏩")
        async def on_skip(self, interaction: discord.Interaction, button: Button):
            await interaction.response.defer()
            await self.sink.skip()
            await interaction.edit_original_response(view=self, embed=self.render_embed())

        @discord.ui.button(style=discord.ButtonStyle.primary)
        async def on_repeat(self, interaction: discord.Interaction, button: Button):
            await interaction.response.defer()
            await self.sink.stop()
            if self.sink.mode == Mode.ONCE:
                self.sink.mode = Mode.REPEAT
                self.children[3].emoji = "🔂"
            else:
                self.sink.mode = Mode.ONCE
                self.children[3].emoji = "🔁"
            await self.sink.start()
            await interaction.edit_original_response(view=self, embed=self.render_embed())

        @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary)
        async def on_edit(self, interaction: discord.Interaction, button: Button):
            try:
                modal = self.sink.edit()
                await interaction.response.send_modal(modal)
                await modal.wait()
            except Exception as ex:
                print(ex)

        @discord.ui.button(label="Quit", style=discord.ButtonStyle.red)
        async def on_cancel(self, interaction: discord.Interaction, button: Button):
            await interaction.response.defer()
            self.stop()

    @commands.command(description='Music Player')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def music(self, ctx: commands.Context, param: Optional[str] = None):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        sink = self.sinks.get(server.name, None)
        if not sink:
            config = self.get_config(server)['sink']
            sink: Sink = getattr(sys.modules['plugins.music.sink'], config['type'])(bot=self.bot, server=server,
                                                                                    config=config)
            self.sinks[server.name] = sink

        if param and param.lower() == "-clear":
            sink.clear()
            await ctx.send('Playlist cleared.', delete_after=10)

        # list the songs ordered by modified timestamp descending (latest first)
        songs = [file.__str__() for file in sorted(Path(self.get_music_dir(server)).glob('*.mp3'),
                                                   key=lambda x: x.stat().st_mtime, reverse=True)]
        if not len(songs):
            await ctx.send("No music uploaded on this server. You can just upload mp3 files in here.")
            return
        view = self.MusicPlayer(songs=songs, sink=sink)
        embed = view.render_embed()
        msg = await ctx.send(embed=embed, view=view)
        try:
            while not view.is_finished():
                await msg.edit(embed=view.render_embed())
                await asyncio.sleep(1)
        finally:
            await msg.delete()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages or messages that do not contain music attachments
        if message.author.bot or not message.attachments or \
                (not message.attachments[0].filename.endswith('.mp3') and not message.attachments[0].filename.endswith('.ogg')):
            return
        server: Server = await self.bot.get_server(message)
        # only DCS Admin role is allowed to upload missions in the servers admin channel
        if not server or not utils.check_roles(
                [x.strip() for x in self.bot.config['ROLES']['DCS Admin'].split(',')], message.author):
            return
        att = message.attachments[0]
        filename = self.get_music_dir(server) + os.path.sep + att.filename
        try:
            ctx = utils.ContextWrapper(message)
            if os.path.exists(filename):
                if await utils.yn_question(ctx, 'File exists. Do you want to overwrite it?') is False:
                    await message.channel.send('Upload aborted.')
                    return
            async with aiohttp.ClientSession() as session:
                async with session.get(att.url) as response:
                    if response.status == 200:
                        with open(filename, 'wb') as outfile:
                            outfile.write(await response.read())
                        await message.channel.send('File uploaded.')
                    else:
                        await message.channel.send(f'Error {response.status} while reading file {att.filename}!')
        except Exception as ex:
            self.log.exception(ex)
        finally:
            await message.delete()


async def setup(bot: DCSServerBot):
    await bot.add_cog(Music(bot, MusicEventListener))
