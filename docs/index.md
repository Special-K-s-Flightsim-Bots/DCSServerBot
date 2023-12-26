---
title: Overview
nav_order: 1
layout: home
---

# Please refrain from this documentation for now unless you are using the v2 version of the bot.
I need to rework it, the v3 documentation is currently in GitHUb only. Please look at the README file in my
GitHub repo for now, thanks!

---

DCSServerBot is a tool written in [Python] to connect your [DCS] dedicated server with your [Discord] server.
This connection is bidirectional. The bot is able to get information from the DCS server (like the running mission, connected players etc.) and displays it on Discord as server status embeds.
You are able to manage the DCS server with Discord bot commands to start and stop the server, change the password or the running mission and so on.

The tool has a plugin system, so it is easy to disable unwanted features. For developers, it is easier to add new functionality in the future.
Developers are able to contribute to this project by adding their own plugins by using the well known GitHub pull-request workflow.

You can control which user is allowed to use a specific category of commands by assigning discord roles, which enables you to use RBAC (Role Based Access Control).

A scheduler is able to start, shutdown or restart your DCS server to specific date and/or times, or to rotate your missions.
Support for additional tools like [SRS], [TacView], [LotATC], [OvGME] etc. is included.

Included is a kick and ban system, with optional support for auto-kick and auto-ban for team kills and friendly fire.
It can be combined with a credit system to earn credits for destroying targets or fulfill mission goals and a slot blocking system to reserve some slots for people with a specific amount of earned credits.

A statistic system supports per-user, per-server and per-mission statistics and can be connected to a cloud-based statistics, which is shared by some of the most popular DCS online servers.
To save this data the bot uses a [PostgreSQL] database, which must be installed and maintained.
If you don't need statistics and search a smaller and easier to install solution, you might want to look at my [DCSServerBotLight], which can do some of the stuff you see in here already, without the need of any PostgreSQL-database.

GameMasters can control a running mission via bot commands by changing mission flags or variables, trigger scripts and send messages.
They are also able to create and run campaigns.

You can enable a [coalitions] feature to support Blue and Red coalitions in your Discord and your PvP DCS server(s).
This changes the way how the bot shows information to avoid espionage of the enemy coalition.

It is possible to integrate the popular [FunkMan] Features GreenieBoard, Trapsheet and TargetRange.

This documentation will show you the main features, how to install and configure the bot and some more sophisticated stuff, e.g. if you run multiple servers, maybe even spread over multiple locations.

# Feature list
- Show status information of your server(s) in Discord
- Manage your DCS server with Discord bot commands
- Use Roles to define who is allowed to use a specific category of commands
- Modular architecture with plugins
- Per-user, per-mission and per-server statistic system
- Optional connect to a cloud-based statistic system, shared with other DCS community servers
- Scheduler to start, shutdown, restart the server and/or execute mission rotation
- Support for [SRS], [TacView], [LotATC], [OvGME] etc.
- Kick and ban people from DCS server and Discord
- Enable auto-kick and auto-ban on your DCS server for team killing or friendly fire
- Add a credit system to give players credits for killing targets or fulfill mission goals
- Activate a slot blocking system, which can be combined with the credit system
- Integrate [FunkMan]s GreenieBoard, Trapsheet and TargetRange
- Auto update of the Bot (needs [Git])
- GameMaster commands to change mission flags or variables, trigger scripts and send messages
- Create, run and delete campaigns
- Activate a coalition system to separate players and status information based on coalition for PvP servers
- Add custom commands to run scripts or executables on your Windows server

[Python]: https://www.python.org/
[DCS]: https://www.digitalcombatsimulator.com
[Discord]: https://discord.com/
[PostgreSQL]: https://www.postgresql.org/
[DCSServerBotLight]: https://github.com/Special-K-s-Flightsim-Bots/DCSServerBotLight
[SRS]: http://dcssimpleradio.com/
[TacView]: https://www.tacview.net/
[LotATC]: https://www.lotatc.com/
[OvGME]: https://github.com/mguegan/ovgme
[FunkMan]: https://github.com/funkyfranky/FunkMan
[Git]: https://git-scm.com/download/win
[coalitions]: configuration/coalitions.md
