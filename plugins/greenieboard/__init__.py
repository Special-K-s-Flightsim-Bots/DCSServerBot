import re
from .const import *


def get_element(comment, element):
    if element == 'wire':
        if 'WIRE#' in comment:
            return re.search(r'WIRE# (?P<wire>\d)', comment)['wire']
        else:
            return None
    elif 'WIRE#' in comment:
        comment = re.sub(r'WIRE# (?P<wire>\d)', '', comment)
    return re.search('LSO: GRADE:(?P<grade>[^ ]*) (?P<comment>.*)', comment)[element].lstrip(' :')
