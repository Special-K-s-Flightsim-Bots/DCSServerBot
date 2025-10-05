import os
import shutil

from core import Extension, DISCORD_FILE_SIZE_LIMIT, ServiceRegistry, Server, get_translation, utils, Autoexec, \
    InstanceImpl
from pathlib import Path
from services.bot import BotService
from services.servicebus import ServiceBus
from typing import cast

_ = get_translation(__name__.split('.')[1])


class Trackfile(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.bus = ServiceRegistry.get(ServiceBus)

    async def startup(self, *, quiet: bool = False) -> bool:
        if self.config.get('enabled', True):
            cfg = Autoexec(cast(InstanceImpl, self.server.instance))
            if cfg.disable_write_track:
                self.log.warning(
                    f"Server {self.server.name} has disable_write_track set and will not write any track file!")
        return await super().startup()

    async def upload_trackfile(self):
        path = Path(self.server.instance.home) /  'Tracks' / 'Multiplayer'
        files = list(path.glob('*.trk'))
        filename = max(files, key=lambda p: p.stat().st_mtime)
        target = self.config['target']
        if target.startswith('<'):
            if os.path.getsize(filename) > DISCORD_FILE_SIZE_LIMIT:
                self.log.warning(f"Can't upload, track file {filename} too large!")
                return
            try:
                await self.bus.send_to_node_sync({
                    "command": "rpc",
                    "service": BotService.__name__,
                    "method": "send_message",
                    "params": {
                        "channel": int(target[4:-1]),
                        "content": _("Track file for server {}").format(self.server.name),
                        "server": self.server.name,
                        "filename": str(filename)
                    }
                })
                self.log.debug(f"Track file {filename} uploaded.")
            except AttributeError:
                self.log.warning(f"Can't upload track file {filename}, "
                                 f"channel {target[4:-1]} incorrect!")
            except Exception as ex:
                self.log.warning(f"Can't upload tack file {filename}: {ex}!")
        else:
            try:
                target_path = os.path.expandvars(utils.format_string(target, server=self.server))
                shutil.copy2(filename, target_path)
                self.log.debug(f"Track file {filename} copied to {target_path}")
            except Exception:
                self.log.warning(f"Can't upload track file {filename} to {target}: ", exc_info=True)

    def shutdown(self, *, quiet: bool = False) -> bool:
        if self.config.get('enabled', True):
            self.loop.create_task(self.upload_trackfile())
        return super().shutdown()
