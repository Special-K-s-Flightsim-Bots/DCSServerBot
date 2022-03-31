# Plugin Greenieboard
This plugin allows you to have a (optional: persistent) greenieboard for the top x (default: 10) players and to give 
players the opportunity to analyse their last carrier landings.

**__Attention:__**<br/> 
- The landing quality marks will only be generated for landings\* on the DCS Super Carrier module!</br>
- MISSION_STATISTICS = true has to be enabled for your server.**
<p></p>
*Not all landings generate a mark for whatever reason.

## Configuration
Greenieboard comes as many other plugins with a JSON configuration. If you don't generate your own config, DCSServerBot
will just copy over the sample by itself and use that one. This will do it for the most users, if you don't plan to
use the persistent mode.

Anyway, this is how the configuration will look like. For now, there is only a default config, so you can't generate
different greenieboards for different servers. If that is a user demand in the future, I might add it.

```json
{
  "configs": [
    {
      "num_landings": 5,            -- the number of latest carrier landings you get, if you use the .carrier command
      "num_rows": 10,               -- the number of players that can get on the greenieboard (there might be discord limits)
      "persistent_board": true,     -- true (default false) if you want a persistent board displayed somewhere in your discord
      "persistent_channel": "1234", -- the ID of the channel where the greenieboard should be displayed
      "ratings": {                  -- ratings will define how many points you get for which LSO rating (see SC documentation for details)
        "_OK_": 5,
        "OK": 4,
        "(OK)": 3,
        "B": 2.5,
        "---": 2,
        "OWO": 2,
        "WO": 1,
        "C": 0
      }
    }
  ]
}
```

## Discord Commands

| Command         | Parameter          | Channel       | Role      | Description                                                                       |
|-----------------|--------------------|---------------|-----------|-----------------------------------------------------------------------------------|
| .greenieboard   |                    | all           | DCS       | Print the current greenieboard.                                                   |
| .carrier        | @member / DCS name | all           | DCS       | Display the last carrier landings for this user and a detailed view on selecion.  |
