# Monitoring Service
This service monitors your DCS servers for crashes and unwanted popups. It can also generate the necessary information
for the ServerStats plugin.

Servers that are considered RUNNING, PAUSED or STOPPED will be monitored for any unusual popup (login or lua error) or
crashes of the respective DCS.exe or DCS_server.exe process. A heartbeat will be sent to DCS every minute. The maximum
number of heartbeats a server can miss can be configured in your instance configuration in nodes.yaml

## Configuration
There is no specific configuration file for this service atm. The main configuration takes place in your nodes.yaml
and servers.yaml files:

### nodes.yaml
```yaml
MyNode:
  DCS.openbeta_server:
    max_hung_minutes: 5   # maximum heartbeats a server can miss (default: 3)
```

### servers.yaml
```yaml
MyFancyServer:
  schedule:
    00-24: YYYYYYY  # the server (and its DCS_server.exe process) should run 24x7
```
