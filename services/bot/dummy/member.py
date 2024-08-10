from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.bot.dummy import DummyRole


class DummyMember:
    def __init__(self, id: str):
        self._id = id
        self._roles: dict[str, "DummyRole"] = {}

    @property
    def id(self) -> str:
        return self._id

    @property
    def roles(self) -> list["DummyRole"]:
        return list(self._roles.values())

    async def add_roles(self, roles: list["DummyRole"]) -> None:
        for role in roles:
            self._roles[role.id] = role
            role._members[self.id] = self

    async def remove_roles(self, roles: list["DummyRole"]) -> None:
        for role in roles:
            self._roles.pop(role.id, None)
            role._members.pop(self.id, None)
