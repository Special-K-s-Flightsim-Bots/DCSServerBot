import os
import re
import string
import sys
import uuid
from core import EventListener, Server, Player, Channel, Side, Plugin, PersistentReport, event
from matplotlib import pyplot as plt
from pathlib import Path
from plugins.creditsystem.player import CreditPlayer
from plugins.greenieboard import get_element
from typing import Optional, cast


class GreenieBoardEventListener(EventListener):

    EVENT_TEXTS = {
        Side.BLUE: {
            'waveoff': '```ansi\n\u001b[0;34mBLUE player {} waved off from carrier {}.```',
            'bolter': '```ansi\n\u001b[0;34mBLUE player {} boltered from carrier {}.```',
            'landing': '```ansi\n\u001b[0;34mBLUE player {} landed on carrier {} with grade {} / {}.```'
        },
        Side.RED: {
            'waveoff': '```ansi\n\u001b[0;31mRED player {} waved off from carrier {}.```',
            'bolter': '```ansi\n\u001b[0;31mRED player {} boltered from carrier {}.```',
            'landing': '```ansi\n\u001b[0;31mRED player {} landed on carrier {} with grade {} / {}.```'
        }
    }

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        config = self.get_config()
        if 'FunkMan' in config:
            sys.path.append(config['FunkMan']['install'])
            from funkman.funkplot.funkplot import FunkPlot
            self.funkplot = FunkPlot(ImagePath=config['FunkMan']['IMAGEPATH'])

    async def update_greenieboard(self, server: Server):
        # update the server specific board
        config = self.plugin.get_config(server)
        if 'persistent_channel' in config and config.get('persistent_board', True):
            channel_id = int(config['persistent_channel'])
            num_rows = config.get('num_rows', 10)
            report = PersistentReport(self.bot, self.plugin_name, 'greenieboard.json',
                                      embed_name='greenieboard', server=server, channel_id=channel_id)
            await report.render(server_name=server.name, num_rows=num_rows)
        # update the global board
        config = self.get_config()
        if 'persistent_channel' in config and config.get('persistent_board', True):
            num_rows = config.get('num_rows', 10)
            report = PersistentReport(self.bot, self.plugin_name, 'greenieboard.json', embed_name='greenieboard',
                                      channel_id=int(config['persistent_channel']))
            await report.render(server_name=None, num_rows=num_rows)

    async def send_chat_message(self, player: Player, data: dict):
        server: Server = self.bot.servers[data['server_name']]
        events_channel = self.bot.get_channel(server.channels[Channel.EVENTS])
        if events_channel is not None:
            carrier = data['place']['name']
            if 'WO' in data['grade']:
                await events_channel.send(self.EVENT_TEXTS[player.side]['waveoff'].format(player.name, carrier))
            elif data['grade'] == 'B':
                await events_channel.send(self.EVENT_TEXTS[player.side]['bolter'].format(player.name, carrier))
            else:
                details = data['details']
                if 'wire' in data and data['wire']:
                    details += f" WIRE# {data['wire']}"
                await events_channel.send(self.EVENT_TEXTS[player.side]['landing'].format(
                    player.name, carrier, data['grade'].replace('_', '\\_'), details))

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        try:
            await self.update_greenieboard(server)
        except FileNotFoundError as ex:
            self.log.error(f'  => File not found: {ex}')

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        # make sure the config cache is re-read on mission changes
        self.plugin.get_config(server, use_cache=False)

    def process_lso_event(self, config: dict, server: Server, player: Player, data: dict):
        time = (int(server.current_mission.start_time) + int(data['time'])) % 86400
        night = time > 20 * 3600 or time < 6 * 3600
        points = data.get('points', config['ratings'][data['grade']])
        if config.get('credits', False):
            cp: CreditPlayer = cast(CreditPlayer, player)
            cp.audit('Landing', cp.points, f"Landing on {data['place']} with grade {data['grade']}.")
            cp.points += points
        case = data.get('case', 1 if not night else 3)
        wire = data.get('wire')
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO greenieboard (mission_id, player_ucid, unit_type, grade, comment, place, trapcase, 
                                              wire, night, points, trapsheet) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (server.mission_id, player.ucid, player.unit_type, data['grade'].strip(), data['details'],
                      data['place']['name'], case, wire, night, points,
                      data.get('trapsheet')))

    @staticmethod
    def normalize_airboss_lso_rating(grade: str) -> Optional[str]:
        if '<SH>' in grade:
            grade = grade[:-4]
        if grade == 'CUT':
            grade = 'C'
        elif 'BOLTER' in grade:
            grade = 'B'
        elif grade == 'WOP':
            grade = 'WO'
        return grade

    def get_trapsheet(self, config: dict, server: Server, player: Player, data: dict) -> Optional[str]:
        dirname = os.path.join(server.instance.home, config['Moose.AIRBOSS']['basedir'])
        carrier = data['place']['name'].split()[0]
        if 'trapsheet' not in data:
            name = re.sub(f"[{string.punctuation}]", "", player.name).strip()
            filename = config['Moose.AIRBOSS']['trapsheets'].format(
                carrier=carrier, name=name, unit_type=player.unit_type, number='*')
        else:
            filename = data['trapsheet'] + "_{unit_type}*.csv".format(unit_type=player.unit_type)
        p = Path(dirname)
        try:
            return max(p.glob(filename), key=lambda x: x.stat().st_mtime).__str__()
        except Exception as ex:
            self.log.exception(ex)
            self.log.error(f'GreenieBoard: No trapsheet with pattern ({filename}) could be found!')
            return None

    def process_airboss_event(self, config: dict, server: Server, player: Player, data: dict):
        data['grade'] = self.normalize_airboss_lso_rating(data['grade'])
        if not data['grade'].startswith("WO"):
            data['trapsheet'] = self.get_trapsheet(config, server, player, data)
        self.process_lso_event(config, server, player, data)

    def process_sc_event(self, config: dict, server: Server, player: Player, data: dict):
        data['details'] = get_element(data['comment'], 'details')
        data['grade'] = get_element(data['comment'], 'grade').replace('---', '--')
        data['wire'] = get_element(data['comment'], 'wire')
        self.process_lso_event(config, server, player, data)

    def process_funkman_event(self, config: dict, server: Server, player: Player, data: dict):
        if 'FunkMan' not in config:
            self.log.warning("Can't process FunkMan event as FunkMan is not configured in your greenieboard.json!")
            return
        if not data['grade'].startswith('WO'):
            filepath = os.path.join(server.instance.home,
                                    config['FunkMan']['basedir'] if 'basedir' in config['FunkMan'] else 'trapsheets')
            if not os.path.exists(filepath):
                os.mkdir(filepath)
            try:
                filename = filepath + os.path.sep + f'{uuid.uuid4()}.png'
                fig, _ = self.funkplot.PlotTrapSheet(data)
                fig.savefig(filename, bbox_inches='tight', facecolor='#2C2F33')
                plt.close(fig)
                data['trapsheet'] = filename
            except TypeError:
                self.log.error("No trapsheet data received from DCS!")
        else:
            del data['trapsheet']
        data['grade'] = self.normalize_airboss_lso_rating(data['grade'])
        data['place'] = {
            'name': data['carriername']
        }
        data['time'] = sum(x * int(t) for x, t in zip([3600, 60, 1], data['mitime'].split(":"))) - int(server.current_mission.start_time)
        self.process_lso_event(config, server, player, data)

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        if 'initiator' not in data:
            return
        config = self.plugin.get_config(server)
        # ignore SC / Moose.AIRBOSS events, if FunkMan is enabled
        if 'FunkMan' in config:
            return
        player: Player = server.get_player(name=data['initiator']['name']) if 'name' in data['initiator'] else None
        if player:
            update = False
            if 'Moose.AIRBOSS' in config:
                if server.is_remote:
                    self.log.warning('Moose.AIRBOSS is not supported on remote servers. '
                                     'Please use the Funkman protocol instead.')
                    return
                if data['eventName'] == 'S_EVENT_AIRBOSS':
                    self.process_airboss_event(config, server, player, data)
                    update = True
            elif data['eventName'] == 'S_EVENT_LANDING_QUALITY_MARK':
                self.process_sc_event(config, server, player, data)
                update = True
            if update:
                await self.send_chat_message(player, data)
                await self.update_greenieboard(server)

    @event(name="moose_lso_grade")
    async def moose_lso_grade(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        player: Player = server.get_player(name=data['name']) if 'name' in data else None
        if player:
            self.process_funkman_event(config, server, player, data)
            await self.send_chat_message(player, data)
            await self.update_greenieboard(server)
