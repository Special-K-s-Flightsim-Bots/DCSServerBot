import psycopg2
from contextlib import closing
from core import EventListener, Server, Player, Channel, Side
from plugins.greenieboard import get_element


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

    async def update_greenieboard(self):
        if self.locals['configs'][0]['persistent_board']:
            server: Server = list(self.bot.servers.values())[0]
            embed = self.plugin.render_board()
            if embed:
                if 'persistent_channel' in self.locals['configs'][0]:
                    channel_id = int(self.locals['configs'][0]['persistent_channel'])
                    await server.setEmbed('greenieboard', embed, channel_id=channel_id)
                else:
                    await server.setEmbed('greenieboard', embed)

    async def send_chat_message(self, player: Player, data: dict, grade: str, comment: str):
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
        await self.update_greenieboard()

    async def onMissionEvent(self, data: dict):
        if data['eventName'] == 'S_EVENT_LANDING_QUALITY_MARK':
            server: Server = self.bot.servers[data['server_name']]
            player: Player = server.get_player(name=data['initiator']['name']) if 'name' in data['initiator'] else None
            if player:
                grade = get_element(data['comment'], 'grade')
                comment = get_element(data['comment'], 'comment')
                time = (int(server.current_mission.start_time) + int(server.current_mission.mission_time)) % 86400
                night = time > 20*3600 or time < 6 * 3600
                points = self.locals['configs'][0]['ratings'][grade]
                await self.send_chat_message(player, data, grade, comment.replace('_', '\\_'))
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute("INSERT INTO greenieboard (player_ucid, unit_type, grade, comment, place, "
                                       "night, points) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                       (player.ucid, data['initiator']['unit_type'], grade, data['comment'],
                                        data['place']['name'], night, points))
                    conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    conn.rollback()
                    self.log.exception(error)
                finally:
                    self.pool.putconn(conn)
                await self.update_greenieboard()
