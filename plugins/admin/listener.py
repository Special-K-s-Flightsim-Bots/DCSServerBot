import discord
import shlex
from core import EventListener, Player, Server, Channel, event, chat_command


class AdminEventListener(EventListener):

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        # upload the current bans to the server
        self.plugin.update_bans(data)

    @event(name="ban")
    async def ban(self, server: Server, data: dict) -> None:
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s)',
                             (data['ucid'], 'DCSServerBot', data['reason']))
        for server in self.bot.servers.values():
            server.sendtoDCS({
                "command": "ban",
                "ucid": data['ucid'],
                "reason": data['reason']
            })

    @chat_command(name="kick", roles=['DCS Admin'], usage="<name>", help="kick a user")
    async def kick(self, server: Server, player: Player, params: list[str]):
        if not params:
            player.sendChatMessage(
                f"Usage: {self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}kick <name> [reason]")
            return
        params = shlex.split(' '.join(params))
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
        await self.bot.audit(f'Player {delinquent.display_name} kicked' +
                             (f' with reason "{reason}".' if reason != 'n/a' else '.'),
                             user=player.member)

    @chat_command(name="spec", roles=['DCS Admin'], usage="<name>", help="moves a user to spectators")
    async def spec(self, server: Server, player: Player, params: list[str]):
        if not params:
            player.sendChatMessage(
                f"Usage: {self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}spec <name> [reason]")
            return
        params = shlex.split(' '.join(params))
        name = params[0]
        if len(params) > 1:
            reason = ' '.join(params[1:])
        else:
            reason = 'n/a'
        delinquent: Player = server.get_player(name=name, active=True)
        if not delinquent:
            player.sendChatMessage(f"Player {name} not found. Use \"\" around names with blanks.")
            return
        server.move_to_spectators(delinquent, reason)
        player.sendChatMessage(f"User {name} moved to spectators.")
        await self.bot.audit(f'Player {delinquent.display_name} moved to spectators' +
                             (f' with reason "{reason}".' if reason != 'n/a' else '.'),
                             user=player.member)

    @chat_command(name="911", usage="<message>", help="send an alert to admins (misuse will be punished!)")
    async def call911(self, server: Server, player: Player, params: list[str]):
        mentions = ''
        for role_name in [x.strip() for x in self.bot.config['ROLES']['DCS Admin'].split(',')]:
            role: discord.Role = discord.utils.get(self.bot.guilds[0].roles, name=role_name)
            if role:
                mentions += role.mention
        message = ' '.join(params)
        await server.get_channel(Channel.ADMIN).send(mentions +
                                                     f" 911 call from player {player.name} (ucid={player.ucid}):"
                                                     f"```{message}```")
