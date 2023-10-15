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
    "WEEKDAYS",
    "MONTH",
    "SAVED_GAMES",
    "DEFAULT_TAG"
]

METER_IN_FEET = 3.28084
METER_PER_SECOND_IN_KNOTS = 1.94384
MMHG_IN_INHG = 0.0393701
MMHG_IN_HPA = 1.333224
QFE_TO_QNH_INHG = 0.00107777777777778
QFE_TO_QNH_MB = 0.03662667


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

SAVED_GAMES = os.path.expandvars(os.path.join('%USERPROFILE%', 'Saved Games'))
if not os.path.exists(SAVED_GAMES) and sys.platform == 'win32':
    SAVED_GAMES = winreg.QueryValueEx(
        winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                       r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders", 0),
        '{4C5C32FF-BB9D-43b0-B5B4-2D72E54EAAA4}'
    )[0]

DEFAULT_TAG = 'DEFAULT'
