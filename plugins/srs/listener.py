import asyncio

from core import EventListener, event, Server, Player, get_translation, Side, Coalition, const, utils, chat_command
from plugins.mission.commands import Mission
from typing import cast, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .commands import SRS

_ = get_translation(__name__.split('.')[1])


class SRSEventListener(EventListener["SRS"]):

    def __init__(self, plugin: "SRS"):
        super().__init__(plugin)
        self.mission: Mission = cast(Mission, self.bot.cogs['Mission'])
        self.srs_users: dict[str, dict[str, dict]] = {}

    def _add_or_update_srs_user(self, server: Server, data: dict) -> None:
        if server.name not in self.srs_users:
            self.srs_users[server.name] = {}
        self.srs_users[server.name][data['player_name']] = data

    def _del_srs_user(self, server: Server, data: dict) -> None:
        if server.name not in self.srs_users:
            return
        self.srs_users[server.name].pop(data['player_name'], None)

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, _: dict) -> None:
        config = self.get_config(server) or {
            "message_no_srs": "You need to use SRS to play on this server!"
        }
        asyncio.create_task(server.send_to_dcs({
            'command': 'loadParams',
            'plugin': self.plugin_name,
            'params': config
        }))

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        if self.get_config(server).get('enforce_srs', False):
            player: Player | None = server.get_player(ucid=data['ucid'], active=True)
            if player and player.name not in self.srs_users.get(server.name, {}):
                asyncio.create_task(server.send_to_dcs({"command": "disableSRS", "name": player.name}))

    @event(name="onSRSConnect")
    async def onSRSConnect(self, server: Server, data: dict) -> None:
        if data['player_name'] == '"LotAtc"' or data['unit'] == 'EAM':
            return
        self._add_or_update_srs_user(server, data)
        if self.get_config(server).get('enforce_srs', False):
            asyncio.create_task(server.send_to_dcs({"command": "enableSRS", "name": data['player_name']}))
        self.mission.eventlistener.display_player_embed(server)

    @event(name="onSRSUpdate")
    async def onSRSUpdate(self, server: Server, data: dict) -> None:
        if data['player_name'] == '"LotAtc"' or data['unit'] == 'EAM':
            return
        self._add_or_update_srs_user(server, data)

    @event(name="onSRSDisconnect")
    async def onSRSDisconnect(self, server: Server, data: dict) -> None:
        if data['player_name'] == '"LotAtc"':
            return
        self._del_srs_user(server, data)
        if self.get_config(server).get('enforce_srs', False):
            asyncio.create_task(server.send_to_dcs({"command": "disableSRS", "name": data['player_name']}))
            if self.get_config(server).get('move_to_spec', False):
                player = server.get_player(name=data['player_name'])
                if player and player.side != Side.NEUTRAL:
                    asyncio.create_task(server.move_to_spectators(player, reason=self.get_config(server).get(
                        'message_no_srs', 'You need to use SRS to play on this server!')))
        self.mission.eventlistener.display_player_embed(server)

    async def send_tts_message(self, server: Server, data: dict):
        frequency = data['frequency']
        if frequency > 100000:
            frequency /= 1000000
        config = {
            "frequency": frequency,
            "modulation": 'AM' if frequency > 108.0 else 'FM',
            "coalition": data['coalition']
        }
        if 'lat' in data:
            config['lat'] = data['lat']
            config['lon'] = data['lon']
            config['alt'] = data['alt']
        asyncio.create_task(server.run_on_extension(
            extension='SRS',
            method='play_external_audio',
            config=config,
            text=data['text'])
        )

    @staticmethod
    async def get_airbase(server: Server, place: str):
        airbase = next((
            x for x in server.current_mission.airbases
            if (place.casefold() in x['name'].casefold()) or (place.upper() == x.get('code', x.get('type')))), None)

        if not airbase:
            airbase = await server.send_to_dcs_sync({
                "command": "getAirbase",
                "name": place
            })
        return airbase

    @event(name="onTTSMessage")
    async def onTTSMessage(self, server: Server, data: dict) -> None:
        await self.send_tts_message(server, data)

    async def build_atis_message(self, server: Server, airbase: dict) -> str:
        data = await server.send_to_dcs_sync({
            "command": "getWeatherInfo",
            "x": airbase['position']['x'],
            "y": airbase['position']['y'],
            "z": airbase['position']['z']
        })
        # TODO: check which radio works for which plane
        tower = airbase['frequencyList'][0][0] / 1000000.0 if 'frequencyList' in airbase else None
        weather = data['weather']
        wind = weather['wind']['atGround']
        visibility = weather['visibility']['distance']
        ret = await server.send_to_dcs_sync({
            "command": "getFog"
        })
        if ret['visibility']:
            visibility = int(ret['visibility'])

        if visibility > 30000:
            visibility = "10+ kilometers"
        else:
            visibility = f"{visibility / 10000:.0f} kilometers"

        active_runways = utils.get_active_runways(airbase['runwayList'],wind)
        runways_in_use = ','.join([f"zero {x[1]}" if x.startswith('0') else x for x in active_runways])

        if 'clouds' in data:
            if 'preset' in data['clouds']:
                clouds = data['clouds']['preset']['readableName'][5:].split('\n')[0].replace('/', '/\n').strip()
            else:
                clouds = 'Base: {:,} ft, Thickness: {:,} ft'.format(
                    int(data['clouds']['base'] * const.METER_IN_FEET + 0.5),
                    int(data['clouds']['thickness'] * const.METER_IN_FEET + 0.5))
        else:
            clouds = "Clear sky."

        letter = "Alpha"
        message = (f"This is {airbase['name']} airbase, information {letter}. "
                   f"Winnd {wind['dir']:.0f} degrees, {wind['speed'] * const.METER_PER_SECOND_IN_KNOTS:.0f} knots. "
                   f"Visibility {visibility}. Clouds: {clouds}. Temperature {data['temp']:.1f} degrees celsius. "
                   f"Al-tim-iter {data['qfe']['pressureIN']:.2f}. "
                   f"Runways in use {runways_in_use}.")
        if tower:
            message += f"Contact tower on {tower}."
        message += f"End of information {letter}."
        return message

    def get_player_data(self, server: Server, player: Player) -> dict | None:
        data = next(
            (x for x in self.srs_users[server.name].values() if x['player_name'] == player.name),
            None
        )
        if not data:
            return None

        if data['radios']:
            frequency, modulation = data['radios'][0][0] / 1000000, data['radios'][0][1]
        else:
            return None

        return {
            "frequency": frequency,
            "modulation": modulation,
            "coalition": 1 if player.coalition == Coalition.RED else 2
        }

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        config = self.get_config(server)
        if not config.get('enforce_atc', False):
            return

        if data['eventName'] in ['S_EVENT_BIRTH', 'S_EVENT_TAKEOFF']:
            player = server.get_player(name=data.get('initiator', {}).get('name'))
            if not player or player.sub_slot > 0:
                return

            place = data.get('place', {}).get('name')
            if not place:
                return

            airbase = await self.get_airbase(server, place)
            if not airbase:
                return

            airbase_type = airbase['type']
            default_freqs = (
                config.
                get('atc_frequencies', {}).
                get(player.coalition.value, {}).
                get('*')
            )
            atc_freqs: list[Any] = (
                config.
                get('atc_frequencies', {}).
                get(player.coalition.value, {}).
                get(airbase_type, default_freqs)
            )
            # we accept lists of frequencies
            if isinstance(atc_freqs, str):
                atc_freqs = [atc_freqs]

            if not atc_freqs:
                atc_freqs = airbase['frequencyList']

            for idx, freq in enumerate(atc_freqs):
                if freq.endswith('AM'):
                    atc_freqs[idx] = (int(freq[:-2]) * 1000, 0)
                elif freq.endswith('FM'):
                    atc_freqs[idx] = (int(freq[:-2]) * 1000, 1)
                else:
                    atc_freqs[idx] = (int(freq) * 1000, 0 if int(freq) > 108000 else 1)

            if data['eventName'] == 'S_EVENT_BIRTH':
                await player.sendPopupMessage(_("Please contact ATC on {}").format(
                    ' or '.join([utils.format_frequency(freq) for freq in atc_freqs])))
            elif data['eventName'] == 'S_EVENT_TAKEOFF':
                crew_members = server.get_crew_members(player)
                on_atc_freq = False
                for member in crew_members:
                    data: dict | None = next(
                        (x for x in self.srs_users.get(server.name, {}).values() if x['player_name'] == member.name),
                        None
                    )
                    if not data:
                        continue
                    if any(x in atc_freqs for x in data['radios']):
                        on_atc_freq = True
                        break
                if not on_atc_freq:
                    await self.bus.send_to_node({
                        "command": "rpc",
                        "service": "ServiceBus",
                        "method": "propagate_event",
                        "params": {
                            "command": "punish",
                            "server": server.name if server else None,
                            "data": {
                                "eventName": "no_atc_usage",
                                "initiator": {
                                    "name": player.name
                                }
                            }
                        }
                    })

    @chat_command(name="srs_atis", hidden=True)
    async def srs_atis(self, server: Server, player: Player, data: dict) -> None:

        if not data:
            await player.sendChatMessage("Usage: {} <airbase>".format(self.srs_atis.name))
            return

        place = data[0]
        airbase = await self.get_airbase(server, place)
        if not airbase or 'position' not in airbase:
            await player.sendChatMessage("Airbase {} not found.".format(place))
            return

        player_data = self.get_player_data(server, player)
        if not player_data:
            return

        message = await self.build_atis_message(server, airbase)
        self.log.info(message)
        await self.send_tts_message(server, player_data | {
            "lat": airbase['lat'],
            "lon": airbase['lng'],
            "alt": airbase['alt'],
            "text": message
        })
