---
layout: default
title: README
nav_section: plugins/scheduler
---

# Plugin Scheduler
With this plugin you can decide when to run your DCS servers, when to run which mission and how long a specific mission 
shall run, either in local time, real time or in mission time. Tasks that can be achieved with this solution are below 
others:
* Have a server rotate a mission every 4 hrs.
* Restart the mission before it gets dark.
* Have two servers run alternately, maybe one with password, one public

The plugin is one of if not the most complex plugins of DCSServerBot. Read this documentation thoroughly. 

## Configuration
Examples:
```yaml
DEFAULT:
  startup_delay: 10                               # delay in seconds between the startup of each DCS instance (default: 10)
  warn:                                           # warn times before a restart / shutdown (see alternative format below)
    text: '!!! {item} will {what} in {when} !!!'  # Message to be displayed as a popup in DCS. These variables can be used in your own message. 
    times:                                        # List of times when a message will be displayed
    - 600
    - 300
    - 60
    - 10
DCS.release_server:                              
  schedule:                                       # Server "DCS.release_server" will run 24x7
    00-24: YYYYYYY
  startup:
    mission_id: 3                                 # Load mission #3 from the mission list on startup (could be a list also / random pick)
instance2:
  schedule:                                       # Server "instance2" will run every day from 0h-12h in the specified time zone
    timezone: Europe/Berlin                       # optional: timezone (default: local time)
    00-12: YYYYYYY
    12-24: NNNNNNN
  action:                                        # at 04:00 and 08:00 LT ..
    local_times:
    - 04:00
    - 08:00
    method: rotate                                # .. it will rotate ..                                
    populated: true                               # .. independently if players are flying or not. 
  onSimulationStart: load:Scripts/net/start.lua   # We will run a specific lua script on server start
  onSimulationStop: load:Scripts/net/stop.lua     # We will run a specific lua script on server stop (restart will trigger stop and start!)
  onMissionEnd: load:Scripts/net/end.lua          # We will run a specific lua script on mission end
  onShutdown: run:shutdown /r                     # if the DCS server is shut down, the real PC will restart
instance3:
  schedule:                                       # server "instance3" will run every day from noon to midnight
    00-12: NNNNNNN
    12-24: YYYYYYY
  action:                                        # It will restart with a DCS server shutdown after 480 mins of mission time ..
    method: restart
    shutdown: true
    mission_time: 480
    populated: false                              # .. only, if nobody is on the server (or as soon as that happens afterwards)
mission:
  schedule:
    18-00: NNNNNNY                                # our mission server will only run on Sundays from 18h - midnight LT
```

### Section "warn"

| Parameter       | Description                                                                                                                                                                                                                                                                                                      |
|-----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| times           | List of seconds, when a warning should be issued or alternatively a dictionary with seconds and specific warn texts.                                                                                                                                                                                             |
| text            | A customizable message that will be sent to the users when a restart is pending.<br/>{item} will be replaced with either "server" or "mission", depending on what's happening.<br/>{what} will be replaced with what is happening (restart, shutdown, rotate)<br/>{when} will be replaced with the time to wait. |

```yaml
  # Alternative format for warn, e. g. to display messages in your own language
  warn:
    times:
      600: Внимание сервер будет перезапущен через 10 минут! 
      300: До перезапуска 5 минут!
      60: Минутная готовность!
      10: Сервер перезапускается!
```

### Section "schedule"

| First Parameter                                                                                                                                                                                                         | Second Parameter                                                                                                                                                                                                                                                   |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Timeframe, with start and end-time in either HH24 or HH24:MM format.<br/>If only one time is provided, the action (see second parameter) has to happen at exactly this time.                                            | The second parameter contains a character for every day, starting Mo and ending Su. Depending on the character, the behaviour will be selected:<br/>Y, N or P - the server should run in that timeframe (Y) or not (N). P means, it should only run, if populated. |
| __Examples:__<br/>Time between 12:30h and 18:00h => 12:30-18:00<br/>Time between 09:00h and 21:30h => 09-21:30<br/>Time between 21:00h and 03:00h => 21-03 (next day!)<br/>All day long (00:00h - 24:00h) => 00-24<br/> | __Examples:__<br/>YYYYYYY => every day<br/>YYYYYNN => weekdays only<br/>&nbsp;<br/>&nbsp;                                                                                                                                                                          |
See the above examples for a better understanding on how it works.

### Section "startup"
Here you can provide a mission_id to be started on server startup.

### Section "action"

| Parameter        | Description                                                                                                                                                                                                                                                                      |
|------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| method           | One of **restart**, **rotate**, **load**, **stop** or **shutdown**.<br/>- "restart" will restart the current mission,<br/>- "shutdown" will only shutdown the server<br/>- "rotate" will launch the next mission in the mission list.<br/>- "load" will load a specific mission. |
| shutdown         | If true, the server will be shut down prior to restarting or rotating (default: false).                                                                                                                                                                                          |
| mission_time     | Time in minutes (according to the mission time passed) when the mission has to be restarted.                                                                                                                                                                                     |
| max_mission_time | Time in minutes (according to the mission time passed) when the mission has to be restarted, even if people are in.                                                                                                                                                              |
| real_time        | Time in minutes since the start of your server (not necessarily your mission, if that is paused for instance), when a restart should happen. Only works with restart_with_shutdown.                                                                                              |
| idle_time        | Time in minutes, the server was not in use (no people in the server)                                                                                                                                                                                                             |
| local_times      | List of times in the format HH24:MM, when the mission should be restated or rotated (see method).                                                                                                                                                                                |
| utc_times        | Like local_times but UTC.                                                                                                                                                                                                                                                        |
| mission_id       | For load only: the mission_id to load (1 = first from the mission list). Can be an integer or a list of integers for random pick.                                                                                                                                                |
| mission_file     | For load only: the mission file name to load (has to be in the mission list). Can be a single mission or list of missions for random pick.                                                                                                                                       |
| populated        | If **false**, the mission will be restarted / rotated only, if no player is in (default: true).                                                                                                                                                                                  |
| mission_end      | Only apply the method on mission end (usually in combination with restart_with_shutdown).                                                                                                                                                                                        |
| run_extensions   | If true, extensions will be applied to the mission prior to the restart / rotation (default: true) .                                                                                                                                                                             |

### on-commands

| Parameter         | Description                                                           |
|-------------------|-----------------------------------------------------------------------|
| onSimulationStart | Called at the start of a mission.                                     |
| onSimulationEnd   | Called, when the server stops.                                        |
| onMissionEnd      | Called, when the mission has ended with a win for either blue or red. |
| onShutdown        | Called, when the DCS server is being shut down.                       |

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

| Command                | Parameter                                  | Channel       | Role      | Description                                                                                                                                                                      |
|------------------------|--------------------------------------------|---------------|-----------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| /scheduler maintenance |                                            | admin-channel | DCS Admin | Sets the servers maintenance mode.                                                                                                                                               |
| /scheduler clear       |                                            | admin-channel | DCS Admin | Clears the maintenance state of a server.                                                                                                                                        |
| /server cleanup        |                                            | admin-channel | DCS Admin | Delete the temporary directories of a shutdown server.                                                                                                                           |
| /server config         |                                            | admin-channel | DCS Admin | Changes the configuration of a server, like name, password, max players.                                                                                                         |
| /server list           |                                            | all           | DCS       | Lists all available servers.                                                                                                                                                     |
| /server migrate        | instance                                   | admin-channel | DCS Admin | WIP: Migrate a server to another instance (maybe even node).                                                                                                                     |
| /server password       |                                            | admin-channel | DCS Admin | Sets a new server or [coalition](../../COALITIONS.md) password.                                                                                                                  |
| /server rename         |                                            | admin-channel | DCS Admin | Rename the respective DCS server. Handle with care!                                                                                                                              |
| /server restart        | [delay] [force] [run_extensions] [mission] | admin-channel | DCS Admin | Restarts a running DCS server and optionally launches another mission.                                                                                                           |
| /server shutdown       | [force] [maintenance]                      | admin-channel | DCS Admin | Shuts the dedicated DCS server process down.<br/>If force is used, no player check will be executed and no onShutdown command will be run. Optional: don't set maintenance flag. |
| /server start          |                                            | admin-channel | DCS Admin | Starts a stopped DCS server.                                                                                                                                                     |
| /server startup        | [maintenance] [mission] [run_extensions]   | admin-channel | DCS Admin | Starts a dedicated DCS server process and optionally launches a specified mission (default is last one). Optional: don't set maintenance flag, don't run extensions.             |
| /server stop           |                                            | admin-channel | DCS Admin | Stops a DCS server.                                                                                                                                                              |
| /server lock           | server                                     | admin-channel | DCS Admin | Locks a server. Nobody can join.                                                                                                                                                 |
| /server unlock         | server                                     | admin-channel | DCS Admin | Unlocks the server again. It will be auto-unlocked on any mission restart.                                                                                                       |
| /server timeleft       | server                                     | all           | DCS       | Shows the time until the next scheduled restart.                                                                                                                                 |

> [!IMPORTANT]
> If a server gets started or stopped manually (using `/server startup` or `/server shutdown`), it will be put into 
> "maintenance" mode unless specified otherwise with the optional maintenance parameter. To clear this and give the 
> control back to the scheduler, use `/server clear`.<br>
> You can put a server into maintenance mode manually, by using `/server maintenance`.

## In-Game Chat Commands
| Command      | Parameter | Role      | Description                                  |
|--------------|-----------|-----------|----------------------------------------------|
| -maintenance |           | DCS Admin | Enables maintenance mode for this server.    |
| -clear       |           | DCS Admin | Clears the maintenance mode for this server. |
| -timeleft    |           | all       | Displays the time until the next restart.    |
