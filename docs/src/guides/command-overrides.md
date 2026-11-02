# Overriding commands and chat commands

Every command can be renamed, restricted to other roles, given a different description, or disabled
— without touching the code.

## Discord commands

The override goes into the plugin's configuration, under `commands:`, following the command tree.
`/dcs bans` lives in the `dcs` group, so:

```yaml
# config/plugins/admin.yaml
commands:
  dcs:
    bans:
      roles:
      - Admin
      enabled: false
      name: prohibiciones
      description: mostrar una lista de todas las prohibiciones en sus servidores
```

With that in place the command is called `/dcs prohibiciones`, shows the new description, is
available to the Admin role only, and `enabled: false` would remove it entirely. Pick one — a
disabled command does not appear whatever its name.

## Chat commands

In-game chat commands are overridden the same way, under `chat_commands:`, keyed by the command
without the prefix:

```yaml
# config/plugins/mission.yaml
chat_commands:
  911:
    enabled: false
```

That line removes `-911` from your servers.

## What you cannot change

The command tree itself: a group command stays a group. Only the name, description, roles and
visibility of an existing command are yours to change.

The full list of commands, with their roles and options, is in the
[command reference](../reference/commands.md).
