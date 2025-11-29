import asyncio
import os
import subprocess

from core import Server, Status, Coalition, utils
from packaging.version import parse
from threading import Thread
from services.music.radios.base import RadioInitError, Radio
from plugins.music.utils import get_tag


class SRSRadio(Radio):

    def __init__(self, name: str, server: Server):
        super().__init__(name, server)
        self.process: subprocess.Popen | None = None

    async def play(self, file: str) -> None:
        if self.current and self.process:
            await self.skip()
        if self.server.status != Status.RUNNING:
            await self.stop()
            return
        self.log.debug(f"Playing {file} ...")

        try:
            try:
                srs_inst = os.path.expandvars(
                    self.server.extensions['SRS'].config.get('installation',
                                                             '%ProgramFiles%\\DCS-SimpleRadio-Standalone'))
                srs_port = self.server.extensions['SRS'].locals['Server Settings']['SERVER_PORT']
            except KeyError:
                raise RadioInitError("You need to set the SRS path in your nodes.yaml!")
            self.current = file

            def exe_path() -> str:
                if parse(self.server.extensions['SRS'].version) >= parse('2.2.0.0'):
                    return os.path.join(srs_inst, "ExternalAudio", "DCS-SR-ExternalAudio.exe")
                else:
                    return os.path.join(srs_inst, "DCS-SR-ExternalAudio.exe")

            def run_subprocess() -> subprocess.Popen:
                def _log_output(p: subprocess.Popen):
                    for line in iter(p.stdout.readline, b''):
                        self.log.debug(line.decode('utf-8', errors='replace').rstrip())

                debug = self.service.get_config().get('debug', False)
                out = subprocess.PIPE if debug else subprocess.DEVNULL
                err = subprocess.PIPE if debug else subprocess.DEVNULL

                args = [
                    exe_path(),
                    "-f", str(self.config['frequency']),
                    "-m", self.config['modulation'],
                    "-c", str(self.config['coalition']),
                    "-v", str(self.config.get('volume', 1.0)),
                    "-p", str(srs_port),
                    "-n", self.config.get('display_name', 'DCSSB MusicBox'),
                    "-i", file
                ]
                # TODO: temporary workaround
                if parse(self.server.extensions['SRS'].version) == parse('2.3.3.2'):
                    args.extend(['--unitId', '1000'])

                if debug:
                    self.log.debug(f"Running {' '.join(args)}")
                p = subprocess.Popen(args, stdout=out, stderr=err)
                if debug:
                    Thread(target=_log_output, args=(p,), daemon=True).start()
                return p

            self.process = await asyncio.to_thread(run_subprocess)
            coalition = Coalition.BLUE if int(self.config['coalition']) == 2 else Coalition.RED
            if 'popup' in self.config:
                kwargs = self.config.copy()
                kwargs['song'] = get_tag(file).title or os.path.basename(file)
                await self.server.sendPopupMessage(coalition, utils.format_string(self.config['popup'], **kwargs))
            if 'chat' in self.config:
                kwargs = self.config.copy()
                kwargs['song'] = get_tag(file).title or os.path.basename(file)
                await self.server.sendChatMessage(coalition, utils.format_string(self.config['chat'], **kwargs))
            await asyncio.to_thread(self.process.wait)
        except Exception as ex:
            self.log.exception(ex)
        finally:
            self.current = None
            self.process = None

    async def skip(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.current = None
            self.process = None

    async def stop(self) -> None:
        if self.queue_worker.is_running():
            self.queue_worker.cancel()
            await self.skip()
            while self.queue_worker.is_running():
                await asyncio.sleep(0.5)
        else:
            await self.skip()
