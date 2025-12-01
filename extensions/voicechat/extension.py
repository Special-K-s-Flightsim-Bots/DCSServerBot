from core import Extension, Server
from typing_extensions import override

__all__ = [
    "VoiceChat"
]


class VoiceChat(Extension):
    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        if not config.get('name'):
            self._name = 'DCS Voice Chat'

    @override
    async def prepare(self) -> bool:
        settings = self.server.settings['advanced']
        settings['voice_chat_server'] = self.config.get('enabled', True)
        self.server.settings['advanced'] = settings
        return await super().prepare()

    @override
    async def render(self, param: dict | None = None) -> dict:
        return {
            "name": self.name,
            "value": "enabled" if self.config.get('enabled', True) else "disabled"
        }

    @override
    def is_installed(self) -> bool:
        return True

    @override
    async def startup(self, *, quiet: bool = False) -> bool:
        return await super().startup(quiet=True)

    @override
    def shutdown(self, *, quiet: bool = False) -> bool:
        return super().shutdown(quiet=True)
