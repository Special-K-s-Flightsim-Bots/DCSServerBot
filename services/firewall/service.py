from __future__ import annotations

import asyncio
import math
import os
import re
import psutil
import subprocess
import sys
import time

from core import Status, Server, utils, proxy, Node
from core.services.base import Service
from core.services.registry import ServiceRegistry
from datetime import datetime, timezone
from discord.ext import tasks
from typing import cast

from ..servicebus import ServiceBus
from ..bot import BotService
from .portstats import collect_port_stats, collect_udp_source_ips, _get_tcp_player_ips, PortStats

__all__ = [
    "FirewallService"
]


@ServiceRegistry.register(plugin="firewall", depends_on=[ServiceBus, BotService])
class FirewallService(Service):

    def __init__(self, node):
        super().__init__(node, name="Firewall")
        self.bus = None
        self._bot = None
        # Port traffic baselines: key = (server_name, port, protocol)
        # value = dict with running mean, variance (Welford), count
        self._port_baselines: dict[tuple[str, int, str], dict] = {}
        self._port_alert_cooldown: dict[tuple[str, int, str], datetime] = {}
        # Consecutive anomaly counters: key = (server_name, port, protocol)
        self._port_anomaly_streak: dict[tuple[str, int, str], int] = {}
        # DDoS state: key = (server_name, port, protocol) → True if currently under attack
        self._port_ddos_active: dict[tuple[str, int, str], bool] = {}
        # Node-wide bandwidth baseline: key = node name
        self._node_bw_baselines: dict[str, dict] = {}
        self._node_bw_alert_cooldown: dict[str, datetime] = {}
        self._node_last_bytes_recv: int = 0
        # Node-wide anomaly streak and DDoS state
        self._node_bw_anomaly_streak: int = 0
        self._node_ddos_active: bool = False
        # DDoS helper process (Windows firewall rule manager, runs elevated)
        self._ddos_helper: psutil.Process | None = None
        self._ddos_helper_lock = asyncio.Lock()
        # Dynamic whitelist: IPs seen connecting via TCP during a UDP DDoS block
        # key = (server_name, port) → set of IP strings
        self._dynamic_whitelist: dict[tuple[str, int], set[str]] = {}
        # Node-wide DDoS: set of server names blocked by the node-wide event
        self._node_blocked_servers: set[str] = set()
        # Per-IP connection counts for single-IP flood detection
        # key = (server_name, port) → dict{ip: conn_count}
        self._ip_conn_counts: dict[tuple[str, int], dict[str, int]] = {}
        # IPs that have been auto-blocked permanently (to avoid re-blocking)
        self._auto_blocked_ips: set[str] = set()
        # Log tail tasks per server: key = server_name → asyncio.Task
        self._log_tail_tasks: dict[str, asyncio.Task] = {}
        # Regex for DCS log connect lines
        self._re_client_connect = re.compile(
            r'added client\[\d+\] name=.+ addr=(\d+\.\d+\.\d+\.\d+):\d+'
        )

    async def _load_baselines_from_db(self) -> None:
        """
        Pre-seed baselines from recent port_traffic data so the bot doesn't
        false-positive on startup when a server already has players connected.

        Reads the last `min_samples` rows per (server, port, protocol) and
        initializes the Welford running stats from their mean and variance.
        """
        config = self.get_config().get('ddos_detection', {})
        if not config.get('enabled', False):
            return

        lookback_minutes = config.get('baseline_lookback_minutes', 30)
        min_samples = config.get('min_samples', 30)

        try:
            async with self.apool.connection() as conn:
                # Load per-port baselines for excess connections
                rows = await conn.execute("""
                    SELECT server_name, port, protocol,
                           AVG(connections - (players * 2)) as avg_excess,
                           STDDEV(connections - (players * 2)) as std_excess,
                           COUNT(*) as cnt
                    FROM port_traffic
                    WHERE node = %s
                      AND under_attack = FALSE
                      AND time > (NOW() AT TIME ZONE 'utc') - interval '%s minutes'
                    GROUP BY server_name, port, protocol
                    HAVING COUNT(*) >= %s
                """, (self.node.name, lookback_minutes, min_samples))

                for row in (await rows.fetchall()):
                    key = (row[0], row[1], row[2])
                    count = row[5]
                    mean = float(row[3]) if row[3] else 0.0
                    std = float(row[4]) if row[4] else 0.0

                    m2 = (std ** 2) * (count - 1) if count > 1 else 0.0

                    self._port_baselines[key] = {
                        'count': count,
                        'mean': mean,
                        'm2': m2,
                    }
                    self.log.info(
                        f"- Loaded baseline for {key[0]} {key[2]}/{key[1]}: "
                        f"excess={mean:.0f}±{std:.0f} ({count} samples)"
                    )

                # Load UDP non-player IP baseline
                udp_rows = await conn.execute("""
                    SELECT server_name, port,
                           AVG(non_player_udp_ips) as avg_udp,
                           STDDEV(non_player_udp_ips) as std_udp,
                           COUNT(*) as cnt
                    FROM port_traffic
                    WHERE node = %s
                      AND protocol = 'udp'
                      AND under_attack = FALSE
                      AND time > (NOW() AT TIME ZONE 'utc') - interval '%s minutes'
                    GROUP BY server_name, port
                    HAVING COUNT(*) >= %s
                """, (self.node.name, lookback_minutes, min_samples))

                for row in (await udp_rows.fetchall()):
                    key = (row[0], row[1], 'udp')
                    count = row[4]
                    mean = float(row[2]) if row[2] else 0.0
                    std = float(row[3]) if row[3] else 0.0
                    m2 = (std ** 2) * (count - 1) if count > 1 else 0.0

                    self._port_baselines[key] = {
                        'count': count,
                        'mean': mean,
                        'm2': m2,
                    }
                    self.log.info(
                        f"- Loaded UDP baseline for {key[0]} udp/{key[1]}: "
                        f"non_player_ips={mean:.0f}±{std:.0f} ({count} samples)"
                    )

                # Load node-wide bandwidth baseline
                bw_rows = await conn.execute("""
                    SELECT AVG(bytes_in), STDDEV(bytes_in), COUNT(*)
                    FROM (
                        SELECT bytes_in
                        FROM port_traffic
                        WHERE node = %s
                          AND under_attack = FALSE
                          AND time > (NOW() AT TIME ZONE 'utc') - interval '%s minutes'
                        ORDER BY time DESC
                        LIMIT %s
                    ) sub
                """, (self.node.name, lookback_minutes, min_samples * 10))

                bw_row = await bw_rows.fetchone()
                if bw_row and bw_row[2] >= min_samples:
                    count = bw_row[2]
                    mean_bw = float(bw_row[0])
                    std_bw = float(bw_row[1]) if bw_row[1] else 0.0
                    m2_bw = (std_bw ** 2) * (count - 1) if count > 1 else 0.0

                    self._node_bw_baselines[self.node.name] = {
                        'count': count,
                        'mean_bw': mean_bw,
                        'm2_bw': m2_bw,
                    }
                    self.log.info(
                        f"- Loaded node bandwidth baseline: {mean_bw:.0f}±{std_bw:.0f} bytes/s ({count} samples)"
                    )

        except Exception as ex:
            self.log.debug(f"Could not load baselines from DB: {ex}")

    # ------------------------------------------------------------------
    # DDoS helper process (Windows firewall rule manager)
    # ------------------------------------------------------------------

    def _ddos_helper_path(self) -> str:
        """Return the absolute path to ddos_helper.py."""
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ddos_helper.py')

    async def _start_ddos_helper(self) -> None:
        """Start the elevated DDoS helper process (Windows only)."""
        if sys.platform != 'win32':
            return
        helper_path = self._ddos_helper_path()
        if not os.path.exists(helper_path):
            self.log.warning(f"DDoS helper not found at {helper_path}")
            return
        try:
            # Resolve DCS installation path from node config
            dcs_install = self.node.locals.get('DCS', {}).get('installation', '')
            # Build command line with proper quoting via list2cmdline (same pattern as do_repair)
            cmdline = subprocess.list2cmdline([helper_path, dcs_install])
            self._ddos_helper = utils.start_elevated(
                sys.executable, os.getcwd(), cmdline
            )
            if self._ddos_helper:
                self.log.info(f"DDoS helper started (PID {self._ddos_helper.pid})")
                # Ensure the helper is stopped when the main process exits
                import atexit
                atexit.register(self._stop_ddos_helper_sync)
                try:
                    # Init: disable DCS general rules (base rules created by server startup / extensions)
                    resp = await self._send_helper_command('init')
                    self.log.info(f"DDoS helper init: {resp}")
                except Exception as ex:
                    self.log.warning(f"DDoS helper init failed: {ex}")
                    # Kill the helper if init failed — don't leave it orphaned
                    try:
                        self._ddos_helper.kill()
                        self._ddos_helper.wait(timeout=5)
                    except Exception:
                        pass
                    self._ddos_helper = None
            else:
                self.log.warning("DDoS helper failed to start (no process returned)")
        except Exception as ex:
            self.log.warning(f"Failed to start DDoS helper: {ex}")
            self._ddos_helper = None

    def _stop_ddos_helper_sync(self) -> None:
        """Synchronous version of _stop_ddos_helper for atexit registration."""
        if self._ddos_helper and self._ddos_helper.is_running():
            try:
                # Send graceful exit command via named pipe
                self._send_helper_command_win32("exit")
                # Wait for the process to exit cleanly
                self._ddos_helper.wait(timeout=5)
            except Exception:
                # Fall back to kill if graceful exit fails
                try:
                    self._ddos_helper.kill()
                    self._ddos_helper.wait(timeout=5)
                except Exception:
                    pass
            self.log.info("DDoS helper stopped (atexit)")
        self._ddos_helper = None

    async def _stop_ddos_helper(self) -> None:
        """Stop the DDoS helper process."""
        if self._ddos_helper and self._ddos_helper.is_running():
            try:
                await self._send_helper_command("exit")
            except Exception:
                pass
            try:
                self._ddos_helper.wait(timeout=5)
            except Exception:
                self._ddos_helper.kill()
            self.log.info("DDoS helper stopped")
        self._ddos_helper = None

    async def _send_helper_command(self, command: str) -> str:
        """
        Send a command to the DDoS helper via named pipe.

        Returns the response string.
        """
        if not self._ddos_helper or not self._ddos_helper.is_running():
            raise RuntimeError("DDoS helper is not running")

        if sys.platform == 'win32':
            return await asyncio.to_thread(self._send_helper_command_win32, command)
        raise RuntimeError("DDoS helper is only supported on Windows")

    @staticmethod
    def _send_helper_command_win32(command: str) -> str:
        """Synchronous named pipe communication (runs in thread)."""
        import ctypes.wintypes

        GENERIC_READ_WRITE = 0xC0000000
        OPEN_EXISTING = 3
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        PIPE_NAME = r'\\.\pipe\ddos_helper'
        kernel32 = ctypes.windll.kernel32

        # Retry up to 10 seconds for the pipe to become available
        hPipe = INVALID_HANDLE_VALUE
        for attempt in range(50):
            hPipe = kernel32.CreateFileW(
                PIPE_NAME, GENERIC_READ_WRITE, 0, None, OPEN_EXISTING, 0, None
            )
            if hPipe != INVALID_HANDLE_VALUE:
                break
            time.sleep(0.2)

        if hPipe == INVALID_HANDLE_VALUE:
            raise RuntimeError(f"Cannot connect to DDoS helper pipe after 10s (error {ctypes.GetLastError()})")

        try:
            # Send command
            cmd_bytes = (command + '\n').encode('utf-8')
            bytes_written = ctypes.wintypes.DWORD(0)
            if not kernel32.WriteFile(hPipe, cmd_bytes, len(cmd_bytes), ctypes.byref(bytes_written), None):
                raise RuntimeError("Failed to write to pipe")

            # Read response
            buf = ctypes.create_string_buffer(4096)
            bytes_read = ctypes.wintypes.DWORD(0)
            if not kernel32.ReadFile(hPipe, buf, 4096, ctypes.byref(bytes_read), None):
                raise RuntimeError("Failed to read from pipe")

            return buf.value.decode('utf-8', errors='replace').strip()
        finally:
            kernel32.CloseHandle(hPipe)

    async def create_base_rule(self, port: int, protocol: str, remote_ips: str = None) -> bool:
        """
        Create a base per-port allow rule for a specific port and protocol.
        Called by server startup and extensions to register their ports.
        Args:
            port: port number
            protocol: 'tcp' or 'udp'
            remote_ips: optional comma-separated IP whitelist. If provided, the
                        base rule only allows these IPs instead of all traffic.
        Returns True if successful.
        """
        if not self._ddos_helper or not self._ddos_helper.is_running():
            self.log.warning("DDoS helper not running, cannot create base rule")
            return False
        try:
            cmd = f'ensure_base {protocol} {port}'
            if remote_ips:
                cmd += f' {remote_ips}'
            resp = await self._send_helper_command(cmd)
            if resp.startswith('OK'):
                self.log.info(f"Base rule created: {protocol}/{port}")
                return True
            else:
                self.log.warning(f"Base rule failed: {protocol}/{port}: {resp}")
                return False
        except Exception as ex:
            self.log.error(f"Base rule error: {protocol}/{port}: {ex}")
            return False

    async def reset_fw_rules(self, server_name: str) -> None:
        """
        Reset all DDoS firewall rules for a server to clean state:
        1. Remove any active DDoS block rules (deny + allow rules) for this server's ports
        2. Re-create base per-port allow rules for known ports
        Call this on server startup to ensure a clean slate.
        """
        if not self._ddos_helper or not self._ddos_helper.is_running():
            self.log.warning("DDoS helper not running, cannot reset firewall rules")
            return
        self.log.info(f"Resetting firewall rules for {server_name}")
        server = self.bus.servers.get(server_name)
        if server and server.instance:
            locals_dict = dict(server.instance.locals)
            # Collect all known ports for this server
            port_keys = ['dcs_port', 'webgui_port']
            for pk in port_keys:
                port_val: int | None = locals_dict.get(pk)
                if not port_val:
                    continue
                for proto in ('tcp', 'udp'):
                    deny_rule = f"DCS-deny-{proto}-{port_val}"
                    try:
                        resp = await self._send_helper_command(f"restore {deny_rule}")
                        self.log.info(f"Reset: {resp}")
                    except Exception as ex:
                        self.log.debug(f"Reset: {deny_rule} not found: {ex}")
                    # Re-create base rule
                    await self.create_base_rule(port_val, proto)
        self.log.info(f"Firewall rules reset complete for {server_name}")

    async def _ddos_block(self, server_name: str, port: int, proto: str,
                          player_ips: list[str]) -> None:
        """
        Per-port DDoS block for a specific protocol:
        1. Disable the base allow-all rule for this port/protocol
        2. Create a deny-all rule on this port
        3. Create per-IP allow rules for known players
        The restrict_rule helper command handles all three steps.
        """
        # Merge player IPs with configured + dynamic whitelist
        all_ips = self._get_whitelist_ips(server_name, port, player_ips)
        ip_str = ','.join(all_ips)
        # The deny rule name includes protocol to allow per-protocol blocking
        deny_rule = f"DCS-deny-{proto}-{port}"
        try:
            response = await self._send_helper_command(
                f"restrict {deny_rule} {proto} {port} {ip_str}"
            )
            if response.startswith('OK'):
                self.log.info(f"DDoS block {proto}/{port}: {response[3:]}")
            else:
                self.log.warning(f"DDoS block {proto}/{port} failed: {response}")
        except Exception as ex:
            self.log.warning(f"DDoS block {proto}/{port} command failed: {ex}")

    async def _ddos_unblock(self, server_name: str, port: int, proto: str) -> None:
        """
        Per-port DDoS unblock:
        1. Remove the deny-all rule and per-IP allow rules for this port/protocol
        2. Re-enable the base allow-all rule for this port/protocol
        The restore_rule helper command handles both steps.
        """
        deny_rule = f"DCS-deny-{proto}-{port}"
        try:
            response = await self._send_helper_command(f"restore {deny_rule}")
            if response.startswith('OK'):
                self.log.info(f"DDoS unblock {proto}/{port}: {response[3:]}")
            else:
                self.log.warning(f"DDoS unblock {proto}/{port} failed: {response}")
        except Exception as ex:
            self.log.warning(f"DDoS unblock {proto}/{port} command failed: {ex}")

    # ------------------------------------------------------------------
    # Dynamic whitelist: tail dcs.log for new TCP connects during UDP block
    # ------------------------------------------------------------------

    def _get_whitelist_ips(self, server_name: str, port: int,
                           player_ips: list[str]) -> list[str]:
        """Merge player IPs, configured whitelist, and dynamic whitelist."""
        config = self.get_config().get('ddos_detection', {})
        cfg_whitelist = set(config.get('whitelist', []))
        dyn_whitelist = self._dynamic_whitelist.get((server_name, port), set())
        return list(set(player_ips) | cfg_whitelist | dyn_whitelist)

    async def _refresh_block(self, server_name: str, port: int, proto: str) -> None:
        """Re-issue the restrict command with the current IP set (after dynamic whitelist update)."""
        if not self._ddos_helper:
            return
        player_ips = await asyncio.to_thread(_get_tcp_player_ips, {port})
        dynamic = self._dynamic_whitelist.get((server_name, port), set())
        all_player = set(player_ips.get(port, set())) | dynamic
        ips = self._get_whitelist_ips(server_name, port, list(all_player))
        if not ips:
            return
        deny_rule = f"DCS-deny-{proto}-{port}"
        try:
            response = await self._send_helper_command(
                f"restrict {deny_rule} {proto} {port} {','.join(ips)}"
            )
            if response.startswith('OK'):
                self.log.info(f"DDoS refresh block: {len(ips)} IPs on {proto}/{port}")
            else:
                self.log.warning(f"DDoS refresh block failed: {response}")
        except Exception as ex:
            self.log.warning(f"DDoS refresh block failed: {ex}")

    async def _tail_dcs_log(self, server_name: str, port: int) -> None:
        """
        Tail the dcs.log file for new TCP client connects.
        When a new IP is found, add it to the dynamic whitelist and refresh the block.
        Runs as a background task until cancelled.
        """
        from core.data.impl.serverimpl import Status
        server = self.bus.servers.get(server_name)
        if not server or not server.instance:
            return
        logfile = os.path.join(server.instance.home, 'Logs', 'dcs.log')

        self.log.info(f"Log tail: watching {logfile} for new connects on {server_name}")
        if not os.path.exists(logfile):
            self.log.warning(f"Log tail: file not found: {logfile}")
            return
        try:
            # Start from end of file
            pos = os.path.getsize(logfile)
            while True:
                try:
                    # Stop if server is no longer running
                    server = self.bus.servers.get(server_name)
                    if server and server.status not in [Status.RUNNING, Status.PAUSED]:
                        self.log.info(f"Log tail: server {server_name} is {server.status}, stopping")
                        return

                    if not os.path.exists(logfile):
                        pos = 0
                        await asyncio.sleep(1)
                        continue

                    current_size = os.path.getsize(logfile)
                    if current_size < pos:
                        # Log rotated
                        pos = 0

                    if current_size > pos:
                        with open(logfile, 'r', encoding='utf-8', errors='ignore') as f:
                            f.seek(pos)
                            new_data = f.read()
                            pos = f.tell()

                        self.log.debug(f"Log tail: read {len(new_data)} bytes from {server_name} log")
                        for line in new_data.splitlines():
                            m = self._re_client_connect.search(line)
                            if m:
                                ip = m.group(1)
                                key = (server_name, port)
                                if ip not in self._dynamic_whitelist.get(key, set()):
                                    self._dynamic_whitelist.setdefault(key, set()).add(ip)
                                    self.log.info(
                                        f"Log tail: new player {ip} on {server_name}, "
                                        f"refreshing block for udp/{port}"
                                    )
                                    await self._refresh_block(server_name, port, 'udp')

                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    self.log.warning(f"Log tail error: {ex}")
                    await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass
        finally:
            self.log.info(f"Log tail: stopped watching {server_name}")

    def _start_log_tail(self, server_name: str, port: int) -> None:
        """Start tailing dcs.log for a server if not already tailing."""
        if server_name in self._log_tail_tasks:
            return
        self._dynamic_whitelist[(server_name, port)] = set()
        task = asyncio.create_task(self._tail_dcs_log(server_name, port))
        self._log_tail_tasks[server_name] = task

    def _stop_log_tail(self, server_name: str) -> None:
        """Stop tailing dcs.log for a server."""
        task = self._log_tail_tasks.pop(server_name, None)
        if task and not task.done():
            task.cancel()
        self._dynamic_whitelist = {
            k: v for k, v in self._dynamic_whitelist.items() if k[0] != server_name
        }

    @property
    def bot(self) -> BotService:
        if not self._bot:
            self._bot = ServiceRegistry.get(BotService)
        return self._bot

    async def start(self):
        await super().start()
        self.bus = cast(ServiceBus, ServiceRegistry.get(ServiceBus))
        # Load historical baselines from DB so we don't false-positive on startup
        await self._load_baselines_from_db()
        # Start DDoS helper if action is 'block' (Windows only)
        config = self.get_config().get('ddos_detection', {})
        if config.get('enabled', False):
            await self._start_ddos_helper()
        utils.safe_start(self.monitoring)

    async def stop(self):
        await self._stop_ddos_helper()
        # Stop all log tail tasks
        for server_name in list(self._log_tail_tasks.keys()):
            self._stop_log_tail(server_name)
        await utils.safe_cancel(self.monitoring)
        await super().stop()

    async def switch(self, master: bool):
        await super().switch(master)
        self._bot = None

    # ------------------------------------------------------------------
    # Port traffic collection, adaptive baseline, and DDoS detection
    # ------------------------------------------------------------------

    async def _collect_port_traffic(self) -> None:
        """
        Collect per-port traffic stats using 3 signals:

        1) TCP excess:  tcp_connections - active_players
        2) UDP flood:   unique non-player source IPs sending UDP to the DCS port
        3) Node bandwidth: total inbound bytes/sec (checked separately in _check_node_bandwidth)

        Signals 1 and 2 are per-instance (per port). Signal 3 is node-wide.
        Each signal has its own independent baseline and state machine.
        """
        config = self.get_config().get('ddos_detection', {})
        if not config.get('enabled', False):
            return

        # Build set of target ports from running instances
        target_ports: set[int] = set()
        server_port_map: dict[int, str] = {}  # port -> server_name
        for server in self.bus.servers.values():
            if server.is_remote or server.status != Status.RUNNING:
                continue
            port = int(server.instance.locals.get('dcs_port', server.settings.get('port', 10308)))
            target_ports.add(port)
            server_port_map[port] = server.name

        if not target_ports:
            return

        # --- Signal 1: TCP connections (via psutil) ---
        try:
            snapshot = await asyncio.to_thread(collect_port_stats, target_ports)
        except Exception as ex:
            self.log.debug("Port stats collection failed: %s", ex)
            snapshot = None

        # --- Signal 2: UDP non-player source IPs (via scapy) ---
        sniff_duration = config.get('udp_sniff_duration', 10)
        sniff_iface = config.get('udp_sniff_iface', None)
        try:
            tcp_player_ips = await asyncio.to_thread(_get_tcp_player_ips, target_ports)
            udp_non_player = await asyncio.to_thread(
                collect_udp_source_ips, target_ports, tcp_player_ips, sniff_duration, sniff_iface
            )
        except Exception as ex:
            self.log.debug("UDP source collection failed: %s", ex)
            udp_non_player = {port: set() for port in target_ports}

        now = datetime.now(timezone.utc)

        # --- Signal 4: Per-IP TCP connection counts ---
        # Count connections per remote IP per port using psutil
        ip_conn_counts: dict[tuple[str, int], dict[str, int]] = {}
        try:
            for conn in psutil.net_connections(kind='inet4'):
                if not conn.laddr or not conn.raddr:
                    continue
                if conn.laddr.port not in target_ports:
                    continue
                if conn.type != 1:  # TCP only
                    continue
                server_name = server_port_map.get(conn.laddr.port)
                if not server_name:
                    continue
                key = (server_name, conn.laddr.port)
                if key not in ip_conn_counts:
                    ip_conn_counts[key] = {}
                ip = conn.raddr.ip
                ip_conn_counts[key][ip] = ip_conn_counts[key].get(ip, 0) + 1
        except (psutil.AccessDenied, PermissionError):
            self.log.debug("psutil.net_connections() access denied for per-IP counts")
        self._ip_conn_counts = ip_conn_counts

        for port in target_ports:
            server_name = server_port_map.get(port)
            if not server_name:
                continue

            players = 0
            for srv in self.bus.servers.values():
                if srv.name == server_name:
                    players = len(srv.get_active_players())
                    break

            # --- Build PortStats for TCP ---
            tcp_ps = None
            if snapshot:
                tcp_ps = snapshot.stats.get((port, 'tcp'))
            if tcp_ps is None:
                tcp_ps = PortStats(port=port, protocol='tcp')

            # Attach UDP non-player count to the UDP PortStats (or create one)
            udp_ps = snapshot.stats.get((port, 'udp')) if snapshot else None
            if udp_ps is None:
                udp_ps = PortStats(port=port, protocol='udp')
            udp_ps.non_player_udp_ips = len(udp_non_player.get(port, set()))

            # --- Determine under_attack flag ---
            tcp_ddos = self._port_ddos_active.get((server_name, port, 'tcp'), False)
            udp_ddos = self._port_ddos_active.get((server_name, port, 'udp'), False)
            node_ddos = self._node_ddos_active
            under_attack = tcp_ddos or udp_ddos or node_ddos

            # --- Persist to DB ---
            try:
                async with self.apool.connection() as conn:
                    # TCP row
                    await conn.execute("""
                        INSERT INTO port_traffic (
                            node, server_name, port, protocol,
                            bytes_in, bytes_out, packets_in, packets_out,
                            unique_ips, connections, non_player_udp_ips, players,
                            under_attack, time
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        self.node.name, server_name, port, 'tcp',
                        tcp_ps.bytes_in, tcp_ps.bytes_out, tcp_ps.packets_in, tcp_ps.packets_out,
                        tcp_ps.unique_ips, tcp_ps.tcp_conns, 0, players,
                        under_attack, now
                    ))
                    # UDP row
                    await conn.execute("""
                        INSERT INTO port_traffic (
                            node, server_name, port, protocol,
                            bytes_in, bytes_out, packets_in, packets_out,
                            unique_ips, connections, non_player_udp_ips, players,
                            under_attack, time
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        self.node.name, server_name, port, 'udp',
                        udp_ps.bytes_in, udp_ps.bytes_out, udp_ps.packets_in, udp_ps.packets_out,
                        udp_ps.unique_ips, udp_ps.udp_conns, udp_ps.non_player_udp_ips, players,
                        under_attack, now
                    ))
            except Exception as ex:
                self.log.debug("Failed to insert port_traffic: %s", ex)

            # --- Signal 1: TCP excess baseline & check ---
            await self._update_baseline_and_check(
                server_name, port, 'tcp', tcp_ps, players, now, config
            )

            # --- Signal 2: UDP non-player baseline & check ---
            await self._update_baseline_and_check_udp(
                server_name, port, udp_ps, players, now, config,
                len(udp_non_player.get(port, set()))
            )

            # --- Signal 4: Per-IP flood check ---
            await self._check_per_ip_flood(server_name, port, config)

    async def _check_node_bandwidth(self) -> None:
        """
        Baseline node-wide inbound bandwidth and detect saturation spikes.

        Uses psutil.net_io_counters() which is already collected by
        _pull_load_params. Computes bytes_recv/sec delta and applies the same
        Welford + z-score anomaly detection as per-port baselines.
        """
        config = self.get_config().get('ddos_detection', {})
        if not config.get('enabled', False):
            return

        net_io = psutil.net_io_counters(pernic=False)
        now = datetime.now(timezone.utc)

        # Calculate bytes_recv/sec since last tick
        prev = self._node_last_bytes_recv
        self._node_last_bytes_recv = net_io.bytes_recv
        if prev == 0:
            return  # need at least two samples

        # Use a fixed ~60s interval assumption (monitoring loop is 1 min)
        bytes_recv_per_sec = max(0, int((net_io.bytes_recv - prev) / 60))

        key = self.node.name
        if key not in self._node_bw_baselines:
            self._node_bw_baselines[key] = {
                'count': 0,
                'mean_bw': 0.0,
                'm2_bw': 0.0,
            }
        baseline = self._node_bw_baselines[key]

        # Cooldown check
        cooldown_minutes = config.get('alert_cooldown_minutes', 15)
        last_alert = self._node_bw_alert_cooldown.get(key)

        min_samples = config.get('min_samples', 30)
        if baseline['count'] < min_samples:
            self._update_welford_bw(baseline, bytes_recv_per_sec)
            return

        threshold_sigma = config.get('threshold_sigma', 3.0)
        consecutive_ticks = config.get('consecutive_ticks', 3)
        recovery_ticks = config.get('recovery_ticks', 5)
        min_abs_mbps = config.get('min_abs_recv_mbps', 10)
        min_abs_bytes = min_abs_mbps * 1024 * 1024

        std = math.sqrt(baseline['m2_bw'] / baseline['count']) if baseline['count'] > 1 else 0
        is_anomalous = bytes_recv_per_sec > min_abs_bytes and std > 0 and \
                       (bytes_recv_per_sec - baseline['mean_bw']) / std > threshold_sigma

        was_active = self._node_ddos_active

        if is_anomalous:
            self._node_bw_anomaly_streak += 1

            if not was_active and self._node_bw_anomaly_streak >= consecutive_ticks:
                self._node_ddos_active = True
                await self._on_node_ddos_start(key, bytes_recv_per_sec, baseline, config)
            elif was_active:
                if not last_alert or (now - last_alert).total_seconds() >= cooldown_minutes * 60:
                    self._node_bw_alert_cooldown[key] = now
                    await self._on_node_ddos_update(key, bytes_recv_per_sec, baseline)
        else:
            self._node_bw_anomaly_streak = 0

            if was_active:
                rec_count = getattr(self, '_node_bw_recovery_count', 0) + 1
                self._node_bw_recovery_count = rec_count
                if rec_count >= recovery_ticks:
                    self._node_ddos_active = False
                    self._node_bw_recovery_count = 0
                    self._node_bw_alert_cooldown.pop(key, None)
                    await self._on_node_ddos_end(key, bytes_recv_per_sec, baseline)
            else:
                self._node_bw_recovery_count = 0

        # Only update baseline with non-anomalous data
        if not is_anomalous:
            self._update_welford_bw(baseline, bytes_recv_per_sec)

    async def _on_node_ddos_start(self, node_name: str, bytes_recv_per_sec: int, baseline: dict, config: dict) -> None:
        """Called when node-wide DDoS is first confirmed. Override for custom actions."""
        current_mbps = round(bytes_recv_per_sec / (1024 * 1024), 1)
        avg_mbps = round(baseline['mean_bw'] / (1024 * 1024), 1)
        std_mbps = round(math.sqrt(baseline['m2_bw'] / baseline['count']) / (1024 * 1024), 1) if baseline['count'] > 1 else 0
        z_score = round((bytes_recv_per_sec - baseline['mean_bw']) / math.sqrt(baseline['m2_bw'] / baseline['count']), 1) if baseline['count'] > 1 else 0

        title = f"🔴 Node {node_name}: DDoS START (bandwidth)"
        message = "Node-wide DDoS attack confirmed"
        fields = [
            ("Inbound:", f"{current_mbps} MB/s"),
            ("Baseline avg:", f"{avg_mbps} MB/s"),
            ("StdDev:", f"{std_mbps} MB/s"),
            ("Z-score:", f"{z_score}σ"),
            ("Baseline samples:", f"{baseline['count']}"),
            ("Consecutive anomaly ticks:", f"{config.get('consecutive_ticks', 3)}")
        ]
        self.log.warning(title)
        try:
            await self.bot.alert(title=title, message=message, server=None, fields=fields)
            await self.bot.audit(f"**DDoS START** on node {node_name}: inbound {current_mbps} MB/s (baseline {avg_mbps} MB/s)")
        except Exception as ex:
            self.log.debug("Failed to send node DDoS start alert: %s", ex)

        # Node-wide firewall block: apply per-instance blocking to ALL running servers
        if self._ddos_helper and config.get('action') == 'block' and config.get('node_block', True):
            await self._ddos_block_all_servers(config)

    async def _on_node_ddos_update(self, node_name: str, bytes_recv_per_sec: int, baseline: dict) -> None:
        """Periodic update while node-wide DDoS continues."""
        current_mbps = round(bytes_recv_per_sec / (1024 * 1024), 1)
        avg_mbps = round(baseline['mean_bw'] / (1024 * 1024), 1)
        std_mbps = round(math.sqrt(baseline['m2_bw'] / baseline['count']) / (1024 * 1024), 1) if baseline['count'] > 1 else 0

        title = f"⚠️ Node {node_name}: DDoS ongoing (bandwidth)"
        message = "Node-wide DDoS continues"
        fields = [
            ("Inbound:", f"{current_mbps} MB/s"),
            ("Baseline avg:", f"{avg_mbps} MB/s"),
            ("StdDev:", f"{std_mbps} MB/s")
        ]
        self.log.warning(title)
        try:
            await self.bot.alert(title=title, message=message, server=None, fields=fields)
        except Exception as ex:
            self.log.debug("Failed to send node DDoS update: %s", ex)

    async def _on_node_ddos_end(self, node_name: str, bytes_recv_per_sec: int, baseline: dict) -> None:
        """Called when node-wide DDoS is confirmed over. Override for custom actions."""
        current_mbps = round(bytes_recv_per_sec / (1024 * 1024), 1)
        avg_mbps = round(baseline['mean_bw'] / (1024 * 1024), 1)
        std_mbps = round(math.sqrt(baseline['m2_bw'] / baseline['count']) / (1024 * 1024), 1) if baseline['count'] > 1 else 0

        title = f"🟢 Node {node_name}: DDoS END (bandwidth)"
        message = "Node-wide DDoS ended — bandwidth returned to normal"
        fields = [
            ("Inbound:", f"{current_mbps} MB/s"),
            ("Baseline avg:", f"{avg_mbps} MB/s"),
            ("StdDev:", f"{std_mbps} MB/s\n"),
            ("Baseline samples:", f"{baseline['count']}"),
            ("_ _", "_ _"),
            ("_ _", "_ _")
        ]
        self.log.info(title)
        try:
            await self.bot.alert(title=title, message=message, server=None, fields=fields)
            await self.bot.audit(f"**DDoS END** on node {node_name}: inbound normalized to {current_mbps} MB/s")
        except Exception as ex:
            self.log.debug("Failed to send node DDoS end alert: %s", ex)

        # Node-wide firewall unblock: restore all servers blocked by the node-wide event
        if self._ddos_helper:
            await self._ddos_unblock_all_servers()

    # ------------------------------------------------------------------
    # Manual DDoS block / unblock (can be called via @proxy from Discord)
    # ------------------------------------------------------------------

    @proxy
    async def ensure_ddos_helper(self, node: Node) -> str:
        """
        Start the DDoS helper process if it is not already running.
        Returns a status message (empty string on success).
        """
        if self._ddos_helper and self._ddos_helper.is_running():
            return ""
        try:
            await self._start_ddos_helper()
        except Exception as ex:
            return f"Failed to start DDoS helper: {ex}"
        if not self._ddos_helper or not self._ddos_helper.is_running():
            return "DDoS helper failed to start. Check logs for details."
        return ""

    @proxy
    async def activate_node_block(self, node: Node) -> str:
        """
        Manually activate DDoS blocking for ALL running servers on the node.
        Returns a status message.
        """
        error = await self.ensure_ddos_helper(node)
        if error:
            return error

        config = self.get_config().get('ddos_detection', {})
        await self._ddos_block_all_servers(config)
        blocked = [s.name for s in self.bus.servers.values()
                   if s.status == Status.RUNNING and not s.is_remote]
        return f"Node-wide DDoS block activated. Blocked {len(blocked)} server(s): {', '.join(blocked)}"

    @proxy
    async def deactivate_node_block(self, node: Node) -> str:
        """
        Manually deactivate DDoS blocking for the whole node.
        Returns a status message.
        """
        if not self._ddos_helper or not self._ddos_helper.is_running():
            return "DDoS helper is not running."

        await self._ddos_unblock_all_servers()
        return "Node-wide DDoS block deactivated."

    @proxy
    async def activate_ddos_block(self, server: Server,
                                  protocols: list[str] | None = None) -> str:
        """
        Manually activate DDoS blocking for a specific server.
        protocols: list of 'tcp', 'udp', or 'both'. Defaults to ['tcp', 'udp'].
        Returns a status message.
        """
        error = await self.ensure_ddos_helper(server.node)
        if error:
            return error

        config = self.get_config().get('ddos_detection', {})

        if server.status not in (Status.RUNNING, Status.PAUSED):
            return f"Server {server.name} is not running (status: {server.status})."

        port = int(server.instance.locals.get('dcs_port',
                     server.settings.get('port', 10308)))
        player_ips = [p.ipaddr for p in server.get_active_players() if p.ipaddr]

        if not player_ips:
            self.log.info(f"activate_ddos_block: no player IPs for {server.name}, "
                          f"applying whitelist-only block")

        # Normalize protocols
        if protocols is None:
            protocols = ['tcp', 'udp']
        if 'both' in protocols:
            protocols = ['tcp', 'udp']

        # Mark as under attack
        tcp_key = (server.name, port, 'tcp')
        udp_key = (server.name, port, 'udp')
        if 'tcp' in protocols:
            self._port_ddos_active[tcp_key] = True
        if 'udp' in protocols:
            self._port_ddos_active[udp_key] = True

        from .portstats import PortStats
        blocked = []

        if 'tcp' in protocols:
            tcp_ps = PortStats(port=port, protocol='tcp', tcp_conns=len(player_ips))
            await self._on_ddos_start(
                server_name=server.name, port=port, proto='tcp',
                ps=tcp_ps, players=len(player_ips),
                excess_conns=0, baseline={'mean': 0, 'm2': 0, 'count': 1},
                config=config, action_override='block'
            )
            blocked.append('tcp')

        if 'udp' in protocols:
            udp_ps = PortStats(port=port, protocol='udp')
            await self._on_ddos_start_udp(
                server_name=server.name, port=port,
                ps=udp_ps, players=len(player_ips),
                non_player_udp_count=0,
                baseline={'mean': 0, 'm2': 0, 'count': 1},
                config=config, action_override='block'
            )
            blocked.append('udp')

        proto_str = '+'.join(blocked)
        return f"{server.name} blocked ({proto_str}/{port}) with {len(player_ips)} player IPs allowed."

    @proxy
    async def deactivate_ddos_block(self, server: Server) -> str:
        """
        Manually deactivate DDoS blocking for a specific server.
        Returns a status message.
        """
        if not self._ddos_helper or not self._ddos_helper.is_running():
            return "DDoS helper is not running."

        port = int(server.instance.locals.get('dcs_port',
                     server.settings.get('port', 10308)))

        tcp_key = (server.name, port, 'tcp')
        udp_key = (server.name, port, 'udp')

        if not self._port_ddos_active.get(tcp_key, False) and \
           not self._port_ddos_active.get(udp_key, False):
            return f"{server.name} is not currently blocked."

        self._port_ddos_active.pop(tcp_key, None)
        self._port_ddos_active.pop(udp_key, None)

        from .portstats import PortStats
        players = len(server.get_active_players()) if server.status in (Status.RUNNING, Status.PAUSED) else 0
        tcp_ps = PortStats(port=port, protocol='tcp', tcp_conns=players)
        udp_ps = PortStats(port=port, protocol='udp')

        await self._on_ddos_end(
            server_name=server.name, port=port, proto='tcp',
            ps=tcp_ps, players=players, excess_conns=0,
            baseline={'mean': 0, 'm2': 0, 'count': 1},
            action_override='block'
        )
        await self._on_ddos_end_udp(
            server_name=server.name, port=port,
            ps=udp_ps, players=players,
            non_player_udp_count=0,
            baseline={'mean': 0, 'm2': 0, 'count': 1},
            action_override='block'
        )
        return f"{server.name} DDoS block deactivated."

    async def _ddos_block_all_servers(self, config: dict) -> None:
        """
        Node-wide DDoS response: block ALL running servers to known player IPs.
        Reuses the existing per-instance _on_ddos_start/_on_ddos_start_udp methods
        so each server gets its own firewall rules, log tails, and dynamic whitelists.
        """
        for server in self.bus.servers.values():
            if server.is_remote or server.status != Status.RUNNING:
                continue
            port = int(server.instance.locals.get('dcs_port',
                         server.settings.get('port', 10308)))
            server_name = server.name

            # Skip if already under individual attack (either TCP or UDP)
            tcp_key = (server_name, port, 'tcp')
            udp_key = (server_name, port, 'udp')
            if self._port_ddos_active.get(tcp_key, False) or \
               self._port_ddos_active.get(udp_key, False):
                self.log.info(f"Node block: {server_name} already under individual attack, skipping")
                continue

            # Mark as under attack so _collect_port_traffic sets under_attack=True in DB
            self._port_ddos_active[tcp_key] = True
            self._port_ddos_active[udp_key] = True

            # Collect player IPs from the server's active player list
            player_ips = [p.ipaddr for p in server.get_active_players() if p.ipaddr]

            self.log.info(f"Node block: blocking {server_name} ({port}) with {len(player_ips)} player IPs")

            # Build minimal PortStats for the callbacks
            from .portstats import PortStats
            tcp_ps = PortStats(port=port, protocol='tcp', tcp_conns=len(player_ips))
            udp_ps = PortStats(port=port, protocol='udp')

            # Call existing per-instance methods — they handle firewall rules,
            # log tails, alerts, etc.
            await self._on_ddos_start(
                server_name=server_name, port=port, proto='tcp',
                ps=tcp_ps, players=len(player_ips),
                excess_conns=0, baseline={'mean': 0, 'm2': 0, 'count': 1},
                config=config, action_override='block'
            )
            await self._on_ddos_start_udp(
                server_name=server_name, port=port,
                ps=udp_ps, players=len(player_ips),
                non_player_udp_count=0,
                baseline={'mean': 0, 'm2': 0, 'count': 1},
                config=config, action_override='block'
            )

            self._node_blocked_servers.add(server_name)

        self.log.info(f"Node block complete: {len(self._node_blocked_servers)} servers blocked")

    async def _ddos_unblock_all_servers(self) -> None:
        """
        Node-wide DDoS recovery: unblock all servers that were blocked by the
        node-wide event. Only touches servers in _node_blocked_servers.
        """
        from .portstats import PortStats

        for server_name in list(self._node_blocked_servers):
            server = self.bus.servers.get(server_name)
            if not server:
                continue
            port = int(server.instance.locals.get('dcs_port',
                         server.settings.get('port', 10308)))

            self.log.info(f"Node unblock: restoring {server_name} ({port})")

            # Clear attack state
            self._port_ddos_active.pop((server_name, port, 'tcp'), None)
            self._port_ddos_active.pop((server_name, port, 'udp'), None)

            # Build minimal PortStats for the end callbacks
            players = len(server.get_active_players()) if server.status == Status.RUNNING else 0
            tcp_ps = PortStats(port=port, protocol='tcp', tcp_conns=players)
            udp_ps = PortStats(port=port, protocol='udp')

            await self._on_ddos_end(
                server_name=server_name, port=port, proto='tcp',
                ps=tcp_ps, players=players, excess_conns=0,
                baseline={'mean': 0, 'm2': 0, 'count': 1},
                action_override='block'
            )
            await self._on_ddos_end_udp(
                server_name=server_name, port=port,
                ps=udp_ps, players=players,
                non_player_udp_count=0,
                baseline={'mean': 0, 'm2': 0, 'count': 1},
                action_override='block'
            )

        self._node_blocked_servers.clear()
        self.log.info("Node unblock complete")

    async def _check_per_ip_flood(self, server_name: str, port: int,
                                   config: dict) -> None:
        """
        Check if any single IP has too many TCP connections to a server port.
        A normal player has exactly 1 TCP connection. If an IP has > max_conns_per_ip
        connections, it's a single-IP flood / port-opening attack.

        Auto-blocks the offending IP via a permanent firewall rule.
        """
        max_conns = config.get('max_conns_per_ip', 3)
        if max_conns <= 0:
            return  # disabled

        key = (server_name, port)
        ip_counts = self._ip_conn_counts.get(key, {})

        for ip, count in ip_counts.items():
            if count > max_conns and ip not in self._auto_blocked_ips:
                self.log.warning(
                    f"Per-IP flood: {ip} has {count} connections to "
                    f"{server_name}:{port} (max {max_conns})"
                )
                # Auto-block this IP permanently
                if self._ddos_helper:
                    try:
                        response = await self._send_helper_command(f"block_ip {ip}")
                        if response.startswith('OK'):
                            self._auto_blocked_ips.add(ip)
                            self.log.info(f"Auto-blocked IP {ip} ({count} conns to {server_name}:{port})")
                            await self.bot.audit(
                                f"**Auto-blocked IP {ip}** — {count} TCP connections "
                                f"to {server_name}:{port} (threshold: {max_conns})"
                            )
                        else:
                            self.log.warning(f"Auto-block failed for {ip}: {response}")
                    except Exception as ex:
                        self.log.warning(f"Auto-block command failed for {ip}: {ex}")

    def _update_welford_single(self, baseline: dict, value: float) -> None:
        """Welford update for a single scalar value. Uses 'mean'/'m2' keys."""
        baseline['count'] += 1
        n = baseline['count']
        delta = value - baseline['mean']
        baseline['mean'] += delta / n
        delta2 = value - baseline['mean']
        baseline['m2'] += delta * delta2

    def _update_welford_bw(self, baseline: dict, value: float) -> None:
        """Welford update for node bandwidth. Uses 'mean_bw'/'m2_bw' keys."""
        baseline['count'] += 1
        n = baseline['count']
        delta = value - baseline['mean_bw']
        baseline['mean_bw'] += delta / n
        delta2 = value - baseline['mean_bw']
        baseline['m2_bw'] += delta * delta2

    def _get_baseline(self, key: tuple[str, int, str]) -> dict:
        """Get or initialize the baseline for a (server, port, protocol)."""
        if key not in self._port_baselines:
            self._port_baselines[key] = {
                'count': 0,
                'mean': 0.0,
                'm2': 0.0,
            }
        return self._port_baselines[key]

    async def _update_baseline_and_check(
        self,
        server_name: str,
        port: int,
        proto: str,
        ps: 'PortStats',
        players: int,
        now: datetime,
        config: dict,
    ) -> None:
        """
        Update the running baseline (Welford's online algorithm) and check for anomalies.

        Uses a state machine: normal → suspicious (consecutive anomalies) → DDoS detected.
        Fires on_ddos_start callback when DDoS is first confirmed, and on_ddos_end
        callback when traffic returns to normal for a sustained period.

        The core signal is "excess connections": total connections minus the expected
        connections from known players (players × 2 for TCP+UDP). Each player has
        exactly 2 connections. Everything above that is excess — ED probes, port
        scanners, or attack bots. The baseline learns normal excess per server and
        alerts when excess spikes significantly.
        """
        key = (server_name, port, proto)
        baseline = self._get_baseline(key)

        # Each player has 1 TCP connection. Excess is everything above that.
        # UDP is invisible to psutil (connectionless), so we only count TCP.
        expected_conns = players
        excess_conns = max(0, ps.tcp_conns - expected_conns)

        # Need minimum samples before we can detect anomalies
        min_samples = config.get('min_samples', 30)
        if baseline['count'] < min_samples:
            self._update_welford_single(baseline, excess_conns)
            return

        threshold_sigma = config.get('threshold_sigma', 3.0)
        consecutive_ticks = config.get('consecutive_ticks', 3)
        recovery_ticks = config.get('recovery_ticks', 5)

        # Check if current tick is anomalous based on excess connections
        std = math.sqrt(baseline['m2'] / baseline['count']) if baseline['count'] > 1 else 0
        is_anomalous = False
        if std > 0:
            z_score = (excess_conns - baseline['mean']) / std
            if z_score > threshold_sigma:
                is_anomalous = True

        was_active = self._port_ddos_active.get(key, False)

        if is_anomalous:
            self._port_anomaly_streak[key] = self._port_anomaly_streak.get(key, 0) + 1

            if not was_active and self._port_anomaly_streak[key] >= consecutive_ticks:
                self._port_ddos_active[key] = True
                await self._on_ddos_start(server_name, port, proto, ps, players,
                                          excess_conns, baseline, config)
            elif was_active:
                cooldown_minutes = config.get('alert_cooldown_minutes', 15)
                last_alert = self._port_alert_cooldown.get(key)
                if not last_alert or (now - last_alert).total_seconds() >= cooldown_minutes * 60:
                    self._port_alert_cooldown[key] = now
                    await self._raise_ddos_alert(server_name, port, proto, ps, players,
                                                 excess_conns, baseline)
        else:
            self._port_anomaly_streak[key] = 0

            if was_active:
                recovery_count = self._port_anomaly_streak.get(key + ('_recovery',), 0) + 1
                self._port_anomaly_streak[key + ('_recovery',)] = recovery_count
                if recovery_count >= recovery_ticks:
                    self._port_ddos_active[key] = False
                    self._port_anomaly_streak.pop(key + ('_recovery',), None)
                    self._port_alert_cooldown.pop(key, None)
                    await self._on_ddos_end(server_name, port, proto, ps, players,
                                            excess_conns, baseline)
            else:
                self._port_anomaly_streak.pop(key + ('_recovery',), None)

        # Only update baseline with non-anomalous data to prevent contamination
        if not is_anomalous:
            self._update_welford_single(baseline, excess_conns)

    async def _update_baseline_and_check_udp(
        self,
        server_name: str,
        port: int,
        ps: 'PortStats',
        players: int,
        now: datetime,
        config: dict,
        non_player_udp_count: int,
    ) -> None:
        """
        Baseline & anomaly check for UDP non-player source IPs (Signal 2).

        This is independent from the TCP excess baseline. A server can have
        a UDP DDoS without a TCP flood, or vice versa.

        The signal is: number of unique source IPs sending UDP to the DCS port
        that are NOT in the TCP player IP set. Normal = a few ED probes.
        DDoS = hundreds/thousands of unique IPs.
        """
        key = (server_name, port, 'udp')
        baseline = self._get_baseline(key)

        min_samples = config.get('min_samples', 30)
        if baseline['count'] < min_samples:
            self._update_welford_single(baseline, non_player_udp_count)
            return

        threshold_sigma = config.get('threshold_sigma', 3.0)
        consecutive_ticks = config.get('consecutive_ticks', 3)
        recovery_ticks = config.get('recovery_ticks', 5)

        std = math.sqrt(baseline['m2'] / baseline['count']) if baseline['count'] > 1 else 0
        is_anomalous = False
        if std > 0:
            z_score = (non_player_udp_count - baseline['mean']) / std
            if z_score > threshold_sigma:
                is_anomalous = True

        was_active = self._port_ddos_active.get(key, False)

        if is_anomalous:
            self._port_anomaly_streak[key] = self._port_anomaly_streak.get(key, 0) + 1

            if not was_active and self._port_anomaly_streak[key] >= consecutive_ticks:
                self._port_ddos_active[key] = True
                await self._on_ddos_start_udp(server_name, port, ps, players,
                                              non_player_udp_count, baseline, config)
            elif was_active:
                cooldown_minutes = config.get('alert_cooldown_minutes', 15)
                last_alert = self._port_alert_cooldown.get(key)
                if not last_alert or (now - last_alert).total_seconds() >= cooldown_minutes * 60:
                    self._port_alert_cooldown[key] = now
                    await self._raise_ddos_alert_udp(server_name, port, ps, players,
                                                     non_player_udp_count, baseline)
        else:
            self._port_anomaly_streak[key] = 0

            if was_active:
                recovery_count = self._port_anomaly_streak.get(key + ('_recovery',), 0) + 1
                self._port_anomaly_streak[key + ('_recovery',)] = recovery_count
                if recovery_count >= recovery_ticks:
                    self._port_ddos_active[key] = False
                    self._port_anomaly_streak.pop(key + ('_recovery',), None)
                    self._port_alert_cooldown.pop(key, None)
                    await self._on_ddos_end_udp(server_name, port, ps, players,
                                                non_player_udp_count, baseline)
            else:
                self._port_anomaly_streak.pop(key + ('_recovery',), None)

        if not is_anomalous:
            self._update_welford_single(baseline, non_player_udp_count)

    async def _on_ddos_start(
        self,
        server_name: str,
        port: int,
        proto: str,
        ps: 'PortStats',
        players: int,
        excess_conns: int,
        baseline: dict,
        config: dict,
        action_override: str | None = None,
    ) -> None:
        """Called when a DDoS attack is first confirmed on a port. Override for custom actions."""
        std = math.sqrt(baseline['m2'] / baseline['count']) if baseline['count'] > 1 else 0

        title = f"🔴 DDoS START on {server_name}\n({proto.upper()} port {port})"
        message = "DDoS attack confirmed"
        fields = [
            ("Active players:", f"{players}"),
            ("TCP connections:", f"{ps.tcp_conns}"),
            ("Expected (players):", f"{players}"),
            ("Excess connections:", f"{excess_conns}"),
            ("Baseline excess avg:", f"{round(baseline['mean'], 1)}"),
            ("StdDev:", f"{round(std, 1)}"),
            ("Baseline samples:", f"{baseline['count']}"),
            ("Consecutive anomaly ticks:", f"{config.get('consecutive_ticks', 3)}")
        ]
        self.log.warning(title)
        try:
            server = self.bus.servers.get(server_name)
            await self.bot.alert(title=title, message=message, server=server, fields=fields)
            await self.bot.audit(
                f"**DDoS START** on {server_name} ({proto.upper()} {port}): "
                f"{ps.tcp_conns} TCP conns, {players} players, {excess_conns} excess"
            )
        except Exception as ex:
            self.log.debug("Failed to send DDoS start alert: %s", ex)
        # Firewall blocking (action='block')
        _action = action_override or self.get_config().get('ddos_detection', {}).get('action')
        if self._ddos_helper and _action == 'block':
            try:
                player_ips = await asyncio.to_thread(_get_tcp_player_ips, {port})
                ips = list(player_ips.get(port, set()))
                if ips:
                    await self._ddos_block(server_name, port, proto, ips)
                    self.log.info(f"DDoS block: {len(ips)} player IPs allowed on {proto}/{port}")
                else:
                    self.log.info(f"DDoS block: no player IPs online for {proto}/{port}, creating block with whitelist only")
                    await self._ddos_block(server_name, port, proto, [])
                # Start tailing dcs.log for new TCP connects (UDP block only)
                if proto == 'udp':
                    self._start_log_tail(server_name, port)
            except Exception as ex:
                self.log.warning(f"DDoS block failed: {ex}")

    async def _on_ddos_end(
        self,
        server_name: str,
        port: int,
        proto: str,
        ps: 'PortStats',
        players: int,
        excess_conns: int,
        baseline: dict,
        action_override: str | None = None,
    ) -> None:
        """Called when a DDoS attack is confirmed over on a port. Override for custom actions."""
        std = math.sqrt(baseline['m2'] / baseline['count']) if baseline['count'] > 1 else 0

        title = f"🟢 DDoS END on {server_name}\n({proto.upper()} port {port})"
        message = "DDoS attack ended — traffic returned to normal"
        fields = [
            ("Active players:", f"{players}"),
            ("TCP connections:", f"{ps.tcp_conns}"),
            ("Expected (players):", f"{players}"),
            ("Excess connections:", f"{excess_conns}"),
            ("Baseline excess avg:", f"{round(baseline['mean'], 1)}"),
            ("StdDev:", f"{round(std, 1)}"),
            ("Baseline samples:", f"{baseline['count']}")
        ]
        self.log.info(title)
        try:
            server = self.bus.servers.get(server_name)
            await self.bot.alert(title=title, message=message, server=server, fields=fields)
            await self.bot.audit(
                f"**DDoS END** on {server_name} ({proto.upper()} {port}): "
                f"traffic normalized ({ps.tcp_conns} TCP conns, {excess_conns} excess)"
            )
        except Exception as ex:
            self.log.debug("Failed to send DDoS end alert: %s", ex)
        # Firewall unblock (action='block')
        _action = action_override or self.get_config().get('ddos_detection', {}).get('action')
        if self._ddos_helper and _action == 'block':
            try:
                await self._ddos_unblock(server_name, port, proto)
            except Exception as ex:
                self.log.warning(f"DDoS unblock failed: {ex}")

    async def _raise_ddos_alert(
        self,
        server_name: str,
        port: int,
        proto: str,
        ps: 'PortStats',
        players: int,
        excess_conns: int,
        baseline: dict,
    ) -> None:
        """Send a periodic DDoS update alert (while an attack is ongoing)."""
        std = math.sqrt(baseline['m2'] / baseline['count']) if baseline['count'] > 1 else 0

        title = f"⚠️ DDoS ongoing on {server_name} ({proto.upper()} port {port})"
        message = "DDoS attack continues"
        fields = [
            ("Active players:", f"{players}"),
            ("TCP connections:", f"{ps.tcp_conns}"),
            ("Expected (players):", f"{players}"),
            ("Excess connections:", f"{excess_conns}"),
            ("Baseline excess avg:", f"{round(baseline['mean'], 1)}"),
            ("StdDev:", f"{round(std, 1)}"),
        ]
        self.log.warning(title)
        try:
            server = self.bus.servers.get(server_name)
            await self.bot.alert(title=title, message=message, server=server, fields=fields)
        except Exception as ex:
            self.log.debug("Failed to send DDoS update alert: %s", ex)

    async def _raise_ddos_alert_udp(
        self,
        server_name: str,
        port: int,
        ps: 'PortStats',
        players: int,
        non_player_udp_count: int,
        baseline: dict,
    ) -> None:
        """Send a periodic UDP DDoS update alert (while an attack is ongoing)."""
        std = math.sqrt(baseline['m2'] / baseline['count']) if baseline['count'] > 1 else 0

        title = f"⚠️ DDoS ongoing on {server_name} (UDP port {port})"
        message = "UDP DDoS attack continues"
        fields = [
            ("Active players:", f"{players}"),
            ("UDP sources (non-player):", f"{non_player_udp_count}"),
            ("Baseline avg:", f"{round(baseline['mean'], 1)}"),
            ("StdDev:", f"{round(std, 1)}"),
        ]
        self.log.warning(title)
        try:
            server = self.bus.servers.get(server_name)
            await self.bot.alert(title=title, message=message, server=server, fields=fields)
        except Exception as ex:
            self.log.debug("Failed to send UDP DDoS update alert: %s", ex)

    # ------------------------------------------------------------------
    # UDP DDoS alert methods (Signal 2: non-player UDP source IPs)
    # ------------------------------------------------------------------

    async def _on_ddos_start_udp(
        self,
        server_name: str,
        port: int,
        ps: 'PortStats',
        players: int,
        non_player_udp_count: int,
        baseline: dict,
        config: dict,
        action_override: str | None = None,
    ) -> None:
        """Called when UDP DDoS is first confirmed on a port."""
        std = math.sqrt(baseline['m2'] / baseline['count']) if baseline['count'] > 1 else 0

        title = f"🔴 DDoS START on {server_name}\n(UDP port {port})"
        message = "UDP DDoS attack confirmed — non-player flood detected"
        fields = [
            ("Active players:", f"{players}"),
            ("TCP player IPs:", f"{players}"),
            ("UDP sources (non-player):", f"{non_player_udp_count}"),
            ("Baseline avg:", f"{round(baseline['mean'], 1)}"),
            ("StdDev:", f"{round(std, 1)}"),
            ("Baseline samples:", f"{baseline['count']}"),
            ("Consecutive anomaly ticks:", f"{config.get('consecutive_ticks', 3)}"),
        ]
        self.log.warning(title)
        try:
            server = self.bus.servers.get(server_name)
            await self.bot.alert(title=title, message=message, server=server, fields=fields)
            await self.bot.audit(
                f"**DDoS START** on {server_name} (UDP {port}): "
                f"{non_player_udp_count} non-player UDP sources "
                f"(baseline {round(baseline['mean'], 1)}±{round(std, 1)})"
            )
        except Exception as ex:
            self.log.debug("Failed to send UDP DDoS start alert: %s", ex)
        # Firewall blocking (action='block')
        _action = action_override or self.get_config().get('ddos_detection', {}).get('action')
        if self._ddos_helper and _action == 'block':
            try:
                player_ips = await asyncio.to_thread(_get_tcp_player_ips, {port})
                ips = list(player_ips.get(port, set()))
                if ips:
                    await self._ddos_block(server_name, port, 'udp', ips)
                    self.log.info(f"DDoS block UDP: {len(ips)} player IPs allowed on udp/{port}")
                else:
                    self.log.info(f"DDoS block UDP: no player IPs online for udp/{port}, creating block with whitelist only")
                    await self._ddos_block(server_name, port, 'udp', [])
                # Start tailing dcs.log for new TCP connects
                self._start_log_tail(server_name, port)
            except Exception as ex:
                self.log.warning(f"DDoS block UDP failed: {ex}")

    async def _on_ddos_end_udp(
        self,
        server_name: str,
        port: int,
        ps: 'PortStats',
        players: int,
        non_player_udp_count: int,
        baseline: dict,
        action_override: str | None = None,
    ) -> None:
        """Called when UDP DDoS is confirmed over on a port."""
        std = math.sqrt(baseline['m2'] / baseline['count']) if baseline['count'] > 1 else 0

        title = f"🟢 DDoS END on {server_name}\n(UDP port {port})"
        message = "UDP DDoS ended — non-player UDP sources returned to normal"
        fields = [
            ("Active players:", f"{players}"),
            ("UDP sources (non-player):", f"{non_player_udp_count}"),
            ("Baseline avg:", f"{round(baseline['mean'], 1)}"),
            ("StdDev:", f"{round(std, 1)}"),
            ("Baseline samples:", f"{baseline['count']}"),
        ]
        self.log.info(title)
        try:
            server = self.bus.servers.get(server_name)
            await self.bot.alert(title=title, message=message, server=server, fields=fields)
            await self.bot.audit(
                f"**DDoS END** on {server_name} (UDP {port}): "
                f"non-player UDP sources normalized to {non_player_udp_count}"
            )
        except Exception as ex:
            self.log.debug("Failed to send UDP DDoS end alert: %s", ex)
        # Firewall unblock (action='block')
        _action = action_override or self.get_config().get('ddos_detection', {}).get('action')
        if self._ddos_helper and _action == 'block':
            try:
                await self._ddos_unblock(server_name, port, 'udp')
            except Exception as ex:
                self.log.warning(f"DDoS unblock UDP failed: {ex}")
        # Stop tailing dcs.log
        self._stop_log_tail(server_name)

    @proxy
    async def simulate_ddos(self, server: Server, port: int, protocol: str = 'udp',
                            duration: int = 30) -> dict:
        """
        Simulate a DDoS attack on a port for testing.
        Triggers the full DDoS detection callback (alert + block + log tail)
        without needing real attack traffic.
        """
        server_name = server.name
        if protocol == 'udp':
            if (server_name, port, 'udp') in self._port_ddos_active:
                return {"status": "already_active", "server": server_name, "port": port, "protocol": "udp"}
        else:
            if (server_name, port, 'tcp') in self._port_ddos_active:
                return {"status": "already_active", "server": server_name, "port": port, "protocol": "tcp"}

        # Build a fake PortStats object with simulated values
        ps = PortStats(
            port=port,
            protocol=protocol,
            tcp_conns=50 if protocol == 'tcp' else 4,
            udp_conns=0 if protocol == 'tcp' else 50,
            bytes_in=999999,
            bytes_out=999999,
            non_player_udp_ips=50 if protocol == 'udp' else 0,
        )

        # Fake baseline (low mean so the fake values look anomalous)
        baseline = {'mean': 5.0, 'm2': 10.0, 'count': 10}

        if protocol == 'udp':
            await self._on_ddos_start_udp(
                server_name=server_name,
                port=port,
                ps=ps,
                players=4,
                non_player_udp_count=ps.non_player_udp_ips,
                baseline=baseline,
                config=self.get_config().get('ddos_detection', {}),
                action_override='block',
            )
        else:
            excess = ps.tcp_conns - 4
            await self._on_ddos_start(
                server_name=server_name,
                port=port,
                proto=protocol,
                ps=ps,
                players=4,
                excess_conns=excess,
                baseline=baseline,
                config=self.get_config().get('ddos_detection', {}),
                action_override='block',
            )

        # Schedule auto-stop if duration > 0
        if duration > 0:
            asyncio.create_task(self._auto_stop_simulate(server_name, port, protocol, duration))

        return {
            "status": "started",
            "server": server_name,
            "port": port,
            "protocol": protocol,
            "auto_stop_seconds": duration if duration > 0 else None,
        }

    @proxy
    async def stop_simulate(self, server: Server, protocol: str = 'udp') -> dict:
        """
        Manually stop a running simulation.
        """
        server_name = server.name
        # Find any port with active simulation for this server+protocol
        if protocol == 'udp':
            key_to_stop = None
            for key in self._port_ddos_active:
                if key[0] == server_name and key[2] == 'udp':
                    key_to_stop = key
                    break
            if key_to_stop is None:
                return {"status": "not_found", "server": server_name, "protocol": protocol}
            port = key_to_stop[1]
            self._port_ddos_active.pop(key_to_stop, None)
            ps = PortStats(port=port, protocol='udp', tcp_conns=4, udp_conns=2,
                           non_player_udp_ips=2)
            await self._on_ddos_end_udp(
                server_name=server_name,
                port=port,
                ps=ps,
                players=4,
                non_player_udp_count=2,
                baseline={'mean': 5.0, 'm2': 10.0, 'count': 10},
                action_override='block'
            )
        else:
            key_to_stop = None
            for key in self._port_ddos_active:
                if key[0] == server_name and key[2] == 'tcp':
                    key_to_stop = key
                    break
            if key_to_stop is None:
                return {"status": "not_found", "server": server_name, "protocol": protocol}
            port = key_to_stop[1]
            self._port_ddos_active.pop(key_to_stop, None)
            ps = PortStats(port=port, protocol='tcp', tcp_conns=4, udp_conns=0)
            await self._on_ddos_end(
                server_name=server_name,
                port=port,
                proto=protocol,
                ps=ps,
                players=4,
                excess_conns=0,
                baseline={'mean': 5.0, 'm2': 10.0, 'count': 10},
                action_override='block'
            )
        return {"status": "stopped", "server": server_name, "port": port, "protocol": protocol}

    @proxy
    async def ddos_whitelist(self, node: Node, ip: str) -> str:
        """
        Add an IP to the in-memory DDoS whitelist on this node.
        """
        config = self.get_config()
        ddos_cfg = config.setdefault('ddos_detection', {})
        whitelist = ddos_cfg.setdefault('whitelist', [])
        if ip in whitelist:
            return f"IP {ip} is already whitelisted."
        whitelist.append(ip)
        return f"IP {ip} whitelisted."

    @proxy
    async def ddos_unwhitelist(self, node: Node, ip: str) -> str:
        """
        Remove an IP from the in-memory DDoS whitelist on this node.
        """
        config = self.get_config()
        ddos_cfg = config.get('ddos_detection', {})
        whitelist = ddos_cfg.get('whitelist', [])
        if ip not in whitelist:
            return f"IP {ip} is not in the whitelist."
        whitelist.remove(ip)
        return f"IP {ip} removed from whitelist."

    @proxy
    async def ddos_blacklist(self, node: Node, ip: str) -> str:
        """
        Permanently block an IP address via Windows Firewall on this node.
        """
        error = await self.ensure_ddos_helper(node)
        if error:
            return error
        try:
            response = await self._send_helper_command(f"block_ip {ip}")
            if response.startswith('OK'):
                return f"IP {ip} permanently blocked. {response[3:]}"
            else:
                return f"Failed to block {ip}: {response}"
        except Exception as ex:
            return f"Error blocking {ip}: {ex}"

    @proxy
    async def ddos_unblacklist(self, node: Node, ip: str) -> str:
        """
        Remove an IP from the permanent block list on this node.
        """
        if not self._ddos_helper or not self._ddos_helper.is_running():
            return "DDoS helper is not running."
        try:
            response = await self._send_helper_command(f"unblock_ip {ip}")
            if response.startswith('OK'):
                return f"IP {ip} unblocked. {response[3:]}"
            else:
                return f"Failed to unblock {ip}: {response}"
        except Exception as ex:
            return f"Error unblocking {ip}: {ex}"

    async def _auto_stop_simulate(self, server_name: str, port: int, protocol: str, delay: int):
        """Auto-stop a simulated DDoS after delay seconds."""
        await asyncio.sleep(delay)
        if protocol == 'udp':
            if (server_name, port, 'udp') in self._port_ddos_active:
                ps = PortStats(port=port, protocol='udp', tcp_conns=4, udp_conns=2,
                               non_player_udp_ips=2)
                await self._on_ddos_end_udp(
                    server_name=server_name,
                    port=port,
                    ps=ps,
                    players=4,
                    non_player_udp_count=2,
                    baseline={'mean': 5.0, 'm2': 10.0, 'count': 10},
                    action_override='block'
                )
        else:
            if (server_name, port, 'tcp') in self._port_ddos_active:
                ps = PortStats(port=port, protocol='tcp', tcp_conns=4, udp_conns=0)
                await self._on_ddos_end(
                    server_name=server_name,
                    port=port,
                    proto=protocol,
                    ps=ps,
                    players=4,
                    excess_conns=0,
                    baseline={'mean': 5.0, 'm2': 10.0, 'count': 10},
                    action_override='block'
                )

    @tasks.loop(minutes=1.0)
    async def monitoring(self):
        try:
            tasks = []
            config = self.get_config().get('ddos_detection', {})
            if config.get('enabled', False):
                tasks.append(self._collect_port_traffic())
                tasks.append(self._check_node_bandwidth())

            # Run all tasks concurrently
            await asyncio.gather(*tasks)
        except Exception as ex:
            self.log.exception(ex)

    @monitoring.before_loop
    async def before_loop(self):
        if self.node.master and self.bot and self.bot.bot:
            await self.bot.bot.wait_until_ready()
