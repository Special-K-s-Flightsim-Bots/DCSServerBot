# Plugin "Commands"
This plugin is a very low level plugin that lets you generate your own discord commands, based on a descriptive language. 
It can be used to start specific tasks on your PC, send a specific event to DCS or anything the like.

## Configuration
As Commands is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - commands
```

The plugin itself needs to be configured with a yaml file in config/plugins/commands.yaml. Each command has a name and 
parameters. These parameters can be used in the arguments you use to call your external program. The sample shows how 
to launch DCS from a specific command (not really useful as you have `/server startup`, but you get the idea) and `dir` 
as a shell command. 

```yaml
commands:
  startup:              # Create a command /startup
    roles:              # that can be run by the DCS Admin role
    - DCS Admin
    execute:            # and map it to a call of DCS_server.exe
      cmd: DCS_server.exe
      args: -w {instance.name}
      cwd: C:\Program Files\Eagle Dynamics\DCS World Server\bin
    params:             # parameter list
      instance:         # instance parameters have auto completion
        required: true  # mandatory parameter
  dir:                  # Command /dir
    description: shows a directory listing
    roles:              # dir can be executed be Admin and DCS Admin
    - Admin
    - DCS Admin
    execute:
      shell: true       # Run dir as a shell command. You want to use this also for bat or cmd files.
      cmd: dir
      cwd: '{path}'
      args: '{option}'
    params:
      option:
        description: Options for the dir command
        type: str
        required: false
      path:
        description: Directory listing of this path
        type: str
        required: false
        default: C:\Program Files\Eagle Dynamics\DCS World
    ephemeral: true     # The commands output should be ephemeral
  setflag:
    roles:
    - DCS Admin
    event:                  # Instead of running a command, send an event to DCS
      command: setFlag      # setFlag takes 2 parameters
      flag: '{flag}'
      value: '{value}'
    params:
      server:               # we need to provide a server for events, otherwise they will be run on all servers
        required: true
      flag:
        description: Flag to be set
        required: true
        type: int
        choices:
          - 100
          - 110
          - 120
      value:
        description: Value to set
        type: int
        required: true
        choices:
          - 1
          - 2
          - 3
```
> [!IMPORTANT]
> DCSServerBot needs to have the permission to launch the respective executables.

> [!WARNING]
> Do **not** run long-running shell scripts. 
> Normal tasks can be long-running. You have commands to terminate them (see below).

### Parameter Structure
```yaml
params:
  name:                 # name of the parameter
    type: str           # One of str, int, bool, member, channel, role, mentionable, number, attachment
    description: xxx    # description of the parameter
    required: true      # default: false
    default: xxx        # A value to be set as default, if required = false. If not set, NONE will be applied.
    nsfw: false         # Commands for NSFW-channels, default: false
    min_value: 0        # Optional: min value for range
    max_value: 10       # Optional: max value for range 
    choices:            # Optional: choice of values (type must match the type above)
      - A
      - B
      - C
```

### Special Parameters

The following special parameters are supported and will be replaced by auto-completion if available:
- node
- instance
- server
- user
- member
- channel
- role

These parameters will be passed as objects.<br>
You can keep it simple in the params section:
```yaml
params:
  server:
    required: true
```

## Discord Commands

| Command             | Parameter | Channel | Role  | Description                                                  |
|:--------------------|:----------|:--------|:------|:-------------------------------------------------------------|
| /commands tasklist  |           | all     | Admin | Show all running processes that were started by this plugin. |
| /commands terminate | process   | all     | Admin | Terminate a running process.                                 |
