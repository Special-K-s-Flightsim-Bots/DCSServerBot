from contextlib import suppress
from core import const, report, Status, Server
from datetime import datetime, timedelta
from typing import Optional


STATUS_IMG = {
    Status.LOADING: 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/loading_256.png?raw=true',
    Status.PAUSED: 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/pause_256.png?raw=true',
    Status.RUNNING: 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/play_256.png?raw=true',
    Status.STOPPED: 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/stop_256.png?raw=true',
    Status.SHUTDOWN: 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/stop_256.png?raw=true'
}


class Init(report.EmbedElement):
    def render(self, server: Server, num_players: int):
        self.embed.set_author(
            name=f"{server.name} [{num_players}/{server.settings['maxPlayers']}]",
            icon_url=STATUS_IMG[server.status])
        if server.status in [Status.PAUSED, Status.RUNNING] and server.current_mission:
            self.embed.description = f"Mission: \"{server.current_mission.name}\""
        else:
            self.embed.description = f"_{server.status.value}_"
        self.embed.set_footer(text='')


class ServerInfo(report.EmbedElement):

    def render(self, server: Server, show_password: Optional[bool] = True):
        self.add_field(name='Map', value=server.current_mission.map if server.current_mission else 'n/a')
        self.add_field(name='Server-IP / Port',
                       value=self.bot.external_ip + ':' + str(server.settings['port']))
        if len(server.settings['password']) > 0:
            if show_password:
                self.add_field(name='Password', value=server.settings['password'])
            else:
                self.add_field(name='Password', value='********')
        else:
            self.add_field(name='Password', value='_ _')
        if server.current_mission:
            uptime = int(server.current_mission.mission_time)
            self.add_field(name='Runtime', value=str(timedelta(seconds=uptime)))
            if isinstance(server.current_mission.date, datetime):
                date = server.current_mission.date.timestamp()
                real_time = date + server.current_mission.start_time + uptime
                value = str(datetime.fromtimestamp(real_time))
            else:
                value = '{} {}'.format(server.current_mission.date,
                                       timedelta(seconds=server.current_mission.start_time + uptime))
            self.add_field(name='Date/Time in Mission', value=value)
            if not self.bot.config.getboolean(server.installation, 'COALITIONS'):
                self.add_field(name='Avail. Slots',
                               value=f'🔹 {server.current_mission.num_slots_blue}  |  '
                                     f'{server.current_mission.num_slots_red} 🔸')
            else:
                self.add_field(name='Coalitions', value='Yes')
        if server.maintenance:
            footer = 'SERVER IS IN MAINTENANCE MODE, SCHEDULER WILL NOT WORK!\n\n'
        else:
            footer = ''
        footer += f'- Server is running DCS {server.dcs_version}'
        self.embed.set_footer(text=footer)


class WeatherInfo(report.EmbedElement):

    def render(self, server: Server):
        if server.current_mission and server.current_mission.weather:
            weather = server.current_mission.weather
            self.add_field(name='Temperature', value=str(int(weather['season']['temperature'])) + ' °C')
            self.add_field(name='QNH', value='{:.2f} inHg'.format(weather['qnh'] * const.MMHG_IN_INHG))
            if server.current_mission.clouds and 'preset' in server.current_mission.clouds:
                self.add_field(name='Clouds', value=server.current_mission.clouds['preset']['readableName'][5:].split('\n')[0].replace('/', '/\n'))
            else:
                self.add_field(name='Weather', value='Dynamic')
            self.add_field(name='Wind',
                           value='\u2002Ground: {}° / {} kts\n\u20026600 ft: {}° / {} kts\n26000 ft: {}° / {} kts'.format(
                                int(weather['wind']['atGround']['dir'] + 180) % 360,
                                int(weather['wind']['atGround']['speed'] * const.METER_PER_SECOND_IN_KNOTS + 0.5),
                                int(weather['wind']['at2000']['dir'] + 180) % 360,
                                int(weather['wind']['at2000']['speed'] * const.METER_PER_SECOND_IN_KNOTS + 0.5),
                                int(weather['wind']['at8000']['dir'] + 180) % 360,
                                int(weather['wind']['at8000']['speed'] * const.METER_PER_SECOND_IN_KNOTS + 0.5)))
            if server.current_mission.clouds:
                clouds = server.current_mission.clouds
                if 'preset' in clouds:
                    self.add_field(name='Cloudbase',
                                   value=f'{int(clouds["base"] * const.METER_IN_FEET + 0.5):,} ft')
                else:
                    self.add_field(name='Clouds',
                                   value='Base:\u2002\u2002\u2002\u2002 {:,} ft\nDensity:\u2002\u2002 {}/10\nThickness: {:,} ft'.format(
                                        int(clouds['base'] * const.METER_IN_FEET + 0.5),
                                        clouds['density'],
                                        int(clouds['thickness'] * const.METER_IN_FEET + 0.5)))
            else:
                self.add_field(name='Clouds', value='n/a')
            visibility = weather['visibility']['distance']
            if weather['enable_fog'] is True:
                visibility = int(weather['fog']['visibility'] * const.METER_IN_FEET + 0.5)
            self.add_field(name='Visibility', value=f'{visibility:,} ft')


class ExtensionsInfo(report.EmbedElement):

    def render(self, server: Server):
        # we don't have any extensions loaded (yet)
        if len(server.extensions) == 0:
            return
        report.Ruler(self.env).render()
        for ext in server.extensions.values():
            with suppress(Exception):
                ext.render(self)


class Footer(report.EmbedElement):
    def render(self, server: Server):
        text = self.embed.footer.text
        for listener in self.bot.eventListeners:
            if (type(listener).__name__ == 'UserStatisticsEventListener') and \
                    (server.name in listener.statistics):
                text += '\n- User statistics are enabled for this server.'
                break
        text += f'\n\nLast updated: {datetime.now():%y-%m-%d %H:%M:%S}'
        self.embed.set_footer(text=text)
