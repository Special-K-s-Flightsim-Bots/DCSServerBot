# Plugin RestAPI
This API provides a very simple RestAPI that you can call from your webserver to receive data from DCSServerBot.
It is WIP and it will be enhanced in the future.

## Configuration
There is some very basic endpoint configuration available as of now:
```yaml
DEFAULT:
  listen: 0.0.0.0   # the interface to bind the internal webserver to
  port: 9876        # the port the internal webserver is listening on
```

## RestAPI
The following commands are available through the API

| API        | GET / POST | Parameters               | Return                                                                                                                                                                                                                                               | Description                                                         |
|------------|------------|--------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| /getuser   | POST       | nick: string             | {"name": string, "last_seen": date}                                                                                                                                                                                                                  | Return a list of players ordered by last seen that match this nick. |
| /stats     | POST       | nick: string, date: date | {<br>"deaths": int,<br>"aakills": int,<br>"aakdr": float,<br>"lastSessionKills": int,<br>"lastSessionDeaths": int,<br>"killsbymodule": [<br>{"module": string, "kills": int}<br>],<br>"kdrByModule": [<br>{"module": string, "kdr": float}<br>]<br>} | Statistics of this player                                           |
| /topkills  | GET        |                          | {"fullNickname": string, "AAkills": int, "deaths": int, "AAKDR": float}                                                                                                                                                                              | Top 10 of players ordered by kills descending.                      |
| /topkdr    | GET        |                          | {"fullNickname": string, "AAkills": int, "deaths": int, "AAKDR": float}                                                                                                                                                                              | Same as /topkills but ordered by AAKDR descending.                  |
| /missilepk | POST       | nick: string, date: date | {"weapon": {"weapon-name": string, "pk": float}}                                                                                                                                                                                                     | Probability of kill for each weapon per given user.                 |

> ⚠️ **Attention!**<br>
> It is advisable to use a reverse proxy like nginx and maybe an SSL encryption between your webserver and this endpoint. 
