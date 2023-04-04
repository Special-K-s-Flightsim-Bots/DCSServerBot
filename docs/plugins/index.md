---
has_children: true
nav_order: 4
---

# Plugin System

DCSServerBot is a modular system. It already provides a rich platform and many useful tools and utilities, 
but you can always extend the platform by writing your own custom plugin. The bot will take over the 
burden of making the different commands and codes available in DCS or Discord, but you still need to code 
a bit.

# List of supported Plugins

Plugin types:
- Mandatory plugins can not be disabled.
- Default plugins are enabled by default, but can be disabled. To remove them, overwrite `PLUGINS` in your `dcsserverbot.ini`.
- Optional plugins are disabled by default, but can be enabled by adding `OPT_PLUGINS` in your `dcsserverbot.ini`.

| Plugin       | Scope                                                               | Type      | Depending on          |
|--------------|---------------------------------------------------------------------|-----------|-----------------------|
| GameMaster   | Interaction with the running mission (inform users, set flags, etc) | Mandatory |                       |
| Mission      | Handling of missions, compared to the WebGUI.                       | Mandatory | GameMaster            |
| Admin        | Admin commands to manage your DCS server.                           | Default   |                       |
| Scheduler    | Autostart / -stop of servers or missions, change weather, etc.      | Default   | Mission               |
| UserStats    | Users statistics system.                                            | Default   | Mission               |
| CreditSystem | User credits, based on achievements.                                | Default   | Mission               |
| MissionStats | Detailed users statistics / mission statistics.                     | Default   | Userstats             |
| Cloud        | Cloud-based statistics and global ban system.                       | Default   | Userstats             |
| Punishment   | Punish users for teamhits or teamkills.                             | Optional  | Mission               |
| SlotBlocking | Slotblocking either based on units or a point based system.         | Optional  | Mission, Creditsystem |
| ServerStats  | Server statistics for your DCS servers.                             | Optional  | Userstats             |
| GreenieBoard | Greenieboard and LSO quality mark analysis (SC and Moose.AIRBOSS)   | Optional  | Missionstats          |
| MOTD         | Generates a message of the day.                                     | Optional  | Mission, Missionstats |
| FunkMan      | Support for [FunkMan](https://github.com/funkyfranky/FunkMan)       | Optional  |                       |
| DBExporter   | Export the whole DCSServerBot database as json.                     | Optional  |                       |
| OvGME        | Install or update mods into your DCS server.                        | Optional  |                       |
| Commands     | Map executables or shell commands to custom discord commands.       | Optional  |                       |
| Music        | Upload and play music over SRS.                                     | Optional  |                       |

### How to install 3rd-Party Plugins

Whenever someone else provides a plugin, they most likely do that as a zip file. You can just download any
plugin zipfile into the plugins directory. They will get unpacked automatically on the next start of DCSServerBot. 

