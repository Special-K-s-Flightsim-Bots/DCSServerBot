import os
import time
import win32api
from core import Extension, report
from datetime import datetime, timedelta
from typing import Optional


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
        default_dir = r"%USERPROFILE%\Documents\Tacview"

        # check if autodelete is configured
        if 'delete_after' not in config:
            return
        # only run once a day
        if lastrun and lastrun > (datetime.now() - timedelta(days=1)):
            return
        now = time.time()
        path = os.path.expandvars(config['path']) if 'path' in config else os.path.expandvars(default_dir)
        for f in [os.path.join(path, x) for x in os.listdir(path)]:
            if os.stat(f).st_mtime < (now - config['delete_after'] * 86400):
                if os.path.isfile(f):
                    os.remove(f)

    def verify(self) -> bool:
        return os.path.exists(os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME']) +
                              r'\Mods\tech\Tacview\bin\tacview.dll')
