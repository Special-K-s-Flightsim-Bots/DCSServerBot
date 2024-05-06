from __future__ import annotations
import asyncio
import discord
import logging
import os
import psycopg

from core import EventListener, Side, Coalition, Channel, utils, event, chat_command, CloudRotatingFileHandler, \
    get_translation, ChatCommand
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Player, Server, Plugin

_ = get_translation(__name__.split('.')[1])


class GameMasterEventListener(EventListener):

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.chat_log = dict()

    def can_run(self, command: ChatCommand, server: Server, player: Player) -> bool:
        if (command.name in ['join', 'leave', 'red', 'blue', 'coalition', 'password'] and
                not self.get_coalition(server, player)):
            return False
        return super().can_run(command, server, player)

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, _: dict) -> None:
        if not server.locals.get('chat_log') or server.name in self.chat_log:
            return
        os.makedirs('logs', exist_ok=True)
        self.chat_log[server.name] = logging.getLogger(name=f'chat-{server.name}')
        self.chat_log[server.name].propagate = False
        self.chat_log[server.name].setLevel(logging.INFO)
        formatter = logging.Formatter(fmt=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
        filename = os.path.join('logs', f'{utils.slugify(server.name)}-chat.log')
        fh = CloudRotatingFileHandler(filename, encoding='utf-8',
                                      maxBytes=int(server.locals['chat_log'].get('size', 1048576)),
                                      backupCount=int(server.locals['chat_log'].get('count', 10)))
        fh.setLevel(logging.INFO)
        fh.setFormatter(formatter)
        self.chat_log[server.name].addHandler(fh)

    @event(name="onChatMessage")
    async def onChatMessage(self, server: Server, data: dict) -> None:
        player: Player = server.get_player(id=data['from'])
        if not player or not data['message']:
            return
        if server.locals.get('chat_log') and self.chat_log.get(server.name):
            self.chat_log[server.name].info(f"{player.ucid}\t{player.name}\t{data['to']}\t{data['message']}")
        chat_channel: Optional[discord.TextChannel] = None
        if server.locals.get('coalitions') and data['to'] == -2 and player.coalition in [Coalition.BLUE, Coalition.RED]:
            if player.coalition == Coalition.BLUE:
                chat_channel = self.bot.get_channel(server.channels[Channel.COALITION_BLUE_CHAT])
            elif player.coalition == Coalition.RED:
                chat_channel = self.bot.get_channel(server.channels[Channel.COALITION_RED_CHAT])
        else:
            if not server.locals.get('no_coalition_chat', False) or data['to'] != -2:
                chat_channel = self.bot.get_channel(server.channels[Channel.CHAT])
        if chat_channel:
            # noinspection PyAsyncCall
            asyncio.create_task(chat_channel.send(f"{player.name} said: {data['message']}"))

    def get_coalition(self, server: Server, player: Player) -> Optional[Coalition]:
        if not player.coalition:
            with self.pool.connection() as conn:
                cursor = conn.execute("""
                    SELECT coalition FROM coalitions 
                    WHERE server_name = %s and player_ucid = %s AND coalition_leave IS NULL
                """, (server.name, player.ucid))
                coalition = (cursor.fetchone())[0] if cursor.rowcount == 1 else None
                if coalition:
                    player.coalition = Coalition(coalition)
        return player.coalition

    def get_coalition_password(self, server: Server, coalition: Coalition) -> Optional[str]:
        with self.pool.connection() as conn:
            cursor = conn.execute('SELECT blue_password, red_password FROM servers WHERE server_name = %s',
                                  (server.name,))
            row = cursor.fetchone()
            return row[0] if coalition == Coalition.BLUE else row[1]

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or not server.locals.get('coalitions') or 'ucid' not in data:
            return
        player: Player = server.get_player(ucid=data['ucid'])
        if not player:
            return

        if player.has_discord_roles(['DCS Admin', 'GameMaster']):
            side = Side.UNKNOWN
        elif player.coalition == Coalition.BLUE:
            side = Side.BLUE
        elif player.coalition == Coalition.RED:
            side = Side.RED
        else:
            side = Side.SPECTATOR
        server.send_to_dcs({
            "command": "setUserCoalition",
            "ucid": player.ucid,
            "coalition": side.value
        })
        # noinspection PyAsyncCall
        asyncio.create_task(self._coalition(server, player))
        if self.get_coalition_password(server, player.coalition):
            # noinspection PyAsyncCall
            asyncio.create_task(self._password(server, player))

    async def campaign(self, command: str, *, servers: Optional[list[Server]] = None, name: Optional[str] = None,
                       description: Optional[str] = None, start: Optional[datetime] = None,
                       end: Optional[datetime] = None):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                if command == 'add':
                    await conn.execute('INSERT INTO campaigns (name, description, start, stop) VALUES (%s, %s, %s, %s)',
                                       (name, description, start, end))
                    if servers:
                        cursor = await conn.execute('SELECT id FROM campaigns WHERE name ILIKE %s', (name,))
                        campaign_id = (await cursor.fetchone())[0]
                        for server in servers:
                            # add this server to the server list
                            await conn.execute('INSERT INTO campaigns_servers VALUES (%s, %s) ON CONFLICT DO NOTHING',
                                               (campaign_id, server.name))
                elif command == 'start':
                    cursor = await conn.execute("""
                        SELECT id FROM campaigns WHERE name ILIKE %s 
                        AND (now() AT TIME ZONE 'utc') BETWEEN start 
                        AND COALESCE(stop, (now() AT TIME ZONE 'utc'))
                    """, (name,))
                    if cursor.rowcount == 0:
                        await conn.execute('INSERT INTO campaigns (name) VALUES (%s)', (name,))
                    if servers:
                        cursor = await conn.execute("""
                            SELECT id FROM campaigns WHERE name ILIKE %s 
                            AND (now() AT TIME ZONE 'utc') BETWEEN start 
                            AND COALESCE(stop, (now() AT TIME ZONE 'utc'))
                        """, (name,))
                        # don't use currval() in here, as we can't rely on the sequence name
                        campaign_id = (await cursor.fetchone())[0]
                        for server in servers:
                            await conn.execute("INSERT INTO campaigns_servers VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                               (campaign_id, server.name,))
                elif command == 'stop':
                    await conn.execute("""
                        UPDATE campaigns SET stop = (now() AT TIME ZONE 'utc') WHERE name ILIKE %s 
                        AND (now() AT TIME ZONE 'utc') BETWEEN start 
                        AND COALESCE(stop, (now() AT TIME ZONE 'utc') )
                    """, (name,))
                elif command == 'delete':
                    cursor = await conn.execute('SELECT id FROM campaigns WHERE name ILIKE %s', (name,))
                    campaign_id = (await cursor.fetchone())[0]
                    await conn.execute('DELETE FROM campaigns_servers WHERE campaign_id = %s', (campaign_id,))
                    await conn.execute('DELETE FROM campaigns WHERE id = %s', (campaign_id,))

    @event(name="startCampaign")
    async def startCampaign(self, server: Server, data: dict) -> None:
        name = data['name'] or '_internal_'
        try:
            await self.campaign('start', servers=[server], name=name)
        except psycopg.errors.UniqueViolation:
            await self.resetCampaign(server, data)

    @event(name="stopCampaign")
    async def stopCampaign(self, server: Server, _: dict) -> None:
        _, name = utils.get_running_campaign(self.bot, server)
        if name:
            await self.campaign('delete', name=name)

    @event(name="resetCampaign")
    async def resetCampaign(self, server: Server, _: dict) -> None:
        _, name = utils.get_running_campaign(self.bot, server)
        if name:
            await self.campaign('delete', name=name)
        else:
            name = '_internal_'
        await self.campaign('start', servers=[server], name=name)

    async def _join(self, server: Server, player: Player, params: list[str]):
        coalition = params[0] if params else ''
        if coalition.casefold() not in ['blue', 'red']:
            player.sendChatMessage(_("Usage: {}join <blue|red>").format(self.prefix))
            return
        if player.coalition:
            player.sendChatMessage(_("You are a member of coalition {} already.").format(coalition))
            return
        # update the database
        async with self.apool.connection() as conn:
            async with conn.transaction():
                # check if the player is eligible to change the coalitions
                lock_time = server.locals['coalitions'].get('lock_time', '1 day')
                cursor = await conn.execute(f"""
                    SELECT coalition FROM coalitions 
                    WHERE server_name = %s AND player_ucid = %s 
                    AND coalition_leave > ((now() AT TIME ZONE 'utc') - interval '{lock_time}')
                """, (server.name, player.ucid))
                if cursor.rowcount == 1:
                    if (await cursor.fetchone())[0] != coalition.casefold():
                        player.sendChatMessage(_("You can't join the {coalition} coalition in-between {lock_time} of "
                                                 "leaving a coalition.").format(
                            coalition=coalition, lock_time=server.locals['coalitions'].get('lock_time', '1 day')))
                        await self.bot.audit(
                            f"{player.display_name} tried to join a new coalition in-between the time limit.",
                            user=player.ucid)
                        return

                # set the new coalition
                await conn.execute("""
                    INSERT INTO coalitions (server_name, player_ucid, coalition, coalition_leave) 
                    VALUES (%s, %s, %s, NULL) 
                    ON CONFLICT (server_name, player_ucid) DO UPDATE 
                    SET coalition = excluded.coalition, coalition_leave = excluded.coalition_leave
                """, (server.name, player.ucid, coalition))
                player.coalition = Coalition(coalition)

        # welcome them in DCS
        password = self.get_coalition_password(server, player.coalition)
        player.sendChatMessage(_('Welcome to the {} side!').format(coalition))
        if password:
            player.sendChatMessage(_("Your coalition password is {}").format(password))

        # set the discord role
        if player.member:
            roles = {
                Coalition.RED: server.locals['coalitions']['red_role'],
                Coalition.BLUE: server.locals['coalitions']['blue_role']
            }
            # noinspection PyAsyncCall
            asyncio.create_task(player.add_role(roles[player.coalition]))

    async def reset_coalitions(self, server: Server, discord_roles: bool):
        guild = self.bot.guilds[0]
        roles = {
            "red": self.bot.get_role(server.locals['coalitions']['red_role']),
            "blue": self.bot.get_role(server.locals['coalitions']['blue_role'])
        }
        async with self.apool.connection() as conn:
            async with conn.transaction():
                cursor = await conn.execute("""
                    SELECT p.ucid, p.discord_id, c.coalition 
                    FROM players p, coalitions c 
                    WHERE p.ucid = c.player_ucid AND c.server_name = %s AND c.coalition IS NOT NULL
                """, (server.name,))
                rows = await cursor.fetchall()
                for row in rows:
                    if discord_roles and row[1] != -1:
                        member = guild.get_member(row[1])
                        if member:
                            try:
                                await member.remove_roles(roles[row[2]])
                            except discord.Forbidden:
                                await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
                    await cursor.execute('DELETE FROM coalitions WHERE server_name = %s AND player_ucid = %s',
                                         (server.name, row[0]))
        server.send_to_dcs({"command": "resetUserCoalitions"})

    @event(name="resetUserCoalitions")
    async def resetUserCoalitions(self, server: Server, data: dict) -> None:
        if not server.locals.get('coalitions'):
            self.log.warning(
                f'Event "resetUserCoalitions" received, but COALITIONS are disabled on server "{server.name}"')
            return
        discord_roles = data.get('discord_roles', False)
        # noinspection PyAsyncCall
        asyncio.create_task(self.reset_coalitions(server, discord_roles))

    @chat_command(name="join", usage="<red|blue>", help=_("join a coalition"))
    async def join(self, server: Server, player: Player, params: list[str]):
        await self._join(server, player, params)

    @chat_command(name="leave", help=_("leave your coalition"))
    async def leave(self, server: Server, player: Player, params: list[str]):
        if not self.get_coalition(server, player):
            player.sendChatMessage(
                _("You are not a member of any coalition. You can join one with {}join blue|red.").format(self.prefix))
            return
        # update the database
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE coalitions SET coalition_leave = (now() AT TIME ZONE 'utc') 
                    WHERE server_name = %s AND player_ucid = %s
                """, (server.name, player.ucid))
                player.sendChatMessage(_("You left the {} coalition!").format(player.coalition.name))
        # remove discord roles
        if player.member:
            roles = {
                Coalition.RED: server.locals['coalitions']['red_role'],
                Coalition.BLUE: server.locals['coalitions']['blue_role']
            }
            # noinspection PyAsyncCall
            asyncio.create_task(player.remove_role(roles[player.coalition]))
        player.coalition = None

    @chat_command(name="red", help=_("join the red side"))
    async def red(self, server: Server, player: Player, _: list[str]):
        await self._join(server, player, ["red"])

    @chat_command(name="blue", help=_("join the blue side"))
    async def blue(self, server: Server, player: Player, params: list[str]):
        await self._join(server, player, ["blue"])

    async def _coalition(self, server: Server, player: Player):
        coalition = self.get_coalition(server, player)
        if coalition:
            player.sendChatMessage(_("You are a member of the {} coalition."))
        else:
            player.sendChatMessage(
                _("You are not a member of any coalition. You can join one with {}join blue|red.").format(self.prefix))

    @chat_command(name="coalition", help=_("displays your current coalition"))
    async def coalition(self, server: Server, player: Player, params: list[str]):
        # noinspection PyAsyncCall
        asyncio.create_task(self._coalition(server, player))

    async def _password(self, server: Server, player: Player):
        coalition = self.get_coalition(server, player)
        if not coalition:
            player.sendChatMessage(
                _("You are not a member of any coalition. You can join one with {}join blue|red.").format(self.prefix))
            return
        password = self.get_coalition_password(server, player.coalition)
        if password:
            player.sendChatMessage(_("Your coalition password is {}").format(password))
        else:
            player.sendChatMessage(_("There is no password set for your coalition."))

    @chat_command(name="password", aliases=["passwd"], help=_("displays the coalition password"))
    async def password(self, server: Server, player: Player, params: list[str]):
        # noinspection PyAsyncCall
        asyncio.create_task(self._password(server, player))

    @chat_command(name="flag", roles=['DCS Admin', 'GameMaster'], usage=_("<flag> [value]"),
                  help=_("reads or sets a flag"))
    async def flag(self, server: Server, player: Player, params: list[str]):
        if not params:
            player.sendChatMessage(_("Usage: {}flag <flag> [value]").format(self.prefix))
            return
        flag = params[0]
        if len(params) > 1:
            value = int(params[1])
            server.send_to_dcs({
                "command": "setFlag",
                "flag": flag,
                "value": value
            })
            player.sendChatMessage(f"Flag {flag} set to {value}.")
        else:
            response = await server.send_to_dcs_sync({"command": "getFlag", "flag": flag})
            player.sendChatMessage(_("Flag {flag} has value {value}.").format(flag=flag, value=response['value']))
