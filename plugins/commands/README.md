# Plugin "Commands"
With this plugin you can create your own Discord commands, that either run a command on your PC, send events to one
or more DCS servers and - if required - parse the result and display it in Discord as a message or embed.  It can be 
used to start specific tasks like backups or other game servers or to read some lua table from a running mission
and display the result with DCSServerBot.

This is a very powerful plugin, but it can do much harm if not handled with care!

## Configuration
Each command has a name and parameters. These parameters can be used in the arguments you use to call your external 
program. The sample shows how to launch DCS from a specific command (not really useful as you have .startup, but you
get the idea) and dir as a shell command. 

```json
{
  "commands": [
    {
      "name": "dcs",                  -- new Discord command .dcs
      "description": "Launch DCS",    -- Description for help command
      "roles": ["DCS Admin"],         -- who can use this command,
      "params": [ "instance" ],       -- supported parameters (string only)
      "execute": {
        "cwd": "C:\\Program Files\\Eagle Dynamics\\DCS World OpenBeta Server\\bin",
        "cmd": "DCS.exe",
        "args": "-w {instance}"       -- here you see the parameter being used!
      }
    },
    {
      "name": "dir",                  -- new Discord command .dir
      "description": "Directory listing",
      "roles": ["Admin", "DCS Admin"],
      "hidden": true,                 -- command is hidden from .help
      "execute": {
        "shell": true,                -- will be run as a shell (cmd) command
        "cwd": "C:\\",
        "cmd": "dir"
      }
    },
    {
      "name": "server_name",          -- new Discord command .server_name
      "description": "Display server name",
      "server_only": true,            -- must be run in ADMIN_CHANNEL, CHAT_CHANNEL or STATUS_CHANNEL
      "execute": {
        "shell": true,
        "cmd": "echo",
        "args": "{server.name}"       -- if you run a command in a server channel or with specifying a server, you can access it
      }
    },
    {
      "name": "shutdown_all",       -- new Discord command .shutdown_all,
      "description": "Shutdown all servers",
      "server_only": false,         -- this event will be sent to ALL servers
      "event": {                    -- send an event to your DCS server(s)
        "command": "shutdown"       
      }
    },
    {
      "name": "mission_status",     -- new Discord command .mission_status
      "description": "Display Mission Status",
      "server": "My Server Name",   -- run it on a specific server only, can be a list of servers with []
      "event": {
        "sync": true,               -- we need to wait for the response
        "command": "getVariable",   -- send a getVariable event
        "name": "myMissionStatus"   -- name of the variable (lua table in your mission environment)
      },
      "report": "my_report.json"    -- name of the report the result will be passed to (and displayed as an embed)
    }
  ]
}
```
When the command is being run in a server channel, you have access to _server_ as a parameter. So you can use things like
```json
{server.name}
```
for instance. To force that, set "server_only" to true.
If you want to run the command on one specific server only, you can add the server instance with "server".

**Attention:**</br>
* DCSServerBot needs to have the permissions to launch the respective executable!
* Do not run long running shell scripts!
