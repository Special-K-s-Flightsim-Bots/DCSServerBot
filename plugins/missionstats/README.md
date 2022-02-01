### Plugin "Missionstats"
This plugin does not (yet) come with commands. When enabled, it will generate a persistent mission statistics embed to be displayed in the status channels and detailed statistics from the ingame event system. 
If enabled, the DCSServerBot.lua and mission.lua will automatically be loaded into any mission running on that specific server.
To disable mission statistics for a specific mission, you can use the following piece of code somewhere in your mission (not in a on-startup trigger, but shortly after).
```lua
  dcsbot.disableMissionStats()
```
