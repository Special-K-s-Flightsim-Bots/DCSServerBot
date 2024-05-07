from dataclasses import dataclass
from enum import Enum, auto

from packaging.version import Version

from core import ServiceRegistry, Service


class MissionType(Enum):
    PvP: auto()
    PvE: auto()
    CoOp: auto()


class TimeEra(Enum):
    MODERN = auto()
    COLD_WAR = auto()
    WW2 = auto()


class AircraftType(Enum):
    FIXED_WING: auto()
    ROTARY_WING: auto()
    MIXED: auto()


class Sides(Enum):
    BLUE = auto()
    RED = auto()
    NEUTRAL = auto()
    MIXED = auto


@dataclass
class MissionVaultItem:
    vault_id: int
    name: str
    terrain: str
    type: MissionType
    aircraft: AircraftType
    era: TimeEra
    sides: Sides


@ServiceRegistry.register(plugin="mission")
class MissionVault(Service):
    def find(self, *, name: str = None, terrain: str = None, type: MissionType = None, aircraft: AircraftType = None,
             era: TimeEra, sides: Sides) -> list[MissionVaultItem]:
        ...

    def detect(self, file: str) -> MissionVaultItem:
        ...
