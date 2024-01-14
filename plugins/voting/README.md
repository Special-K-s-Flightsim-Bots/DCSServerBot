# Plugin "Voting"
This plugin enables a voting system for users. People can either change a mission preset (like weather, time) or the 
mission itself. Both can be selected either from a provided list or from all available options in your system.

You can define who can create a new voting, and you can define who can participate in one.

## Configuration
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
    kick: {}            # Vote for kicking a player
    tempban:            # Vote to tempban a player for duration days
      duration: 3       # default: 3 (days)
  creator:              # If present, only specific roles are allowed to create a vote (default: every player).
    - DCS
  voter:                # If present, only specific roles are allowed to vote (default: every player).
    - DCS
  mission_time: 30      # If specified, a voting can take place when the mission is running at least that many minutes.
  time: 300             # Seconds the voting is open.
  reminder:             # When to send reminders to people to vote including the current top vote.
    - 180
    - 60
    - 30
  voting_threshold: 0.25  # 25% of all players have to vote for the vote to count
  voting_rule: "majority" # one of "majority" (default), "supermajority" (>= 33%), "unanimous" or "absolute" (>= 50%)
  credits: 10             # a vote costs 10 credit points (default: 0 = off)
```

## In-Game Commands

| Command | Parameter          | Description                                      |
|---------|--------------------|--------------------------------------------------|
| .vote   | \<what\> \[param\] | Start a voting .                                 | 
| .vote   | cancel             | Cancel a voting (only DCS Admin can do that).    |
| .vote   | <num>              | Vote for one of the options.                     |
| .vote   |                    | Display the current voting and the leading vote. |

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
        self.server.sendChatMessage(Coalition.ALL, message)
        self.server.sendPopupMessage(Coalition.ALL, message)
```
