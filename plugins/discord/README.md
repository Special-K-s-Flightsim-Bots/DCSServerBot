# Plugin "Discord"
Add some helper commands to your Discord server.

## Configuration
As Discord is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - discord
```
You can configure the plugin with an optional `config\plugins\discord.yaml`:
```yaml
DEFAULT:
  on_join:
    message: Welcome {name} to this server!
    mention: 9988776655443322   # Optional: Role id to mention (in addition to the user itself, if configured)
    channel: 1199228833774466   # -1 for DM, see also greeting_dm in bot.yaml!
  roles:
    1122334455667788:   # role id of role "Sample"
      on_add:
      message: '{mention}, welcome to the Sample role!'
      mention: '@here'                  # Optional: Role to mention (in addition to the user itself, if configured)
      channel: 9988776655443322         # -1 for DM
    on_remove:
      message: You lost the Sample role!
      channel: -1                       # -1 for DM
    on_leave:
      message: '{name}, see you next time!'
      channel: -1                       # -1 for DM
```


## Discord Commands

| Command  | Parameter                                              | Channel | Role      | Description                                                                |
|----------|--------------------------------------------------------|---------|-----------|----------------------------------------------------------------------------|
| /addrole | member role                                            | all     | Admin     | Allow DCS Admins to add roles to people that are below the bots role.      |
| /delrole | member role                                            | all     | Admin     | Allow DCS Admins to remove roles from people that are below the bots role. |
| /clear   | [channel] [older_than] [ignore] [after_id] [before_id] | all     | Admin     | Purge a channel (default: current).                                        |
