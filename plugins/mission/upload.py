import asyncio
import discord
import os

from core import UploadStatus, utils, get_translation, Status, Plugin, Server
from core.utils.discord import ServerUploadHandler
from typing import Optional

_ = get_translation(__name__.split('.')[1])


class MissionUploadHandler(ServerUploadHandler):

    def __init__(self, plugin: Plugin, server: Server, message: discord.Message, pattern: list[str]):
        super().__init__(server, message, pattern)
        self.plugin = plugin
        self.log = plugin.log


    async def handle_attachment(self, directory: str, att: discord.Attachment) -> UploadStatus:
        ctx = await self.bot.get_context(self.message)
        rc = await self.server.uploadMission(att.filename, att.url, force=False, missions_dir=directory)
        if rc == UploadStatus.FILE_IN_USE:
            if not await utils.yn_question(ctx, _('A mission is currently active.\n'
                                                       'Do you want me to stop the DCS-server to replace it?')):
                await self.channel.send(_('Upload aborted.'))
                return rc
        elif rc == UploadStatus.FILE_EXISTS:
            self.log.debug("File exists, asking for overwrite.")
            if not await utils.yn_question(ctx, _('File exists. Do you want to overwrite it?')):
                await self.channel.send(_('Upload aborted.'))
                return rc
        if rc != UploadStatus.OK:
            rc = await self.server.uploadMission(att.filename, att.url, force=True, missions_dir=directory)
            if rc != UploadStatus.OK:
                self.log.debug(f"Error while uploading: {rc}")
                await self.channel.send(_('Error while uploading: {}').format(rc.name))
                return rc

        name = utils.escape_string(os.path.basename(att.filename)[:-4])
        if not self.server.locals.get('autoadd', True):
            await self.channel.send(_('Mission "{mission}" uploaded to server {server} and NOT added.').format(
                mission=name, server=self.server.display_name))
            return rc
        if self.server.locals.get('autoscan', False):
            self.log.debug("Autoscan enabled, waiting for mission to be auto-added.")
            await self.channel.send(
                _('Mission "{mission}" uploaded to server {server}.\n'
                  'As you have "autoscan" enabled, it might take some seconds to appear in your mission list.'
                  ).format(mission=name, server=self.server.display_name))
        else:
            await self.channel.send(_('Mission "{mission}" uploaded to server {server}').format(
                mission=name, server=self.server.display_name))
        return rc

    async def _wait_for_mission(self, att: discord.Attachment) -> Optional[str]:
        # wait 60s for the mission to appear
        for i in range(0, 6):
            filename = next((file for file in await self.server.getMissionList()
                             if os.path.basename(file) == os.path.basename(att.filename)), None)
            if filename:
                return filename
            await asyncio.sleep(10)
        return None

    async def _load_mission(self, filename: str):
        ctx = await self.bot.get_context(self.message)
        name = utils.escape_string(os.path.basename(filename)[:-4])
        if (self.server.status != Status.SHUTDOWN and self.server.current_mission and
                self.server.current_mission.filename != filename and
                await utils.yn_question(ctx, _('Do you want to load mission {}?').format(name))):
            extensions = [
                x.name for x in self.server.extensions.values()
                if getattr(x, 'beforeMissionLoad').__module__ != 'core.extension'
            ]
            if len(extensions):
                modify = await utils.yn_question(ctx, _("Do you want to apply extensions before mission start?"))
            else:
                modify = False
            tmp = await self.channel.send(_('Loading mission {} ...').format(name))
            try:
                await self.server.loadMission(filename, modify_mission=modify)
            except (TimeoutError, asyncio.TimeoutError):
                await tmp.delete()
                await self.channel.send(_("Timeout while trying to load the mission."))
                await self.bot.audit(f"Timeout while trying to load mission {name}",
                                     server=self.server)
                return
            await self.bot.audit(f"loaded mission {name}", server=self.server, user=self.message.author)
            await tmp.delete()
            await self.channel.send(_('Mission {} loaded.').format(name))

    async def post_upload(self, uploaded: list[discord.Attachment]):
        # if only one mission was uploaded, ask if it should be loaded and load it
        if len(uploaded) == 1:
            filename = await self._wait_for_mission(uploaded[0])
            if not filename:
                msg = 'Error while uploading: File not found in severSettings.lua!'
                self.log.error(msg)
                await self.channel.send(_(msg))
                return
            await self._load_mission(filename)