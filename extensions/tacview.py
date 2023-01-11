import os
import string
import time
import discord
import win32api
from core import Extension, report, Server
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


DEFAULT_DIR = r"%USERPROFILE%\Documents\Tacview"


class Tacview(Extension):
    def _load_config(self) -> dict:
        if 'Tacview' in self.server.options['plugins']:
            # check config for errors
            tacview = self.server.options['plugins']['Tacview']
            if 'tacviewPlaybackDelay' not in tacview or tacview['tacviewPlaybackDelay'] == 0:
                self.log.warning(f'  => {self.server.name}: tacviewPlaybackDelay is not set, you might see performance issues!')
            return tacview
        else:
            return dict()

    def load_config(self) -> Optional[dict]:
        if self.server.options:
            return self._load_config()
        else:
            return None

    @property
    def version(self) -> str:
        installation = self.server.installation
        path = os.path.expandvars(self.bot.config[installation]['DCS_HOME']) + r'\Mods\tech\Tacview\bin\tacview.dll'
        if os.path.exists(path):
            info = win32api.GetFileVersionInfo(path, '\\')
            version = "%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                    info['FileVersionMS'] % 65536,
                                    info['FileVersionLS'] / 65536)
        else:
            version = 'n/a'
        return version

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        if not self.locals:
            self.locals = self._load_config()
        name = 'Tacview'
        if ('tacviewModuleEnabled' in self.locals and not self.locals['tacviewModuleEnabled']) or \
                ('tacviewFlightDataRecordingEnabled' in self.locals and
                 not self.locals['tacviewFlightDataRecordingEnabled']):
            value = 'disabled'
        else:
            show_passwords = self.config['show_passwords'] if 'show_passwords' in self.config else True
            value = ''
            if 'tacviewRealTimeTelemetryPort' in self.locals and len(self.locals['tacviewRealTimeTelemetryPort']) > 0:
                value += f"{self.bot.external_ip}:{self.locals['tacviewRealTimeTelemetryPort']}\n"
            if 'tacviewRealTimeTelemetryEnabled' in self.locals and self.locals['tacviewRealTimeTelemetryEnabled']:
                name += ' RT'
                if show_passwords and 'tacviewRealTimeTelemetryPassword' in self.locals and \
                        len(self.locals['tacviewRealTimeTelemetryPassword']) > 0:
                    value += f"Password: {self.locals['tacviewRealTimeTelemetryPassword']}\n"
            elif show_passwords and 'tacviewHostTelemetryPassword' in self.locals \
                    and len(self.locals['tacviewHostTelemetryPassword']) > 0:
                value += f"Password: {self.locals['tacviewHostTelemetryPassword']}\n"
            if 'tacviewRemoteControlEnabled' in self.locals and self.locals['tacviewRemoteControlEnabled']:
                value += f"**Remote Ctrl [{self.locals['tacviewRemoteControlPort']}]**\n"
                if show_passwords and 'tacviewRemoteControlPassword' in self.locals and \
                        len(self.locals['tacviewRemoteControlPassword']) > 0:
                    value += f"Password: {self.locals['tacviewRemoteControlPassword']}"
            if len(value) == 0:
                value = 'enabled'
        embed.add_field(name=name, value=value)

    @staticmethod
    def schedule(config: dict, lastrun: Optional[datetime] = None):
        # check if autodelete is configured
        if 'delete_after' not in config:
            return
        # only run once a day
        if lastrun and lastrun > (datetime.now() - timedelta(days=1)):
            return
        now = time.time()
        path = os.path.expandvars(config['path']) if 'path' in config else os.path.expandvars(DEFAULT_DIR)
        for f in [os.path.join(path, x) for x in os.listdir(path)]:
            if os.stat(f).st_mtime < (now - config['delete_after'] * 86400):
                if os.path.isfile(f):
                    os.remove(f)

    def verify(self) -> bool:
        return os.path.exists(os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME']) +
                              r'\Mods\tech\Tacview\bin\tacview.dll')

    async def onMissionEnd(self, data: dict):
        path = Path(os.path.expandvars(self.config['path']) if 'path' in self.config else os.path.expandvars(DEFAULT_DIR))
        server: Server = self.bot.servers[data['server_name']]
        whitelist = "-_.() %s%s" % (string.ascii_letters, string.digits)
        filename = 'Tacview-*-DCS-'
        filename += ''.join(c for c in server.current_mission.name if c in whitelist)
        # wait 2 mins max for the mission to shut down
        now = round(time.mktime(datetime.today().timetuple()))
        file = None
        for s in range(1, 120):
            for file in sorted(path.glob(filename + '.txt.acmi'), key=os.path.getmtime, reverse=True):
                if file.exists() and (now - round(file.stat().st_mtime)) < 120:
                    break
            else:
                break
        if file:
            filename = file.name.replace('.txt.', '.zip.')
        else:
            filename += '.zip.acmi'
        # now search the correct file
        for s in range(1, 120):
            for file in sorted(path.glob(filename), key=os.path.getmtime, reverse=True):
                if (now - round(file.stat().st_mtime)) < 60:
                    channel = self.bot.get_channel(self.config['channel'])
                    await channel.send(file=discord.File(file.__str__()))
                    return
        return
