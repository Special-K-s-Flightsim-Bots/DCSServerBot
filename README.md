# Welcome to DCSServerBot!
You've found a comprehensive solution that lets you administrate your DCS instances via Discord, has built in per-server and per-user statistics and much more!
With its plugin system and reporting framework, DCSServerBot can be enhanced very easily to support whatever might come into your mind. 

This documentation will show you the main features, how to install and configure the bot and some more sophisticated stuff at the bottom, 
if you for instance run multiple servers maybe even over multiple locations. 

First let's see, what it can do for you!

---
## Plugins
DCSServerBot has a modular architecture with plugins that support specific Discord commands or allow events from connected DCS servers to be processed.
It comes with a rich set of default plugins but can be enhanced either by optional plugins provided by me or some that you wrote on your own.

### General Administrative Commands
These commands can be used to administrate the bot itself.

| Command     | Parameter | Channel       | Role    | Description                                                                                                                                                    |
|-------------|-----------|---------------|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| .reload     | [Plugin]  | all           | Admin   | Reloads one or all plugin(s) and their configurations from disk.                                                                                               |
| .upgrade    |           | all           | Admin   | Upgrades the bot to the latest available version (git needed, see below).                                                                                      |
| .rename     | newname   | admin-channel | Admin   | Renames a DCS server. DCSServerBot auto-detects server renaming, too.                                                                                          |
| .unregister |           | admin-channel | Admin   | Unregisters the current server from this agent.<br/>Only needed, if the very same server is going to be started on another machine connected to another agent. |

### List of supported Plugins
| Plugin       | Scope                                                               | Optional | Dependent on | Documentation                              |
|--------------|---------------------------------------------------------------------|----------|--------------|--------------------------------------------|
| Admin        | Admin commands to manage your DCS server.                           | no       |              | [README](./plugins/admin/README.md)        |
| Mission      | Handling of mission, compared to the WebGUI.                        | no       |              | [README](./plugins/mission/README.md)      |
| Scheduler    | Autostart / -stop of servers or missions.                           | no       | Mission      | [README](./plugins/scheduler/README.md)    |
| Userstats    | Users statistics system.                                            | yes*     | Mission      | [README](./plugins/userstats/README.md)    |
| Missionstats | Detailed users statistics / mission statistics.                     | yes*     | Userstats    | [README](./plugins/missionstats/README.md) |
| Serverstats  | Server statistics for your DCS servers.                             | yes      | Userstats    | [README](./plugins/serverstats/README.md)  |
| Punishment   | Punish users for teamhits or teamkills.                             | yes      | Mission      | [README](./plugins/punishment/README.md)   |
| Slotblocking | Slotblocking either based on units or a point based system.         | yes      | Mission      | [README](./plugins/slotblocking/README.md) |
| Gamemaster   | Interaction with the running mission (inform users, set flags, etc) | yes*     |              | [README](./plugins/gamemaster/README.md)   |
| DBExporter   | Export the whole DCSServerBot database as json.                     | yes      |              | [README](./plugins/dbexporter/README.md)   |

*) These plugins are loaded by the bot by default, but they are not necessarily needed to operate the bot.

### In case you want to write your own plugin ...
There is a sample in the plugins/samples subdirectory, that will guide you through the steps. If you want your plugin to be added to the distribution, just contact me via the contact details below.

---
## Installation

### Prerequisites
You need to have [python 3.9](https://www.python.org/downloads/) and [PostgreSQL](https://www.postgresql.org/download/) installed.
The python modules needed are listed in requirements.txt and can be installed with ```pip3 install -r requirements.txt```.
If using PostgreSQL remotely over unsecured networks, it is recommended to have SSL enabled.
For autoupdate to work, you have to install [GIT](https://git-scm.com/download/win) and make sure, ```git``` is in your PATH.

### Discord Token
The bot needs a unique Token per installation. This one can be obtained at http://discord.com/developers <br/>
Create a "New Application", add a Bot, select Bot from the left menu, give it a nice name and icon, press "Copy" below "Click to Reveal Token".
Now your Token is in your clipboard. Paste it in dcsserverbot.ini in your config-directory.
Both "Privileged Gateway Intents" have to be enabled on that page.<br/>
To add the bot to your Discord guild, select "OAuth2" from the menu, then "URL Generator", select the "bot" checkbox, and then select the following permissions:
_Manage Channels, Send Messages, Manage Messages, Embed Links, Attach Files, Read Message History, Add Reactions_
Press "Copy" on the generated URL, paste it into the browser of your choice, select the guild the bot has to be added to - and you're done!
For easier access to channel IDs, enable "Developer Mode" in "Advanced Settings" in Discord.

### Download
Download the latest release version and extract it somewhere on your PC that is running the DCS server(s) and give it write permissions, if needed. Best is to use ```git clone``` as you then can use the autoupdate functionality of the bot.

__Attention:__ Make sure that the bot's installation directory can only be seen by yourself and is not exposed to anybody outside via www etc.

### Bot Configuration
The bot configuration is held in **config/dcsserverbot.ini**. See **dcsserverbot.ini.sample** for an example.<br/>
If you start the bot for the first time, it will generate a basic file for you that you can amend to your needs afterwards.<br/>
For some configurations, default values may apply. They are kept in config/default.ini. **Don't change this file**, just overwrite the settings, if you want to have them differently.

The following parameters can be used to configure the bot:

a) __BOT Section__

| Parameter        | Description                                                                                                                                                                                                                                                                                                                                                                                                          |
|------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| OWNER            | The Discord ID of the Bot's owner (that's you!). If you don't know your ID, go to your Discord profile, make sure "Developer Mode" is enabled under "Advanced", go to "My Account", press the "..." besides your profile picture and select "Copy ID"                                                                                                                                                                |
| TOKEN            | The token to be used to run the bot. Can be obtained at http://discord.com/developers.                                                                                                                                                                                                                                                                                                                               |
| DATABASE_URL     | URL to the PostgreSQL database used to store our data.                                                                                                                                                                                                                                                                                                                                                               |
| COMMAND_PREFIX   | The prefix to be used. Default is '.'                                                                                                                                                                                                                                                                                                                                                                                |
| HOST             | IP the bot listens on for messages from DCS. Default is 127.0.0.1, to only accept internal communication on that machine.                                                                                                                                                                                                                                                                                            |
| PORT             | UDP port, the bot listens on for messages from DCS. Default is 10081. **__Don't expose this port to the outside world!__**                                                                                                                                                                                                                                                                                           |
| MASTER           | If true, start the bot in master-mode (default for one-bot-installations). If only one bot is running, then there is only a master.\nIf you have to use more than one bot installation, for multiple DCS servers that are spanned over several locations, you have to install one agent (MASTER = false) at every other location. All DCS servers of that location will then automatically register with that agent. |
| PLUGINS          | List of plugins to be loaded (you usually don't want to touch this).                                                                                                                                                                                                                                                                                                                                                 |
| OPT_PLUGINS      | List of optional plugins to be loaded. Here you can add your plugins that you want to use and that are not loaded by default.                                                                                                                                                                                                                                                                                        |
| AUTOUPDATE       | If true, the bot autoupdates itself with the latest release on startup.                                                                                                                                                                                                                                                                                                                                              |
| AUTOBAN          | If true, members leaving the discord will be automatically banned.                                                                                                                                                                                                                                                                                                                                                   |
| LOGLEVEL         | The level of logging that is written into the logfile (DEBUG, INFO, WARNING, ERROR, CRITICAL).                                                                                                                                                                                                                                                                                                                       |
| MESSAGE_TIMEOUT  | General timeout for popup messages (default 10 seconds).                                                                                                                                                                                                                                                                                                                                                             | 
| AUDIT_CHANNEL    | (Optional) The ID of an audit channel where audit events will be logged into. For security reasons, it is recommended that no users can delete messages in this channel.                                                                                                                                                                                                                                             |

b) __ROLES Section__

| Parameter  | Description                                                                                                                   |
|------------|-------------------------------------------------------------------------------------------------------------------------------|
| Admin      | The name of the admin role in you Discord.                                                                                    |
| DCS Admin  | The name of the role you'd like to give admin rights on your DCS servers (_Moderator_ for instance).                          |
| DCS        | The role of users being able to see their statistics and mission information (usually the general user role in your Discord). |

c) __FILTER Section__ (Optional)

| Parameter      | Description                                                                                                                                                                                                                       |
|----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| TAG_FILTER     | Many groups have their own tag, that might make it difficult for the bot to match usernames. The usual tags like [Tag], =Tag= or similar ones, are supported already. If you see matching issues, you might want to try this one. |
| SERVER_FILTER  | Filter to shorten server names (if needed)                                                                                                                                                                                        |
| MISSION_FILTER | Filter to shorten mission names (if needed)                                                                                                                                                                                       |
| EVENT_FILTER   | Filter events from the missionstats plugin (optional). See [here](https://wiki.hoggitworld.com/view/DCS_singleton_world) for a complete list of events.                                                                           |

d) __DCS Section__

| Parameter                | Description                                                                                                         |
|--------------------------|---------------------------------------------------------------------------------------------------------------------|
| DCS_INSTALLATION         | The installation directory of DCS World.                                                                            |
| AUTOUPDATE               | If true, your DCS server will be kept up-to-date automatically by the bot (default=false).                          |
| SRS_INSTALLATION         | The installation directory of DCS-SRS (optional).                                                                   |
| GREETING_MESSAGE_MEMBERS | A greeting message, that people will receive in DCS, if they get recognized by the bot as a member of your discord. |
| GREETING_MESSAGE_UNKNOWN | A greeting message, that people will receive in DCS, if they are not recognized as a member of your discord.        |
| SERVER_USER              | The username to display as user no. 1 in the server (Observer)                                                      |

e) __Server Specific Sections__

This section has to be named **exactly** like your Saved Games\<instance> directory. Usual names are DCS.OpenBeta or DCS.openbeta_server.
If your directory is named DCS instead (stable version), just add these fields to the DCS category above.

| Parameter          | Description                                                                                                                                                                                |
|--------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| DCS_HOST           | The internal (!) IP of the machine, DCS is running onto. If the DCS server is running on the same machine as the bot (default), this should be 127.0.0.1.                                  |
| DCS_PORT           | Must be a unique value > 1024 of an unused port in your system. This is **NOT** the DCS tcp/udp port (10308), that is used by DCS but a unique different one. Keep the default, if unsure. |
| DCS_HOME           | The main configuration directory of your DCS server installation (for Hook installation). Keep it empty, if you like to place the Hook by yourself.                                        |
| SRS_CONFIG         | The configuration file to use for the dedicated DCS-SRS server (optional).                                                                                                                 |
| SRS_HOST           | The IP-address the DCS-SRS server is listening on (optional, default: 127.0.0.1).                                                                                                          |
| SRS_PORT           | The port the DCS-SRS server uses (optional, default: 5002).                                                                                                                                |
| AUTOSTART_DCS      | [Deprecated] If true, the corresponding DCS server will be started automatically at bot start.<br/>Replaced, see [Scheduler](./plugins/scheduler/README.md)                                |
| AUTOSTART_SRS      | [Deprecated] If true, the corresponding DCS-SRS server will be started automatically at bot start (optional).<br/>Replaced, see [Scheduler](./plugins/scheduler/README.md)                 |
| STATISTICS         | If false, no statistics will be generated for this server. Default is true (see [Userstats](./plugins/userstats/README.md)).                                                               |
| MISSION_STATISTICS | If true, mission statistics will be generated for all missions loaded in this server (see [Missionstats](./plugins/missionstats/README.md)).                                               | 
| CHAT_CHANNEL       | The ID of the in-game chat channel to be used for the specific DCS server. Must be unique for every DCS server instance configured. If "-1", no chat messages will be generated.           |
| STATUS_CHANNEL     | The ID of the status-display channel to be used for the specific DCS server. Must be unique for every DCS server instance configured.                                                      |
| ADMIN_CHANNEL      | The ID of the admin-commands channel to be used for the specific DCS server. Must be unique for every DCS server instance configured.                                                      |

### DCS/Hook Configuration
The DCS World integration is done via a Hook. They are being installed automatically into your configured DCS servers.

### Sanitization
DCSServerBot sanitizes your MissionScripting environment. That means, it changes entries in {DCS_INSTALLATION}\Scripts\MissionScripting.lua.
If you use any other method of sanitization, DCSServerBot checks, if additional sanitizations are needed and conducts them.
**To be able to do so, you must change the permissions on the DCS-installation directory. Give the User group write permissions for instance.**
Your MissionScripting.lua will look like this afterwards:
```lua
do
	sanitizeModule('os')
	--sanitizeModule('io')
	--sanitizeModule('lfs')
	--_G['require'] = nil
	_G['loadlib'] = nil
	--_G['package'] = nil
end
```

### Discord Configuration
The bot uses the following **internal** roles to apply specific permissions to commands.
You can change the role names to the ones being used in your discord. That has to be done in the dcsserverbot.ini configuration file. If you want to add multiple groups, separate them by comma.

| Role      | Description                                                                                                                                         |
|-----------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| DCS       | People with this role are allowed to chat, check their statistics and gather information about running missions and players.                        |
| DCS Admin | People with this role are allowed to restart missions, managing the mission list, ban and unban people.                                             |
| Admin     | People with this role are allowed to manage the server, start it up, shut it down, update it, change the password and gather the server statistics. |

### Running of the Bot
To start the bot, you can either use the packaged _run.cmd_ command or _python run.py_.
If using _AUTOUPDATE = true_ it is recommended to start the bot in a loop as it will close itself after an update has taken place.

### **!!! ATTENTION !!!**
_One of the concepts of this bot it to bind people to your discord._

The bot automatically bans / unbans people from the configured DCS servers, as soon as they leave / join the configured Discord guild.
If you don't like that feature, set _AUTOBAN = false_ in dcsserverbot.ini.
Besides that, people that have no pilot ID (empty), will not get into the server. That is not configurable, it's a general rule (and a good one in my eyes).

---
## How to do the more complex stuff?
DCSServerBot can be used to run a whole worldwide distributed set of DCS servers and therefore supports the largest communities.
The installation and maintenance of such a use-case is a bit more complex than a single server installation.

### Setup Multiple Servers on a Single Host
DCSServerBot is able to contact DCS servers at the same machine or over the local network.

To run multiple DCS servers under control of DCSServerBot you just have to make sure that you configure different communication ports. This can be done with the parameter DCS_PORT in DCSServerBotConfig.lua. The default is 6666, you can just increase that for every server (6667, 6668, ...).
Don't forget to configure different Discord channels (CHAT_CHANNEL, STATUS_CHANNEL and ADMIN_CHANNEL) for every server, too.
To add subsequent servers, just follow the steps above, and you're good, unless they are on a different Windows server (see below).

### Setup Multiple Servers on Multiple Host at the Same Location
To communicate with DCSServerBot over the network, you need to change two configurations.
By default, DCSServerBot is configured to be bound to the loopback interface (127.0.0.1) not allowing any external connection to the system. This can be changed in dcsserverbot.ini by using the LAN IP address of the Windows server running DCSServerBot instead.<br/>

__Attention:__ The scheduler, .startup and .shutdown commands will only work without issues, if the DCS servers are on the same machine as the bot. 
So you might consider installing a bot instance on every server that you use in your network. Just configure them as agents (_MASTER = false_) and you are good.

### Setup Multiple Servers on Multiple Host at Different Locations
DCSServerBot is able to run in multiple locations, worldwide. In every location, one instance of DCSServerBot is needed to be installed in the local network containing the DCS server(s).
Only one single instance of the bot (worldwide) is to be configured as a master. This instance has to be up 24/7 to use the statistics or ban commands. Currently, DCSServerBot does not support handing over the master to other bot instances, if the one and only master goes down.
To configure a server as a master, you have to set _MASTER = true_ (default) in the dcsserverbot.ini configuration file. Every other instance of the bot has to be set as an agent (_MASTER = false_).
The master and all agents are collecting statistics of the DCS servers they control, but only the master runs the statistics module to display them in Discord. To be able to write the statistics to the **central** database, all servers need access to the database. You can either host that database at the location where the master runs and enable all other agents to access that instance (keep security like SSL encryption in mind) or you use a cloud database, available on services like Amazon, Heroku, etc.

### Moving a Server from one Location to Another
When running multiple servers over different locations it might be necessary to move a server from one location to another. As all servers are registered with their local bots, some steps are needed to move a server over.
1) Stop the server in the **old** location from where it should be moved (```.shutdown```)
2) Goto the ADMIN_CHANNEL of that server and type ```.unregister```
3) Remove the entries of that server from the dcsserverbot.ini at the **old** location.
4) Configure a server at the **new** location with the very same name and make sure the correct channels are configured in dcsserverbot.ini of that server.
5) Reload the configuration of that server using the ```.reload``` command.
6) Start the server at the **new** location.

### How to talk to the Bot from inside Missions
If you plan to create Bot-events from inside a DCS mission, that is possible! Just make sure, you include this line in a trigger:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
```
_Don't use a Mission Start trigger, as this might clash with other plugins loading stuff into the mission._<br/> 
After that, you can for instance send chat messages to the bot using
```lua
  dcsbot.sendBotMessage('Hello World', '12345678') -- 12345678 is the ID of the channel, the message should appear, default is the configured chat channel
```
inside a trigger or anywhere else where scripting is allowed.

**Attention!** Channel always has to be a string, encapsulated with '', **not** a number.

Embeds can be sent using code similar to this snippet:
```lua
  title = 'Special K successfully landed at Kutaisi!'
  description = 'The unbelievable and unimaginable event happend. Special K succeeded at his 110th try to successfully land at Kutaisi, belly down.'
  img = 'https://i.chzbgr.com/full/8459987200/hB315ED4E/damn-instruction-manual'
  fields = {
    ['Pilot'] = 'sexy as hell',
    ['Speed'] = '130 kn',
    ['Wind'] = 'calm'
  }
  footer = 'Just kidding, they forgot to put their gear down!'
  dcsbot.sendEmbed(title, description, img, fields, footer)
```
They will be posted in the chat channel by default, if not specified otherwise (adding the channel id as a last parameter of the sendEmbed() call, see sendBotMessage() above).

If you like to use a single embed, maybe in the status channel, and update it instead, you can do that, too:
```lua
  title = 'RED Coalition captured Kutaisi!'
  description = 'After a successful last bombing run, RED succeeded in capturing the strategic base of Kutaisi.\nBLUE has to fight back **NOW** there is just one base left!'
  dcsbot.updateEmbed('myEmbed', title, description)
  --[....]
  title = 'Mission Over!'
  description = 'RED has won after capturing the last BLUE base Batumi, congratulations!'
  img = 'http://3.bp.blogspot.com/-2u16gMPPgMQ/T1wfXR-bn9I/AAAAAAAAFrQ/yBKrNa9Q88U/s1600/chuck-norris-in-war-middle-east-funny-pinoy-jokes-2012.jpg'
  dcsbot.updateEmbed('myEmbed', title, description, img)
```
If no embed named "myEmbed" is there already, the updateEmbed() call will generate it for you, otherwise it will be replaced with this one.

---
## TODO
Things to be added in the future:
* user-friendly installation
* more plugins!

---
## Contact / Support
If you need support, if you want to chat with me or other users or if you like to contribute, jump into my [Support Discord](https://discord.gg/zjRateN).

If you like what I do and you want to support me, you can do that via my [Patreon Page](https://www.patreon.com/DCS_SpecialK).

---
## Credits
Thanks to the developers of the awesome solutions [HypeMan](https://github.com/robscallsign/HypeMan) and [perun](https://github.com/szporwolik/perun), that gave me the main ideas to this solution.
I gave my best to mark parts in the code to show where I copied some ideas or even code from you guys, which honestly is just a very small piece. Hope that is ok.
