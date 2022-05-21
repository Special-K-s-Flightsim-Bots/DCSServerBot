from core import EventListener, utils


class HelpListener(EventListener):
    async def onChatCommand(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        if data['subcommand'] == 'help':
            messages = [
                'You can use the following commands:\n',
                '"-linkme token" link your user to Discord',
                '"-atis airport" display ATIS information'
            ]
            player = utils.get_player(self, server['server_name'], id=data['from_id'])
            member = utils.get_member_by_ucid(self, player['ucid'], True)
            dcs_admin = member is not None and utils.check_roles(['DCS Admin'], member)
            if dcs_admin:
                messages.append('"-kick \'name\'"  kick a user')
                messages.append('"-restart time" restart the running mission')
                messages.append('"-list"         list available missions')
                messages.append('"-load number"  load a specific mission')
                messages.append('"-preset"       load a specific weather preset')
            game_master = member is not None and utils.check_roles(['GameMaster'], member)
            if dcs_admin or game_master:
                messages.append('"-flag"         reads or sets a flag')
            if 'punishment' in self.bot.plugins:
                messages.append('"-penalty"      displays your penalty points')
                messages.append('"-forgive"      forgive another user for teamhits/-kills')
            if 'slotblocking' in self.bot.plugins:
                messages.append('"-credits"      displays your credits')
            if self.config.getboolean(server['installation'], 'COALITIONS'):
                messages.append('"-join coal."   join a coalition')
                messages.append('"-leave"        leave a coalition')
                messages.append('"-password"     shows coalition password')
                messages.append('"-coalition"    shows your current coalition')
            utils.sendUserMessage(self, server, data['from_id'], '\n'.join(messages), 30)

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] != 1:
            utils.sendChatMessage(self, data['server_name'], data['id'], 'Use "-help" for commands.')
