import os
import re
import shutil

from configparser import RawConfigParser
from core import Server, utils, InstallableExtension, Autoexec
from extensions.srs import SRS
from typing import Any, cast, TextIO
from typing_extensions import override


class HoundTTS(InstallableExtension):

    CONFIG_DICT = {
        "autoupdate": {
            "type": bool,
            "label": "Autoupdate",
            "default": True,
            "required": False
        },
        "DEFAULT_PROVIDER": {
            "type": list,
            "label": "Default Provider",
            "options": [
                "sapi",
                "piper",
                "azure",
                "google",
                "elevenlabs",
                "aws",
                "polly",
                "supertonic"
            ],
            "min_values": 0,
            "max_values": 1,
            "default": "sapi",
            "required": False
        },
        "DEFAULT_VOICE": {
            "type": str,
            "label": "Default Voice",
            "required": False
        },
        "DEFAULT_CULTURE": {
            "type": str,
            "label": "Default Culture",
            "default": "en-US",
            "required": True
        },
        "DEFAULT_GENDER": {
            "type": list,
            "label": "Default Gender",
            "options": [
                "male",
                "female"
            ],
            "default": "female",
            "min_values": 1,
            "max_values": 1,
            "required": True
        }
    }

    def __init__(self, server: Server, config: dict):
        self.ini = RawConfigParser()
        self.ini.optionxform = str
        super().__init__(server, config, repo="https://github.com/uriba107/HoundTTS", package_name="HoundTTS-windows")
        self.home = os.path.join(self.server.instance.home, 'Mods', 'Services', 'HoundTTS')

    def get_config_path(self) -> str:
        return os.path.join(self.server.instance.home, 'Config', 'HoundTTS.lua')

    def get_ini_path(self) -> str:
        return os.path.join(self.server.instance.home, 'Config', 'HoundTTS-credentials.ini')

    @staticmethod
    def parse(value: str) -> Any:
        if value.startswith('{'):
            return value[1:-1].split(',')
        elif value.startswith('"'):
            return value.strip('"')
        elif value.startswith("'"):
            return value.strip("'")
        elif value == 'true':
            return True
        elif value == 'false':
            return False
        elif '.' in value:
            return float(value)
        else:
            return int(value)

    @staticmethod
    def unparse(value: Any) -> str:
        if isinstance(value, bool):
            return value.__repr__().lower()
        elif isinstance(value, str):
            return '"' + value + '"'
        else:
            return value

    @override
    def load_config(self) -> dict:
        def read_file(file: TextIO, cfg: dict):
            for line in file.readlines():
                match = exp.match(line)
                if match:
                    key = match.group('key').strip()
                    if key.startswith('--'):
                        continue
                    value = match.group('value').strip(' ,')
                    cfg[key] = self.parse(value)

        # read ini file
        ini_file = self.get_ini_path()
        if not os.path.exists(ini_file):
            return {}
        self.ini.read(ini_file, encoding='utf-8')

        # read lua file
        exp = re.compile(r'(?P<key>.*) = (?P<value>.*)')
        path = self.get_config_path()
        cfg = dict()
        if os.path.exists(path):
            with open(path, mode='r', encoding='utf-8') as file:
                read_file(file, cfg)
        return cfg

    @override
    def is_installed(self) -> bool:
        return os.path.exists(self.home)

    @override
    @property
    def version(self) -> str:
        version = utils.get_windows_version(os.path.join(self.home, r'bin', 'HoundTTS.dll'))
        if version:
            elements = version.split('.')
            if len(elements) > 3:
                elements = elements[0:3]
            version = '.'.join(elements)
        return version or "0.1.1"

    async def uninstall(self) -> bool:
        if not self.service or not await super().uninstall():
            try:
                utils.safe_rmtree(self.home)
            except Exception as ex:
                self.log.error(f"Error during uninstall of {self.name}: {str(ex)}")
                return False
        config_file = self.get_config_path()
        if os.path.exists(config_file):
            os.remove(config_file)
        ini_file = self.get_ini_path()
        if os.path.exists(ini_file):
            os.remove(ini_file)
        return True

    def _maybe_update_config(self, section: str, key: str, value: Any):
        if not value:
            return False
        if section not in self.ini:
            self.ini[section] = {}
        if not self.ini[section].get(key) or Autoexec.parse(self.ini[section][key]) != value:
            self.ini.set(section, key, value)
            self.log.info(f"  => {self.server.name}: [{section}][{key}] set to {value}")
            return True
        return False

    @override
    async def prepare(self) -> bool:
        ini = self.get_ini_path()
        if not os.path.exists(ini):
            shutil.copy2(ini + '.example', ini)
        config = self.get_config_path()
        if not os.path.exists(config):
            shutil.copy2(config + '.example', config)
            self.locals = self.load_config()

        # write config file
        dirty = False
        if not self.locals:
            self.locals = self.load_config()
        extension = cast(SRS, self.server.extensions.get('SRS'))
        if extension:
            srs_host = extension.config.get('host', '127.0.0.1')
            srs_port = extension.config.get('port', extension.locals['Server Settings']['SERVER_PORT'])
            if self.locals.get("SRS_HOST") != srs_host:
                self.locals["SRS_HOST"] = srs_host
                dirty = True
            if self.locals.get("SRS_PORT") != srs_port:
                self.locals["SRS_PORT"] = srs_port
                dirty = True
            new_locals = self.locals.copy() | self.config.copy()
            new_locals.pop('enabled', None)
            new_locals.pop('autoupdate', None)
            new_locals.pop('debug', None)
            if new_locals != self.locals:
                self.locals = new_locals
                dirty = True
        if dirty:
            with open(self.get_config_path() + '.example', 'r', encoding='utf-8') as infile:
                with open(self.get_config_path(), mode='w', encoding='utf-8') as outfile:
                    exp = re.compile(r'(?P<key>.*) = (?P<value>.*)')
                    for line in infile:
                        match = exp.match(line)
                        if match:
                            key = match.group('key').strip()
                            if key.startswith('--'):
                                outfile.write(f"{line}")
                            else:
                                outfile.write(f"{key} = {self.unparse(self.locals.get(key, ''))}\n")
                        else:
                            outfile.write(f"{line}")
            self.locals = self.load_config()

        # write ini file
        dirty = False
        dirty |= self._maybe_update_config('General', 'log_level', 'debug' if self.config.get('debug', False) else 'error')
        for name, provider in self.config.get('providers', {}):
            if name == 'Piper':
                dirty |= self._maybe_update_config('Piper', 'path', provider.get('path'))
                dirty |= self._maybe_update_config('Piper', 'voice_path', provider.get('voice_path'))
                dirty |= self._maybe_update_config('Piper', 'threads', provider.get('threads'))
                dirty |= self._maybe_update_config('Piper', 'max_concurrent', provider.get('max_concurrent'))
            elif name == 'Google':
                dirty |= self._maybe_update_config('Google', 'credentials_file', provider.get('credentials_file'))
            elif name == 'Azure':
                dirty |= self._maybe_update_config('Azure', 'key', provider.get('key'))
                dirty |= self._maybe_update_config('Azure', 'region', provider.get('region'))
            elif name == 'ElevenLabs':
                dirty |= self._maybe_update_config('ElevenLabs', 'api_key', provider.get('api_key'))
                dirty |= self._maybe_update_config('ElevenLabs', 'model_id', provider.get('model_id', 'eleven_turbo_v2'))
            elif name == 'AWS':
                dirty |= self._maybe_update_config('AWS', 'access_key', provider.get('access_key'))
                dirty |= self._maybe_update_config('AWS', 'secret_key', provider.get('secret_key'))
                dirty |= self._maybe_update_config('AWS', 'region', provider.get('region'))
                dirty |= self._maybe_update_config('AWS', 'engine', provider.get('engine', 'standard'))
            elif name == 'OpenAI':
                dirty |= self._maybe_update_config('OpenAI', 'api_key', provider.get('api_key'))
                dirty |= self._maybe_update_config('OpenAI', 'endpoint', provider.get('endpoint', 'https://api.openai.com/'))
                dirty |= self._maybe_update_config('OpenAI', 'model', provider.get('model', 'tts-1'))
                dirty |= self._maybe_update_config('OpenAI', 'chat_model', provider.get('chat_model', 'gpt-4o-mini'))
            elif name == 'LibreTranslate':
                dirty |= self._maybe_update_config('LibreTranslate', 'api_key', provider.get('api_key'))
                dirty |= self._maybe_update_config('LibreTranslate', 'endpoint', provider.get('endpoint', 'http://localhost:5000'))
            elif name == 'Discord':
                # TODO: this could be us!
                dirty |= self._maybe_update_config('Discord', 'bot_token', provider.get('bot_token'))
            elif name == 'Supertonic':
                dirty |= self._maybe_update_config('Supertonic', 'path', provider.get('path'))
                dirty |= self._maybe_update_config('Supertonic', 'model_path', provider.get('model_path'))
                dirty |= self._maybe_update_config('Supertonic', 'voice_style', provider.get('voice_style'))
                dirty |= self._maybe_update_config('Supertonic', 'voice_style_path', provider.get('voice_style_path'))
                dirty |= self._maybe_update_config('Supertonic', 'total_steps', provider.get('total_steps'))
                dirty |= self._maybe_update_config('Supertonic', 'threads', provider.get('threads'))
                dirty |= self._maybe_update_config('Supertonic', 'max_concurrent', provider.get('max_concurrent'))

        if dirty:
            with open(self.get_ini_path(), mode='w', encoding='utf-8') as ini:
                self.ini.write(ini)
            self.locals = self.load_config()

        return True
