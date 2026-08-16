import aiofiles
import aiohttp
import certifi
import os
import ssl

from aiohttp import ClientResponseError
from contextlib import suppress
from core import InstallableExtension, utils
from packaging.version import parse
from typing_extensions import override

from extensions.tacview import Tacview

DKS_GITHUB_URL = "https://api.github.com/repos/jassmith/tacview-wx-exporter/releases/latest"
DKS_DLL_DOWNLOAD_URL = "https://github.com/jassmith/tacview-wx-exporter/releases/download/v{version}/tacview.dll"
DKS_LUA_DOWNLOAD_URL = "https://raw.githubusercontent.com/jassmith/tacview-wx-exporter/v{version}/lua/DKSWeatherSampler.lua"


class DKS(InstallableExtension):
    @property
    def tacview(self) -> Tacview | None:
        return self.server.extensions.get('Tacview')

    @property
    def version(self) -> str | None:
        version_file = os.path.join(self.server.instance.home, r'Mods\tech\Tacview\bin\dks.ver')
        if os.path.exists(version_file):
            with open(version_file, 'r') as f:
                return f.readline()
        return None

    @property
    def tacview_dll(self) -> str:
        return os.path.join(self.server.instance.home, r'Mods\tech\Tacview\bin\tacview.dll')

    @property
    def dks_dll(self) -> str:
        return os.path.join(self.server.instance.home, r'Mods\tech\Tacview\bin\tacview_real.dll')

    @property
    def dks_lua(self) -> str:
        return os.path.join(self.server.instance.home, 'Scripts', 'Hooks', 'DKSWeatherSampler.lua')

    @override
    def is_installed(self) -> bool:
        return os.path.exists(self.dks_dll) and os.path.exists(self.dks_lua)

    @override
    async def prepare(self) -> bool:
        if not self.tacview or not self.tacview.enabled:
            self.log.warning(f"  => {self.name}: Tacview is not active, disabling {self.name} ...")
            await self.disable()
            return False
        if not self.tacview.locals.get('tacviewRealTimeTelemetryEnabled', True):
            self.log.info(f"  => {self.name}: To get live feed, set tacviewRealTimeTelemetryEnabled to true.")
        if not self.tacview.locals.get('tacviewRealTimeTelemetryPassword'):
            self.log.warning(f"  => {self.name}: You do not have a tacviewRealTimeTelemetryPassword set!")
        return await super().prepare()

    @override
    async def install(self, version: str | None = None) -> bool:
        if not self.tacview or not self.tacview.enabled:
            self.log.warning(f"  => {self.name}: Tacview not active, skipping.")
            await self.disable()
            return False

        if not os.path.exists(self.dks_dll):
            os.rename(self.tacview_dll, self.dks_dll)
        # try to install the same version as tacview
        try:
            try:
                await self.do_update(utils.get_windows_version(self.dks_dll))
                self.log.info(f"  => {self.name} {self.version} installed into instance {self.server.instance.name}.")
                return True
            except ClientResponseError as ex:
                if ex.status == 404:
                    await self.do_update(await self.get_latest_version())
                    self.log.info(
                        f"  => {self.name} {self.version} installed into instance {self.server.instance.name}.")
                    return True
                raise
        except Exception as ex:
            self.log.exception(ex)
            return False

    @override
    async def uninstall(self) -> bool:
        if os.path.exists(self.dks_dll):
            os.remove(self.tacview_dll)
            os.rename(self.dks_dll, self.tacview_dll)
        if os.path.exists(self.dks_lua):
            os.remove(self.dks_lua)
        return True

    @override
    async def update_available(self) -> str | None:
        latest = await self.get_latest_version()
        if not latest:
            return None
        # was there a tacview update?
        if parse(utils.get_windows_version(self.tacview_dll)) > parse(utils.get_windows_version(self.dks_dll)):
            os.remove(self.dks_dll)
            return latest
        elif parse(latest) > parse(self.version):
            return latest
        return None

    @override
    async def get_latest_version(self) -> str | None:
        with suppress(aiohttp.ClientConnectionError):
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get(DKS_GITHUB_URL, proxy=self.node.proxy,
                                       proxy_auth=self.node.proxy_auth) as response:
                    data = await response.json()
                    if isinstance(data, list):
                        data = data[0]
                    return data.get('tag_name', '').strip('v')
        return None

    async def do_update(self, version: str):
        async with aiohttp.ClientSession() as session:
            # download the DLL
            async with session.get(
                    DKS_DLL_DOWNLOAD_URL.format(version=version),
                    raise_for_status=True,
                    proxy=self.node.proxy,
                    proxy_auth=self.node.proxy_auth
            ) as response:
                async with aiofiles.open(self.tacview_dll, 'wb') as f:
                    await f.write(await response.read())
            # download the rest (LUA)
            lua = os.path.join(self.server.instance.home, 'Scripts', 'Hooks', 'DKSWeatherSampler.lua')
            async with session.get(
                    DKS_LUA_DOWNLOAD_URL.format(version=version),
                    raise_for_status=True,
                    proxy=self.node.proxy,
                    proxy_auth=self.node.proxy_auth
            ) as response:
                async with aiofiles.open(lua, 'wb') as f:
                    await f.write(await response.read())
            # write version file
            version_file = os.path.join(self.server.instance.home, r'Mods\tech\Tacview\bin\dks.ver')
            with open(version_file, 'w') as f:
                f.writelines([version])

    @override
    async def render(self, param: dict | None = None) -> dict:
        ret = await super().render(param)
        if not self.enabled:
            value = "not enabled"
        else:
            value = f"[Link](https://www.digitalkneeboardsimulator.com/)"
            if self.tacview.locals.get('tacviewRealTimeTelemetryEnabled', True):
                value += "\nTacview RT feed enabled"
        return ret | {
            "value": value
        }
