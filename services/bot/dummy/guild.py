import os

from services.bot.dummy import DummyRole, DummyMember
from typing import AsyncIterator

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


class DummyGuild:
    def __init__(self):
        with open(os.path.join('config', 'services', 'bot.yaml'), mode='r', encoding='utf-8') as f:
            main = yaml.load(f)
        self._id = main.get('guild_id', 1)
        self._name = main.get('guild_name', 'n/a')
        with open(os.path.join('config', 'services', 'bot.yaml'), mode='r', encoding='utf-8') as f:
            data = yaml.load(f)
        self._roles: dict[int, DummyRole] = {}
        self._members: dict[str, DummyMember] = {}
        for role, members in data.get('roles', {}).items():
            _role = DummyRole(role)
            for member in members:
                _member = self.get_member(member) or DummyMember(member)
                _member._roles[role] = _role
                self._members[member] = _member
                _role._members[member] = _member
            self._roles[role] = _role

    @property
    def id(self) -> int:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def roles(self) -> list[DummyRole]:
        return list(self._roles.values())

    @property
    def members(self) -> list[DummyMember]:
        return list(self._members.values())

    def get_member(self, ucid: str) -> DummyMember | None:
        return self._members.get(ucid)

    async def fetch_member(self, ucid: str) -> DummyMember | None:
        return self.get_member(ucid)

    async def bans(self) -> AsyncIterator[DummyMember | None]:
        # noinspection PyUnreachableCode
        if False:
            yield
