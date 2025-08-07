# Plugin RestAPI
This API provides a very simple RestAPI that you can call from your webserver to receive data from DCSServerBot.
It is WIP, and it will be enhanced in the future.

## Configuration
As RestAPI is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - restapi
```

There is some very basic endpoint configuration available as of now, which you can add in your 
config/plugins/restapi.yaml like so:
```yaml
DEFAULT:
  listen: 0.0.0.0   # the interface to bind the internal webserver to
  port: 9876        # the port the internal webserver is listening on
  prefix: /stats    # use this prefix (optional)
  debug: false      # Enable /openapi.json, /docs and /redoc endpoints to test the API (default: false)
```

> [!WARNING]
> Do NOT enable debug for normal operations, especially, if you expose the REST service to the outside world.

## RestAPI
The following commands are available through the API

| API               | GET / POST | Parameters                                            | Return                                                                                                                                                                                                                                                                     | Description                                                                           |
|-------------------|------------|-------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| /getuser          | POST       | nick: string                                          | {"name": string, "date": date}                                                                                                                                                                                                                                             | Return a list of players ordered by last seen that match this nick.                   |
| /servers          | GET        |                                                       | [{"name": string, "status": string, "address": string, "password": string, "mission": {"name": string, "uptime": string, "date_time": string, "theatre", string, "blue_slots: int, "red_slots": int, "blue_slots_used": int, "red_slots_used": int, "restart_time": int}}] | Status for each server.                                                               |
| /stats            | POST       | nick: string, date: date                              | {<br>"deaths": int,<br>"aakills": int,<br>"aakdr": float,<br>"lastSessionKills": int,<br>"lastSessionDeaths": int,<br>"killsbymodule": [<br>{"module": string, "kills": int}<br>],<br>"kdrByModule": [<br>{"module": string, "kdr": float}<br>]<br>}                       | Statistics of this player                                                             |
| /highscore        | GET        | [server_name: string], [period: string], [limit: int] |                                                                                                                                                                                                                                                                            | Highscore output                                                                      |
| /topkills         | GET        | [limit: int]                                          | {"fullNickname": string, "AAkills": int, "deaths": int, "AAKDR": float}                                                                                                                                                                                                    | Top x of players ordered by kills descending.                                         |
| /topkdr           | GET        | [limit: int]                                          | {"fullNickname": string, "AAkills": int, "deaths": int, "AAKDR": float}                                                                                                                                                                                                    | Same as /topkills but ordered by AAKDR descending.                                    |
| /trueskill        | GET        | [limit: int]                                          |                                                                                                                                                                                                                                                                            | Top x trueskill ratings.                                                              |
| /missilepk        | POST       | nick: string, date: date                              | {"weapon": {"weapon-name": string, "pk": float}}                                                                                                                                                                                                                           | Probability of kill for each weapon per given user.                                   |
| /credits          | POST       | nick: string, date: date                              | [{"id": int, "name": string, "credits": float}]                                                                                                                                                                                                                            | Credits of a specific player.                                                         |
| /traps            | POST       | nick: string, date: string, [limit: int]              |                                                                                                                                                                                                                                                                            | Lists the traps of that user.                                                         |
| /squadrons        | GET        |                                                       | [{"name": string, "description": string, "image_url": string, "locked": boolean, "role": string}]                                                                                                                                                                          | Lists all squadrons.                                                                  |
| /squadron_members | POST       | name: string                                          | [{"name": string, "date": date}]                                                                                                                                                                                                                                           | Lists the members of the squadron with that name.                                     |
| /linkme           | POST       | discord_id: string, force: bool                       | {"token": 1234, "timestamp": "2025-02-03 xx:xx:xx...", "rc": 0}                                                                                                                                                                                                            | Same as /linkme in discord. Returns a new token that can be used in the in-game chat. |

> [!NOTE]
> To access the API documentation, you can enable debug and access the documentation with these links: 
> http://localhost:9876/docs
> http://localhost:9876/redoc
> Please refer to the [OpenAPI specification](https://swagger.io/specification/) for more information and the 
> warning about debug above.

> [!IMPORTANT]
> It is advisable to use a reverse proxy like nginx and maybe SSL encryption between your webserver and this endpoint. 
