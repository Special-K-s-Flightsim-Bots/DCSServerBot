import aiohttp
import certifi
import io
import os
import shutil
import ssl
import zipfile

from core import InstallableExtension, get_translation
from packaging.version import InvalidVersion, parse
from typing_extensions import override

_ = get_translation(__name__.split('.')[1])

__all__ = [
    "DKS"
]

DKS_URL = "https://www.digitalkneeboardsimulator.com"
DKS_EXPORTER_PATH = "/api/telemetry/exporter"


def lua_string(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n') + '"'


class DKS(InstallableExtension):
    """
    Installs the DKS exporter, which records the server for Digital Kneeboard Simulator and replaces Tacview:
    it serves Tacview real-time telemetry and writes Tacview files itself. The exporter updates itself
    (signed releases), so this extension only installs it once and keeps its key file in sync.
    """

    CONFIG_DICT = {
        "keyId": {
            "type": str,
            "label": _("Key ID"),
            "placeholder": _("From your server's page in DKS"),
            "required": True
        },
        "secret": {
            "type": str,
            "label": _("Secret"),
            "placeholder": _("From your server's page in DKS"),
            "required": True
        },
        "ownerId": {
            "type": str,
            "label": _("Server ID"),
            "placeholder": _("From your server's page in DKS"),
            "required": True
        }
    }

    @property
    def dks_url(self) -> str:
        return self.config.get('dksUrl', DKS_URL).rstrip('/')

    @property
    def hook(self) -> str:
        return os.path.join(self.server.instance.home, 'Scripts', 'Hooks', 'DKS-Telemetry.lua')

    @property
    def mod_dir(self) -> str:
        return os.path.join(self.server.instance.home, 'Mods', 'tech', 'DKSTelemetry')

    @property
    def cfg(self) -> str:
        return os.path.join(self.server.instance.home, 'Config', 'DKS-Telemetry.cfg')

    @override
    @property
    def version(self) -> str | None:
        versions_dir = os.path.join(self.mod_dir, 'versions')
        if not os.path.isdir(versions_dir):
            return None
        versions = []
        for name in os.listdir(versions_dir):
            if not os.path.isdir(os.path.join(versions_dir, name)):
                continue
            try:
                versions.append(parse(name))
            except InvalidVersion:
                pass
        return str(max(versions)) if versions else None

    def tacview_enabled(self) -> bool:
        tacview = self.server.extensions.get('Tacview')
        return tacview is not None and tacview.enabled

    @override
    def is_installed(self) -> bool:
        return os.path.exists(self.hook) and os.path.isdir(self.mod_dir)

    @override
    async def install(self, version: str | None = None) -> bool:
        if self.tacview_enabled():
            self.log.error(f"  => {self.name}: DKS replaces Tacview. Disable the Tacview extension to use it.")
            return False
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get(self.dks_url + DKS_EXPORTER_PATH, raise_for_status=True,
                                       proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                    data = await response.read()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                zf.extractall(self.server.instance.home)
        except Exception as ex:
            self.log.error(f"  => {self.name}: Couldn't download the DKS exporter: {ex}")
            return False
        self.log.info(f"  => {self.name} {self.version} installed into instance {self.server.instance.name}.")
        return True

    @override
    async def uninstall(self) -> bool:
        # Recordings not yet uploaded stay in Saved Games\...\DKS\telemetry.
        for file in [self.hook, self.cfg]:
            if os.path.exists(file):
                os.remove(file)
        if os.path.isdir(self.mod_dir):
            shutil.rmtree(self.mod_dir, ignore_errors=True)
        return True

    @override
    async def update_available(self) -> str | None:
        # The exporter updates itself.
        return None

    @override
    async def prepare(self) -> bool:
        if self.tacview_enabled():
            self.log.error(f"  => {self.name}: DKS replaces Tacview. Disable the Tacview extension to use it.")
            return False
        missing = [x for x in ['keyId', 'secret', 'ownerId'] if not self.config.get(x)]
        if missing:
            self.log.error(f"  => {self.name}: {', '.join(missing)} missing. "
                           f"Copy the DKS block from your server's page in DKS.")
            return False
        self.remove_weather_exporter()
        self.turn_off_tacview()
        self.write_cfg()
        return await super().prepare()

    def write_cfg(self):
        lines = [
            "-- Written by DCSServerBot from nodes.yaml (extension DKS). Keep it private.",
            "DKS_TELEMETRY = {",
            f"  dksUrl = {lua_string(self.dks_url)},",
            f"  keyId = {lua_string(self.config['keyId'])},",
            f"  secret = {lua_string(self.config['secret'])},",
            f"  ownerId = {lua_string(self.config['ownerId'])},",
            f"  serverName = {lua_string(self.server.name)},",
            "}",
            ""
        ]
        os.makedirs(os.path.dirname(self.cfg), exist_ok=True)
        with open(self.cfg, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

    def turn_off_tacview(self):
        # A Tacview installed outside DCSServerBot keeps recording and holds the real-time port otherwise.
        if not os.path.exists(os.path.join(self.server.instance.home, 'Scripts', 'Hooks', 'TacviewGameGUI.lua')):
            return
        options = self.server.options['plugins'] or {}
        if options.get('Tacview', {}).get('tacviewModuleEnabled', True):
            options.setdefault('Tacview', {})['tacviewModuleEnabled'] = False
            self.server.options['plugins'] = options
            self.log.info(f"  => {self.server.name}: Tacview turned off, DKS replaces it.")

    def remove_weather_exporter(self):
        # The DKS weather exporter of earlier versions of this extension wrapped tacview.dll.
        tacview_bin = os.path.join(self.server.instance.home, 'Mods', 'tech', 'Tacview', 'bin')
        real_dll = os.path.join(tacview_bin, 'tacview_real.dll')
        if os.path.exists(real_dll):
            os.replace(real_dll, os.path.join(tacview_bin, 'tacview.dll'))
        for file in [os.path.join(tacview_bin, 'dks.ver'),
                     os.path.join(self.server.instance.home, 'Scripts', 'Hooks', 'DKSWeatherSampler.lua')]:
            if os.path.exists(file):
                os.remove(file)

    @override
    async def render(self, param: dict | None = None) -> dict:
        ret = await super().render(param)
        if not self.enabled:
            value = "not enabled"
        else:
            value = f"[DKS]({self.dks_url}/)"
            if self.version:
                value += f"\nExporter {self.version}"
        return ret | {
            "value": value
        }
