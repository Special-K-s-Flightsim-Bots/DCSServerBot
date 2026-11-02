import os

from services.bot.dummy import DummyRole, DummyMember
from typing import Any, AsyncIterator

from core.utils.yaml_helper import new_yaml


class DummyGuild:
    def __init__(self):
        with open(os.path.join('config', 'services', 'bot.yaml'), mode='r', encoding='utf-8') as f:
            main = new_yaml().load(f)
        self._id = main.get('guild_id', 1)
        self._name = main.get('guild_name', 'n/a')
        with open(os.path.join('config', 'services', 'bot.yaml'), mode='r', encoding='utf-8') as f:
            data = new_yaml().load(f)
        self._roles: dict[int, DummyRole] = {}
        self._members: dict[str | int, DummyMember] = {}
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

    def get_member(self, user_id: str | int) -> DummyMember | None:
        return self._members.get(user_id) or self._members.get(str(user_id))

    async def fetch_member(self, user_id: str | int) -> DummyMember | None:
        return self.get_member(user_id)

    async def bans(self, *, limit: int | None = None, before: Any = None,
                   after: Any = None) -> AsyncIterator[Any]:
        """Yield no bans: this guild stands in for an install without Discord.

        The keyword-only signature is discord.py's, so a caller that pages the ban list finds
        an empty one instead of a TypeError.
        """
        # noinspection PyUnreachableCode
        if False:
            yield
