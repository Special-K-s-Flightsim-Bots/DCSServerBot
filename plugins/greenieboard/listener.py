import asyncio
import io
import os
import psycopg
import re
import string
import sys

from core import EventListener, Server, Player, Channel, Side, PersistentReport, event, get_translation, utils
from matplotlib import pyplot as plt
from pathlib import Path
from plugins.creditsystem.player import CreditPlayer
from plugins.greenieboard import get_element, GRADES
from contextlib import suppress
from typing import cast, TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import GreenieBoard

_ = get_translation(__name__.split('.')[1])


class GreenieBoardEventListener(EventListener["GreenieBoard"]):

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

    def __init__(self, plugin: "GreenieBoard"):
        super().__init__(plugin)
        config = self.get_config()
        if 'FunkMan' in config:
            path = config['FunkMan']['install']
            if not os.path.exists(path):
                self.log.error(f"FunkMan install path is not correct in your {self.plugin_name}.yaml! "
                               f"FunkMan will not work.")
                return
            sys.path.append(path)
            from funkman.funkplot.funkplot import FunkPlot
            self.funkplot = FunkPlot(ImagePath=config['FunkMan']['IMAGEPATH'])
        else:
            self.funkplot = None

    async def update_greenieboard(self, server: Server):
        try:
            # update the server specific board
            config = self.plugin.get_config(server)
            if config.get('persistent_board', False):
                channel_id = int(config.get('persistent_channel', server.channels[Channel.STATUS]))
                num_rows = config.get('num_rows', 10)
                num_landings = config.get('num_landings', 30)
                theme = config.get('theme', 'dark')
                landings_rtl = config.get('landings_rtl', True)
                report = PersistentReport(self.bot, self.plugin_name, 'greenieboard.json',
                                          embed_name='greenieboard', server=server, channel_id=channel_id)
                await report.render(server_name=server.name, num_rows=num_rows, num_landings=num_landings, theme=theme,
                                    landings_rtl=landings_rtl)
                squadrons = config.get('squadrons', [])
                if squadrons:
                    for squadron in squadrons:
                        row = utils.get_squadron(self.node, name=squadron['name'])
                        if not row:
                            self.log.warning(f"Squadron {squadron['name']} not found!")
                            continue
                        report = PersistentReport(self.bot, self.plugin_name, 'greenieboard.json',
                                                  embed_name=f"greenieboard_s{row['id']}", server=server,
                                                  channel_id=squadron.get('channel', channel_id))
                        await report.render(server_name=server.name, num_rows=num_rows, num_landings=num_landings,
                                            theme=theme, landings_rtl=landings_rtl, squadron=row)
            # update the global board
            config = self.get_config()
            if 'persistent_channel' in config and config.get('persistent_board', False):
                channel_id = int(config.get('persistent_channel'))
                num_rows = config.get('num_rows', 10)
                num_landings = config.get('num_landings', 30)
                theme = config.get('theme', 'dark')
                landings_rtl = config.get('landings_rtl', True)
                report = PersistentReport(self.bot, self.plugin_name, 'greenieboard.json',
                                          embed_name='greenieboard', channel_id=channel_id)
                await report.render(server_name=None, num_rows=num_rows, num_landings=num_landings, theme=theme,
                                    landings_rtl=landings_rtl)
                squadrons = config.get('squadrons', [])
                if squadrons:
                    for squadron in squadrons:
                        row = utils.get_squadron(self.node, name=squadron['name'])
                        if not row:
                            self.log.warning(f"Squadron {squadron['name']} not found!")
                            continue
                        report = PersistentReport(self.bot, self.plugin_name, 'greenieboard.json',
                                                  embed_name=f"greenieboard_s{row['id']}",
                                                  channel_id=squadron.get('channel', channel_id))
                        await report.render(server_name=None, num_rows=num_rows, num_landings=num_landings,
                                            theme=theme, landings_rtl=landings_rtl, squadron=row)
        except FileNotFoundError as ex:
            self.log.error(f'  => File not found: {ex}')
        except Exception as ex:
            self.log.exception(ex)

    async def send_chat_message(self, player: Player, data: dict):
        server: Server = self.bot.servers[data['server_name']]
        events_channel = self.bot.get_channel(server.channels.get(Channel.EVENTS, -1))
        if events_channel is not None:
            carrier = data['place']['name'] if 'place' in data else 'n/a'
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
    async def registerDCSServer(self, server: Server, _: dict) -> None:
        config = self.get_config(server)
        if 'persistent_channel' in config:
            self.bot.check_channel(int(config.get('persistent_channel')))
        for squadron in config.get('squadrons', []):
            if 'channel' in squadron:
                self.bot.check_channel(int(squadron['channel']))
        asyncio.create_task(self.update_greenieboard(server))

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, _: dict) -> None:
        # make sure the config cache is re-read on mission changes
        self.plugin.get_config(server, use_cache=False)

    async def process_lso_event(self, config: dict, server: Server, player: Player, data: dict):
        time = (int(server.current_mission.start_time) + int(data['time'])) % 86400
        night = time > 20 * 3600 or time < 6 * 3600
        grades = GRADES | config.get('grades', {})
        points = int(data.get('points', grades.get(data['grade'], {}).get('rating', 0)))
        # map some events to NC
        if data['grade'] in ['WOP', 'OWO', 'TWO', 'TLU']:
            data['grade'] = 'NC'
        elif data['grade'] == 'WOFD':
            data['grade'] = 'WO'
        # Moose.AIRBOSS sometimes gives negative points for WO. That is not according to any standard.
        # After SME consultation, any WO will give the WO points (typically 1.0).
        if points < 0 and data['grade'] == 'WO':
            points = grades['WO']['rating']
        if config.get('credits', False):
            cp: CreditPlayer = cast(CreditPlayer, player)
            cp.audit(_('Carrier Landing'), cp.points,
                     _("Landing on {place} with grade {grade}.").format(place=data['place'], grade=data['grade']))
            cp.points += points
        case = data.get('case', 1 if not night else 3)
        wire = data.get('wire')
        async with self.apool.connection() as conn:
            await conn.execute("""
                INSERT INTO traps (mission_id, player_ucid, unit_type, grade, comment, place, trapcase, wire, 
                                   night, points, trapsheet) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (server.mission_id, player.ucid, player.unit_type, data['grade'].strip(), data['details'],
                  data['place']['name'], case, wire, night, points, psycopg.Binary(data.get('trapsheet'))))

    @staticmethod
    def normalize_airboss_lso_rating(grade: str) -> str | None:
        if '<SH>' in grade:
            grade = grade[:-4]
        if grade == 'CUT':
            grade = 'C'
        elif 'BOLTER' in grade:
            grade = 'B'
        elif grade == 'WOP':
            grade = 'WO'
        return grade

    def get_trapsheet(self, config: dict, server: Server, player: Player, data: dict) -> str | None:
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

    async def process_airboss_event(self, config: dict, server: Server, player: Player, data: dict):
        data['grade'] = self.normalize_airboss_lso_rating(data['grade'])
        if not data['grade'].startswith("WO"):
            filename = self.get_trapsheet(config, server, player, data)
            if filename and os.path.exists(filename):
                data['trapsheet'] = self.plugin.plot_trapheet(filename)
                with suppress(Exception):
                    os.remove(filename)
            else:
                data.pop('trapsheet', None)
        else:
            data.pop('trapsheet', None)
        await self.process_lso_event(config, server, player, data)

    async def process_sc_event(self, config: dict, server: Server, player: Player, data: dict):
        data['details'] = get_element(data['comment'], 'details')
        data['grade'] = get_element(data['comment'], 'grade').replace('---', '--')
        data['wire'] = get_element(data['comment'], 'wire')
        await self.process_lso_event(config, server, player, data)

    async def process_funkman_event(self, config: dict, server: Server, player: Player, data: dict):
        if 'FunkMan' not in config:
            self.log.warning(
                f"Can't process FunkMan event as FunkMan is not configured in your {self.plugin_name}.yaml!")
            return
        try:
            fig, _ = self.funkplot.PlotTrapSheet(data)
            buf = io.BytesIO()
            fig.savefig(buf, bbox_inches='tight', facecolor='#2C2F33')
            data['trapsheet'] = buf.getvalue()
            buf.close()
            plt.close(fig)
        except TypeError:
            self.log.warning("No trapsheet data received from DCS!")
            data.pop('trapsheet', None)
        data['grade'] = self.normalize_airboss_lso_rating(data['grade'])
        data['place'] = {
            'name': data['carriername']
        }
        data['time'] = sum(x * int(t) for x, t in zip([3600, 60, 1], data['mitime'].split(":"))) - int(server.current_mission.start_time)
        await self.process_lso_event(config, server, player, data)

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        if 'initiator' not in data:
            return
        config = self.plugin.get_config(server)
        # ignore SC / Moose.AIRBOSS events, if FunkMan is enabled
        if 'FunkMan' in config and config['FunkMan'].get('enabled', True):
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
                    await self.process_airboss_event(config, server, player, data)
                    update = True
            elif data['eventName'] == 'S_EVENT_LANDING_QUALITY_MARK':
                await self.process_sc_event(config, server, player, data)
                update = True
            if update:
                asyncio.create_task(self.send_chat_message(player, data))
                asyncio.create_task(self.update_greenieboard(server))

    @event(name="moose_lso_grade")
    async def moose_lso_grade(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        player: Player = server.get_player(name=data['name']) if 'name' in data else None
        if player:
            if not self.funkplot:
                self.log.error(f"Your FunkMan path is not set in your {self.plugin_name}.yaml! FunkMan event ignored.")
                return
            await self.process_funkman_event(config, server, player, data)
            asyncio.create_task(self.send_chat_message(player, data))
            asyncio.create_task(self.update_greenieboard(server))
