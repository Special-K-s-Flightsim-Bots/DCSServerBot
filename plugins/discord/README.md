# Plugin "Discord"
Add some helper commands to your Discord server.

## Configuration
As Discord is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - discord
```

## Discord Commands

| Command  | Parameter                                              | Channel | Role      | Description                                                                |
|----------|--------------------------------------------------------|---------|-----------|----------------------------------------------------------------------------|
| /addrole | member role                                            | all     | Admin     | Allow DCS Admins to add roles to people that are below the bots role.      |
| /delrole | member role                                            | all     | Admin     | Allow DCS Admins to remove roles from people that are below the bots role. |
| /clear   | [channel] [older_than] [ignore] [after_id] [before_id] | all     | Admin     | Purge a channel (default: current).                                        |
