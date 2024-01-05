from core import EventListener, Server, Channel, event


class WeaponBlockingListener(EventListener):
    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict):
        # server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if not config:
            return
        
        # if self.plugin.get_config(server) and server.status == Status.RUNNING:
        if data['eventName'] in ['S_EVENT_SHOT', 'S_EVENT_SHOOTING_START'] and data.get('initiator'):
            initiator = data['initiator']
            unit_type = initiator['unit_type']
            weapon_name = data['weapon']['name']

            # return if they are not a human player
            if 'name' not in initiator:
                return
            player = server.get_player(name=initiator['name'])

            # Define action and message
            if 'action' in config:
                action = config["action"]
            else:
                action = "move_to_spec"
            if 'message' in config:
                message = config["message"].format(player_name=player.name,
                                                   weapon_name=weapon_name,
                                                   unit_type=unit_type)
            else:
                message = f"Firing {weapon_name} from {unit_type} is not allowed on this server!"
            
            # if restricted_units is defined
            if 'restricted_units' in config:
                # Get the config for that unit type
                unit_config = next((item for item in config['restricted_units'] if item['unit_type'] == unit_type),
                                   None)
                # return if unit_type doesn't have a config
                if not unit_config:
                    return
                
                weapon_status = None
                if unit_config['mode'] == 'whitelist':
                    # If that weapon is not in the whitelist
                    if 'weapons' not in unit_config or weapon_name not in unit_config['weapons']:
                        weapon_status = "non-whitelisted"
                elif unit_config['mode'] == 'blacklist':
                    # If weapons is defined and that weapon is in the blacklist
                    if 'weapons' in unit_config and weapon_name in unit_config['weapons']:
                        weapon_status = "blacklisted"
                
                if not weapon_status:
                    return
                
                # Log to debug
                self.log.debug(f'weaponblocking - kicking "{player.name}" to spectators for firing {weapon_status} '
                               f'weapon "{weapon_name}" from "{unit_type}"')

                # Punish the player
                if action == "move_to_spec":
                    player.sendPopupMessage(message, 30)
                    server.move_to_spectators(player)
                    player.sendChatMessage(message)
                    action_taken = "moved to spectators"
                elif action == "kick":
                    server.kick(player, message)
                    action_taken = "kicked"
                else:
                    return

                # Log message to admin channel
                if player.member:
                    await self.bot.get_channel(server.channels[Channel.ADMIN]).send(
                        f'Member {player.member.display_name} has been {action_taken} for firing {weapon_name} '
                        f'from {unit_type}.')
                else:
                    await self.bot.get_channel(server.channels[Channel.ADMIN]).send(
                        f"Player {player.name} (ucid={player.ucid}) has been {action_taken} for firing {weapon_name} "
                        f"from {unit_type}.")
