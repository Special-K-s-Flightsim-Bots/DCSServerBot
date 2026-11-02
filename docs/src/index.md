# Welcome to DCSServerBot

**Manage your DCS World servers from Discord — or entirely without it.**

DCSServerBot runs your missions, watches your servers and keeps the statistics, so you do not have to
sit in front of the machine. One server on one PC, or a cluster spread over several nodes: the same
bot, the same commands.

![The website dashboard with two servers](assets/images/dashboard.png){ loading=lazy }

## What it does

- **Runs your servers** — mission rotation, restarts, shutdowns, gated on players being online
- **Watches them** — availability, performance and traffic-light status per server
- **Keeps the record** — per-player and per-server statistics, logbook, credits, awards
- **Admins the players** — bans, punishment, slot blocking, in-game chat commands
- **Reaches out** — SRS, LotAtc, Tacview, DCS Olympus, MOOSE and your own scripts

## Two ways to drive it

**Discord** — slash commands, embeds, buttons and menus in your server. The default.

A status embed per server carries what players ask for: the running mission, the map, the players,
the password and the time flown. The buttons next to it start, stop and restart the server.

![A server's status embed in Discord](assets/images/discord-status.webp){ loading=lazy }

`/help` lists everything the asking user's roles allow, one page per plugin:

![The output of /help](assets/images/help-command.webp){ loading=lazy }

**The website** — served by the bot itself, with a login of its own, so an installation can be
operated by someone who has no Discord account at all.

[What the website can do](website.md){ .md-button }

## Install in five steps

1. Install [Python 3.11+](https://www.python.org/downloads/) and
   [PostgreSQL](https://www.postgresql.org/download/).
2. Get the code: `git clone https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot.git`
3. Create a bot at <https://discord.com/developers> and copy its token.
4. Run `run.cmd` (Windows) or `./run.sh` (Linux) — a setup tour opens in your browser and writes the
   configuration for you.
5. Point it at your missions and start it.

[Full installation](install.md){ .md-button .md-button--primary }
[Configuration](configure.md){ .md-button }

## Where to go next

| You want to … | Read |
|:--------------|:-----|
| Get it running | [Installation](install.md) |
| Change a setting | [Configuration](configure.md) |
| Know what a command does | [Command reference](reference/commands.md) |
| Give people roles and permissions | [Roles and permissions](guides/roles.md) |
| Link players to Discord | [Player linking](guides/player-linking.md) |
| Send messages from a mission | [Mission scripting](guides/mission-scripting.md) |
| Run several servers or nodes | [Several servers](guides/multiple-servers.md) / [Multi-node](multinode.md) |
| Add a feature | [Plugins](plugins/index.md) / [Extensions](extensions/index.md) |
| Build your own component | [Developing](developers/overview.md) |
| Report a problem | [Support](https://discord.gg/h2zGDH9szZ) |

## Support

Join the [support Discord](https://discord.gg/h2zGDH9szZ) to ask questions, report bugs or talk to
other admins. If you enjoy what this does, you can support it on
[Patreon](https://www.patreon.com/DCS_SpecialK) or
[buy me a coffee](https://buymeacoffee.com/special_k).

## Credits

Thanks to the developers of [HypeMan](https://github.com/robscallsign/HypeMan) and
[perun](https://github.com/szporwolik/perun), who gave me the main ideas for this solution — the few
places where I borrowed ideas or code are marked in the source. Thanks also to Moose for aligning the
[FunkMan](https://github.com/funkyfranky/FunkMan) API with me and making it compatible in the first
place.
