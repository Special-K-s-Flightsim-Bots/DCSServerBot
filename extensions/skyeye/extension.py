import aiohttp
import asyncio
import atexit
import certifi
import json
import logging
import os
import psutil
import re
import shutil
import ssl
import subprocess
import tempfile
import zipfile

from contextlib import suppress
from core import Extension, utils, ServiceRegistry, get_translation
from logging.handlers import RotatingFileHandler
from io import BytesIO
from packaging.version import parse
from pathlib import Path
from services.bot import BotService
from services.servicebus import ServiceBus
from threading import Thread
from typing import Optional, Union, Any

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML(typ='safe')

_ = get_translation(__name__.split('.')[1])

SKYEYE_GITHUB_URL = "https://github.com/dharmab/skyeye/releases/latest"
SKYEYE_DOWNLOAD_URL = "https://github.com/dharmab/skyeye/releases/download/{}/skyeye-windows-amd64.zip"
WHISPER_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{}"

LOGLEVEL = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'WARN': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
    'FATAL': logging.FATAL
}


class SkyEye(Extension):

    def __init__(self, server, config):
        self.configs = []
        super().__init__(server, config)
        self._version = None
        self.processes = []
        self.loggers = []
        if self.enabled:
            for process in utils.find_process(self.get_exe_path(), self.server.instance.name):
                self.processes.append(process)
            if self.processes:
                self.log.info(f"  => {self.name}: Running SkyEye server(s) detected.")
        # register shutdown handler
        atexit.register(self.terminate)

    def get_config_path(self, cfg: dict) -> str:
        path = os.path.expandvars(utils.format_string(
            cfg.get('config', '{instance.home}\\Config\\SkyEye-{coalition}.yaml'),
            server=self.server, instance=self.server.instance, coalition=cfg.get('coalition', 'blue'))
        )
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file {path} does not exist.")
        return path

    def load_config(self) -> Optional[dict]:
        base_config = os.path.join(os.path.dirname(self.get_exe_path()), "config.yaml")
        if os.path.exists(base_config):
            data = yaml.load(Path(base_config).read_text(encoding='utf-8')) or {}
        else:
            data = {}
        if 'instances' in self.config:
            main_config = self.config.copy()
            main_config.pop('instances')
            for instance in self.config['instances']:
                cfg = data.copy()
                cfg |= main_config
                cfg_file = self.get_config_path(cfg | instance)
                if os.path.exists(cfg_file):
                    cfg |= yaml.load(Path(cfg_file).read_text(encoding='utf-8')) or {}
                else:
                    self._prepare_config(cfg)
                cfg |= instance.copy()
                self.configs.append(cfg)
        else:
            cfg_file = self.get_config_path(self.config)
            if os.path.exists(cfg_file):
                self.config |= yaml.load(Path(cfg_file).read_text(encoding='utf-8')) or {}
            else:
                self._prepare_config(self.config)
            self.configs = [data | self.config]
        return data

    def set_affinity(self, process: psutil.Process, affinity: Union[list[int], str]):
        if isinstance(affinity, str):
            affinity = [int(x.strip()) for x in affinity.split(',')]
        elif isinstance(affinity, int):
            affinity = [affinity]
        self.log.info("  => Setting process affinity to {}".format(','.join(map(str, affinity))))
        process.cpu_affinity(affinity)

    async def download_whisper_file(self, name: str):
        whisper_path = os.path.join(os.path.dirname(self.get_exe_path()), "whisper.bin")
        async with aiohttp.ClientSession() as session:
            # Check the size of the remote file
            head_resp = await session.head(WHISPER_URL.format(name), allow_redirects=True, proxy=self.node.proxy,
                                           proxy_auth=self.node.proxy_auth)
            remote_size = int(head_resp.headers['Content-Length'])

            # Check the size of the local file
            if os.path.exists(whisper_path):
                local_size = os.path.getsize(whisper_path)
            else:
                local_size = 0

            # Download and update the file only if there's a new version available online
            if remote_size != local_size:
                if local_size == 0:
                    what = 'download'
                else:
                    what = 'updat'
                self.log.info(f"  => {self.name}: {what.title()}ing whisper model...")
                async with session.get(WHISPER_URL.format(name), raise_for_status=True, proxy=self.node.proxy,
                                       proxy_auth=self.node.proxy_auth) as response:
                    with open(whisper_path, 'wb') as f:
                        while True:
                            chunk = await response.content.read(1024 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                self.log.info(f"  => {self.name}: Whisper model {what}ed.")
            else:
                self.log.debug(f"  => {self.name}: Whisper model up-to-date.")

    def _maybe_update_config(self, cfg: dict, key: str, value: Any):
        if not value:
            return False
        if key in self.locals:
            if cfg[key] != value:
                cfg[key] = value
                return True
        else:
            cfg[key] = value
            return True
        return False

    async def _prepare_config(self, cfg: dict):
        dirty = False

        # make sure we have a local model, unless configured otherwise
        if cfg.get('recognizer', 'openai-whisper-local') == 'openai-whisper-local':
            await self.download_whisper_file(cfg.get('whisper-model', 'ggml-small.en.bin'))
            dirty |= self._maybe_update_config(cfg, 'recognizer', 'openai-whisper-local')
            dirty |= self._maybe_update_config(cfg,'recognizer-lock-path',
                                               os.path.join(os.path.dirname(self.get_exe_path()), 'recognizer.lck'))
        else:
            dirty |= self._maybe_update_config(cfg, 'recognizer', 'openai-whisper-api')
            dirty |= self._maybe_update_config(cfg, 'openai-api-key', cfg['openai-api-key'])

        dirty |= self._maybe_update_config(cfg, 'voice-lock-path',
                                           os.path.join(os.path.dirname(self.get_exe_path()), 'voice.lck'))
        dirty |= self._maybe_update_config(cfg, 'whisper-model', cfg.get('whisper-model', 'ggml-small.en.bin'))
        dirty |= self._maybe_update_config(cfg, 'coalition', cfg.get('coalition'))
        if 'callsign' in cfg:
            dirty |= self._maybe_update_config(cfg, 'callsign', cfg['callsign'])
        elif 'callsigns' in cfg:
            dirty |= self._maybe_update_config(cfg, 'callsigns', cfg['callsigns'])
        dirty |= self._maybe_update_config(cfg, 'voice', cfg.get('voice'))
        dirty |= self._maybe_update_config(cfg, 'voice-playback-speed', cfg.get('voice-playback-speed'))
        dirty |= self._maybe_update_config(cfg, 'voice-playback-pause', cfg.get('voice-playback-pause'))
        dirty |= self._maybe_update_config(cfg, 'auto-picture', cfg.get('auto-picture'))
        dirty |= self._maybe_update_config(cfg, 'auto-picture-interval', cfg.get('auto-picture-interval'))
        dirty |= self._maybe_update_config(cfg, 'threat-monitoring', cfg.get('threat-monitoring'))
        dirty |= self._maybe_update_config(cfg,
            'threat-monitoring-interval', cfg.get('threat-monitoring-interval'))
        dirty |= self._maybe_update_config(cfg,
            'mandatory-threat-radius', cfg.get('mandatory-threat-radius'))
        dirty |= self._maybe_update_config(cfg, 'log-format', 'json')
        if cfg.get('discord-webhook-id'):
            dirty |= self._maybe_update_config(cfg, 'discord-webhook-id', cfg['discord-webhook-id'])
            dirty |= self._maybe_update_config(cfg, 'discord-webhook-token', cfg['discord-webhook-token'])

        # Configure Tacview
        tacview = self.server.extensions.get('Tacview')
        if tacview:
            tacview_port = tacview.locals.get('tacviewRealTimeTelemetryPort', 42674)
            dirty |= self._maybe_update_config(cfg, 'telemetry-address', f"localhost:{tacview_port}")
            dirty |= self._maybe_update_config(cfg,
                'telemetry-password', tacview.locals.get('tacviewRealTimeTelemetryPassword')
            )
        else:
            # we definitely need Tacview, so if no Tacview extension is configured, expect the values to be in the config
            dirty |= self._maybe_update_config(cfg,
                'telemetry-address', cfg.get('telemetry-address', f"localhost:42674"))
            dirty |= self._maybe_update_config(cfg, 'telemetry-password', cfg.get('telemetry-password'))

        # Configure SRS
        srs = self.server.extensions.get('SRS')
        if srs:
            srs_port = srs.config.get('port', srs.locals['Server Settings']['SERVER_PORT'])
            dirty |= self._maybe_update_config(cfg, 'srs-server-address', f"localhost:{srs_port}")
            if cfg.get('coalition', 'blue') == 'blue':
                dirty |= self._maybe_update_config(cfg,
                    'srs-eam-password',
                    srs.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD']
                )
            else:
                dirty |= self._maybe_update_config(cfg,
                    'srs-eam-password',
                    srs.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_RED_PASSWORD']
                )
        else:
            # we definitely need SRS, so if no SRS extension is configured, expect the values to be in the config
            dirty |= self._maybe_update_config(cfg,
                'srs-server-address', cfg.get('srs-server-address', f"localhost:5002"))
            dirty |= self._maybe_update_config(cfg, 'srs-eam-password', cfg.get('srs-eam-password'))
        dirty |= self._maybe_update_config(cfg, 'srs-frequencies', cfg.get('srs-frequencies'))

        # Configure gRPC
        grpc = self.server.extensions.get('gRPC')
        if grpc:
            grpc_port = grpc.locals.get('port', 50051)
            dirty |= self._maybe_update_config(cfg, 'enable-grpc', True)
            dirty |= self._maybe_update_config(cfg, 'grpc-address', f"localhost:{grpc_port}")
            # grpc-password is not supported yet

        if dirty:
            with open(self.get_config_path(cfg), mode='w', encoding='utf-8') as outfile:
                out = cfg.copy()
                out.pop('installation', None)
                out.pop('autoupdate', None)
                out.pop('enabled', None)
                out.pop('log', None)
                out.pop('debug', None)
                out.pop('config', None)
                out.pop('affinity', None)
                yaml.dump(out, outfile)

    async def _autoupdate(self):
        try:
            version = await self.check_for_updates()
            if version:
                self.log.info(f"A new SkyEye update is available. Updating to version {version} ...")
                await self.do_update(version)
                self._version = version.lstrip('v')
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

    async def prepare(self) -> bool:
        for cfg in self.configs:
            await self._prepare_config(cfg)
        if self.config.get('autoupdate', False):
            await self._autoupdate()
        return await super().prepare()

    async def startup(self) -> bool:
        def run_subprocess(cfg: dict):
            debug = cfg.get('debug', False)
            log_file = utils.format_string(cfg.get('log'),
                                           server=self.server,
                                           instance=self.server.instance,
                                           coalition=cfg.get('coalition'))

            if log_file:
                logger = logging.getLogger(os.path.basename(log_file)[:-4])
                logger.setLevel(logging.DEBUG)
                if not logger.handlers:
                    handler = RotatingFileHandler(
                        log_file,
                        maxBytes=10 * 1024 * 1024,
                        backupCount=5
                    )
                    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s%(extra_data)s')
                    handler.setFormatter(formatter)
                    handler.doRollover()
                    logger.addHandler(handler)
                logger.propagate = False
                self.loggers.append(logger)

            # Define the subprocess command
            args = [
                self.get_exe_path(),
                '--config-file', self.get_config_path(cfg)
            ]
            if cfg.get('recognizer', 'openai-whisper-local') == 'openai-whisper-local':
                args.extend(['--whisper-model', 'whisper.bin'])

            self.log.debug("Launching {}".format(' '.join(args)))

            # Launch the subprocess and capture stdout/stderr
            proc = subprocess.Popen(
                args,
                cwd=os.path.dirname(self.get_exe_path()),
                stdout=subprocess.PIPE if log_file else subprocess.DEVNULL,
                stderr=subprocess.PIPE if log_file else subprocess.DEVNULL,
                close_fds=True,
                universal_newlines=True  # Ensure text mode for captured output
            )

            def log_output(pipe, logger):
                def get_remaining_values(data: dict) -> dict:
                    return {key: value for key, value in data.items() if key not in ['level', 'message', 'time']}

                for line in iter(pipe.readline, ''):
                    if line.startswith('{'):
                        try:
                            data = json.loads(line)
                            level = LOGLEVEL[data.get('level', 'INFO').upper()]
                            message = data['message']
                            extra_data = get_remaining_values(data)
                            logger.log(level, message, extra={"extra_data": f"- {extra_data}" if extra_data else ""})
                            continue
                        except json.JSONDecodeError:
                            pass
                    elif debug:
                        self.log.debug(f"{self.name}: {line.rstrip()}")
                pipe.close()

            if log_file:
                Thread(target=log_output, args=(proc.stdout, logger), daemon=True).start()
                Thread(target=log_output, args=(proc.stderr, logger), daemon=True).start()

            return proc

        try:
            # avoid race conditions on startup
            async with self.lock:
                if self.is_running():
                    return True
                else:
                    self.shutdown()

                # Start the SkyEye server(s)
                for cfg in self.configs:
                    # waiting for SRS to be started
                    self.log.debug(f"{self.name}: Waiting for SRS to start ...")
                    ip, port = cfg.get('srs-server-address').split(':')
                    # Give the SRS server 10s to start
                    for _ in range(0, 10):
                        if utils.is_open(ip, port):
                            break
                        await asyncio.sleep(1)
                    else:
                        self.log.warning(f"  => {self.name}: SRS is not running, skipping SkyEye.")
                        return False
                    self.log.debug(f"{self.name}: SRS is running, launching SkyEye ...")

                    p = await asyncio.to_thread(run_subprocess, cfg)
                    try:
                        process = psutil.Process(p.pid)
                        if cfg.get('affinity'):
                            self.set_affinity(process, cfg['affinity'])
                        else:
                            p_core_affinity = utils.get_p_core_affinity()
                            if p_core_affinity:
                                self.log.warning("No core-affinity set for SkyEye server, using all available P-cores!")
                                self.set_affinity(process, utils.get_cpus_from_affinity(p_core_affinity))
                            else:
                                self.log.warning("No core-affinity set for SkyEye server, using all available cores!")
                        self.processes.append(process)
                    except (AttributeError, psutil.NoSuchProcess):
                        self.log.error(f"Failed to start SkyEye server, enable debug in the extension.")
                        return False
        except OSError as ex:
            self.log.error("Error while starting SkyEye: " + str(ex))
            return False
        # Give the SkyEye server(s) 10s to start
        for _ in range(0, 10):
            if self.is_running():
                break
            await asyncio.sleep(1)
        else:
            return False
        return await super().startup()

    def terminate(self) -> bool:
        try:
            for process in self.processes:
                utils.terminate_process(process)
            self.processes = []
            return True
        except Exception as ex:
            self.log.error(f"Error during shutdown of {self.get_exe_path()}: {str(ex)}")
            return False

    def shutdown(self) -> bool:
        def close_log_handlers(self):
            for logger in self.loggers:
                while logger.handlers:  # Remove and close all handlers
                    handler = logger.handlers[0]
                    handler.close()
                    logger.removeHandler(handler)
        try:
            super().shutdown()
            return self.terminate()
        finally:
            close_log_handlers(self)

    def is_running(self) -> bool:
        for process in self.processes:
            if not process.is_running():
                return False
        return len(self.processes) == len(self.configs)

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
        value = ""
        for cfg in self.configs:
            coalition = '🔹' if cfg.get('coalition', 'blue') == 'blue' else '🔸'
            value += f"{coalition} {cfg.get('callsign', 'Focus')}: {cfg.get('srs-frequencies', '251.0AM,133.0AM,30.0FM')}\n"
        return {
            "name": self.__class__.__name__,
            "version": self.version,
            "value": value
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
        with suppress(aiohttp.ClientConnectionError):
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get(SKYEYE_GITHUB_URL, proxy=self.node.proxy,
                                       proxy_auth=self.node.proxy_auth) as response:
                    if response.status in [200, 302]:
                        version = response.url.raw_parts[-1]
                        if parse(version) > parse(self.version):
                            return version
        return None

    async def do_update(self, version: str):
        installation_dir = os.path.expandvars(self.config['installation'])
        async with aiohttp.ClientSession() as session:
            async with session.get(SKYEYE_DOWNLOAD_URL.format(version), raise_for_status=True, proxy=self.node.proxy,
                                   proxy_auth=self.node.proxy_auth) as response:
                zipdata = BytesIO(await response.content.read())
                with tempfile.TemporaryDirectory() as tmpdir:
                    with zipfile.ZipFile(zipdata) as z:
                        z.extractall(tmpdir)
                    root_folder = os.path.join(tmpdir, 'skyeye-windows-amd64')
                    for item in os.listdir(root_folder):
                        source_path = os.path.join(root_folder, item)
                        destination_path = os.path.join(installation_dir, item)
                        if os.path.exists(destination_path):
                            if os.path.isdir(destination_path):
                                shutil.rmtree(destination_path)
                            else:
                                os.remove(destination_path)
                        shutil.move(source_path, destination_path)
