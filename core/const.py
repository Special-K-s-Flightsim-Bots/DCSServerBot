# const.py
from enum import Enum

METER_IN_FEET = 3.28084
METER_PER_SECOND_IN_KNOTS = 1.94384
MMHG_IN_INHG = 0.0393701


class Status(Enum):
    UNKNOWN = 'Unknown'
    SHUTDOWN = 'Shutdown'
    SHUTDOWN_PENDING = 'Shutdown Pending'
    RUNNING = 'Running'
    PAUSED = 'Paused'
    STOPPED = 'Stopped'
    LOADING = 'Loading'


SIDE_UNKNOWN = -1
SIDE_SPECTATOR = 0
SIDE_RED = 1
SIDE_BLUE = 2
SIDE_NEUTRAL = 3

PLAYER_SIDES = {
    SIDE_UNKNOWN: 'UNKNOWN',
    SIDE_SPECTATOR: 'SPECTATOR',
    SIDE_RED: 'RED',
    SIDE_BLUE: 'BLUE',
    SIDE_NEUTRAL: 'NEUTRAL'
}

WEEKDAYS = {
    0: 'Mon',
    1: 'Tue',
    2: 'Wed',
    3: 'Thu',
    4: 'Fri',
    5: 'Sat',
    6: 'Sun'
}


STATUS_IMG = {
    Status.LOADING: 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/loading_256.png?raw=true',
    Status.PAUSED: 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/pause_256.png?raw=true',
    Status.RUNNING: 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/play_256.png?raw=true',
    Status.STOPPED: 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/stop_256.png?raw=true',
    Status.SHUTDOWN: 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/stop_256.png?raw=true',
    Status.SHUTDOWN_PENDING: 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/stop_256.png?raw=true'
}
