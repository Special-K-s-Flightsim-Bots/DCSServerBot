import asyncio

from core import const, report, Status, Server, utils, ServiceRegistry, Plugin, Side, cache_with_expiration
from datetime import datetime, timedelta, timezone
from services.bot import BotService
from typing import Optional, cast
from zoneinfo import ZoneInfo

STATUS_IMG = {
    Status.LOADING:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/loading_256.png?raw=true',
    Status.PAUSED:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/pause_256.png?raw=true',
    Status.RUNNING:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/play_256.png?raw=true',
    Status.STOPPED:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/stop_256.png?raw=true',
    Status.SHUTTING_DOWN:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/shutting_down_256.png?raw=true',
    Status.SHUTDOWN:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/development/images/shutdown_256.png?raw=true',
    Status.UNREGISTERED:
        'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/development/images/unregistered_256.png?raw=true'
}


class Init(report.EmbedElement):
    async def render(self, server: Server):
        num_players = len(server.get_active_players()) + 1
        self.embed.set_author(
            name=f"{server.name} [{num_players}/{server.settings.get('maxPlayers', 16)}]",
            icon_url=STATUS_IMG[server.status])
        if server.status in [Status.PAUSED, Status.RUNNING] and server.current_mission:
            self.embed.description = f"Mission: \"{server.current_mission.display_name}\""
        else:
            self.embed.description = f"_{server.status.value}_"
        self.embed.set_footer(text='')


class ServerInfo(report.EmbedElement):

    async def render(self, server: Server, show_password: Optional[bool] = True, host: Optional[str] = None,):
        if not server.locals.get('show_passwords', True):
            show_password = False
        name = value = ""
        if server.node.public_ip:
            name = "Server-IP / Port"
            value = f"{host or server.node.public_ip}:{server.settings.get('port', 10308)}"
        if server.settings.get('password', ''):
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

        # add a ruler at the bottom
        await report.Ruler(self.env).render()

        if server.maintenance:
            footer = 'SERVER IS IN MAINTENANCE MODE, SCHEDULER WILL NOT WORK!\n\n'
        else:
            footer = ''
        if server.node.dcs_version:
            footer += f'DCS {server.node.dcs_version} | DCSServerBot {self.node.bot_version}.{self.node.sub_version} | '
        self.embed.set_footer(text=footer)


@cache_with_expiration(expiration=300)
async def get_visibility(server: Server) -> int:
    try:
        ret = await server.send_to_dcs_sync({
            "command": "getFog"
        })
        if ret['visibility']:
            return int(ret['visibility'])
    except (TimeoutError, asyncio.TimeoutError):
        pass
    return 0


class WeatherInfo(report.EmbedElement):

    async def render(self, server: Server):
        if server.current_mission and server.current_mission.weather:
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
                elif 'density' in clouds and clouds['density'] == 0:
                    value = "Clear"
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
            if server.status == Status.RUNNING:
                visibility = (await get_visibility(server)) or visibility
            value = "{:,} m / {:.2f} SM".format(int(visibility), visibility / const.METERS_IN_SM) \
                if visibility < 30000 else "10 km / 6 SM (+)"
            value += ("\n\n**Wind**\n"
                      "\u2002Ground: {}° / {} kts\n\u20026600 ft: {}° / {} kts\n26000 ft: {}° / {} kts").format(
                int(weather['wind']['atGround']['dir'] + 180) % 360,
                int(weather['wind']['atGround']['speed'] * const.METER_PER_SECOND_IN_KNOTS + 0.5),
                int(weather['wind']['at2000']['dir'] + 180) % 360,
                int(weather['wind']['at2000']['speed'] * const.METER_PER_SECOND_IN_KNOTS + 0.5),
                int(weather['wind']['at8000']['dir'] + 180) % 360,
                int(weather['wind']['at8000']['speed'] * const.METER_PER_SECOND_IN_KNOTS + 0.5))
            self.add_field(name='Visibility', value=value)

            # add a ruler at the bottom
            await report.Ruler(self.env).render()


class IntegrityCheck(report.EmbedElement):

    async def render(self, server: Server):
        values = []
        if not server.settings.get('advanced', {}).get('allow_trial_only_clients', False):
            values.append("No Trial Clients")
        if server.settings.get('require_pure_clients', True):
            values.append("Pure Clients Required")
        if server.settings.get('require_pure_scripts', False):
            values.append("Pure Scripts Required")
        if server.settings.get('require_pure_models', True):
            values.append("Pure Models Required")
        if server.settings.get('require_pure_textures', True):
            values.append("Pure Textures Required")
        if values:
            self.add_field(name='Client Limits', value='\n'.join([f":shield: {x}" for x in values]))


class ExtensionsInfo(report.EmbedElement):

    async def render(self, server: Server):
        extensions = await server.render_extensions() if server.status in [Status.RUNNING, Status.PAUSED] else None
        # we don't have any extensions loaded (yet)
        if not extensions:
            return
        footer = self.embed.footer.text or ''
        for ext in extensions:
            self.add_field(name=ext['name'], value=ext['value'])
        footer += " | ".join([f"{ext['name']} v{ext['version']}" for ext in extensions if ext.get('version')])
        self.embed.set_footer(text=footer)
        # add a ruler at the bottom
        await report.Ruler(self.env).render()


class ScheduleInfo(report.EmbedElement):

    async def render(self, server: Server):
        bot = ServiceRegistry.get(BotService).bot
        scheduler: Plugin = cast(Plugin, bot.cogs.get('Scheduler'))
        if scheduler:
            config = scheduler.get_config(server)
            if 'schedule' in config:
                if (len(config['schedule']) == 1 and list(config['schedule'].keys())[0] == '00-24' and
                        config['schedule']['00-24'] == 'YYYYYYY'):
                    return
                self.add_field(name="This server runs on the following schedule:", value='_ _', inline=False)
                value = ''
                now = datetime.now()
                tz = now.astimezone().tzinfo
                for period, daystate in config['schedule'].items():
                    if period == 'timezone':
                        tz = ZoneInfo(daystate)
                        continue
                    for c in daystate:
                        if c == 'Y':
                            value += '✅|'
                        elif c == 'N':
                            value += '❌|'
                        elif c == 'P':
                            value += '☑️|'
                    value += '\n'
                now = now.replace(tzinfo=tz)
                hours, rem = divmod(tz.utcoffset(now).total_seconds(), 3600)
                minutes, _ = divmod(rem, 60)
                if hours == 0 and minutes == 0:
                    name = 'Time (UTC)'
                else:
                    sign = '+' if hours >= 0 else '-'
                    name = f'Time (UTC{sign}{int(abs(hours)):02d}:{int(minutes):02d})'
                self.add_field(name=name, value='\n'.join([x for x in config['schedule'].keys() if x != 'timezone']))
                self.add_field(name='🇲|🇹|🇼|🇹|🇫|🇸|🇸', value=value)
                self.add_field(name='_ _', value='✅ = Server running\n'
                                                 '❌ = Server not running\n'
                                                 '☑️ = Server shuts down without players')
                # add a ruler at the bottom
                await report.Ruler(self.env).render()


class Footer(report.EmbedElement):
    async def render(self, server: Server):
        text = self.embed.footer.text or ''
        for listener in self.bot.eventListeners:
            # noinspection PyUnresolvedReferences
            if (type(listener).__name__ == 'UserStatisticsEventListener') and \
                    (server.name in listener.active_servers):
                text += '\n\n- User statistics are enabled.'
                break
        current_mission = await server.get_current_mission_file()
        if current_mission and current_mission.endswith('.sav'):
            text += '\n- Mission persistence is enabled.'
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
            name = f"{server.name} [{len(server.players) + 1}/{server.settings.get('maxPlayers', 16)}]"
            value = f"IP/Port:  {server.node.public_ip}:{server.settings.get('port', 10308)}\n"
            if server.current_mission:
                value += f"Mission:  {server.current_mission.name}\n"
                value += f"Uptime:   {utils.convert_time(int(server.current_mission.mission_time))}\n"
            if server.restart_time and not server.maintenance:
                restart_in = int((server.restart_time - datetime.now(timezone.utc)).total_seconds())
                value += f"Restart:  in {utils.format_time(restart_in)}\n"
            if server.settings.get('password', ''):
                name = '🔐 ' + name
                value += f"Password: {server.settings['password']}"
            else:
                name = '🔓 ' + name
            self.add_field(name=name, value=f"```{value}```", inline=False)
            num += 1
        if num == 0:
            self.add_field(name="_ _", value="There are currently no servers running.")
