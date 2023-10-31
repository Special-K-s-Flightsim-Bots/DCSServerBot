from typing import Optional

from core import Extension, report


class VoiceChat(Extension):
    async def prepare(self) -> bool:
        settings = self.server.settings['advanced']
        settings['voice_chat_server'] = self.config.get('enabled', True)
        self.server.settings['advanced'] = settings
        return True

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        embed.add_field(name='DCS Voice Chat', value='enabled')

    def is_installed(self) -> bool:
        return self.config.get('enabled', True)

    async def shutdown(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True
