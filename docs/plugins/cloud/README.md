---
layout: default
title: README
nav_section: plugins/cloud
---

# Plugin "Cloud"
#### _With this plugin, the world of DCSServerBot gets even bigger!_
When using DCSServerBot in your Discord and with your DCS servers, people gather lots of statistics. As people often 
not only fly in one community, they might want to see their statistics that they gathered in all communities in a 
single place.</br>
That said, DCSServerBot offers the opportunity to use a cloud based database system, to upload aggregated statistics
for every active user into the cloud. Users then can use Discord commands to see their overall stats, like they are
used to see with `/statistics`.

> [!NOTE]
> The Cloud statistics are meant for larger servers only. I am happy to provide you access to it, using a token, provided 
> by me. The service comes free of charge but without any liabilities or guarantees, you can use it or leave it and so 
> can I revoke any token at any time.

## Global Ban System
If you opt in to the cloud plugin and even have not opted in to the cloud statistics, you can still use the global ban
system. We've put together a group consisting of the admins of the most popular DCS servers, and we monitor what's going
on in the community. When we see someone that is crashing servers by hacking or any other **really** (really) bad stuff,
we put them in the global ban list. Nobody that gets usually banned on a server for misbehavior will get onto the list.
There are only the real bad guys on it.</br>
If you opt in to that plugin, you already participate from that ban list. You can choose whether to ban DCS players 
and/or Discord users. Both are active as a default.</br>
If you are a server admin of a large server and not part of DGSA, the "DCS Global Server Admins" yet, send me a DM.

## Configuration
```yaml
DEFAULT:
  banlist: pvp                          # One of pvp, pve or both. For DCS bans / watchlist only (default: both).
  dcs-ban: true                         # true: subscribe to the global ban service for DCS users (default: false).
  discord-ban: true                     # true: subscribe to the global ban service for Discord users (default: false).
  watchlist_only: true                  # true: a player being on the global banlist will be added to the watchlist only (default: false, does not work with dcs-ban: true)
  host: dcsserverbot-prod.herokuapp.com # Don't change that until told otherwise.
  port: 443                             # Don't change that until told otherwise.
  protocol: https                       # Don't change that until told otherwise.
  register: true                        # True, send general statistics to my community stats (please do that!)
  upload_errors: true                   # True, upload exceptions to the central error database, so that I can see what happened in your bot (and fix it)
#  token: xxxyyyzzz111222333444         # If you got a TOKEN to participate in the cloud statistics, then put it in here.
```
The online registration helps me to better understand which installations are out there. There is no personal
information sent to the cloud, and you can always see what is being sent (logs/dcssb-*.log) and disable it, if you feel
uncomfortable with it. I would appreciate, if you send me that little bit of data, as it helps me (and you) in
maintaining the solutions that are out in the wild.

## Discord Commands
| Command           | Parameter        | Role      | Description                                          |
|-------------------|------------------|-----------|------------------------------------------------------|
| /cloud status     |                  | Admin     | Status of the connection to the cloud service.       |
| /cloud resync     | [@member / ucid] | DCS Admin | Resync all players (or this player) with the cloud.  |
| /cloud statistics | [@member / ucid] | DCS       | Display player cloud statistics (overall, per guild) |
| /serverlist       | name             | DCS       | Display the DCS server list.                         |
