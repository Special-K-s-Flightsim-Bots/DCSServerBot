from core import EventListener, Server, Channel


class WeaponBlockingListener(EventListener):
    async def onMissionEvent(self, data):
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        # if self.plugin.get_config(server) and server.status == Status.RUNNING:
        if data['eventName'] == 'S_EVENT_SHOT' or data['eventName'] == 'S_EVENT_SHOOTING_START' and 'initiator' in data and len(data['initiator']) > 0:
            initiator = data['initiator']
            unit_type = initiator['unit_type']
            weapon_name = data['weapon']['name']

            # return if they are not a human player
            if 'name' not in initiator:
                return
            player = server.get_player(name=initiator['name'])
            

            # if restricted_units is defined
            if 'restricted_units' in config:
                # Get the config for that unit type
                unit_config = next((item for item in config['restricted_units'] if item['unit_type'] == unit_type), None)
                # return if unit_type doesn't have a config
                if not unit_config:
                    return
                
                if unit_config['mode'] == 'whitelist':
                    # If that weapon is not in the whitelist
                    if not 'weapons' in unit_config or not weapon_name in unit_config['weapons']:
                        self.log.debug(f'weaponblocking - kicking "{player.name}" to spectators for firing non-whitelisted weapon "{weapon_name}" from "{unit_type}"')

                        # Punish the player
                        message = f"Firing {weapon_name} from {unit_type} is not permitted.\nYou have been kicked back to spectators."
                        player.sendChatMessage(message)
                        player.sendPopupMessage(message, 30)
                        server.move_to_spectators(player)

                        # Log to discord
                        if player.member:
                            message = f'Member {player.member.display_name} has been moved to spectators for firing {weapon_name} from {unit_type}.'
                            await server.get_channel(Channel.ADMIN).send(message)
                            # await self.bot.audit(message)
                        else:
                            message = f"Player {player.name} (ucid={player.ucid}) has been moved to spectators for firing {weapon_name} from {unit_type}."
                            await server.get_channel(Channel.ADMIN).send(message)
                            # await self.bot.audit(message)
                
                if unit_config['mode'] == 'blacklist':
                    # If weapons is defined and that weapon is in the blacklist
                    if 'weapons' in unit_config and weapon_name in unit_config['weapons']:
                        self.log.debug(f'weaponblocking - kicking "{player.name}" to spectators for firing blacklisted weapon "{weapon_name}" from "{unit_type}"')

                        # Punish the player
                        message = f"Firing {weapon_name} from {unit_type} is not permitted.\nYou have been kicked back to spectators."
                        player.sendChatMessage(message)
                        player.sendPopupMessage(message, 30)
                        server.move_to_spectators(player)

                        # Log to discord
                        if player.member:
                            message = f'Member {player.member.display_name} has been moved to spectators for firing {weapon_name} from {unit_type}.'
                            await server.get_channel(Channel.ADMIN).send(message)
                            # await self.bot.audit(message)
                        else:
                            message = f"Player {player.name} (ucid={player.ucid}) has been moved to spectators for firing {weapon_name} from {unit_type}."
                            await server.get_channel(Channel.ADMIN).send(message)
                            # await self.bot.audit(message)