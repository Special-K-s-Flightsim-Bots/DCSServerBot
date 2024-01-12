# Plugin "Voting"
This plugin enables a voting system for users. People can either change a mission preset (like weather, time) or the 
mission itself. Both can be selected either from a provided list or from all available options in your system.

You can define who can create a new voting, and you can define who can participate in one.

## Configuration
The plugin can be configured via yaml in config/plugins/voting.yaml:
```yaml
DEFAULT:
  preset:           # Selection of presets that need to be specified in your presets.yaml
    - Summer        # If no preset section is specified, all available presets will be used (not recommended).
    - Winter        # If you do not want presets to be part of the voting, set an empty list in here.
    - Morning
    - Nighttime
  mission:          # Selection of missions that can be used in a voting. They must be available in your serverSettings.lua
    - a.miz         # If no mission section is present, all missions from serverSettings.lua will be used.
    - b.miz
  creator:          # If present, only specific roles are allowed to create a vote (default: every player).
    - DCS
  voter:            # If present, only specific roles are allowed to vote (default: every player).
    - DCS
  mission_time: 30  # If specified, a voting can take place when the mission is running at least that many minutes.
  time: 300         # Seconds the voting is open.
  reminder:         # When to send reminders to people to vote including the current top vote.
    - 180
    - 60
    - 30
```

If you do not want people to change either of missions or presets, you can have that by setting the respective section
to an empty list like so:
```yaml
DEFAULT:
  mission: []
```
