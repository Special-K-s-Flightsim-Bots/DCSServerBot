import psycopg2
from contextlib import closing
from core import EventListener, utils, const
from plugins.greenieboard import get_element


class GreenieBoardEventListener(EventListener):

    async def update_greenieboard(self):
        if self.locals['configs'][0]['persistent_board']:
            data = {"channel": self.locals['configs'][0]['persistent_channel'] if 'persistent_channel' in self.locals['configs'][0] else "-1",
                    "server_name": list(self.globals.values())[0]['server_name']}
            embed = self.plugin.render_board()
            if embed:
                await self.bot.setEmbed(data, 'greenieboard', embed)

    async def send_chat_message(self, player, data, grade, comment):
        chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
        if chat_channel is not None:
            carrier = data['place']['name']
            if grade in ['WO', 'OWO']:
                await chat_channel.send('{} player {} waved off from carrier {}.'.format(
                    const.PLAYER_SIDES[player['side']], player['name'], carrier))
            elif grade == 'B':
                await chat_channel.send('{} player {} boltered from carrier {}.'.format(
                    const.PLAYER_SIDES[player['side']], player['name'], carrier))
            else:
                await chat_channel.send('{} player {} landed on carrier {} with grade {} / {}.'.format(
                    const.PLAYER_SIDES[player['side']], player['name'], carrier, grade, comment))

    async def registerDCSServer(self, data):
        await self.update_greenieboard()

    async def onMissionEvent(self, data):
        if data['eventName'] == 'S_EVENT_LANDING_QUALITY_MARK':
            server = self.globals[data['server_name']]
            player = utils.get_player(self, data['server_name'], name=data['initiator']['name']) if 'name' in data['initiator'] else None
            if player:
                grade = get_element(data['comment'], 'grade')
                comment = get_element(data['comment'], 'comment')
                time = (int(server['start_time']) + int(server['mission_time'])) % 86400
                night = time > 20*3600 or time < 6 * 3600
                points = self.locals['configs'][0]['ratings'][grade]
                await self.send_chat_message(player, data, grade, comment.replace('_', '\\_'))
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute("INSERT INTO greenieboard (player_ucid, unit_type, grade, comment, place, "
                                       "night, points) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                       (player['ucid'], data['initiator']['unit_type'], grade, data['comment'],
                                        data['place']['name'], night, points))
                    conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    conn.rollback()
                    self.log.exception(error)
                finally:
                    self.pool.putconn(conn)
                await self.update_greenieboard()
