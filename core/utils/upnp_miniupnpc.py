"""
UPnP backend using the miniupnpc library (Python < 3.14).

miniupnpc provides a simple synchronous API with a UPnP class.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class _MiniUPnPBackend:
    """Backend that wraps the miniupnpc library (Python < 3.14)."""

    def __init__(self):
        import miniupnpc
        self._upnp = miniupnpc.UPnP()
        self._selected = False

    def _select(self) -> bool:
        """Discover and select an IGD. Idempotent."""
        if self._selected:
            return True
        try:
            devices = self._upnp.discover()
            if devices <= 0:
                logger.debug("miniupnpc: no UPnP devices found")
                return False
            if not self._upnp.selectigd():
                logger.debug("miniupnpc: no IGD selected")
                return False
            self._selected = True
            return True
        except Exception as ex:
            logger.debug("miniupnpc discover/select failed: %s", ex)
            return False

    def is_available(self) -> bool:
        return self._select()

    def get_external_ip(self) -> Optional[str]:
        if not self._select():
            return None
        try:
            return self._upnp.externalipaddress()
        except Exception as ex:
            logger.debug("externalipaddress failed: %s", ex)
            return None

    def add_port_mapping(self, external_port: int, protocol: str,
                         internal_port: int, description: str,
                         lease_duration: int) -> bool:
        if not self._select():
            return False
        try:
            internal_client = self._get_internal_client()
            self._upnp.addportmapping(
                external_port,
                protocol,
                internal_port,
                internal_client,
                description,
                lease_duration,
            )
            logger.info("UPnP port mapping added: %d/%s -> %d (%s)",
                        external_port, protocol, internal_port, description)
            return True
        except Exception as ex:
            logger.warning("addportmapping failed: %s", ex)
            return False

    def remove_port_mapping(self, external_port: int, protocol: str) -> bool:
        if not self._select():
            return False
        try:
            self._upnp.deleteportmapping(external_port, protocol)
            logger.info("UPnP port mapping removed: %d/%s", external_port, protocol)
            return True
        except Exception as ex:
            logger.warning("deleteportmapping failed: %s", ex)
            return False

    def list_port_mappings(self) -> list[dict]:
        if not self._select():
            return []
        mappings = []
        index = 0
        try:
            while True:
                mapping = self._upnp.getgenericportmapping(index)
                if mapping is None:
                    break
                # miniupnpc returns tuple:
                # (external_port, protocol, internal_port, internal_client,
                #  description, lease_duration, enabled)
                ext_port, proto, int_port, int_client, desc, lease, enabled = mapping
                mappings.append({
                    "external_port": int(ext_port),
                    "protocol": proto.upper() if proto else "",
                    "internal_port": int(int_port) if int_port else 0,
                    "internal_client": int_client or "",
                    "description": desc or "",
                    "lease_duration": int(lease) if lease else 0,
                    "enabled": bool(enabled),
                })
                index += 1
        except Exception as ex:
            logger.debug("list_port_mappings stopped at index %d: %s", index, ex)
        return mappings

    def has_port_mapping(self, external_port: int, protocol: str) -> bool:
        if not self._select():
            return False
        try:
            mapping = self._upnp.getspecificportmapping(external_port, protocol)
            return mapping is not None
        except Exception as ex:
            logger.debug("has_port_mapping failed: %s", ex)
            return False

    def _get_internal_client(self) -> str:
        """Get the local IP address of this machine on the LAN."""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("192.168.1.1", 80))
                return s.getsockname()[0]
            finally:
                s.close()
        except Exception:
            return ""
