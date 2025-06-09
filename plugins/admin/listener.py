import asyncio

from core import EventListener, event, Server
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Admin


class AdminEventListener(EventListener["Admin"]):

    async def enable(self, server: Server, extension: str, config: dict) -> bool:
        await server.config_extension(extension, config)
        # do we need to initialize the extension?
        try:
            await server.run_on_extension(extension=extension, method='enable')
        except ValueError:
            await server.init_extensions()
            try:
                await server.run_on_extension(extension=extension, method='prepare')
            except ValueError as ex:
                self.log.error(f"Extension {extension} could not be enabled: {ex}")
                return False
        is_running = await server.run_on_extension(extension=extension, method='is_running')
        if not is_running:
            await server.run_on_extension(extension=extension, method='startup')
        return True

    @event(name="enableExtension")
    async def enableExtension(self, server: Server, data: dict) -> None:
        extension = data['extension']
        config = data.get('config', {})
        config['enabled'] = True
        asyncio.create_task(self.enable(server, extension, config))

    async def disable(self, server: Server, extension: str) -> bool:
        try:
            is_running = await server.run_on_extension(extension=extension, method='is_running')
            if not is_running:
                return True
            await server.config_extension(extension, {"enabled": False})
            await server.run_on_extension(extension=extension, method='disable')
            return True
        except ValueError as ex:
            self.log.error(f"Extension {extension} could not be disabled: {ex}")
            return False

    @event(name="disableExtension")
    async def disableExtension(self, server: Server, data: dict) -> None:
        extension = data['extension']
        asyncio.create_task(self.disable(server, extension))
