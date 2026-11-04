#!/usr/bin/env python3
"""
Firewall Helper — Windows Firewall rule manager for DCSServerBot.

Runs with administrator privileges (started via ShellExecuteExW / runas).
Communicates with the bot via a named pipe.

Uses the Windows Firewall COM API (INetFwPolicy2) via comtypes — no netsh parsing,
no locale issues.
"""
import sys
import os
import threading
import time
import logging

# Set up file logging to <bot_root>/logs/firewall_helper.log
_bot_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_log_dir = os.path.join(_bot_root, 'logs')
os.makedirs(_log_dir, exist_ok=True)
_log_path = os.path.join(_log_dir, 'firewall_helper.log')

_logger = logging.getLogger('firewall_helper')
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
    crash_log = os.path.join(os.environ.get('TEMP', '.'), 'firewall_helper_crash.log')
    with open(crash_log, 'a') as f:
        f.write(traceback.format_exc() + '\n')
    raise

# ---------------------------------------------------------------------------
# Windows Firewall COM API via comtypes
# ---------------------------------------------------------------------------
_logger.info("Importing comtypes...")
try:
    import comtypes.client
    from comtypes import CLSCTX_ALL
    comtypes.CoInitialize()
    _logger.info("comtypes imported and COM initialized")
    _COM_AVAILABLE = True
except ImportError as _e:
    _logger.error("comtypes not installed: %s", _e)
    _COM_AVAILABLE = False
except Exception as _e:
    _logger.error("COM initialization failed: %s", _e, exc_info=True)
    _COM_AVAILABLE = False

# Firewall constants
NET_FW_ACTION_BLOCK = 0
NET_FW_ACTION_ALLOW = 1
NET_FW_RULE_DIR_IN = 1
NET_FW_PROFILE2_DOMAIN = 1
NET_FW_PROFILE2_PRIVATE = 2
NET_FW_PROFILE2_PUBLIC = 4
NET_FW_PROFILE2_ALL = NET_FW_PROFILE2_DOMAIN | NET_FW_PROFILE2_PRIVATE | NET_FW_PROFILE2_PUBLIC
NET_FW_IP_PROTOCOL_TCP = 6
NET_FW_IP_PROTOCOL_UDP = 17
NET_FW_IP_PROTOCOL_ANY = 256


def _get_fw_policy():
    """Get the Windows Firewall policy COM object."""
    return comtypes.client.CreateObject("HNetCfg.FwPolicy2", clsctx=CLSCTX_ALL)


def _fw_rule_exists(rule_name: str) -> bool:
    """Check if a firewall rule exists by name."""
    try:
        policy = _get_fw_policy()
        for rule in policy.Rules:
            if rule.Name == rule_name:
                return True
        return False
    except Exception as ex:
        _logger.error("rule_exists(%s) error: %s", rule_name, ex)
        return False


def _set_rule_enabled(rule_name: str, enabled: bool) -> tuple:
    """Enable or disable an existing firewall rule by name.

    Disables the first matching rule with the given name. Use
    _set_rule_enabled_obj() when you have a direct reference to the COM rule
    object to avoid matching the wrong rule among duplicates.
    """
    try:
        policy = _get_fw_policy()
        for rule in policy.Rules:
            if rule.Name == rule_name:
                return _set_rule_enabled_obj(rule, enabled)
        return False, f"Rule '{rule_name}' not found"
    except Exception as ex:
        _logger.error("set_rule_enabled(%s, %s) error: %s", rule_name, enabled, ex)
        return False, f"Failed to set rule '{rule_name}': {ex}"


def _set_rule_enabled_obj(rule, enabled: bool) -> tuple:
    """Enable or disable a firewall rule by COM object reference.

    Use this when you already have the rule object (e.g. from iterating
    policy.Rules) to ensure the exact rule is toggled, not a different rule
    with the same name.
    """
    try:
        rule.Enabled = enabled
        state = "enabled" if enabled else "disabled"
        return True, f"Rule '{rule.Name}' {state}"
    except Exception as ex:
        _logger.error("set_rule_enabled_obj(%s, %s) error: %s", rule.Name, enabled, ex)
        return False, f"Failed to set rule '{rule.Name}': {ex}"


def _fw_delete_rule(rule_name: str) -> tuple:
    """Delete a firewall rule by name. Returns (ok, msg)."""
    try:
        policy = _get_fw_policy()
        policy.Rules.Remove(rule_name)
        return True, f"Rule '{rule_name}' deleted"
    except Exception as ex:
        if "not found" in str(ex).lower() or "0x80070002" in str(ex):
            return True, f"Rule '{rule_name}' does not exist"
        return False, f"Failed to delete rule '{rule_name}': {ex}"


def _fw_create_rule(rule_name, direction, action, protocol, local_port=None,
                    remote_ips=None, program=None, enabled=True, description="") -> tuple:
    """Create a Windows Firewall rule using the COM API. Returns (ok, msg)."""
    try:
        policy = _get_fw_policy()
        rule = comtypes.client.CreateObject("HNetCfg.FWRule", clsctx=CLSCTX_ALL)
        rule.Name = rule_name
        rule.Description = description or rule_name
        rule.Direction = direction
        rule.Action = action
        rule.Protocol = protocol
        rule.Profiles = NET_FW_PROFILE2_ALL
        rule.Enabled = enabled
        rule.Grouping = "DCSServerBot"
        if local_port is not None:
            rule.LocalPorts = str(local_port)
        if remote_ips is not None:
            rule.RemoteAddresses = remote_ips
        if program is not None:
            rule.ApplicationName = program
        policy.Rules.Add(rule)
        return True, f"Rule '{rule_name}' created"
    except Exception as ex:
        return False, f"Failed to create rule '{rule_name}': {ex}"


# ---------------------------------------------------------------------------
# DCS exe resolution (unchanged from working version)
# ---------------------------------------------------------------------------

def normalize_path(path: str) -> str:
    """Normalize a Windows path: expand vars, normpath, lowercase."""
    if not path:
        return ""
    try:
        # Expand environment variables like %ProgramFiles%
        path = os.path.expandvars(path)
        # Convert / to \ and handle double slashes
        path = os.path.normpath(path)
        return path.lower()
    except Exception:
        return path.lower()


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
        dcs_install = os.path.expandvars(dcs_install)
        for exe_name in ('DCS_Server.exe', 'DCS_server.exe', 'DCS.exe'):
            path = os.path.join(dcs_install, 'bin', exe_name)
            if os.path.exists(path):
                return path
        _logger.warning("DCS executable not found. Install path: %s", dcs_install)
        for exe_name in ('DCS_Server.exe', 'DCS_server.exe', 'DCS.exe'):
            path = os.path.join(dcs_install, 'bin', exe_name)
            _logger.warning("  Tried: %s -> exists=%s", path, os.path.exists(path))
        bin_dir = os.path.join(dcs_install, 'bin')
        if os.path.isdir(bin_dir):
            _logger.warning("  Contents of %s: %s", bin_dir, os.listdir(bin_dir))
        else:
            _logger.warning("  bin dir does not exist: %s", bin_dir)

    return ''


# ---------------------------------------------------------------------------
# Firewall rule management (using COM API instead of netsh)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Manual rule suppression (for when manage_rules is False)
# ---------------------------------------------------------------------------

# Map rule key -> reference count
_suppressed_manual_rules = {}
# Map (protocol, port) -> list of rule keys
_port_to_manual_rules = {}


def _rule_key(rule):
    """Create a uniquely-ish identifying key for a firewall rule."""
    return rule.Name, rule.ApplicationName, rule.LocalPorts, rule.Protocol


def _port_in_range(port: int, port_range: str) -> bool:
    """Check if a port is within a firewall port specification (single, list, or range)."""
    if '-' in port_range:
        try:
            start, end = map(int, port_range.split('-'))
            return start <= port <= end
        except ValueError:
            return False
    try:
        return int(port_range) == port
    except ValueError:
        return False


def _matches_port(port: int, local_ports: str) -> bool:
    """Check if a port matches the LocalPorts specification of a rule."""
    if not local_ports or local_ports == '*':
        return True
    return any(_port_in_range(port, p.strip()) for p in local_ports.split(','))


def _suppress_manual_rules(protocol: int, port: int) -> list:
    """Find and disable manual firewall rules matching the port or DCS exe."""
    exe_path = _resolve_dcs_exe()
    normalized_exe = normalize_path(exe_path) if exe_path else None

    suppressed_keys = []
    try:
        policy = _get_fw_policy()
        for rule in policy.Rules:
            # Ignore our own rules
            if rule.Grouping == "DCSServerBot" or rule.Name.startswith("DCS-"):
                continue

            matches = False
            # Match by ApplicationName (DCS exe)
            if normalized_exe and rule.ApplicationName:
                if normalize_path(rule.ApplicationName) == normalized_exe:
                    matches = True

            # Match by Port
            if not matches and rule.LocalPorts:
                if _matches_port(port, rule.LocalPorts):
                    if rule.Protocol == protocol or rule.Protocol == NET_FW_IP_PROTOCOL_ANY:
                        matches = True

            if matches:
                key = _rule_key(rule)
                if rule.Enabled:
                    # If not already suppressed by another port attack, disable it
                    if _suppressed_manual_rules.get(key, 0) == 0:
                        _set_rule_enabled_obj(rule, False)
                        _logger.info("Suppressed manual rule: %s (matches port %d or exe)", rule.Name, port)
                    
                    # Increment ref count for this port/protocol
                    _suppressed_manual_rules[key] = _suppressed_manual_rules.get(key, 0) + 1
                    suppressed_keys.append(key)
                elif key in _suppressed_manual_rules:
                    # Rule is already disabled AND it is in our suppression list, 
                    # meaning it was disabled by another port attack.
                    _suppressed_manual_rules[key] += 1
                    suppressed_keys.append(key)
                # Else: rule is disabled but NOT in our suppression list, 
                # meaning it was disabled by the user manually. We leave it alone.
        
        if suppressed_keys:
            _port_to_manual_rules[(protocol, port)] = suppressed_keys

    except Exception as ex:
        _logger.error("Error suppressing manual rules: %s", ex)

    return [k[0] for k in suppressed_keys]


def _restore_manual_rules(protocol: int, port: int) -> list:
    """Decrement suppression ref count and re-enable manual rules if it reaches zero."""
    keys = _port_to_manual_rules.pop((protocol, port), [])
    restored = []
    if not keys:
        return restored

    try:
        policy = _get_fw_policy()
        # Collect all rules first to avoid multiple iterations? 
        # Actually, iterating once is safer.
        for rule in policy.Rules:
            key = _rule_key(rule)
            if key in keys:
                _suppressed_manual_rules[key] = max(0, _suppressed_manual_rules.get(key, 0) - 1)
                if _suppressed_manual_rules[key] == 0:
                    # No more attacks require this rule suppressed -> restore it
                    if not rule.Enabled:
                        _set_rule_enabled_obj(rule, True)
                        restored.append(rule.Name)
                        _logger.info("Restored manual rule: %s", rule.Name)
    except Exception as ex:
        _logger.error("Error restoring manual rules: %s", ex)
    
    return restored


def rule_exists(rule_name: str) -> bool:
    """Check if a firewall rule exists."""
    if not _COM_AVAILABLE:
        _logger.error("comtypes not available, cannot check rule")
        return False
    return _fw_rule_exists(rule_name)


def restrict_rule(rule_name: str, protocol: str, port: int, allowed_ips: list[str]) -> tuple:
    """
    Per-port DDoS block:
    1. Disable the base allow-all rule for this port/protocol
    2. Remove any existing deny rule with this name
    3. Create a deny-all rule on this port
    4. Create a single allow-all rule with all player IPs in RemoteAddresses
    """
    if not _COM_AVAILABLE:
        return False, "comtypes not available"
    _logger.info("restrict_rule: %s %s/%d ips=%s", rule_name, protocol, port, ','.join(allowed_ips))

    proto_num = NET_FW_IP_PROTOCOL_TCP if protocol == 'tcp' else NET_FW_IP_PROTOCOL_UDP
    base_rule = f"DCS-base-{protocol}-{port}"

    # Step 1: Disable the base allow-all rule for this port/protocol
    if _fw_rule_exists(base_rule):
        _set_rule_enabled(base_rule, False)
        _logger.info("restrict_rule: disabled base rule %s", base_rule)

    # Step 1b: Suppress manual rules matching this port or DCS exe
    suppressed = _suppress_manual_rules(proto_num, port)
    if suppressed:
        _logger.info("restrict_rule: suppressed %d manual rules for %s/%d", len(suppressed), protocol, port)

    # Step 2: Remove any existing deny rule with this name
    if _fw_rule_exists(rule_name):
        _fw_delete_rule(rule_name)

    # Step 3: Create deny-all rule
    ok, msg = _fw_create_rule(
        rule_name, NET_FW_RULE_DIR_IN, NET_FW_ACTION_BLOCK, proto_num,
        local_port=port, enabled=True,
        description=f"DCSServerBot deny-all {protocol}/{port}",
    )
    if not ok:
        return False, f"Failed to create deny-all rule: {msg}"

    results = [f"Deny-all '{rule_name}' created"]

    # Step 4: Create a single allow rule with all player IPs
    if allowed_ips:
        allow_name = f"DCS-allow-{protocol}-{port}-players"
        # Remove stale if exists
        if _fw_rule_exists(allow_name):
            _fw_delete_rule(allow_name)
        ip_list = ','.join(allowed_ips)
        ok, msg = _fw_create_rule(
            allow_name, NET_FW_RULE_DIR_IN, NET_FW_ACTION_ALLOW, proto_num,
            local_port=port, remote_ips=ip_list, enabled=True,
            description=f"DCSServerBot allow {len(allowed_ips)} IPs on {protocol}/{port}",
        )
        if not ok:
            results.append(f"WARNING: failed allow rule: {msg}")
            _logger.warning("Failed allow rule for %s/%s: %s", protocol, port, msg)
        else:
            results.append(f"Allow rule with {len(allowed_ips)} IPs created")

    _logger.info("restrict_rule: %s", "; ".join(results))
    return True, "; ".join(results)


def restore_rule(rule_name: str, base_rule: str = None) -> tuple:
    """
    Per-port DDoS unblock:
    1. Remove the deny-all rule
    2. Remove the single per-port allow rule (all player IPs)
    3. Re-enable the base allow-all rule
    """
    if not _COM_AVAILABLE:
        return False, "comtypes not available"
    _logger.info("restore_rule: %s", rule_name)

    # Derive protocol and port from rule_name (e.g. "DCS-deny-udp-1308")
    parts = rule_name.split('-')
    if len(parts) >= 4 and parts[0] == 'DCS' and parts[1] == 'deny':
        protocol = parts[2]
        port = int(parts[3])
    else:
        # Fallback: just delete the named rule
        _logger.warning("restore_rule: could not parse protocol/port from %s", rule_name)
        if _fw_rule_exists(rule_name):
            ok, msg = _fw_delete_rule(rule_name)
            return ok, msg if ok else f"Failed: {msg}"
        return True, f"Rule '{rule_name}' does not exist"

    results = []

    # Step 1: Remove deny-all rule
    if _fw_rule_exists(rule_name):
        ok, msg = _fw_delete_rule(rule_name)
        results.append(f"Deny-all '{rule_name}' removed")
    else:
        results.append(f"Deny-all '{rule_name}' did not exist")

    # Step 2: Remove the single per-port allow rule
    allow_name = f"DCS-allow-{protocol}-{port}-players"
    if _fw_rule_exists(allow_name):
        _fw_delete_rule(allow_name)
        results.append(f"Allow rule '{allow_name}' removed")
    else:
        results.append(f"Allow rule '{allow_name}' did not exist")

    # Step 3: Re-enable base allow-all rule
    if base_rule is None:
        base_rule = f"DCS-base-{protocol}-{port}"
    if _fw_rule_exists(base_rule):
        _set_rule_enabled(base_rule, True)
        results.append(f"Base rule '{base_rule}' re-enabled")
    else:
        results.append(f"Base rule '{base_rule}' did not exist")

    # Step 4: Restore manual rules matching this port or DCS exe
    proto_num = NET_FW_IP_PROTOCOL_TCP if protocol == 'tcp' else NET_FW_IP_PROTOCOL_UDP
    restored = _restore_manual_rules(proto_num, port)
    if restored:
        results.append(f"Restored {len(restored)} manual rules")

    _logger.info("restore_rule: %s", "; ".join(results))
    return True, "; ".join(results)


BLOCKED_RULE_NAME = 'DCS-blocked'


def _get_blocked_ips() -> list:
    """Read the current list of blocked IPs from the DCS-blocked firewall rule."""
    if not _COM_AVAILABLE:
        return []
    try:
        policy = _get_fw_policy()
        for rule in policy.Rules:
            if rule.Name == BLOCKED_RULE_NAME and rule.Enabled and rule.RemoteAddresses:
                return [ip.strip() for ip in rule.RemoteAddresses.split(',')]
        return []
    except Exception as ex:
        _logger.error("Error reading blocked IPs: %s", ex)
        return []


def _set_blocked_ips(ips: list) -> tuple:
    """Create or update the DCS-blocked rule with the given IP list."""
    if not _COM_AVAILABLE:
        return False, "comtypes not available"
    # Remove existing rule
    if _fw_rule_exists(BLOCKED_RULE_NAME):
        _fw_delete_rule(BLOCKED_RULE_NAME)

    if not ips:
        return True, "Rule removed (no IPs to block)"

    ip_list = ','.join(ips)
    ok, msg = _fw_create_rule(
        BLOCKED_RULE_NAME, NET_FW_RULE_DIR_IN, NET_FW_ACTION_BLOCK,
        NET_FW_IP_PROTOCOL_ANY, remote_ips=ip_list, enabled=True,
        description="DCSServerBot permanently blocked IPs",
    )
    if ok:
        return True, f"Rule '{BLOCKED_RULE_NAME}' updated: blocking {ip_list}"
    return False, msg


def block_ip(ip: str) -> tuple:
    """Add an IP to the permanent block list."""
    if not _COM_AVAILABLE:
        return False, "comtypes not available"
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


def unblock_ip(ip: str) -> tuple:
    """Remove an IP from the permanent block list."""
    if not _COM_AVAILABLE:
        return False, "comtypes not available"
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
        if _fw_rule_exists(BLOCKED_RULE_NAME):
            ok, msg = _fw_delete_rule(BLOCKED_RULE_NAME)
            if ok:
                msg = f"IP {ip} removed, rule deleted (no more blocked IPs)"
        else:
            ok, msg = True, f"IP {ip} removed, no rule to delete"
    if ok:
        _logger.info("unblock_ip: %s", msg)
    else:
        _logger.error("unblock_ip: %s", msg)
    return ok, msg


def list_blocked_ips() -> tuple:
    """List all IPs in the permanent block list."""
    ips = _get_blocked_ips()
    if not ips:
        return True, "No blocked IPs"
    return True, ', '.join(ips)


# ---------------------------------------------------------------------------
# DCS general rule management
# ---------------------------------------------------------------------------

def disable_dcs_general_rules():
    """Disable DCS general allow rules matching our exe path."""
    if not _COM_AVAILABLE:
        return False, "comtypes not available"
    exe_path = _resolve_dcs_exe()
    if not exe_path:
        return False, "DCS executable not found"
    normalized_exe_dir = os.path.dirname(normalize_path(exe_path))
    disabled = []
    try:
        policy = _get_fw_policy()
        for rule in policy.Rules:
            if rule.Name != "DCS":
                continue
            if not rule.ApplicationName:
                continue
            rule_dir = os.path.dirname(normalize_path(rule.ApplicationName))
            if rule_dir != normalized_exe_dir:
                continue
            if rule.Enabled:
                ok, msg = _set_rule_enabled_obj(rule, False)
                if ok:
                    disabled.append(f"{rule.Name} ({rule.Protocol})")
                    _logger.info("Disabled DCS general rule: %s proto=%s (exe=%s)",
                                 rule.Name, rule.Protocol, rule.ApplicationName)
    except Exception as ex:
        _logger.error("Error disabling DCS general rules: %s", ex)
        return False, str(ex)
    if disabled:
        return True, "Disabled: " + ", ".join(disabled)
    return True, "No DCS general rules found to disable"


def restore_dcs_general_rules():
    """Re-enable DCS general allow rules matching our exe path."""
    if not _COM_AVAILABLE:
        return False, "comtypes not available"
    exe_path = _resolve_dcs_exe()
    if not exe_path:
        return False, "DCS executable not found"
    normalized_exe_dir = os.path.dirname(normalize_path(exe_path))
    enabled = []
    try:
        policy = _get_fw_policy()
        for rule in policy.Rules:
            if rule.Name != "DCS":
                continue
            if not rule.ApplicationName:
                continue
            rule_dir = os.path.dirname(normalize_path(rule.ApplicationName))
            if rule_dir != normalized_exe_dir:
                continue
            if not rule.Enabled:
                ok, msg = _set_rule_enabled_obj(rule, True)
                if ok:
                    enabled.append(f"{rule.Name} ({rule.Protocol})")
                    _logger.info("Re-enabled DCS general rule: %s proto=%s (exe=%s)",
                                 rule.Name, rule.Protocol, rule.ApplicationName)
    except Exception as ex:
        _logger.error("Error restoring DCS general rules: %s", ex)
        return False, str(ex)
    if enabled:
        return True, "Enabled: " + ", ".join(enabled)
    return True, "No DCS general rules found to enable"


# ---------------------------------------------------------------------------
# Base per-port allow rules
# ---------------------------------------------------------------------------

def ensure_base_rule(protocol: str, port: int, remote_ips: str = None,
                       rule_name: str = None, reset: bool = False) -> tuple:
    """
    Create (or re-enable) the base per-port allow rule.

    Args:
        protocol: 'tcp' or 'udp'
        port: port number
        remote_ips: optional comma-separated IP list. If provided, the base rule
                    will only allow these IPs (whitelist mode). If None, the rule
                    allows all traffic on the port.
        rule_name: optional custom rule name. If None, defaults to
                   'DCS-base-{protocol}-{port}'.
        reset: if True and the rule already exists, delete and recreate it
               (to update IP whitelist or reset enabled state). If False and
               the rule exists, just re-enable it.
    """
    if not _COM_AVAILABLE:
        return False, "comtypes not available"
    if not rule_name:
        rule_name = f"DCS-base-{protocol}-{port}"
    if _fw_rule_exists(rule_name):
        if reset:
            _fw_delete_rule(rule_name)
            _logger.info("ensure_base_rule: deleted existing rule %s for reset", rule_name)
        else:
            return _set_rule_enabled(rule_name, True)
    proto_num = NET_FW_IP_PROTOCOL_TCP if protocol == "tcp" else NET_FW_IP_PROTOCOL_UDP
    desc = f"DCSServerBot base allow {protocol}/{port}"
    if remote_ips:
        desc += f" (whitelist: {remote_ips})"
    return _fw_create_rule(
        rule_name, NET_FW_RULE_DIR_IN, NET_FW_ACTION_ALLOW, proto_num,
        local_port=port, remote_ips=remote_ips, enabled=True,
        description=desc,
    )


def init_server() -> tuple:
    """Full init: disable DCS general rules + create base allow rules for common ports."""
    if not _COM_AVAILABLE:
        return False, "comtypes not available"
    results = []
    ok, msg = disable_dcs_general_rules()
    results.append(msg)
    if not ok:
        return False, "; ".join(results)
    return True, "; ".join(results)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

def handle_command(line: str) -> str:
    """Parse and execute a command, return response string."""
    _logger.info("handle_command: %s", line)
    import shlex
    parts = shlex.split(line.strip())
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
            ips = []
        ok, msg = restrict_rule(rule_name, protocol, port, ips)
        return f'{"OK" if ok else "ERROR"} {msg}'

    if cmd == 'restore' and len(parts) in (2, 3):
        rule_name = parts[1]
        base_rule = parts[2] if len(parts) == 3 else None
        ok, msg = restore_rule(rule_name, base_rule=base_rule)
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

    if cmd == 'init':
        ok, msg = init_server()
        return f'{"OK" if ok else "ERROR"} {msg}'

    if cmd == 'disable_dcs_rules':
        ok, msg = disable_dcs_general_rules()
        return f'{"OK" if ok else "ERROR"} {msg}'

    if cmd == 'restore_dcs_rules':
        ok, msg = restore_dcs_general_rules()
        return f'{"OK" if ok else "ERROR"} {msg}'

    if cmd == 'ensure_base' and len(parts) in (3, 4, 5):
        protocol = parts[1].lower()
        try:
            port = int(parts[2])
        except ValueError:
            return f'ERROR Invalid port: {parts[2]}'
        if protocol not in ('tcp', 'udp'):
            return f'ERROR Invalid protocol: {protocol} (use tcp or udp)'
        # Parse remaining parts: IPs (if not 'reset' and not a rule name), rule_name, reset
        remote_ips = None
        rule_name = None
        reset = False
        remaining = parts[3:]
        if remaining and remaining[-1].lower() == 'reset':
            reset = True
            remaining = remaining[:-1]
        if remaining:
            # If it looks like an IP list, treat as remote_ips; otherwise it's a rule_name
            candidate = remaining[0]
            if ',' in candidate or all(c.isdigit() or c == '.' for c in candidate):
                remote_ips = candidate
            else:
                rule_name = candidate
        ok, msg = ensure_base_rule(protocol, port, remote_ips, rule_name=rule_name, reset=reset)
        return f'{"OK" if ok else "ERROR"} {msg}'

    if cmd == 'enable' and len(parts) == 2:
        rule_name = parts[1]
        ok, msg = _set_rule_enabled(rule_name, True)
        return f'{"OK" if ok else "ERROR"} {msg}'

    if cmd == 'disable' and len(parts) == 2:
        rule_name = parts[1]
        ok, msg = _set_rule_enabled(rule_name, False)
        return f'{"OK" if ok else "ERROR"} {msg}'

    _logger.warning("handle_command: unknown command: %s", line)
    return f'ERROR Unknown command: {line}'


# ---------------------------------------------------------------------------
# Named pipe server (unchanged from working version)
# ---------------------------------------------------------------------------

PIPE_NAME = r'\\.\pipe\firewall_helper'


def _create_security_attributes():
    """
    Create a SECURITY_ATTRIBUTES with a NULL DACL, allowing access from all
    integrity levels. This is needed because the helper runs elevated (high
    integrity) and the bot runs non-elevated (medium integrity).
    """
    import ctypes.wintypes

    PSECURITY_DESCRIPTOR = ctypes.c_void_p
    SECURITY_DESCRIPTOR_MIN_LENGTH = 20

    sd = ctypes.create_string_buffer(SECURITY_DESCRIPTOR_MIN_LENGTH)
    advapi32 = ctypes.windll.advapi32

    if not advapi32.InitializeSecurityDescriptor(sd, 1):
        _logger.error("InitializeSecurityDescriptor failed, error %d", ctypes.GetLastError())
        return None
    if not advapi32.SetSecurityDescriptorDacl(sd, True, None, False):
        _logger.error("SetSecurityDescriptorDacl failed, error %d", ctypes.GetLastError())
        return None
    _logger.info("Security attributes created successfully")

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
    return sa, sd


def pipe_server(stop_event: threading.Event):
    """Run the named pipe server, handling one client at a time."""
    import ctypes.wintypes

    # Initialize COM on this thread (required for comtypes COM calls)
    try:
        import comtypes
        comtypes.CoInitialize()
        _logger.info("COM initialized on pipe server thread")
    except Exception as _e:
        _logger.error("COM init failed on pipe server thread: %s", _e)

    GENERIC_READ_WRITE = 0xC0000000
    OPEN_EXISTING = 3
    PIPE_ACCESS_DUPLEX = 0x00000003
    PIPE_TYPE_MESSAGE = 0x00000004
    PIPE_READMODE_MESSAGE = 0x00000002
    PIPE_WAIT = 0x00000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    ERROR_PIPE_CONNECTED = 535
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateNamedPipeW.restype = ctypes.wintypes.HANDLE

    _logger.info("Creating security attributes...")
    sa_result = _create_security_attributes()
    if sa_result:
        sa, sd = sa_result
        pSecurityAttributes = ctypes.byref(sa)
        _logger.info("Security attributes: using NULL DACL")
    else:
        pSecurityAttributes = None
        _logger.info("Security attributes: using default (NULL)")

    while not stop_event.is_set():
        hPipe = kernel32.CreateNamedPipeW(
            PIPE_NAME,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
            1,
            4096, 4096,
            0,
            pSecurityAttributes,
        )

        if hPipe == INVALID_HANDLE_VALUE or hPipe is None or hPipe == 0:
            if stop_event.is_set():
                break
            time.sleep(1)
            continue

        connected = kernel32.ConnectNamedPipe(hPipe, None)
        if not connected and ctypes.GetLastError() != ERROR_PIPE_CONNECTED:
            kernel32.CloseHandle(hPipe)
            time.sleep(0.5)
            continue

        try:
            while not stop_event.is_set():
                buf = ctypes.create_string_buffer(4096)
                bytes_read = ctypes.wintypes.DWORD(0)
                success = kernel32.ReadFile(hPipe, buf, 4096, ctypes.byref(bytes_read), None)

                if not success or bytes_read.value == 0:
                    break

                line = buf.value.decode('utf-8', errors='replace').strip()
                if not line:
                    continue

                response = handle_command(line)

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

        if not stop_event.is_set():
            time.sleep(0.1)


# ---------------------------------------------------------------------------
# Main (unchanged from working version)
# ---------------------------------------------------------------------------

def main():
    _logger.info("Firewall Helper started. Listening on %s", PIPE_NAME)
    _logger.info("DCS executable: %s", _resolve_dcs_exe() or "NOT FOUND")

    stop_event = threading.Event()
    server_thread = threading.Thread(target=pipe_server, args=(stop_event,), daemon=True)
    server_thread.start()

    try:
        while not stop_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    stop_event.set()
    server_thread.join(timeout=5)
    _logger.info("Firewall Helper stopped.")


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        crash_log = os.path.join(os.environ.get('TEMP', '.'), 'firewall_helper_crash.log')
        with open(crash_log, 'a') as f:
            f.write(traceback.format_exc() + '\n')
        raise
