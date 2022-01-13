# const.py
METER_IN_FEET = 3.28084
METER_PER_SECOND_IN_KNOTS = 1.94384
MMHG_IN_INHG = 0.0393701

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

STATUS_IMG = {
    'Loading': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe48ed915d1592000048/traffic-light-amber.jpg',
    'Paused': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe48ed915d1592000048/traffic-light-amber.jpg',
    'Running': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe3e40f0b6156700004f/traffic-light-green.jpg',
    'Stopped': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe1940f0b6156700004d/traffic-light-red.jpg',
    'Shutdown': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe1940f0b6156700004d/traffic-light-red.jpg'
}
