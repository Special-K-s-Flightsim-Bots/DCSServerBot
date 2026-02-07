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
  ping_everyone:                # Handling of @everyone or @here pings
    report: true                # create an audit report (default: true)
    timeout: 60                 # optional: timeout for 60 minutes (default: 60 minutes, 0 to disable)
    kick: false                 # optional: kick member on mentioning (default: false)
  on_join:                      # Auto-generate a welcome message for a user
    message: Welcome {name} to this server!
    mention: 9988776655443322   # Optional: Role id to mention (in addition to the user itself, if configured)
    channel: 1199228833774466   # -1 for DM, see also greeting_dm in bot.yaml!
  roles:                        # Auto-generate a message if a user gets or loses a specific role
    1122334455667788:           # role name or id
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
  reaction:                               # Auto-generate a reaction message to give roles to people
    channel: 1122334455667788             # The channel id to hold the reaction message
    title: Welcome to {guild}!
    message: |
      Please react to give yourself a role!
    bot_trap: true                        # If true, a bot trap will be generated to auto-kick bot users that klick this role
    roles:
      '🇹':
        role: Tester                      # can be a role or id
        message: Test Role                # message to be displayed to users 
      '🇦': 
        role: 11223344556677
        message: Application Role
```

> [!IMPORTANT]
> You need to give DCSServerBot the following additional permissions:
> - Manage Roles (you need that for other plugins also already)
> - Time out members (if you enable timeout on everyone / here pings)
> - Kick, Approve and Reject Members (if you want to enable member kicking)

## Discord Commands

| Command  | Parameter                                              | Channel | Role      | Description                                                                |
|----------|--------------------------------------------------------|---------|-----------|----------------------------------------------------------------------------|
| /addrole | member role                                            | all     | Admin     | Allow DCS Admins to add roles to people that are below the bots role.      |
| /delrole | member role                                            | all     | Admin     | Allow DCS Admins to remove roles from people that are below the bots role. |
| /clear   | [channel] [older_than] [ignore] [after_id] [before_id] | all     | Admin     | Purge a channel (default: current).                                        |
