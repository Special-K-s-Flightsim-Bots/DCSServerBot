import re
from .const import *
from .version import __version__


def get_element(comment: str, element: str) -> str | None:
    match = re.search(r'LSO: GRADE:(?P<grade>[^: ]+)(\s*:)?\s*(?P<details>.*)', comment)

    if element == 'grade':
        return match.group('grade').strip() if match else None

    elif element == 'details':
        details = match.group('details') if match else comment
        details = re.sub(r'WIRE# \d', '', details)
        return details.strip()

    elif element == 'wire' and match and 'WIRE#' in match.group('details'):
        return re.search(r'WIRE# (?P<wire>\d)', match.group('details'))['wire']

    return None
