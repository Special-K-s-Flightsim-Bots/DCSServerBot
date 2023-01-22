# Plugin "Commands"
This plugin is a very low level plugin that lets you start commands on the server PC and map them to discord commands. 
It can be used to start specific tasks like backups or other game servers with DCSServerBot.

## Configuration
Each command has a name and parameters. These parameters can be used in the arguments you use to call your external 
program. The sample shows how to launch DCS from a specific command (not really useful as you have .startup, but you
get the idea) and dir as a shell command. 

```json
{
  "commands": [
    {
      "name": "dcs",                  -- name of the command
      "roles": ["DCS Admin"],         -- who can use this command
      "params": [ "instance" ],       -- suppoerted parameters (string only)
      "cmd": {
        "cwd": "C:\\Program Files\\Eagle Dynamics\\DCS World OpenBeta Server\\bin",
        "exe": "DCS.exe",
        "args": "-w {instance}"       -- here you see the parameter being used!
      }
    },
    {
      "name": "dir",
      "roles": ["Admin", "DCS Admin"],
      "hidden": true,                 -- command is hidden from .help
      "cmd": {
        "shell": true,
        "cwd": "C:\\",
        "exe": "dir"
      }
    }
  ]
}
```
**Attention:**</br>
* DCSServerBot needs to have the permissions to launch the respective executable!
* Do not run long running shell scripts!
