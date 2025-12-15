import asyncio
import discord

from core import EventListener, Server, Status, utils, event, chat_command, get_translation, DataObjectFactory
from typing import cast, TYPE_CHECKING, Literal

from .player import CreditPlayer
from .squadron import Squadron

if TYPE_CHECKING:
    from .commands import CreditSystem

_ = get_translation(__name__.split('.')[1])


class CreditSystemListener(EventListener["CreditSystem"]):

    def __init__(self, plugin: "CreditSystem"):
        super().__init__(plugin)
        self.squadrons: dict[str, Squadron] = {}

    @staticmethod
    def get_points_per_kill(config: dict, data: dict) -> int:
        default = 1
        if 'points_per_kill' in config:
            for unit in config['points_per_kill']:
                if 'category' in unit and data.get('victimCategory', 'Planes') != unit['category']:
                    continue
                if 'unit_type' in unit and unit['unit_type'] != data['arg5']:
                    continue
                if 'type' in unit and ((unit['type'] == 'AI' and int(data['arg4']) != -1) or
                                       (unit['type'] == 'Player' and int(data['arg4']) == -1)):
                    continue
                if 'category' in unit or 'unit_type' in unit or 'type' in unit:
                    return unit['points']
                elif 'default' in unit:
                    default = unit['default'] if data.get('victimCategory', 'Planes') != 'Structures' else 0
        return default if data.get('victimCategory', 'Planes') != 'Structures' else 0

    def get_initial_points(self, player: CreditPlayer, config: dict) -> int:
        if not config or 'initial_points' not in config:
            return 0
        if isinstance(config['initial_points'], int):
            return config['initial_points']
        elif isinstance(config['initial_points'], list):
            roles = [x.id for x in player.member.roles] if player.member else []
            for element in config['initial_points']:
                if 'discord' in element:
                    role_ids = utils.get_role_ids(self.plugin, element['discord'])
                    if any(item in roles for item in role_ids):
                        return element['points']
                elif 'default' in element:
                    return element['default']
        return 0

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        config = self.plugin.get_config(server)
        player = cast(CreditPlayer, server.get_player(ucid=data['ucid']))
        if not player:
            return
        if player.points == -1:
            # do not add initial points to squadrons
            squadron = player.squadron
            player.squadron = None
            player.points = self.get_initial_points(player, config)
            player.audit('init', 0, _('Initial points received'))
            player.squadron = squadron
        else:
            asyncio.create_task(server.send_to_dcs({
                'command': 'updateUserPoints',
                'ucid': player.ucid,
                'points': player.points
            }))
        if config:
            asyncio.create_task(player.sendChatMessage(_("{name}, you currently have {credits} credit points.").format(
                name=player.name, credits=player.points)))

    @event(name="addUserPoints")
    async def addUserPoints(self, server: Server, data: dict) -> None:
        if data.get('points', 0) == 0:
            return
        player: CreditPlayer = cast(CreditPlayer, server.get_player(name=data.get('name')))
        if not player:
            return

        config = self.plugin.get_config(server)
        old_points = player.points
        points_to_add = int(data['points'])
        player.deposit += points_to_add * config.get('multiplier', 1.0)
        # only add the credit points directly if points_on_rtb is false
        if not config.get('points_on_rtb', False):
            player.points += points_to_add
            if old_points != player.points:
                player.audit('mission', old_points, data.get('reason', _('Unknown mission achievement')))

    @event(name="addSquadronPoints")
    async def addSquadronPoints(self, server: Server, data: dict) -> None:
        if data['points'] != 0:
            squadron = self.squadrons.get(data['squadron'])
            if not squadron:
                campaign_id, name = utils.get_running_campaign(self.node, server)
                if not campaign_id:
                    self.log.warning("You need an active campaign to use squadron credits!")
                    return
                squadron = DataObjectFactory().new(Squadron, node=self.node, name=data['squadron'],
                                                   campaign_id=campaign_id)
                self.squadrons[data['squadron']] = squadron
            old_points = squadron.points
            squadron.points += int(data['points'])
            if old_points != squadron.points:
                squadron.audit('mission', old_points, data.get('reason', _('Unknown mission achievement')))

    async def get_flighttime(self, ucid: str, campaign_id: int) -> int:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))), 0) AS playtime 
                FROM statistics s JOIN missions m ON m.id = s.mission_id 
                JOIN campaigns c ON c.id = %(campaign_id)s
                JOIN campaigns_servers cs ON cs.campaign_id = c.id
                WHERE s.player_ucid = %(ucid)s 
                AND m.server_name = cs.server_name 
                AND tsrange(s.hop_on, s.hop_off) && tsrange(c.start, c.stop)
            """, {"campaign_id": campaign_id, "ucid": ucid})
            return int((await cursor.fetchone())[0])

    async def process_achievements(self, server: Server, player: CreditPlayer):

        async def manage_role(role: str | int, action: Literal['add', 'remove']):
            _role = self.bot.get_role(role)
            if not _role:
                self.log.error(f"Role {role} not found in your Discord!")
                return
            try:
                if action == "add" and _role not in member.roles:
                    await member.add_roles(_role)
                    await self.bot.audit(f"achieved the rank {_role.name}", user=member)
                elif action == "remove" and _role in member.roles:
                    await member.remove_roles(_role)
                    await self.bot.audit(f"lost the rank {_role.name}", user=member)
            except discord.Forbidden:
                self.log.error(
                    f'The bot needs the "Manage Roles" permission or needs to be placed higher than role {_role.name}!')

        async def manage_badge(badge: dict, action: Literal['add', 'remove']):
            if action == "add":
                async with self.apool.connection() as conn:
                    async with conn.transaction():
                        await conn.execute("""
                            INSERT INTO players_badges (campaign_id, player_ucid, badge_name, badge_url) 
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (campaign_id, player_ucid) DO NOTHING
                        """, (campaign_id, player.ucid, badge['name'], badge['img']))
                        await self.bot.audit(f"achieved the badge {badge['name']}", user=player.ucid)
            elif action == "remove":
                async with self.apool.connection() as conn:
                    async with conn.transaction():
                        await conn.execute("""
                            DELETE FROM players_badges WHERE campaign_id = %s AND player_ucid = %s
                        """, (campaign_id, player.ucid))
                        await self.bot.audit(f"lost the badge {badge['name']}", user=player.ucid)

        config: dict = self.plugin.get_config(server)
        if 'achievements' not in config:
            return

        campaign_id, _ = utils.get_running_campaign(self.node, server)
        # only members can get roles
        member = player.member
        playtime = (await self.get_flighttime(player.ucid, campaign_id)) / 3600.0
        sorted_achievements = sorted(config['achievements'],
                                     key=lambda x: x['credits'] if 'credits' in x else x['playtime'],
                                     reverse=True)
        role_given = badge_given = False
        for achievement in sorted_achievements:
            if role_given or badge_given:
                if role_given:
                    await manage_role(achievement['role'], 'remove')
                if badge_given:
                    await manage_badge(achievement['badge'], 'remove')
                continue
            if achievement.get('combined'):
                if ('credits' in achievement and player.points >= achievement['credits']) and \
                        ('playtime' in achievement and playtime >= achievement['playtime']):
                    if 'role' in achievement and member:
                        await manage_role(achievement['role'], 'add')
                        role_given = True
                    if 'badge' in achievement:
                        await manage_badge(achievement['badge'], 'add')
                        badge_given = True
                else:
                    if 'role' in achievement and member:
                        await manage_role(achievement['role'], 'remove')
                    if 'badge' in achievement:
                        await manage_badge(achievement['badge'], 'remove')
            else:
                if ('credits' in achievement and player.points >= achievement['credits']) or \
                        ('playtime' in achievement and playtime >= achievement['playtime']):
                    if 'role' in achievement and member:
                        await manage_role(achievement['role'], 'add')
                        role_given = True
                    if 'badge' in achievement:
                        await manage_badge(achievement['badge'], 'add')
                        badge_given = True
                else:
                    if 'role' in achievement and member:
                        await manage_role(achievement['role'], 'remove')
                    if 'badge' in achievement:
                        await manage_badge(achievement['badge'], 'remove')

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config or server.status != Status.RUNNING:
            return
        if data['eventName'] == 'kill':
            # players gain points only if they don't kill themselves and no teamkills
            if data['arg1'] != -1 and data['arg1'] != data['arg4'] and data['arg3'] != data['arg6']:
                # Multicrew - pilot and all crew members gain points
                for player in server.get_crew_members(server.get_player(id=data['arg1'])):  # type: CreditPlayer
                    ppk = self.get_points_per_kill(config, data)
                    if ppk:
                        old_points = player.points
                        # We will add the PPK to the deposit to allow for multiplied paybacks
                        # (to be configured in Slotblocking)
                        player.deposit += ppk * config.get('multiplier', 1.0)
                        player.points += ppk
                        player.audit('kill', old_points, _("for killing {}").format(data['arg5']))
                        victim = server.get_player(id=data['arg4'])
                        message_kill = config.get('messages', {}).get('message_kill')
                        if message_kill:
                            await player.sendUserMessage(message_kill.format(
                                points=ppk, victim=victim.name if victim else f"AI in {data['arg5']}"))

        elif data['eventName'] == 'disconnect':
            server: Server = self.bot.servers[data['server_name']]
            player = cast(CreditPlayer, server.get_player(id=data['arg1']))
            if player:
                asyncio.create_task(self.process_achievements(server, player))

    @event(name="onCampaignReset")
    async def onCampaignReset(self, server: Server, data: dict) -> None:
        if server.status != Status.RUNNING:
            return
        config = self.plugin.get_config(server)
        for player in server.get_active_players():  # type: CreditPlayer
            # do not add initial points to squadrons
            squadron = player.squadron
            player.squadron = None
            player.points = self.get_initial_points(player, config)
            player.squadron = squadron

    @chat_command(name="credits", help=_("Shows your current credits"))
    async def credits(self, server: Server, player: CreditPlayer, params: list[str]):
        message = _("You currently have {} credit points").format(player.points)
        if player.deposit > 0:
            message += f", {player.deposit} on deposit"
        message += '.'
        await player.sendChatMessage(message)

    @chat_command(name="donate", help=_("Donate credits to another player"))
    async def donate(self, server: Server, player: CreditPlayer, params: list[str]):
        if len(params) < 2:
            await player.sendChatMessage(_("Usage: {prefix}{command} player points").format(
                prefix=self.prefix, command=self.donate.name))
            return
        name = ' '.join(params[:-1])
        try:
            donation = int(params[-1])
        except ValueError:
            await player.sendChatMessage(_("Usage: {prefix}{command} player points").format(
                prefix=self.prefix, command=self.donate.name))
            return
        if donation > player.points:
            await player.sendChatMessage(_("You can't donate {donation} credit points, you only have {total}!").format(
                donation=donation, total=player.points))
            return
        elif donation <= 0:
            await player.sendChatMessage(_("Your donation has to be > 0."))
            return
        receiver: CreditPlayer = cast(CreditPlayer, server.get_player(name=name))
        if not receiver:
            await player.sendChatMessage(_("Player {} not found.").format(name))
            return
        config = self.plugin.get_config(server)
        if 'max_points' in config and (receiver.points + donation) > int(config['max_points']):
            await player.sendChatMessage(
                _("Player {} would overrun the configured maximum points with this donation. "
                  "Aborted.").format(receiver))
            return
        old_points_player = player.points
        old_points_receiver = receiver.points
        squadron = player.squadron
        player.squadron = None
        player.points -= donation
        player.squadron = squadron
        player.audit('donation', old_points_player, _("Donation to player {}").format(receiver.name))
        # do not donate to a squadron
        squadron = receiver.squadron
        receiver.squadron = None
        receiver.points += donation
        receiver.squadron = squadron
        receiver.audit('donation', old_points_receiver, _("Donation from player {}").format(player.name))
        await player.sendChatMessage(_("You've donated {donation} credit points to player {name}.").format(
            donation=donation, name=name))
        await receiver.sendChatMessage(_("Player {name} donated {donation} credit points to you!").format(
            name=player.name, donation=donation))

    @chat_command(name="tip", help=_("Tip a GCI with points"))
    async def tip(self, server: Server, player: CreditPlayer, params: list[str]):
        if not params:
            await player.sendChatMessage(_("Usage: {prefix}{command} points [gci_number]").format(
                prefix=self.prefix, command=self.tip.name))
            return

        donation = int(params[0])
        if len(params) > 1:
            gci_index = int(params[1]) - 1
        else:
            gci_index = -1

        active_gci = list[CreditPlayer]()
        for p in server.get_active_players():
            if player.side == p.side and p.unit_type == "forward_observer":
                active_gci.append(cast(CreditPlayer, p))
        if not len(active_gci):
            await player.sendChatMessage(_("There is currently no {} GCI active.").format(player.side.name))
            return
        elif len(active_gci) == 1:
            gci_index = 0

        if gci_index not in range(0, len(active_gci)):
            await player.sendChatMessage(_('Multiple GCIs found, use "{}tip points gci_number".').format(self.prefix))
            for i, gci in enumerate(active_gci):
                await player.sendChatMessage(f"{i + 1}) {gci.name}")
            return
        else:
            receiver = active_gci[gci_index]

        old_points_player = player.points
        old_points_receiver = receiver.points
        player.points -= donation
        player.audit('donation', old_points_player, _("Donation to player {}").format(receiver.name))
        receiver.points += donation
        receiver.audit('donation', old_points_receiver, _("Donation from player {}").format(player.name))
        await player.sendChatMessage(
            _("You've donated {donation} credit points to GCI {name}.").format(donation=donation, name=receiver.name))
        await receiver.sendChatMessage(
            _("Player {name} donated {donation} credit points to you!").format(name=player.name, donation=donation))
