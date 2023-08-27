from __future__ import annotations
import discord
import logging
import os
import psycopg2
from contextlib import closing
from core import EventListener, Side, Coalition, Channel, utils, event, chat_command
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Player, Server, Plugin


class GameMasterEventListener(EventListener):

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.chat_log = dict()

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if self.bot.config.getboolean(server.installation, 'CHAT_LOG') and server.installation not in self.chat_log:
            self.chat_log[server.installation] = logging.getLogger(name=f'chat-{server.installation}')
            self.chat_log[server.installation].setLevel(logging.INFO)
            formatter = logging.Formatter(fmt=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s',
                                          datefmt='%Y-%m-%d %H:%M:%S')
            filename = os.path.expandvars(self.bot.config[server.installation]['DCS_HOME'] + r'\Logs\chat.log')
            fh = RotatingFileHandler(filename, encoding='utf-8',
                                     maxBytes=int(self.bot.config[server.installation]['CHAT_LOGROTATE_SIZE']),
                                     backupCount=int(self.bot.config[server.installation]['CHAT_LOGROTATE_COUNT']))
            fh.setLevel(logging.INFO)
            fh.setFormatter(formatter)
            self.chat_log[server.installation].addHandler(fh)

    @event(name="onChatMessage")
    async def onChatMessage(self, server: Server, data: dict) -> None:
        player: Player = server.get_player(id=data['from_id'])
        if self.bot.config.getboolean(server.installation, 'CHAT_LOG'):
            self.chat_log[server.installation].info(f"{player.ucid}\t{player.name}\t{data['to']}\t{data['message']}")
        chat_channel: Optional[discord.TextChannel] = None
        if self.bot.config.getboolean(server.installation, 'COALITIONS') \
                and data['to'] == -2 and player.coalition in [Coalition.BLUE, Coalition.RED]:
            if player.coalition == Coalition.BLUE:
                chat_channel = server.get_channel(Channel.COALITION_BLUE_CHAT)
            elif player.coalition == Coalition.RED:
                chat_channel = server.get_channel(Channel.COALITION_RED_CHAT)
        else:
            chat_channel = server.get_channel(Channel.CHAT)
        if chat_channel:
            if 'from_id' in data and data['from_id'] != 1 and len(data['message']) > 0:
                await chat_channel.send(f"{data['from_name']}  said: {data['message']}")

    def get_coalition(self, server: Server, player: Player) -> Optional[Coalition]:
        if not player.coalition:
            conn = self.bot.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('SELECT coalition FROM coalitions WHERE server_name = %s and player_ucid = %s '
                                   'AND coalition_leave IS NULL', (server.name, player.ucid))
                    coalition = cursor.fetchone()[0] if cursor.rowcount == 1 else None
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

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] != 1 and self.bot.config.getboolean(server.installation, 'COALITIONS'):
            player: Player = server.get_player(id=data['id'])
            if player.has_discord_roles(['DCS Admin', 'GameMaster']):
                side = Side.UNKNOWN
            elif player.coalition == Coalition.BLUE:
                side = Side.BLUE
            elif player.coalition == Coalition.RED:
                side = Side.RED
            else:
                side = Side.SPECTATOR
            server.sendtoDCS({
                "command": "setUserCoalition",
                "ucid": player.ucid,
                "coalition": side.value
            })
            data['from_id'] = data['id']
            await self._coalition(server, player)
            if self.get_coalition_password(server, player.coalition):
                await self._password(server, player)

    def campaign(self, command: str, *, servers: Optional[list[str]] = None, name: Optional[str] = None,
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
                        for server_name in servers:
                            # add this server to the server list
                            cursor.execute('INSERT INTO campaigns_servers VALUES (%s, %s) ON CONFLICT DO NOTHING',
                                           (campaign_id, server_name))
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
                        for server_name in servers:
                            cursor.execute("INSERT INTO campaigns_servers VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                           (campaign_id, server_name,))
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

    @event(name="startCampaign")
    async def startCampaign(self, server: Server, data: dict) -> None:
        name = data['name'] or '_internal_'
        try:
            self.campaign('start', servers=[server.name], name=name)
        except psycopg2.errors.UniqueViolation:
            await self.resetCampaign(data)

    @event(name="stopCampaign")
    async def stopCampaign(self, server: Server, data: dict) -> None:
        _, name = utils.get_running_campaign(server)
        if name:
            self.campaign('delete', name=name)

    @event(name="resetCampaign")
    async def resetCampaign(self, server: Server, data: dict) -> None:
        _, name = utils.get_running_campaign(server)
        if name:
            self.campaign('delete', name=name)
        self.campaign('start', servers=[server.name])

    async def _join(self, server: Server, player: Player, params: list[str]):
        coalition = params[0] if params else ''
        if coalition.casefold() not in ['blue', 'red']:
            player.sendChatMessage(f"Usage: {self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}join <blue|red>")
            return
        if player.coalition:
            if player.coalition == Coalition(coalition):
                player.sendChatMessage(f"You are a member of coalition {coalition} already.")
            else:
                if player.coalition == Coalition.RED:
                    player.sendChatMessage(f"You are a member of coalition red already.")
                else:
                    player.sendChatMessage(f"You are a member of coalition blue already.")
            return

        # update the database
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # check if the player is eligible to change the coalitions
                cursor.execute("SELECT coalition FROM coalitions WHERE server_name = %s AND player_ucid = %s "
                               "AND coalition_leave > (NOW() - interval %s)",
                               (server.name, player.ucid, self.bot.config[server.installation]['COALITION_LOCK_TIME']))
                if cursor.rowcount == 1:
                    if cursor.fetchone()[0] != coalition.casefold():
                        player.sendChatMessage(f"You can't join the {coalition} coalition in-between "
                                               f"{self.bot.config[server.installation]['COALITION_LOCK_TIME']} of "
                                               f"leaving a coalition.")
                        await self.bot.audit(f"{player.display_name} tried to join a new coalition in-between the time "
                                             f"limit.", user=player.ucid)
                        return

                # set the new coalition
                cursor.execute('INSERT INTO coalitions (server_name, player_ucid, coalition, coalition_leave) '
                               'VALUES (%s, %s, %s, NULL) '
                               'ON CONFLICT (server_name, player_ucid) DO UPDATE '
                               'SET coalition = excluded.coalition, coalition_leave = excluded.coalition_leave',
                               (server.name, player.ucid, coalition))
                player.coalition = Coalition(coalition)
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

        # welcome them in DCS
        password = self.get_coalition_password(server, player.coalition)
        player.sendChatMessage(f'Welcome to the {coalition} side!')
        if password:
            player.sendChatMessage(f"Your coalition password is {password}.")

        # set the discord role
        try:
            if player.member:
                roles = {
                    Coalition.RED: discord.utils.get(player.member.guild.roles,
                                                     name=self.bot.config[server.installation]['Coalition Red']),
                    Coalition.BLUE: discord.utils.get(player.member.guild.roles,
                                                      name=self.bot.config[server.installation]['Coalition Blue'])
                }
                role = roles[player.coalition]
                if role:
                    await player.member.add_roles(role)
        except discord.Forbidden:
            await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)

    async def reset_coalitions(self, server: Server, discord_roles: bool):
        guild = self.bot.guilds[0]
        roles = {
            "red": discord.utils.get(guild.roles, name=self.bot.config[server.installation]['Coalition Red']),
            "blue": discord.utils.get(guild.roles, name=self.bot.config[server.installation]['Coalition Blue'])
        }
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT p.ucid, p.discord_id, c.coalition FROM players p, coalitions c '
                               'WHERE p.ucid = c.player_ucid and c.server_name = %s AND c.coalition IS NOT NULL',
                               (server.name,))
                for row in cursor.fetchall():
                    if discord_roles and row[1] != -1:
                        member = self.bot.guilds[0].get_member(row[1])
                        await member.remove_roles(roles[row[2]])
                    cursor.execute('DELETE FROM coalitions WHERE server_name = %s AND player_ucid = %s',
                                   (server.name, row[0]))
                server.sendtoDCS({"command": "resetUserCoalitions"})
            conn.commit()
        except discord.Forbidden:
            await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)
            raise
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    @event(name="resetUserCoalitions")
    async def resetUserCoalitions(self, server: Server, data: dict) -> None:
        if not self.bot.config.getboolean(server.installation, 'COALITIONS'):
            self.log.warning(
                f'Event "resetUserCoalitions" received, but COALITIONS are disabled on server "{server.name}"')
            return
        discord_roles = data.get('discord_roles', False)
        try:
            await self.reset_coalitions(server, discord_roles)
        except discord.Forbidden:
            self.log.error('The bot is missing the "Manage Roles" permission.')

    @chat_command(name="join", usage="<coalition>", help="join a coalition")
    async def join(self, server: Server, player: Player, params: list[str]):
        await self._join(server, player, params)

    @chat_command(name="leave", help="leave your coalition")
    async def leave(self, server: Server, player: Player, params: list[str]):
        if not self.get_coalition(server, player):
            player.sendChatMessage(f"You are not a member of any coalition. You can join one with "
                                   f"{self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}join blue|red.")
            return
        # update the database
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE coalitions SET coalition_leave = NOW() WHERE server_name = %s '
                               'AND player_ucid = %s', (server.name, player.ucid))
                player.sendChatMessage(f"You've left the {player.coalition.name} coalition!")
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)
        # remove discord roles
        try:
            if player.member:
                roles = {
                    Coalition.RED: discord.utils.get(player.member.guild.roles,
                                                     name=self.bot.config[server.installation]['Coalition Red']),
                    Coalition.BLUE: discord.utils.get(player.member.guild.roles,
                                                      name=self.bot.config[server.installation]['Coalition Blue'])
                }
                await player.member.remove_roles(roles[player.coalition])
        except discord.Forbidden:
            await self.bot.audit(f'Permission "Manage Roles" missing for {self.bot.member.name}.', user=self.bot.member)
        finally:
            player.coalition = None

    @chat_command(name="red", help="join the red side")
    async def red(self, server: Server, player: Player, params: list[str]):
        await self._join(server, player, ["red"])

    @chat_command(name="blue", help="join the blue side")
    async def blue(self, server: Server, player: Player, params: list[str]):
        await self._join(server, player, ["blue"])

    async def _coalition(self, server: Server, player: Player):
        coalition = self.get_coalition(server, player)
        if coalition:
            player.sendChatMessage(f"You are a member of the {coalition.name} coalition.")
        else:
            player.sendChatMessage(f"You are not a member of any coalition. You can join one with "
                                   f"{self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}join blue|red.")

    @chat_command(name="coalition", help="displays your current coalition")
    async def coalition(self, server: Server, player: Player, params: list[str]):
        await self._coalition(server, player)

    async def _password(self, server: Server, player: Player):
        coalition = self.get_coalition(server, player)
        if not coalition:
            player.sendChatMessage(f"You are not a member of any coalition. You can join one with "
                                   f"{self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}join blue|red.")
            return
        password = self.get_coalition_password(server, player.coalition)
        if password:
            player.sendChatMessage(f"Your coalition password is {password}.")
        else:
            player.sendChatMessage("There is no password set for your coalition.")

    @chat_command(name="password", aliases=["passwd"], help="displays the coalition password")
    async def password(self, server: Server, player: Player, params: list[str]):
        await self._password(server, player)

    @chat_command(name="flag", roles=['DCS Admin', 'GameMaster'], usage="<flag> [value]", help="reads or sets a flag")
    async def flag(self, server: Server, player: Player, params: list[str]):
        if not params:
            player.sendChatMessage(f"Usage: {self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}flag <flag> [value]")
            return
        flag = params[0]
        if len(params) > 1:
            value = int(params[1])
            server.sendtoDCS({
                "command": "setFlag",
                "flag": flag,
                "value": value
            })
            player.sendChatMessage(f"Flag {flag} set to {value}.")
        else:
            response = await server.sendtoDCSSync({"command": "getFlag", "flag": flag})
            player.sendChatMessage(f"Flag {flag} has value {response['value']}.")
