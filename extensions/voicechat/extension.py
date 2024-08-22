from core import Extension
from typing import Optional

__all__ = [
    "VoiceChat"
]


class VoiceChat(Extension):
    async def prepare(self) -> bool:
        settings = self.server.settings['advanced']
        settings['voice_chat_server'] = self.config.get('enabled', True)
        self.server.settings['advanced'] = settings
        return True

    async def render(self, param: Optional[dict] = None) -> dict:
        return {
            "name": "DCS Voice Chat",
            "value": "enabled" if self.config.get('enabled', True) else "disabled"
        }

    def is_installed(self) -> bool:
        return True

    def shutdown(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True
