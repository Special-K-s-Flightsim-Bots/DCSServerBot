# Plugin Firewall
This plugin provides DDoS protection for your DCS servers. 
It is the frontend to the [Firewall Service](../../services/firewall/README.md).

## Configuration
There is no specific plugin configuration. 
Please see the [service documentation](../../services/firewall/README.md) for configuration.

## Discord Commands
| Command                | Parameter                           | Role      | Description                                                                                                                                                   |
|------------------------|-------------------------------------|-----------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
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
