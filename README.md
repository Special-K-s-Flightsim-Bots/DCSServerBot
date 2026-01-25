# Welcome to DCSServerBot!

[![Latest Release](https://img.shields.io/github/v/release/Special-K-s-Flightsim-Bots/DCSServerBot?logo=GitHub)](https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/releases/latest)
[![Discord](https://img.shields.io/discord/722748768113393664?logo=Discord)](https://discord.gg/h2zGDH9szZ)
[![License](https://img.shields.io/github/license/Special-K-s-Flightsim-Bots/DCSServerBot)](https://raw.githubusercontent.com/Special-K-s-Flightsim-Bots/DCSServerBot/refs/heads/master/LICENSE)

You've found a comprehensive solution that helps you administrate your DCS World servers. It has a Discord integration 
([now optional](#dcsserverbot-installation-non-discord)!) with smart slash-commands, built-in per-server and per-user 
statistics, optional cloud-based statistics, [Coalitions](./COALITIONS.md)-support, a whole 
[Tournament-System](./plugins/tournament/README.md), a third-party [web-frontend](https://github.com/Penfold-88/DCS-Statistics-Dashboard) for statistics and much more!
<p> 
With its plugin system and reporting framework, DCSServerBot can be enhanced very easily to support whatever might come 
into your mind. DCSServerBot is a solution for DCS server admins built by a DCS server admin.
<p>
This documentation shows you the main features, how to install and configure the bot and some more sophisticated 
stuff at the bottom, if you for instance run multiple servers maybe even over multiple locations. 
<p>

Now let's see what DCSServerBot can do for you (installation-instructions [below](#installation))!

---

## Architecture
DCSServerBot has a modular architecture with services, plugins, and extensions that provide specific functionalities 
like monitoring the availability of your servers, a lot of Discord slash-commands and supports common add-ons like SRS, 
LotAtc, DCS Olympus, and others.

The solution itself is made for anything from single-server environments up to large scale, worldwide installations with
high-availability requirements. There are nearly no limits. 
If you are interested in some deeper insights to the bot's architecture, read [here](./ARCHITECTURE.md)

### Node
A node is an installation of DCSServerBot on one PC. The usual user will have one installation, meaning one node.
You can run multiple instances of DCS ("DCS servers") with each node (see below). If you run multiple PCs or (virtual) 
servers, you need to install multiple DCSServerBot nodes. This results in a DCSServerBot cluster.<br>
One node is always a master node, which handles all the Discord commands and controls the rest of the cluster.

### Instance
Each node can control multiple instances of DCS, meaning `DCS.exe` or `DCS_Server.exe` processes. You can use the
normal client installation of DCS World to run a server, but the [Dedicated Server](https://www.digitalcombatsimulator.com/en/downloads/world/server/) installation would be preferable.

### Services
A service is a component that runs on each node. Services can be combined with plugins if they provide additional
Discord commands, like the Music service. Some services only run on the master node, like the Bot-service, for instance.

| Service     | Scope                                                                                                     | Plugin      | Documentation                             |
|:------------|:----------------------------------------------------------------------------------------------------------|:------------|:------------------------------------------|
| Backup      | Backup and restore your bot- and DCS-configuration, your missions, database, etc.                         | Backup      | [README](./services/backup/README.md)     |
| Bot         | The Discord bot handling all discord commands. There is a Discord-free variant available also (see blow)! |             | [README](./services/bot/README.md)        |
| Cleanup     | Cleanup log-files, track-files, etc. from your disk.                                                      |             | [README](./services/cleanup/README.md)    |
| Cron        | Schedule tasks based on a cron-like configuration.                                                        |             | [README](./services/cron/README.md)       |
| Dashboard   | Nice console graphics display to show the status of your bot / servers.                                   |             | [README](./services/dashboard/README.md)  |
| ModManager  | Manage mods that needs to be installed / updated in your DCS servers.                                     | ModManager  | [README](./services/modmanager/README.md) |
| Monitoring  | Availability- and performance-monitoring of your DCS servers.                                             | ServerStats | [README](./services/monitoring/README.md) |
| Music       | Play music over different SRS-radios on your servers.                                                     | Music       | [README](./services/music/README.md)      |
| ServiceBus  | Communication hub between every node of the bot cluster and all DCS-servers.                              |             | [README](./services/servicebus/README.md) |
| WebService  | A simple webserver to provide support for REST-endpoints and embedded websites.                           |             | [README](./services/webservice/README.md) |

### Plugins
A plugin is an expansion of the bot that can be controlled via Discord commands and sometimes in-game chat commands. 
DCSServerBot comes with a rich set of default plugins, but it can be enhanced with optional plugins. I enhance the bot
from time to time, but you as a community member can also create your own plugins (and maybe share them with others). 

| Plugin        | Scope                                                                                           | Optional | Depending on                          | Documentation                               |
|:--------------|:------------------------------------------------------------------------------------------------|:--------:|:--------------------------------------|:--------------------------------------------|
| GameMaster    | Interaction with the running mission (inform users, set flags, etc)                             |    no    |                                       | [README](./plugins/gamemaster/README.md)    |
| Mission       | Handling of missions, comparable to the WebGUI.                                                 |    no    | GameMaster                            | [README](./plugins/mission/README.md)       |
| Admin         | Admin commands to manage your DCS server.                                                       | yes[^1]  |                                       | [README](./plugins/admin/README.md)         |
| Help          | Interactive help commands for Discord and in-game chat                                          | yes[^1]  |                                       | [README](./plugins/help/README.md)          |
| UserStats     | Users statistics system.                                                                        | yes[^1]  | Mission                               | [README](./plugins/userstats/README.md)     |
| CreditSystem  | User credits, based on achievements.                                                            | yes[^1]  | Mission                               | [README](./plugins/creditsystem/README.md)  |
| Scheduler     | Autostart / -stop of servers or missions, modify missions, etc.                                 | yes[^1]  | Mission                               | [README](./plugins/scheduler/README.md)     |
| Cloud         | Cloud-based statistics and connection to the [DGSA](#dgsa) global ban system.                   | yes[^1]  | Userstats                             | [README](./plugins/cloud/README.md)         |
| MissionStats  | Detailed users statistics / mission statistics.                                                 | yes[^1]  | Userstats                             | [README](./plugins/missionstats/README.md)  |
| Monitoring    | Monitoring and statistics for your DCS servers.                                                 | yes[^1]  | Userstats                             | [README](plugins/monitoring/README.md)      |
| Backup        | Backup or restore your database, server or bot configurations.                                  |   yes    |                                       | [README](./plugins/backup/README.md)        |
| Battleground  | Support for [DCS Battleground](https://github.com/Frigondin/DCSBattleground)                    |   yes    |                                       | [README](./plugins/battleground/README.md)  |
| Battleground2 | Support for the new version of [DCS Battleground](https://github.com/Frigondin/DCSBattleground) |   yes    |                                       | [README](./plugins/battleground2/README.md) |
| Commands      | Create custom discord commands.                                                                 |   yes    |                                       | [README](./plugins/commands/README.md)      |
| Competitive   | Support for PvP communities, especially with TrueSkill™️ ranking system.                        |   yes    | Mission                               | [README](./plugins/competitive/README.md)   |
| DBExporter    | Export the DCSServerBot database or singular tables as json.                                    |   yes    |                                       | [README](./plugins/dbexporter/README.md)    |
| Debug         | Enables debug logging of DCS Hook- and MSE-events into the dcs.log or extra logfiles.           |   yes    |                                       | [README](./plugins/debug/README.md)         |
| Discord       | Discord helper commands.                                                                        |   yes    |                                       | [README](./plugins/discord/README.md)       |
| FunkMan       | Support for [FunkMan](https://github.com/funkyfranky/FunkMan)                                   |   yes    |                                       | [README](./plugins/funkman/README.md)       |
| GreenieBoard  | Greenieboard and LSO quality mark analysis (Super Carrier and Moose.AIRBOSS / FunkMan)          |   yes    | Missionstats                          | [README](./plugins/greenieboard/README.md)  |
| Logistics     | Cargo delivery missions with F10 map markers and in-game task management.                       |   yes    | MissionStats                          | [README](./plugins/logistics/README.md)     |
| LotAtc        | Upload LotAtc Transponder files to your servers.                                                |   yes    |                                       | [README](./plugins/lotatc/README.md)        |
| MOTD          | Message for players on join or when they jump in a module.                                      |   yes    | Mission, MissionStats                 | [README](./plugins/motd/README.md)          |
| Music         | Upload and play music over SRS.                                                                 |   yes    |                                       | [README](./plugins/music/README.md)         |
| ModManager    | Install or update mods into your DCS server.                                                    |   yes    |                                       | [README](./plugins/modmanager/README.md)    |
| Pretense      | Commands for Pretense missions.                                                                 |   yes    |                                       | [README](./plugins/pretense/README.md)      |
| Profiler      | Attaches LUA profilers to DCS (WIP).                                                            |   yes    |                                       | [README](./plugins/profiler/README.md)      |
| Punishment    | Punish users for team-hits or team-kills.                                                       |   yes    | Mission                               | [README](./plugins/punishment/README.md)    |
| RealWeather   | Apply real weather to your missions (also available as an extension).                           |   yes    |                                       | [README](./plugins/realweather/README.md)   |
| RestAPI       | Simple REST-API to query users and statistics (WIP).                                            |   yes    | Userstats, MissionStats               | [README](./plugins/restapi/README.md)       |
| SlotBlocking  | Slot blocking either based on discord roles or credits.                                         |   yes    | Mission, CreditSystem                 | [README](./plugins/slotblocking/README.md)  |
| SRS           | Display players activity on SRS, show active channels and enable slot blocking.                 |   yes    | MissionStats                          | [README](./plugins/srs/README.md)           |
| Tacview       | Install or uninstall Tacview from your server(s) and do a basic configuration.                  |   yes    |                                       | [README](./plugins/tacview/README.md)       |
| Tournament    | A full fledged tournament system for your group.                                                |   yes    | GameMaster, MissionStats, Competitive | [README](./plugins/tournament/README.md)    |
| Voting        | Simple voting system for players to be able to change missions, weather, etc.                   |   yes    |                                       | [README](./plugins/voting/README.md)        |

[^1] These plugins are loaded by the bot by default, but they are not mandatory to operate the bot.<br> 
&nbsp;&nbsp;&nbsp;&nbsp;If you do not want to load any of them, define a list of `plugins` in your main.yaml and only<br>
&nbsp;&nbsp;&nbsp;&nbsp;list the plugins you want to load.

#### How to install Third-Party Plugins
If a community member provides a plugin for DCSServerBot, chances are that it is packed into a zip file. You can 
download this zipfile and place it directly into the /plugins directory. DCSServerBot will automatically unpack the 
plugin for you when DCSServerBot restarts. Keep in mind that some of these plugins might need configurations. Please 
refer to the respective plugin-documentation for more.

### Extensions
Many DCS admins use extensions or add-ons like DCS-SRS, Tacview, LotAtc, etc.</br>
DCSServerBot supports a lot of them already which can add some quality of life.

| Extension   | Scope                                                                                                      | Documentation                                |
|:------------|:-----------------------------------------------------------------------------------------------------------|:---------------------------------------------|
| Cloud       | Registers your servers to the cloud server list.                                                           | [README](./plugins/cloud/README.md)          |
| DSMC        | DSMC mission handling, should be activated when dealing with DSMC missions.                                | [README](./extensions/dsmc/README.md)        |
| GitHub      | Clone / update a Git repository into a directory on your server.                                           | [README](./extensions/github/README.md)      |
| gRPC        | Support gRPC, a communication framework with DCS World.                                                    | [README](./extensions/grpc/README.md)        |
| Lardoon     | Webgui for Tacview with search options.                                                                    | [README](./extensions/lardoon/README.md)     |
| LogAnalyser | Analyses the dcs.log on the fly and does some helpful things. Activated per default.                       | [README](./extensions/loganalyser/README.md) |
| LotAtc      | GCI- and ATC-extension for DCS World. Simple display only extension.                                       | [README](./extensions/lotatc/README.md)      |
| MizEdit     | My own invention, can be used to modify your missions. Very powerful!                                      | [README](./extensions/mizedit/README.md)     |
| ModManager  | Use mods within your DCS World servers.                                                                    | [README](./extensions/modmanager/README.md)  |
| Olympus     | Real-time control of your DCS missions through a map interface.                                            | [README](./extensions/olympus/README.md)     |
| Pretense    | Dynamic campaign framework by Dzsek.                                                                       | [README](./extensions/pretense/README.md)    |
| RealWeather | Real weather for your missions using DCS Real Weather.                                                     | [README](./extensions/realweather/README.md) |
| SkyEye      | AI Powered GCI Bot for DCS                                                                                 | [README](./extensions/skyeye/README.md)      |
| Sneaker     | Moving map interface (see [Battleground](https://github.com/Frigondin/DCSBattleground) for another option! | [README](./extensions/sneaker/README.md)     |
| SRS         | DCS-SRS, "market leader" in DCS VOIP integration.                                                          | [README](./extensions/srs/README.md)         |
| Tacview     | Well known flight data capture and analysis tool.                                                          | [README](./extensions/tacview/README.md)     |
| Trackfile   | Upload your track files to a Discord channel or a (cloud) drive.                                           | [README](./extensions/trackfile/README.md)   |
| VoiceChat   | DCS VOIP system to communicate with other pilots.                                                          | [README](./extensions/voicechat/README.md)   |

> [!IMPORTANT]
> Many of the solutions that these extensions rely on are created by talented community members. I deeply appreciate 
> the extensive time and effort they have invested in developing these tools to their current state.<br>
> However, please note that I am not accountable for these extensions, including any bugs or their overall 
> functionality. The developers typically have dedicated Discord servers for support inquiries or GitHub repositories 
> where you can report any issues.<br>
> Therefore, if you encounter any problems with these solutions, please reach out directly to the developers for 
> assistance.

---

## Installation

### Prerequisites
You need the following software to run DCSServerBot:

#### a) Python
You need to have [Python](https://www.python.org/downloads/) 3.11 or higher installed. 
Please make sure that you tick "Add python.exe to PATH" during your Python installation.

> [!WARNING]
> Python 3.14 is still very new, and many third-party libraries are either not or not fully supported yet.
> The bot should install with 3.14, though, but you cannot expect the same performance as with 3.13 yet.
> That said – use 3.14 at your own risk.

#### b) PostgreSQL
DCSServerBot needs a database to store information in. I decided to use [PostgreSQL](https://www.postgresql.org/download/), as it has great performance
and stability and allows secure remote access, which is needed for [Multi-Node](./MULTINODE.md) installations.
> [!NOTE]
> If you install PostgreSQL on Linux, please make sure that you install the postgresXX-contrib package also.

> [!IMPORTANT]
> If using PostgreSQL remotely over unsecured networks, it is recommended to have SSL enabled.

#### c) Git (optional)
If you want to use instant autoupdate from the master branch, you have to install [Git for Windows](https://git-scm.com/download/win) and make sure 
the ```git```-command is in your PATH.

### Discord Setup
The bot needs a unique Token per installation. This one can be obtained at http://discord.com/developers <br/>
- Create a "New Application"
- Select "Installation" from the left menu and uncheck "User Install"
- Select "Bot" from the left menu and give it a nice name, icon, and maybe a banner.
- Press "Reset Token" and then "Copy" to get your token. 
- Now your Token is in your clipboard. Paste it in some editor for later use. 
- **All** "Privileged Gateway Intents" have to be **enabled** on that page.<br/>
- To add the bot to your Discord "guild" (aka your Discord server), select "OAuth2" from the left menu
- Select the "bot" checkbox in "OAuth2 URL Generator"
- Select the following "Bot Permissions":
  - Left side (optional):
    - Manage Channels (only if you want to have the bot auto-rename your status channel with the current number of players)
    - Ban Members (only if you want to use the bots auto-ban feature that is part of the global [DGSA](#dgsa) banlist)
  - Center (mandatory):
    - Send Messages
    - Manage Messages
    - Embed Links
    - Attach Files
    - Read Message History
    - Add Reactions
- Press "Copy" on the generated URL and paste it into the browser of your choice
- Select the guild the bot has to be added to — and you're done!

> [!IMPORTANT]
> For easier access to user- and channel-IDs, enable "Developer Mode" in "Advanced Settings" in your Discord client.

### 🆕 Setup without using Discord
If you do not want to use Discord, or if you maybe are not allowed to do so due to limitations of your Country, etc.
you can now install DCSServerBot without the need to use Discord. Select the respective option during the 
installation, and you will install a variant that works without.

> [!NOTE]
> It's important to note that DCSServerBot was initially designed for integration with Discord, and certain features 
> only function properly within that platform, such as statistics graphs, greenieboards, and others. 
> However, many aspects of the bot can still be used without Discord, including in-game chat commands, automated 
> restarts, etc.

### Download
Best is to use ```git clone https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot.git``` as you then always have 
the newest fixes, independent of and release version. Otherwise, download the latest release version as ZIP and extract 
it somewhere on your PC running the DCS server(s) and give it write permissions, if needed. 

> [!CAUTION]
> Make sure that the bots installation directory can only be seen by yourself and is not exposed to anybody 
> outside via www etc. as it contains sensitive data. Do NEVER expose any of the DCSServerBot ports to the
> outside world also.

### DCSServerBot Installation (Discord)
Run the provided `install.cmd` script or just `run.cmd`.<br>
It will ask you for your Guild ID (which is your Server ID, so right-click on your Discord server icon and select 
"Copy Server ID") and the bot's user ID (right-click on the bot user and select "Copy User ID"). 
Then it will search for existing DCS installations, create the database user, password, and database, and asks whether 
you want to add existing DCS servers to the configuration.<br>
When finished, the bot should launch successfully and maybe even start your servers already, if configured.

### DCSServerBot Installation (non-Discord)
Run the provided `install.cmd` script or just `run.cmd`.<br>
It will ask you for your DCS group name and a role mapping, where you can give specific DCS users roles that are needed
to make the in-game commands work. You need the UCIDs of the users here. Then it will search for existing DCS 
installations, create the database user, password, and database, and asks whether you want to add existing DCS servers 
to the configuration.<br>
When finished, the bot should launch successfully and maybe even start your servers already, if configured.

> [!IMPORTANT] 
> You should shut down your DCS servers during the bot installation, as it places its own LUA hooks inside
> the server's Scripts directory.<br>
> Please keep also in mind that a lot of configuration parameters which you find below are not needed for a 
> non-Discord setup. If you have no idea what to put in a specific parameter, that is usually a good sign to just
> skip it.

You can start the installer with these parameters:
```
Usage: install.cmd [-h] [-n NODE] [-c CONFIG] [-u USER] [-d DATABASE]

Welcome to DCSServerBot!

options:
  -h, --help                        Show this help message and exit
  -n NODE, --node NODE              Node name (default = hostname)
  -c CONFIG, --config CONFIG        Path to configuration
  -u USER, --user USER              Database username (default = dcsserverbot)
  -d DATABASE, --database DATABASE  Database name (default = dcsserverbot)
```
You might want to provide different node names if you install multiple nodes on one PC, and different database user
and database names if you want to install multiple bots for multiple Discord groups.

> [!TIP]
> If you need to rename a node, just launch `install.cmd` again with the --node parameter and give it the new name.
> You will then get a list of existing nodes and will be asked to either add a new node or rename an existing one.
> Select rename an `existing` one and select the node to be renamed.<br>
> This might be necessary, if your hostname changes, or you move a bot from one PC to another.

### Desanitization
DCSServerBot desanitizes your MissionScripting environment. That means it changes entries in Scripts\MissionScripting.lua
of your DCS installation. If you use any other method of desanitization, DCSServerBot checks if additional 
desanitizations are required and conducts them.

> [!IMPORTANT]
> DCSServerBot needs write-permissions on the DCS-installation directory.<br>
> You can usually achieve that by giving the "User group" write permissions on it. Right-click on your DCS installation
> folder,<br>select Properties → Security → Edit, select "Users (...)" and tick Modify below. Then press the OK button.
> There might be a question about changing the permission on all subdirectories — say yes in that case. 

Your MissionScripting.lua should look like this after a successful bot start:
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
> [!TIP]
> To use a custom MissionScripting.lua with enhanced desanitization (such as for LotAtc, Moose, OverlordBot, or 
> similar) or to load additional features (like LotAtc or DCS-gRPC), place your custom MissionScripting.lua file in 
> the config directory of the bot. 
> The custom script will then be used instead of the default one.
---

## Configuration
The bot configuration is held in several files in the **config** subdirectory.
If you run the `install.cmd` script for the first time, it will generate basic files for you that you can amend to your 
needs afterward. Your bot should be ready to run already, and you can skip this section for now if you don't want to
bother with the bot configuration in the first place.

> [!TIP]
> If you run more than one bot node, the best is to share the configuration between all nodes. 
> This can be done via a cloud drive, for instance, or with some file sync tool (see [Multi-Node](./MULTINODE.md)).

The following samples will show you what you can configure in DCSServerBot. For most of the configuration, default 
values will apply, so you don't need to define all these values explicitly. I printed them here for completeness and 
for the sake of documentation.

### config/main.yaml
This file holds the main information about DCSServerBot. You can configure which plugins are loaded here, for instance.

```yaml
guild_id: 112233445566    # Your Discord server ID. Right-click on your server and select "Copy Server ID". On non-discord installations this number is filled for you.
guild_name: My Group      # Non-Discord only: your DCS group name
autoupdate: true          # use the bots autoupdate functionality, default is false
use_dashboard: true       # Use the dashboard display for your node. Default is true.
chat_command_prefix: '-'  # The command prefix to be used for in-game chat commands. Default is "-"
language: de              # Change the bot's language to German. This is WIP, several languages are in the making, including DE, ES, RU and more
validation: lazy          # YAML schema validation. One of none, lazy, strict. none = disabled, lazy = display warnings / errors in log (default), strict = fail on error
database:
  url: postgres://USER:PASSWORD@DB-IP:DB-PORT/DB-NAME   # The bot will auto-move the database password from here to a secret place and replace it with SECRET.
  pool_min: 5             # min size of the DB pool, default is 5
  pool_max: 10            # max size of the DB pool, default is 10
  max_reties: 10          # maximum number of retries to initially connect to the database on startups
logging:
  loglevel: DEBUG           # loglevel, default is DEBUG
  logrotate_count: 5        # Number of logfiles to keep after rotation. Default is 5.    
  logrotate_size: 10485760  # max size of a logfile, default is 10 MB
  utc: true                 # log in UTC (default: true), use local time otherwise
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
- modmanager
- commands
- restapi
```

### config/nodes.yaml
This file holds the main configuration for all your nodes.<br>
For a cluster installation you want to describe all your nodes and instances on all your nodes, as the bot can 
(auto-)migrate stuff in-between the cluster!

```yaml
NODENAME:                       # this will usually be your hostname
  listen_port: 10042            # On which port should the bot listen? Default is 10042
  listen_address: 127.0.0.1     # Optional: On which interface should the bot listen? Default is 127.0.0.1 (localhost only).
  public_ip: 88.77.66.55        # Optional: Your public IP. ONLY if you have a static IP! Put this in here to speed up the startup-process of the bot.
  slow_system: false            # Optional: if you are using a slower PC to run your servers, you should set this to true (default: false)
  use_upnp: true                # The bot will auto-detect if there is a UPnP IGD available and configure this setting initially for you! If you do NOT want to use UPnP, even IF it is available, put this to false.
  nodestats: true               # Enable/disable node statistics (database pool and event queue sizes), default: true
  restrict_commands: true       # Disable commands that can affect the integrity of the server. Default: false (see MULTINODE.md)
  restrict_owner: false         # If set to true, the owner of the bot can also not run restricted commands. Default: false (see MULTINODE.md)
  database:                     # Optional: It might be that you need to use different IPs to connect to the same database server. This is the place you could do that.
    url: postgres://USER:PASSWORD@DB-IP:DB-PORT/DB-NAME   # The bot will auto-move the database password from here to a secret place and replace it with SECRET.
    pool_min: 5                 # min size of the DB pool, default is 5
    pool_max: 10                # max size of the DB pool, default is 10
    max_reties: 10              # maximum number of retries to initially connect to the database on startups
  cluster:                      # Cluster only: Your cluster configuration. See MULTINODE.md for reference.
    preferred_master: true      # Cluster only: this node should be the preferred master node (default: false)
    no_master: false            # Cluster only: this node should never be a master node (default: false)
    heartbeat: 30               # Cluster only: time for the heartbeat between the master and agent nodes to run (default: 30)
    cloud_drive: true           # Cluster only: set this to false if you do not have the bot installed on a cloud drive (default and recommended: true) 
  auto_affinity:                # Optional / Experimental: auto-affinity settings
    enabled: true               # Enabled auto-affinity **for the whole node** (default = false)
    excluded_cores: [0, 1]      # Optional: exclude cores from auto-affinity.
    min_cores: 1                # Min number of cores to be used for the bot itself (default: 1)
    max_cores: 2                # Max number of cores to be used for the bot itself (default: 2)
    quality: 1                  # Quality of the requested CPU cores (0 = low, 1 = medium, 2 = high, default: 1)
  DCS:
    installation: '%ProgramFiles%\\Eagle Dynamics\\DCS World Server'  # This is your DCS installation. Usually autodetected by the bot.
    autoupdate: true            # enable auto-update for your DCS servers. Default is false.
    announce:                   # Optional: post a message to Discord after an update was conducted
      channel: 11223344556677
      title: DCS has been updated to version {}!
      description: 'The following servers have been updated:'
      footer: Please make sure you update your DCS client to join!
      mention:                  # Optional mentioning
        - Admin
        - DCS Admin
    update_window:              # Optional update window. No update will happen if N is set for the respective time and day.
      timezone: Europe/Berlin   # Optional timezone, default is the local time of the server.
      00-18: YYYYYYY
      18-20: YYYYYYN            # Do not update DCS on Sundays, 18:00-20:00 LT
      20-24: YYYYYYY
    use_upnp: true              # Do you want to use UPnP to forward your DCS ports automatically? If not set, the global setting will be used.
    cloud: true                 # If you have installed DCS on a NAS or cloud drive, autoupdate and desanitization will only take place once on all your nodes.
    desanitize: true            # Desanitize your MissionScripting.lua after each update. Default is true.
    minimized: true             # Start DCS minimized (default: true)
    user: xxxx                  # Your DCS username (only needed for specific use-cases)
    password: xxxx              # Your DCS password (will be auto-moved by the bot to a secret place)
  extensions:                   # Your extensions have 2 sections, one in the node and one in each instance.
    SRS:                        # Your node-global settings for SRS
      installation: '%ProgramFiles%\DCS-SimpleRadio-Standalone' 
      autoupdate: true          # Auto-update SRS, whenever a new version is available.
      use_upnp: true            # Do you want to use UPnP to auto-forward your SRS ports? If not set, the global setting will be used.
    Tacview:                    # Your node-global settings for Tacview
      installation: 'C:\Program Files (x86)\Steam\steamapps\common\Tacview'
      tacviewExportPath: '%USERPROFILE%\Documents\Tacview'
#   Any other Extension:
#     ...
  instances:
    DCS.dcs_serverrelease:      # The name of your instance. You can have multiple instances that have to have unique names.
      home: '%USERPROFILE%\\Saved Games\\DCS.dcs_serverrelease' # The path to your saved games directory.
      missions_dir: '%USERPROFILE%\Documents\Missions'       # You can overwrite the default missions dir like so. Default is the Missions dir below the instance home folder.
      mission_rewrite: false    # Disable rewrite of missions by MizEdit or RealWeather. The server will be stopped for any mission change then. (default: true)
      bot_port: 6666            # The port DCSServerBot uses to communicate with your DCS server. Each instance has to have a unique port. This is NOT your DCS port (10308)!!!
      webgui_port: 8088         # The port of the WebGUI (default: 8088)
      dcs_port: 10308           # The DCS port of this instance (default: 10308)
      max_hung_minutes: 3       # Let DCSServerBot kill your server if it is unresponsive for more than x minutes. Default is 3. Disable it with 0.
      affinity: 2,3             # Deprecated: set the CPU-affinity for the DCS_Server.exe (use auto_affinity instead)
      auto_affinity:            # Optional: configure CPU affinity
        min_cores: 1            # Min. number of cores to be used (default: 1)
        max_cores: 2            # Max. number of cores to be used (default: 2)
        quality: 3              # Core quality (1 = normal, 2 = high, 3 = reserved for DCS only, default: 3)
      priority: normal          # Optional: set the process priority (low, normal, high, realtime) for the DCS_Server.exe
      extensions:               # See the extension documentation for more detailed information on what to set here.
        SRS:
          config: '%USERPROFILE%\Saved Games\DCS.dcs_serverrelease\Config\SRS.cfg'  # it is recommended to copy your SRS "server.cfg" below your instances home directory.
          host: 127.0.0.1       # SRS servers local IP (default is 127.0.0.1)
          port: 5002            # SRS servers local port (default is 5002). The bot will change this in your SRS configuration if set here!
          autostart: true       # this will autostart your DCS server with the DCS server start (default: true)
        Tacview:
          show_passwords: false # If you don't want to show the Tacview passwords (default: true)
#    instance2:                 # you can have an unlimited number of instance configurations, but each instance has to have a physical representation on your disk.
#      ...
```
> [!TIP]
> Remember to put apostrophes around any path, as the colon might mangle your YAML!

### config/servers.yaml
This is your server configuration.<br>
You might wonder why the configuration is split between nodes.yaml and servers.yaml? Even if you have a basic setup! 
This is to decouple the server configuration from the physical node (aka the "DCS.exe" / "DCS_Server.exe" process). You 
will learn to love it, especially when you decide to move a server from one instance to another or even from one node to 
another. This is much easier with a non-coupled approach like that.
```yaml
DEFAULT:
  messages:                     # General messages for servers. You can overwrite any in any server.
    greeting_message_members: "{player.name}, welcome back to {server.name}!"
    greeting_message_unmatched: '{player.name}, please use /linkme in our Discord, if you want to see your user stats!'
    message_player_username: Your player name contains invalid characters. 
      Please change your name to join our server. # Default message for players with invalid usernames
    message_player_default_username: Please change your default player name at the top right  
      of the multiplayer selection list to an individual one! # Default message for players with default usernames
    message_player_inappropriate_username: Your username is inappropriate and needs to be changed to join this server.
    message_ban: 'You are banned from this server. Reason: {}' # default message, if a player is banned on the DCS server
    message_reserved: 'This server is locked for specific users.\nPlease contact a server admin.' # Message if server requires discord role (optional)
    message_no_voice: 'You need to be in voice channel "{}" to use this server!'  # default message, if you are not in Discord voice, but force_voice is on.
    message_seat_locked: 'Your player is currently locked.' # Server and all seats are locked (by /player lock)
  message_timeout: 10           # default timeout for DCS popup messages in seconds 
  profanity_filter: true        # Use the profanity filter for player names and the in-game chat (default: false).
  no_join_with_cursename: true  # Block people with potential cursewords in their nicknames (default: true, only works with profanity_fileter: true)
  display_ai_chat: false        # do not display AI chat messages in the chat channel (default: false)
  rules: |                      # Optional: Rules to be displayed for new users (needs MissionStats enabled!)
    These are the rules to play on this server:
    1) Do not team-kill
    2) Do not harass people
    3) Be a decent human being
    4) ...
  accept_rules_on_join: true    # True, if rules have to be acknowledged (players will be moved to spectators otherwise, default: false)
My Fancy Server:                # Your server name, as displayed in the server list and listed in serverSettings.lua
  channels:
    status: 1122334455667788    # The Discord channel to display the server status embed and players embed into. Right-click on your channel and select "Copy Channel ID". You can disable it with -1
    chat: 8877665544332211      # The Discord channel for the in-game chat replication. You can disable it by setting it to -1.
    events: 1928374619283746    # Optional: if you want to split game events from chat messages, you can enable an optional events channel.
    admin: 1188227733664455     # Optional: The channel where you can fire admin commands to this server. You can decide if you want to have a central admin channel or server-specific ones. See bot.yaml for more.
    voice: 1827364518273645     # Optional: The voice channel, where people need to connect to (mandatory if force_voice is true). 
    audit: 9182736459182736     # Optional: a server-specific audit channel (for those of you who like channels, all others can use the global one)
  server_user: Admin            # Optional Name of the server user #1 (technical user), default is "Admin".
  show_passwords: true          # Optional: Do you want the password to be displayed in the server status embed? (default: true)
  smooth_pause: 5               # Optional: Servers that are configured to PAUSE on startup will run for this number of seconds until they are paused again (default 0 = off)
  lock_on_load: 120             # Optional: Schedule a time for server lockdown during mission restarts, allowing for complete initialization before users can re-enter.
  ping_admin_on_crash: true     # Optional: Ping DCS Admin role in discord, when the server crashed. Default: true
  autoscan: false               # Optional: Enable autoscan for new missions (and auto-add them to the mission list). Default: false
  autoadd: true                 # Optional: Enable auto-adding of uploaded missions (default: true)
  validate_missions: true       # Optional: Check if your missions can be loaded or not (missing maps, etc.). Default: true.
  ignore_dirs:                  # Optional: ignore directories from mission upload / mission add (already ignored are .dcssb, Scripts and Saves)
    - archive
  autorole: Fancy Players       # Optional: give people this role if they are online on this server (overwrites autorole/online in bot.yaml!).
  show_atis: true               # Optional: show ATIS information on BIRTH (default: false)
  force_voice: false            # Optional: enforce the usage of a voice channel (users need to be linked!) - default: false
  discord:                      # Optional: specify discord roles that are allowed to use this server
    - '@everyone'               # Attention: people cannot self-link on these servers and have to be liked properly already!
  managed_by:
    - Special Admin             # Optional: a list of Discord roles that can manage this server (default: DCS Admin)
  chat_log:
    count: 10                   # A log file that holds the in-game chat to check for abuse. Tells how many files will be kept, default is 10.
    size: 1048576               # Max logfile size, default is 1 MB. 
  no_coalition_chat: true       # Optional: Do not replicate red and blue chats to the Discord chat replication (default: false)
  afk:                          # Optional: AFK check
    message: '{player.name}, you have been kicked for being AFK for more than {time}.'  # default message for AFK users
    afk_time: 300               # Time in seconds after which a player on spectators is considered being AFK. Default: -1, which is disabled
    exemptions:                 # List of UCIDs or discord roles that are exempted from AFK kicks (besides the users that have the DCS Admin or GameMaster role)
      ucid:
        - aabbccddeeff1122334455
      discord:
        - Donators              # DCS Admin and GameMaster are automatically exempted from AFK kicks
  usage_alarm:          # Optional: usage alarms for your server
    min_threshold: 30   # send a message if less than 30 people fly on your server
    max_threshold: 10   # send a message if more than 10 people fly on your server
    role: DCS Admin     # the role that should be pinged
    channel: 1122334455 # the channel to send the ping in (default: admin channel)
  slot_spamming:        # Optional: allow for max x slot changes per y seconds (5 in 5 in the example)
    message: You have been kicked for slot spamming! # default message for slot spamming
    check_time: 5       # number of seconds to test
    slot_changes: 5     # number of slot changes in these numbers of seconds that are allowed
  smart_bans: true      # Optional: Used to disable the smart ban system (default: enabled). Servers that see people getting banned by a high amount of IPv4 re-usage (in CN, for instance) you want to say false here.
  serverSettings:       # Optional: Overwrite the serverSettings.lua with these values
    port: 10308
    advanced:
      resume_mode: 0
My 2nd Fancy Server:    # You can have an unlimited number of server configurations.
  ...
```

### config/presets.yaml
This file holds your different presets that you can apply to missions as modifications.<br>
See [MizEdit](./extensions/mizedit/README.md) for further details.

### services/bot.yaml
This is your Discord-bot configuration.

```yaml
token: SECRET_DISCORD_TOKEN                     # Your TOKEN, as received from the discord developer portal. The bot will auto-move this to a secret place.
owner: 1122334455667788                         # The Discord ID of the owner. Right-click on your Discord user, select "Copy User ID". If unsure, use the bot user.
automatch: true                                 # Use the bot's auto-matching functionality (see below), default is false.
autoban: false                                  # Use the bot's auto-ban functionality (see below), default is false.
autorole:                                       # Automatically give roles to people, depending on conditions (see below). The roles need to be set up in your Discord server.
  on_join: Member                               # Give anyone the "Member" role if they join your Discord.
  linked: DCS                                   # Give people that get linked the DCS role.
  online: Online                                # Give people that are online on any of your servers the "Online" role.
no_dcs_autoban: false                           # If true, people banned on your Discord will not be banned on your servers (default: false)
message_ban: User has been banned on Discord.   # Default reason to show people that try to join your DCS servers when they are banned on Discord.
message_autodelete: 300                         # Optional: Most of the Discord messages are private messages. If not, this is the timeout after which they vanish. Default is 300 (5 mins). 
channels:
  admin: 1122334455667788                       # Optional: Central admin channel (see below).
  audit: 88776655443322                         # Central audit channel to send audit events to (default: none)
reports:
  num_workers: 4                                # Number of worker threads to be used for any reports generated by the bot. Default is 4.
discord_status: Managing DCS servers ...        # Message to be displayed as the bot's Discord status. Default is none.
proxy:                                          # Optional: Proxy to be used for Discord
  url: 'https://127.0.0.1:8080'
  username: abcd                                # Optional: username and password (password will be secured after the first run)
  password: defg
roles:                                          # Roles mapping. The bot uses internal roles to decouple from Discord's own role system.
  Admin:                                        # Map your Discord role "Admin" to the bot's role "Admin" (default: Admin)
  - Admin                                       
  Alert:                                        # Optional Alert role. Default is DCS Admin. Would be pinged on server crashes and low performance
  - DCS Admin
  DCS Admin:                                    # Map your Discord role "Moderator" and "Staff" to the bots "DCS Admin" role (default: DCS Admin)
  - Moderator
  - Staff
  GameMaster:                                   # Map the GameMaster role to anybody with the Staff role in your Discord.
  - Staff
  DCS:                                          # Map the bot's DCS role to everyone in your discord. Only everyone needs the leading @!
  - @everyone
```
> [!CAUTION]
> Never ever share your Discord TOKEN with anyone! If you plan to check in your configuration to GitHub, don't do that
> for the Discord TOKEN. GitHub will automatically revoke it from Discord for security reasons.

> [!IMPORTANT]
> The bot role needs to be moved above any other role in your Discord server that it has to manage.<br>
> If you, for example, want the bot to give the "Online" role to people, it has to be below the bot's role.

> [!TIP]
> The bot will remove the Discord TOKEN from your bot.yaml on the first startup.<br>
> If you want to replace the Discord TOKEN later, re-add the line into your bot.yaml and DCSServerBot will replace 
> the internally saved Discord TOKEN with this one.
> `token: NEW_TOKEN`

### Mission File Handling
When modifying missions using tools like MizEdit or RealWeather, the DCSServerBot creates a copy of the mission file 
that's currently being run. This is because the file is locked by the running DCS process, preventing direct 
overwriting.
The bot then loads the saved copy instead, ensuring smooth gameplay without interruptions or player disconnections. 
In such cases, it also maintains the ability to roll back to the original, unmodified version of the mission if needed.
For this purpose, DCSServerBot creates a directory .dcssb to save the original mission files (.orig) and copies of the 
running mission, if needed.

#### .dcssb sub-directory
DCSServerBot creates its own directory below your Missions-directory. This is needed to allow changes of .miz files,
that are locked by DCS (see above). Whenever a Missions\x.miz file is locked, a similar file is created in 
Missions\.dcssb\x.miz. 
This file is then changed and loaded. Whenever you change the mission again, the earlier file (Missions\x.miz) is 
changed again. This happens in a round-robin way.

#### .orig files
Whenever a mission is changed, the original one is copied into a file with the .orig extension. If you see any such file
in your .dcssb-directory, there is nothing to worry about. These are your backups in case you want to roll back.

#### Example
You upload test.miz to your Missions directory and run it. Your server now locks the mission "test.miz."<br>
Now you change the mission, let's say the start-time. You use `/mission modify` and load the respective preset.
First, a backup is created by copying test.miz to .dcssb/test.miz.orig. Then, it gets changed but cannot be written, 
as test.miz is locked by DCS. So, DCSServerBot creates .dcssb\test.miz, writes the new mission, and loads .dcssb\test.miz 
to your DCS server.<br>
After this process, you end up with test.miz, .dcssb\test.miz.orig, and .dcssb\test.miz. Sounds like a lot of copies? 
Well, it's what you get when you want to change things at runtime.<br>
DCSServerBot is smart enough to be able to replace the missions again on upload, load the correct mission on, 
`/mission load` and provide the correct mission on `/download <Missions>` also.

> [!NOTE]
> When changing missions with `/mission modify` or the [MizEdit](./extensions/mizedit/README.md) extension, the change 
> will per default use the .orig mission as a startpoint. This means, if you apply some preset that is not re-entrant, a 
> later call will not change the changed mission again, but will run against the .orig mission.<br>
> You can configure this behavior though, all commands have an option "use_orig", which you can set to "false" to use
> the latest mission file as reference instead.

### CJK-Fonts Support
DCSServerBot supports external fonts, especially CJK-fonts to render the graphs and show your player names using the 
real characters of your language. Unfortunately, I cannot auto-download the respective fonts from Google Fonts
anymore, where I have to ask you guys to do that on your own.<br>
To download the supported fonts, go to https://fonts.google.com/ and search for 
- [Noto Sans Traditional Chinese](https://fonts.google.com/noto/specimen/Noto+Sans+TC)
- [Noto Sans Japanese](https://fonts.google.com/noto/specimen/Noto+Sans+JP)
- [Noto Sans Korean](https://fonts.google.com/noto/specimen/Noto+Sans+KR)

Then press "Get font" and "Download all". Copy the ZIP file into a folder "fonts" that you create below the DCSServerBot
installation directory. The bot will take this ZIP on its next startup, unpack it, and delete the ZIP file. From then
on, the bot will use the respective font(s) without further configurations.

### Auto Matching (default: enabled)
To use in-game commands, your DCS players need to be matched to Discord users. Matched players are able to see statistics,  
and you can see a variety of statistics yourself as well. The bot offers a linking system between Discord and DCS accounts 
to enable this. Players can do this with the `/linkme` command. This creates a permanent and secured link that can then 
be used for in-game commands.<p>
The bot can also auto-match a DCS player to Discord user. This way, players can see their own stats via Discord 
commands. The bot will try to match the Discord username to DCS player name. This works best when DCS and Discord names 
match! It can generate false links, which is why I prefer (or recommend) the /linkme command. People still seem 
to like the auto-matching, that is why it is in, and you can use it (disabled per default).

### Auto-Banning (default: disabled)
DCSServerBot supports automatically bans / unbans of players from the configured DCS servers, as soon as they leave / join 
your Discord guild. If you like that feature, set `autoban: true` in services/bot.yaml (default: false).

However, players that are being banned from your Discord or that are being detected as hackers are auto-banned from 
all your configured DCS servers independent of that setting. You can prevent this by setting `no_dcs_autoban: true`.

### Roles (Discord and non-Discord)
The bot uses the following **internal** roles to apply specific permissions to commands.<br>
You can map your Discord roles to these internal roles as described in the example above or, for the non-Discord
variant, you add your UCIDs as a list below each group.<br>
Non-Discord installations usually only need the "Admin" and "DCS Admin" roles.

> [!NOTE]
> The owner of the bot (owner_id in bot.yaml) can run _any_ command, independently of the role.

| Role            | Description                                                                                                                                          |
|:----------------|:-----------------------------------------------------------------------------------------------------------------------------------------------------|
| Admin           | People with this role are allowed to manage the server, start it up, shut it down, update it, change the password and gather the server statistics.  |
| DCS Admin       | People with this role are allowed to restart missions, managing the mission list, ban and unban people.                                              |
| DCS             | People with this role are allowed to chat, check their statistics and gather information about running missions and players.                         |
| GameMaster      | People with this role can see both [Coalitions](./COALITIONS.md) and run specific commands that are helpful in missions.                             |

See [Coalitions](./COALITIONS.md) for coalition roles.

### Profanity Filter
DCSServerBot supports profanity filtering of your player nicknames and the in-game chat. 
Per default, that is not enabled, but you can just set `profanity_filter: true` in your servers.yaml to activate it. 
It will then copy one of the prepared lists from `samples/wordlists` to `config/profanity.txt` which you then can amend 
to your needs on your own. 
The language is determined by which language you set in your main.yaml (default=en).

### Handling of Passwords and other Secrets
DCSServerBot stores the secret Discord TOKEN and your database and (optional) DCS password in separate files. If  
you have ever added these to your config files as mentioned above, the bot will take them and move them away. This is 
a security feature. If you somehow forgot the values, you can always reveal them by starting the bot with the -s option
like so: `run.cmd -s`.

### DCS/Hook Configuration
The DCS World integration is done via Hooks. They are being installed automatically into your configured DCS servers by 
the bot.

### Sample Configuration
To view some sample configurations for the bot or for each configurable plugin, look [here](/samples/README.md).

### Additional Security Features
Players who have no pilot ID (empty or whitespace) or that share an account with others will not be able to join your 
DCS server. This is not configurable, it's a general rule (and a good one in my eyes).<br>
Besides that, people that try to join from the very same IP that a banned user has used before will be rejected also
(ban-avoidance). You get a message in the discord admin-channel about it.

> [!NOTE]
> If you want to "unban" such a player that was detected to have the same IP but, for instance, joined from a shared 
> flat, you can unban the IP with `/dcs unban <ip>`.

### Set up Multiple Servers on a Single Host
To run multiple DCS servers under the control of DCSServerBot, you have to make sure that you configure different 
communication ports. This can be done with the parameter `bot_port` in nodes.yaml. The default is 6666. You can 
increase that for every server (6667, 6668, ...).<br>
Remember to configure different Discord channels (`chat` and `status`, optional `admin`) for every server, too. 
This will be done in the servers.yaml file.<br>
To add more servers, follow the steps above, and you're good, unless they are on a different Windows server 
(see below).

> [!NOTE]
> DCSServerBot will autodetect all configured DCS servers on an installation and generate simple configuration files 
> for you already. To add a new instance, you can either do that manually or use `/node add_instance` in your Discord.

### How to set up a Multi-Node-System?
DCSServerBot can be used to run multiple DCS servers on multiple PCs, which can even be at different locations. 
The installation and maintenance of such a use-case is just a bit more complex than a single server 
installation. Please refer to [Multi-Node](./MULTINODE.md) for further information.

---

## Starting the Bot
To start the bot, use the packaged ```run.cmd``` command. This creates the necessary Python virtual environment and 
launches the bot afterward.<br/>
If you want to run the bot from Windows autostart, press Win+R, enter `shell:startup` and press ENTER, create a 
shortcut to your `run.cmd` in there.

---

## Repairing the Bot
If you have issues starting DCSServerBot, especially after an update, it might be that some third-party library got 
corrupted. In rare cases, it can also happen, that an auto-update is not possible at all, because some file got changed 
that was not supposed to be changed, or some other corruption has occurred.<br>
In these cases, you can run the `repair.cmd` script in the DCSServerBot installation folder.

---

## Backup and Restore
The platform allows you to back up and restore your database, server configurations, or bot settings. 
The backup and restore functionality are accessible in the Backup [service](./services/backup/README.md) 
and [plugin](./plugins/backup/README.md).

> [!NOTE]
> The bot includes an auto-restore feature that allows you to easily transfer your data. 
> To use this feature, copy the appropriate backup file into a folder named restore within the DCSServerBot 
> installation directory (make sure to create it first). Upon startup, DCSServerBot will read this file and restore any 
> saved information such as:
> * Database backup
> * DCSServerBot configuration backup
> * Instance backup (including missions and configurations; for details, see the backup service)

> [!TIP]
> Utilizing the auto-restore feature can be helpful when moving your database from one computer to another. 
> After installing DCSServerBot on the new system, place the appropriate db_*.tar file in a `restore` folder within the 
> bot's installation directory and ensure the node's nodes.yaml file points to the newly created database. 
> Then launch the bot and allow the restoration process to complete.
> 
> _Please be prepared to provide the new PostgreSQL master password when prompted._
---

## How to use DCSServerBot in Missions?

### How to talk to the Bot from inside Missions
If you plan to create Bot-events from inside a DCS mission, that is possible! 
Make sure you include this line in a trigger:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
```
> Don't use a Mission Start trigger, as this might clash with other plugins loading stuff into the mission._<br/>
 
After that you can, for instance, send chat messages to the bot using
```lua
  dcsbot.sendBotMessage('Hello World', '12345678') -- 12345678 is the ID of the channel, the message should appear, default is the configured chat channel
```
inside a trigger or anywhere else where scripting is allowed.<br>
If you want to send raw messages containing discord formatting options (like ANSI), then you can do it like so:
```lua
  dcsbot.sendBotMessage('Hello World', '12345678', true) -- last parameter is "raw", which disables any bot-related formatting
```

> [!IMPORTANT]
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
They will be posted in the chat channel by default, if not specified otherwise (adding the channel id as a last 
parameter of the sendEmbed() call, see sendBotMessage() above).

If you like to use a single embed, maybe in the status channel, and update it instead of creating new messages, you 
can do that by giving is a name like "myEmbed" in this example. The name has to be unique per server.
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

## How to enhance DCSServerBot?

### How to overwrite DCSServerBot's default commands?
You can change any command either in discord or the in-game chat. You can select a different name, different roles, etc.,
or even disable the command completely.
For Discord, you need to keep the command structure in mind, meaning, if you have a group command (like /server startup)
or a single one (like /help). If you want to change any of the commands, go to your respective plugin configuration and
add a top-level section like so:
```yaml
commands:
  dcs:
    bans:
      roles:
      - Admin
      enabled: false # disable a command
      name: prohibiciones
      description: mostrar una lista de todas las prohibiciones en sus servidores
```
If you add this to your admin.yaml, it will rename the command `/dcs bans` to `/dcs prohibiciones`, change the 
documentation of it and make it only available for people that own the Admin role.

If you want to change in-game chat commands, you can do it like so:
```yaml
chat_commands:
  911:
    enabled: false
```
If you add these lines to your mission.yaml, you disable the -911 command on your servers.

### How to develop your own Services / Extensions, Plugins, and Reports?
DCSServerBot has an extensible architecture. You can more or less overwrite, enhance, change anything you like.
The easiest way to start developing for DCSServerBot would be to read up the concepts and to look at some examples.
I have created some READMEs for you that you can start with:

| Component  | Description                                                                                                                         | Documentation                     |
|:-----------|:------------------------------------------------------------------------------------------------------------------------------------|:----------------------------------|
| Plugin     | A plugin provides Discord or in-game commands. It handles events from DCS and runs on the master node only.                         | [README](./plugins/README.md)     |
| Service    | A service runs once onto each node of your cluster. Some services are master bound.                                                 | [README](./services/README.md)    |
| Extension  | An extension runs for each supported server, but on the node the server is running on. It can access all resources of that server.  | [README](./extensions/README.md)  |                             
| Reports    | The reporting framework allows you to create your own customized reports for DCSServerBot or overwrite existing ones.               | [README](./reports/README.md)     |

> [!NOTE]
> If you decide to develop something that might be of interest for other community members, I highly encourage you to 
> share it. You can either ask me to incorporate it in the solution (some requirements might need to be met), or you 
> just provide it in the format and way you prefer.

---

## DGSA
DGSA (DCS Global Server Admins) is an association of server administrators managing the largest and most popular DCS 
servers worldwide. We established this group to enable quick and efficient coordination, ensuring a smooth experience 
for players by addressing issues such as cheaters or disruptive individuals who negatively impact the enjoyment of DCS.

One of the outcomes of this collaboration is the creation of two banlists: one for DCS and one for Discord. These 
lists include players who fall under the above-mentioned criteria. The **DCSServerBot** integrates with these banlists, 
ensuring that such players are automatically prevented from accessing your servers. For details on configuring this 
feature, please visit [this guide](./plugins/cloud/README.md).

If you’re interested in becoming a member of DGSA, don’t hesitate to reach out to me (see contact details right below).

---

## Contact / Support
If you need support, want to chat with me or other users, or are interested in contributing, feel free to join 
my [Support Discord](https://discord.gg/h2zGDH9szZ).<br>

If you enjoy what I do and would like to support me, you can do so on my [Patreon Page](https://www.patreon.com/DCS_SpecialK).

---

## Credits
Thanks to the developers of the awesome solutions [HypeMan](https://github.com/robscallsign/HypeMan) and 
[perun](https://github.com/szporwolik/perun), that gave me the main ideas for this solution. 
I gave my best to mark the few parts in the code to show where I copied some ideas or even code from you guys, 
which honestly is just a tiny piece. Hope that is ok. Also, thanks to Moose for aligning the API for [FunkMan](https://github.com/funkyfranky/FunkMan) 
with me and making it compatible with DCSServerBot in the first place.
