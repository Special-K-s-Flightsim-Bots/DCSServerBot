# Welcome to DCSServerBot!
You've found a comprehensive solution that lets you administrate your DCS instances via Discord slash-commands, has 
built in per-server and per-user statistics, optional cloud-based statistics, [Coalitions](./COALITIONS.md)-support and much more! 
With its plugin system and reporting framework, DCSServerBot can be enhanced very easily to support whatever might come 
into your mind. 

This documentation shows you the main features, how to install and configure the bot and some more sophisticated 
stuff at the bottom, if you for instance run multiple servers maybe even over multiple locations. 

Now let's see, what DCSServerBot can do for you (installation instructions below)!

---
## Architecture
DCSServerBot has a modular architecture with services, plugins and extensions that provide specific functionalities 
like monitoring the availability of your servers, a lot of Discord slash-commands and supports common add-ons like SRS, 
LotAtc and others.

The solution itself is made for anything from single-server environments up to large scale, worldwide installations with
high availability requirements. There are nearly no limits. If you are interested into some deeper insights to the
bots architecture, read [here](./ARCHITECTURE.md)

### Node
A node is an installation of DCSServerBot on one PC. The usual user will have one installation, meaning one node.
You can run multiple instances of DCS with each node (see blow). If you run multiple PCs or (virtual) servers, you 
need to install multiple DCSServerBot nodes. This results in a DCSServerBot cluster.<br>
One node is always a master node, which handles all the Discord commands and controls the rest of the cluster. Each
node can be a master. You can define nodes as preferred master nodes, which you usually want to do with nodes that
are close to your database server (see below).

### Instance
Each node can control multiple instances of DCS, meaning `DCS.exe` or `DCS_Server.exe` processes.

### Services
A service is a component that runs on each node. Services can be combined with plugins, if they provide additional
Discord commands, like the Music service. You can define that a service only runs on the master node.

| Service    | Scope                                                                   | Plugin      | Documentation                             |
|------------|-------------------------------------------------------------------------|-------------|-------------------------------------------|
| ServiceBus | Communication hub between every node of the bot cluster and DCS.        |             | [README](./services/servicebus/README.md) |
| Bot        | The Discord bot handling all discord commands.                          |             | [README](./services/bot/README.md)        |
| Monitoring | Availability monitoring of your DCS servers.                            | ServerStats | [README](./services/monitoring/README.md) |
| Backup     | Backup your bot- and DCS-configuration, your missions, database, etc.   | Backup      | [README](./services/bot/README.md)        |
| Dashboard  | Nice console graphics display to show the status of your bot / servers. |             | [README](./services/dashboard/README.md)  |
| OvGME      | Manage mods that needs to be installed / updated in your DCS servers.   | OvGME       | [README](./services/ovgme/README.md)      |
| Music      | Play music over different SRS-radios on your servers.                   | Music       | [README](./services/music/README.md)      |

### Plugins
A plugin is an expansion of the bot that can be controlled via Discord commands and sometimes in-game chat commands. 
These commands can be received in DCS or be controlled by events in the game. DCSServerBot comes with a rich set of 
default plugins, but it can be enhanced with optional plugins. I can write those myself, but you as a community member 
can also create your own plugins (and maybe share them with others). 

| Plugin       | Scope                                                                         | Optional | Depending on            | Documentation                              |
|--------------|-------------------------------------------------------------------------------|----------|-------------------------|--------------------------------------------|
| GameMaster   | Interaction with the running mission (inform users, set flags, etc)           | no       |                         | [README](./plugins/gamemaster/README.md)   |
| Mission      | Handling of missions, comparable to the WebGUI.                               | no       | GameMaster              | [README](./plugins/mission/README.md)      |
| Help         | Interactive help commands for Discord and in-game chat                        | yes*     |                         | [README](./plugins/help/README.md)         |
| Scheduler    | Autostart / -stop of servers or missions, modify missions, etc.               | yes*     | Mission                 | [README](./plugins/scheduler/README.md)    |
| Admin        | Admin commands to manage your DCS server.                                     | yes*     |                         | [README](./plugins/admin/README.md)        |
| UserStats    | Users statistics system.                                                      | yes*     | Mission                 | [README](./plugins/userstats/README.md)    |
| CreditSystem | User credits, based on achievements.                                          | yes*     | Mission                 | [README](./plugins/creditsystem/README.md) |
| MissionStats | Detailed users statistics / mission statistics.                               | yes*     | Userstats               | [README](./plugins/missionstats/README.md) |
| Punishment   | Punish users for teamhits or teamkills.                                       | yes      | Mission                 | [README](./plugins/punishment/README.md)   |
| SlotBlocking | Slotblocking either based on discord roles or credits.                        | yes      | Mission, Creditsystem   | [README](./plugins/slotblocking/README.md) |
| Cloud        | Cloud-based statistics and connection to the DGSA global ban system.          | yes      | Userstats               | [README](./plugins/cloud/README.md)        |
| ServerStats  | Server statistics for your DCS servers.                                       | yes      | Userstats               | [README](./plugins/serverstats/README.md)  |
| GreenieBoard | Greenieboard and LSO quality mark analysis (SC and Moose.AIRBOSS / FunkMan)   | yes      | Missionstats            | [README](./plugins/greenieboard/README.md) |
| MOTD         | Message for players on join or when they jump in a module.                    | yes      | Mission, Missionstats   | [README](./plugins/motd/README.md)         |
| FunkMan      | Support for [FunkMan](https://github.com/funkyfranky/FunkMan)                 | yes      |                         | [README](./plugins/funkman/README.md)      |
| DBExporter   | Export the DCSServerBot database or singular tables as json.                  | yes      |                         | [README](./plugins/dbexporter/README.md)   |
| Backup       | Create a backup of your database, server or bot configurations.               | yes      |                         | [README](./plugins/backup/README.md)       |
| OvGME        | Install or update mods into your DCS server.                                  | yes      |                         | [README](./plugins/ovgme/README.md)        |
| Music        | Upload and play music over SRS.                                               | yes      |                         | [README](./plugins/music/README.md)        |
| Commands     | Create custom discord commands.                                               | yes      |                         | [README](./plugins/commands/README.md)     |
| RestAPI      | Simple REST-API to query users and statistics (WIP).                          | yes      | Userstats, Missionstats | [README](./plugins/restapi/README.md)      |
| Battleground | Support for [DCS Battleground](https://github.com/Frigondin/DCSBattleground)  | yes      |                         | [README](./plugins/battleground/README.md) |

*) These plugins are loaded by the bot by default, but they are not mandatory to operate the bot.<br> 
&nbsp;&nbsp;&nbsp;&nbsp;If you want to change that, define a list of `plugins` in your main.yaml.

#### How to install 3rd-Party Plugins
If a community member provides a plugin for DCSServerBot, chances are that it is packed into a zip file. You can 
download this zipfile and place it directly into the /plugins directory. DCSServerBot will automatically unpack the 
plugin for you, when DCSServerBot restarts. Keep in mind that some of them might need configurations. Please refer to 
the plugins documentation for more.

#### In case you want to write your own Plugin ...
You can find a sample in the plugins/sample subdirectory and a guide [here](./plugins/README.md). These will guide you 
through the steps needed to build your own plugin. Do you want your plugin to be added as an optional plugin to the 
DCSServerBot? Contact me via the contact details listed below.

### Extensions
Many DCS admins use extensions or add-ons like DCS-SRS, Tacview, LotAtc, etc.</br>
DCSServerBot supports some of them already and can add a bit of quality of life.

| Extension        | Scope                                                                                                             | 
|------------------|-------------------------------------------------------------------------------------------------------------------|
| DCS-SRS          | Market leader in DCS VOIP integration.                                                                            |
| Tacview          | Well known flight data capture and analysis tool.                                                                 |
| LotAtc           | Simple display only extension.                                                                                    |
| MizEdit          | My own invention, can be used to modify your missions. Very powerful, read it up [here](./extensions/MizEdit.md)! |
| DSMC             | DSMC mission handling, should be activated when dealing with DSMC missions.                                       |
| Lardoon          | Start, stop and manage Lardoon servers.                                                                           |
| Sneaker          | Moving map interface (see [Battleground](https://github.com/Frigondin/DCSBattleground) for another option!        |
| DCS Real Weather | Real weather for your missions.                                                                                   |


Check out [Extensions](./extensions/README.md) for more info on how to use them.

---
## Installation

### Prerequisites
You need to have [python 3.9](https://www.python.org/downloads/) or higher (3.11 recommended) and [PostgreSQL](https://www.postgresql.org/download/) installed.
If using PostgreSQL remotely over unsecured networks, it is recommended to have SSL enabled.
For autoupdate to work, you have to install [GIT](https://git-scm.com/download/win) and make sure the ```git```-command is in your PATH.

### Discord Token
The bot needs a unique Token per installation. This one can be obtained at http://discord.com/developers <br/>
- Create a "New Application"
- Add a Bot.
- Select Bot from the left menu, give it a nice name and icon, press "Copy" below "Click to Reveal Token". 
- Now your Token is in your clipboard. Paste it in some editor for later use. 
- All "Privileged Gateway Intents" have to be enabled on that page.<br/>
- To add the bot to your Discord guild, select "OAuth2" from the menu, then "URL Generator"
- Select the "bot" checkbox, and then select the following permissions:

  - Manage Channels
  - Send Messages
  - Manage Messages
  - Embed Links
  - Attach Files
  - Read Message History
  - Add Reactions
  - Use Slash Commands

- Press "Copy" on the generated URL, paste it into the browser of your choice
- Select the guild the bot has to be added to - and you're done!
- For easier access to user and channel IDs, enable "Developer Mode" in "Advanced Settings" in Discord.

### Download
Best is to use ```git clone https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot.git``` as you then can use the 
autoupdate functionality of the bot. Otherwise, download the latest release version as ZIP and extract it somewhere on 
your PC that is running the DCS server(s) and give it write permissions, if needed. 

> ⚠️ **Attention!**<br>
> Make sure that the bots installation directory can only be seen by yourself and is not exposed to anybody 
> outside via www etc. as it contains sensitive data.

### Database
DCSServerBot uses PostgreSQL to store all information that needs to be persistent. This consists of, but is not limited
to: players, mission information, statistics and whatnot. DCSServerBot needs a fast database to do this. Install the 
latest available PostgreSQL version from the above-mentioned website.

### DCSServerBot Installation
Run the provided `install.cmd` script or just `run.cmd`.<br>
It will ask you for your Guild ID (right click on your Discord server icon and select "Copy Server ID") and the bots 
user ID (right click on the bot user and select "Copy User ID"). Then it will search for existing DCS installations, 
create the database user, password and database and asks whether you want to add existing DCS servers to the 
configuration.<br>
When finished, the bot should launch successfully and maybe even start your servers already, if configured.

> ⚠️ **Attention!**<br> 
> You should shut down your DCS servers during the bots installation, as it places its own LUA hooks inside
> the servers Scripts directories.

---
## Configuration
The bot configuration is held in several files in the **config** subdirectory.
If you run the `install.cmd` script for the first time, it will generate basic files for you that you can amend to your 
needs afterwards. Your bot should be ready to run already and you can skip this section for now, if you don't want to
bother with the bots configuration in first place.

> ⚠️ **Attention!**<br>
> If you run more than one bot node, best is to share the configuration between all nodes. This can be done via a cloud
> drive for instance.

The following samples will show you what you can configure in DCSServerBot. For most of the configuration, default 
values will apply, so you don't need to set them explicitly.

### config/main.yaml
This file holds the main information about DCSServerBot. You can configure which plugins are loaded here for instance.

```yaml
guild_id: 112233445566  # Your Discord server ID. Right click on your server and select "Copy Server ID".
use_dashboard: true     # Use the dashboard display for your node. Default is true.
chat_command_prefix: .  # The command prefix to be used for in-game chat commands. Default is "."
database:
  url: postgres://USER:PASSWORD@DB-IP:DB-PORT/DB-NAME
  pool_min: 5           # min size of the DB pool, default is 5
  pool_max: 10          # max size of the DB pool, default is 10
logging:
  loglevel: DEBUG           # loglevel, default is DEBUG
  logrotate_count: 5        # Number of logfiles to keep after rotation. Default is 5.    
  logrotate_size: 10485760  # max size of a logfile, default is 10 MB
messages:
  player_username: Your player name contains invalid characters. Please change your # Default message for players with invalid usernames
    name to join our server.
  player_default_username: Please change your default player name at the top right  # Default message for players with default usernames
    of the multiplayer selection list to an individual one!
  player_banned: 'You are banned from this server. Reason: {}'                      # Default message for banned players.
filter:
  server_name: ^Special K -           # Filter to shorten your server names on many bot displays. Default is none. 
  mission_name: ^Operation|_|\(.*\)   # Filter to shorten your mission names on many bot displays. Default is none.
  tag: "'^[JDS]'"                     # If your community uses specific tags, this helps with the bots automatch functionality.
opt_plugins:                          # Optional: list of optional plugins to be loaded and used by the bot
- serverstats                         # see above
- dbexporter
- motd
- greenieboard
- punishment
- slotblocking
- music
- funkman
- ovgme
- commands
- restapi
```

### config/nodes.yaml
This file holds the main configuration for all your nodes.<br>
For a cluster installation, you want to describe all your nodes and instances on all your nodes, as the bot can 
(auto-)migrate stuff in-between the cluster!

```yaml
NODENAME:                       # this will be your hostname
  listen_address: 0.0.0.0       # On which interface should the bot listen to? Default is 0.0.0.0
  listen_port: 10042            # On which port should the bot listen to? Default is 10042
  autoupdate: true              # use the bots autoupdate functionality, default is false
  slow_system: false            # if you are using a slower PC to run your servers, you should set this to true (default: false)
  preferred_master: true        # this node should be the preferred master node (only needed in a cluster configuration)
  DCS:
    installation: '%ProgramFiles%\\Eagle Dynamics\\DCS World OpenBeta Server'  # This is your DCS installation. Usually autodetected by the bot.
    autoupdate: false           # enable auto-update for your DCS servers. Default is false.
    desanitize: true            # Desanitize your MissionScripting.lua after each update. Default is true.
  instances:
    DCS.openbeta_server:        # The name of your instance. You can have multiple instances that have to have unique names.
      home: '%USERPROFILE%\\Saved Games\\DCS.openbeta_server' # The path to your saved games directory.
      bot_port: 6666            # The port DCSServerBot uses to communicate with your DCS server. Each instance has to have a unique port. This is NOT your DCS port (10308)!!!
      max_hung_minutes: 3       # Let DCSServerBot kill your server if it is unresponsive for more than x minutes. Default is 3. Disable it with 0.
      server: My Fancy Server   # The server (name) that is associated to this instance. See servers.yaml
      affinity: 2,3             # Optional: set the CPU-affinity for this instance.
      extensions:               # See the extension documentation for more detailed information on what to set here.
        SRS:
          config: '%USERPROFILE%\Saved Games\DCS.openbeta_server\Config\SRS.cfg'  # it is recommended to copy your SRS "server.cfg" below your instances home directory.
          host: 127.0.0.1       # SRS servers local IP (default is 127.0.0.1)
          port: 5002            # SRS servers local port (default is 5002). The bot will change this in your SRS configuration, if set here!
          autostart: true       # this will autostart your DCS server with the DCS server start (default: true)
          autoupdate: true      # This will auto-update your SRS servers. Default is false, you need to run the bot as Administrator to make it work!
        Tacview:
          show_passwords: false # If you don't want to show the Tacview passwords (default: true)
    instance2:                  # you can have an unlimited amount of instance configurations, but each instance has to have a physical representation on your disk.
      ...
```

### config/servers.yaml
This is your server configuration.<br>
You might wonder why the configuration is split between nodes.yaml and servers.yaml? Even if you have a basic setup! 
This is to decouple the server configuration from the physical node (aka the "DCS.exe" / "DCS_Server.exe" process). You 
will learn to love it, especially when you decide to move a server from one instance to another or even from one node to 
another. This is much easier with a non-coupled approach like that.
```yaml
DEFAULT:
  message_afk: '{player.name}, you have been kicked for being AFK for more than {time}.'  # default message for AFK users
  message_server_full: The server is full, please try again later!                        # default message, if the server is considered full (see SlotBlocking plugin)
  message_timeout: 10                                                                     # default timeout for DCS popup messages in seconds 
My Fancy Server:                # Your server name, as displayed in the server list and listed in serverSettings.lua
  server_user: Admin            # Name of the server user #1 (technical user), default is "Admin".
  afk_time: 300                 # Time in seconds after which a player that is on spectators is considered being AFK. Default: -1, which is disabled
  ping_admin_on_crash: true     # Ping DCS Admin role in discord, when the server crashed. Default: true
  missions_dir: %USERPROFILE%\Documents\Missions  # Central missions dir, if wanted. Default is the Missions dir below the instance home folder.
  autoscan: false               # Enable autoscan for new missions (and auto-add them to the mission list). Default: false
  channels:
    status: 1122334455667788    # The Discord channel to display the server status embed and players embed into. Right click on your channel and select "Copy Channel ID".
    chat: 8877665544332211      # The Discord channel for the in-game chat replication. You can disable it with setting it to -1.
    admin: 1188227733664455     # The channel where you can fire admin commands to this server. You can decide if you want to have a central admin channel or server specific ones. See bot.yaml for more.
  chat_log:
    count: 10                   # A log file that holds the in-game chat to check for abuse. Tells how many files will be kept, default is 10.
    size: 1048576               # Max logfile size, default is 1 MB. 
  no_coalition_chat: true       # Do not replicate red and blue chats to the Discord chat replication (default: false)
My 2nd Fancy Server:            # You can have an unlimited amount of server configurations.
  ...
```

### config/presets.yaml
This file holds your different presets that you can apply to missions as modifications.<br>
See TODO for further details.

### services/bot.yaml
This is your Discord-bot configuration.

```yaml
token: AAaahhg2347286adhjdjasd2347263473        # Your TOKEN, as received from the discord developer portal.
owner: 1122334455667788                         # The ID of your bot user. Right click, select "Copy User ID".
automatch: true                                 # Use the bots auto-matching functionality (see below), default is true.
autoban: false                                  # Use the bots auto-ban functionality (see below), default is false.
message_ban: User has been banned on Discord.   # Default reason to show people that try to join your DCS servers when they are banned on Discord.
message_autodelete: 300                         # Most of the Discord messages are private messages. If not, this is the timeout after that they vanish. Default is 300 (5 mins). 
admin_channel: 1122334455667788                 # Optional: Central admin channel (see below).
reports:
  num_workers: 4                                # Number of worker threads to be used for any reports generated by the bot. Default is 4.
  cjk_font: KR                                  # Optional: You can specify a CJK font to be used in your reports.
discord_status: Managing DCS servers ...        # Message to be displayed as the bots Discord status. Default is none.
audit_channel: 88776655443322                   # Central audit channel to send audit events to (default: none)
roles:                                          # Roles mapping. The bot uses internal roles to decouple from Discord own role system.
  Admin:                                        # Map your Discord role "Admin" to the bots role "Admin" (default: Admin)
  - Admin                                       
  DCS Admin:                                    # Map your Discord role "Moderator" and "Staff" to the bots "DCS Admin" role (default: DCS Admin)
  - Moderator
  - Staff
  GameMaster:                                   # Give the GameMaster role to anybody with the Staff role in your Discord.
  - Staff
  DCS:                                          # Give the bots DCS role to everyone in your discord. Only everyone needs the leading @!
  - @everyone

```
#### Auto Matching (default: enabled)
To use in-game commands, your DCS players need to be matched to Discord users. Matched players are able to see statistics 
and you can see a variety of statistics yourself as well. The bot offers a linking system between Discord and DCS accounts 
to enable this.
Players can do this with the /linkme command. This creates a permanent and secured link that can then be used for in-game 
commands. The bot can also auto-match a DCS player to Discord user. This way, players can see their own stats via Discord 
commands. The bot will try to match the Discord username to DCS player name. This works best when DCS and Discord names 
match! It can generate false links though, which is why I prefer (or recommend) the /linkme command. People still seem 
to like the auto-matching, that is why it is in and you can use it (enabled per default).

#### Auto-Banning (default: disabled)
The bot supports automatically bans / unbans of players from the configured DCS servers, as soon as they leave / join 
your Discord guild. If you like that feature, set `autoban: true` in services/bot.yaml (default: false).

However, players that are being banned from your Discord or that are being detected as hackers are auto-banned from 
all your configured DCS servers independent of that setting.

#### Discord Roles
The bot uses the following **internal** roles to apply specific permissions to commands.<br>
You can map your Discord roles to these internal roles like described in the example above.

| Role           | Description                                                                                                                                         |
|----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| Admin          | People with this role are allowed to manage the server, start it up, shut it down, update it, change the password and gather the server statistics. |
| DCS Admin      | People with this role are allowed to restart missions, managing the mission list, ban and unban people.                                             |
| DCS            | People with this role are allowed to chat, check their statistics and gather information about running missions and players.                        |
| GameMaster     | People with this role can see both [Coalitions](./COALITIONS.md) and run specific commands that are helpful in missions.                            |

See [Coalitions](./COALITIONS.md) for coalition roles.

### DCS/Hook Configuration
The DCS World integration is done via Hooks. They are being installed automatically into your configured DCS servers by the bot.

### Desanitization
DCSServerBot desanitizes your MissionScripting environment. That means, it changes entries in Scripts\MissionScripting.lua
of your DCS installation. If you use any other method of desanitization, DCSServerBot checks, if additional 
desanitizations are required and conducts them.<br>
**To be able to do so, you must change the permissions on the DCS-installation directory.**
Give the User group write permissions for instance. 

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

### Custom MissionScripting.lua
If you want to use a **custom MissionScripting.lua** that has more sanitization (for instance for LotAtc, Moose, 
OverlordBot or the like) or additional lines to be loaded (for instance for LotAtc, or DCS-gRPC), just place the 
MissionScripting.lua of your choice in the config directory of the bot. It will then be replaced on every bot startup.

### Sample Configuration
To view some sample configurations for the bot or for each configurable plugin, look [here](config/samples/README.md).

### Additional Security Features
Players that have no pilot ID (empty or whitespace) or that share an account with others, will not be able to join your 
DCS server. This is not configurable, it's a general rule (and a good one in my eyes).

---
## Starting the Bot
To start the bot, you can either use the packaged ```run.cmd``` command (recommended) or ```venv\Scripts\python run.py```.<br/>
If using `autoupdate: true` in your main.yaml, it is recommended to start the bot via ```run.cmd```. This runs it in 
a loop, as it will try to restart itself after an update has taken place.</br>
If you want to run the bot from autostart, create a small batch script, that will change to the bots installation 
directory and run the bot from there like so:
```cmd
@echo off
cd "<whereveryouinstalledthebot>\DCSServerBot"
:loop
venv\Scripts\python run.py
goto loop
```
DCSServerBot runs in a Python virtual environment, with its own independent set of Python libraries and packages.

---
## How to do the more complex stuff?
DCSServerBot can be used to run a whole worldwide distributed set of DCS servers and therefore supports the largest 
communities. The installation and maintenance of such a use-case is just a bit more complex than a single server 
installation.

### Setup Multiple Servers on a Single Host
To run multiple DCS servers under control of DCSServerBot you just have to make sure that you configure different 
communication ports. This can be done with the parameter `bot_port` in nodes.yaml. The default is 6666, you can just 
increase that for every server (6667, 6668, ...).<br>
Don't forget to configure different Discord channels (`chat` and `status`, optional `admin`) for every server, too. 
This will be done in the servers.yaml file.<br>
To add subsequent servers, just follow the steps above, and you're good, unless they are on a different Windows server 
(see below).

DCSServerBot will autodetect all configured DCS servers on installation and generate simple configuration files 
for you already. To add a new instance, you can either do that manually or use `/node add_instance` in your Discord.

### Setup Multiple Servers on Multiple Host
DCSServerBot is able to run in multiple locations, worldwide. On every PC in this cluster, one instance of DCSServerBot
(a "node") needs to be installed. One node will always be the "Master", taking over the Discord communication and most of
the work. If the Master fails, any other node in the cluster will automatically take over.<br>
All nodes collect statistics of the DCS servers they control, but only the master runs the statistics module to display 
them in Discord. To be able to communicate, all nodes need to have access to a **central** database. 

You can either host that database at one of the nodes and give all other nodes access to it (keep security 
like SSL encryption in mind) or you use a cloud database, available on services like Amazon, Heroku, etc.
This would be the recommended approach, as you would still have a single point of failure in your cluster with a local
database. All depending on your high availability requirements.

Many files like configuration, missions, music and whatnot should be kept on a cloud drive in that case, even the whole
DCSServerBot installation could be on a cloud drive (like Google Drive). You can start each bot in each
location on this shared directory. Each bot will read its individual configuration based on the node name of that PC.

### Moving a Server from one Location to Another
Each server is loosely coupled to an instance on a node. You can migrate a server to another instance though, by using
the `/server migrate` command. Please keep in mind that - unless you use a central missions directory - the necessary
missions (or scripts) for this server might not be available on the other node.

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

> ⚠️ **Attention!**<br>
> Channel always has to be a string, encapsulated with '', **not** a number because of Integer limitations in LUA.

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

If you like to use a single embed, maybe in the status channel, and update it instead of creating new messages, you 
can do that, by giving is a name like "myEmbed" in this example. The name has to be unique per server.
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
If no embed named "myEmbed" is already there, the updateEmbed() call will generate it for you. Otherwise, it will be 
replaced with this one.

---
## Contact / Support
If you need support, if you want to chat with me or other users or if you like to contribute, jump into my [Support Discord](https://discord.gg/zjRateN).

If you like what I do, and you want to support me, you can do that via my [Patreon Page](https://www.patreon.com/DCS_SpecialK).

---
## Credits
Thanks to the developers of the awesome solutions [HypeMan](https://github.com/robscallsign/HypeMan) and [perun](https://github.com/szporwolik/perun), that gave me the main ideas to this solution.
I gave my best to mark parts in the code to show where I copied some ideas or even code from you guys, which honestly is just a very small piece. Hope that is ok.
Also thanks to Moose for aligning the API for [FunkMan](https://github.com/funkyfranky/FunkMan) with me and make it compatible with DCSServerBot in first place.
