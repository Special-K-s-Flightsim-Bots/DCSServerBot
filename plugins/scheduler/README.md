# Plugin Scheduler
With this plugin you can decide when to run your DCS servers, when to run which mission and how long a specific mission shall run, either in local time or in mission time.
Tasks that can be achieved with this solution are:
* Have a server rotate a mission every 4 hrs.
* Restart the mission before it gets dark.
* Have two servers run alternately, maybe one with password, one public
* Change time and weather in your mission on specific times or randomly

## Configuration
Examples:
```json
{
  "configs": [
    {
      "warn": {
        "times": [ 600, 300, 60, 10],         -- warn users at 10 mins, 5 mins, 1 min and 10 sec before the event
        "text": "!!! Server will {what} in {when} !!!"
      },
      "presets": {                            -- Weather presets (see below)
          "Winter Nighttime": {"start_time": "03:00", "date": "2016-01-10", "temperature": -10, "clouds": "RainyPreset1", "wind": {"at8000":  {"speed": 10, "dir":  105}, "at2000": {"speed": 10, "dir": 130}, "atGround": {"speed": 5, "dir": 20}}},
          "Winter Daytime": {"start_time": "08:00", "date": "2016-01-10", "temperature": -12, "clouds": "Preset2", "wind": {"at8000":  {"speed": 10, "dir":  105}, "at2000": {"speed": 5, "dir": 130}, "atGround": {"speed": 5, "dir": 20}}},
          "Summer Nighttime": {"start_time": "03:00", "date": "2016-07-26", "temperature": 18, "clouds": "Preset1", "wind": {"at8000":  {"speed": 2, "dir": 305}, "at2000": {"speed": 5, "dir": 280}, "atGround": {"speed": 0, "dir": 290}}},
          "Summer Daytime": {"start_time": "10:00", "date": "2016-07-26", "temperature": 22, "clouds": "Preset2", "wind": {"at8000":  {"speed": 2, "dir": 305}, "at2000": {"speed": 5, "dir": 280}, "atGround": {"speed": 0, "dir": 290}}},
          "Heavy Storm": {"start_time": "16:00", "temperature": 16, "clouds": "RainyPreset3", "wind": {"at8000":  {"speed": 25, "dir": 305}, "at2000": {"speed": 20, "dir": 280}, "atGround": {"speed": 15, "dir": 290}}}
      }
    },
    {
      "installation": "instance1",
      "affinity": [2, 3],                     -- CPU affinity for this process
      "schedule": {
        "00-12": "NNNNNNN",                   -- instance1 will run everyday from 12 to 24 hrs, besides Sundays.
        "12-24": "YYYYYYN"
      },
      "extensions": [ "SRS" ],                -- which extensions should be started / stopped with the server
      "restart": {
        "method": "restart_with_shutdown",    -- restarts the whole server instead only the mission
        "mission_time": 480,                  -- restart the mission after 8 hrs (480 minutes)
        "populated": false                    -- no restart of the mission (!), as long as people are in
      },
      "reset": "run:del \"{dcs_installation}\\SnowfoxMkII*.lua\""   -- delete files (persistency) on .reset command
    },
    {
      "installation": "instance2",
      "schedule": {
        "00-12:30": "YYYYYYY",                -- instance2 runs Sunday all day, rest of the week between 00 and 12:30 hrs
        "12:30-24": "NNNNNNY"
      },
      "extensions": [ "SRS" ],                -- which extensions should be started / stopped with the server
      "restart": {                            -- missions rotate every 4 hrs
        "method": "rotate",
        "local_times": ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"],
        "settings": {                         -- Weather will change on a time basis
          "00:00-07:59": "Winter Nighttime",
          "08:00-11:59": "Winter Daytime",
          "12:00-19:59": "Summer Daytime",
          "20:00-23:59": "Summer Nighttime"
        }
      },
     "onMissionEnd": "load:Scripts/net/persist.lua", -- load a specific lua on restart 
     "onShutdown": "run:shutdown /s"                 -- shutdown the PC when DCS is shut down
    },
    {
      "installation": "missions",
      "schedule": {
        "21:30": "NNNNNYN",                   -- Missions start on Saturdays at 21:30, so start the server there
        "23:00-00:00": "NNNNNPN",             -- Mission ends somewhere between 23:00 and 00:00, so shutdown when no longer populated        
        "settings": [                         -- Weather will change randomly
          "Winter Daytime",
          "Summer Daytime"
        ]
      },
     "onMissionStart": "load:Script/net/f10menu.lua"  -- load some lua in the mission on mission start
    }
  ]
}
```

### Section "warn"

| Parameter       | Description                                                                                                                                                                                                        |
|-----------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| times           | List of seconds, when a warning should be issued.                                                                                                                                                                  |
| text            | A customizable message that will be sent to the users when a restart is pending.<br/>{what} will be replaced with what is happening (restart, shutdown, rotate)<br/>{when} will be replaced with the time to wait. |

### Section "preset"

| Parameter        | Description                                     |
|------------------|-------------------------------------------------|
|                  | First parameter is the name of the preset.      |
| date             | The missions date.                              |
| start_time       | The missions start time in seconds.             |
| temperature      | Temperature in °C.                              |
| clouds           | Name of a DCS cloud preset.                     |
| wind             | Wind atGround, at2000 (m) and at8000 (m) in m/s |
| qnh              | Pressure at sea level in mmHg                   |
| groundTurbulence | Ground turbulence in 0.1 * meters               |
| dust_density     | Dust density in meters, 0 = off                 |


### Section "schedule"

| First Parameter                                                                                                                                                                                                         | Second Parameter                                                                                                                                                                                                                                                   |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Timeframe, with start and endtime in either HH24 or HH24:MM format.<br/>If only one time is provided, the action (see second parameter) has to happen at exactly this time.                                             | The second parameter contains a character for every day, starting Mo and ending Su. Depending on the character, the behaviour will be selected:<br/>Y, N or P - the server should run in that timeframe (Y) or not (N). P means, it should only run, if populated. |
| __Examples:__<br/>Time between 12:30h and 18:00h => 12:30-18:00<br/>Time between 09:00h and 21:30h => 09-21:30<br/>Time between 21:00h and 03:00h => 21-03 (next day!)<br/>All day long (00:00h - 24:00h) => 00-24<br/> | __Examples:__<br/>YYYYYYY => every day<br/>YYYYYNN => weekdays only<br/>&nbsp;<br/>&nbsp;                                                                                                                                                                          |
See the above examples for a better understanding on how it works.

### Section "extensions"

A list of extensions that should be started / stopped with the server. Currently, only SRS is supported.
If SRS is listed as an extension, a configured SRS server will be started with the DCS server.

### Section "restart"

| Parameter    | Description                                                                                                                                                                                                                                                    |
|--------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| method       | One of **restart**, **restart_with_shutdown** or **rotate**.<br/>- "restart" will restart the current mission,<br/>- "restart_with_shutdown" will do the same but shutdown the whole server<br/>- "rotate" will take the next mission out of the mission list. |
| mission_time | Time in minutes (according to the mission time passed) when the mission has to be restarted.                                                                                                                                                                   |
| local_times  | List of times in the format HH24:MM, when the mission should be restated or rotated (see method).                                                                                                                                                              |
| populated    | If **false**, the mission will be restarted / rotated only, if no player is in.                                                                                                                                                                                |
| settings     | Timeframes in which a weather preset is valid or a list of presets that should change randomly. If not provided, the mission will run as is.                                                                                                                   |

**Attention!**<br/>
If using the presets / settings, your missions will be amended automatically by the bot. You might want to create safety copies upfront.

### on-commands

| Parameter      | Description                                                                                  |
|----------------|----------------------------------------------------------------------------------------------|
| onMissionStart | Called at the start of a mission (onSimulationStart).                                        |
| onMissionEnd   | Called, when the Scheduler (!) ends a mission. Not if the mission is ended in any other way. |
| onShutdown     | Called, when the DCS server is being shut down.                                              |

Commands can be executed in different ways:

| Starts with      | Description                                                     | Example                                                               |
|------------------|-----------------------------------------------------------------|-----------------------------------------------------------------------|
| load             | Load an external lua file into the mission (do_script_file).    | load:Scripts/net/test.lua                                             |
| lua              | Run this lua script inside the mission environment (do_script). | lua:dcsbot.restartMission()                                           |
| call             | Send a DCSServerBot command to DCS.                             | call:shutdown()                                                       | 
| run              | Run a Windows command (via cmd.exe).                            | run:shutdown /s                                                       |

The following environment variables can be used in the "run" command:

| Variable         | Meaning                         |
|------------------|---------------------------------|
| dcs_installation | DCS installation path           |
| dcs_home         | Saved Games directory           |
| server           | internal server datastructure   |
| config           | dcsserverbot.ini representation |


## Section "reset"

| Parameter | Description                                                           |
|-----------|-----------------------------------------------------------------------|
|           | Command / Script to be run at .reset. The on-command syntax applies.  |

## Discord Commands

If a server gets started or stopped manually (using .startup / .shutdown), it will be put in "maintenance" mode.
To clear this and give the control back to the scheduler, use the following command.

| Command | Parameter | Channel       | Role      | Description                                          |
|---------|-----------|---------------|-----------|------------------------------------------------------|
| .clear  |           | admin-channel | DCS Admin | Clears the maintenance state of a server.            |
| .preset |           | admin-channel | DCS Admin | Changes the preset (date/time/weather) of a mission. |
| .reset  |           | admin-channel | DCS Admin | Calls a configurable reset command.                  |
