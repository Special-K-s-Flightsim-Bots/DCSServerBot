from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.bot.dummy import DummyRole


class DummyMember:
    def __init__(self, id: str, *, name: str = ""):
        self._id = id
        self._name = name
        self._roles: dict[str, "DummyRole"] = {}

    @property
    def id(self) -> str:
        return self._id

    @property
    def roles(self) -> list["DummyRole"]:
        return list(self._roles.values())

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name

    async def add_roles(self, roles: list["DummyRole"]) -> None:
        for role in roles:
            self._roles[role.id] = role
            role._members[self.id] = self

    async def remove_roles(self, roles: list["DummyRole"]) -> None:
        for role in roles:
            self._roles.pop(role.id, None)
            role._members.pop(self.id, None)
