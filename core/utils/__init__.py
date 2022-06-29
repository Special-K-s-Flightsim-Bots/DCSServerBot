from configparser import ConfigParser


def reload() -> ConfigParser:
    cfg = ConfigParser()
    cfg.read('config/default.ini')
    cfg.read('config/dcsserverbot.ini')
    return cfg


config = reload()

from .coalitions import *
from .dcs import *
from .discord import *
from .helper import *
from .os import *
