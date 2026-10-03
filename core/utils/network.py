import aiohttp
import asyncio
import base64
import hashlib
import hmac
import ipaddress
import logging
import secrets
import socket
import time

from contextlib import closing, suppress
from core import Port, PortType
from typing import Iterable, TYPE_CHECKING

from .os import get_password, set_password

if TYPE_CHECKING:
    from core import Node

__all__ = [
    "hmac_hash",
    "hash_ip_addr",
    "is_open",
    "is_port_free",
    "get_public_ip",
    "is_upnp_available",
    "generate_firewall_rules",
    "wait_for_internet"
]

API_URLS = [
    'https://api4.ipify.org/',
    'https://ipinfo.io/ip',
    'https://www.trackip.net/ip',
    'https://api4.my-ip.io/v1/ip'  # they have an issue with their cert atm, hope they get it fixed
]

logger = logging.getLogger(__name__)


def get_hash_secret(config_dir='config') -> bytes:
    """
        Return the HMAC secret key as raw bytes.

        The key is stored in a base‑64 string under the name 'hash' inside
        ``config_dir``.  If the key is missing, corrupted, or otherwise
        undecodable, a new 32‑byte key is generated, stored, and returned.
    """
    try:
        secret = base64.b64decode(get_password('hash', config_dir).encode('ascii'))
    except (ValueError, TypeError):
        secret = secrets.token_bytes(32)
        secret_b64 = base64.b64encode(secret).decode('ascii')
        set_password("hash", secret_b64, config_dir)
    return secret


def hmac_hash(value: str) -> str:
    return hmac.new(get_hash_secret(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def get_network_prefix(ip_str: str, prefix_len: int | None = None) -> str:
    ip_obj = ipaddress.ip_address(ip_str)

    # Default prefix length if not supplied
    if prefix_len is None:
        prefix_len = 24 if isinstance(ip_obj, ipaddress.IPv4Address) else 48

    net = ipaddress.ip_network(f"{ip_str}/{prefix_len}", strict=False)
    return f"{net.network_address}/{prefix_len}"


def hash_ip_addr(ip_addr: str, prefix_len: int | None = None) -> str:
    return hmac_hash(get_network_prefix(ip_addr, prefix_len))


def is_open(ip: str, port: int, *, timeout: float = 1.0) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(timeout)
        try:
            s.connect((ip, int(port)))
            return True
        except (socket.timeout, OSError):
            return False


def _can_bind(ip: str, port: int, *, udp: bool) -> bool:
    kind = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    with closing(socket.socket(socket.AF_INET, kind)) as s:
        try:
            s.bind((ip, int(port)))
            return True
        except OSError:
            return False


def is_port_free(ip: str, port: int, *, udp: bool = False, timeout: float = 1.0) -> bool:
    """Can ``(ip, port)`` be bound by the process that is about to use it?

    The two protocols need different tests, because "is this port taken" cannot be answered by a
    bind on both platforms:

    * **UDP** - a flagless bind of the address. A holder that set ``SO_REUSEADDR`` (the service bus
      binds its listener that way) is still caught, measured on Linux *and* Windows, and UDP has no
      ``TIME_WAIT``. A wildcard target is also probed on the loopback, because Windows lets a
      wildcard bind coexist with a holder on a specific address while Linux does not.
    * **TCP** - a *connection*, not a bind. A bind answers wrongly either way: **with**
      ``SO_REUSEADDR`` it succeeds over a live listener on Windows (measured: ``BOUND`` against a
      listener, which is how this check first shipped and why it refused nothing), and **without**
      it a port left in ``TIME_WAIT`` by the server that just stopped fails on Linux, refusing every
      quick restart. A listener answers a connection on both platforms while a ``TIME_WAIT`` socket
      refuses it, so one call detects the conflict and tolerates the restart.

    The target address is always paired with the loopback, so a holder bound to either one is found.
    """
    wildcard = not ip or ip in ('0.0.0.0', '::')
    hosts = ['127.0.0.1'] if wildcard else [ip, '127.0.0.1']
    if udp:
        if wildcard:
            hosts.append('0.0.0.0')
        for host in dict.fromkeys(hosts):
            if not _can_bind(host, int(port), udp=True):
                return False
        return True
    for host in dict.fromkeys(hosts):
        if is_open(host, int(port), timeout=timeout):
            return False
    return True


async def get_public_ip():
    for url in API_URLS:
        with suppress(aiohttp.ClientError, ValueError):
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    return ipaddress.ip_address(await resp.text()).compressed
    raise TimeoutError("Public IP could not be retrieved.")


def is_upnp_available() -> bool:
    """
    Check if a UPnP Internet Gateway Device (IGD) is available on the network.

    Note: full UPnP port-mapping operations moved to ``core.utils.upnp``.
    This function is kept for backward compatibility.
    """
    from core.utils.upnp import is_available
    return is_available()


def fw_rule(port: int, protocol: str, name: str, description: str) -> str:
    """
    Returns a single New-NetFirewallRule command string.
    """
    cmd = (
        f'New-NetFirewallRule '
        f'-DisplayName "{name}" '
        f'-Direction Inbound '
        f'-Action Allow '
        f'-Protocol {protocol} '
        f'-LocalPort {port} '
        f'-Profile Any '
        f'-Description "{description}"'
    )
    return cmd


def generate_firewall_rules(ports: Iterable[Port]) -> str:
    """
    Write a PowerShell script that adds inbound rules for the given ports.
    """
    lines = [
        "# ------------------------------------------------------------",
        "# Auto‑generated PowerShell script to add inbound firewall rules",
        "# Run this script **as Administrator** in PowerShell.",
        "# ------------------------------------------------------------",
        "",
        "Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process -Force",
        ""
    ]

    for p in ports:
        if p.typ is PortType.BOTH:
            # Create two separate rules
            for proto in [PortType.TCP, PortType.UDP]:
                name = f"Allow {p.port}/{proto.value.lower()}"
                desc = f"Auto‑generated rule for inbound {p.port}/{proto.value.lower()}"
                lines.append(fw_rule(p.port, proto.value, name, desc))
                lines.append("")  # blank line for readability
        else:
            name = f"Allow {p.port}/{p.typ.value.lower()}"
            desc = f"Auto‑generated rule for inbound {p.port}/{p.typ.value.lower()}"
            lines.append(fw_rule(p.port, p.typ.value, name, desc))
            lines.append("")

    return "\n".join(lines)


async def _check_google_dns(
    host: str = "8.8.8.8",
    port: int = 53,
    per_attempt_timeout: float = 3.0,
) -> bool:
    """
    Try to open a TCP connection once with a per-attempt timeout.
    Returns True if successful, False otherwise.
    """
    try:
        conn_coro = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(conn_coro, timeout=per_attempt_timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except (asyncio.TimeoutError, OSError):
        return False


async def wait_for_internet(
    timeout: float,
    interval: float = 1.0,
    host: str = "8.8.8.8",
    port: int = 53,
    per_attempt_timeout: float = 3.0,
) -> bool:
    """
    Wait until an internet connection is available or until 'timeout' seconds pass.

    Returns:
        True  if a connection was established before timeout,
        False if timeout was reached without success.
    """
    deadline = time.monotonic() + timeout

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False

        # Don't let a single attempt take longer than remaining time
        attempt_timeout = min(per_attempt_timeout, remaining)

        if await _check_google_dns(host=host, port=port, per_attempt_timeout=attempt_timeout):
            return True

        # Sleep before the next attempt, but don't overshoot the deadline
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(interval, remaining))
