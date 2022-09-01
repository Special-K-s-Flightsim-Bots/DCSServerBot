import asyncio
import csv
import os
import psycopg2
import re
import string
from contextlib import closing
from core import EventListener, Server, Player, Channel, Side
from datetime import datetime
from pathlib import Path
from plugins.greenieboard import get_element
from typing import Tuple, Optional


class GreenieBoardEventListener(EventListener):

    EVENT_TEXTS = {
        Side.BLUE: {
            'waveoff': '```ini\n[BLUE player {} waved off from carrier {}.]```',
            'bolter': '```ini\n[BLUE player {} boltered from carrier {}.]```',
            'landing': '```ini\n[BLUE player {} landed on carrier {} with grade {} / {}.]```'
        },
        Side.RED: {
            'waveoff': '```css\n[RED player {} waved off from carrier {}.]```',
            'bolter': '```css\n[RED player {} boltered from carrier {}.]```',
            'landing': '```css\n[RED player {} landed on carrier {} with grade {} / {}.]```'
        }
    }

    def _update_greenieboard(self):
        if self.locals['configs'][0]['persistent_board']:
            server: Server = list(self.bot.servers.values())[0]
            embed = self.plugin.render_board()
            if embed:
                if 'persistent_channel' in self.locals['configs'][0]:
                    channel_id = int(self.locals['configs'][0]['persistent_channel'])
                    self.bot.loop.call_soon(asyncio.create_task, server.setEmbed('greenieboard', embed, channel_id=channel_id))
                else:
                    self.bot.loop.call_soon(asyncio.create_task, server.setEmbed('greenieboard', embed))

    async def _send_chat_message(self, player: Player, data: dict, grade: str, comment: str):
        server: Server = self.bot.servers[data['server_name']]
        chat_channel = server.get_channel(Channel.CHAT)
        if chat_channel is not None:
            carrier = data['place']['name']
            if grade in ['WO', 'OWO']:
                await chat_channel.send(self.EVENT_TEXTS[player.side]['waveoff'].format(player.name, carrier))
            elif grade == 'B':
                await chat_channel.send(self.EVENT_TEXTS[player.side]['bolter'].format(player.name, carrier))
            else:
                await chat_channel.send(self.EVENT_TEXTS[player.side]['landing'].format(player.name, carrier,
                                                                                        grade.replace('_', '\\_'),
                                                                                        comment))

    async def registerDCSServer(self, data: dict):
        self._update_greenieboard()

    def _process_sc_lso_event(self, config: dict, server: Server, player: Player, data: dict) -> Tuple[str, str]:
        grade = get_element(data['comment'], 'grade')
        comment = get_element(data['comment'], 'comment')
        time = (int(server.current_mission.start_time) + int(data['time'])) % 86400
        night = time > 20 * 3600 or time < 6 * 3600
        points = data['points'] if 'points' in data else config['ratings'][grade]
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("INSERT INTO greenieboard (mission_id, player_ucid, unit_type, grade, comment, place, "
                               "night, points, trapsheet) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                               (server.mission_id, player.ucid, player.unit_type, grade, data['comment'],
                                data['place']['name'], night, points,
                                data['trapsheet'] if 'trapsheet' in data else None))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        return grade, comment

    def _get_lso_rating(self, config: dict, server: Server, player: Player, data: dict) -> Tuple[Optional[str], Optional[str]]:
        carrier = data['place']['name'].split()[0]
        filename = os.path.expandvars(self.bot.config[server.installation]['DCS_HOME'] + os.path.sep +
                                      config['Moose.AIRBOSS']['basedir'] + os.path.sep +
                                      config['Moose.AIRBOSS']['grades'].format(carrier=carrier))
        with open(filename, 'r') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row['Name'] == player.name and \
                        row['Airframe'] == player.unit_type and \
                        row['Carrier Name'] == data['place']['name'] and \
                        abs(datetime.strptime(row['OS Date'], '%a %b %d %H:%M:%S %Y').timestamp() - datetime.now().timestamp()) < 60.0:
                    grade: str = row['Grade']
                    if '<SH>' in grade:
                        grade = grade[:-4]
                    if grade == 'CUT':
                        grade = 'C'
                    if grade.startswith('--'):
                        grade = '--- : '
                    else:
                        grade = f"{grade} :"
                    comment = row['Details']
                    if row['Wire'] != 'n/a':
                        comment += f"  WIRE# {row['Wire']}"
                    return grade, comment
        return None, None

    def get_trapsheet(self, config: dict, server: Server, player: Player, data: dict) -> Optional[str]:
        dirname = os.path.expandvars(self.bot.config[server.installation]['DCS_HOME'] + os.path.sep +
                                     config['Moose.AIRBOSS']['basedir'])
        carrier = data['place']['name'].split()[0]
        name = re.sub(f"[{string.punctuation}]", "", player.name).strip()
        filename = config['Moose.AIRBOSS']['trapsheets'].format(
            carrier=carrier, name=name, unit_type=player.unit_type, number='*')
        p = Path(dirname)
        try:
            return max(p.glob(filename), key=lambda x: p.stat().st_mtime).__str__()
        except:
            return None

    def _process_airboss_event(self, config: dict, server: Server, player: Player, data: dict) -> Tuple[Optional[str], Optional[str]]:
        # check if we find a landing information about that user in our LSO file
        grade, comment = self._get_lso_rating(config, server, player, data)
        if not grade:
            return None, None
        # generate a pseudo S_EVENT_LANDING_QUALITY_MARK event
        data['command'] = 'onMissionEvent'
        data['eventName'] = 'S_EVENT_LANDING_QUALITY_MARK'
        data['comment'] = f"LSO: GRADE:{grade} {comment}"
        # get the trapsheet
        data['grade'] = grade
        data['trapsheet'] = self.get_trapsheet(config, server, player, data)
        self._process_sc_lso_event(config, server, player, data)
        return grade, comment

    async def onMissionEvent(self, data: dict):
        if 'initiator' not in data:
            return
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        player: Player = server.get_player(name=data['initiator']['name']) if 'name' in data['initiator'] else None
        if player:
            grade = comment = None
            if 'Moose.AIRBOSS' in config:
                if data['eventName'] == 'S_EVENT_AIRBOSS':
                    grade, comment = self._process_airboss_event(config, server, player, data)
                    if not grade:
                        return
            elif data['eventName'] == 'S_EVENT_LANDING_QUALITY_MARK':
                grade, comment = self._process_sc_lso_event(config, server, player, data)
            if grade:
                await self._send_chat_message(player, data, grade, comment.replace('_', '\\_'))
                self._update_greenieboard()
