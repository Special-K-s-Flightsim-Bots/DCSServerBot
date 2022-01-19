# const.py
METER_IN_FEET = 3.28084
METER_PER_SECOND_IN_KNOTS = 1.94384
MMHG_IN_INHG = 0.0393701

STATUS_UNKNOWN = 'Unknown'
STATUS_SHUTDOWN = 'Shutdown'
STATUS_RUNNING = 'Running'
STATUS_PAUSED = 'Paused'
STATUS_STOPPED = 'Stopped'
STATUS_LOADING = 'Loading'

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

PERIOD_DAY = 0
PERIOD_WEEK = 1
PERIOD_MONTH = 2
PERIOD_YEAR = 3

PERIODS = {
    PERIOD_DAY: 'day',
    PERIOD_WEEK: 'week',
    PERIOD_MONTH: 'month',
    PERIOD_YEAR: 'year'
}

STATUS_IMG = {
    'Loading': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe48ed915d1592000048/traffic-light-amber.jpg',
    'Paused': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe48ed915d1592000048/traffic-light-amber.jpg',
    'Running': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe3e40f0b6156700004f/traffic-light-green.jpg',
    'Stopped': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe1940f0b6156700004d/traffic-light-red.jpg',
    'Shutdown': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe1940f0b6156700004d/traffic-light-red.jpg'
}
