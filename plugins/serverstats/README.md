# Plugin Serverstats
If you enable this plugin you will gather statistics for your DCS servers.<br/>

## Configuration
n/a

## Discord Commands

| Command      | Parameter               | Role  | Description                                                                                                                                             |
|--------------|-------------------------|-------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| .serverstats | [day/week/month] [-all] | Admin | Displays server statistics, like usual playtime, most frequented servers and missions.<br/>If -all is provided, you can cycle through all your servers. |
| .serverload  | [hour/day/week] [-all]  | Admin | Displays technical server statistics, like CPU load, memory consumption, etc.<br/>If -all is provided, you can cycle through all your server nodes.     |

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
