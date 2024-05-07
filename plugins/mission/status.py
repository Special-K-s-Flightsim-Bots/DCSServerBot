import asyncio

from core import const, report, Status, Server, utils, ServiceRegistry, Plugin, Side
from datetime import datetime, timedelta, timezone
from services import BotService
from typing import Optional, cast

STATUS_IMG = {
    Status.LOADING:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/loading_256.png?raw=true',
    Status.PAUSED:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/pause_256.png?raw=true',
    Status.RUNNING:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/play_256.png?raw=true',
    Status.STOPPED:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/stop_256.png?raw=true',
    Status.SHUTDOWN:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/stop_256.png?raw=true',
    Status.UNREGISTERED:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/stop_256.png?raw=true'
}


class Init(report.EmbedElement):
    async def render(self, server: Server):
        num_players = len(server.get_active_players()) + 1
        self.embed.set_author(
            name=f"{server.name} [{num_players}/{server.settings['maxPlayers']}]",
            icon_url=STATUS_IMG[server.status])
        if server.status in [Status.PAUSED, Status.RUNNING] and server.current_mission:
            self.embed.description = f"Mission: \"{server.current_mission.display_name}\""
        else:
            self.embed.description = f"_{server.status.value}_"
        self.embed.set_footer(text='')


class ServerInfo(report.EmbedElement):

    async def render(self, server: Server, show_password: Optional[bool] = True):
        name = value = ""
        if server.node.public_ip:
            name = "Server-IP / Port"
            value = f"{server.node.public_ip}:{server.settings['port']}"
        if server.settings['password']:
            if value:
                value += '\n\n**Password**\n'
            else:
                name = "Password"
            value += f"{server.settings['password']}" if show_password else r'\*\*\*\*\*\*\*\*'
        self.add_field(name=name, value=value)
        if server.current_mission:
            value = server.current_mission.map
            if not server.locals.get('coalitions'):
                blue = len(server.get_active_players(side=Side.BLUE))
                red = len(server.get_active_players(side=Side.RED))
                value += "\n\n**Slots**\n"
                if server.current_mission.num_slots_blue:
                    value += f"🔹Used: {blue} / {server.current_mission.num_slots_blue}\n"
                if server.current_mission.num_slots_red:
                    value += f"🔸Used: {red} / {server.current_mission.num_slots_red}"
            else:
                value += "\n\n**Coalitions**\nYes"
            self.add_field(name='Map', value=value)

            uptime = int(server.current_mission.mission_time)
            if isinstance(server.current_mission.date, datetime):
                date = server.current_mission.date.timestamp()
                real_time = date + server.current_mission.start_time + uptime
                value = str(datetime.fromtimestamp(real_time))
            else:
                value = '{} {}'.format(server.current_mission.date,
                                       timedelta(seconds=server.current_mission.start_time + uptime))

            if server.restart_time and not server.maintenance:
                value += (f"\n\n**Runtime\t|\tRestart**\n"
                          f"{timedelta(seconds=uptime)}\t|\t<t:{int(server.restart_time.timestamp())}:R>")
            else:
                value += f"\n\n**Runtime**\n{timedelta(seconds=uptime)}"
            self.add_field(name='Date / Time in Mission', value=value)
        if server.maintenance:
            footer = 'SERVER IS IN MAINTENANCE MODE, SCHEDULER WILL NOT WORK!\n\n'
        else:
            footer = ''
        if server.dcs_version:
            footer += f'DCS {server.dcs_version} | DCSServerBot {self.node.bot_version}.{self.node.sub_version} | '
        self.embed.set_footer(text=footer)


class WeatherInfo(report.EmbedElement):

    async def render(self, server: Server):
        if server.current_mission and server.current_mission.weather:
            await report.Ruler(self.env).render()
            weather = server.current_mission.weather
            value = f"{weather['season']['temperature']:.1f} °C"
            value += "\n\n**QNH (QFF)**\n{:.2f} inHg\n{} hPa".format(
                weather['qnh'] * const.MMHG_IN_INHG, int(weather['qnh'] * const.MMHG_IN_HPA))
            self.add_field(name='Temperature', value=value)
            clouds = server.current_mission.clouds
            if clouds:
                if 'preset' in clouds:
                    value = clouds['preset']['readableName'][5:].split('\n')[0].replace('/', '/\n')
                    value += f"\n\n**Cloudbase**\n{int(clouds['base'] * const.METER_IN_FEET + 0.5):,} ft"
                else:
                    value = "Dynamic"
                    value += ("\n\n**Cloudbase**\n"
                              "Base:\u2002\u2002\u2002\u2002 {:,} ft\nDensity:\u2002\u2002 {}/10\nThickness: {:,} ft"
                              ).format(int(clouds['base'] * const.METER_IN_FEET + 0.5),
                                       clouds['density'],
                                       int(clouds['thickness'] * const.METER_IN_FEET + 0.5))
                self.add_field(name='Clouds', value=value)
            else:
                self.add_field(name='Weather', value='Dynamic\n**Clouds**\nn/a')

            visibility = weather['visibility']['distance']
            if weather['enable_fog'] is True:
                visibility = int(weather['fog']['visibility'] * const.METER_IN_FEET + 0.5)
            value = "{:,} ft".format(int(visibility)) if visibility < 30000 else "10 km (+)"
            value += ("\n\n**Wind**\n"
                      "\u2002Ground: {}° / {} kts\n\u20026600 ft: {}° / {} kts\n26000 ft: {}° / {} kts").format(
                int(weather['wind']['atGround']['dir'] + 180) % 360,
                int(weather['wind']['atGround']['speed'] * const.METER_PER_SECOND_IN_KNOTS + 0.5),
                int(weather['wind']['at2000']['dir'] + 180) % 360,
                int(weather['wind']['at2000']['speed'] * const.METER_PER_SECOND_IN_KNOTS + 0.5),
                int(weather['wind']['at8000']['dir'] + 180) % 360,
                int(weather['wind']['at8000']['speed'] * const.METER_PER_SECOND_IN_KNOTS + 0.5))
            self.add_field(name='Visibility', value=value)


class ExtensionsInfo(report.EmbedElement):

    async def render(self, server: Server):
        extensions = await server.render_extensions() if server.status in [Status.RUNNING, Status.PAUSED] else None
        # we don't have any extensions loaded (yet)
        if not extensions:
            return
        await report.Ruler(self.env).render()
        footer = self.embed.footer.text or ''
        for ext in extensions:
            self.add_field(name=ext['name'], value=ext['value'])
        footer += " | ".join([f"{ext['name']} v{ext['version']}" for ext in extensions if ext.get('version')])
        self.embed.set_footer(text=footer)


class ScheduleInfo(report.EmbedElement):

    async def render(self, server: Server):
        bot = ServiceRegistry.get(BotService).bot
        scheduler: Plugin = cast(Plugin, bot.cogs.get('Scheduler'))
        if scheduler:
            config = scheduler.get_config(server)
            if 'schedule' in config:
                await report.Ruler(self.env).render(text="This server runs on the following schedule:")
                utc_diff = utils.get_utc_offset()
                self.add_field(name=f'Time (UTC{utc_diff})', value='\n'.join(config['schedule'].keys()))
                value = ''
                for schedule in config['schedule'].values():
                    for c in schedule:
                        if c == 'Y':
                            value += '✅|'
                        elif c == 'N':
                            value += '❌|'
                        elif c == 'P':
                            value += '☑️|'
                    value += '\n'
                self.add_field(name='🇲|🇹|🇼|🇹|🇫|🇸|🇸', value=value)
                self.add_field(name='_ _', value='✅ = Server running\n'
                                                 '❌ = Server not running\n'
                                                 '☑️ = Server shuts down without players')


class Footer(report.EmbedElement):
    async def render(self, server: Server):
        await report.Ruler(self.env).render()
        text = self.embed.footer.text or ''
        for listener in self.bot.eventListeners:
            # noinspection PyUnresolvedReferences
            if (type(listener).__name__ == 'UserStatisticsEventListener') and \
                    (server.name in listener.statistics):
                text += '\n\nUser statistics are enabled for this server.'
                break
        text += f'\n\nLast updated: {datetime.now(timezone.utc):%y-%m-%d %H:%M:%S UTC}'
        self.embed.set_footer(text=text)


class All(report.EmbedElement):
    async def render(self):
        num = 0
        for server in self.bot.servers.values():
            while server.status == Status.UNREGISTERED:
                await asyncio.sleep(1)
            if server.status == Status.SHUTDOWN:
                continue
            name = f"{server.name} [{len(server.players) + 1}/{server.settings['maxPlayers']}]"
            value = f"IP/Port:  {server.node.public_ip}:{server.settings['port']}\n"
            if server.current_mission:
                value += f"Mission:  {server.current_mission.name}\n"
                value += f"Uptime:   {utils.convert_time(int(server.current_mission.mission_time))}\n"
            if server.restart_time and not server.maintenance:
                restart_in = int((server.restart_time - datetime.now(timezone.utc)).total_seconds())
                value += f"Restart:  in {utils.format_time(restart_in)}\n"
            if server.settings['password']:
                name = '🔐 ' + name
                value += f"Password: {server.settings['password']}"
            else:
                name = '🔓 ' + name
            self.add_field(name=name, value=f"```{value}```", inline=False)
            num += 1
        if num == 0:
            self.add_field(name="_ _", value="There are currently no servers running.")
