# Monitoring Service
This service monitors the health of your DCS servers.<p>
It detects crashes or unwanted popups, full disks, too low server FPS or too high RAM usage. 
It also generates load information to be used with the `/serverload` command or has DDoS detection.

Servers that are considered RUNNING, PAUSED, or STOPPED will be monitored for any unusual popup (login or lua error) or
crashes of the respective DCS.exe or DCS_server.exe process. A heartbeat will be sent to DCS every minute. The maximum
number of heartbeats a server can miss can be configured in your instance configuration in nodes.yaml

If you do not have a static IP address, you can use this service to auto-restart your servers on IP changes.

## Configuration
The (optional) configuration file for this service has to be placed into config\services\monitoring.yaml:
```yaml
# config/services/monitoring.yaml
DEFAULT:
  time_sync: true           # sync the PC time with a time-server every 12 hrs, default: false
  time_server: pool.ntp.org # and use this non-default time-server for it, default: Windows default
  ignore_ip_changes: false  # If you don't have a fixed IP, the bot will automatically restart your servers on a change of your public IP. If you don't want that, set this to true.
  ddos_detect: true         # Enable DDoS detection (see below, default: false)
  messages:
    ip_change: Server is being restarted due to an IP change!
  thresholds:
    Drive:              # You cannot disable the drive check. If you do not specify anything, these values will be taken as default. 
      warn: 10          # Warn, if your drive where DCS is installed (or your C: drive), gets below 10% (default: 10)
      alert: 5          # Send an alert if your DCS drive (or your C: drive) gets below 5% (default: 5)
      message: "Available space on drive {drive} has dropped below {pct}%!\nOnly {bytes_free} out of {bytes_total} free."
    FPS:                # Optional FPS-check
      min: 30           # if FPS reaches a min of 30 (default: 30)
      period: 5         # for at least 5 minutes (default: 5)
      message: "Server {server} FPS ({fps}) has been below {min_fps} for more than {period} minutes."
      mentioning: true  # and mention the admins (default: true)
    RAM:                # Optional RAM-check
      max: 32           # if RAM exceeds 32 GB (default: 32)
      period: 5         # for at least 5 minutes (default: 5)
      message: "Server {server} RAM usage is {ram} GB, exceeding the maximum of {max_ram} GB for more than {period} minutes."
      mentioning: true  # and mention the admins (default: true)
    DDoS:                           # Optional DDoS-check
      threshold_sigma: 3.0          # z-score threshold for anomaly detection (default: 3.0)
      min_samples: 30               # minimum baseline samples before detection starts (default: 30)
      min_abs_recv_mbps: 10         # minimum absolute bandwidth in MB/s to consider (default: 10)
      consecutive_ticks: 3          # consecutive anomaly ticks to confirm DDoS START (default: 3)
      recovery_ticks: 5             # consecutive normal ticks to confirm DDoS END (default: 5)
      alert_cooldown_minutes: 15    # minimum minutes between repeated alerts (default: 15)
      baseline_lookback_minutes: 30 # minutes of historical data to seed baselines on startup (default: 30)
      udp_sniff_duration: 10        # seconds to sniff UDP for non-player source IPs (default: 10)
      udp_sniff_iface: null         # network interface for UDP sniff, null = auto-detect (default: null)
      action: alert                 # "alert" = detect+notify only, "block" = alert + firewall block (default: alert)
      whitelist: []                 # static IPs always allowed during blocks (e.g. admin IPs)
      node_block: true              # on node-wide bandwidth DDoS, block ALL running servers (default: true)
      max_conns_per_ip: 2           # auto-block IPs with >N TCP connections per port, 0=disable (default: 2)
```

### DDoS Detection Configuration (ddos_detect)
To enable DDoS detection, first you need to install the [NPCAP](https://npcap.com/#download) library on Windows.
This is necessary to sniff UDP traffic.

After that, set `ddos_detect` to true in your monitoring.yaml.
You then get a lot of options to configure the detection. Most users will not need to change anything.

#### Detection signals:
* Per-port unique IPs (primary): monitors unique remote IPs connecting to each instance's DCS port (TCP+UDP). 
  Learns what's normal per server including ED server list probes.
* Per-port connections (secondary): total socket count on each port. Corroborates the unique IP signal.
* Node-wide inbound bandwidth: total bytes/sec received on the node. Catches attacks that saturate the network pipe 
  even if individual port stats look normal.
* Per-IP connection count: monitors how many TCP connections each remote IP has to a server port. A normal player
  has exactly 1 TCP connection. If an IP exceeds `max_conns_per_ip` connections, it is auto-blocked permanently
  via a `DCS-blocked-<ip>` firewall rule. This catches single-IP port-opening attacks that try to exhaust slots.

#### Node-wide Blocking (node_block)
When `action` is `block` (not `alert`) and `node_block` is `true` (default), and a node-wide bandwidth
DDoS is confirmed, the service automatically applies the per-instance blocking strategy to **all** running
servers on the node. Each server gets its own firewall restrict rules (TCP+UDP), log tail for new player
discovery, and dynamic whitelist — the same as if each server were individually attacked. Servers already
under individual attack are skipped to avoid double-blocking.

Set `node_block: false` to disable the node-wide firewall response while keeping per-instance blocking
and node-wide bandwidth detection/alerts active.

When the node-wide attack ends, only servers that were blocked by the node-wide event are unblocked.
Servers that were individually attacked remain blocked until their own attack ends.

#### Permanent IP Blocking (max_conns_per_ip)
When `max_conns_per_ip` is set to a value > 0, the service automatically adds offending IPs
to a single persistent Windows Firewall rule named `DCS-blocked`. This rule accumulates all
blocked IPs and is only removed when the last IP is unblocked. The rule:
* Is **not** tied to DCS.exe or specific ports — it blocks all traffic from the listed IPs
* Persists across DDoS state changes (it is not created/deleted dynamically)
* Can be managed manually with `/ddos_block`, `/ddos_unblock`, and `/ddos_blocked` commands
* Is also created automatically by the per-IP flood detection (Signal 4)

#### State machine per signal:
```
NORMAL → (N consecutive anomalous ticks) → DDoS START callback
       → (periodic updates every alert_cooldown_minutes while attack continues)
       → (M consecutive normal ticks) → DDoS END callback → NORMAL
```

#### Baseline behavior:
* Welford's online algorithm — continuously adapts mean and variance, no fixed window
* Attack data is excluded from baseline updates to prevent contamination
* On startup, historical data from the port_traffic table is loaded to pre-seed baselines
* The cleanup loop purges data older than 1 month

#### Dynamic Whitelist (action=block)
When `action` is set to `block`, the service automatically tails the DCS log (`dcs.log`) during a UDP DDoS block.
Any player that connects via TCP during the block is automatically added to a dynamic whitelist, and the
firewall rule is refreshed to allow that player's IP on UDP. This ensures legitimate players can join even
while the UDP port is under attack.

The log tail stops automatically when the DCS server is shut down (status ≠ RUNNING/PAUSED).

### DCS Server keep-alive Check
To configure the DCS server monitoring, you can change these values in your nodes.yaml and scheduler.yaml:

### nodes.yaml
```yaml
# config/nodes.yaml
MyNode:
  DCS.dcs_serverrelease:
    max_hung_minutes: 5   # maximum heartbeats a server can miss (default: 3)
```

### plugins/scheduler.yaml
```yaml
# config/plugins/scheduler.yaml
DCS.dcs_serverrelease:
  schedule:
    00-24: YYYYYYY  # the server (and its DCS_server.exe process) should run 24x7
```
