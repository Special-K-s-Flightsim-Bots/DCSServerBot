# UPS Service
If your server is battery backed, this service handles the graceful shutdown of your DCS servers and your PC in case of
a power failure.

## Configuration
The configuration file for this service has to be placed into config\services\upsservice.yaml:
```yaml
DEFAULT:
  device: nutdev1       # UPS device name
  host: 192.168.178.123 # UPS host (the host running the NUT server)
  port: 3493            # Optional: NUT port (default: 3493)
  username: xxxx        # Optional: the user credentials to log in to the NUT server.
  password: xxxx        # Optional: the user credentials to log in to the NUT server.
  thresholds:
    warn: 90            # warn users
    shutdown: 50        # shutdown DCS
    halt: 20            # shutdown PC
```

To configure the DCS server monitoring, you can change these values in your nodes.yaml and scheduler.yaml:

### nodes.yaml
```yaml
MyNode:
  DCS.dcs_serverrelease:
    max_hung_minutes: 5   # maximum heartbeats a server can miss (default: 3)
```

### plugins/scheduler.yaml
```yaml
DCS.dcs_serverrelease:
  schedule:
    00-24: YYYYYYY  # the server (and its DCS_server.exe process) should run 24x7
```
