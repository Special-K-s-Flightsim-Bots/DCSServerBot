# const.py
from enum import Enum

METER_IN_FEET = 3.28084
METER_PER_SECOND_IN_KNOTS = 1.94384
MMHG_IN_INHG = 0.0393701


class Status(Enum):
    UNKNOWN = 'Unknown'
    SHUTDOWN = 'Shutdown'
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
#    Status.LOADING: 'https://assets.digital.cabinet-office.gov.uk/media/559fbe48ed915d1592000048/traffic-light-amber.jpg',
#    Status.PAUSED: 'https://assets.digital.cabinet-office.gov.uk/media/559fbe48ed915d1592000048/traffic-light-amber.jpg',
#    Status.RUNNING: 'https://assets.digital.cabinet-office.gov.uk/media/559fbe3e40f0b6156700004f/traffic-light-green.jpg',
#    Status.STOPPED: 'https://assets.digital.cabinet-office.gov.uk/media/559fbe1940f0b6156700004d/traffic-light-red.jpg',
#    Status.SHUTDOWN: 'https://assets.digital.cabinet-office.gov.uk/media/559fbe1940f0b6156700004d/traffic-light-red.jpg'
    Status.LOADING: 'http://icongal.com/gallery/image/99380/forward_button.png',
    Status.PAUSED: 'http://icongal.com/gallery/image/99408/stepforwardnormalblue.png',
    Status.RUNNING: 'http://icongal.com/gallery/image/99436/stepforwardnormal.png',
    Status.STOPPED: 'http://icongal.com/gallery/image/99345/stop_red.png',
    Status.SHUTDOWN: 'http://icongal.com/gallery/image/99331/grey_stop_disabled.png'
}
