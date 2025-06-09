---
layout: default
title: README
nav_section: services/monitoring
---

# Monitoring Service
This service monitors the health of your DCS servers.<p>
It detects crashes or unwanted popups, full disks, too low server FPS or too high RAM usage. 
It also generates load information to be used with the `/serverload` command.

Servers that are considered RUNNING, PAUSED or STOPPED will be monitored for any unusual popup (login or lua error) or
crashes of the respective DCS.exe or DCS_server.exe process. A heartbeat will be sent to DCS every minute. The maximum
number of heartbeats a server can miss can be configured in your instance configuration in nodes.yaml

## Configuration
The (optional) configuration file for this service has to be placed into config\services\monitoring.yaml:
```yaml
DEFAULT:
  time_sync: true           # sync the PC time with a time-server every 12 hrs, default: false
  time_server: pool.ntp.org # and use this non-default time-server for it, default: Windows default
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
```

To configure the DCS server monitoring, you can change these values in your nodes.yaml and scheduler.yaml:

### nodes.yaml
```yaml
MyNode:
  DCS.release_server:
    max_hung_minutes: 5   # maximum heartbeats a server can miss (default: 3)
```

### plugins/scheduler.yaml
```yaml
DCS.release_server:
  schedule:
    00-24: YYYYYYY  # the server (and its DCS_server.exe process) should run 24x7
```
