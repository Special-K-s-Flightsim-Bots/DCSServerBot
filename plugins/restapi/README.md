# Plugin RestAPI
This API provides a very simple RestAPI that you can user together with the [WebService](../../services/webservice/README.md).
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
```

> [!WARNING]
> Do NOT use a prefix if you work with the DCS Statistics Dasboard!

## RestAPI
The following commands are available through the API

| API               | GET / POST | Parameters                                            | Description                                                                           |
|-------------------|------------|-------------------------------------------------------|---------------------------------------------------------------------------------------|
| /serverstats      | GET        |                                                       | A comprehensive statistic for your whole setup.                                       |
| /servers          | GET        |                                                       | Status for each server.                                                               |
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
> To get a more detailled API-documentation, please enable debug in your WebService config and 
> access https://localhost:9876/docs.
