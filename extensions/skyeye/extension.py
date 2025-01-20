import aiohttp
import asyncio
import certifi
import os
import psutil
import re
import shutil
import ssl
import subprocess
import zipfile

from contextlib import suppress
from core import Extension, utils, ServiceRegistry, get_translation
from discord.ext import tasks
from io import BytesIO
from packaging.version import parse
from pathlib import Path
from services.bot import BotService
from services.servicebus import ServiceBus
from threading import Thread
from typing import Optional, Union, Any

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = get_translation(__name__.split('.')[1])

SKYEYE_GITHUB_URL = "https://github.com/dharmab/skyeye/releases/latest"
SKYEYE_DOWNLOAD_URL = "https://github.com/dharmab/skyeye/releases/download/{}/skyeye-windows-amd64.zip"
WHISPER_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{}"


class SkyEye(Extension):

    def __init__(self, server, config):
        super().__init__(server, config)
        self._version = None
        self.process: Optional[psutil.Process] = utils.find_process(self.get_exe_path(), self.server.instance.name)
        if self.process:
            self.log.info(f"  => {self.name}: Running SkyEye server detected.")

    def load_config(self) -> Optional[dict]:
        path = os.path.expandvars(self.config['config'])
        if not os.path.exists(path):
            base_config = os.path.join(os.path.dirname(self.get_exe_path()), "config.yaml")
            if not os.path.exists(base_config):
                return {}
            shutil.copy2(base_config, path)
        return yaml.load(Path(path).read_text(encoding='utf-8'))

    def set_affinity(self, affinity: Union[list[int], str]):
        if isinstance(affinity, str):
            affinity = [int(x.strip()) for x in affinity.split(',')]
        elif isinstance(affinity, int):
            affinity = [affinity]
        self.log.info("  => Setting process affinity to {}".format(','.join(map(str, affinity))))
        self.process.cpu_affinity(affinity)

    async def download_whisper_file(self, name: str):
        whisper_path = os.path.join(os.path.dirname(self.get_exe_path()), "whisper.bin")
        async with aiohttp.ClientSession() as session:
            async with session.get(WHISPER_URL.format(name), raise_for_status=True) as response:
                with open(whisper_path, 'wb') as f:
                    while True:
                        chunk = await response.content.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)

    def _maybe_update_config(self, key: str, value: Any):
        if not value:
            return False
        if key in self.locals:
            if self.locals[key] != value:
                self.locals[key] = value
                return True
        else:
            self.locals[key] = value
            return True
        return False

    async def prepare(self) -> bool:
        dirty = False

        # make sure we have a local model, unless configured otherwise
        if self.config.get('recognizer', 'openai-whisper-local') == 'openai-whisper-local':
            self.log.warning(f"  => {self.name}: Local Whisper model configured. This has a performance impact on your system!")
            whisper_path = os.path.join(os.path.dirname(self.get_exe_path()), "whisper.bin")
            if not os.path.exists(whisper_path):
                self.log.info(f"  => {self.name}: Downloading whisper model...")
                await self.download_whisper_file(self.config.get('whisper-model', 'ggml-small.en.bin'))
                self.log.info(f"  => {self.name}: Whisper model downloaded.")
            dirty |= self._maybe_update_config('recognizer', 'openai-whisper-local')
        else:
            dirty |= self._maybe_update_config('openai-api-key', self.config['openai-api-key'])

        dirty |= self._maybe_update_config('whisper-model', self.config.get('whisper-model', 'ggml-small.en.bin'))
        dirty |= self._maybe_update_config('coalition', self.config.get('coalition'))
        if 'callsign' in self.config:
            dirty |= self._maybe_update_config('callsign', self.config['callsign'])
        elif 'callsigns' in self.config:
            dirty |= self._maybe_update_config('callsigns', self.config['callsigns'])
        else:
            dirty |= self._maybe_update_config('callsign', 'Focus')
        dirty |= self._maybe_update_config('voice', self.config.get('voice'))
        dirty |= self._maybe_update_config('voice-playback-speed', self.config.get('voice-playback-speed'))
        dirty |= self._maybe_update_config('voice-playback-pause', self.config.get('voice-playback-pause'))
        dirty |= self._maybe_update_config('auto-picture', self.config.get('auto-picture'))
        dirty |= self._maybe_update_config('auto-picture-interval', self.config.get('auto-picture-interval'))
        dirty |= self._maybe_update_config('threat-monitoring', self.config.get('threat-monitoring'))
        dirty |= self._maybe_update_config(
            'threat-monitoring-interval', self.config.get('threat-monitoring-interval'))
        dirty |= self._maybe_update_config(
            'mandatory-threat-radius', self.config.get('mandatory-threat-radius'))
        dirty |= self._maybe_update_config('log-format', 'json')
        if self.config.get('discord-webhook-id'):
            dirty |= self._maybe_update_config('discord-webhook-id', self.config['discord-webhook-id'])
            dirty |= self._maybe_update_config('discord-webhook-token', self.config['discord-webhook-token'])

        # Configure Tacview
        tacview = self.server.extensions.get('Tacview')
        if tacview:
            tacview_port = tacview.locals.get('tacviewRealTimeTelemetryPort', 42674)
            dirty |= self._maybe_update_config('telemetry-address', f"localhost:{tacview_port}")
            dirty |= self._maybe_update_config(
                'telemetry-password', tacview.locals.get('tacviewRealTimeTelemetryPassword')
            )
        else:
            # we definitely need Tacview, so if no Tacview extension is configured, expect the values to be in the config
            dirty |= self._maybe_update_config(
                'telemetry-address', self.config.get('telemetry-address', f"localhost:42674"))
            dirty |= self._maybe_update_config('telemetry-password', self.config.get('telemetry-password'))

        # Configure SRS
        srs = self.server.extensions.get('SRS')
        if srs:
            srs_port = srs.config.get('port', srs.locals['Server Settings']['SERVER_PORT'])
            dirty |= self._maybe_update_config('srs-server-address', f"localhost:{srs_port}")
            if self.config['coalition'] == 'blue':
                dirty |= self._maybe_update_config(
                    'srs-eam-password',
                    srs.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD']
                )
            else:
                dirty |= self._maybe_update_config(
                    'srs-eam-password',
                    srs.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_RED_PASSWORD']
                )
        else:
            # we definitely need SRS, so if no SRS extension is configured, expect the values to be in the config
            dirty |= self._maybe_update_config(
                'srs-server-address', self.config.get('srs-server-address', f"localhost:5002"))
            dirty |= self._maybe_update_config('srs-eam-password', self.config.get('srs-eam-password'))
        dirty |= self._maybe_update_config('srs-frequencies', self.config.get('srs-frequencies'))

        # Configure gRPC
        grpc = self.server.extensions.get('gRPC')
        if grpc:
            grpc_port = grpc.locals.get('port', 50051)
            dirty |= self._maybe_update_config('enable-grpc', True)
            dirty |= self._maybe_update_config('grpc-address', f"localhost:{grpc_port}")
            # grpc-password is not supported yet

        if dirty:
            with open(os.path.expandvars(self.config['config']), mode='w', encoding='utf-8') as outfile:
                yaml.dump(self.locals, outfile)
        return await super().prepare()

    async def startup(self) -> bool:
        def log_output(proc: subprocess.Popen):
            for line in iter(proc.stdout.readline, b''):
                self.log.debug(line.decode('utf-8').rstrip())

        def run_subprocess():
            out = subprocess.PIPE if self.config.get('debug', False) else subprocess.DEVNULL
            args = [
                self.get_exe_path(),
                '--config-file', os.path.expandvars(self.config['config']),
                '--whisper-model', 'whisper.bin'
            ]
            self.log.debug("Launching {}".format(' '.join(args)))
            proc = subprocess.Popen(
                args, cwd=os.path.dirname(self.get_exe_path()), stdout=out, stderr=subprocess.STDOUT, close_fds=True
            )
            if self.config.get('debug', False):
                Thread(target=log_output, args=(proc,), daemon=True).start()
            return proc

        try:
            # waiting for SRS to be started
            self.log.debug(f"{self.name}: Waiting for SRS to start ...")
            ip, port = self.locals.get('srs-server-address').split(':')
            # Give the SkyEye server 10s to start
            for _ in range(0, 10):
                if utils.is_open(ip, port):
                    break
                await asyncio.sleep(1)
            else:
                self.log.warning(f"  => {self.name}: SRS is not running, skipping SkyEye.")
                return False
            self.log.debug(f"{self.name}: SRS is running, launching SkyEye ...")
            # Start the SkyEye server
            p = await asyncio.to_thread(run_subprocess)
            try:
                self.process = psutil.Process(p.pid)
                if self.config.get('affinity'):
                    self.set_affinity(self.config['affinity'])
                else:
                    p_core_affinity = utils.get_p_core_affinity()
                    if p_core_affinity:
                        self.log.warning("No core-affinity set for SkyEye server, using all available P-cores!")
                        self.set_affinity(utils.get_cpus_from_affinity(p_core_affinity))
                    else:
                        self.log.warning("No core-affinity set for SkyEye server, using all available cores!")

            except (AttributeError, psutil.NoSuchProcess):
                self.log.error(f"Failed to start SkyEye server, enable debug in the extension.")
                return False
        except OSError as ex:
            self.log.error("Error while starting SkyEye: " + str(ex))
            return False
        # Give the SkyEye server 10s to start
        for _ in range(0, 10):
            if self.is_running():
                break
            await asyncio.sleep(1)
        else:
            return False
        return await super().startup()

    def terminate(self) -> bool:
        try:
            utils.terminate_process(self.process)
            self.process = None
            return True
        except Exception as ex:
            self.log.error(f"Error during shutdown of {self.get_exe_path()}: {str(ex)}")
            return False

    def shutdown(self) -> bool:
        super().shutdown()
        return self.terminate()

    def is_running(self) -> bool:
        return self.process is not None and self.process.is_running()

    def get_exe_path(self) -> str:
        return os.path.join(os.path.expandvars(self.config['installation']), "skyeye.exe")

    def _get_version(self) -> Optional[str]:
        with suppress(Exception):
            # Run the program and capture its output
            result = subprocess.run([self.get_exe_path(), '--version'], text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if result.returncode == 0:
                match = re.search(r'v(\d+\.\d+\.\d+)', result.stdout)
                if match:
                    return match.group(1)
        return "1.1.1"  # default version

    @property
    def version(self) -> Optional[str]:
        if not self._version:
            # try to read the version from the exe
            self._version = utils.get_windows_version(self.get_exe_path())
            if not self._version:
                self._version = self._get_version()
        return self._version

    async def render(self, param: Optional[dict] = None) -> dict:
        return {
            "name": self.__class__.__name__,
            "version": self.version,
            "value": '\n'.join([x.strip() for x in self.locals.get('srs-frequencies', '').split(',')])
        }

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        exe_path = self.get_exe_path()
        if not os.path.exists(exe_path):
            self.log.error(f"  => SkyEye executable not found in {exe_path}")
            return False
        return True

    async def check_for_updates(self) -> Optional[str]:
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get(SKYEYE_GITHUB_URL) as response:
                    if response.status in [200, 302]:
                        version = response.url.raw_parts[-1]
                        if parse(version) > parse(self.version):
                            return version
                        else:
                            return None
        except aiohttp.ClientConnectionError:
            return None

    async def do_update(self, version: str):
        installation_dir = os.path.expandvars(self.config['installation'])
        async with aiohttp.ClientSession() as session:
            async with session.get(SKYEYE_DOWNLOAD_URL.format(version), raise_for_status=True) as response:
                with zipfile.ZipFile(BytesIO(await response.content.read())) as z:
                    root_folder = z.namelist()[0].split('/')[0]
                    for member in z.namelist():
                        relative_path = os.path.relpath(member, start=root_folder)
                        if relative_path == ".":
                            continue
                        destination_path = os.path.join(installation_dir, relative_path)
                        if member.endswith('/'):
                            os.makedirs(destination_path, exist_ok=True)
                        else:
                            os.makedirs(os.path.dirname(destination_path), exist_ok=True)
                            with open(destination_path, 'wb') as output_file:
                                output_file.write(z.read(member))

    @tasks.loop(minutes=30)
    async def schedule(self):
        if not self.config.get('autoupdate', False):
            return
        try:
            version = await self.check_for_updates()
            if version:
                self.log.info(f"A new SkyEye update is available. Updating to version {version} ...")
                await self.do_update(version)
                self.log.info("SkyEye updated.")
                bus = ServiceRegistry.get(ServiceBus)
                await bus.send_to_node({
                    "command": "rpc",
                    "service": BotService.__name__,
                    "method": "audit",
                    "params": {
                        "message": f"{self.name} updated to version {version} on node {self.node.name}."
                    }
                })
        except Exception as ex:
            self.log.exception(ex)
