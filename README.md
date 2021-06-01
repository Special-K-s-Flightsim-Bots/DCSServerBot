# Documentation
DCSServerBot lets you interact between Discord and DCS.
The bot has to be installed on the same machine that runs DCS or at least in the same network.
The following two main features are supported:

## DCS Server Remote Control
Control DCS servers in your local network via Discord commands.
The following commands are supported:
Command|Parameter|Description
-------|-----------|--------------
.status||(Admin only) Lists all registered DCS servers. They will auto-register on startup.
.mission||Information about the active mission.
.briefing/.brief||Shows the description / briefing of the running mission.
.players||Lists the players currently active on the server.
.list||Lists all missions with IDs available on this server (same as WebGUI).
.add|[miz-file]|Adds a specific mission to the list of missions. Has to be in DCS_HOME/Missions. If no miz file is provided, a list of all files will be provided.
.delete|ID|Deletes the mission with this ID from the list of missions.
.load|ID|Load a specific mission by ID.
.restart|[time in secs] [message]|Restarts the current mission after [time] seconds. A message will be sent into the ingame chat of that server.
.chat|message|Send a message to the DCS ingame-chat.
.ban|@member or ucid|Bans a specific player either by his Discord ID or his UCID
.unban|@member or ucid|Unbans a specific player either by his Discord ID or his UCID
.bans||Lists the current bans.

## User Statistics
Gather statistics data from users and display them in a user-friendly way in your Discord.
The following commands are supported:
Command|Parameter|Description
-------|-----------|--------------
.link|@member or ucid|Sometimes users can't be linked automatically. That is a manual workaround.
.statistics/.stats|[@member]|Display your own statistics or that of a specific member.
.highscore/.hs|[day] / [week] / [month]|Shows the players with the most playtime or most kills in specific areas (CAP/CAS/SEAD/Anti-Ship)
.serverstats|[week] / [month]|Displays server statistics, like usual playtimes, most frequented servers and missions

## Installation
First of all, download the latest release version and extract it somewhere on your server, where it has write access.
Make sure that this directory can only be seen by yourself and is not exposed to anybody outside via www etc.

### Prerequisites
You need to have [python 3.8](https://www.python.org/downloads/) or higher and [PostgreSQL](https://www.postgresql.org/download/) installed.
The python modules needed are listed in requirements.txt and can be installed with ```pip3 install -r requirements.txt```.
PostgreSQL need SSL enabled.

### Discord Token
The bot needs a unique Token per installation. This one can be obtained at http://discord.com/developers.
Create a "New Application", add a Bot, select Bot from the left menu, give it a nice name and icon, press "Copy" below "Click to Reveal Token".
Now your Token is in your clipboard. Paste it in dcsserverbot.ini in your config-directory.
Both "Priviledged Gateway Intents" have to be enabled on that page.
To add the bot to your Discord guild, go to OAuth2, select "bot" in the OAuth2 URL Generator, select the following permissions:
_Send Messages, Manage Messages, Embed Links, Attach Files, Read Message History, Add Reactions_
Press "Copy" on the generated URL, paste it into the browser of your choice, select the guild the bot has to be added to - and you're done!
For easier access to channel IDs, enable "Developer Mode" in "Advanced Settings" in Discord.

### Bot Configuration
The bot configuration is held in config/dcsserverbot.ini. The following parameters can be used to configure the bot:
Parameter|Description
-----------|--------------
OWNER|The Discord ID of the Bot's owner (that's you!). If you don't know your ID, go to your Discord profile, make sure "Developer Mode" is enabled under "Advanced", go to "My Account", press the "..." besides your profile picture and select "Copy ID"
TOKEN|The token to be used to run the bot. Can be obtained at http://discord.com/developers.
DATABASE_URL|URL to the PostgreSQL database used to store our data.
COMMAND_PREFIX|The prefix to be used. Default is '.'
HOST|IP the bot listens on for messages from DCS. Default is 127.0.0.1, to only accept internal communication on that machine.
PORT|UDP port, the bot listens on for messages from DCS. Default is 10081. **__Don't expose that port to the outside world!__**
MASTER|If true, start the bot in master-mode which enables specific commands that are only allowed **once** on your server. If only one bot is running, then there is only a master.\nIf you have to use more than one bot, for multiple DCS servers that are spanned over several locations, you have to install one agent at every location. All DCS servers of that specific location will then automatically register with that specific bot and can only be controlled by that bot.
AUTOBAN|If true, members leaving the discord will be automatically banned.
SERVER_FILTER|Filter to shorten server names (if needed)
MISSION_FILTER|Filter to shorten mission names (if needed)
USER_LOGIN|Your login to ED's website. Needed to gather status information of the running server(s).
USER_PASSWORD|Your password to ED's website. Needed to gather status information of the running server(s).
DCS_HOME|The main configuration directory of your DCS server installation (for Hook installation). Keep it empty, if you like to place the Hook by yourself.
GREETING_MESSAGE_MEMBERS|A greeting message, that people will receive in DCS, if they get recognized by the bot as a member of your discord.
GREETING_MESSAGE_UNKNOWN|A greeting message, that people will receive in DCS, if they or not recognized as a member of your discord.

### DCS/Hook Configuration
The DCS World integration is done via a Hook. This is being installed automatically.
You need to configure the Hook upfront in Scripts/net/DCSServerBot/DCSServerBotConfig.lua
Parameter|Description
-----------|--------------
..BOT_HOST|Must be the same as HOST in the Bot configuration.
..BOT_PORT|Must be the same as PORT in the Bot configuration (default 10081).
..DCS_HOST|The IP of the machine, DCS is running onto. If you are an agent to a master in the same network but not on your machine, this has to be the internal IP of the DCS server.
..DCS_PORT|Must be a unique value > 1024 of a port that is not in use on your system. Must be unique for every DCS server instance configured. **__Don't expose that port to the outside world!__**
..CHAT_CHANNEL|The ID of the in-game chat channel to be used for the specific DCS server. Must be unique for every DCS server instance configured.
..STATUS_CHANNEL|The ID of the status-display channel to be used for the specific DCS server. Must be unique for every DCS server instance configured.
..ADMIN_CHANNEL|The ID of the admin-commands channel to be used for the specific DCS server. Must be unique for every DCS server instance configured.

### Discord Configuration
The following roles have to be set up in your Discord guild:
Role|Description
-------|-----------
DCS|People with this role are allowed to chat, check their statistics and gather information about running missions and players.
DCS Admin|People with this role are allowed to restart missions and managing the mission list.
Admin / Moderator|People with that role are allowed to ban/unban people and to link discord-IDs to ucids, when the autodetection didn't work

### **ATTENTION**
_One of the main concepts of this bot it to bind people to your discord. Therefor, this solution is not very suitable for public servers._

The bot automatically bans / unbans people from the configured DCS servers, as soon as they leave / join the configured Discord guild.
If you don't like that feature, set AUTOBAN = false in dcsserverbot.ini.

## TODO
Things to be added in the future:
* Database versioning / update handling
* More statistics
* Ability to combine stats from different bots (if multiple servers are being run in different locations)

## Credits
Thanks to the developers of the awesome solutions [HypeMan](https://github.com/robscallsign/HypeMan) and [perun](https://github.com/szporwolik/perun), that gave me the main ideas to this solution.
I gave my best to mark parts in the code to show where I copied some ideas or even code from you guys. Hope that is ok.
Both frameworks are much more comprehensive than what I did here, so you better check those out before you look at my simple solution.
