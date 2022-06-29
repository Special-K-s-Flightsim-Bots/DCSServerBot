import discord
from abc import ABC
from dataclasses import dataclass, field


@dataclass
class Slot(ABC):
    category: str = None
    unit_type: str = None
    group_name: str = None
    message: str = None

    def match(self, **kwargs) -> bool:
        if 'category' in kwargs and self.category != kwargs['category']:
            return False
        if 'unit_type' in kwargs and self.unit_type != kwargs['unit_type']:
            return False
        if 'group_name' in kwargs and self.group_name != kwargs['group_name']:
            return False
        return True


@dataclass
class RoleBaseSlot(Slot):
    roles: list[discord.Role] = field(default_factory=list)

    def match(self, **kwargs) -> bool:
        return super().match(**kwargs)


@dataclass
class CreditBasedSlot(Slot):
    points: int = 0
    costs: int = 0

    def match(self, **kwargs) -> bool:
        return super().match(**kwargs)
