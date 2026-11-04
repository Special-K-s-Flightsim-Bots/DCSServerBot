# Roles and permissions

Every command needs a role. The bot uses its own **internal** roles and maps them to your Discord
roles, so the same permission model works with or without Discord.

| Role | What it allows |
|:-----|:---------------|
| **Admin** | Manage the server: start it, shut it down, update it, change the password, gather server statistics. |
| **DCS Admin** | Restart missions, manage the mission list, ban and unban players. |
| **DCS** | Chat, check own statistics, read information about running missions and players. |
| **GameMaster** | See both coalitions and run the mission-serving commands. |

!!! note
    The bot's owner — `owner` in `config/services/bot.yaml` — can run *any* command, whatever role
    the mapping gives them.

## Mapping your roles

In `config/services/bot.yaml` you map each internal role to one or more of your Discord roles:

```yaml
roles:
  Admin:                      # internal role
  - Admin                     # your Discord role(s)
  Alert:                      # pinged on crashes and low performance
  - DCS Admin
  DCS Admin:
  - Moderator
  - Staff
  GameMaster:
  - Staff
  DCS:
  - '@everyone'               # only @everyone needs the leading @
```

A role you do not map keeps its default: the internal role name itself.

!!! important
    The bot's own role has to sit **above** every role it hands out or manages. If it should give
    people an "Online" role, its role must be higher in the Discord role list than that role.

## Without Discord

A non-Discord installation has no Discord roles to map, so you list UCIDs under each role instead.
Most installations only need `Admin` and `DCS Admin`.

Coalition roles are a separate mapping — see [Coalitions](../coalitions.md).
