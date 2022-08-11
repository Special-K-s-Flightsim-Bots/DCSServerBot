import asyncio
import discord
import psycopg2
import shlex
from contextlib import closing
from core import EventListener, Player, Server, Channel


class AdminEventListener(EventListener):

    def _updateBans(self, data=None):
        banlist = []
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT ucid, reason FROM bans')
                banlist = [dict(row) for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        if data is not None:
            servers = [self.bot.servers[data['server_name']]]
        else:
            servers = self.bot.servers.values()
        for server in servers:
            for ban in banlist:
                server.sendtoDCS({
                    "command": "ban",
                    "ucid": ban['ucid'],
                    "reason": ban['reason']
                })

    async def registerDCSServer(self, data):
        # upload the current bans to the server
        self._updateBans(data)

    async def ban(self, data):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s)',
                               (data['ucid'], 'DCSServerBot', data['reason']))
                for server in self.bot.servers.values():
                    server.sendtoDCS({
                        "command": "ban",
                        "ucid": data['ucid'],
                        "reason": data['reason']
                    })
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    async def onChatCommand(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['from_id'], active=True)
        if not player:
            return
        if data['subcommand'] == 'kick' and player and player.has_discord_roles(['DCS Admin']):
            if len(data['params']) == 0:
                player.sendChatMessage("Usage: -kick <name> [reason]")
                return
            params = shlex.split(' '.join(data['params']))
            name = params[0]
            if len(params) > 1:
                reason = ' '.join(params[1:])
            else:
                reason = 'n/a'
            delinquent: Player = server.get_player(name=name, active=True)
            if not delinquent:
                player.sendChatMessage(f"Player {name} not found. Use \"\" around names with blanks.")
                return
            server.kick(delinquent, reason)
            player.sendChatMessage(f"User {name} kicked.")
            self.bot.loop.call_soon(asyncio.create_task,
                                    self.bot.audit(f'kicked player {name}' + (f' with reason "{reason}".' if reason != 'n/a' else '.'),
                                                   user=player.member))
        elif data['subcommand'] == '911':
            mentions = ''
            for role_name in [x.strip() for x in self.bot.config['ROLES']['DCS Admin'].split(',')]:
                role: discord.Role = discord.utils.get(self.bot.guilds[0].roles, name=role_name)
                if role:
                    mentions += role.mention
            message = ' '.join(data['params'])
            self.bot.loop.call_soon(asyncio.create_task, server.get_channel(Channel.ADMIN).send(
                mentions + f" 911 call from player {player.name} (ucid={player.ucid}):```{message}```"))
