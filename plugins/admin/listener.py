# listener.py
import psycopg2
import shlex
from contextlib import closing
from core import utils, EventListener, Status


class AdminEventListener(EventListener):

    def updateBans(self, data=None):
        banlist = []
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT ucid FROM bans')
                banlist = [dict(row) for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        if data is not None:
            servers = [self.globals[data['server_name']]]
        else:
            servers = self.globals.values()
        for server in servers:
            for ban in banlist:
                self.bot.sendtoDCS(server, {"command": "ban", "ucid": ban['ucid'], "channel": server['status_channel']})

    async def registerDCSServer(self, data):
        # upload the current bans to the server
        self.updateBans(data)

    async def ban(self, data):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s)',
                               (data['ucid'], 'DCSServerBot', data['reason']))
                for server in self.globals.values():
                    self.bot.sendtoDCS(server, {
                        "command": "ban",
                        "ucid": data['ucid'],
                        "reason": data['reason']
                    })
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    async def onChatCommand(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        if data['subcommand'] == 'kick' and utils.has_discord_roles(self, server, data['from_id'], ['DCS Admin']):
            if len(data['params']) == 0:
                utils.sendChatMessage(self, data['server_name'], data['from_id'], "Usage: -kick <name> [reason]")
                return
            params = shlex.split(' '.join(data['params']))
            name = params[0]
            if len(params) > 1:
                reason = ' '.join(params[1:])
            else:
                reason = 'n/a'
            delinquent = utils.get_player(self, server['server_name'], name=name, active=True)
            if not delinquent:
                utils.sendChatMessage(self, data['server_name'], data['from_id'], f"Player {name} not found. Use \"\" "
                                                                                  f"around names with blanks.")
                return
            self.bot.sendtoDCS(server, {"command": "kick", "name": name, "reason": reason})
            utils.sendChatMessage(self, data['server_name'], data['from_id'], f"User {name} kicked.")
            player = utils.get_player(self, server['server_name'], id=data['from_id'])
            member = utils.get_member_by_ucid(self, player['ucid'], True)
            await self.bot.audit(f'kicked player {name}' + (f' with reason "{reason}".' if reason != 'n/a' else '.'),
                                 user=member)
