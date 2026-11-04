# Components at a glance

DCSServerBot is a cluster of **nodes**, each controlling one or more DCS **instances**, each hosting
a configured **server**. On top of that sit three kinds of component: services, plugins and
extensions. Read [Architecture](../architecture.md) for how they fit together, and the
[Plugins](../plugins/index.md), [Services](../services/index.md) and [Extensions](../extensions/index.md)
overviews for the page of each one.

## Services

A service runs on every node of the cluster. Some are master-bound, like the bot itself.

| Service | Scope | Plugin | Documentation |
|:--------|:------|:-------|:--------------|
| Backup | Backups for bot and DCS config, missions, databases | Backup | [Backup service](../services/backup.md) |
| Bot | Discord bot, with an optional Discord-free variant | — | [Bot service](../services/bot.md) |
| Cleanup | Clears files from disk and channels in Discord | — | [Cleanup service](../services/cleanup.md) |
| Cron | Task scheduling based on a cron-like config | — | [Cron service](../services/cron.md) |
| Dashboard | Console graphics for bot and server status | — | [Dashboard service](../services/dashboard.md) |
| Firewall | Firewall rules and DDoS detection | Firewall | [Firewall service](../services/firewall.md) |
| ModManager | Mod installations and updates for DCS servers | ModManager | [ModManager service](../services/modmanager.md) |
| Monitoring | Availability and performance monitoring | Monitoring | [Monitoring service](../services/monitoring.md) |
| Music | Plays music over SRS radios | Music | [Music service](../services/music.md) |
| ServiceBus | Communication between cluster nodes and DCS servers | — | [ServiceBus service](../services/servicebus.md) |
| WebService | Webserver for the REST API and the website | — | [WebService service](../services/webservice.md) |

## Plugins

A plugin provides Discord and in-game commands, handles DCS events and runs on the master node only.
`no` means the plugin is always loaded, `default` that the bot loads it unless you define your own
`plugins` list in `config/main.yaml`, and `yes` that it starts only when you list it in `opt_plugins`
or configure it.

| Plugin | Scope | Optional | Depends on |
|:-------|:------|:---------|:-----------|
| Admin | DCS server administration commands | default | — |
| Commands | Custom Discord commands | yes | — |
| Help | Interactive help for Discord and in-game chat | default | — |
| MOTD | Automated messages to players | yes | Mission, MissionStats |
| Discord | Discord helper commands | yes | — |
| Punishment | Punish system for team-kills and more | yes | Mission |
| SlotBlocking | Slot blocking based on roles or credits | yes | Mission, CreditSystem |
| Voting | Voting system to change missions, weather, … | yes | — |
| Firewall | Frontend to the Firewall service | yes | Userstats |
| CreditSystem | Credits based on achievements | default | Mission |
| UserStats | User statistics system | default | Mission |
| MissionStats | Detailed player and mission statistics | default | Userstats |
| Logbook | Pilot logbook with squadrons and qualifications | yes | Userstats |
| Competitive | PvP support with TrueSkill ranking | yes | Mission |
| Tournament | Tournament system | yes | GameMaster, MissionStats, Competitive |
| GreenieBoard | Greenieboard and LSO quality analysis | yes | Missionstats |
| Charity | Shows donations of your charity campaigns | yes | — |
| Mission | Handling of missions | no | GameMaster |
| Scheduler | Auto start and stop of servers or missions | default | Mission |
| GameMaster | Interaction with missions through messages and flags | no | — |
| Battleground | [DCS Battleground](https://github.com/Frigondin/DCSBattleground) support | yes | — |
| Battleground2 | [DCS Battleground](https://github.com/Frigondin/DCSBattleground), new version | yes | — |
| Pretense | Commands for Pretense missions | yes | — |
| FlightPlan | IFR-style flight planning with F10 map visualization | yes | — |
| Logistics | Cargo delivery missions, F10 markers | yes | MissionStats |
| Backup | Database, server or bot configuration backup | yes | — |
| Monitoring | Server monitoring and statistics | default | Userstats |
| ModManager | DCS mod installation and update | yes | — |
| Cloud | Cloud statistics and DGSA ban list integration | default | Userstats |
| RestAPI | REST API to query and control the bot | yes | Userstats, MissionStats |
| Profiler | LUA profilers for DCS | yes | — |
| DBExporter | Export the database to CSV | yes | — |
| Debug | Debug logging for hook and mission-scripting events | yes | — |
| SRS | Player radio display, slot blocking | yes | MissionStats |
| LotAtc | LotAtc support with transponder file uploading | yes | — |
| Tacview | Manual recordings for Tacview | yes | — |
| DKS | Link to [Digital Kneeboard Simulator](https://www.digitalkneeboardsimulator.com/) | yes | RestAPI |
| SkyEye | [SkyEye](https://github.com/dharmab/skyeye) support | yes | — |
| FunkMan | [FunkMan](https://github.com/funkyfranky/FunkMan) support | yes | — |
| RealWeather | Real weather in your missions | yes | — |
| Music | SRS music playback | yes | — |

## Extensions

An extension runs for each supported server, on the node that server runs on, with access to all of
that server's resources.

| Extension | Scope |
|:----------|:------|
| Cloud | Cloud registration and statistics uploads |
| DKS | [Digital Kneeboard Simulator](https://www.digitalkneeboardsimulator.com/) support |
| DSMC | Support for DSMC |
| gRPC | DCS-gRPC support |
| GitHub | Load and update missions or scripts from GitHub |
| HoundTTS | Hound text-to-speech support |
| Lardoon | Web interface for Tacview files |
| LogAnalyser | Checks `dcs.log` for errors |
| LotAtc | GCI and ATC extension for DCS World |
| MizEdit | Change *anything* in your missions on load |
| ModManager | Display mods in your mission |
| Olympus | Real-time control of missions through a web interface |
| Persistence | Simple date and time persistence for missions |
| Pretense | Dynamic campaign framework by Dzsek |
| RealWeather | Real weather in your missions |
| SkyEye | AI-powered GCI bot for DCS |
| Sneaker | Moving map interface |
| SRS | DCS-SRS support |
| Tacview | Automatic Tacview upload |
| Trackfile | Automatic trackfile upload |
| VoiceChat | DCS VOIP support |

!!! note
    Many of these extensions rely on tools built by other community members, who support them in
    their own Discords and repositories. Problems with the tool itself belong there, not in
    DCSServerBot's support channel.

If you use the [ModManager service](../services/modmanager.md), some extensions (DSMC, gRPC,
HoundTTS, Olympus) can be installed automatically — try `/enable extension` first.

## Third-party plugins

A community plugin usually arrives as a ZIP. Drop it into the `plugins` directory and restart the bot:
it unpacks on start. Many need a configuration file of their own — see the plugin's documentation.
