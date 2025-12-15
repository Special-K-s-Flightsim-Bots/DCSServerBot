# Plugin "Voting"
This plugin enables a voting system for users. People can choose from several voting options. DCSServerBot comes with 
some pre-defined ones, like mission change, preset change, kicking or temp-banning of annoying users.

You can define who can create a new voting, and you can define who can participate in one. The voting can be combined 
with the [CreditSystem](../creditsystem/README.md), meaning you can make votes cost credits if you like.

## Configuration
As Voting is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - voting
```

The plugin can be configured via yaml in config/plugins/voting.yaml:
```yaml
DEFAULT:
  options:              # These are the voting options, players can chose from. It is up to you, to enable them or not!
    preset:             # Selection of presets that need to be specified in your presets.yaml
      choices:
        - Summer        # If no preset section is specified, all available presets will be used (not recommended).
        - Winter        # If you do not want presets to be part of the voting, set an empty list in here.
        - Morning
        - Nighttime
    mission:            # Selection of missions that can be used in a voting. They must be available in your serverSettings.lua
      choices:
        - a.miz         # If no mission section is present, all missions from serverSettings.lua will be used.
        - b.miz
    restart:            # Vote for a mission restart
      run_extensions: true  # if true, extensions like RealWeather, MizEdit, etc will be run (default: false).
      shutdown: true        # if true, the server will be shut down for the restart (default: false)
    kick: {}            # Vote for kicking a player
    tempban:            # Vote to tempban a player for duration days
      duration: 3       # default: 3 (days)
  creator:              # If present, only specific roles are allowed to create a vote (default: every player).
    - Donators
  voter:                # If present, only specific roles are allowed to vote (default: every player).
    - Donators
  mission_time: 30      # If specified, a voting can take place when the mission is running at least that many minutes.
  time: 300             # Seconds the voting is open.
  reminder:             # When to send reminders to people to vote including the current top vote.
    - 180
    - 60
    - 30
  voting_threshold: 0.25  # 25% of all players have to vote for the vote to count
  voting_rule: "majority" # one of "majority" (default), "supermajority" (>= 33%), "unanimous" or "absolute" (>= 50%)
  credits: 10             # a vote costs 10 credit points (default: 0 = off)
instance2:
  enabled: false        # Disable the Voting plugin on instance2
```

If you don't want to provide a list of presets or missions, just send an empty tag like so:
```yaml
DEFAULT:
  options:              # These are the voting options, players can chose from. It is up to you, to enable them or not!
    preset: {}          # Select all available presets of your presets.yaml
    mission: {}         # Select all available presets of your serverSettings.lua
```

## Discord Commands
| Command      | Parameter     | Channel | Role      | Description                                                              |
|:-------------|:--------------|:-------:|:----------|:-------------------------------------------------------------------------|
| /vote list   |               |   all   | DCS       | Lists all active votes on servers.                                       |
| /vote create | server choice |   all   | DCS       | Create a new vote. Permission specifics might apply (e.g. creator role). |
| /vote cancel | server        |   all   | DCS Admin | Cancel the running vote on this server.                                  |

> [!IMPORTANT]
> You need to define the `creator` role in your voting.yaml to use `/vote create`.
> This is to avoid that random people can create votes in your Discord server.

## In-Game Commands
| Command | Parameter          | Description                                      |
|:--------|:-------------------|:-------------------------------------------------|
| -vote   | \<what\> \[param\] | Start a voting.                                  | 
| -vote   | cancel             | Cancel a voting (only DCS Admin can do that).    |
| -vote   | <num>              | Vote for one of the options.                     |
| -vote   |                    | Display the current voting and the leading vote. |

A voting will automatically end after `time` seconds and execute the result.

## How to create your own voting option?
As usual, you can enhance DCSServerBot with your own ideas.<br>
To create your own voting option, you have to implement a VotingItem in plugins/voting/options. The naming is relevant.
Let's implement a class to vote the sexiest pilot on your server. We will call it "sexy". Then you need to create a
file plugins/voting/options/sexy.py and implement a class Sexy inside of it like so:
```python
from typing import Optional

from core import Server, Coalition
from plugins.voting.base import VotableItem


class Sexy(VotableItem):

    def __init__(self, server: Server, config: dict, params: Optional[list[str]] = None):
        super().__init__('sexy', server, config, params)

    def print(self) -> str:
        return f"You can now vote for the most sexiest pilot that is active on the server."

    def get_choices(self) -> list[str]:
        return [x.name for x in self.server.get_active_players()]

    async def execute(self, winner: str):
        message = f"{winner} is the most sexiest player on this server!"
        await self.server.sendChatMessage(Coalition.ALL, message)
        await self.server.sendPopupMessage(Coalition.ALL, message)
```

To enable it, just add it to the options in your voting.yaml:
```yaml
DEFAULT:
  options:
    sexy: {}  # Our plugin does not have any configuration, so {} is given
```
