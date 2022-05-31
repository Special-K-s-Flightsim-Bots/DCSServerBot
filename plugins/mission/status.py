from core import const, report
from core.const import Status
from datetime import datetime, timedelta
from typing import List, Optional


class Init(report.EmbedElement):
    def render(self, server: dict, num_players: int):
        self.embed.set_author(
            name=f"{server['server_name']} [{num_players}/{server['serverSettings']['maxPlayers']}]",
            icon_url=const.STATUS_IMG[server['status']])
        if server['status'] in [Status.PAUSED, Status.RUNNING]:
            self.embed.description = f"Mission: \"{server['current_mission']}\""
        else:
            self.embed.description = f"_{server['status'].value}_"
        self.embed.set_footer(text='')


class ServerInfo(report.EmbedElement):

    def render(self, server: dict, show_password: Optional[bool] = True):
        self.add_field(name='Map', value=server['current_map'] if 'current_map' in server else 'n/a')
        self.add_field(name='Server-IP / Port',
                       value=self.bot.external_ip + ':' + str(server['serverSettings']['port']))
        if len(server['serverSettings']['password']) > 0:
            if show_password:
                self.add_field(name='Password', value=server['serverSettings']['password'])
            else:
                self.add_field(name='Password', value='********')
        else:
            self.add_field(name='Password', value='_ _')
        if 'mission_time' in server:
            uptime = int(server['mission_time'])
            self.add_field(name='Runtime', value=str(timedelta(seconds=uptime)))
            if 'start_time' in server:
                if server['date']['Year'] >= 1970:
                    date = datetime(server['date']['Year'], server['date']['Month'],
                                    server['date']['Day'], 0, 0).timestamp()
                    real_time = date + server['start_time'] + uptime
                    value = str(datetime.fromtimestamp(real_time))
                else:
                    value = '{}-{:02d}-{:02d} {}'.format(server['date']['Year'], server['date']['Month'],
                                                         server['date']['Day'],
                                                         timedelta(seconds=server['start_time'] + uptime))
            else:
                value = '-'
            self.add_field(name='Date/Time in Mission', value=value)
            if not self.bot.config.getboolean(server['installation'], 'COALITIONS'):
                self.add_field(name='Avail. Slots', value='🔹 {}  |  {} 🔸'.format(server['num_slots_blue'] if 'num_slots_blue' in server else '-', server['num_slots_red'] if 'num_slots_red' in server else '-'))
            else:
                self.add_field(name='Coalitions', value='Yes')
        self.embed.set_footer(text='- Server is running DCS {}'.format(server['dcs_version']))


class WeatherInfo(report.EmbedElement):

    def render(self, server: dict):
        if 'weather' in server:
            weather = server['weather']
            self.add_field(name='Temperature', value=str(int(weather['season']['temperature'])) + ' °C')
            self.add_field(name='QNH', value='{:.2f} inHg'.format(weather['qnh'] * const.MMHG_IN_INHG))
            if 'clouds' in server and 'preset' in server['clouds']:
                self.add_field(name='Clouds', value=server['clouds']['preset']['readableName'][5:].split('\n')[0].replace('/', '/\n'))
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
            if 'clouds' in server:
                if 'preset' in server['clouds']:
                    self.add_field(name='Cloudbase',
                                   value=f'{int(server["clouds"]["base"] * const.METER_IN_FEET + 0.5):,} ft')
                else:
                    self.add_field(name='Clouds',
                                   value='Base:\u2002\u2002\u2002\u2002 {:,} ft\nDensity:\u2002\u2002 {}/10\nThickness: {:,} ft'.format(
                                        int(server['clouds']['base'] * const.METER_IN_FEET + 0.5),
                                        server['clouds']['density'],
                                        int(server['clouds']['thickness'] * const.METER_IN_FEET + 0.5)))
            else:
                self.add_field(name='Clouds', value='n/a')
            visibility = weather['visibility']['distance']
            if weather['enable_fog'] is True:
                visibility = int(weather['fog']['visibility'] * const.METER_IN_FEET + 0.5)
            self.add_field(name='Visibility', value=f'{visibility:,} ft')
            report.Ruler(self.env).render()


class ExtensionsInfo(report.EmbedElement):

    def render(self, server: dict):
        # we don't have any extensions loaded (yet)
        if 'extensions' not in server:
            return
        extensions = server['extensions']
        footer = self.embed.footer.text
        for ext in extensions.values():
            ext.render(self)
            footer += f", {ext.name} {ext.version}"
        if len(extensions):
            ext_names = list(extensions.keys())
            footer += '\n- The IP address of '
            if len(ext_names) == 1:
                footer += ext_names[0]
            else:
                footer += ', '.join(ext_names[0:-1]) + ' and ' + ext_names[-1]
            footer += ' is the same as the server.\n'
        self.embed.set_footer(text=footer)


class Footer(report.EmbedElement):
    def render(self, server: dict):
        for listener in self.bot.eventListeners:
            if (type(listener).__name__ == 'UserStatisticsEventListener') and \
                    (server['server_name'] in listener.statistics):
                self.embed.set_footer(text=self.embed.footer.text + '- User statistics are enabled for this server.')
