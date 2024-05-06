import asyncio

from core import EventListener, chat_command, Server, Player, utils, Coalition, event, ChatCommand
from functools import partial
from itertools import islice
from typing import Optional

# ruamel YAML support
from ruamel.yaml import YAML

from plugins.creditsystem.player import CreditPlayer
from plugins.voting.base import VotableItem

yaml = YAML()


all_votes: dict[str, 'VotingHandler'] = dict()


class VotingHandler:
    def __init__(self, listener: 'VotingListener', item: VotableItem, server: Server, config: dict):
        self.loop = asyncio.get_event_loop()
        self.listener = listener
        self.item = item
        self.server = server
        self.config = config
        self.votes:  dict[int, int] = dict()
        self.voter: list[Player] = list()
        self.tasks: list[asyncio.TimerHandle] = []
        self.start()

    def cancel(self):
        for task in self.tasks:
            task.cancel()

    def get_leading_vote(self) -> str:
        if self.votes:
            win_id = max(self.votes, key=self.votes.get) - 1
            winner = next(islice(self.item.get_choices(), win_id, None))
            if winner:
                return f"\nCurrent leading vote: \"{winner}\""
        return ""

    def print(self, player: Optional[Player] = None):
        message = self.item.print() + '\n'
        for idx, element in enumerate(self.item.get_choices()):
            message += f'{idx + 1}. {element}\n'
        message += self.get_leading_vote()
        message += f"\nUse {self.config['prefix']}vote <number> to vote for the change.\n"
        if player:
            player.sendUserMessage(message)
        else:
            self.server.sendChatMessage(Coalition.ALL, message)
            self.server.sendPopupMessage(Coalition.ALL, message, timeout=20)

    def start(self):
        self.print()
        voting_time = self.config.get('time', 300)
        message = f"You have {utils.format_time(voting_time)} to vote."
        self.server.sendChatMessage(Coalition.ALL, message)
        self.server.sendPopupMessage(Coalition.ALL, message, timeout=20)
        for reminder in sorted(self.config.get('reminder', []), reverse=True):
            if reminder >= voting_time:
                continue
            self.tasks.append(self.loop.call_later(delay=voting_time - reminder,
                                                   callback=partial(asyncio.create_task, self.remind(reminder))))
        self.tasks.append(self.loop.call_later(delay=voting_time,
                                               callback=lambda: asyncio.create_task(self.end_vote())))

    def vote(self, player: Player, num: int):
        if player in self.voter:
            player.sendChatMessage("You can only vote once.")
            return
        if num not in self.votes.keys():
            self.votes[num] = 1
        else:
            self.votes[num] += 1
        self.voter.append(player)
        player.sendChatMessage("Your vote has been counted.")

    async def remind(self, remaining: int):
        message = f"A voting is now open for another {utils.format_time(remaining)}!"
        message += self.get_leading_vote()
        self.server.sendPopupMessage(Coalition.ALL, message)

    def _get_possible_voters(self) -> int:
        if self.config.get('voter'):
            return len([x for x in self.server.get_active_players() if self.listener.check_role(x, self.config['voter'])])
        else:
            return len(self.server.get_active_players())

    def check_vote(self) -> int:
        message = "Voting finished"
        voting_rule = self.config.get('voting_rule', 'majority')
        possible_voters = self._get_possible_voters()
        if not self.votes or not possible_voters:
            message += " without any (active) participant."
        elif self.config.get('voting_threshold') and (sum(self.votes.values()) / possible_voters) < self.config.get('voting_threshold'):
            message += f" but less than {self.config['voting_threshold'] * 100}% players participated."
        elif voting_rule == 'majority':
            return max(self.votes, key=self.votes.get) - 1
        elif voting_rule == 'supermajority':
            if max(self.votes.values()) / sum(self.votes.values()) < 0.33:
                message += f' but no vote got the super-majority.'
            else:
                return max(self.votes, key=self.votes.get) - 1
        elif voting_rule == 'absolute':
            if max(self.votes.values()) / sum(self.votes.values()) < 0.5:
                message += f' but no vote got the absolute majority.'
            else:
                return max(self.votes, key=self.votes.get) - 1
        elif voting_rule == 'unanimous':
            if len(self.votes) != 1:
                message += " but the vote was not unanimous."
            else:
                return next(iter(self.votes))
        message += '\nEverything will stay as it is.'
        self.server.sendPopupMessage(Coalition.ALL, message)
        self.server.sendChatMessage(Coalition.ALL, message)
        return -1

    async def end_vote(self):
        global all_votes

        win_id = self.check_vote()
        if win_id > -1:
            winner = next(islice(self.item.get_choices(), win_id, None))
            message = f"\"{winner}\" won with {self.votes[win_id + 1]} votes!"
            self.server.sendChatMessage(Coalition.ALL, message)
            self.server.sendPopupMessage(Coalition.ALL, message)
            await self.item.execute(winner)
        del all_votes[self.server.name]


class VotingListener(EventListener):

    def can_run(self, command: ChatCommand, server: Server, player: Player) -> bool:
        config = self.get_config(server=server)
        if not config or not config.get('enabled', True):
            return False
        return super().can_run(command, server, player)

    def check_role(self, player: Player, roles: Optional[list[str]] = None) -> bool:
        if not roles:
            return True
        elif isinstance(roles, str):
            roles = [roles]
        member = self.bot.get_member_by_ucid(player.ucid)
        if not member or not utils.check_roles(roles, member):
            return False
        return True

    def do_vote(self, server: Server, player: Player, params: list[str]):
        global all_votes

        vote = all_votes.get(server.name)
        if len(params) != 1:
            vote.print(player)
            return
        elif not params[0].isnumeric():
            player.sendChatMessage(f"Usage: {self.prefix}vote <number>")
            return
        vote.vote(player, int(params[0]))

    async def create_vote(self, server: Server, player: Player, config: dict, params: list[str]):
        global all_votes

        points = config.get("credits")
        # if credits are specified, check that the player has enough
        if points and isinstance(player, CreditPlayer) and player.points < points:
            player.sendChatMessage(f"You need at least {points} credit points to create a vote.")
            return

        choices = list(config['options'].keys())
        if len(choices) > 1:
            if not params:
                player.sendChatMessage('Usage: {}vote <{}>'.format(self.prefix, '|'.join(choices)))
                return
            else:
                what = params[0].lower()
        elif len(choices) == 1:
            what = choices[0]
        else:
            return
        if what not in choices:
            player.sendChatMessage('Usage: {}vote <{}>'.format(self.prefix, '|'.join(choices)))
            return
        config['prefix'] = self.prefix
        try:
            class_name = f"plugins.voting.options.{what}.{what.title()}"
            item: VotableItem = utils.str_to_class(class_name)(
                server, config['options'].get(what), params[1:] if len(params) > 1 else None
            )
            if not item:
                self.log.error(f"Can't find class {class_name}! Voting aborted.")
                player.sendChatMessage("Voting aborted due to a server misconfiguration.")
                return
            elif not item.can_vote():
                player.sendChatMessage("This voting option is not available at the moment.")
                return
            if points and isinstance(player, CreditPlayer):
                player.points -= points
                player.sendChatMessage(f"Your voting has been created for the cost of {points} credit points.")
        except (TypeError, ValueError) as ex:
            player.sendChatMessage(str(ex))
            return
        all_votes[server.name] = VotingHandler(listener=self, item=item, server=server, config=config)
        await self.bot.audit(f"{player.display_name} created a voting", user=player.member or player.ucid,
                             server=server)

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        config = self.get_config(server)
        if 'welcome_message' not in config or not config.get('enabled', True):
            return
        player: Player = server.get_player(ucid=data['ucid'])
        if player:
            player.sendChatMessage(utils.format_string(config['welcome_message'], server=server, player=player,
                                                       prefix=self.prefix))

    @chat_command(name="vote", help="start a voting or vote for a change")
    async def vote(self, server: Server, player: Player, params: list[str]):
        global all_votes

        config = self.get_config(server=server)
        if server.name in all_votes:
            if len(params) == 1 and params[0] == 'cancel':
                if utils.check_roles(self.bot.roles['DCS Admin'], player.member):
                    all_votes[server.name].cancel()
                    del all_votes[server.name]
                    message = "The voting has been cancelled by an Admin."
                    server.sendChatMessage(Coalition.ALL, message)
                    server.sendPopupMessage(Coalition.ALL, message)
                    await self.bot.audit("cancelled voting", user=player.member, server=server)
                    return
                else:
                    player.sendChatMessage("You don't have the permission to cancel a voting.")
                    return
            elif not self.check_role(player, config.get('voter')):
                player.sendChatMessage("You don't have the permission to vote.")
                return
            self.do_vote(server, player, params)
            return
        elif not self.check_role(player, config.get('creator')):
            player.sendChatMessage("You don't have the permission to start a voting.")
            return
        if 'mission_time' in config:
            delta = int(config['mission_time']) * 60 - server.current_mission.mission_time
            if delta > 0:
                player.sendChatMessage(f"A new voting can be started in {utils.format_time(delta)}")
                return
        await self.create_vote(server, player, config, params)
