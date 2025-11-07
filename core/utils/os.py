from __future__ import annotations

import aiohttp
import asyncio
import ipaddress
import logging
import os
import pickle
import platform
import psutil
import socket
import stat
import subprocess
import sys

from contextlib import closing, suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from core import Node

if sys.platform == 'win32':
    import ctypes
    import pywintypes
    import win32api
    import win32console
    import winreg

    from pywinauto.win32defines import SEE_MASK_NOCLOSEPROCESS, SW_HIDE, SW_SHOWMINNOACTIVE

API_URLS = [
    'https://api4.ipify.org/',
    'https://ipinfo.io/ip',
    'https://www.trackip.net/ip',
    'https://api4.my-ip.io/ip'  # they have an issue with their cert atm, hope they get it fixed
]

__all__ = [
    "is_open",
    "get_public_ip",
    "find_process",
    "find_process_async",
    "is_process_running",
    "get_windows_version",
    "get_drive_space",
    "list_all_files",
    "make_unix_filename",
    "safe_rmtree",
    "is_junction",
    "terminate_process",
    "quick_edit_mode",
    "create_secret_dir",
    "set_password",
    "get_password",
    "delete_password",
    "sanitize_filename",
    "is_upnp_available",
    "get_win32_error_message",
    "CloudRotatingFileHandler",
    "run_elevated",
    "is_uac_enabled",
    "start_elevated"
]

logger = logging.getLogger(__name__)


def is_open(ip, port):
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(1.0)
        return s.connect_ex((ip, int(port))) == 0


async def get_public_ip(node: Node | None = None):
    for url in API_URLS:
        with suppress(aiohttp.ClientError, ValueError):
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=node.proxy if node else None,
                                       proxy_auth=node.proxy_auth if node else None) as resp:
                    return ipaddress.ip_address(await resp.text()).compressed
    else:
        raise TimeoutError("Public IP could not be retrieved.")


def find_process(proc: str, instance: str | None = None) -> Generator[psutil.Process, None, None]:
    proc_set = {name.casefold() for name in proc.split("|")}

    # Get all processes at once with their info
    processes = {p.pid: p for p in psutil.process_iter(['name', 'cmdline'])}

    # Filter by name first
    matching_processes = {pid: p for pid, p in processes.items()
                          if p.info['name'] and p.info['name'].casefold() in proc_set}

    # Then check instance if needed
    for p in matching_processes.values():
        try:
            if instance:
                cmdline = p.info['cmdline']
                if not cmdline:
                    continue
                if any(instance.casefold() in c.replace('\\', '/').casefold().split('/')
                       for c in cmdline):
                    yield p
            else:
                yield p
        except (psutil.AccessDenied, psutil.NoSuchProcess, IndexError):
            continue


async def find_process_async(proc: str, instance: str | None = None):
    def _find_first_match():
        return next(find_process(proc, instance), None)

    return await asyncio.to_thread(_find_first_match)


def is_process_running(process: subprocess.Popen | psutil.Process):
    if isinstance(process, subprocess.Popen):
        return process.poll() is None
    else:
        return process.is_running()


MS_LSB_MULTIPLIER = 65536


def get_windows_version(cmd: str) -> str | None:
    if sys.platform != 'win32':
        return None
    try:
        # noinspection PyUnresolvedReferences
        info = win32api.GetFileVersionInfo(os.path.expandvars(cmd), '\\')
        version = "%d.%d.%d.%d" % (info['FileVersionMS'] / MS_LSB_MULTIPLIER,
                                   info['FileVersionMS'] % MS_LSB_MULTIPLIER,
                                   info['FileVersionLS'] / MS_LSB_MULTIPLIER,
                                   info['FileVersionLS'] % MS_LSB_MULTIPLIER)
    except pywintypes.error:
        version = None
    return version


def get_drive_space(directory) -> tuple[int, int]:
    if platform.system() == 'Windows':
        free_bytes = ctypes.c_ulonglong(0)
        total_bytes = ctypes.c_ulonglong(0)

        # noinspection PyUnresolvedReferences
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(directory),
                                                   ctypes.pointer(free_bytes),
                                                   ctypes.pointer(total_bytes),
                                                   None)
        return total_bytes.value, free_bytes.value
    else:
        st = os.statvfs(directory)
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        return total, free


def list_all_files(path: str) -> list[str]:
    """
    Returns a list of all files in a given directory path, including files in subdirectories.

    :param path: The path of the directory to search for files.
    :return: A list of file paths relative to the given directory path.
    """
    # If we only have one file, return that
    if not os.path.isdir(path):
        return [os.path.basename(path)]
    file_paths = []
    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            full_path = os.path.join(dirpath, filename)
            relative_path = os.path.relpath(full_path, path)
            file_paths.append(relative_path)
    return file_paths


def make_unix_filename(*args) -> str:
    return '/'.join(arg.replace('\\', '/').strip('/') for arg in args)


def safe_rmtree(path: str | Path):
    # if path is a single file, delete that
    if os.path.isfile(path):
        os.chmod(path, stat.S_IWUSR)
        with suppress(FileNotFoundError):
            os.remove(path)
        return
    # otherwise delete the tree
    elif os.path.isdir(path):
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                filename = os.path.join(root, name)
                os.chmod(filename, stat.S_IWUSR)
                with suppress(FileNotFoundError):
                    os.remove(filename)
            for name in dirs:
                dirname = os.path.join(root, name)
                os.chmod(dirname, stat.S_IWUSR)
                with suppress(FileNotFoundError):
                    os.rmdir(dirname)
        os.chmod(path, stat.S_IWUSR)
        with suppress(FileNotFoundError):
            os.rmdir(path)


def is_junction(path):
    if not os.path.exists(path):
        return False
    if os.path.islink(path):
        return True
    # noinspection PyUnresolvedReferences
    attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
    if attrs == -1:
        raise ctypes.WinError()
    return bool(attrs & 0x0400)


def terminate_process(process: psutil.Process | None):
    if process is not None and process.is_running():
        process.terminate()
        try:
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def quick_edit_mode(turn_on=None) -> bool:
    """ Enable/Disable windows console Quick Edit Mode """
    if sys.platform != 'win32':
        return False

    ENABLE_QUICK_EDIT_MODE = 0x40
    ENABLE_EXTENDED_FLAGS = 0x80

    screen_buffer = win32console.GetStdHandle(-10)
    orig_mode = screen_buffer.GetConsoleMode()
    is_on = (orig_mode & ENABLE_QUICK_EDIT_MODE)
    if is_on != turn_on and turn_on is not None:
        if turn_on:
            new_mode = orig_mode | ENABLE_QUICK_EDIT_MODE
        else:
            new_mode = orig_mode & ~ENABLE_QUICK_EDIT_MODE
        screen_buffer.SetConsoleMode(new_mode | ENABLE_EXTENDED_FLAGS)

    return is_on if turn_on is None else turn_on


def create_secret_dir(config_dir='config'):
    path = os.path.join(config_dir, '.secret')
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
        if sys.platform == 'win32':
            import ctypes

            # noinspection PyUnresolvedReferences
            ctypes.windll.kernel32.SetFileAttributesW(path, 2)


def set_password(key: str, password: str, config_dir='config'):
    create_secret_dir()
    with open(os.path.join(config_dir, '.secret', f'{key}.pkl'), mode='wb') as f:
        pickle.dump(password, f)


def get_password(key: str, config_dir='config') -> str:
    try:
        with open(os.path.join(config_dir, '.secret', f'{key}.pkl'), mode='rb') as f:
            return str(pickle.load(f))
    except FileNotFoundError:
        raise ValueError(key)


def delete_password(key: str, config_dir='config'):
    try:
        os.remove(os.path.join(config_dir, '.secret', f'{key}.pkl'))
    except FileNotFoundError:
        raise ValueError(key)


def sanitize_filename(filename: str, base_directory: str) -> str:
    """
    Sanitizes an input filename to prevent relative path injection.
    Ensures the file path is within the `base_directory`.

    Args:
        filename (str): The input filename to sanitize.
        base_directory (str): The base directory where all downloads should be stored.

    Returns:
        str: A sanitized, safe file path.

    Raises:
        ValueError: If the filename contains invalid patterns or escapes the base directory.
    """
    # Ensure the base_directory is absolute
    base_directory = os.path.abspath(base_directory)

    # Resolve the filename into an absolute path
    resolved_path = os.path.abspath(os.path.join(base_directory, filename))

    # Ensure the resolved path is within the base directory
    if not os.path.commonpath([base_directory, resolved_path]) == base_directory:
        raise ValueError(f"Relative path injection attempt detected: {filename}")

    # Optional: Check file name for illegal characters (e.g., reject ../)
    if ".." in filename or filename.startswith("/"):
        raise ValueError(f"Invalid filename detected: {filename}")

    return resolved_path


def get_win32_error_message(error_code: int) -> str:
    # Load the system message corresponding to the error code
    if sys.platform != 'win32':
        return ""

    buffer = ctypes.create_unicode_buffer(512)
    # noinspection PyUnresolvedReferences
    ctypes.windll.kernel32.FormatMessageW(
        0x00001000,  # FORMAT_MESSAGE_FROM_SYSTEM
        None,
        error_code,
        0,  # Default language
        buffer,
        len(buffer),
        None
    )
    return buffer.value.strip()

if sys.version_info >= (3, 14):

    def is_upnp_available() -> bool:
        from upnpy import UPnP

        try:
            upnp = UPnP()
            devices = upnp.discover()
            if not devices:
                return False

            # Look for an InternetGatewayDevice and its WANIPConnection (or WANPPPConnection) service
            for device in devices:
                if "InternetGatewayDevice" in (device.device_type or ""):
                    try:
                        # Try WANIPConnection first
                        wan_services = device.get_services()
                        has_wan = any(
                            ("WANIPConnection" in s.service_type) or ("WANPPPConnection" in s.service_type)
                            for s in wan_services
                        )
                        if has_wan:
                            return True
                    except Exception:
                        continue
            return False
        except Exception:
            return False

else:

    def is_upnp_available() -> bool:
        import miniupnpc

        try:
            upnp = miniupnpc.UPnP()
            devices = upnp.discover()  # Discover UPnP-enabled devices
            if devices > 0:
                if upnp.selectigd():
                    # UPnP is enabled and an IGD was found.
                    return True
                else:
                    # UPnP is enabled, but no Internet Gateway Device (IGD) is selected
                    return False
            else:
                # No UPnP devices detected on the network.
                return False
        except Exception:
            # A UPnP device was found, but no IGD was found.
            return False


class CloudRotatingFileHandler(RotatingFileHandler):
    def shouldRollover(self, record):
        """
        Determine if rollover should occur by comparing the log file size to
        the size specified when the handler was created.
        """
        if self.maxBytes > 0:  # are we rolling over?
            log_file_size = os.path.getsize(self.baseFilename)
            if log_file_size >= self.maxBytes:
                return 1
        return 0


def run_elevated(exe_path, cwd, *args):
    """Start *exe_path* as Administrator and return the return code."""
    if sys.platform != 'win32':
        return -1

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("fMask", ctypes.c_ulong),
            ("hwnd", ctypes.c_void_p),
            ("lpVerb", ctypes.c_wchar_p),
            ("lpFile", ctypes.c_wchar_p),
            ("lpParameters", ctypes.c_wchar_p),
            ("lpDirectory", ctypes.c_wchar_p),
            ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.c_void_p),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", ctypes.c_wchar_p),
            ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", ctypes.c_ulong),
            ("hIcon", ctypes.c_void_p),
            ("hProcess", ctypes.c_void_p),
        ]

    sei = SHELLEXECUTEINFO()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask  = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = os.path.abspath(exe_path)
    sei.lpDirectory = os.path.abspath(cwd) if cwd else os.path.dirname(exe_path)
    sei.lpParameters = ' '.join(map(str, args))
    sei.nShow = SW_HIDE

    # noinspection PyUnresolvedReferences
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        raise ctypes.WinError()

    hproc = sei.hProcess
    # noinspection PyUnresolvedReferences
    ctypes.windll.kernel32.WaitForSingleObject(hproc, ctypes.c_ulong(-1))

    exit_code = ctypes.c_ulong()
    # noinspection PyUnresolvedReferences
    ctypes.windll.kernel32.GetExitCodeProcess(hproc, ctypes.byref(exit_code))

    return ctypes.c_int32(exit_code.value).value


def is_uac_enabled() -> bool:
    """Return True if UAC is enabled; False if it is disabled."""
    if sys.platform != 'win32':
        # non Win32 systems don't have a UAC, but need to tackle permissions differently
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\Microsoft\Windows\CurrentVersion\Policies\System",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            # check if UAC is enabled at all
            lua_enabled, _ = winreg.QueryValueEx(key, 'EnableLUA')
            if lua_enabled == 0:
                return False
            # now check if we get prompted (if not, treat UAC as disabled)
            admin_behaviour, _ = winreg.QueryValueEx(key, "ConsentPromptBehaviorAdmin")
            return admin_behaviour > 0          # > 0 => True, 0 = False
    except (FileNotFoundError, PermissionError):
        # if not found or permission is denied, fall back to a safe default.
        return True


def start_elevated(exe_path: str, cwd: str, *args) -> psutil.Process | None:
    """
    Start exe_path as Administrator and return a psutil.Process for the started process (Popen-like).
    Returns None on non-Windows platforms.

    Note: The returned handle refers to the primary process created by ShellExecuteExW.
    """
    if sys.platform != 'win32':
        return None

    sei = SHELLEXECUTEINFO()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = os.path.abspath(exe_path)
    sei.lpDirectory = os.path.abspath(cwd) if cwd else os.path.dirname(exe_path)
    sei.lpParameters = ' '.join(map(str, args))
    sei.nShow = SW_SHOWMINNOACTIVE

    # noinspection PyUnresolvedReferences
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        raise ctypes.WinError()

    hproc = sei.hProcess

    # Try to get PID from the handle to wrap in psutil.Process.
    # Kernel32 GetProcessId returns DWORD PID.
    GetProcessId = ctypes.windll.kernel32.GetProcessId  # type: ignore[attr-defined]
    GetProcessId.argtypes = [ctypes.c_void_p]
    GetProcessId.restype = ctypes.c_ulong
    pid = GetProcessId(hproc)

    if pid:
        try:
            return psutil.Process(pid)
        except psutil.NoSuchProcess:
            return None
    else:
        return None
