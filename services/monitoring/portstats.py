"""
Per-port network traffic statistics collector.

Windows: uses GetPerTcpConnectionEStats / GetPerUdpConnectionEStats via ctypes (iphlpapi.dll).
Linux:   parses /proc/net/snmp and /proc/net/netstat for per-port byte/packet counts.

Both platforms also use psutil.net_connections() to enumerate sockets and count
unique remote IPs per local port.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import psutil
import sys

from dataclasses import dataclass, field

if sys.platform == 'win32':
    import socket

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PortStats:
    """Traffic statistics for a single port/protocol."""
    port: int
    protocol: str  # 'tcp' or 'udp'
    bytes_in: int = 0
    bytes_out: int = 0
    packets_in: int = 0
    packets_out: int = 0
    unique_ips: int = 0
    tcp_conns: int = 0
    udp_conns: int = 0
    # UDP-specific: unique source IPs seen sending to this port that are NOT
    # in the TCP player set (populated by scapy sniffer, not psutil)
    non_player_udp_ips: int = 0

    @property
    def connections(self) -> int:
        return self.tcp_conns + self.udp_conns


@dataclass
class PerPortSnapshot:
    """Collected stats for all monitored ports at a single point in time."""
    stats: dict[tuple[int, str], PortStats] = field(default_factory=dict)

    def get(self, port: int, protocol: str) -> PortStats:
        key = (port, protocol)
        if key not in self.stats:
            self.stats[key] = PortStats(port=port, protocol=protocol)
        return self.stats[key]


# ---------------------------------------------------------------------------
# Windows: iphlpapi per-connection stats
# ---------------------------------------------------------------------------

if sys.platform == 'win32':

    # --- constants ---
    AF_INET = 2
    AF_INET6 = 23
    TCP_TABLE_OWNER_PID_ALL = 5
    UDP_TABLE_OWNER_PID = 1

    # TCP connection state (MIB_TCP_STATE)
    MIB_TCP_STATE_CLOSED = 1
    MIB_TCP_STATE_LISTEN = 2
    MIB_TCP_STATE_SYN_SENT = 3
    MIB_TCP_STATE_SYN_RCVD = 4
    MIB_TCP_STATE_ESTAB = 5
    MIB_TCP_STATE_FIN_WAIT1 = 6
    MIB_TCP_STATE_FIN_WAIT2 = 7
    MIB_TCP_STATE_CLOSE_WAIT = 8
    MIB_TCP_STATE_CLOSING = 9
    MIB_TCP_STATE_LAST_ACK = 10
    MIB_TCP_STATE_TIME_WAIT = 11
    MIB_TCP_STATE_DELETE_TCB = 12

    # --- structs ---

    class MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", ctypes.wintypes.DWORD),
            ("dwLocalAddr", ctypes.wintypes.DWORD),
            ("dwLocalPort", ctypes.wintypes.DWORD),
            ("dwRemoteAddr", ctypes.wintypes.DWORD),
            ("dwRemotePort", ctypes.wintypes.DWORD),
            ("dwOwningPid", ctypes.wintypes.DWORD),
        ]

    class MIB_TCPTABLE_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwNumEntries", ctypes.wintypes.DWORD),
            ("table", MIB_TCPROW_OWNER_PID * 1),  # variable-length
        ]

    class MIB_UDPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwLocalAddr", ctypes.wintypes.DWORD),
            ("dwLocalPort", ctypes.wintypes.DWORD),
            ("dwOwningPid", ctypes.wintypes.DWORD),
        ]

    class MIB_UDPTABLE_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwNumEntries", ctypes.wintypes.DWORD),
            ("table", MIB_UDPROW_OWNER_PID * 1),
        ]

    # --- Estats structures for per-connection byte counters ---

    class TCP_ESTATS_ROD_v0(ctypes.Structure):
        _fields_ = [
            ("CurRto", ctypes.c_ulong),
            ("MaxRto", ctypes.c_ulong),
            ("Mss", ctypes.c_ulong),
            ("CountRetransmissions", ctypes.c_ulong),
            ("StartRtt", ctypes.c_ulong),
            ("MeanRtt", ctypes.c_ulong),
            ("MinRtt", ctypes.c_ulong),
            ("SumRtt", ctypes.c_ulong),
            ("SqD", ctypes.c_ulong),
            ("SynRetrans", ctypes.c_ulong),
            ("FastRetrans", ctypes.c_ulong),
            ("DupAcksOut", ctypes.c_ulong),
            ("BytesOut", ctypes.c_ulonglong),
            ("SegsOut", ctypes.c_ulong),
            ("BytesIn", ctypes.c_ulonglong),
            ("SegsIn", ctypes.c_ulong),
            ("SndLimTransRwin", ctypes.c_ulong),
            ("SndLimTimeRwin", ctypes.c_ulong),
            ("SndLimBytesRwin", ctypes.c_ulonglong),
            ("SndLimTransCwnd", ctypes.c_ulong),
            ("SndLimTimeCwnd", ctypes.c_ulong),
            ("SndLimBytesCwnd", ctypes.c_ulonglong),
            ("SndLimTransSnd", ctypes.c_ulong),
            ("SndLimTimeSnd", ctypes.c_ulong),
            ("SndLimBytesSnd", ctypes.c_ulonglong),
            ("SndLimTransRop", ctypes.c_ulong),
            ("SndLimTimeRop", ctypes.c_ulong),
            ("SndLimBytesRop", ctypes.c_ulonglong),
            ("Cwnd", ctypes.c_ulong),
            ("SndWnd", ctypes.c_ulong),
            ("RcvWnd", ctypes.c_ulong),
            ("Rtt", ctypes.c_ulong),
            ("BytesRetrans", ctypes.c_ulong),
            ("DupAckIn", ctypes.c_ulong),
            ("SacksRcvd", ctypes.c_ulong),
            ("SackBlocksRcvd", ctypes.c_ulong),
            ("CongSignals", ctypes.c_ulong),
            ("PreCongSumCwnd", ctypes.c_ulong),
            ("PreCongSumRtt", ctypes.c_ulong),
            ("PostCongSumRtt", ctypes.c_ulong),
            ("PostCongCountRtt", ctypes.c_ulong),
            ("EcnSignals", ctypes.c_ulong),
            ("Ect0", ctypes.c_ulong),
            ("Ect1", ctypes.c_ulong),
            ("CurReasmQueue", ctypes.c_ulong),
            ("CurRwinSent", ctypes.c_ulong),
            ("MaxRwinSent", ctypes.c_ulong),
            ("MinRwinSent", ctypes.c_ulong),
            ("LimRwin", ctypes.c_ulong),
            ("DupEpRsts", ctypes.c_ulong),
            ("SndScale", ctypes.c_ulong),
            ("RcvScale", ctypes.c_ulong),
        ]

    class TCP_ESTATS_SND_CONG_ROD_v0(ctypes.Structure):
        _fields_ = [
            ("SndLimTransRwin", ctypes.c_ulong),
            ("SndLimTimeRwin", ctypes.c_ulong),
            ("SndLimBytesRwin", ctypes.c_ulonglong),
            ("SndLimTransCwnd", ctypes.c_ulong),
            ("SndLimTimeCwnd", ctypes.c_ulong),
            ("SndLimBytesCwnd", ctypes.c_ulonglong),
            ("SndLimTransSnd", ctypes.c_ulong),
            ("SndLimTimeSnd", ctypes.c_ulong),
            ("SndLimBytesSnd", ctypes.c_ulonglong),
            ("SndLimTransRop", ctypes.c_ulong),
            ("SndLimTimeRop", ctypes.c_ulong),
            ("SndLimBytesRop", ctypes.c_ulonglong),
            ("Cwnd", ctypes.c_ulong),
            ("SndWnd", ctypes.c_ulong),
            ("RcvWnd", ctypes.c_ulong),
            ("Rtt", ctypes.c_ulong),
            ("BytesRetrans", ctypes.c_ulong),
            ("DupAckIn", ctypes.c_ulong),
            ("SacksRcvd", ctypes.c_ulong),
            ("SackBlocksRcvd", ctypes.c_ulong),
            ("CongSignals", ctypes.c_ulong),
            ("PreCongSumCwnd", ctypes.c_ulong),
            ("PreCongSumRtt", ctypes.c_ulong),
            ("PostCongSumRtt", ctypes.c_ulong),
            ("PostCongCountRtt", ctypes.c_ulong),
            ("EcnSignals", ctypes.c_ulong),
            ("Ect0", ctypes.c_ulong),
            ("Ect1", ctypes.c_ulong),
        ]

    TcpConnectionEstatsSynRecvs = 13  # enum value from estats_types.h

    # --- helper: get per-connection TCP byte/packet counters via Estats ---

    def _get_tcp_byte_counters(target_ports: set[int]) -> dict[int, dict[str, int]]:
        """
        Call GetPerTcpConnectionEStats for each TCP connection to get
        byte and packet counters, summed per local port.

        Returns:
            dict mapping port -> {bytes_in, bytes_out, packets_in, packets_out}
        """
        result: dict[int, dict[str, int]] = {}
        iphlpapi = ctypes.windll.iphlpapi

        # Get the extended table with PID info
        size = ctypes.wintypes.DWORD(0)
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
        if size.value == 0:
            return result
        buf = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
        if ret != 0:
            return result
        table = ctypes.cast(buf, ctypes.POINTER(MIB_TCPTABLE_OWNER_PID)).contents

        estats_buf = ctypes.create_string_buffer(ctypes.sizeof(TCP_ESTATS_ROD_v0))
        buf_size = ctypes.c_ulong(ctypes.sizeof(TCP_ESTATS_ROD_v0))
        estats_warned = False
        for i in range(table.dwNumEntries):
            row = table.table[i]
            local_port = _ntohs(row.dwLocalPort)
            if local_port not in target_ports:
                continue

            try:
                ret = iphlpapi.GetPerTcpConnectionEStats(
                    ctypes.byref(row),  # pRow (PMIB_TCPROW_OWNER_PID)
                    0,                 # EstatsType: TcpConnectionEstatsRod = 0
                    estats_buf,        # pData
                    buf_size,          # RwSize
                    0,                 # Version (0 = v0)
                    0,                 # Size (0 = auto)
                    0,                 # ConnectionIndex (0 = first)
                )
                if ret != 0:
                    if not estats_warned:
                        log.info("To get detailled port statistics like bytes_in/bytes_out/packets_in/packets_out"
                                 "you need to run DCSServerBot as Administrator.\n"
                                 "While this is not recommended, it might be beneficial in certain situations.")
                        estats_warned = True
                    continue
                rod = ctypes.cast(estats_buf, ctypes.POINTER(TCP_ESTATS_ROD_v0)).contents
                if local_port not in result:
                    result[local_port] = {'bytes_in': 0, 'bytes_out': 0, 'packets_in': 0, 'packets_out': 0}
                result[local_port]['bytes_in'] += rod.BytesIn
                result[local_port]['bytes_out'] += rod.BytesOut
                result[local_port]['packets_in'] += rod.SegsIn
                result[local_port]['packets_out'] += rod.SegsOut
            except Exception:
                pass  # skip connections we can't query

        return result

    # --- helper: get extended TCP table ---

    def _get_tcp_table() -> list[MIB_TCPROW_OWNER_PID]:
        """Return all TCP connections with PID info."""
        iphlpapi = ctypes.windll.iphlpapi
        size = ctypes.wintypes.DWORD(0)
        # First call to get size
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
        buf = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
        if ret != 0:
            log.debug("GetExtendedTcpTable failed: %d", ret)
            return []
        table = ctypes.cast(buf, ctypes.POINTER(MIB_TCPTABLE_OWNER_PID)).contents
        rows = []
        for i in range(table.dwNumEntries):
            rows.append(table.table[i])
        return rows

    def _get_udp_table() -> list[MIB_UDPROW_OWNER_PID]:
        """Return all UDP endpoints with PID info."""
        iphlpapi = ctypes.windll.iphlpapi
        size = ctypes.wintypes.DWORD(0)
        iphlpapi.GetExtendedUdpTable(None, ctypes.byref(size), False, AF_INET, UDP_TABLE_OWNER_PID, 0)
        buf = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetExtendedUdpTable(buf, ctypes.byref(size), False, AF_INET, UDP_TABLE_OWNER_PID, 0)
        if ret != 0:
            log.debug("GetExtendedUdpTable failed: %d", ret)
            return []
        table = ctypes.cast(buf, ctypes.POINTER(MIB_UDPTABLE_OWNER_PID)).contents
        rows = []
        for i in range(table.dwNumEntries):
            rows.append(table.table[i])
        return rows

    def _ntohs(port: int) -> int:
        """Convert port from network byte order to host byte order."""
        return socket.ntohs(port)

    def _collect_windows_port_stats(target_ports: set[int]) -> PerPortSnapshot:
        """Collect per-port stats on Windows using iphlpapi and psutil."""
        snapshot = PerPortSnapshot()

        # Track unique connections as (remote_addr, remote_port) tuples per (local_port, proto)
        # to avoid double-counting the two directions of a single TCP connection.
        tcp_conn_set: dict[int, set[tuple[int, int]]] = {port: set() for port in target_ports}

        # --- TCP via kernel API ---
        try:
            tcp_rows = _get_tcp_table()
            for row in tcp_rows:
                local_port = _ntohs(row.dwLocalPort)
                if local_port not in target_ports:
                    continue
                remote_addr = row.dwRemoteAddr
                remote_port = _ntohs(row.dwRemotePort)
                if remote_addr != 0:
                    tcp_conn_set[local_port].add((remote_addr, remote_port))
        except Exception as ex:
            log.debug("Windows TCP port stats collection failed: %s", ex)

        # Convert unique connection counts to snapshot
        for port, conns in tcp_conn_set.items():
            ps = snapshot.get(port, 'tcp')
            ps.tcp_conns = len(conns)

        # --- TCP byte/packet counters via Estats ---
        try:
            tcp_bytes = _get_tcp_byte_counters(target_ports)
            for port, counters in tcp_bytes.items():
                ps = snapshot.get(port, 'tcp')
                ps.bytes_in = counters['bytes_in']
                ps.bytes_out = counters['bytes_out']
                ps.packets_in = counters['packets_in']
                ps.packets_out = counters['packets_out']
        except Exception as ex:
            log.debug("Windows TCP byte counter collection failed: %s", ex)

        # --- UDP via kernel API ---
        try:
            udp_rows = _get_udp_table()
            for row in udp_rows:
                local_port = _ntohs(row.dwLocalPort)
                if local_port not in target_ports:
                    continue
                ps = snapshot.get(local_port, 'udp')
                ps.udp_conns += 1
        except Exception as ex:
            log.debug("Windows UDP port stats collection failed: %s", ex)

        # --- Unique IP counting via psutil (deduplicated) ---
        unique_ip_map: dict[tuple[int, str], set[str]] = {}
        try:
            for conn in psutil.net_connections(kind='inet4'):
                if not conn.laddr:
                    continue
                local_port = conn.laddr.port
                if local_port not in target_ports:
                    continue
                proto = 'tcp' if conn.type == 1 else 'udp' if conn.type == 2 else None
                if proto is None or not conn.raddr:
                    continue
                key = (local_port, proto)
                if key not in unique_ip_map:
                    unique_ip_map[key] = set()
                unique_ip_map[key].add(conn.raddr.ip)
        except (psutil.AccessDenied, PermissionError):
            log.debug("psutil.net_connections() access denied for port stats")

        for (port, proto), ips in unique_ip_map.items():
            ps = snapshot.get(port, proto)
            ps.unique_ips = len(ips)

        return snapshot


# ---------------------------------------------------------------------------
# Linux: /proc/net parsing
# ---------------------------------------------------------------------------

def _parse_proc_net_sockets(filepath: str, target_ports: set[int], protocol: str,
                             snapshot: PerPortSnapshot) -> None:
    """Parse /proc/net/tcp or /proc/net/udp and update the snapshot."""
    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return

    # Skip header line
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        local_address = parts[1]
        # Format: local_address:port (hex)
        if ':' not in local_address:
            continue
        _, port_hex = local_address.rsplit(':', 1)
        try:
            local_port = int(port_hex, 16)
        except ValueError:
            continue
        if local_port not in target_ports:
            continue

        ps = snapshot.get(local_port, protocol)
        if protocol == 'tcp':
            ps.tcp_conns += 1
        else:
            ps.udp_conns += 1

        remote_address = parts[2]
        if remote_address != '00000000:0000':
            ps.unique_ips += 1

        # Parse bytes if available (some kernels expose this in /proc/net/tcp)
        # Standard /proc/net/tcp doesn't have byte counts, but /proc/net/snmp does
        # We'll use the connection count + unique IPs as the primary signal


def _collect_linux_port_stats(target_ports: set[int]) -> PerPortSnapshot:
    """Collect per-port stats on Linux using /proc/net parsing."""
    snapshot = PerPortSnapshot()

    _parse_proc_net_sockets('/proc/net/tcp', target_ports, 'tcp', snapshot)
    _parse_proc_net_sockets('/proc/net/udp', target_ports, 'udp', snapshot)

    # Accurate unique IP counting via psutil (deduplicated per port+proto)
    unique_ip_map: dict[tuple[int, str], set[str]] = {}
    try:
        for conn in psutil.net_connections(kind='inet4'):
            if not conn.laddr:
                continue
            local_port = conn.laddr.port
            if local_port not in target_ports:
                continue
            proto = 'tcp' if conn.type == 1 else 'udp' if conn.type == 2 else None
            if proto is None or not conn.raddr:
                continue
            key = (local_port, proto)
            if key not in unique_ip_map:
                unique_ip_map[key] = set()
            unique_ip_map[key].add(conn.raddr.ip)
    except (psutil.AccessDenied, PermissionError):
        pass

    for (port, proto), ips in unique_ip_map.items():
        ps = snapshot.get(port, proto)
        ps.unique_ips = len(ips)

    return snapshot


# ---------------------------------------------------------------------------
# Scapy-based UDP source IP collector
# ---------------------------------------------------------------------------

def _get_tcp_player_ips(target_ports: set[int]) -> dict[int, set[str]]:
    """
    Get the set of remote IPs that have established TCP connections to each
    target port. These are the "known player" IPs.

    Returns:
        dict mapping port -> set of remote IP strings
    """
    player_ips: dict[int, set[str]] = {port: set() for port in target_ports}
    try:
        for conn in psutil.net_connections(kind='inet4'):
            if not conn.laddr or not conn.raddr:
                continue
            if conn.laddr.port not in target_ports:
                continue
            if conn.type != 1:  # SOCK_STREAM only (TCP)
                continue
            player_ips[conn.laddr.port].add(conn.raddr.ip)
    except (psutil.AccessDenied, PermissionError):
        log.debug("psutil.net_connections() denied for player IP collection")
    return player_ips


def collect_udp_source_ips(
    target_ports: set[int],
    tcp_player_ips: dict[int, set[str]],
    sniff_duration: int = 10,
    iface: str | None = None,
) -> dict[int, set[str]]:
    """
    Sniff UDP packets on the target ports for a short duration and return
    the set of source IPs that are NOT in the known TCP player set.

    This is the primary DDoS signal for UDP: a large number of non-player
    IPs sending UDP data to the DCS port indicates a UDP flood / DDoS.

    Requires scapy + npcap (Windows) or libpcap (Linux).
    Does NOT require promiscuous mode — we only capture packets addressed
    to our IP on the target port.

    Args:
        target_ports: Set of local UDP port numbers to sniff.
        tcp_player_ips: dict of port -> set of known player IPs (from TCP connections).
        sniff_duration: How many seconds to sniff (default: 10).
        iface: Network interface to sniff on. None = auto-detect.
               On Windows with npcap, use the interface name from
               scapy.get_if_list(). For localhost testing, use the
               npcap loopback adapter (e.g. "NPF_Loopback").

    Returns:
        dict mapping port -> set of non-player source IP strings seen sending UDP.
    """
    try:
        from scapy.all import sniff, UDP, IP, get_if_list, conf
    except ImportError:
        log.warning(
            "scapy not installed — UDP DDoS detection disabled. "
            "Install with: pip install scapy"
        )
        return {port: set() for port in target_ports}

    non_player_ips: dict[int, set[str]] = {port: set() for port in target_ports}

    # Build BPF filter: "udp dst port 10308 or udp dst port 10309"
    port_filter = " or ".join(f"udp dst port {p}" for p in target_ports)
    if not port_filter:
        return non_player_ips

    # Auto-detect interface if not specified
    if iface is None:
        # If all TCP player IPs are loopback, sniff on loopback
        all_player_ips = set()
        for ips in tcp_player_ips.values():
            all_player_ips.update(ips)
        if all_player_ips and all(ip.startswith("127.") or ip == "::1" for ip in all_player_ips):
            # Try to find a loopback interface
            try:
                for candidate in get_if_list():
                    lower = candidate.lower()
                    if "loopback" in lower or "lo" in lower:
                        iface = candidate
                        log.info(f"Auto-selected loopback interface for UDP sniff: {candidate}")
                        break
            except Exception:
                pass
            if iface is None:
                log.warning(
                    "All players are on localhost but no loopback interface found. "
                    "UDP sniff may not capture packets. Install npcap loopback adapter "
                    "or set iface manually in config."
                )

    def _process_packet(pkt):
        if not pkt.haslayer(UDP) or not pkt.haslayer(IP):
            return
        dst_port = pkt[UDP].dport
        if dst_port not in target_ports:
            return
        src_ip = pkt[IP].src
        # Check if this source IP is a known TCP player
        if src_ip not in tcp_player_ips.get(dst_port, set()):
            non_player_ips[dst_port].add(src_ip)

    sniff_kwargs = dict(
        filter=port_filter,
        timeout=sniff_duration,
        store=False,
        prn=_process_packet,
    )
    if iface is not None:
        sniff_kwargs["iface"] = iface

    try:
        sniff(**sniff_kwargs)
    except PermissionError:
        log.error(
            "Sniffing requires administrator privileges. "
            "Run the bot as administrator for UDP DDoS detection."
        )
    except Exception as ex:
        log.debug("UDP sniff failed: %s", ex)

    return non_player_ips


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_port_stats(target_ports: set[int]) -> PerPortSnapshot:
    """
    Collect per-port network statistics.

    Args:
        target_ports: Set of local port numbers to monitor (e.g. {10308, 10309}).

    Returns:
        PerPortSnapshot with stats for each (port, protocol) pair.
    """
    if not target_ports:
        return PerPortSnapshot()

    if sys.platform == 'win32':
        return _collect_windows_port_stats(target_ports)
    else:
        return _collect_linux_port_stats(target_ports)
