from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.bot.dummy import DummyMember


class DummyRole:
    def __init__(self, id: str):
        self._id = id
        self._members: dict[str, "DummyMember"] = {}

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._id

    @property
    def members(self) -> list["DummyMember"]:
        return list(self._members.values())

    @property
    def mention(self) -> str:
        return ""
