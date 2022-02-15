# Plugin "Missionstats"
When enabled, this plugin will generate a persistent mission statistics embed to be displayed in the status channels and detailed statistics from the ingame event system. 
The global DCSServerBot.lua and a plugin specific mission.lua will automatically be loaded into any mission running on that specific server.

## Configuration
Mission statistics can be enabled or disabled in the server configuration (see [e) Server Specific Sections](../../README.md)).
Missionstats needs the Userstats plugin to be loaded first.

## How to disable Missionstats inside of missions
To disable mission statistics for a specific mission, you can use the following piece of code somewhere in your mission (not in a on-startup trigger, but shortly after).
```lua
  dcsbot.disableMissionStats()
```

## Discord Commands
This plugin does not (yet) come with commands. 
