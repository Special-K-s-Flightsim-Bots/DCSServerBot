import re
import discord

from core import EventListener, Plugin, Server, Status, utils, event, Side
from typing import Union, cast, Optional
from plugins.creditsystem.player import CreditPlayer


class SlotBlockingListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)

    def _migrate_roles(self, restriction: dict, config: dict) -> None:
        guild = self.bot.guilds[0]
        roles = config.get('VIP', {}).get('discord', [])
        if isinstance(roles, str) and not roles.isnumeric():
            config['VIP']['discord'] = discord.utils.get(guild.roles, name=roles).id
        elif roles:
            config['VIP']['discord'] = [
                discord.utils.get(guild.roles, name=role).id for role in roles if not role.isnumeric()
            ]
        for restriction in config.get('restricted', []):
            if 'discord' in restriction:
                if isinstance(restriction['discord'], list):
                    _roles = []
                    for _role in restriction['discord']:
                        if not _role.isnumeric():
                            role = discord.utils.get(guild.roles, name=restriction['discord'])
                            if not role:
                                self.log.warning(f"Role {restriction['discord']} not found!")
                            else:
                                _roles.append(role.id)
                        else:
                            _roles.append(_role)
                    restriction['discord'] = _roles
                else:
                    role = discord.utils.get(guild.roles, name=restriction['discord'])
                    if not role:
                        self.log.warning(f"Role {restriction['discord']} not found!")
                    else:
                        restriction['discord'] = role.id

    def _load_params_into_mission(self, server: Server):
        config: dict = self.plugin.get_config(server, use_cache=False)
        if config:
            self._migrate_roles(config)
            server.send_to_dcs({
                'command': 'loadParams',
                'plugin': self.plugin_name,
                'params': config
            })
            guild = self.bot.guilds[0]
            roles = []
            for role in config.get('VIP', {}).get('discord', []):
                roles.append(self.bot.get_role(role))
            if not roles:
                return
            # get all linked members
            batch = []
            with self.pool.connection() as conn:
                for row in conn.execute("""
                    SELECT ucid, discord_id FROM players WHERE discord_id != -1 AND LENGTH(ucid) = 32
                """).fetchall():
                    member = guild.get_member(row[1])
                    if not member:
                        continue
                    if any(role in roles for role in member.roles):
                        batch.append({
                            'ucid': row[0],
                            'roles': [x.id for x in member.roles]
                        })
            server.send_to_dcs({'command': 'uploadUserRoles', 'batch': batch})

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        # the server is running already
        if data['channel'].startswith('sync-'):
            self._load_params_into_mission(server)

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        self._load_params_into_mission(server)

    def _get_points(self, server: Server, player: CreditPlayer) -> int:
        config = self.plugin.get_config(server)

        for unit in config.get('restricted', []):
            is_unit_type = unit.get('unit_type') == player.unit_type
            is_unit_name = unit.get('unit_name') in player.unit_name
            is_group_name = unit.get('group_name') in player.group_name

            if is_unit_type or is_unit_name or is_group_name:
                is_player_slot = player.sub_slot == 0 and 'points' in unit
                is_crew_slot = player.sub_slot > 0 and 'crew' in unit

                if is_player_slot:
                    return unit['points']
                elif is_crew_slot:
                    return unit['crew']
        return 0

    def _get_costs(self, server: Server, data: Union[CreditPlayer, dict]) -> int:
        def _get_data(data: Union[CreditPlayer, dict], attribute_name: str):
            return getattr(data, attribute_name) if isinstance(data, CreditPlayer) else data[attribute_name]

        def _is_unit_match(unit: dict, attribute_name: str, attribute_value: str) -> bool:
            if attribute_name in unit and re.search(unit[attribute_name],
                                                    utils.lua_pattern_to_python_regex(attribute_value)):
                return True
            return False

        config = self.plugin.get_config(server)

        attributes = ['unit_type', 'unit_name', 'group_name']
        for unit in config.get('restricted', []):
            for attribute in attributes:
                if _is_unit_match(unit, attribute, _get_data(data, attribute)):
                    return unit.get('costs', 0)
        return 0

    def _is_vip(self, config: dict, data: dict) -> bool:
        if 'VIP' not in config:
            return False
        if 'ucid' in config['VIP']:
            ucid = config['VIP']['ucid']
            if (isinstance(ucid, str) and ucid == data['ucid']) or (isinstance(ucid, list) and data['ucid'] in ucid):
                return True
        if 'discord' in config['VIP']:
            member = self.bot.get_member_by_ucid(data['ucid'])
            return utils.check_roles(config['VIP']['discord'], member) if member else False
        return False

    @event(name="onPlayerConnect")
    async def onPlayerConnect(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config or data['id'] == 1:
            return
        if self._is_vip(config, data) and 'audit' in config['VIP'] and config['VIP']['audit']:
            member = self.bot.get_member_by_ucid(data['ucid'])
            if member:
                message = "VIP member {} joined".format(utils.escape_string(member.display_name))
            else:
                message = "VIP user {}(ucid={} joined".format(utils.escape_string(data['name']), data['ucid'])
            await self.bot.audit(message, server=server)

    def _pay_for_plane(self, server: Server, player: CreditPlayer, data: Optional[dict] = None):
        plane_costs = self._get_costs(server, data if data else player)
        if not plane_costs:
            return
        old_points = player.points
        player.points -= plane_costs
        player.audit('buy', old_points, 'Points taken for using a reserved module')
        player.deposit = plane_costs

    def _payback(self, server: Server, player: CreditPlayer, reason: str, *, plane_only: bool = False):
        old_points = player.points
        plane_costs = self._get_costs(server, player)
        if plane_only:
            player.points += plane_costs
        else:
            achieved_points = player.deposit - plane_costs
            multiplier = self.get_config(server).get('multiplier', 1)
            player.points += plane_costs + (achieved_points * multiplier)
        player.audit('payback', old_points, reason)
        player.deposit = 0

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config or 'side' not in data:
            return
        player: CreditPlayer = cast(CreditPlayer, server.get_player(ucid=data['ucid'], active=True))
        if not player:
            return
        # if payback is enabled, we need to clear the deposit on any slot change
        if config.get('payback', False):
            player.deposit = 0
        elif Side(data['side']) != Side.SPECTATOR and data['sub_slot'] == 0:
            self._pay_for_plane(server, player, data)

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config.get('payback', False) or not config.get('restricted') or server.status != Status.RUNNING:
            return
        if data['eventName'] == 'kill' and data['arg4'] != -1:
            player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg4']))
            if not player:
                return
            # give points back on team-kill
            if data['arg3'] == data['arg6']:
                self._payback(server, player, 'Credits refund for being team-killed')
        elif data['eventName'] == 'landing':
            # payback on landing
            player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg1']))
            if player and player.deposit > 0:
                self._payback(server, player, 'Credits for RTB')
        elif data['eventName'] == 'takeoff':
            # take deposit on takeoff
            player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg1']))
            if player and player.deposit == 0 and int(player.sub_slot) == 0:
                self._pay_for_plane(server, player)
        elif data['eventName'] == 'mission_end':
            # give all players their credit back, if the mission ends, and they are still airborne
            for player in server.players.values():
                self._payback(server, player, 'Refund on mission end', plane_only=True)
