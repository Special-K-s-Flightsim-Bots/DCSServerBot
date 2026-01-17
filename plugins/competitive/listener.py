import asyncio
import trueskill

from core import EventListener, event, Server, Status, Player, chat_command, Side, get_translation, ChatCommand, \
    Coalition, ThreadSafeDict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from discord.ext import tasks
from functools import partial
from plugins.competitive import rating
from trueskill import Rating
from typing import TYPE_CHECKING

from ..creditsystem.listener import CreditSystemListener
from ..creditsystem.squadron import Squadron

if TYPE_CHECKING:
    from .commands import Competitive

_ = get_translation(__name__.split('.')[1])

GLOBAL_MATCH_ID = 'MASTER'


@dataclass
class Match:
    match_id: str
    alive: dict[Side, list[Player]] = field(default_factory=dict)
    dead: dict[Side, list[Player]] = field(default_factory=dict)
    log: list[tuple[datetime, str]] = field(default_factory=list)
    started: datetime = field(default=None)
    finished: datetime = field(default=None)
    winner: Side | None = field(default=None)
    timer_task: asyncio.Task | None = field(default=None)
    first_join: Side | None = field(default=None)

    @property
    def teams(self) -> dict[Side, list[Player]]:
        return {
            Side.BLUE: [p for p in self.alive.get(Side.BLUE, [])] + [p for p in self.dead.get(Side.BLUE, [])],
            Side.RED: [p for p in self.alive.get(Side.RED, [])] + [p for p in self.dead.get(Side.RED, [])]
        }

    def player_join(self, player: Player):
        if player.side not in self.alive:
            self.alive[player.side] = []
        self.alive[player.side].append(player)

    def player_dead(self, player: Player):
        if player in self.alive.get(player.side, []):
            try:
                self.alive[player.side].remove(player)
            except ValueError:
                pass
            if player.side not in self.dead:
                self.dead[player.side] = []
            self.dead[player.side].append(player)

    def survivor(self) -> Side | None:
        num_red = len(self.alive.get(Side.RED, []))
        num_blue = len(self.alive.get(Side.BLUE, []))
        if num_red == 0 and num_blue > 0:
            return Side.BLUE
        elif num_blue == 0 and num_red > 0:
            return Side.RED
        elif num_red == 0 and num_blue == 0:
            return Side.UNKNOWN
        return None

    def is_on(self) -> bool:
        return len(self.alive.get(Side.BLUE, [])) > 0 and len(self.alive.get(Side.RED, [])) > 0

    # match only: get the squadron of one side
    def get_squadron(self, side: Side) -> Squadron | None:
        # noinspection PyUnresolvedReferences
        return next((x.squadron for x in self.alive.get(side, []) if x.squadron), None)

    def to_dict(self) -> dict:
        return {
            'match_id': self.match_id,
            'started': self.started.isoformat() if self.started else None,
            'finished': self.finished.isoformat() if self.finished else None,
            'winner': self.winner.name.lower() if self.winner else None,
            'alive': {
                Side.BLUE.name: [p.ucid for p in self.alive.get(Side.BLUE, [])],
                Side.RED.name: [p.ucid for p in self.alive.get(Side.RED, [])]
            },
            'log': {
                x[0].isoformat(): x[1] for x in self.log
            }
        }


class CompetitiveListener(EventListener["Competitive"]):

    def __init__(self, plugin: "Competitive"):
        super().__init__(plugin)
        self.matches: dict[str, dict[str, Match]] = ThreadSafeDict()
        self.in_match: dict[str, dict[str, Match]] = {}
        self.home_base: dict[str, dict[str, str]] = {}
        self.active_servers: set[str] = set()
        self.check_matches.start()

    async def shutdown(self) -> None:
        self.check_matches.cancel()

    async def processEvent(self, name: str, server: Server, data: dict) -> None:
        try:
            if name == 'registerDCSServer' or server.name in self.active_servers:
                await super().processEvent(name, server, data)
        except Exception as ex:
            self.log.exception(ex)

    async def can_run(self, command: ChatCommand, server: Server, player: Player) -> bool:
        return server.name in self.active_servers

    @staticmethod
    async def inform_players(server: Server, match: Match, message: str, time: int | None = 10):
        all_players = match.teams
        if match.match_id == GLOBAL_MATCH_ID:
            await server.sendPopupMessage(Coalition.ALL, message, timeout=time)
        else:
            for player in ([p for p in all_players[Side.BLUE]] + [p for p in all_players[Side.RED]]):
                await player.sendUserMessage(message, timeout=time)

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
            self.in_match[server.name] = ThreadSafeDict()
        if server.name not in self.matches:
            self.matches[server.name] = {}
        if server.name not in self.home_base:
            self.home_base[server.name] = {}

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, _: dict) -> None:
        self.matches[server.name] = {}
        self.in_match[server.name] = {}

    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, _: dict) -> None:
        self.matches[server.name].clear()
        self.in_match[server.name].clear()
        self.home_base[server.name].clear()

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        player: Player = server.get_player(ucid=data['ucid'])
        if player:
            asyncio.create_task(self._print_trueskill(player))

    async def start_match(self, server: Server, match: Match):
        match.started = datetime.now(timezone.utc)
        self.log.debug(f"The match {match.match_id} is now on.")
        await self.inform_players(
            server,
            match, _("The match is on! If you die, crash or leave now, you lose!"))

    async def countdown_with_warnings(self, match: Match, server: Server, delayed_start: int):
        start_time = datetime.now()
        end_time = start_time + timedelta(seconds=delayed_start)
        last_warning = delayed_start + 1

        # wait until the server is unpaused
        try:
            await server.wait_for_status_change(status=[Status.RUNNING], timeout=300)
        except (TimeoutError, asyncio.TimeoutError):
            pass

        # Define warning thresholds in seconds
        warning_thresholds = [
            (60, lambda t: _("The match will start in 1 minute.")),
            (30, lambda t: _("The match will start in 30 seconds.")),
            (10, lambda t: _("The match will start in 10 seconds."))
        ]
        warning_thresholds = [(x, y) for x, y in warning_thresholds if x <= delayed_start]

        while match.alive.get(Side.RED, []) or match.alive.get(Side.BLUE, []):
            remaining = int((end_time - datetime.now()).total_seconds())

            if remaining <= 0:
                break

            # For times greater than 1 minute (not including exactly 1 minute)
            if remaining > 60:
                current_minute = (remaining + 59) // 60
                if last_warning != current_minute * 60:
                    await self.inform_players(
                        server, match, _("The match will start in {} minutes.").format(current_minute), time=60)
                    last_warning = current_minute * 60
            elif remaining < 10:
                await self.inform_players(
                    server, match, _("The match will start in {} second{}.").format(
                        remaining, 's' if remaining > 1 else ''), time=1)
            else:
                # Find the highest threshold that's greater than the remaining time
                for threshold, message_func in warning_thresholds:
                    if remaining <= threshold < last_warning:
                        await self.inform_players(server, match, message_func(remaining))
                        last_warning = threshold
                        break

            await asyncio.sleep(1)

    async def check_join_timeout(self, server: Server, match: Match, waiting_side: Side):
        config = self.get_config(server)
        timeout = config['win_on_noshow']

        try:
            await asyncio.sleep(timeout)
            # If we reach this point, the timer wasn't canceled,
            # meaning the other side didn't join
            message = _("{} side lost due to not participarting {} seconds after {} side joined the match").format(
                waiting_side.name.title(), timeout, match.first_join.name.title())
            await server.sendPopupMessage(Coalition.ALL, message)
            match.log.append((datetime.now(timezone.utc), message))

            # Trigger match end with the side that took off as winner
            match.winner = match.first_join

        except asyncio.CancelledError:
            # Timer was canceled because the other side joined - do nothing
            pass

    async def _addPlayerToMatch(self, server: Server, data: dict) -> None:
        players = server.get_crew_members(server.get_player(name=data['player_name']))
        for player in players:
            match = self.in_match[server.name].get(player.ucid)
            # don't re-add the player to a match (e.g., join on takeoff)
            if match:
                return

            match_id = data['match_id']
            if match_id not in self.matches[server.name]:
                # there is no match yet, create one
                match = Match(match_id=match_id)
                self.matches[server.name][match_id] = match
            else:
                match = self.matches[server.name][match_id]

            is_on = match.is_on()
            match.player_join(player)
            self.in_match[server.name][player.ucid] = match
            self.log.debug(f"Player {player.name} ({player.ucid}) joined the match {match_id}")

            config = self.get_config(server)

            # initialize noshow timer
            if config.get('win_on_noshow'):
                if not match.is_on() and not match.timer_task:
                    match.first_join = player.side
                    waiting_side = Side.RED if player.side == Side.BLUE else Side.BLUE
                    match.timer_task = asyncio.create_task(self.check_join_timeout(server, match, waiting_side))
                else:
                    if match.timer_task:
                        match.timer_task.cancel()
                        await match.timer_task
                        match.timer_task = None
                    match.first_join = None

            if not config.get('silent', False):
                await self.inform_players(
                    server,
                    match, _("Player {name} ({rating}) joined the {side} team!").format(
                        name=player.name, rating=self.calculate_rating(await self.get_rating(player)),
                        side=player.side.name))

            # if we are in a global match, lock the seat
            if match_id == GLOBAL_MATCH_ID:
                await player.lock()

            # inform the players if the match is on now
            if config.get('debug', False) or (not is_on and match.is_on()):
                delayed_start = config.get('delayed_start', 0)
                if delayed_start > 0:
                    await self.countdown_with_warnings(match, server, delayed_start)
                if not match.finished:
                    asyncio.create_task(self.start_match(server, match))

    @event(name="addPlayerToMatch")
    async def addPlayerToMatch(self, server: Server, data: dict) -> None:
        asyncio.create_task(self._addPlayerToMatch(server, data))

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] in ['S_EVENT_BIRTH', 'S_EVENT_PLAYER_ENTER_UNIT']:
            # we don't care about AI
            if not data['initiator'].get('name'):
                return
            config = self.get_config(server)
            if config.get('join_on', '').lower() == 'birth':
                new_data = {
                    'player_name': data['initiator']['name'],
                    'match_id': GLOBAL_MATCH_ID
                }
                asyncio.create_task(self._addPlayerToMatch(server, new_data))

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

        async def remove_players(match: Match, server: Server, players: list[Player]):
            for player in players:
                match.player_dead(player)
                if match.match_id != GLOBAL_MATCH_ID:
                    # remove the player from the running match so that they can join another one
                    self.in_match[server.name].pop(player.ucid, None)
                if self.get_config(server).get('kick_on_death', False):
                    kick_time = self.get_config(server).get('kick_time', 30)
                    self.loop.call_later(delay=kick_time,
                                         callback=partial(asyncio.create_task,
                                                          server.kick(player, "You are dead.")))

            config = self.get_config(server)
            survivor = match.survivor()
            # if all sides are dead, the match is finished
            if survivor == Side.UNKNOWN or config.get('win_on', 'survival') == 'survival':
                match.winner = survivor

        def in_match(server: Server, player: Player) -> Match | None:
            match: Match = self.in_match[server.name].get(player.ucid)
            if match and match.started and not match.finished and player not in match.dead.get(player.side, []):
                return match
            return None

        async def award_squadron(server: Server, match: Match, player: Player):
            side = Side.RED if player.side == Side.BLUE else Side.BLUE
            squadron = match.get_squadron(side)
            if not squadron:
                return
            ppk = CreditSystemListener.get_points_per_kill(
                self.get_config(server, plugin_name='creditsystem'),
                {
                    "arg4": data['arg1'],
                    "arg5": player.unit_type
                }
            )
            if not ppk:
                return
            squadron.points += ppk
            squadron.audit('credit_on_leave', ppk, 'Enemy player left the game.')
            if not self.get_config(server).get('silent', False):
                await server.sendPopupMessage(
                    Coalition.BLUE if side == Side.BLUE else Coalition.RED,
                    _("Your squadron was credited with {} points because an enemy player bailed out!").format(ppk),
                    timeout=10)

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
            match = in_match(server, killers[0])
            if match and match == in_match(server, victims[0]):
                match.log.append(
                    (now, _("{killer} in {killer_module} {what} {victim} in {victim_module} with {weapon}").format(
                        killer=print_crew(killers), killer_module=killers[0].unit_display_name,
                        what=_('killed') if data['arg3'] != data['arg4'] else _('team-killed'),
                        victim=print_crew(victims), victim_module=victims[0].unit_display_name,
                        weapon=data['arg7'] or 'Guns')))
                # on team-kills the enemy squadron gets the kill points
                if data['arg3'] == data['arg6']:
                    if self.get_config(server).get('credit_on_leave', False):
                        await award_squadron(server, match, victims[0])
                await remove_players(match, server, victims)
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
                return
            match = in_match(server, players[0])
            if match:
                match.log.append((now, _("{player} in {module} died ({event})").format(
                    player=print_crew(players), module=players[0].unit_display_name, event=_(data['eventName']))))
                await remove_players(match, server, players)
                # on self-kills the enemy squadron gets the kill points
                if self.get_config(server).get('credit_on_leave', False):
                    await award_squadron(server, match, players[0])
        elif data['eventName'] in ['eject', 'disconnect', 'change_slot']:
            player = server.get_player(id=data['arg1'])
            if not player or data.get('arg3', -1) == 0: # ignore slot changes from spectators or before disconnecting
                return
            # if the pilot of an MC aircraft leaves, both pilots get booted
            if player.slot == 0:
                players = server.get_crew_members(server.get_player(id=data['arg1']))
            else:
                players = [player]
            match = in_match(server, player)
            if match:
                match.log.append((now, _("{player} in {module} died ({event})").format(
                    player=print_crew(players), module=players[0].unit_display_name, event=_(data['eventName']))))
                await remove_players(match, server, players)
                # on bail-outs the enemy squadron gets the kill points
                if self.get_config(server).get('credit_on_leave', False):
                    await award_squadron(server, match, player)
            elif self.in_match[server.name].get(player.ucid):
                # in the unlikely event of a player leaving before the match started
                match = self.in_match[server.name][player.ucid]
                try:
                    match.alive[player.side].remove(player)
                except ValueError:
                    pass
                # are all players gone => finish the match
                if match.survivor() == Side.UNKNOWN:
                    match = self.matches[server.name].pop(match.match_id, None)
                    await self.finish_match(server, match)
        elif data['eventName'] == 'takeoff':
            player = server.get_player(id=data['arg1'])
            if not player:
                return
            players = server.get_crew_members(player)
            config = self.get_config(server)
            if config.get('join_on', '') == 'takeoff':
                for player in players:
                    asyncio.create_task(self._addPlayerToMatch(
                        server, {"player_name": player.name, "match_id": GLOBAL_MATCH_ID}))
            if config.get('win_on', '') == 'rtb':
                if not data['arg3']:
                    self.log.error(
                        f"Competitive: Player {player.name} joined in an airstart, but win_on is 'rtb.")
                    return
                for player in players:
                    self.home_base[server.name][player.ucid] = data['arg3']
        elif data['eventName'] == 'landing':
            player = server.get_player(id=data['arg1'])
            if not player:
                return
            match = in_match(server, player)
            if not match:
                return
            config = self.get_config(server)
            win_type = config.get('win_on', 'survival')
            winner = match.survivor()
            if win_type not in ['landing', 'rtb'] or not winner:
                return
            place = data['arg3']
            if win_type == 'rtb':
                proper_place = self.home_base[server.name].get(player.ucid)
                if place != proper_place:
                    asyncio.create_task(player.sendChatMessage(
                        _("You landed at the wrong place! You need to land at {}!").format(proper_place)))
                    return
            match.log.append((now, _("{player} landed at {place} and saved the win for the {side} side!").format(
                player=print_crew(server.get_crew_members(player)), place=place, side=winner.name)))
            match.winner = winner

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        asyncio.create_task(self._onGameEvent(server, data))

    async def finish_match(self, server: Server, match: Match) -> None:
        if not match:
            self.log.error("Match end condition reached without a running match. Check your match setup!")
            return

        match.finished = datetime.now(timezone.utc)

        # unlock players
        if match.match_id == GLOBAL_MATCH_ID:
            for ucid in self.in_match[server.name].keys():
                player = server.get_player(ucid=ucid)
                await player.unlock()
            self.in_match[server.name].clear()
        else:
            for player in match.alive.get(match.survivor(), []):
                await player.unlock()
                self.in_match[server.name].pop(player.ucid, None)

    @event(name="onMatchFinished")
    async def onMatchFinished(self, server: Server, data: dict) -> None:
        match = self.matches[server.name].pop(data['match_id'], None)
        if not match:
            return

        # finish match / unlock players
        await self.finish_match(server, match)

        # restart the mission if configured
        config = self.get_config(server)
        if config.get('end_mission', False):
            if data['winner'] in ['red', 'blue']:
                side = data['winner']
            else:
                side = 'none'  # it's a draw, needs to be different from a server shutdown
            await server.send_to_dcs({
                "command": "endMission",
                "winner": side,
                "time": 60
            })

    async def _print_trueskill(self, player: Player):
        if not self.get_config(player.server).get('silent', False):
            await player.sendChatMessage(_("Your TrueSkill rating is: {}").format(
                self.calculate_rating(await self.get_rating(player))))

    @chat_command(name="skill", help=_("Display your rating"))
    async def skill(self, _server: Server, player: Player, _params: list[str]):
        asyncio.create_task(self._print_trueskill(player))

    @tasks.loop(seconds=5)
    async def check_matches(self):
        for server in self.bot.servers.values():
            if server.status != Status.RUNNING:
                continue
            for match in self.matches.get(server.name, {}).values():
                winner: Side = match.winner
                if match.finished or not winner:
                    continue

                if winner != Side.UNKNOWN:
                    if match.teams[Side.BLUE] and match.teams[Side.RED]:
                        await self.rank_teams(match.teams[winner],
                                              match.teams[Side.BLUE if winner == Side.RED else Side.RED])
                    message = _("The match is over, {} won!\n\n"
                                "The following players are still alive:\n").format(winner.name)
                    for player in match.alive[winner]:
                        message += f"- {player.name}\n"
                else:
                    message = _("The match is over, it is a draw!\n\n")
                message += _("\nThis is the log of your last match:\n")
                for time, log in match.log:
                    message += f"{time:%H:%M:%S}: {log}\n"
                message += _("\nYour new rating is as follows:\n")
                for player in match.teams[Side.BLUE] + match.teams[Side.RED]:
                    message += f"- {player.name}: {self.calculate_rating(await self.get_rating(player))}\n"
                asyncio.create_task(self.inform_players(server, match, message, 60))

                asyncio.create_task(self.bot.bus.send_to_node({
                    "command": "onMatchFinished",
                    "server_name": server.name
                } | match.to_dict()))
