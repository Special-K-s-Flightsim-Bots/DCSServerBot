import discord
import psycopg2
from contextlib import closing
from core import EventListener, utils, const
from typing import Optional


class GameMasterEventListener(EventListener):

    async def onChatMessage(self, data) -> None:
        server = self.globals[data['server_name']]
        player = utils.get_player(self, data['server_name'], id=data['from_id'])
        if self.config.getboolean(server['installation'], 'COALITIONS') \
                and data['to'] == -2 and player['side'] in [const.SIDE_BLUE, const.SIDE_RED]:
            if player['side'] == const.SIDE_BLUE:
                chat_channel = self.bot.get_bot_channel(data, 'coalition_blue_channel')
            elif player['side'] == const.SIDE_RED:
                chat_channel = self.bot.get_bot_channel(data, 'coalition_red_channel')
        else:
            chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
        if chat_channel:
            if 'from_id' in data and data['from_id'] != 1 and len(data['message']) > 0:
                return await chat_channel.send(data['from_name'] + ': ' + data['message'])

    def get_coalition(self, player: dict) -> str:
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT coalition FROM players WHERE ucid = %s', (player['ucid'], ))
                return cursor.fetchone()[0]
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    def get_coalition_password(self, server_name: str, coalition: str) -> Optional[str]:
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT blue_password, red_password FROM servers WHERE server_name = %s',
                               (server_name,))
                row = cursor.fetchone()
                return row[0] if coalition == 'blue' else row[1]
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    async def onPlayerStart(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        if data['id'] != 1 and self.config.getboolean(server['installation'], 'COALITIONS'):
            coalition = self.get_coalition(utils.get_player(self, data['server_name'], id=data['id']))
            if coalition:
                data['subcommand'] = 'coalition'
                await self.onChatCommand(data)
            data['subcommand'] = 'password'
            await self.onChatCommand(data)

    async def join(self, data: dict):
        coalition = data['params'][0] if len(data['params']) > 0 else ''
        if coalition.casefold() not in ['blue', 'red']:
            utils.sendChatMessage(self, data['server_name'], data['from_id'], 'Usage: -join <blue|red>')
            return
        player = utils.get_player(self, data['server_name'], id=data['from_id'], active=True)
        if self.get_coalition(player) == coalition:
            utils.sendChatMessage(self, data['server_name'], data['from_id'],
                                  f"You are a member of coalition {coalition} already.")
            return
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # we don't care about coalitions if they left longer than one day before
                cursor.execute("SELECT coalition FROM players WHERE ucid = %s AND coalition_leave > (NOW() - "
                               "interval %s)", (player['ucid'], self.config['BOT']['COALITION_LOCK_TIME']))
                if cursor.rowcount == 1:
                    if cursor.fetchone()[0] != coalition.casefold():
                        utils.sendChatMessage(self, data['server_name'], data['from_id'],
                                              f"You can't join the {coalition} coalition in-between "
                                              f"{self.config['BOT']['COALITION_LOCK_TIME']} of leaving a coalition.")
                        await self.bot.audit(f"{player['name']} tried to join a new coalition in-between the time limit.",
                                             user=player['ucid'])
                        return
                member = utils.get_member_by_ucid(self, player['ucid'])
                if member:
                    roles = {
                        "red": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Red']),
                        "blue": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Blue'])
                    }
                    await member.add_roles(roles[coalition.lower()])
                cursor.execute('UPDATE players SET coalition = %s WHERE ucid = %s', (coalition, player['ucid']))
                player['coalition'] = coalition
                utils.sendChatMessage(self, data['server_name'], data['from_id'], f'Welcome to the {coalition} side!')
                conn.commit()
        except discord.Forbidden:
            await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    async def leave(self, data):
        player = utils.get_player(self, data['server_name'], id=data['from_id'], active=True)
        coalition = self.get_coalition(player)
        if not coalition:
            utils.sendChatMessage(self, data['server_name'], data['from_id'],
                                  f"You are not a member of any coalition. You can join one with -join <blue|red>.")
            return
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET coalition = NULL, coalition_leave = NOW() WHERE ucid = %s', (player['ucid'],))
            conn.commit()
            member = utils.get_member_by_ucid(self, player['ucid'])
            if member:
                roles = {
                    "red": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Red']),
                    "blue": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Blue'])
                }
                await member.remove_roles(roles[coalition])
            utils.sendChatMessage(self, data['server_name'], data['from_id'], f"You've left the {coalition} coalition!")
            return
        except discord.Forbidden:
            await self.bot.audit(f'Permission "Manage Roles" missing for {self.bot.member.name}.', user=self.bot.member)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    async def onChatCommand(self, data):
        server = self.globals[data['server_name']]
        if not self.config.getboolean(server['installation'], 'COALITIONS'):
            return
        if data['subcommand'] == 'join':
            await self.join(data)
        elif data['subcommand'] == 'leave':
            await self.leave(data)
        elif data['subcommand'] == 'coalition':
            coalition = self.get_coalition(utils.get_player(self, data['server_name'], id=data['from_id']))
            utils.sendChatMessage(self, data['server_name'], data['from_id'],
                                  f"You are a member of the {coalition} coalition." if coalition
                                  else "You are not a member of any coalition. You can join one with -join <blue|red>.")
        elif data['subcommand'] in ['password', 'passwd']:
            coalition = self.get_coalition(utils.get_player(self, data['server_name'], id=data['from_id']))
            if not coalition:
                utils.sendChatMessage(self, data['server_name'], data['from_id'],
                                      f"You are not a member of any coalition. You can join one with -join <blue|red>.")
                return
            password = self.get_coalition_password(data['server_name'], coalition)
            if password:
                utils.sendChatMessage(self, data['server_name'], data['from_id'],
                                      f"Your coalition password is {password}.")
            else:
                utils.sendChatMessage(self, data['server_name'], data['from_id'],
                                      "There is no password set for your coalition.")
        elif data['subcommand'] == 'flag' and \
                utils.has_discord_roles(self, server, data['from_id'], ['DCS Admin', 'GameMaster']):
            if len(data['params']) == 0:
                utils.sendChatMessage(self, data['server_name'], data['from_id'], f"Usage: -flag <flag> [value]")
                return
            flag = int(data['params'][0])
            if len(data['params']) > 1:
                value = int(data['params'][1])
                self.bot.sendtoDCS(server, {
                    "command": "setFlag",
                    "flag": flag,
                    "value": value
                })
                utils.sendChatMessage(self, data['server_name'], data['from_id'], f"Flag {flag} set to {value}.")
            else:
                response = await self.bot.sendtoDCSSync(server, {"command": "getFlag", "flag": flag})
                utils.sendChatMessage(self, data['server_name'], data['from_id'],
                                      f"Flag {flag} has value {response['value']}.")
