"""
Unified UPnP port-forwarding interface.

Works with two different libraries depending on Python version:
- Python >= 3.14: upnpy (async-capable)
- Python < 3.14: miniupnpc (synchronous)

Usage:
    from core.utils.upnp import is_available, add_port_mapping, remove_port_mapping, get_external_ip

    if is_available():
        add_port_mapping(1308, 'udp', 1308, 'DCS World Server')
        external_ip = get_external_ip()
"""

import logging
import sys
from abc import ABC, abstractmethod
from typing import Optional

from core import Port

__all__ = [
    "is_available",
    "get_external_ip",
    "add_port_mapping",
    "remove_port_mapping",
    "list_port_mappings",
    "has_port_mapping",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract backend interface
# ---------------------------------------------------------------------------

class _UPnPBackend(ABC):
    """Abstract base for UPnP backends (upnpy or miniupnpc)."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if a UPnP IGD is available on the network."""

    @abstractmethod
    def get_external_ip(self) -> Optional[str]:
        """Get the external/public IP from the UPnP IGD."""

    @abstractmethod
    def add_port_mapping(self, external_port: int, protocol: str,
                         internal_port: int, description: str,
                         lease_duration: int) -> bool:
        """Add a port mapping."""

    @abstractmethod
    def remove_port_mapping(self, external_port: int, protocol: str) -> bool:
        """Remove a port mapping."""

    @abstractmethod
    def list_port_mappings(self) -> list[dict]:
        """List all current port mappings."""

    @abstractmethod
    def has_port_mapping(self, external_port: int, protocol: str) -> bool:
        """Check if a specific port mapping exists."""


# ---------------------------------------------------------------------------
# Backend singleton
# ---------------------------------------------------------------------------

_backend: Optional[_UPnPBackend] = None


def _get_backend() -> Optional[_UPnPBackend]:
    """Get or create the singleton UPnP backend (auto-detects library)."""
    global _backend
    if _backend is None:
        if sys.version_info >= (3, 14):
            try:
                from core.utils.upnp_upnpy import _UPnpyBackend
                _backend = _UPnpyBackend()
            except ImportError:
                logger.warning("upnpy not installed, UPnP unavailable")
        else:
            try:
                from core.utils.upnp_miniupnpc import _MiniUPnPBackend
                _backend = _MiniUPnPBackend()
            except ImportError:
                logger.warning("miniupnpc not installed, UPnP unavailable")
    return _backend


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """
    Check if a UPnP Internet Gateway Device (IGD) is available on the network.

    This performs a discovery which may take a few seconds. The result is
    cached — subsequent calls return immediately.
    """
    backend = _get_backend()
    if backend is None:
        return False
    return backend.is_available()


def get_external_ip() -> Optional[str]:
    """
    Get the external/public IP address from the UPnP IGD.

    Returns None if UPnP is unavailable or the query failed.
    """
    backend = _get_backend()
    if backend is None:
        return None
    try:
        return backend.get_external_ip()
    except Exception as ex:
        logger.debug("get_external_ip failed: %s", ex)
        return None


def add_port_mapping(port: int, protocol: str,
                    internal_port: int | None = None,
                    description: str = "DCSServerBot",
                    lease_duration: int = 0) -> bool:
    """
    Add a UPnP port mapping on the router.

    Args:
        port: external port number to open.
        protocol: 'TCP' or 'UDP' (case-insensitive).
        internal_port: internal port to forward to. Defaults to the same as
                       ``port`` if not specified.
        description: human-readable label for the mapping.
        lease_duration: lease time in seconds. 0 means permanent/infinite.

    Returns True if the mapping was added successfully, False otherwise
    (e.g. no IGD found, mapping already exists, or library error).
    """
    backend = _get_backend()
    if backend is None:
        logger.warning("UPnP backend not available, cannot add port mapping")
        return False

    protocol = protocol.upper()
    if protocol not in ("TCP", "UDP"):
        logger.error("Invalid protocol: %s (must be TCP or UDP)", protocol)
        return False

    if internal_port is None:
        internal_port = port

    # Check for existing mapping first
    if has_port_mapping(port, protocol):
        logger.info("Port mapping %d/%s already exists, skipping", port, protocol)
        return True

    try:
        return backend.add_port_mapping(
            port, protocol, internal_port, description, lease_duration
        )
    except Exception as ex:
        logger.error("add_port_mapping(%d/%s) failed: %s", port, protocol, ex)
        return False


def remove_port_mapping(port: int, protocol: str) -> bool:
    """
    Remove a UPnP port mapping from the router.

    Args:
        port: external port number.
        protocol: 'TCP' or 'UDP' (case-insensitive).

    Returns True if the mapping was removed successfully, False otherwise.
    """
    backend = _get_backend()
    if backend is None:
        logger.warning("UPnP backend not available, cannot remove port mapping")
        return False

    protocol = protocol.upper()
    if protocol not in ("TCP", "UDP"):
        logger.error("Invalid protocol: %s (must be TCP or UDP)", protocol)
        return False

    try:
        return backend.remove_port_mapping(port, protocol)
    except Exception as ex:
        logger.error("remove_port_mapping(%d/%s) failed: %s", port, protocol, ex)
        return False


def list_port_mappings() -> list[dict]:
    """
    List all current UPnP port mappings on the router.

    Returns a list of dicts with keys:
        external_port, protocol, internal_port, internal_client,
        description, lease_duration, enabled

    Returns an empty list if UPnP is unavailable or query failed.
    """
    backend = _get_backend()
    if backend is None:
        return []
    try:
        return backend.list_port_mappings()
    except Exception as ex:
        logger.debug("list_port_mappings failed: %s", ex)
        return []


def has_port_mapping(port: int, protocol: str) -> bool:
    """
    Check if a specific port mapping already exists.

    Args:
        port: external port number.
        protocol: 'TCP' or 'UDP' (case-insensitive).

    Returns True if the mapping exists, False otherwise.
    """
    backend = _get_backend()
    if backend is None:
        return False
    try:
        return backend.has_port_mapping(port, protocol.upper())
    except Exception as ex:
        logger.debug("has_port_mapping(%d/%s) failed: %s", port, protocol, ex)
        return False


# ---------------------------------------------------------------------------
# Convenience wrappers for Port objects
# ---------------------------------------------------------------------------

def add_port_mapping_from_port(port_obj: Port,
                               description: str = "DCSServerBot",
                               lease_duration: int = 0) -> bool:
    """
    Add UPnP port mapping(s) for a Port object.

    If the Port type is BOTH, two mappings are added (TCP and UDP).
    Returns True if all mappings were added successfully.
    """
    from core import PortType
    if port_obj.typ is PortType.BOTH:
        ok_tcp = add_port_mapping(port_obj.port, 'tcp', port_obj.port,
                                  f"{description} TCP", lease_duration)
        ok_udp = add_port_mapping(port_obj.port, 'udp', port_obj.port,
                                  f"{description} UDP", lease_duration)
        return ok_tcp and ok_udp
    else:
        return add_port_mapping(port_obj.port, port_obj.typ.value, port_obj.port,
                                description, lease_duration)


def remove_port_mapping_from_port(port_obj: Port) -> bool:
    """
    Remove UPnP port mapping(s) for a Port object.

    If the Port type is BOTH, both TCP and UDP mappings are removed.
    Returns True if all removals succeeded.
    """
    from core import PortType
    if port_obj.typ is PortType.BOTH:
        ok_tcp = remove_port_mapping(port_obj.port, 'tcp')
        ok_udp = remove_port_mapping(port_obj.port, 'udp')
        return ok_tcp and ok_udp
    else:
        return remove_port_mapping(port_obj.port, port_obj.typ.value)
