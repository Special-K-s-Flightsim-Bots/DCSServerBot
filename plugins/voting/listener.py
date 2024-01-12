import asyncio
import os

from core import EventListener, chat_command, Server, Player, utils, Coalition, Plugin, Status
from functools import partial
from itertools import islice
from typing import Iterable, Optional

# ruamel YAML support
from ruamel.yaml import YAML

from services import DCSServerBot

yaml = YAML()


all_votes: dict[str, 'Vote'] = dict()


class Vote:
    def __init__(self, bot: DCSServerBot, server: Server, config: dict, what: str):
        self.loop = asyncio.get_event_loop()
        self.bot = bot
        self.server = server
        self.config = config
        self.what = what
        self.votes:  dict[int, int] = dict()
        self.voter: list[Player] = list()
        self.tasks: list[asyncio.TimerHandle] = []
        self.start()

    def cancel(self):
        for task in self.tasks:
            task.cancel()

    def get_lists(self) -> Iterable[str]:
        if self.what == 'mission':
            return self.config.get('mission', [os.path.basename(x) for x in self.server.settings['missionList']])
        elif self.what == 'preset':
            return self.config.get('preset', utils.get_presets())

    def get_leading_vote(self) -> str:
        if self.votes:
            win_id = max(self.votes, key=self.votes.get) - 1
            winner = next(islice(self.get_lists(), win_id, None))
            if winner:
                return f"\nCurrent leading vote: {self.what.title()} \"{winner}\""
        return ""

    def print(self, player: Optional[Player] = None):
        message = f"You can now vote to change the {self.what} of this server.\n"
        for idx, element in enumerate(self.get_lists()):
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

    async def end_vote(self):
        global all_votes

        if not self.votes:
            message = "Voting finished without any vote for a change."
            self.server.sendPopupMessage(Coalition.ALL, message)
            self.server.sendChatMessage(Coalition.ALL, message)
        else:
            win_id = max(self.votes, key=self.votes.get) - 1
            winner = next(islice(self.get_lists(), win_id, None))
            message = (f"{self.what.title()} \"{winner}\" won with {self.votes[win_id + 1]} votes!\n"
                       f"The mission will change in 60s.")
            self.server.sendChatMessage(Coalition.ALL, message)
            self.server.sendPopupMessage(Coalition.ALL, message)
            await asyncio.sleep(60)
            if self.what == 'mission':
                for idx, mission in enumerate(self.server.settings['missionList']):
                    if winner in mission:
                        await self.server.loadMission(mission=idx + 1, modify_mission=False)
                        break
                else:
                    mission = os.path.join(await self.server.get_missions_dir(), winner)
                    await self.server.loadMission(mission=mission, modify_mission=False)
                    await self.bot.audit("Mission changed by voting", server=self.server)
            else:
                filename = await self.server.get_current_mission_file()
                if not self.server.node.config.get('mission_rewrite', True):
                    await self.server.stop()
                new_filename = await self.server.modifyMission(filename, utils.get_preset(winner))
                if new_filename != filename:
                    await self.server.replaceMission(int(self.server.settings['listStartIndex']), new_filename)
                await self.server.restart(modify_mission=False)
                if self.server.status == Status.STOPPED:
                    await self.server.start()
                await self.bot.audit("Mission preset changed by voting", server=self.server)
        del all_votes[self.server.name]


class VotingListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)

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

    async def create_vote(self, bot: DCSServerBot, server: Server, player: Player, config: dict, params: list[str]):
        global all_votes

        choices = []
        if 'preset' not in config or config.get('preset'):
            choices.append('preset')
        if 'mission' not in config or config.get('mission'):
            choices.append('mission')
        if len(choices) > 1:
            if not params:
                player.sendChatMessage(f'Usage: {self.prefix}vote <mission|preset>')
                return
            else:
                what = params[0].lower()
        elif len(choices) == 1:
            what = choices[0]
        else:
            return
        if what not in choices:
            player.sendChatMessage(f"Invalid option '{what}'.")
            return
        config['prefix'] = self.prefix
        all_votes[server.name] = Vote(bot=bot, server=server, config=config, what=what)
        await bot.audit("created a voting", user=player.member or player.ucid, server=server)

    @chat_command(name="vote", help="start a voting or vote for a change")
    async def vote(self, server: Server, player: Player, params: list[str]):
        global all_votes

        config = self.get_config(server=server)
        if not config:
            return
        if server.name in all_votes:
            if len(params) == 1 and params[0] == 'cancel':
                if utils.check_roles(['DCS Admin'], player.member):
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
        await self.create_vote(self.bot, server, player, config, params)
