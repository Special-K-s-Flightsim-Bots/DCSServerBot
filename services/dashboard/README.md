# Dashboard Service
The dashboard is a nice overlay over your CMD window, that runs DCSServerBot. It displays the current status of your
servers, the number of people flying, other nodes, if you're part of a cluster and the most recent entries of your
dcssb-_node_.log file.

## Configuration
You can configure the colors of the dashboard in the respective config/services/dashboard.yaml like so:
```yaml
DEFAULT:
  header:
    background: white on navy_blue
    border: white
  servers:
    hide_remote_servers: true         # show only local servers on the MASTER node (default: false)
    background: white on dark_blue
    border: white
  nodes:
    background: white on dark_blue
    border: white
  log:
    background: white on grey15
    border: white
```

> [!TIP]
> You can disable the dashboard in your main.yaml like so:<br>
> `use_dashboard: false  # disable the Dashboard (default: true)`
