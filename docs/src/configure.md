# Configuration

The bot reads everything from YAML files in its `config` directory. A fresh installation already has
working files — this page is for knowing which file owns which switch, and what a change costs.

## The files

| File | Holds | Reference |
|:-----|:------|:----------|
| `config/main.yaml` | guild, database, logging, filters, enabled and optional plugins | [Main reference](reference/config/main.md) |
| `config/nodes.yaml` | one block per node: DCS installation, instances, ports, extensions | [Nodes reference](reference/config/nodes.md) |
| `config/servers.yaml` | one block per server: channels, mission list, settings, `DEFAULT` | [Servers reference](reference/config/servers.md) |
| `config/presets.yaml` | mission presets used by the modify commands | [Presets reference](reference/config/presets.md) |
| `config/services/<service>.yaml` | one file per service: the bot token and roles, backup schedules, the website, … | [Services](services/index.md) |
| `config/plugins/<plugin>.yaml` | one file per plugin, including its command overrides | [Plugins](plugins/index.md) |
| `config/.secret/*.pkl` | the token and every password the bot stores for you | [Secrets](secrets.md) |

Every component page on this site ends with the configuration reference generated from that
component's own schema, so the plugin page is the place to look for a plugin's keys.

A service or plugin starts **only** when its configuration file exists. Deleting the file is how you
switch it off.

Start from the commented samples in `samples/` — `samples/main.yaml`, `samples/servers.yaml`,
`samples/plugins/mission.yaml` and so on — they carry every key with a working value.

## Where a value comes from

`servers.yaml` is one block per server, and `DEFAULT` holds what every server inherits — the in-game
messages for instance. A key in a server's own block wins over `DEFAULT`. A key no block states
falls back to the bot's built-in default.

Why split `nodes.yaml` and `servers.yaml` at all? Because a server is not its process: moving a server
to another instance or another machine is a configuration change, not a migration.

## What a change costs

Saving from the website tells you which of these applies, and so does the bot's own loader:

| You changed | What happens |
|:------------|:-------------|
| a plugin configuration | that plugin reloads in place, nothing restarts |
| `main.yaml`, `nodes.yaml`, `servers.yaml`, a service configuration | the bot restarts on the affected node |
| a preset, a mission list, anything read when a mission is prepared | applied by the next launch |

From Discord, `/reload <plugin>` reloads a single plugin.
