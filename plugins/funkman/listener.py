import asyncio
import discord
import sys
import uuid
import matplotlib.figure

from core import EventListener, Server, event, Player, PersistentReport, Channel, get_translation
from io import BytesIO
from matplotlib import pyplot as plt
from typing import Literal, TYPE_CHECKING

from .const import StrafeQuality, BombQuality

if TYPE_CHECKING:
    from .commands import FunkMan

_ = get_translation(__name__.split('.')[1])


class FunkManEventListener(EventListener["FunkMan"]):

    def __init__(self, plugin: "FunkMan"):
        super().__init__(plugin)
        self.config = self.get_config()
        path = self.config.get('install')
        sys.path.append(path)
        from funkman.utils.utils import _GetVal
        self.funkplot = None
        self._GetVal = _GetVal
        self.lock = asyncio.Lock()

    async def get_funkplot(self):
        async with self.lock:
            if not self.funkplot:
                from funkman.funkplot.funkplot import FunkPlot
                self.funkplot = FunkPlot(ImagePath=self.config['IMAGEPATH'])
            return self.funkplot

    # from FunkBot, to be replaced with a proper function call!
    def create_lso_embed(self, result: dict) -> discord.Embed:
        actype = self._GetVal(result, "airframe", "Unkown")
        Tgroove = self._GetVal(result, "Tgroove", "?", 1)
        player = self._GetVal(result, "name", "Ghostrider")
        grade = self._GetVal(result, "grade", "?")
        points = self._GetVal(result, "points", "?")
        details = self._GetVal(result, "details", "?")
        case = self._GetVal(result, "case", "?")
        wire = self._GetVal(result, "wire", "?")
        carriertype = self._GetVal(result, "carriertype", "?")
        carriername = self._GetVal(result, "carriername", "?")
        windondeck = self._GetVal(result, "wind", "?", 1)
        missiontime = self._GetVal(result, "mitime", "?")
        missiondate = self._GetVal(result, "midate", "?")
        theatre = self._GetVal(result, "theatre", "Unknown Map")
        theta = self._GetVal(result, "carrierrwy", -9)

        color = 0x00ff00
        urlIm = "https://i.imgur.com/1bWgcV7.png"
        if isinstance(points, int) or isinstance(points, float):
            if points == 0:
                color = 0x000000  # black
                urlIm = "https://i.imgur.com/rZpu9c0.png"
            elif points == 1:
                color = 0xff0000  # red
                urlIm = "https://i.imgur.com/LXgD2Op.png"
            elif points == 2:
                color = 0xFFA500  # orange
                urlIm = "https://i.imgur.com/EjviMBk.png"
            elif points == 2.5:
                color = 0xB47E59  # brown
                urlIm = "https://i.imgur.com/nYWrL4Z.png"
            elif points == 3:
                color = 0xFFFF00  # yellow
                urlIm = "https://i.imgur.com/wH0Gjqx.png"
            elif points == 4:
                color = 0x00FF00  # green
                urlIm = "https://i.imgur.com/1bWgcV7.png"
            elif points == 5:
                color = 0x0000FF  # blue
                urlIm = "https://i.imgur.com/6ecFSqo.png"

        # Create Embed
        embed = discord.Embed(title=_("LSO Grade"),
                              description=_("Result for {player} at carrier {carriername} [{carriertype}]").format(
                                  player=player, carriername=carriername, carriertype=carriertype),
                              color=color)

        # Images.
        embed.set_thumbnail(url=urlIm)

        # Data.
        embed.add_field(name=_("Grade"), value=grade)
        embed.add_field(name=_("Points"), value=points)
        embed.add_field(name=_("Details"), value=details)
        embed.add_field(name=_("Groove"), value=Tgroove)
        if wire != "?":
            embed.add_field(name=_("Wire"), value=wire)
        embed.add_field(name=_("Case"), value=case)
        embed.add_field(name=_("Wind"), value=windondeck)
        embed.add_field(name=_("Aircraft"), value=actype)

        # Footer.
        embed.set_footer(text=f"{theatre}: {missiondate} ({missiontime})")
        return embed

    @staticmethod
    def save_fig(fig: matplotlib.figure.Figure) -> tuple[str, BytesIO]:
        filename = f'{uuid.uuid4()}.png'
        buffer = BytesIO()
        fig.savefig(buffer, format='png', bbox_inches='tight', facecolor='#2C2F33')
        buffer.seek(0)
        plt.close(fig)
        return filename, buffer

    async def send_fig(self, fig: matplotlib.figure.Figure, channel: discord.TextChannel):
        try:
            filename, buffer = self.save_fig(fig)
            with buffer:
                await channel.send(file=discord.File(fp=buffer, filename=filename),
                                   delete_after=self.config.get('delete_after'))
        except Exception as ex:
            self.log.exception(ex)

    async def update_rangeboard(self, server: Server, what: Literal['strafe', 'bomb']):
        try:
            # update the server specific board
            config = self.plugin.get_config(server)
            if config.get(f'{what}_board', False):
                channel_id = int(config.get(f'{what}_channel', server.channels[Channel.STATUS]))
                num_rows = config.get('num_rows', 10)
                report = PersistentReport(self.bot, self.plugin_name, f'{what}board.json',
                                          embed_name=f'{what}board', server=server, channel_id=channel_id)
                await report.render(server_name=server.name, num_rows=num_rows)
            # update the global board
            config = self.get_config()
            if f'{what}_channel' in config and config.get(f'{what}_board', False):
                num_rows = config.get('num_rows', 10)
                report = PersistentReport(self.bot, self.plugin_name, f'{what}board.json', embed_name=f'{what}board',
                                          channel_id=int(config[f'{what}_channel']))
                await report.render(server_name=None, num_rows=num_rows)
        except Exception as ex:
            self.log.exception(ex)

    @event(name='registerDCSServer')
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        config = self.get_config(server)
        for name in ['CHANNELID_MAIN', 'CHANNELID_RANGE', 'CHANNELID_AIRBOSS']:
            if name in config:
                self.bot.check_channel(self.config[name])

    @event(name="moose_text")
    async def moose_text(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        channel = self.bot.get_channel(int(config.get('CHANNELID_MAIN', -1)))
        if not channel:
            return
        await channel.send(data['text'], delete_after=self.config.get('delete_after'))

    @event(name="moose_bomb_result")
    async def moose_bomb_result(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        player: Player = server.get_player(name=data['player'])
        if player:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute("""
                        INSERT INTO bomb_runs (mission_id, player_ucid, unit_type, range_name, distance, quality)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (server.mission_id, player.ucid, player.unit_type, data.get('rangename', 'n/a'),
                          data['distance'], BombQuality[data['quality']].value))
            asyncio.create_task(self.update_rangeboard(server, 'bomb'))
        channel = self.bot.get_channel(int(config.get('CHANNELID_RANGE', -1)))
        if not channel:
            return
        fig, _ = (await self.get_funkplot()).PlotBombRun(data)
        if not fig:
            self.log.error("Bomb result could not be plotted (due to missing data?)")
            return
        asyncio.create_task(self.send_fig(fig, channel))

    @event(name="moose_strafe_result")
    async def moose_strafe_result(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        player: Player = server.get_player(name=data['player'])
        if player:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute("""
                        INSERT INTO strafe_runs (mission_id, player_ucid, unit_type, range_name, accuracy, quality)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (server.mission_id, player.ucid, player.unit_type, data.get('rangename', 'n/a'),
                          data['strafeAccuracy'], StrafeQuality[data['roundsQuality'].replace(' ', '_')].value if not data.get('invalid', False) else None))
            asyncio.create_task(self.update_rangeboard(server, 'strafe'))
        channel = self.bot.get_channel(int(config.get('CHANNELID_RANGE', -1)))
        if not channel:
            return
        fig, _ = (await self.get_funkplot()).PlotStrafeRun(data)
        if not fig:
            self.log.error("Strafe result could not be plotted (due to missing data?)")
            return
        asyncio.create_task(self.send_fig(fig, channel))

    @event(name="moose_lso_grade")
    async def moose_lso_grade(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        channel = self.bot.get_channel(int(config.get('CHANNELID_AIRBOSS', -1)))
        if not channel:
            return
        try:
            fig, _ = (await self.get_funkplot()).PlotTrapSheet(data)
            if not fig:
                self.log.error("Trapsheet could not be plotted (due to missing data?)")
                return
            filename, buffer = self.save_fig(fig)
            with buffer:
                embed = self.create_lso_embed(data)
                embed.set_image(url=f"attachment://{filename}")
                await channel.send(embed=embed, file=discord.File(fp=buffer, filename=filename),
                                   delete_after=self.config.get('delete_after'))
        except (ValueError, TypeError):
            self.log.warning("No or invalid trapsheet data received from DCS!")
