from configparser import ConfigParser


def reload() -> ConfigParser:
    cfg = ConfigParser()
    cfg.read('config/default.ini', encoding='utf-8')
    cfg.read('config/dcsserverbot.ini', encoding='utf-8')
    return cfg


config = reload()

from .campaigns import *
from .coalitions import *
from .dcs import *
from .discord import *
from .helper import *
from .os import *
