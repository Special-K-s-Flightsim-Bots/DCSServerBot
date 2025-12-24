import aiohttp
import asyncio
import ipaddress
import socket
import sys
import time

from contextlib import closing, suppress
from core import Port, PortType
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Node

__all__ = [
    "is_open",
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


def is_open(ip, port):
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(1.0)
        return s.connect_ex((ip, int(port))) == 0


async def get_public_ip(node: "Node | None" = None):
    for url in API_URLS:
        with suppress(aiohttp.ClientError, ValueError):
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=node.proxy if node else None,
                                       proxy_auth=node.proxy_auth if node else None) as resp:
                    return ipaddress.ip_address(await resp.text()).compressed
    raise TimeoutError("Public IP could not be retrieved.")


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
