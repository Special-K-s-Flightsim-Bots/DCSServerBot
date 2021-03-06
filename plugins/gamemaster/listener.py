from __future__ import annotations
import discord
import psycopg2
from contextlib import closing
from core import EventListener, Side, Coalition, Channel, utils
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Player, Server


class GameMasterEventListener(EventListener):

    async def onChatMessage(self, data) -> None:
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['from_id'])
        chat_channel: Optional[discord.TextChannel] = None
        if self.bot.config.getboolean(server.installation, 'COALITIONS') \
                and data['to'] == -2 and player.side in [Side.BLUE, Side.RED]:
            if player.side == Side.BLUE:
                chat_channel = server.get_channel(Channel.COALITION_BLUE)
            elif player.side == Side.RED:
                chat_channel = server.get_channel(Channel.COALITION_RED)
        else:
            chat_channel = server.get_channel(Channel.CHAT)
        if chat_channel:
            if 'from_id' in data and data['from_id'] != 1 and len(data['message']) > 0:
                await chat_channel.send(data['from_name'] + ': ' + data['message'])

    def get_coalition(self, player: Player) -> Optional[Coalition]:
        if not player.coalition:
            conn = self.bot.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('SELECT coalition FROM players WHERE ucid = %s', (player.ucid, ))
                    coalition = cursor.fetchone()[0]
                    if coalition:
                        player.coalition = Coalition(coalition)
            except (Exception, psycopg2.DatabaseError) as error:
                self.bot.log.exception(error)
            finally:
                self.bot.pool.putconn(conn)
        return player.coalition

    def get_coalition_password(self, server: Server, coalition: Coalition) -> Optional[str]:
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT blue_password, red_password FROM servers WHERE server_name = %s',
                               (server.name,))
                row = cursor.fetchone()
                return row[0] if coalition == Coalition.BLUE else row[1]
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    async def onPlayerStart(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        if data['id'] != 1 and self.bot.config.getboolean(server.installation, 'COALITIONS'):
            player: Player = server.get_player(id=data['id'])
            if self.get_coalition(player):
                data['subcommand'] = 'coalition'
                await self.onChatCommand(data)
            data['subcommand'] = 'password'
            await self.onChatCommand(data)

    async def join(self, data: dict):
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['from_id'], active=True)
        coalition = data['params'][0] if len(data['params']) > 0 else ''
        if coalition.casefold() not in ['blue', 'red']:
            player.sendChatMessage('Usage: -join <blue|red>')
            return
        if self.get_coalition(player) == Coalition(coalition):
            player.sendChatMessage(f"You are a member of coalition {coalition} already.")
            return
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # we don't care about coalitions if they left longer than one day before
                cursor.execute("SELECT coalition FROM players WHERE ucid = %s AND coalition_leave > (NOW() - "
                               "interval %s)", (player.ucid, self.bot.config['BOT']['COALITION_LOCK_TIME']))
                if cursor.rowcount == 1:
                    if cursor.fetchone()[0] != coalition.casefold():
                        player.sendChatMessage(f"You can't join the {coalition} coalition in-between "
                                               f"{self.bot.config['BOT']['COALITION_LOCK_TIME']} of leaving a coalition.")
                        await self.bot.audit(f"{player.name} tried to join a new coalition in-between the time limit.",
                                             user=player.ucid)
                        return
                player.coalition = Coalition(coalition)
                if player.member:
                    roles = {
                        Coalition.RED: discord.utils.get(player.member.guild.roles, name=self.bot.config['ROLES']['Coalition Red']),
                        Coalition.BLUE: discord.utils.get(player.member.guild.roles, name=self.bot.config['ROLES']['Coalition Blue'])
                    }
                    await player.member.add_roles(roles[player.coalition])
                cursor.execute('UPDATE players SET coalition = %s WHERE ucid = %s', (coalition, player.ucid))
                player.sendChatMessage(f'Welcome to the {coalition} side!')
                conn.commit()
        except discord.Forbidden:
            await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    async def leave(self, data):
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['from_id'], active=True)
        if not self.get_coalition(player):
            player.sendChatMessage(f"You are not a member of any coalition. You can join one with "
                                   f"-join <blue|red>.")
            return
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET coalition = NULL, coalition_leave = NOW() WHERE ucid = %s',
                               (player.ucid,))
            conn.commit()
            if player.member:
                roles = {
                    Coalition.RED: discord.utils.get(player.member.guild.roles, name=self.bot.config['ROLES']['Coalition Red']),
                    Coalition.BLUE: discord.utils.get(player.member.guild.roles, name=self.bot.config['ROLES']['Coalition Blue'])
                }
                await player.member.remove_roles(roles[player.coalition])
            player.sendChatMessage(f"You've left the {player.coalition.name} coalition!")
            player.coalition = None
            return
        except discord.Forbidden:
            await self.bot.audit(f'Permission "Manage Roles" missing for {self.bot.member.name}.', user=self.bot.member)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    def campaign(self, command: str, *, servers: Optional[list[Server]] = None, name: Optional[str] = None,
                 description: Optional[str] = None, start: Optional[datetime] = None, end: Optional[datetime] = None):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if command == 'add':
                    cursor.execute('INSERT INTO campaigns (name, description, start, stop) VALUES (%s, %s, %s, %s)',
                                   (name, description, start, end))
                    if servers:
                        cursor.execute('SELECT id FROM campaigns WHERE name ILIKE %s', (name,))
                        campaign_id = cursor.fetchone()[0]
                        for server in servers:
                            # add this server to the server list
                            cursor.execute('INSERT INTO campaigns_servers VALUES (%s, %s) ON CONFLICT DO NOTHING',
                                           (campaign_id, server.name))
                elif command == 'start':
                    cursor.execute('SELECT id FROM campaigns WHERE name ILIKE %s AND NOW() BETWEEN start AND '
                                   'COALESCE(stop, NOW())', (name,))
                    if cursor.rowcount == 0:
                        cursor.execute('INSERT INTO campaigns (name) VALUES (%s)', (name,))
                    if servers:
                        cursor.execute('SELECT id FROM campaigns WHERE name ILIKE %s AND NOW() BETWEEN start AND '
                                       'COALESCE(stop, NOW())', (name,))
                        # don't use currval() in here, as we can't rely on the sequence name
                        campaign_id = cursor.fetchone()[0]
                        for server in servers:
                            cursor.execute("INSERT INTO campaigns_servers VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                           (campaign_id, server.name,))
                elif command == 'stop':
                    cursor.execute('UPDATE campaigns SET stop = NOW() WHERE name ILIKE %s AND NOW() BETWEEN start AND '
                                   'COALESCE(stop, NOW())', (name,))
                elif command == 'delete':
                    cursor.execute('SELECT id FROM campaigns WHERE name ILIKE %s', (name,))
                    campaign_id = cursor.fetchone()[0]
                    cursor.execute('DELETE FROM campaigns_servers WHERE campaign_id = %s', (campaign_id,))
                    cursor.execute('DELETE FROM campaigns WHERE id = %s', (campaign_id,))
            conn.commit()
        except (Exception, psycopg2.DatabaseError):
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    async def startCampaign(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        name = data['name'] or '_internal_'
        try:
            self.campaign('start', servers=[server], name=name)
        except psycopg2.errors.UniqueViolation:
            await self.resetCampaign(data)

    async def stopCampaign(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        _, name = utils.get_running_campaign(server)
        self.campaign('delete', name=name)

    async def resetCampaign(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        _, name = utils.get_running_campaign(server)
        self.campaign('delete', name=name)
        self.campaign('start', servers=[server])

    async def onChatCommand(self, data):
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['from_id'])
        coalition = self.get_coalition(player)
        if self.bot.config.getboolean(server.installation, 'COALITIONS'):
            if data['subcommand'] == 'join':
                await self.join(data)
            elif data['subcommand'] == 'leave':
                await self.leave(data)
            elif data['subcommand'] == 'coalition':
                player.sendChatMessage(f"You are a member of the {coalition.name} coalition." if coalition else "You are not a member of any coalition. You can join one with -join <blue|red>.")
            elif data['subcommand'] in ['password', 'passwd']:
                if not coalition:
                    player.sendChatMessage(f"You are not a member of any coalition. You can join one with "
                                           f"-join <blue|red>.")
                    return
                password = self.get_coalition_password(server, player.coalition)
                if password:
                    player.sendChatMessage(f"Your coalition password is {password}.")
                else:
                    player.sendChatMessage("There is no password set for your coalition.")
        if data['subcommand'] == 'flag' and player.has_discord_roles(['DCS Admin', 'GameMaster']):
            if len(data['params']) == 0:
                player.sendChatMessage(f"Usage: -flag <flag> [value]")
                return
            flag = int(data['params'][0])
            if len(data['params']) > 1:
                value = int(data['params'][1])
                server.sendtoDCS({
                    "command": "setFlag",
                    "flag": flag,
                    "value": value
                })
                player.sendChatMessage(f"Flag {flag} set to {value}.")
            else:
                response = await server.sendtoDCSSync({"command": "getFlag", "flag": flag})
                player.sendChatMessage(f"Flag {flag} has value {response['value']}.")
