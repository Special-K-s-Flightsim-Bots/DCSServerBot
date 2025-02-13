import asyncio
import trueskill

from core import EventListener, event, Server, Status, Player, chat_command, Plugin, Side, get_translation, ChatCommand
from dataclasses import dataclass, field
from datetime import datetime, timezone
from discord.ext import tasks
from plugins.competitive import rating
from trueskill import Rating
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Competitive

_ = get_translation(__name__.split('.')[1])


@dataclass
class Match:
    match_id: str
    alive: dict[Side, list[Player]] = field(default_factory=dict)
    dead: dict[Side, list[Player]] = field(default_factory=dict)
    log: list[tuple[datetime, str]] = field(default_factory=list)
    started: datetime = field(init=False)

    @property
    def teams(self) -> dict[Side, list[Player]]:
        return {
            Side.BLUE: [p for p in self.alive[Side.BLUE]] + [p for p in self.dead[Side.BLUE]],
            Side.RED: [p for p in self.alive[Side.RED]] + [p for p in self.dead[Side.RED]]
        }

    def player_join(self, player: Player):
        if player.side.name not in self.alive:
            self.alive[player.side] = []
        self.alive[player.side].append(player)

    def player_dead(self, player: Player):
        if player in self.alive[player.side]:
            self.alive[player.side].remove(player)
            if player.side.name not in self.dead:
                self.dead[player.side] = []
            self.dead[player.side].append(player)

    def is_over(self) -> Optional[Side]:
        if not len(self.alive[Side.RED]):
            return Side.BLUE
        elif not len(self.alive[Side.BLUE]):
            return Side.RED
        return None

    def is_on(self) -> bool:
        return len(self.alive[Side.BLUE]) > 0 and len(self.alive[Side.RED]) > 0


class CompetitiveListener(EventListener["Competitive"]):

    def __init__(self, plugin: "Competitive"):
        super().__init__(plugin)
        self.matches: dict[str, dict[str, Match]] = {}
        self.in_match: dict[str, dict[str, Match]] = {}
        self.active_servers: set[str] = set()

    async def processEvent(self, name: str, server: Server, data: dict) -> None:
        try:
            if name == 'registerDCSServer' or server.name in self.active_servers:
                await super().processEvent(name, server, data)
        except Exception as ex:
            self.log.exception(ex)

    async def can_run(self, command: ChatCommand, server: Server, player: Player) -> bool:
        if server.name not in self.active_servers:
            return False

    async def inform_players(self, match: Match, message: str, time: Optional[int] = 10):
        all_players = match.teams
        for player in ([p for p in all_players[Side.BLUE]] + [p for p in all_players[Side.RED]]):
            await player.sendPopupMessage(message, timeout=time)

    async def rank_teams(self, winners: list[Player], losers: list[Player]):
        r_winners, r_losers = trueskill.rate([
            [await self.get_rating(p) for p in winners],
            [await self.get_rating(p) for p in losers]
        ], [0, 1])
        for idx, p in enumerate(winners):
            await self.set_rating(p, r_winners[idx])
        for idx, p in enumerate(losers):
            await self.set_rating(p, r_losers[idx])

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, _: dict) -> None:
        config = self.get_config(server)
        if config.get('enabled', True):
            self.active_servers.add(server.name)
        else:
            self.active_servers.discard(server.name)
            return
        if server.name not in self.in_match:
            self.in_match[server.name] = {}
        if server.name not in self.matches:
            self.matches[server.name] = {}

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, _: dict) -> None:
        self.matches[server.name] = {}
        self.in_match[server.name] = {}

    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, _: dict) -> None:
        self.matches[server.name].clear()
        self.in_match[server.name].clear()

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        player: Player = server.get_player(ucid=data['ucid'])
        if player:
            # noinspection PyAsyncCall
            asyncio.create_task(self._print_trueskill(player))

    @event(name="addPlayerToMatch")
    async def addPlayerToMatch(self, server: Server, data: dict) -> None:
        players = server.get_crew_members(server.get_player(name=data['player_name']))
        for player in players:
            match = self.in_match[server.name].get(player.ucid)
            # don't re-add the player to a match
            if match:
                return
            match_id = data['match_id']
            if match_id not in self.matches[server.name]:
                match = Match(match_id=match_id)
                self.matches[server.name][match_id] = match
            else:
                match = self.matches[server.name][match_id]
                # check that we were not in the same match but died
                if player in match.dead[Side.BLUE] or player in match.dead[Side.RED]:
                    # noinspection PyAsyncCall
                    asyncio.create_task(server.move_to_spectators(
                        player, reason=_("You're not allowed to re-join the same match!")))
                    return
            is_on = match.is_on()
            match.player_join(player)
            self.in_match[server.name][player.ucid] = match
            await self.inform_players(
                match, _("Player {name} ({rating}) joined the {side} team!").format(
                    name=player.name, rating=self.calculate_rating(await self.get_rating(player)), 
                    side=player.side.name))
            # inform the players if the match is on now
            if not is_on and match.is_on():
                match.started = datetime.now(timezone.utc)
                await self.inform_players(
                    match, _("The match is on! If you die, crash or leave now, you lose!"))

    async def get_rating(self, player: Player) -> Rating:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT skill_mu, skill_sigma 
                FROM trueskill 
                WHERE player_ucid = %s
            """, (player.ucid, ))
            row = await cursor.fetchone()
            if not row:
                r = rating.create_rating()
                return await self.set_rating(player, r)
            else:
                return Rating(float(row[0]), float(row[1]))

    async def set_rating(self, player: Player, skill: Rating) -> Rating:
        async with self.apool.connection() as conn:
            await conn.execute("""
                INSERT INTO trueskill (player_ucid, skill_mu, skill_sigma) 
                VALUES (%s, %s, %s)
                ON CONFLICT (player_ucid) DO UPDATE
                SET skill_mu = excluded.skill_mu, skill_sigma = excluded.skill_sigma
            """, (player.ucid, skill.mu, skill.sigma))
        return skill

    @staticmethod
    def calculate_rating(r: Rating) -> float:
        return round(r.mu - 3.0 * r.sigma, 2)

    async def _onGameEvent(self, server: Server, data: dict) -> None:
        def print_crew(players: list[Player]) -> str:
            return ' and '.join([p.name for p in players])

        now = datetime.now(timezone.utc)
        if data['eventName'] == 'kill':
            # human players only
            if data['arg1'] == -1 or data['arg4'] == -1:
                return
            # self-kill
            if data['arg1'] == data['arg4']:
                data['eventName'] = 'self_kill'
                await self._onGameEvent(server, data)
            # Multi-crew - pilot and all crew members gain points
            killers = server.get_crew_members(server.get_player(id=data['arg1']))
            victims = server.get_crew_members(server.get_player(id=data['arg4']))
            if not killers or not victims:
                return
            # check if we are in a registered match
            match: Match = self.in_match[server.name].get(killers[0].ucid)
            if match:
                for player in victims:
                    match.log.append(
                        (now, _("{killer} in {killer_module} {what} {victim} in {victim_module} with {weapon}").format(
                            killer=print_crew(killers), killer_module=data['arg2'],
                            what=_('killed') if data['arg3'] != data['arg4'] else _('team-killed'),
                            victim=print_crew(victims), victim_module=data['arg5'], weapon=data['arg7'] or 'Guns')))
                    match.player_dead(player)
                    self.in_match[server.name].pop(player.ucid, None)
            # no, then we don't count team-kills
            elif data['arg3'] != data['arg6']:
                await self.rank_teams(killers, victims)
                if self.get_config(server).get('silent', False):
                    return
                for player in killers:
                    await player.sendChatMessage(_("You won against {loser}! Your new rating is {rating}").format(
                        loser=print_crew(victims), rating=self.calculate_rating(await self.get_rating(player))))
                for player in victims:
                    await player.sendChatMessage(_("You lost against {winner}! Your new rating is {rating}").format(
                        winner=print_crew(killers), rating=self.calculate_rating(await self.get_rating(player))))
        elif data['eventName'] in ['self_kill', 'crash']:
            players = server.get_crew_members(server.get_player(id=data['arg1']))
            if not players:
                # we should never get here
                return
            match: Match = self.in_match[server.name].get(players[0].ucid)
            if match:
                match.log.append((now, _("{player} in {module} died ({event})").format(
                    player=print_crew(players), module=data['arg2'], event=_(data['eventName']))))
                for player in players:
                    match.player_dead(player)
                    self.in_match[server.name].pop(player.ucid, None)
        elif data['eventName'] in ['eject', 'disconnect', 'change_slot']:
            player = server.get_player(id=data['arg1'])
            if not player:
                # we should never get here
                return
            # if the pilot of an MC aircraft leaves, both pilots get booted
            if player.slot == 0:
                players = server.get_crew_members(server.get_player(id=data['arg1']))
            else:
                players = [player]
            match: Match = self.in_match[server.name].get(player.ucid)
            if match:
                match.log.append((now, _("{player} in {module} died ({event})").format(
                    player=print_crew(players), module=data['arg2'], event=_(data['eventName']))))
                for player in players:
                    match.player_dead(player)
                    self.in_match[server.name].pop(player.ucid, None)

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        # noinspection PyAsyncCall
        asyncio.create_task(self._onGameEvent(server, data))

    async def _print_trueskill(self, player: Player):
        if not self.get_config(player.server).get('silent', False):
            await player.sendChatMessage(_("Your TrueSkill rating is: {}").format(
                self.calculate_rating(await self.get_rating(player))))

    @chat_command(name="skill", help=_("Display your rating"))
    async def skill(self, server: Server, player: Player, params: list[str]):
        # noinspection PyAsyncCall
        asyncio.create_task(self._print_trueskill(player))

    @tasks.loop(seconds=5)
    async def check_matches(self):
        for server in self.bot.servers.values():
            if server.status != Status.RUNNING:
                continue
            finished: list[Match] = []
            for match in self.matches[server.name].values():
                winner = match.is_over()
                if winner:
                    await self.rank_teams(match.teams[winner],
                                          match.teams[Side.BLUE if winner == Side.RED else Side.RED])
                    message = _("The match is over, {} won!\n\n"
                                "The following players are still alive:\n").format(winner.name)
                    for player in match.alive[winner]:
                        message += f"- {player.name}\n"
                    message += _("\nThis is the log of your last match:\n")
                    for time, log in match.log:
                        message += f"{time:%H:%M:%S}: {log}\n"
                    message += _("\nYour new rating is as follows:\n")
                    for player in match.teams[Side.BLUE] + match.teams[Side.RED]:
                        message += f"- {player.name}: {self.calculate_rating(await self.get_rating(player))}\n"
                    # noinspection PyAsyncCall
                    asyncio.create_task(self.inform_players(match, message, 30))
                    finished.append(match)
            for match in finished:
                del self.matches[server.name][match.match_id]
