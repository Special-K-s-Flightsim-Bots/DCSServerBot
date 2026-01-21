# Logbook Plugin

A comprehensive pilot logbook and squadron management plugin for DCSServerBot. Provides military-style record keeping
for virtual squadrons including flight statistics, qualifications, awards, and flight plans.

## Features

- **Pilot Information**: Unified pilot view showing service, rank, squadrons, hours, qualifications, and awards
- **Pilot Management**: Track pilot service branch (RN, RAF, AAC) and rank separately from squadron assignments
- **Squadron Management**: Create squadrons with service affiliation, CO/XO hierarchy, and member assignments
- **Multi-Squadron Support**: Pilots can be assigned to multiple squadrons across different services
- **Qualifications**: Define qualifications with optional expiration, auto-grant based on requirements
- **Awards**: Create awards with custom ribbon colors, generate ribbon rack images
- **Flight Plans**: File, track, and manage flight plans with status workflow
- **Historical Hours**: Import and preserve flight hours from legacy systems

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

| Command                                      | Description                                        | Role      |
|----------------------------------------------|----------------------------------------------------|-----------|
| `/logbook stats [user]`                      | Show pilot flight statistics                       | DCS       |
| `/logbook pilot [user]`                      | Show unified pilot info (service, rank, squadrons, qualifications, awards, ribbon rack) | DCS |
| `/logbook setpilot <user> <service> <rank>`  | Set or update pilot service and rank               | DCS Admin |

### Squadron Commands (`/logbook squadron`)

| Command                                                                 | Description                     | Role      |
|-------------------------------------------------------------------------|---------------------------------|-----------|
| `/logbook squadron list`                                                | List all squadrons              | DCS       |
| `/logbook squadron info <squadron>`                                     | Show squadron details           | DCS       |
| `/logbook squadron roster <squadron>`                                   | Show squadron roster with stats | DCS       |
| `/logbook squadron create <name> <service> [abbreviation] [description]`| Create a new squadron           | DCS Admin |
| `/logbook squadron delete <squadron>`                                   | Delete a squadron               | DCS Admin |
| `/logbook squadron assign <squadron> <user> [position]`                 | Assign pilot to squadron        | DCS Admin |
| `/logbook squadron remove <squadron> <member>`                          | Remove pilot from squadron      | DCS Admin |
| `/logbook squadron setco <squadron> <member>`                           | Set Commanding Officer          | DCS Admin |
| `/logbook squadron setxo <squadron> <member>`                           | Set Executive Officer           | DCS Admin |

> **Note:** Pilots can be assigned to multiple squadrons. Service and rank are properties of the pilot (set via `/logbook setpilot`), not the squadron assignment.

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

> **Note:** Flight plan commands have been moved to the separate `flightplan` plugin with enhanced IFR-style features, F10 map visualization, and OpenAIP integration. Stores/logistics commands are in the `logistics` plugin.

## Auto-Grant Qualifications

Qualifications can be automatically granted when pilots meet specified requirements. Define requirements as JSON when creating a qualification:

```
/qualification create name:"Carrier Qualified" valid_days:90
```

Then set requirements in the database `logbook_qualifications.requirements` column:
```json
{"flight_hours": 50, "carrier_landings": 10}
```

Supported requirement keys:
- `flight_hours` - Total flight hours
- `total_kills` - Total kills
- `deaths` - Total deaths (use `deaths_max` for maximum)
- `takeoffs` - Total takeoffs
- `landings` - Total landings
- `carrier_landings` - Carrier landings (requires greenieboard plugin)

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
- `logbook_pilots` - Pilot service branch and rank
- `logbook_squadrons` - Squadron definitions with service affiliation
- `logbook_squadron_members` - Pilot-squadron assignments (many-to-many)
- `logbook_qualifications` - Qualification definitions
- `logbook_pilot_qualifications` - Granted qualifications
- `logbook_awards` - Award definitions with ribbon images
- `logbook_pilot_awards` - Granted awards
- `logbook_stores_requests` - Squadron stores/equipment requests
- `logbook_historical_hours` - Imported historical flight time

And one view:
- `pilot_logbook_stats` - Aggregated pilot statistics

> **Note:** Flight plans are managed by the separate `flightplan` plugin. See the flightplan plugin README for details.

## Data Model

```
Player (from DCSServerBot core)
  └── logbook_pilots (1:1) - service, rank
  └── logbook_squadron_members (1:many) - squadron assignments
  └── logbook_pilot_qualifications (1:many)
  └── logbook_pilot_awards (1:many)
  └── logbook_historical_hours (1:many)

Squadron
  └── service (RN, RAF, AAC, etc.)
  └── members (many pilots)
  └── CO/XO references
```
