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
        self.add_field(name='Map', value=server['current_map'])
        self.add_field(name='Server-IP / Port',
                       value=self.bot.external_ip + ':' + str(server['serverSettings']['port']))
        if len(server['serverSettings']['password']) > 0:
            if show_password:
                self.add_field(name='Password', value=server['serverSettings']['password'])
            else:
                self.add_field(name='Password', value='********')
        else:
            self.add_field(name='Password', value='_ _')
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
        self.add_field(name='Avail. Slots', value='🔹 {}  |  {} 🔸'.format(server['num_slots_blue'] if 'num_slots_blue' in server else '-', server['num_slots_red'] if 'num_slots_red' in server else '-'))
        self.embed.set_footer(text='- Server is running DCS {}\n'.format(server['dcs_version']))


class WeatherInfo(report.EmbedElement):

    def render(self, server: dict):
        if 'weather' in server:
            if 'clouds' in server and 'preset' in server['clouds']:
                self.add_field(name='Preset', value=server['clouds']['preset']['readableNameShort'])
            else:
                self.add_field(name='Weather', value='Dynamic')
            weather = server['weather']
            self.add_field(name='Temperature', value=str(int(weather['season']['temperature'])) + ' °C')
            self.add_field(name='QNH', value='{:.2f} inHg'.format(weather['qnh'] * const.MMHG_IN_INHG))
            self.add_field(name='Wind',
                           value='\u2002Ground: {}° / {} kts\n\u20026600 ft: {}° / {} kts\n26000 ft: {}° / {} kts'.format(
                                int(weather['wind']['atGround']['dir'] + 180) % 360,
                                int(weather['wind']['atGround']['speed']),
                                int(weather['wind']['at2000']['dir'] + 180) % 360,
                                int(weather['wind']['at2000']['speed']),
                                int(weather['wind']['at8000']['dir'] + 180) % 360,
                                int(weather['wind']['at8000']['speed'])))
            if 'clouds' in server:
                if 'preset' in server['clouds']:
                    self.add_field(name='Cloudbase',
                                   value=f'{int(server["clouds"]["base"] * const.METER_IN_FEET):,} ft')
                else:
                    self.add_field(name='Clouds',
                                   value='Base:\u2002\u2002\u2002\u2002 {:,} ft\nDensity:\u2002\u2002 {}/10\nThickness: {:,} ft'.format(
                                        int(server['clouds']['base'] * const.METER_IN_FEET),
                                        server['clouds']['density'],
                                        int(server['clouds']['thickness'] * const.METER_IN_FEET)))
            else:
                self.add_field(name='Clouds', value='n/a')
            visibility = weather['visibility']['distance']
            if weather['enable_fog'] is True:
                visibility = weather['fog']['visibility'] * const.METER_IN_FEET
            self.add_field(name='Visibility', value=f'{int(visibility):,} ft')
            report.Ruler(self.env).render()


class PluginsInfo(report.EmbedElement):

    def render_srs(self, server: dict, param: dict) -> bool:
        if 'SRSSettings' in server:
            show_password = param['show_password'] if 'show_password' in param else True
            if show_password and 'EXTERNAL_AWACS_MODE' in server['SRSSettings'] and \
                    'EXTERNAL_AWACS_MODE_BLUE_PASSWORD' in server['SRSSettings'] and \
                    'EXTERNAL_AWACS_MODE_RED_PASSWORD' in server['SRSSettings'] and \
                    server['SRSSettings']['EXTERNAL_AWACS_MODE'] is True:
                value = '🔹 Pass: {}\n🔸 Pass: {}'.format(
                    server['SRSSettings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD'],
                    server['SRSSettings']['EXTERNAL_AWACS_MODE_RED_PASSWORD'])
            else:
                value = '_ _'
            self.add_field(name='SRS [{}]'.format(
                server['SRSSettings']['SERVER_SRS_PORT']), value=value)
            return True
        else:
            return False

    def render_lotatc(self, server: dict, param: dict) -> bool:
        if 'lotAtcSettings' in server:
            show_password = param['show_password'] if 'show_password' in param else True
            if show_password and 'blue_password' in server['lotAtcSettings'] \
                    and 'red_password' in server['lotAtcSettings']:
                value = '🔹 Pass: {}\n🔸 Pass: {}'.format(
                                server['lotAtcSettings']['blue_password'], server['lotAtcSettings']['red_password'])
            else:
                value = '_ _'
            # TODO: something has changed with 2.2.5?
            if 'port' in server['lotAtcSettings']:
                self.add_field(name='LotAtc [{}]'.format(server['lotAtcSettings']['port']), value=value)
            else:
                self.add_field(name='LotAtc', value=value)
            return True
        else:
            return False

    def render_tacview(self, server: dict, param: dict) -> bool:
        retval = False
        if 'Tacview' in server['options']['plugins']:
            name = 'Tacview'
            if ('tacviewModuleEnabled' in server['options']['plugins']['Tacview'] and
                server['options']['plugins']['Tacview']['tacviewModuleEnabled'] is False) or (
                    'tacviewFlightDataRecordingEnabled' in server['options']['plugins']['Tacview'] and
                    server['options']['plugins']['Tacview']['tacviewFlightDataRecordingEnabled'] is False):
                value = 'disabled'
            else:
                show_password = param['show_password'] if 'show_password' in param else True
                value = ''
                tacview = server['options']['plugins']['Tacview']
                if 'tacviewRealTimeTelemetryEnabled' in tacview and tacview['tacviewRealTimeTelemetryEnabled'] is True:
                    name += ' RT'
                    if show_password and 'tacviewRealTimeTelemetryPassword' in tacview and len(
                            tacview['tacviewRealTimeTelemetryPassword']) > 0:
                        value += 'Password: {}\n'.format(tacview['tacviewRealTimeTelemetryPassword'])
                elif show_password and 'tacviewHostTelemetryPassword' in tacview and len(tacview['tacviewHostTelemetryPassword']) > 0:
                    value += 'Password: "{}"\n'.format(tacview['tacviewHostTelemetryPassword'])
                if 'tacviewRealTimeTelemetryPort' in tacview and len(tacview['tacviewRealTimeTelemetryPort']) > 0:
                    name += ' [{}]'.format(tacview['tacviewRealTimeTelemetryPort'])
                if 'tacviewRemoteControlEnabled' in tacview and tacview['tacviewRemoteControlEnabled'] is True:
                    value += '**Remote Ctrl [{}]**\n'.format(tacview['tacviewRemoteControlPort'])
                    if show_password and 'tacviewRemoteControlPassword' in tacview and len(tacview['tacviewRemoteControlPassword']) > 0:
                        value += 'Password: {}'.format(tacview['tacviewRemoteControlPassword'])
                if len(value) == 0:
                    value = 'enabled'
                    retval = True
            self.add_field(name=name, value=value)
            return retval

    def render(self, server: dict, params: List[dict]):
        plugins = []
        for param in params:
            if param['plugin'] == 'SRS':
                if self.render_srs(server, param):
                    plugins.append('SRS')
            elif param['plugin'] == 'LotAtc':
                if self.render_lotatc(server, param):
                    plugins.append('LotAtc')
            elif param['plugin'] == 'Tacview':
                if self.render_tacview(server, param):
                    plugins.append('Tacview')
        if len(plugins) > 0:
            footer = '- The IP address of '
            if len(plugins) == 1:
                footer += plugins[0]
            else:
                footer += ', '.join(plugins[0:len(plugins) - 1]) + ' and ' + plugins[len(plugins) - 1]
            footer += ' is the same as the server.\n'
            self.embed.set_footer(text=self.embed.footer.text + footer)


class Footer(report.EmbedElement):
    def render(self, server: dict):
        for listener in self.bot.eventListeners:
            if (type(listener).__name__ == 'UserStatisticsEventListener') and \
                    (server['server_name'] in listener.statistics):
                self.embed.set_footer(text=self.embed.footer.text + '- User statistics are enabled for this server.')
