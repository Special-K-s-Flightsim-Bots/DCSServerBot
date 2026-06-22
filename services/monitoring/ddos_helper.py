#!/usr/bin/env python3
"""
DDoS Helper — Windows Firewall rule manager for DCSServerBot.

Runs with administrator privileges (started via ShellExecuteExW / runas).
Communicates with the bot via a named pipe.

Protocol (line-based, newline-delimited):
  Client -> Server:
    restrict <rule_name> <tcp|udp> <port> <ip1,ip2,...>
    restore <rule_name>
    block_ip <ip>
    unblock_ip <ip>
    list_blocked
    exit

  Server -> Client:
    OK <message>
    ERROR <message>
"""
import sys
import os
import subprocess
import threading
import time
import logging

# Set up file logging to <bot_root>/logs/ddos_helper.log
# This file lives in <bot_root>/services/monitoring/, so go up 3 levels
_bot_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_log_dir = os.path.join(_bot_root, 'logs')
os.makedirs(_log_dir, exist_ok=True)
_log_path = os.path.join(_log_dir, 'ddos_helper.log')

_logger = logging.getLogger('ddos_helper')
_logger.setLevel(logging.DEBUG)
_logger.propagate = False
try:
    _fh = logging.FileHandler(_log_path, encoding='utf-8')
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter(
        fmt='%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    _logger.addHandler(_fh)
except Exception:
    import traceback
    crash_log = os.path.join(os.environ.get('TEMP', '.'), 'ddos_helper_crash.log')
    with open(crash_log, 'a') as f:
        f.write(traceback.format_exc() + '\n')
    raise


PIPE_NAME = r'\\.\pipe\ddos_helper'


def _resolve_dcs_exe() -> str:
    """
    Resolve the DCS executable path.

    Priority:
      1. DCS installation path passed as CLI arg (from node config)
      2. DCS_Server.exe in that path's bin/ directory (server install)
      3. DCS_server.exe in that path's bin/ directory (alternate naming)
      4. DCS.exe in that path's bin/ directory (client install)
    """
    dcs_install = ''
    if len(sys.argv) > 1 and sys.argv[1]:
        dcs_install = sys.argv[1]

    if dcs_install:
        # Expand environment variables (e.g. %ProgramFiles%)
        dcs_install = os.path.expandvars(dcs_install)
        # Try DCS_Server.exe first (server installation), then DCS.exe (client)
        for exe_name in ('DCS_Server.exe', 'DCS_server.exe', 'DCS.exe'):
            path = os.path.join(dcs_install, 'bin', exe_name)
            if os.path.exists(path):
                return path
        # Log diagnostic info
        _logger.warning("DCS executable not found. Install path: %s", dcs_install)
        for exe_name in ('DCS_Server.exe', 'DCS_server.exe', 'DCS.exe'):
            path = os.path.join(dcs_install, 'bin', exe_name)
            _logger.warning("  Tried: %s -> exists=%s", path, os.path.exists(path))
        bin_dir = os.path.join(dcs_install, 'bin')
        if os.path.isdir(bin_dir):
            _logger.warning("  Contents of %s: %s", bin_dir, os.listdir(bin_dir))
        else:
            _logger.warning("  bin dir does not exist: %s", bin_dir)

    # Fallback: should not normally be reached if node config is correct
    return ''


def run_netsh(args: list[str]) -> tuple[int, str, str]:
    """Run a netsh command and return (returncode, stdout, stderr)."""
    cmd = ['netsh', 'advfirewall', 'firewall'] + args
    _logger.debug("netsh: %s", ' '.join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        if result.returncode == 0:
            _logger.debug("netsh OK: %s", result.stdout.strip()[:200])
        else:
            _logger.warning("netsh RC=%d: %s", result.returncode, result.stderr.strip()[:200])
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as ex:
        _logger.error("netsh exception: %s", ex)
        return -1, '', str(ex)


def rule_exists(rule_name: str) -> bool:
    """Check if a firewall rule exists."""
    rc, out, _ = run_netsh(['show', 'rule', f'name={rule_name}'])
    return rc == 0 and 'No rules match the specified criteria' not in out


def restrict_rule(rule_name: str, protocol: str, port: int, allowed_ips: list[str]) -> tuple[bool, str]:
    """
    Create or update a per-instance firewall rule that allows only specific IPs.
    """
    _logger.info("restrict_rule: %s %s/%d ips=%s", rule_name, protocol, port, ','.join(allowed_ips))
    # Remove existing rule with this name if present
    if rule_exists(rule_name):
        _logger.info("restrict_rule: deleting existing rule %s", rule_name)
        rc, out, err = run_netsh(['delete', 'rule', f'name={rule_name}'])
        if rc != 0:
            return False, f"Failed to delete existing rule: {err or out}"

    # Resolve DCS exe path from node config (passed as CLI arg)
    exe_path = _resolve_dcs_exe()
    if not exe_path:
        return False, "Cannot find DCS executable. Check DCS installation path in node config."

    ip_list = ','.join(allowed_ips) if allowed_ips else None

    if ip_list:
        # Allow rule for known player IPs (+ whitelist)
        args = [
            'add', 'rule',
            f'name={rule_name}',
            'dir=in',
            'action=allow',
            'program=' + exe_path,
            'protocol=' + protocol,
            'localport=' + str(port),
            'remoteip=' + ip_list,
            'enable=yes',
            'profile=any',
        ]
    else:
        # Empty allow list → create a deny-all rule instead
        # (an allow rule with no remoteip means "any", which defeats the purpose)
        _logger.info("restrict_rule: empty IP list, creating deny-all rule for %s/%d",
                     protocol, port)
        args = [
            'add', 'rule',
            f'name={rule_name}',
            'dir=in',
            'action=block',
            'protocol=' + protocol,
            'localport=' + str(port),
            'enable=yes',
            'profile=any',
        ]

    rc, out, err = run_netsh(args)
    if rc == 0:
        if ip_list:
            msg = f"Rule '{rule_name}' created: {protocol}/{port} from {ip_list}"
        else:
            msg = f"Rule '{rule_name}' created: {protocol}/{port} (deny-all, no players/whitelist)"
        _logger.info("restrict_rule: %s", msg)
        return True, msg
    else:
        msg = f"Failed to create rule: {err or out}"
        _logger.error("restrict_rule: %s", msg)
        return False, msg


def restore_rule(rule_name: str) -> tuple[bool, str]:
    """Remove a per-instance firewall rule, restoring the global rule's effect."""
    _logger.info("restore_rule: %s", rule_name)
    if not rule_exists(rule_name):
        _logger.info("restore_rule: rule %s does not exist, nothing to do", rule_name)
        return True, f"Rule '{rule_name}' does not exist, nothing to restore"

    rc, out, err = run_netsh(['delete', 'rule', f'name={rule_name}'])
    if rc == 0:
        msg = f"Rule '{rule_name}' removed"
        _logger.info("restore_rule: %s", msg)
        return True, msg
    else:
        msg = f"Failed to remove rule: {err or out}"
        _logger.error("restore_rule: %s", msg)
        return False, msg


BLOCKED_RULE_NAME = 'DCS-blocked'


def _get_blocked_ips() -> list[str]:
    """Read the current list of blocked IPs from the DCS-blocked firewall rule."""
    rc, out, err = run_netsh(['show', 'rule', f'name={BLOCKED_RULE_NAME}'])
    if rc != 0 or 'No rules match the specified criteria' in out:
        return []
    # Parse the RemoteIP line from the netsh output
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('RemoteIP:'):
            ips_str = line.split(':', 1)[1].strip()
            if ips_str and ips_str != 'Any':
                return [ip.strip() for ip in ips_str.split(',')]
    return []


def _set_blocked_ips(ips: list[str]) -> tuple[bool, str]:
    """Create or update the DCS-blocked rule with the given IP list."""
    # Remove existing rule
    if rule_exists(BLOCKED_RULE_NAME):
        rc, out, err = run_netsh(['delete', 'rule', f'name={BLOCKED_RULE_NAME}'])
        if rc != 0:
            return False, f"Failed to remove existing rule: {err or out}"

    if not ips:
        return True, "Rule removed (no IPs to block)"

    ip_list = ','.join(ips)
    args = [
        'add', 'rule',
        f'name={BLOCKED_RULE_NAME}',
        'dir=in',
        'action=block',
        'remoteip=' + ip_list,
        'enable=yes',
        'profile=any',
    ]
    rc, out, err = run_netsh(args)
    if rc == 0:
        return True, f"Rule '{BLOCKED_RULE_NAME}' updated: blocking {ip_list}"
    else:
        return False, f"Failed to create rule: {err or out}"


def block_ip(ip: str) -> tuple[bool, str]:
    """
    Add an IP to the permanent block list.
    Uses a single 'DCS-blocked' firewall rule that accumulates IPs.
    """
    _logger.info("block_ip: %s", ip)
    ips = _get_blocked_ips()
    if ip in ips:
        _logger.info("block_ip: %s already blocked", ip)
        return True, f"IP {ip} is already in the block list"
    ips.append(ip)
    ok, msg = _set_blocked_ips(ips)
    if ok:
        _logger.info("block_ip: %s", msg)
    else:
        _logger.error("block_ip: %s", msg)
    return ok, msg


def unblock_ip(ip: str) -> tuple[bool, str]:
    """Remove an IP from the permanent block list."""
    _logger.info("unblock_ip: %s", ip)
    ips = _get_blocked_ips()
    if ip not in ips:
        _logger.info("unblock_ip: %s not in block list", ip)
        return True, f"IP {ip} is not in the block list"
    ips.remove(ip)
    if ips:
        ok, msg = _set_blocked_ips(ips)
    else:
        # No IPs left — remove the rule entirely
        if rule_exists(BLOCKED_RULE_NAME):
            rc, out, err = run_netsh(['delete', 'rule', f'name={BLOCKED_RULE_NAME}'])
            if rc == 0:
                msg = f"IP {ip} removed, rule deleted (no more blocked IPs)"
                _logger.info("unblock_ip: %s", msg)
                return True, msg
            else:
                msg = f"Failed to remove rule: {err or out}"
                _logger.error("unblock_ip: %s", msg)
                return False, msg
        msg = f"IP {ip} removed, no rule to delete"
        _logger.info("unblock_ip: %s", msg)
        return True, msg
    if ok:
        _logger.info("unblock_ip: %s", msg)
    else:
        _logger.error("unblock_ip: %s", msg)
    return ok, msg


def list_blocked_ips() -> tuple[bool, str]:
    """List all IPs in the permanent block list."""
    ips = _get_blocked_ips()
    if not ips:
        return True, "No blocked IPs"
    return True, ', '.join(ips)


def handle_command(line: str) -> str:
    """Parse and execute a command, return response string."""
    _logger.info("handle_command: %s", line)
    parts = line.strip().split(maxsplit=4)
    if not parts:
        return 'ERROR Empty command'

    cmd = parts[0].lower()

    if cmd == 'exit':
        _logger.info("handle_command: exit received")
        return 'OK Exiting'

    if cmd == 'restrict' and len(parts) in (4, 5):
        rule_name = parts[1]
        protocol = parts[2].lower()
        try:
            port = int(parts[3])
        except ValueError:
            return f'ERROR Invalid port: {parts[3]}'
        if protocol not in ('tcp', 'udp'):
            return f'ERROR Invalid protocol: {protocol} (use tcp or udp)'
        if len(parts) == 5:
            ips = [ip.strip() for ip in parts[4].split(',') if ip.strip()]
        else:
            # No IPs provided → deny-all rule
            ips = []
        ok, msg = restrict_rule(rule_name, protocol, port, ips)
        return f'{"OK" if ok else "ERROR"} {msg}'

    if cmd == 'restore' and len(parts) == 2:
        rule_name = parts[1]
        ok, msg = restore_rule(rule_name)
        return f'{"OK" if ok else "ERROR"} {msg}'

    if cmd == 'block_ip' and len(parts) == 2:
        ip = parts[1].strip()
        ok, msg = block_ip(ip)
        return f'{"OK" if ok else "ERROR"} {msg}'

    if cmd == 'unblock_ip' and len(parts) == 2:
        ip = parts[1].strip()
        ok, msg = unblock_ip(ip)
        return f'{"OK" if ok else "ERROR"} {msg}'

    if cmd == 'list_blocked' and len(parts) == 1:
        ok, msg = list_blocked_ips()
        return f'{"OK" if ok else "ERROR"} {msg}'

    _logger.warning("handle_command: unknown command: %s", line)
    return f'ERROR Unknown command: {line}'


def _create_security_attributes():
    """
    Create a SECURITY_ATTRIBUTES with a NULL DACL, allowing access from all
    integrity levels. This is needed because the helper runs elevated (high
    integrity) and the bot runs non-elevated (medium integrity).
    """
    import ctypes
    import ctypes.wintypes

    PSECURITY_DESCRIPTOR = ctypes.c_void_p
    SECURITY_DESCRIPTOR_MIN_LENGTH = 20

    # Initialize the security descriptor
    sd = ctypes.create_string_buffer(SECURITY_DESCRIPTOR_MIN_LENGTH)
    advapi32 = ctypes.windll.advapi32

    if not advapi32.InitializeSecurityDescriptor(
        sd, 1  # SECURITY_DESCRIPTOR_REVISION
    ):
        return None  # Fall back to default security

    # Set a NULL DACL (grants full access to everyone)
    if not advapi32.SetSecurityDescriptorDacl(
        sd,
        True,   # DACL present
        None,   # DACL = NULL → full access to all
        False   # DACL not defaulted
    ):
        return None

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", ctypes.wintypes.DWORD),
            ("lpSecurityDescriptor", PSECURITY_DESCRIPTOR),
            ("bInheritHandle", ctypes.wintypes.BOOL),
        ]

    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.lpSecurityDescriptor = ctypes.addressof(sd)
    sa.bInheritHandle = False
    return sa, sd  # Return both to keep sd alive


def pipe_server(stop_event: threading.Event):
    """Run the named pipe server, handling one client at a time."""
    import ctypes
    import ctypes.wintypes

    GENERIC_READ_WRITE = 0xC0000000
    OPEN_EXISTING = 3
    PIPE_ACCESS_DUPLEX = 0x00000003
    PIPE_TYPE_MESSAGE = 0x00000004
    PIPE_READMODE_MESSAGE = 0x00000002
    PIPE_WAIT = 0x00000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    ERROR_PIPE_CONNECTED = 535
    kernel32 = ctypes.windll.kernel32

    # Create security attributes with NULL DACL for cross-integrity-level access
    sa_result = _create_security_attributes()
    if sa_result:
        sa, sd = sa_result
        pSecurityAttributes = ctypes.byref(sa)
    else:
        pSecurityAttributes = None  # Fall back to default

    while not stop_event.is_set():
        # Create a named pipe instance
        hPipe = kernel32.CreateNamedPipeW(
            PIPE_NAME,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
            1,  # max instances
            4096, 4096,
            0,
            pSecurityAttributes,
        )

        if hPipe == INVALID_HANDLE_VALUE:
            if stop_event.is_set():
                break
            time.sleep(1)
            continue

        # Wait for a client to connect
        connected = kernel32.ConnectNamedPipe(hPipe, None)
        if not connected and ctypes.GetLastError() != ERROR_PIPE_CONNECTED:
            kernel32.CloseHandle(hPipe)
            time.sleep(0.5)
            continue

        # Read/write loop for this client
        try:
            while not stop_event.is_set():
                # Read a line from the pipe
                buf = ctypes.create_string_buffer(4096)
                bytes_read = ctypes.wintypes.DWORD(0)
                success = kernel32.ReadFile(hPipe, buf, 4096, ctypes.byref(bytes_read), None)

                if not success or bytes_read.value == 0:
                    break

                line = buf.value.decode('utf-8', errors='replace').strip()
                if not line:
                    continue

                response = handle_command(line)

                # Send response
                resp_bytes = (response + '\n').encode('utf-8')
                bytes_written = ctypes.wintypes.DWORD(0)
                kernel32.WriteFile(hPipe, resp_bytes, len(resp_bytes), ctypes.byref(bytes_written), None)

                if line.strip().lower() == 'exit':
                    stop_event.set()
                    break
        except Exception:
            pass
        finally:
            kernel32.DisconnectNamedPipe(hPipe)
            kernel32.CloseHandle(hPipe)

        # Small sleep before creating next pipe instance to avoid tight loop
        if not stop_event.is_set():
            time.sleep(0.1)


def main():
    _logger.info("DDoS Helper started. Listening on %s", PIPE_NAME)
    _logger.info("DCS executable: %s", _resolve_dcs_exe() or "NOT FOUND")

    stop_event = threading.Event()
    server_thread = threading.Thread(target=pipe_server, args=(stop_event,), daemon=True)
    server_thread.start()

    # Wait for stop
    try:
        while not stop_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    stop_event.set()
    server_thread.join(timeout=5)
    _logger.info("DDoS Helper stopped.")


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        crash_log = os.path.join(os.environ.get('TEMP', '.'), 'ddos_helper_crash.log')
        with open(crash_log, 'a') as f:
            f.write(traceback.format_exc() + '\n')
        raise
