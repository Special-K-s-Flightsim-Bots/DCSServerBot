import aiohttp
import asyncio
import ipaddress
import logging
import miniupnpc
import os
import pickle
import platform
import psutil
import socket
import stat
import subprocess
import sys

if sys.platform == 'win32':
    import ctypes
    import pywintypes
    import win32api
    import win32console

from contextlib import closing, suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Union, TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from core import Node

API_URLS = [
    'https://api4.my-ip.io/ip',
    'https://api4.ipify.org/'
]

ENABLE_QUICK_EDIT_MODE = 0x40
ENABLE_EXTENDED_FLAGS = 0x80

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
    "CloudRotatingFileHandler"
]

logger = logging.getLogger(__name__)


def is_open(ip, port):
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(1.0)
        return s.connect_ex((ip, int(port))) == 0


async def get_public_ip(node: Optional["Node"] = None):
    for url in API_URLS:
        with suppress(aiohttp.ClientError, ValueError):
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=node.proxy if node else None,
                                       proxy_auth=node.proxy_auth if node else None) as resp:
                    return ipaddress.ip_address(await resp.text()).compressed
    else:
        raise TimeoutError("Public IP could not be retrieved.")


def find_process(proc: str, instance: Optional[str] = None) -> Generator[psutil.Process, None, None]:
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


async def find_process_async(proc: str, instance: Optional[str] = None):
    def _find_first_match():
        return next(find_process(proc, instance), None)

    return await asyncio.to_thread(_find_first_match)


def is_process_running(process: Union[subprocess.Popen, psutil.Process]):
    if isinstance(process, subprocess.Popen):
        return process.poll() is None
    else:
        return process.is_running()


MS_LSB_MULTIPLIER = 65536


def get_windows_version(cmd: str) -> Optional[str]:
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


def safe_rmtree(path: Union[str, Path]):
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
    attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
    if attrs == -1:
        raise ctypes.WinError()
    return bool(attrs & 0x0400)


def terminate_process(process: Optional[psutil.Process]):
    if process is not None and process.is_running():
        process.terminate()
        try:
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def quick_edit_mode(turn_on=None):
    """ Enable/Disable windows console Quick Edit Mode """
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
            ctypes.windll.kernel32.SetFileAttributesW(path, 2)


def set_password(key: str, password: str, config_dir='config'):
    create_secret_dir()
    with open(os.path.join(config_dir, '.secret', f'{key}.pkl'), mode='wb') as f:
        pickle.dump(password, f)


def get_password(key: str, config_dir='config') -> str:
    filename = os.path.join(config_dir, '.secret', f'{key}.pkl')
    try:
        with open(filename, mode='rb') as f:
            logger.debug(f'Loading password for {key} from {filename}.')
            return str(pickle.load(f))
    except FileNotFoundError:
        logger.debug(f'Password for {key} not found in {filename}.')
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


def get_win32_error_message(error_code: int):
    # Load the system message corresponding to the error code
    buffer = ctypes.create_unicode_buffer(512)
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


def is_upnp_available() -> bool:
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
        # An UPnP device was found, but no IGD was found.
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
