# Plugin "Commands"
This plugin is a very low level plugin that lets you generate your own discord commands, based on a descriptive language. 
It can be used to start specific tasks on your PC, send a specific event to DCS or anything the like.

## Configuration
Each command has a name and parameters. These parameters can be used in the arguments you use to call your external 
program. The sample shows how to launch DCS from a specific command (not really useful as you have .startup, but you
get the idea) and dir as a shell command. 

```yaml
command_prefix: .   # The prefix to use for this discord command
commands:
- cmd:
    args: -w {instance}
    cwd: C:\Program Files\Eagle Dynamics\DCS World OpenBeta Server\bin
    exe: DCS.exe
  name: dcs
  params:
  - instance
  roles:
  - DCS Admin
- cmd:
    cwd: C:\
    exe: dir
    shell: true
  hidden: true
  name: dir
  roles:
  - Admin
  - DCS Admin
```
**Attention:**</br>
* DCSServerBot needs to have the permissions to launch the respective executable!
* Do not run long-running shell scripts!
+ These commands do NOT start with the prefix / but with another prefix, set in the configuration!
