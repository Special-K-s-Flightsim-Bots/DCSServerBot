# Monitoring Service
This service monitors your DCS servers for crashes and unwanted popups. It can also generate the necessary information
for the ServerStats plugin.

Servers that are considered RUNNING, PAUSED or STOPPED will be monitored for any unusual popup (login or lua error) or
crashes of the respective DCS.exe or DCS_server.exe process. A heartbeat will be sent to DCS every minute. The maximum
number of heartbeats a server can miss can be configured in your instance configuration in nodes.yaml

## Configuration
The (optional) configuration file for this service has to be placed into config\services\monitoring.yaml:
```yaml
DEFAULT:
  time_sync: true           # sync the PC time with a time-server every 12 hrs, default: false
  time_server: pool.ntp.org # and use this non-default time-server for it, default: Windows default
  drive_warn_threshold: 10  # Warn, if your drive where DCS is installed (or your C: drive), gets below 10%
  drive_alert_threshold: 5  # Send an alert and ping admins if your DCS drive (or your C: drive) gets below 5%
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
