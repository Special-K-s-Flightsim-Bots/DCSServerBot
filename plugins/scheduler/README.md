# Plugin Scheduler
With this plugin you can decide when to run your DCS servers, when to run which mission and how long a specific mission shall run, either in local time or in mission time.
Tasks that can be achieved with this solution are:
* Have a server rotate a mission every 4 hrs.
* Restart the mission before it gets dark.
* Remove the password to have the mission public every day from 08:00 to 12:00, besides that keep it private.

## Configuration
```json
{
  "configs": [
    {
      "warn": {
        "times": [ 600, 300, 60, 10],                           -- warn users at 10 mins, 5 mins, 1 min and 10 sec before restart
        "text": "!!! Server will restart in {} seconds !!!"
      }
    },
    {
      "installation": "instance1",
      "schedule": {
        "00-12": "NNNNNNN",                   -- instance1 will run everyday besides Sundays from 12 to 24 hrs.
        "12-24": "YYYYYYN"
      },
      "extensions": [ "SRS" ],                -- which extensions should be started / stopped with the server
      "restart": {
        "method": "restart",                  -- one of "restart" or "rotate"
        "mission_time": 480,                  -- restart the mission after 8 hrs
        "populated": false                    -- no restart, if people are flying
      }
    },
    {
      "installation": "instance2",
      "missions": [
        "Punishment Test.miz",                -- Mission 1, has to be in the servers Mission directory
        "Slot Blocking Test.miz",             -- Mission 2
        "Test Bot Commands.miz"               -- Mission 3
      ],
      "schedule":                             -- have specific missions running at specific times from 00 to 12 hrs
      { 
        "00-04": "1111111",
        "04-08": "2222222",
        "08-12": "3333333",
        "12-24": "NNNNNNN"
      },
      "extensions": [ "SRS" ]                 -- which extensions should be started / stopped with the server
    },
    {
      "installation": "instance3",
      "schedule": {
        "00-12": "YYYYYYY",                   -- instance3 runs Sunday all day, rest of the week between 00 and 12 hrs
        "12-24": "NNNNNNY"
      },
      "extensions": [ "SRS" ],                -- which extensions should be started / stopped with the server
      "restart": {                            -- missions rotate every 4 hrs
        "method": "rotate",
        "local_times": [ "00:00", "04:00", "08:00" ],
        "shutdown": true                      -- shuts DCS down on every rotate to clean the memory
      }
    }
  ]
}
```

### Section "restart"

| Parameter    | Description                                                                                                                            |
|--------------|----------------------------------------------------------------------------------------------------------------------------------------|
| method       | One of **restart** or **rotate**. restart will restart the current mission, rotate will take the next mission out of the mission list. |
| mission_time | Time in minutes (according to the mission time passed) when the mission has to be restarted.                                           |
| local_times  | List of times in the format HH24:MM, when the mission should be restated or rotated (see method).                                      |
 | populated    | If **false**, the server will be restarted only, if no player is in.                                                                   |
 | shutdown     | If **true**, the mission will not only be restarted but the whole DCS server.                                                          |

### Section "warn"

| Parameter       | Description                                                                                                                            |
|-----------------|----------------------------------------------------------------------------------------------------------------------------------------|
| times           | List of seconds, when a warning should be issued.                                                                                      |
| text            | A customizable message that will be sent to the users when a restart is pending.                                                       |

### Section "missions"

**// This is not implemented yet! //**<br/>
A list of missions, that should be run on this server. If not specified, the missions will be taken from the serverSettings.lua (default).
<br/>**// This is not implemented yet! //**

### Section "schedule"

| First Parameter            | Second Parameter                                                                                                                                                                                                                                                                                  |
|----------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Time in format "HH24-HH24" | The second parameter contains a character for every day, starting Mo and ending Su.<br/>Depending on the character, the behaviour will be selected:<br/>Y or N - the server should run in that timeframe (Y) or not (N).<br/>1..2..3 - Run a specific mission with this number in this timeframe. |
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

