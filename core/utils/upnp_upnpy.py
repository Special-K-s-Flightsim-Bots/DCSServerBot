"""
UPnP backend using the upnpy library (Python >= 3.14).

upnpy is async-capable and provides structured device/service objects.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class _UPnpyBackend:
    """Backend that wraps the upnpy library (Python >= 3.14)."""

    def __init__(self):
        from upnpy import UPnP
        self._upnp = UPnP()
        self._wan_service = None
        self._discovered = False
        self._available = False

    def _discover_and_select(self) -> bool:
        """Discover devices and find a WANIPConnection service. Idempotent."""
        if self._discovered:
            return self._available
        self._discovered = True

        try:
            devices = self._upnp.discover()
        except Exception as ex:
            logger.debug("upnpy discover failed: %s", ex)
            return False

        for device in devices:
            if "InternetGatewayDevice" not in (device.device_type or ""):
                continue
            try:
                services = device.get_services()
            except Exception:
                continue
            for svc in services:
                if ("WANIPConnection" in svc.service_type or
                        "WANPPPConnection" in svc.service_type):
                    self._wan_service = svc
                    self._available = True
                    return True
        return False

    def is_available(self) -> bool:
        return self._discover_and_select()

    def get_external_ip(self) -> Optional[str]:
        from upnpy.exceptions import UPnPError
        if not self._discover_and_select():
            return None
        try:
            # WANIPConnection GetExternalIPAddress action
            result = self._wan_service.call_action(
                "GetExternalIPAddress", NewExternalIPAddress=""
            )
            return result.get("NewExternalIPAddress")
        except UPnPError as ex:
            logger.debug("GetExternalIPAddress failed: %s", ex)
            return None
        except Exception as ex:
            logger.debug("GetExternalIPAddress unexpected error: %s", ex)
            return None

    def add_port_mapping(self, external_port: int, protocol: str,
                         internal_port: int, description: str,
                         lease_duration: int) -> bool:
        from upnpy.exceptions import UPnPError
        if not self._discover_and_select() or self._wan_service is None:
            return False
        try:
            self._wan_service.call_action(
                "AddPortMapping",
                NewRemoteHost="",
                NewExternalPort=external_port,
                NewProtocol=protocol,
                NewInternalPort=internal_port,
                NewInternalClient=self._get_internal_client(),
                NewEnabled=1,
                NewPortMappingDescription=description,
                NewLeaseDuration=lease_duration,
            )
            logger.info("UPnP port mapping added: %d/%s -> %d (%s)",
                        external_port, protocol, internal_port, description)
            return True
        except UPnPError as ex:
            logger.warning("AddPortMapping failed: %s", ex)
            return False
        except Exception as ex:
            logger.warning("AddPortMapping unexpected error: %s", ex)
            return False

    def remove_port_mapping(self, external_port: int, protocol: str) -> bool:
        from upnpy.exceptions import UPnPError
        if not self._discover_and_select() or self._wan_service is None:
            return False
        try:
            self._wan_service.call_action(
                "DeletePortMapping",
                NewRemoteHost="",
                NewExternalPort=external_port,
                NewProtocol=protocol,
            )
            logger.info("UPnP port mapping removed: %d/%s", external_port, protocol)
            return True
        except UPnPError as ex:
            logger.warning("DeletePortMapping failed: %s", ex)
            return False
        except Exception as ex:
            logger.warning("DeletePortMapping unexpected error: %s", ex)
            return False

    def list_port_mappings(self) -> list[dict]:
        if not self._discover_and_select() or self._wan_service is None:
            return []
        mappings = []
        try:
            # upnpy exposes get_port_mappings() on the WAN service
            raw = self._wan_service.get_port_mappings()
            for entry in raw:
                mappings.append({
                    "external_port": int(entry.get("NewExternalPort", 0)),
                    "protocol": entry.get("NewProtocol", "").upper(),
                    "internal_port": int(entry.get("NewInternalPort", 0)),
                    "internal_client": entry.get("NewInternalClient", ""),
                    "description": entry.get("NewPortMappingDescription", ""),
                    "lease_duration": int(entry.get("NewLeaseDuration", 0)),
                    "enabled": bool(entry.get("NewEnabled", 1)),
                })
        except Exception as ex:
            logger.debug("list_port_mappings failed: %s", ex)
        return mappings

    def has_port_mapping(self, external_port: int, protocol: str) -> bool:
        if not self._discover_and_select() or self._wan_service is None:
            return False
        try:
            raw = self._wan_service.get_port_mappings()
            for entry in raw:
                if (int(entry.get("NewExternalPort", 0)) == external_port and
                        entry.get("NewProtocol", "").upper() == protocol.upper()):
                    return True
            return False
        except Exception as ex:
            logger.debug("has_port_mapping failed: %s", ex)
            return False

    def _get_internal_client(self) -> str:
        """Get the local IP address of this machine on the LAN."""
        import socket
        try:
            # Connect to router's LAN IP to determine local interface IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # Doesn't actually connect, just determines route
                s.connect(("192.168.1.1", 80))
                return s.getsockname()[0]
            finally:
                s.close()
        except Exception:
            return ""
