---
title: Prepare Discord Server
parent: Installation
nav_order: 5
---

# Change configuration
- Open your Discord settings (not the server settings)
- For easier access to channel IDs, enable "Developer Mode" in "Advanced Settings" in Discord.

# Create text channels

The bot needs some text channels you must create prior the installation:
- For each DCS server you want to connect to the bot, you need to create the following channels:
  - status, admin, chat

Example:
If you want to use the bot with two DCS servers you need to create 6 channels:
- `srv1-status`, `srv1-admin`, `srv1-chat`
- `srv2-status`, `srv2-admin`, `srv2-chat`

{: .note }
> The channel names can be chosen freely.

You should add one additional channel for audit messages, e.g. `srv-audit`.
This channel is optional, but it is best practise to use it.

# Create Roles
The bot uses 4 roles with different level of rights to the commands (see command reference for details):
- Admin: Can everything that `DCS Admin`can do, but is also able to run commands (executables or scripts) on the Windows server
- DCS Admin: Can control all aspects of an DCS server (includes rights of GameMaster and DCS role)
- GameMaster: Is able to change missions and to change triggers, flags etc. within a mission (Includes rights of DCS role)
- DCS: Every Player who should be able to request statistics or status information

The easiest way is to create four role with these names, because per default the bot try to search and use these roles.
But you can change the role names the bot uses by changing the file `dcsserverbot.ini` after installation.

Example of custom role names:
```
[ROLES]
Admin = DcsSrvBot-WinAdmin
DCS Admin = DcsSrvBot-DcsAdmin
GameMaster = DcsSrvBot-GameMaster
DCS = DcsSrvBot-DcsUser
```

Maybe you have already some roles, and you want the bot to use them.
Every Discord member has to read your rules and got the role members after accepting them.
And you have a role DiscordAdmins which should have the rights of DCS Admin.
Then you can use this:

```
[ROLES]
DCS Admin = DiscordAdmins
DCS = members
```
