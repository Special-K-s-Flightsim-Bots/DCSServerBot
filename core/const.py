import os
import sys
if sys.platform == 'win32':
    import winreg

__all__ = [
    "METER_IN_FEET",
    "METER_PER_SECOND_IN_KNOTS",
    "MMHG_IN_INHG",
    "MMHG_IN_HPA",
    "QFE_TO_QNH_INHG",
    "QFE_TO_QNH_MB",
    "MAX_SAFE_INTEGER",
    "WEEKDAYS",
    "MONTH",
    "TRAFFIC_LIGHTS",
    "SAVED_GAMES",
    "DEFAULT_TAG",
    "SEND_ONLY_CHANNEL_PERMISSIONS",
    "DEFAULT_CHANNEL_PERMISSIONS",
    "FULL_MANAGE_CHANNEL_PERMISSIONS",
]

METER_IN_FEET = 3.28084
METER_PER_SECOND_IN_KNOTS = 1.94384
METERS_IN_SM = 1609.344
MMHG_IN_INHG = 0.0393701
MMHG_IN_HPA = 1.333224
QFE_TO_QNH_INHG = 0.00107777777777778
QFE_TO_QNH_MB = 0.03662667
MAX_SAFE_INTEGER = 9007199254740991 # Lua 5.1 max integer representation, 2^253 - 1

WEEKDAYS = {
    0: 'Mon',
    1: 'Tue',
    2: 'Wed',
    3: 'Thu',
    4: 'Fri',
    5: 'Sat',
    6: 'Sun'
}

MONTH = {
    1: 'January',
    2: 'February',
    3: 'March',
    4: 'April',
    5: 'May',
    6: 'June',
    7: 'July',
    8: 'August',
    9: 'September',
    10: 'October',
    11: 'November',
    12: 'December'
}

TRAFFIC_LIGHTS = {
    "red": "https://assets.digital.cabinet-office.gov.uk/media/559fbe1940f0b6156700004d/traffic-light-red.jpg",
    "amber": "https://assets.digital.cabinet-office.gov.uk/media/559fbe48ed915d1592000048/traffic-light-amber.jpg",
    "green": "https://assets.digital.cabinet-office.gov.uk/media/559fbe3e40f0b6156700004f/traffic-light-green.jpg"
}

SAVED_GAMES = os.path.expandvars(os.path.join('%USERPROFILE%', 'Saved Games'))
if not os.path.exists(SAVED_GAMES) and sys.platform == 'win32':
    SAVED_GAMES = winreg.QueryValueEx(
        winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                       r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders", 0),
        '{4C5C32FF-BB9D-43b0-B5B4-2D72E54EAAA4}'
    )[0]

DEFAULT_TAG = 'DEFAULT'

SEND_ONLY_CHANNEL_PERMISSIONS = {
    "view_channel",
    "send_messages",
    "read_messages",
    "read_message_history",
    "add_reactions",
}

DEFAULT_CHANNEL_PERMISSIONS = SEND_ONLY_CHANNEL_PERMISSIONS | {
    "attach_files",
    "embed_links",
    "manage_messages",
}

FULL_MANAGE_CHANNEL_PERMISSIONS = DEFAULT_CHANNEL_PERMISSIONS | {
    "manage_channel",
}
