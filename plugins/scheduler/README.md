# Plugin Scheduler
With this plugin you can decide when to run your DCS servers, when to run which mission and how long a specific mission 
shall run, either in local time or in mission time. Tasks that can be achieved with this solution are below others:
* Have a server rotate a mission every 4 hrs.
* Restart the mission before it gets dark.
* Have two servers run alternately, maybe one with password, one public
* Change time and weather in your mission on specific times or randomly

The plugin is one of if not the most complex plugins of DCSServerBot. Read this documentation thoroughly. 

## Configuration
Examples:
```yaml
DEFAULT:
  warn:                                           # warn times before a restart / shutdown
    text: '!!! {item} will {what} in {when} !!!'  # Message to be displayed as a popup in DCS. These variables can be used in your own message. 
    times:                                        # List of times when a message will be displayed
    - 600
    - 300
    - 60
    - 10
DCS.openbeta_server:                              
  schedule:                                       # Server "DCS.openbeta_server" will run 24x7
    00-24: YYYYYYY
instance2:
  schedule:                                       # Server "instance2" will run every day from 0h-12h local time (LT)
    00-12: YYYYYYY
    12-24: NNNNNNN
  restart:                                        # at 04:00 and 08:00 LT ..
    local_times:
    - 04:00
    - 08:00
    method: rotate                                # .. it will rotate ..                                
    populated: true                               # .. independently if players are flying or not. 
  onMissionStart: load:Scripts/net/start.lua      # We will run a specific lua script on mission start
  onMissionEnd: load:Scripts/net/end.lua          # We will run a specific lua script on mission end
  onShutdown: run:shutdown /r                     # if the DCS server is shut down, the real PC will restart
instance3:
  schedule:                                       # server "instance3" will run every day from noon to midnight
    00-12: NNNNNNN
    12-24: YYYYYYY
  restart:                                        # It will restart with a DCS server shutdown after 480 mins of mission time ..
    method: restart_with_shutdown
    mission_time: 480
    populated: false                              # .. only, if nobody is on the server (or as soon as that happens afterwards)
mission:
  schedule:
    18-00: NNNNNNY                                # our mission server will only run on Sundays from 18h - midnight LT
```

### Section "warn"

| Parameter       | Description                                                                                                                                                                                                                                                                                                      |
|-----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| times           | List of seconds, when a warning should be issued.                                                                                                                                                                                                                                                                |
| text            | A customizable message that will be sent to the users when a restart is pending.<br/>{item} will be replaced with either "server" or "mission", depending on what's happening.<br/>{what} will be replaced with what is happening (restart, shutdown, rotate)<br/>{when} will be replaced with the time to wait. |

### Section "schedule"

| First Parameter                                                                                                                                                                                                         | Second Parameter                                                                                                                                                                                                                                                   |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Timeframe, with start and end-time in either HH24 or HH24:MM format.<br/>If only one time is provided, the action (see second parameter) has to happen at exactly this time.                                            | The second parameter contains a character for every day, starting Mo and ending Su. Depending on the character, the behaviour will be selected:<br/>Y, N or P - the server should run in that timeframe (Y) or not (N). P means, it should only run, if populated. |
| __Examples:__<br/>Time between 12:30h and 18:00h => 12:30-18:00<br/>Time between 09:00h and 21:30h => 09-21:30<br/>Time between 21:00h and 03:00h => 21-03 (next day!)<br/>All day long (00:00h - 24:00h) => 00-24<br/> | __Examples:__<br/>YYYYYYY => every day<br/>YYYYYNN => weekdays only<br/>&nbsp;<br/>&nbsp;                                                                                                                                                                          |
See the above examples for a better understanding on how it works.

### Section "restart"

| Parameter        | Description                                                                                                                                                                                                                                                                                                                |
|------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| method           | One of **restart**, **restart_with_shutdown**, **rotate** or **shutdown**.<br/>- "restart" will restart the current mission,<br/>- "restart_with_shutdown" will do the same but shutdown the whole server<br/>- "shutdown" will only shutdown the server<br/>- "rotate" will launch the next mission in the mission list.  |
| mission_time     | Time in minutes (according to the mission time passed) when the mission has to be restarted.                                                                                                                                                                                                                               |
| max_mission_time | Time in minutes (according to the mission time passed) when the mission has to be restarted, even if people are in.                                                                                                                                                                                                        |
| local_times      | List of times in the format HH24:MM, when the mission should be restated or rotated (see method).                                                                                                                                                                                                                          |
| populated        | If **false**, the mission will be restarted / rotated only, if no player is in (default: true).                                                                                                                                                                                                                            |
| mission_end      | Only apply the method on mission end (usually in combination with restart_with_shutdown).                                                                                                                                                                                                                                  |

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

| Variable         | Meaning                       |
|------------------|-------------------------------|
| dcs_installation | DCS installation path         |
| dcs_home         | Saved Games directory         |
| server           | internal server datastructure |

## Discord Commands

| Command             | Parameter | Channel       | Role      | Description                                                                                                                                |
|---------------------|-----------|---------------|-----------|--------------------------------------------------------------------------------------------------------------------------------------------|
| /server list        |           | all           | DCS       | Lists all available servers.                                                                                                               |
| /server startup     |           | admin-channel | DCS Admin | Starts a dedicated DCS server process.                                                                                                     |
| /server shutdown    | [force]   | admin-channel | DCS Admin | Shuts the dedicated DCS server process down.<br/>If force is used, no player check will be executed and no onShutdown command will be run. |
| /server start       |           | admin-channel | DCS Admin | Starts a stopped DCS server.                                                                                                               |
| /server stop        |           | admin-channel | DCS Admin | Stops a DCS server.                                                                                                                        |
| /server maintenance |           | admin-channel | DCS Admin | Sets the servers maintenance mode.                                                                                                         |
| /server clear       |           | admin-channel | DCS Admin | Clears the maintenance state of a server.                                                                                                  |
| /server password    |           | admin-channel | DCS Admin | Sets a new server or [coalition](../../COALITIONS.md) password.                                                                            |
| /server config      |           | admin-channel | DCS Admin | Changes the configuration of a server, like name, password, max players.                                                                   |
| /server rename      |           | admin-channel | DCS Admin | Rename the respective DCS server. Handle with care!                                                                                        |
| /server migrate     | instance  | admin-channel | DCS Admin | WIP: Migrate a server to another instance (maybe even node).                                                                               |

> ⚠️ **Attention!**<br>
> If a server gets started or stopped manually (using `/server startup` or `/server shutdown`), it will be put into 
> "maintenance" mode. To clear this and give the control back to the scheduler, use `/server clear`.<br>
> You can put a server into maintenance mode manually, by using `/server maintenance`.
