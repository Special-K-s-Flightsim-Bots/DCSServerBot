# Plugin Scheduler
With this plugin you can decide when to run your DCS servers, when to run which mission and how long a specific mission shall run, either in local time or in mission time.
Tasks that can be achieved with this solution are:
* Have a server rotate a mission every 4 hrs.
* Restart the mission before it gets dark.
* Have two servers run alternately, maybe one with password, one public

## Configuration
Examples:
```json
{
  "configs": [
    {
      "warn": {
        "times": [ 600, 300, 60, 10],         -- warn users at 10 mins, 5 mins, 1 min and 10 sec before restart
        "text": "!!! Server will restart in {} seconds !!!"
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
      }
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
        "local_times": [ "00:00", "04:00", "08:00" ],
      }
    },
    {
      "installation": "missions",
      "schedule": {
        "21:30": "NNNNNYN",                   -- Missions start on Saturdays at 21:30, so start the server there
        "23:00-00:00": "NNNNNPN"              -- Mission ends somewhere between 23:00 and 00:00, so shutdown when no longer populated        
      }
    }
  ]
}
```

### Section "restart"

| Parameter    | Description                                                                                                                                                                                                                                                    |
|--------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| method       | One of **restart**, **restart_with_shutdown** or **rotate**.<br/>- "restart" will restart the current mission,<br/>- "restart_with_shutdown" will do the same but shutdown the whole server<br/>- "rotate" will take the next mission out of the mission list. |
| mission_time | Time in minutes (according to the mission time passed) when the mission has to be restarted.                                                                                                                                                                   |
| local_times  | List of times in the format HH24:MM, when the mission should be restated or rotated (see method).                                                                                                                                                              |
 | populated    | If **false**, the mission will be restarted / rotated only, if no player is in.                                                                                                                                                                                |

### Section "warn"

| Parameter       | Description                                                                                                                            |
|-----------------|----------------------------------------------------------------------------------------------------------------------------------------|
| times           | List of seconds, when a warning should be issued.                                                                                      |
| text            | A customizable message that will be sent to the users when a restart is pending.                                                       |

### Section "schedule"

| First Parameter                                                                                                                                                                                                         | Second Parameter                                                                                                                                                                                                                                                   |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Timeframe, with start and endtime in either HH24 or HH24:MM format.<br/>If only one time is provided, the action (see second parameter) has to happen at exactly this time.                                             | The second parameter contains a character for every day, starting Mo and ending Su. Depending on the character, the behaviour will be selected:<br/>Y, N or P - the server should run in that timeframe (Y) or not (N). P means, it should only run, if populated. |
| __Examples:__<br/>Time between 12:30h and 18:00h => 12:30-18:00<br/>Time between 09:00h and 21:30h => 09-21:30<br/>Time between 21:00h and 03:00h => 21-03 (next day!)<br/>All day long (00:00h - 24:00h) => 00-24<br/> | __Examples:__<br/>YYYYYYY => every day<br/>YYYYYNN => weekdays only<br/>&nbsp;<br/>&nbsp;                                                                                                                                                                          |
See the above examples for a better understanding on how it works.

### Section "extensions"

A list of extensions that should be started / stopped with the server. Currently, only SRS is supported.
If SRS is listed as an extension, a configured SRS server will be started with the DCS server.

## Discord Commands

If a server gets started or stopped manually (using .startup / .shutdown), it will be put in "maintenance" mode.
To clear this and give the control back to the scheduler, use the following command.

| Command | Parameter | Channel | Role      | Description                               |
|---------|-----------|---------|-----------|-------------------------------------------|
| .clear  |           | all     | DCS Admin | Clears the maintenance state of a server. |
