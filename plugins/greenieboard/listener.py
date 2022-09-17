import asyncio
import os
import psycopg2
import re
import string
from contextlib import closing
from core import EventListener, Server, Player, Channel, Side
from pathlib import Path
from plugins.greenieboard import get_element
from typing import Optional


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

    async def _send_chat_message(self, player: Player, data: dict):
        server: Server = self.bot.servers[data['server_name']]
        chat_channel = server.get_channel(Channel.CHAT)
        if chat_channel is not None:
            carrier = data['place']['name']
            if 'WO' in data['grade']:
                await chat_channel.send(self.EVENT_TEXTS[player.side]['waveoff'].format(player.name, carrier))
            elif data['grade'] == 'B':
                await chat_channel.send(self.EVENT_TEXTS[player.side]['bolter'].format(player.name, carrier))
            else:
                details = data['details']
                if data['wire']:
                    details += f" WIRE# {data['wire']}"
                await chat_channel.send(self.EVENT_TEXTS[player.side]['landing'].format(
                    player.name, carrier, data['grade'].replace('_', '\\_'), details))

    async def registerDCSServer(self, data: dict):
        self._update_greenieboard()

    def _process_lso_event(self, config: dict, server: Server, player: Player, data: dict):
        time = (int(server.current_mission.start_time) + int(data['time'])) % 86400
        night = time > 20 * 3600 or time < 6 * 3600
        points = data['points'] if 'points' in data else config['ratings'][data['grade']]
        wire = data['wire'] if 'wire' in data else None
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("INSERT INTO greenieboard (mission_id, player_ucid, unit_type, grade, comment, place, "
                               "wire, night, points, trapsheet) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                               (server.mission_id, player.ucid, player.unit_type, data['grade'], data['details'],
                                data['place']['name'], wire, night, points,
                                data['trapsheet'] if 'trapsheet' in data else None))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @staticmethod
    def _normalize_airboss_lso_rating(grade: str) -> Optional[str]:
        if '<SH>' in grade:
            grade = grade[:-4]
        if grade == 'CUT':
            grade = 'C'
        if 'BOLTER' in grade:
            grade = 'B'
        return grade

    def get_trapsheet(self, config: dict, server: Server, player: Player, data: dict) -> Optional[str]:
        dirname = os.path.expandvars(self.bot.config[server.installation]['DCS_HOME'] + os.path.sep +
                                     config['Moose.AIRBOSS']['basedir'])
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

    def _process_airboss_event(self, config: dict, server: Server, player: Player, data: dict):
        data['grade'] = self._normalize_airboss_lso_rating(data['grade'])
        data['trapsheet'] = self.get_trapsheet(config, server, player, data)
        self._process_lso_event(config, server, player, data)

    def _process_sc_event(self, config: dict, server: Server, player: Player, data: dict):
        data['details'] = get_element(data['comment'], 'details')
        data['grade'] = get_element(data['comment'], 'grade').replace('---', '--')
        data['wire'] = get_element(data['comment'], 'wire')
        self._process_lso_event(config, server, player, data)

    async def onMissionEvent(self, data: dict):
        if 'initiator' not in data:
            return
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        player: Player = server.get_player(name=data['initiator']['name']) if 'name' in data['initiator'] else None
        if player:
            update = False
            if 'Moose.AIRBOSS' in config:
                if data['eventName'] == 'S_EVENT_AIRBOSS':
                    self._process_airboss_event(config, server, player, data)
                    update = True
            elif data['eventName'] == 'S_EVENT_LANDING_QUALITY_MARK':
                self._process_sc_event(config, server, player, data)
                update = True
            if update:
                await self._send_chat_message(player, data)
                self._update_greenieboard()
