# Plugin Monitoring
This is a default plugin of DCSServerBot. It gathers load statistics of your DCS server. The plugin itself is only
the frontend to the [Monitoring Service](../../services/monitoring/README.md).

## Configuration
There is no specific plugin configuration. Please see the [service documentation](../../services/monitoring/README.md) for configuration.

## Discord Commands

| Command      | Parameter                 | Role  | Description                                                                                                                                                  |
|--------------|---------------------------|-------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| /serverstats | [server] [day/week/month] | Admin | Displays server statistics, like usual playtime, most frequented servers and missions.<br/>If no server is provided, you can cycle through all your servers. |
| /serverload  | [server] [hour/day/week]  | Admin | Displays technical server statistics, like CPU load, memory consumption, etc.<br/>If no server is provided, you can cycle through all your servers.          |
| /cpuinfo     | node                      | Admin | Shows the CPU topology of your node. More to come.                                                                                                           |

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
