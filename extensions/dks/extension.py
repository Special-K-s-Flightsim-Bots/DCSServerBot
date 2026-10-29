import aiohttp
import base64
import certifi
import glob
import io
import json
import os
import re
import shutil
import ssl
import zipfile

from core import InstallableExtension, get_translation, Port, PortType, Status
from typing_extensions import override

_ = get_translation(__name__.split('.')[1])

__all__ = [
    "DKS"
]

DKS_URL = "https://www.digitalkneeboardsimulator.com"
DKS_EXPORTER_PATH = "/api/telemetry/exporter"
# The only paths the exporter ZIP may write, and its size limit.
ZIP_PATHS = ('Scripts/Hooks/DKS-Telemetry.lua', 'Mods/tech/DKSTelemetry/')
ZIP_MAX_BYTES = 100 * 1024 * 1024
VERSION_PATTERN = re.compile(r'^(\d+)\.(\d+)\.(\d+)$')


def lua_string(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n') + '"'


class DKS(InstallableExtension):
    """
    Installs the DKS exporter, which records the server for Digital Kneeboard Simulator and replaces Tacview: it takes
    over Tacview's real-time telemetry port and writes Tacview files to Tacview's folder. The exporter updates itself
    (signed releases), so this extension installs it once and keeps its key file in sync.
    """

    CONFIG_DICT = {
        "key": {
            "type": str,
            "label": _("Install code"),
            "placeholder": _("From your server's page in DKS"),
            "required": True
        }
    }

    @property
    def identity(self) -> dict[str, str]:
        """dksUrl, keyId, secret and ownerId, from the install code."""
        code = (self.config.get('key') or '').strip().strip('"\'')
        if code.startswith('dks1.'):
            data = code[len('dks1.'):]
            try:
                decoded = json.loads(base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)))
                return {k: v for k, v in decoded.items() if isinstance(v, str)}
            except (ValueError, AttributeError):
                pass
        return {}

    def has_identity(self) -> bool:
        if all(self.identity.get(x) for x in ['keyId', 'secret', 'ownerId']):
            return True
        if self.config.get('key'):
            self.log.error(f"  => {self.name}: The install code is not valid. Copy it again from your server's page "
                           f"in DKS, then run /extension enable, type DKS and paste it.")
        else:
            self.log.error(f"  => {self.name}: No install code. The DKS extension installs the DKS exporter now: copy "
                           f"the install code from your server's page in DKS, then run /extension enable, type DKS "
                           f"and paste the code.")
        return False

    def server_down(self) -> bool:
        # DCS holds the exporter's DLL while it runs.
        if self.server.status in [Status.UNREGISTERED, Status.SHUTDOWN]:
            return True
        self.log.error(f"  => {self.name}: Stop the server before you disable DKS.")
        return False

    @property
    def dks_url(self) -> str:
        return self.identity.get('dksUrl', DKS_URL).rstrip('/')

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
        # The newest version the exporter's startup hook would run: complete and not marked failed.
        versions_dir = os.path.join(self.mod_dir, 'versions')
        if not os.path.isdir(versions_dir):
            return None
        versions = []
        for name in os.listdir(versions_dir):
            match = VERSION_PATTERN.match(name)
            folder = os.path.join(versions_dir, name)
            if (match and os.path.exists(os.path.join(folder, 'dkstelemetry.dll'))
                    and os.path.exists(os.path.join(folder, 'DKS-Telemetry.lua'))
                    and not os.path.exists(os.path.join(folder, 'failed'))):
                versions.append((tuple(int(x) for x in match.groups()), name))
        return max(versions)[1] if versions else None

    def tacview_enabled(self) -> bool:
        tacview = self.server.extensions.get('Tacview')
        return tacview is not None and tacview.enabled

    @property
    def tacview_options(self) -> dict:
        return (self.server.options['plugins'] or {}).get('Tacview', {})

    @override
    def is_installed(self) -> bool:
        return os.path.exists(self.hook) and os.path.isdir(self.mod_dir)

    @override
    async def install(self, version: str | None = None) -> bool:
        self.remove_weather_exporter()
        if not self.has_identity():
            return False
        # The exporter updates itself: once it is there, nothing is downloaded again (its files may be in use).
        if not self.is_installed():
            try:
                await self.download()
            except Exception as ex:
                self.log.error(f"  => {self.name}: Couldn't download the DKS exporter: {ex}. You can also download "
                               f"the exporter ZIP from your server's page in DKS and extract it into "
                               f"{self.server.instance.home}.")
                return False
            self.log.info(f"  => {self.name} {self.version} installed into instance {self.server.instance.name}.")
        # Only now that DKS is in place, Tacview goes: its files stay, DCSServerBot stops running it.
        if self.tacview_enabled():
            await self.server.config_extension('Tacview', {"Instance": {"enabled": False}})
            self.log.info(f"  => {self.server.name}: Tacview extension disabled, DKS replaces it.")
        return True

    async def download(self):
        # Native code only ever comes from DKS over https, never from an address in an install code.
        url = f"{DKS_URL}{DKS_EXPORTER_PATH}?serverId={self.identity['ownerId']}"
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
            async with session.get(url, raise_for_status=True, proxy=self.node.proxy,
                                   proxy_auth=self.node.proxy_auth) as response:
                data = bytearray()
                async for chunk in response.content.iter_chunked(1024 * 1024):
                    data += chunk
                    if len(data) > ZIP_MAX_BYTES:
                        raise ValueError("the download is too large")
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
            members = [m for m in zf.namelist() if m.startswith(ZIP_PATHS) and '..' not in m]
            if sum(zf.getinfo(m).file_size for m in members) > ZIP_MAX_BYTES:
                raise ValueError("the download is too large")
            zf.extractall(self.server.instance.home, members=members)

    @override
    async def disable(self) -> bool:
        # Refuse before the extension is shut down and marked disabled, not halfway.
        if self.is_installed() and not self.server_down():
            return False
        return await super().disable()

    @override
    async def uninstall(self) -> bool:
        # Removing files while DCS runs would leave half an exporter behind.
        if not self.server_down():
            return False
        # Recordings not yet uploaded stay in Saved Games\...\DKS\telemetry.
        try:
            for file in [self.hook, self.cfg]:
                if os.path.exists(file):
                    os.remove(file)
            if os.path.isdir(self.mod_dir):
                shutil.rmtree(self.mod_dir)
        except OSError as ex:
            self.log.error(f"  => {self.name}: Couldn't remove the DKS exporter, stop the server first: {ex}")
            return False
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
        if not self.has_identity():
            return False
        self.remove_weather_exporter()
        self.turn_off_tacview()
        self.write_cfg()
        return await super().prepare()

    def tacview_settings(self) -> dict:
        """What Tacview did on this server, for the exporter to carry on: real-time telemetry and Tacview files."""
        tacview = self.tacview_options
        if not tacview:
            return {}
        settings = {"acmi": bool(tacview.get('tacviewFlightDataRecordingEnabled', True))}
        if tacview.get('tacviewExportPath'):
            settings['acmiDir'] = tacview['tacviewExportPath']
        if tacview.get('tacviewRealTimeTelemetryEnabled', True):
            settings |= {
                "rtt": True,
                "rttPort": int(tacview.get('tacviewRealTimeTelemetryPort', 42674)),
                "rttPassword": tacview.get('tacviewRealTimeTelemetryPassword', '')
            }
        return settings

    def write_cfg(self):
        identity = self.identity
        lines = [
            "-- Written by DCSServerBot from nodes.yaml (extension DKS). Keep it private.",
            "DKS_TELEMETRY = {",
            f"  dksUrl = {lua_string(self.dks_url)},",
            f"  keyId = {lua_string(identity['keyId'])},",
            f"  secret = {lua_string(identity['secret'])},",
            f"  ownerId = {lua_string(identity['ownerId'])},",
            f"  serverName = {lua_string(self.server.name)},",
        ]
        for name, value in self.tacview_settings().items():
            if isinstance(value, bool):
                lines.append(f"  {name} = {'true' if value else 'false'},")
            elif isinstance(value, int):
                lines.append(f"  {name} = {value},")
            else:
                lines.append(f"  {name} = {lua_string(str(value))},")
        lines += ["}", ""]
        text = '\n'.join(lines)
        if os.path.exists(self.cfg):
            with open(self.cfg, 'r', encoding='utf-8') as f:
                if f.read() == text:
                    return
        os.makedirs(os.path.dirname(self.cfg), exist_ok=True)
        with open(self.cfg, 'w', encoding='utf-8') as f:
            f.write(text)

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
        hook = os.path.join(self.server.instance.home, 'Scripts', 'Hooks', 'DKSWeatherSampler.lua')
        if os.path.exists(hook):
            os.remove(hook)
        try:
            if os.path.exists(real_dll):
                os.replace(real_dll, os.path.join(tacview_bin, 'tacview.dll'))
            for pattern in ['dks.ver', 'dks-wx.*', 'dks-census.txt', 'dks-event*.txt']:
                for file in glob.glob(os.path.join(tacview_bin, pattern)):
                    os.remove(file)
        except OSError as ex:
            # Its DLL is in use while DCS runs: the next start removes it.
            self.log.warning(f"  => {self.name}: The old DKS weather exporter will be removed at the next start: {ex}")

    @override
    def get_ports(self) -> dict[str, Port]:
        settings = self.tacview_settings()
        if settings.get('rtt'):
            return {"rttPort": Port(settings['rttPort'], PortType.TCP, public=True)}
        return {}

    @override
    async def render(self, param: dict | None = None) -> dict:
        ret = await super().render(param)
        if not self.enabled:
            value = "not enabled"
        else:
            value = f"[DKS]({self.dks_url}/)"
            if self.version:
                value += f"\nExporter {self.version}"
            if self.tacview_settings().get('rtt'):
                value += f"\nReal-time telemetry on port {self.tacview_settings()['rttPort']}"
        return ret | {
            "value": value
        }
