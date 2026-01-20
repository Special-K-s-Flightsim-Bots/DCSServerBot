# Logbook Plugin

A comprehensive pilot logbook and squadron management plugin for DCSServerBot. Provides military-style record keeping
for virtual squadrons including flight statistics, qualifications, awards, and squadron management.

## Features

- **Pilot Statistics**: View flight hours, kills, deaths, takeoffs, landings from existing DCSServerBot data
- **Squadron Management**: Create squadrons with CO/XO hierarchy, assign members with ranks and positions
- **Qualifications**: Define qualifications with optional expiration, auto-grant based on requirements
- **Awards**: Create awards with custom ribbon colors, generate ribbon rack images, auto-grant based on requirements

> **Note:** Flight plan functionality has been moved to the dedicated `flightplan` plugin.

## Requirements

- **userstats plugin** must be enabled (provides the `statistics` table used for pilot stats)
- **greenieboard plugin** (optional) - enables carrier landing counts for auto-grant qualifications

## Installation

1. Add `logbook` to `opt_plugins` in your `config/main.yaml`:
   ```yaml
   opt_plugins:
     - logbook
   ```

2. Restart DCSServerBot - the database tables will be created automatically

3. (Optional) Configure the plugin in `config/plugins/logbook.yaml`

## Configuration

```yaml
# config/plugins/logbook.yaml
DEFAULT:
  auto_qualifications: true  # Enable auto-grant qualifications on requirements met
```

## Commands

### Logbook Commands (`/logbook`)

| Command                 | Description                  | Role   |
|-------------------------|------------------------------|--------|
| `/logbook stats [user]` | Show pilot flight statistics | DCS    |

### Squadron Commands (`/logbook squadron`)

| Command                                                        | Description                     | Role      |
|----------------------------------------------------------------|---------------------------------|-----------|
| `/logbook squadron list`                                       | List all squadrons              | DCS       |
| `/logbook squadron info <squadron>`                            | Show squadron details           | DCS       |
| `/logbook squadron roster <squadron>`                          | Show squadron roster with stats | DCS       |
| `/logbook squadron create <name> [abbreviation] [description]` | Create a new squadron           | DCS Admin |
| `/logbook squadron delete <squadron>`                          | Delete a squadron               | DCS Admin |
| `/logbook squadron assign <squadron> <user> [rank] [position]` | Assign pilot to squadron        | DCS Admin |
| `/logbook squadron remove <squadron> <member>`                 | Remove pilot from squadron      | DCS Admin |
| `/logbook squadron promote <squadron> <member> <rank>`         | Update a member's rank          | DCS Admin |
| `/logbook squadron setco <squadron> <member>`                  | Set Commanding Officer          | DCS Admin |
| `/logbook squadron setxo <squadron> <member>`                  | Set Executive Officer           | DCS Admin |

#### CO/XO Self-Administration

Squadron Commanding Officers (CO) and Executive Officers (XO) can manage their own squadrons without requiring the DCS Admin role. This includes:
- Assigning, removing, and promoting members
- Setting positions within the squadron

The CO/XO permissions are automatically detected based on the squadron's `co_ucid` and `xo_ucid` fields.

### Qualification Commands (`/qualification`)

| Command                                                                   | Description                                   | Role      |
|---------------------------------------------------------------------------|-----------------------------------------------|-----------|
| `/qualification list [user]`                                              | List qualifications or pilot's qualifications | DCS       |
| `/qualification info <qualification>`                                     | Show qualification details                    | DCS       |
| `/qualification create <name> [description] [aircraft_type] [valid_days]` | Create qualification                          | DCS Admin |
| `/qualification delete <qualification>`                                   | Delete qualification                          | DCS Admin |
| `/qualification grant <user> <qualification>`                             | Grant qualification to pilot                  | DCS Admin |
| `/qualification revoke <user> <qualification>`                            | Revoke qualification from pilot               | DCS Admin |
| `/qualification refresh <user> <qualification>`                           | Refresh expiration date                       | DCS Admin |
| `/qualification expiring [days]`                                          | List qualifications expiring soon             | DCS Admin |

### Award Commands (`/award`)

| Command                                                          | Description                   | Role      |
|------------------------------------------------------------------|-------------------------------|-----------|
| `/award list [user]`                                             | List awards or pilot's awards | DCS       |
| `/award info <award>`                                            | Show award details            | DCS       |
| `/award ribbon [user]`                                           | Generate ribbon rack image    | DCS       |
| `/award create <name> [description] [ribbon_colors] [image_url]` | Create award                  | DCS Admin |
| `/award delete <award>`                                          | Delete award                  | DCS Admin |
| `/award grant <user> <award> [citation]`                         | Grant award to pilot          | DCS Admin |
| `/award revoke <user> <award>`                                   | Revoke award from pilot       | DCS Admin |

## Auto-Grant Qualifications

Qualifications can be automatically granted when pilots meet specified requirements. Define requirements as JSON when creating a qualification:

```
/qualification create name:"Carrier Qualified" valid_days:90
```

Then set requirements in the database `logbook_qualifications.requirements` column:
```json
{"flight_hours": 50, "carrier_landings": 10}
```

### Supported Requirement Keys

| Key               | Description                                    |
|-------------------|------------------------------------------------|
| `flight_hours`    | Total flight hours                             |
| `total_kills`     | Total kills                                    |
| `pvp_kills`       | PvP kills                                      |
| `deaths`          | Total deaths (use `deaths_max` for maximum)    |
| `ejections`       | Total ejections                                |
| `crashes`         | Total crashes                                  |
| `takeoffs`        | Total takeoffs                                 |
| `landings`        | Total landings                                 |
| `carrier_landings`| Carrier landings (requires greenieboard plugin)|

### Requirement Modifiers

Use the `_min` or `_max` suffix to specify minimum or maximum constraints:
- `flight_hours_min: 50` - Requires at least 50 flight hours
- `deaths_max: 10` - Requires 10 or fewer deaths
- Without a suffix, the key defaults to a minimum requirement (e.g., `flight_hours: 50` is equivalent to `flight_hours_min: 50`)

### Example Requirements

```json
{
  "flight_hours_min": 100,
  "carrier_landings": 25,
  "deaths_max": 5,
  "ejections": 0
}
```

## Auto-Grant Awards

Awards also support automatic granting based on requirements. The database table `logbook_awards` includes:
- `auto_grant` (boolean) - Set to `true` to enable automatic granting
- `requirements` (JSONB) - Same format as qualification requirements

Example award setup in the database:
```sql
UPDATE logbook_awards
SET auto_grant = true,
    requirements = '{"flight_hours": 500, "total_kills": 100}'
WHERE name = 'Distinguished Flying Cross';
```

## In-Game Notifications

When a player connects and the `auto_qualifications` feature grants them a new qualification, they receive an in-game chat message:

> "Congratulations! You've earned qualification(s): Carrier Qualified, Night Vision Certified"

This notification is sent via `player.sendChatMessage()` immediately after the qualifications are granted.

## Ribbon Generation

Awards can have custom ribbon colors defined as a JSON array of hex colors:
```
/award create name:"Distinguished Flying Cross" ribbon_colors:'["#0000FF", "#FFFFFF", "#FF0000"]'
```

Use `/award ribbon` to generate a ribbon rack image showing all of a pilot's awards.

**Note**: Ribbon generation requires PIL (Pillow), numpy, and matplotlib libraries.

## Migration from dcs_server_logbook

A migration script is included for importing data from the Joint Strike Wing's dcs_server_logbook:

```bash
python plugins/logbook/scripts/migrate_from_dcs_server_logbook.py \
  --sqlite-path /path/to/mayfly.db \
  --slmod-path /path/to/SlmodStats.lua \
  --postgres-url "postgres://user:pass@host:5432/db"
```

Options:
- `--dry-run` - Preview migration without making changes
- `--verbose` - Show detailed progress

The migration preserves all historical flight hours using a `GREATEST()` function in the stats view, ensuring pilots never see fewer hours after migration.

## Database Schema

The plugin creates the following tables:
- `logbook_squadrons` - Squadron definitions
- `logbook_squadron_members` - Pilot-squadron assignments
- `logbook_qualifications` - Qualification definitions
- `logbook_pilot_qualifications` - Granted qualifications
- `logbook_awards` - Award definitions (includes `auto_grant` and `requirements` fields)
- `logbook_pilot_awards` - Granted awards
- `logbook_flight_plans` - Filed flight plans (legacy, see `flightplan` plugin)
- `logbook_stores_requests` - Stores/logistics requests
- `logbook_historical_hours` - Imported historical flight time

And one view:
- `pilot_logbook_stats` - Aggregated pilot statistics
