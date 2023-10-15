# Plugin "Commands"
This plugin is a very low level plugin that lets you generate your own discord commands, based on a descriptive language. 
It can be used to start specific tasks on your PC, send a specific event to DCS or anything the like.

## Configuration
Each command has a name and parameters. These parameters can be used in the arguments you use to call your external 
program. The sample shows how to launch DCS from a specific command (not really useful as you have .startup, but you
get the idea) and dir as a shell command. 

```yaml
command_prefix: .   # The prefix to use for these discord commands
commands:
- name: dcs         # Create a command .dcs
  roles:            # that can be run by the DCS Admin role
  - DCS Admin
  cmd:              # and map it to a call of DCS_server.exe
    args: -w {instance}
    cwd: C:\Program Files\Eagle Dynamics\DCS World OpenBeta Server\bin
    exe: DCS_server.exe
  params:           # which receives a parameter "instance" (will be auto replaced by the instance name)
  - instance
- name: dir         # Create a command .dir
  roles:            # which can be run by Admin and DCS Admin
  - Admin
  - DCS Admin
  cmd:              # and that maps to a shell command "dir c:\"
    cwd: C:\
    exe: dir
    shell: true
  hidden: true      # the .dir command will not apply in the help command
```
> ⚠️ **Attention:**</br>
> * DCSServerBot needs to have the permissions to launch the respective executable!
> * Do not run long-running shell scripts!
> + These commands are NO slash commands, so they start with another prefix, set in the configuration!
