# Plugin Monitoring
This is a default plugin of DCSServerBot. It gathers load statistics of your DCS server. The plugin itself is only
the frontend to the [Monitoring Service](../../services/monitoring/README.md).

## Configuration
There is no specific plugin configuration. Please see the [service documentation](../../services/monitoring/README.md) for configuration.

## Discord Commands

| Command                | Parameter                           | Role      | Description                                                                                                                                                   |
|------------------------|-------------------------------------|-----------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| /serverstats           | [server] [day/week/month]           | Admin     | Displays server statistics, like usual playtime, most frequented servers and missions.<br/>If no server is provided, you can cycle through all your servers.  |
| /serverload            | [server] [hour/day/week]            | Admin     | Displays technical server statistics, like CPU load, memory consumption, etc.<br/>If no server is provided, you can cycle through all your servers.           |
| /ddos status           | [server] [period]                   | DCS Admin | Displays DDoS detection statistics and graphs for a server or all servers.                                                                                    |
| /ddos test start       | server [protocol] [port] [duration] | Admin     | Simulate a DDoS attack for testing. Triggers the full detection+blocking flow without real attack traffic. Auto-stops after `duration` seconds (default 30).  |
| /ddos test stop        | server                              | Admin     | Stop a running DDoS simulation on a server.                                                                                                                   |
| /ddos block            | node, [server]                      | Admin     | Manually trigger DDoS blocking for a server or the whole node (omit server for node-wide). Requires action=block.                                             |
| /ddos unblock          | node, [server]                      | Admin     | Deactivate DDoS blocking for a server or the whole node (omit server for node-wide).                                                                          |
| /ddos whitelist add    | ip                                  | DCS Admin | Add an IP to the DDoS whitelist on all nodes (allowed during blocks).                                                                                         |
| /ddos whitelist remove | ip                                  | DCS Admin | Remove an IP from the DDoS whitelist on all nodes.                                                                                                            |
| /ddos blacklist add    | ip                                  | DCS Admin | Permanently block an IP address via Windows Firewall on all nodes.                                                                                            |
| /ddos blacklist remove | ip                                  | DCS Admin | Remove an IP from the permanent block list on all nodes.                                                                                                      |

## Tables
### Serverstats
| Column      | Type                             | Description                                          |
|-------------|----------------------------------|------------------------------------------------------|
| #id         | SERIAL                           | Auto-incrementing unique ID of this column.          |
| agent_host  | TEXT NOT NULL                    | Hostname the bot runs on.                            |
| server_name | TEXT NOT NULL                    | Server name of this event.                           |
| mission_id  | INTEGER NOT NULL                 | The ID of the running mission.                       |
| users       | INTEGER NOT NULL                 | Number of active users at that point in time.        |
| status      | TEXT NOT NULL                    | Status of the server (PAUSED, RUNNING, etc.)         |
| cpu         | NUMERIC(5,2) NOT NULL            | CPU load of the dcs.exe process                      |
| mem_total   | NUMERIC NOT NULL                 | total memory consumption of the dcs.exe process      |
| mem_ram     | NUMERIC NOT NULL                 | part of memory being in RAM                          |
| read_bytes  | NUMERIC NOT NULL                 | number of bytes read from disk per minute            |
| write_bytes | NUMERIC NOT NULL                 | number of bytes written  to disk per minute          |
| bytes_sent  | NUMERIC NOT NULL                 | number of bytes sent over the network per minute     |
| bytes_recv  | NUMERIC NOT NULL                 | number of bytes received over the network per minute |
| fps         | NUMERIC(5,2) NOT NULL            | current "FPS" at that point in time                  |
| time        | TIMESTAMP NOT NULL DEFAULT NOW() | time of measurement                                  |

### port_traffic
| Column             | Type                             | Description                                          |
|--------------------|----------------------------------|------------------------------------------------------|
| #id                | SERIAL                           | Auto-incrementing unique ID.                         |
| node               | TEXT NOT NULL                    | Node name.                                           |
| server_name        | TEXT NOT NULL                    | DCS server name.                                     |
| port               | INTEGER NOT NULL                 | Local port number.                                   |
| protocol           | TEXT NOT NULL                    | Protocol: 'tcp' or 'udp'.                            |
| bytes_in           | BIGINT NOT NULL DEFAULT 0        | Total bytes received (TCP only, requires admin).     |
| bytes_out          | BIGINT NOT NULL DEFAULT 0        | Total bytes sent (TCP only, requires admin).         |
| packets_in         | BIGINT NOT NULL DEFAULT 0        | Total packets received (TCP only, requires admin).   |
| packets_out        | BIGINT NOT NULL DEFAULT 0        | Total packets sent (TCP only, requires admin).       |
| unique_ips         | INTEGER NOT NULL DEFAULT 0       | Unique remote IP addresses.                          |
| connections        | INTEGER NOT NULL DEFAULT 0       | Active TCP/UDP connections.                          |
| players            | INTEGER NOT NULL DEFAULT 0       | Number of active players at collection time.         |
| non_player_udp_ips | INTEGER NOT NULL DEFAULT 0       | Unique non-player UDP source IPs (scapy sniff).      |
| under_attack       | BOOLEAN NOT NULL DEFAULT FALSE   | True if collected during an active DDoS attack.      |
| time               | TIMESTAMP NOT NULL DEFAULT NOW() | Time of measurement.                                 |
