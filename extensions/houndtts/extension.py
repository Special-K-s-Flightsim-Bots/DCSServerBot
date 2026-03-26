import os

from core import Server, utils, InstallableExtension
from typing_extensions import override


class HoundTTS(InstallableExtension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config, repo="https://github.com/uriba107/HoundTTS", package_name="HoundTTS-windows")
        self.home = os.path.join(self.server.instance.home, 'Mods', 'Services', 'HoundTTS')

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
