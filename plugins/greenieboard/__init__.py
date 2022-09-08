import re
from typing import Optional
from .const import *
from .version import __version__


def get_element(comment: str, element: str) -> Optional[str]:
    if element == 'wire':
        if 'WIRE#' in comment:
            return re.search(r'WIRE# (?P<wire>\d)', comment)['wire']
        else:
            return None
    elif 'WIRE#' in comment:
        comment = re.sub(r'WIRE# (?P<wire>\d)', '', comment)
    return re.search('LSO: GRADE:(?P<grade>[^ ]*) (?P<details>.*)', comment)[element].lstrip(' :')
