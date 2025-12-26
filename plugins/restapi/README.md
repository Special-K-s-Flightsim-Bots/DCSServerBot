# Plugin RestAPI
This API provides a very simple RestAPI that you can use together with the [WebService](../../services/webservice/README.md).
You can use it to power the [DCS Statistics Dasboard](https://github.com/Penfold-88/DCS-Statistics-Dashboard).

## Configuration
As RestAPI is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - restapi
```

You can configure the RestAPI endpoints in your config\plugins\restapi.yaml like so:
```yaml
DEFAULT:
  prefix: /stats            # Optional: use this prefix
  api_key: aaabbbcccc       # Optional: API key to be used to secure the API
  endpoints:                 # endpoint configuration
    servers:                # /servers
      filter:               # config parameter (in this case, the server filter list)
        - 'MyPrivateServer' # Do not show a server named "MyPrivateServer"
        - '(.*)Private(.*)' # Do not show any server that has "Private" in its name 
      include_weather: true # Include weather information in /servers endpoint (default: true)
    server_attendance:      # /server_attendance 
      enabled: true         # Enable the server attendance statistics endpoint (default: true)
```

> [!WARNING]
> Do NOT use a prefix if you work with the DCS Statistics Dasboard!

## RestAPI
The following commands are available through the API

| API               | GET / POST | Parameters                                            | Description                                                                           |
|-------------------|------------|-------------------------------------------------------|---------------------------------------------------------------------------------------|
| /serverstats      | GET        |                                                       | A comprehensive statistic for your whole setup.                                       |
| /servers          | GET        |                                                       | Status for each server, including weather information if enabled.                     |
| /server_attendance| GET        | [server_name: string]                                 | Detailed server attendance statistics (24h, 7d, 30d) and daily trends.               |
| /getuser          | POST       | nick: string                                          | Return a list of players ordered by last seen that match this nick.                   |
| /stats            | POST       | nick: string, date: date                              | Statistics of this player                                                             |
| /highscore        | GET        | [server_name: string], [period: string], [limit: int] | Highscore output                                                                      |
| /topkills         | GET        | [limit: int]                                          | Top x of players ordered by kills descending.                                         |
| /topkdr           | GET        | [limit: int]                                          | Same as /topkills but ordered by AAKDR descending.                                    |
| /trueskill        | GET        | [limit: int]                                          | Top x trueskill ratings.                                                              |
| /weaponpk         | POST       | nick: string, date: date                              | Probability of kill for each weapon per given user.                                   |
| /credits          | POST       | nick: string, date: date, [campaign]                  | Credits of a specific player.                                                         |
| /traps            | POST       | nick: string, date: string, [limit: int]              | Lists the traps of that user.                                                         |
| /squadrons        | GET        |                                                       | Lists all squadrons.                                                                  |
| /squadron_members | POST       | name: string                                          | Lists the members of the squadron with that name.                                     |
| /squadron_credits | POST       | name: string, [campaign]                              | Lists the members of the squadron with that name.                                     |
| /linkme           | POST       | discord_id: string, force: bool                       | Same as /linkme in discord. Returns a new token that can be used in the in-game chat. |

> [!NOTE]
> To get more detailled API documentation, please enable debug in your WebService config and 
> access https://localhost:9876/docs.

## New Features

### Weather Information
The `/servers` endpoint now includes real-time weather data for running DCS servers:
```json
{
  "weather": {
    "temperature": 16.0,
    "wind_speed": 0.968,
    "wind_direction": 290,
    "pressure": 765.0,
    "visibility": 5000,
    "clouds_base": 0,
    "clouds_density": 0,
    "precipitation": 0,
    "fog_enabled": false,
    "dust_enabled": false
  }
}
```

### Server Attendance Statistics
The `/server_attendance` endpoint provides detailed server attendance analytics:

**Global statistics (no parameters):**
```bash
GET /server_attendance
```

**Server-specific statistics (using DCS server name):**
```bash
GET /server_attendance?server_name=VEAF (www.veaf.org) [fr] - Private Foothold 2
```

**Server-specific statistics (using instance alias):**
```bash
GET /server_attendance?server_name=foothold2_server
```

**Response example:**
```json
{
  "current_players": 8,
  "unique_players_24h": 15,
  "total_playtime_hours_24h": 45.5,
  "discord_members_24h": 12,
  "unique_players_7d": 35,
  "total_playtime_hours_7d": 180.2,
  "discord_members_7d": 28,
  "unique_players_30d": 85,
  "total_playtime_hours_30d": 720.8,
  "discord_members_30d": 65,
  "daily_trend": [
    {"date": "2025-12-24", "unique_players": 15},
    {"date": "2025-12-25", "unique_players": 18}
  ]
}
```

### Server Name Resolution
All endpoints that accept a `server_name` parameter now support both server naming conventions seamlessly:
- **Instance alias**: `foothold2_server` (from nodes.yaml configuration)
- **DCS server name**: `VEAF (www.veaf.org) [fr] - Private Foothold 2` (from servers.yaml configuration)

The resolution is handled transparently by the `get_resolved_server()` method, which:
1. Checks if the provided name is already a full DCS server name
2. If not, searches instance aliases and returns the corresponding DCS server name
3. Returns both the resolved name and server object for use in endpoints

This approach ensures consistent behavior across all endpoints while maintaining simple, readable SQL construction patterns.
