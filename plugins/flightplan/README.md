# FlightPlan Plugin

An IFR-style flight planning system for DCSServerBot with Discord integration, F10 map visualization, and navigation fix support. File, activate, and track flight plans with waypoints, altitudes, and timing.

## Features

- **Flight Plan Filing**: Create detailed flight plans with departure, destination, waypoints, altitude, and ETD
- **Auto-Lifecycle**: Automatic activation on takeoff and completion on landing at destination
- **F10 Map Visualization**: Plot flight plans as markers on the F10 map (temporary or permanent)
- **Discord Integration**: Publish flight plan status to a dedicated channel with live updates
- **Navigation Fixes**: Support for VORs, NDBs, TACANs, and intersections with OpenAIP integration
- **User Waypoints**: Create custom named waypoints for reuse across flight plans
- **Multiple Input Formats**: Accept MGRS, DMS, decimal coordinates, airbase names, and navigation fixes
- **In-Game Commands**: Full chat command support for managing flight plans while flying
- **F10 Menu**: Dynamic menu for viewing, plotting, activating, and completing flight plans
- **Stale Plan Cleanup**: Automatic cancellation of expired flight plans

## Requirements

- DCSServerBot v3.6+
- PostgreSQL database (standard DCSServerBot setup)

## Installation

1. Add `flightplan` to `opt_plugins` in your `config/main.yaml`:
   ```yaml
   opt_plugins:
     - flightplan
   ```

2. Restart DCSServerBot - the database tables will be created automatically

3. (Optional) Configure the plugin in `config/plugins/flightplan.yaml`

4. (Optional) Seed navigation fixes:
   - Use `/flightplan fix seed` to load bundled navigation data
   - Or use `/flightplan fix sync <theater>` to fetch from OpenAIP (requires API key)

## Configuration

```yaml
# config/plugins/flightplan.yaml
DEFAULT:
  enabled: true
  status_channel: 123456789012345678  # Discord channel for flight plan updates
  publish_on_file: true               # Post to Discord when plan is filed
  publish_on_activate: true           # Update Discord when plan is activated
  stale_hours: 24                     # Cancel plans older than this (hours)
  auto_cancel_stale: true             # Auto-cancel stale plans on mission start
  marker_timeout: 30                  # Default F10 marker display time (seconds)

  # Auto-Lifecycle: Automatic activation on takeoff, completion on landing
  auto_lifecycle:
    activate_on_takeoff: true         # Auto-activate filed plan when player takes off
    complete_on_landing: true         # Auto-complete active plan when landing at destination
    require_departure_match: false    # If true, only activate if takeoff is at departure airbase
    proximity_threshold: 3000         # Fallback distance (meters) for destination matching

  openaip:                            # OpenAIP integration (optional)
    api_key: "your-api-key-here"      # Get from https://www.openaip.net/
    cache_hours: 168                  # Cache navigation data for 1 week
```

### Auto-Lifecycle Feature

The auto-lifecycle feature provides seamless flight plan management:

1. **Automatic Activation on Takeoff**: When a player takes off, their most recent filed flight plan is automatically activated. F10 markers are created and Discord is updated.

2. **Automatic Completion on Landing**: When a player lands at their destination airbase, the active flight plan is automatically completed. Markers are removed and Discord is updated.

3. **Destination Matching**: The system uses airbase name matching (handles variations like "Batumi" vs "Batumi-Chorokhi") and falls back to proximity checking if the airbase name isn't available in the event data.

To disable auto-lifecycle features (for manual-only operation):
```yaml
DEFAULT:
  auto_lifecycle:
    activate_on_takeoff: false
    complete_on_landing: false
```

## Discord Slash Commands

### Flight Plan Commands (`/flightplan`)

| Command | Description | Role |
|---------|-------------|------|
| `/flightplan file <server> <callsign> <aircraft> <departure> <destination> [options]` | File a new flight plan | DCS |
| `/flightplan view <plan>` | View flight plan details | DCS |
| `/flightplan list [status] [user]` | List flight plans with filters | DCS |
| `/flightplan activate <plan>` | Activate a filed flight plan | DCS |
| `/flightplan complete <plan>` | Mark flight plan as completed | DCS |
| `/flightplan cancel <plan>` | Cancel a flight plan | DCS |
| `/flightplan plot <plan> [duration]` | Plot flight plan on F10 map | DCS |
| `/flightplan publish <plan>` | Publish flight plan to Discord | DCS |
| `/flightplan stale [hours]` | Cancel stale flight plans | DCS Admin |

#### File Command Options

| Option | Description | Example |
|--------|-------------|---------|
| `server` | Target server (required) | `My Server` |
| `callsign` | Your callsign (required) | `Viper11` |
| `aircraft_type` | Aircraft type (required) | `F-16C` |
| `departure` | Departure airfield (required) | Autocompletes from mission |
| `destination` | Destination airfield (required) | Autocompletes from mission |
| `alternate` | Alternate airfield (optional) | Autocompletes from mission |
| `waypoints` | Comma-separated waypoints (optional) | `ADLER,38TLN1234,@MYPOINT` |
| `cruise_altitude` | Cruise altitude (optional) | `FL300` or `30000` |
| `cruise_speed` | Cruise speed in knots (optional) | `450` |
| `etd` | Estimated departure time UTC (optional) | `14:30` or `1430` |
| `remarks` | Additional remarks (optional) | `Training flight` |

### Waypoint Commands (`/flightplan waypoint`)

| Command | Description | Role |
|---------|-------------|------|
| `/flightplan waypoint add <name> <coordinates> <theater> [altitude] [description]` | Create a user waypoint | DCS |
| `/flightplan waypoint list [theater]` | List user waypoints | DCS |
| `/flightplan waypoint delete <name>` | Delete a waypoint | DCS |

### Navigation Fix Commands (`/flightplan fix`)

| Command | Description | Role |
|---------|-------------|------|
| `/flightplan fix list [theater]` | List navigation fixes | DCS |
| `/flightplan fix count` | Count fixes by theater | DCS |
| `/flightplan fix add <identifier> <lat> <lon> <type> <theater> [name] [freq]` | Add a navigation fix | DCS Admin |
| `/flightplan fix delete <identifier> <theater>` | Delete a navigation fix | DCS Admin |
| `/flightplan fix sync <theater>` | Sync fixes from OpenAIP | DCS Admin |
| `/flightplan fix seed` | Load bundled seed data | DCS Admin |

## In-Game Chat Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `-flightplan` | `-fp` | Show your active flight plan |
| `-plotfp [id]` | | Plot flight plan on F10 map for 30 seconds |
| `-fileplan <dep> <dest> [aircraft]` | | Quick file a flight plan |
| `-activatefp [id]` | | Activate your filed flight plan |
| `-completefp` | | Complete your active flight plan |
| `-cancelfp` | | Cancel your flight plan |

### Examples

```
-fp                           Show your current flight plan
-plotfp                       Plot your own plan on F10 map
-plotfp 5                     Plot flight plan #5 on F10 map
-fileplan Batumi Kutaisi      Quick file: Batumi to Kutaisi in current aircraft
-fileplan BATUMI SENAKI F-16C File with specific aircraft type
-activatefp                   Activate your most recent filed plan
-completefp                   Mark your flight as complete
-cancelfp                     Cancel your current plan
```

## F10 Menu Structure

```
Flight Plan/
+-- View Active Plans         (Show popup with all visible plans)
+-- My Flight Plan            (Show your current plan details)
+-- Plot All Plans (30s)      (Temporarily display all plans on map)
+-- Plot Plan/                (Submenu to plot specific plans)
|   +-- #1: Viper11
|   +-- #2: Hawg21
|   +-- ...
+-- Activate Plan/            (Submenu - only if you have filed plans)
|   +-- #3: Batumi -> Kutaisi
|   +-- ...
+-- Complete Flight           (Only if you have active plan)
+-- Cancel Flight             (Only if you have active/filed plan)
```

## Waypoint/Coordinate Input Formats

The plugin accepts multiple coordinate formats for waypoints:

### MGRS Coordinates
Military Grid Reference System coordinates.
```
38TLN1234567890    # Full 10-digit precision
38TLN12345678      # 8-digit precision
38T LN 12345 67890 # With spaces (will be normalized)
```

### DMS (Degrees Minutes Seconds)
```
N41 30'00" E044 15'00"    # With symbols
N413000 E0441500          # Compact format
N 41 30 00 E 044 15 00    # With spaces
```

### Decimal Degrees
```
41.5, 44.25               # Latitude, Longitude
41.5 44.25                # Space separator also works
```

### User-Defined Waypoints
Reference custom waypoints with the `@` prefix.
```
@PANTHER                  # Looks up "PANTHER" in waypoints table
@IP_NORTH                 # Custom ingress point
@REFUEL1                  # Tanker holding point
```

### Airbase Names
Airbase names from the current mission are resolved automatically.
```
Batumi                    # Exact match
Senaki                    # Partial match finds "Senaki-Kolkhi"
Stennis                   # Carrier names work too
```

### Navigation Fixes
VORs, NDBs, TACANs, and intersection identifiers.
```
ADLER                     # VOR identifier
TSK                       # NDB identifier
PANTH                     # Intersection
```

## Altitude Formats

| Format | Example | Result |
|--------|---------|--------|
| Flight Level | `FL300` | 30,000 ft |
| Flight Level with space | `FL 300` | 30,000 ft |
| Feet (plain) | `30000` | 30,000 ft |
| Feet with suffix | `30000ft` | 30,000 ft |
| With comma | `30,000` | 30,000 ft |

## Speed Formats

Cruise speed is specified in knots as a plain number:

| Example | Description |
|---------|-------------|
| `450` | 450 knots TAS |
| `350` | 350 knots TAS |

## ETD Time Formats

Estimated Time of Departure in UTC:

| Format | Example | Description |
|--------|---------|-------------|
| With colon | `14:30` | 14:30 UTC |
| Without colon | `1430` | 14:30 UTC |
| With Z suffix | `14:30Z` | 14:30 UTC |
| Mixed | `1430Z` | 14:30 UTC |

If the specified time has already passed today, it is assumed to be tomorrow.

## OpenAIP Integration

The plugin can sync real-world navigation data from [OpenAIP](https://www.openaip.net/):

1. Register for a free API key at https://www.openaip.net/
2. Add the key to your configuration:
   ```yaml
   DEFAULT:
     openaip:
       api_key: "your-api-key-here"
   ```
3. Sync navigation fixes for each theater:
   ```
   /flightplan fix sync Caucasus
   /flightplan fix sync Syria
   /flightplan fix sync PersianGulf
   ```

### Supported Theaters

| Theater | Region |
|---------|--------|
| Caucasus | Georgia, South Russia |
| Syria | Syria, Lebanon, Cyprus, Turkey |
| PersianGulf | UAE, Iran, Oman |
| Nevada | Nevada, California |
| Normandy | Northern France |
| TheChannel | English Channel |
| MarianaIslands | Guam, Mariana Islands |
| SouthAtlantic | Falkland Islands |
| Sinai | Egypt, Sinai Peninsula |
| Afghanistan | Afghanistan region |
| Kola | Northern Russia, Norway |

## Database Schema

The plugin creates the following tables:

### flightplan_plans
Main flight plan storage.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| player_ucid | TEXT | Player's unique ID |
| server_name | TEXT | Associated server |
| callsign | TEXT | Flight callsign |
| aircraft_type | TEXT | Aircraft type |
| departure | TEXT | Departure location name |
| destination | TEXT | Destination location name |
| alternate | TEXT | Alternate location name |
| waypoints | JSONB | Array of parsed waypoints |
| cruise_altitude | INTEGER | Altitude in feet |
| cruise_speed | INTEGER | Speed in knots |
| etd | TIMESTAMP | Estimated departure time |
| status | TEXT | filed/active/completed/cancelled |
| departure_position | JSONB | DCS coordinates {x, z, lat, lon} |
| destination_position | JSONB | DCS coordinates {x, z, lat, lon} |
| alternate_position | JSONB | DCS coordinates {x, z, lat, lon} |
| discord_message_id | BIGINT | Published Discord message ID |
| stale_at | TIMESTAMP | When plan becomes stale |

### flightplan_waypoints
User-defined named waypoints.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| name | TEXT | Waypoint name (e.g., PANTHER) |
| created_by_ucid | TEXT | Creator's player ID |
| position_x | DOUBLE | DCS X coordinate |
| position_z | DOUBLE | DCS Z coordinate |
| latitude | DOUBLE | Latitude in decimal degrees |
| longitude | DOUBLE | Longitude in decimal degrees |
| altitude | INTEGER | Optional altitude in feet |
| map_theater | TEXT | Theater restriction |
| is_public | BOOLEAN | Visible to all players |

### flightplan_navigation_fixes
Navigation aids and fixes.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| identifier | TEXT | Fix identifier (e.g., ADLER) |
| name | TEXT | Full name |
| fix_type | TEXT | VOR/NDB/TACAN/DME/WYP/INT |
| latitude | DOUBLE | Latitude |
| longitude | DOUBLE | Longitude |
| map_theater | TEXT | Associated theater |
| frequency | TEXT | Radio frequency (if applicable) |
| source | TEXT | Data source (openaip/user/seed) |

### flightplan_markers
F10 marker tracking for cleanup.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| server_name | TEXT | Server instance |
| flight_plan_id | INTEGER | Associated flight plan |
| marker_id | INTEGER | DCS marker ID |
| marker_type | TEXT | Marker type identifier |
| expires_at | TIMESTAMP | When marker should be removed |

## Flight Plan Workflow

```
                    +------------------+
                    |      Filed       |
                    | (Plan created)   |
                    +--------+---------+
                             |
            +----------------+----------------+
            |                |                |
            v                v                v
  +---------+---------+  [TAKEOFF]  +---------+---------+
  |   Manual Activate |  (auto)    |     Cancelled     |
  |   (command/menu)  |---+        | (User cancelled)  |
  +---------+---------+   |        +-------------------+
            |             |
            +------+------+
                   |
                   v
         +---------+---------+
         |     Activated     |
         | (Route on F10)    |
         +---------+---------+
                   |
            +------+------+----------------+
            |             |                |
            v             v                v
  +---------+---------+ [LAND@DEST]  +---------+---------+
  |  Manual Complete  | (auto)       |     Cancelled     |
  |  (command/menu)   |---+          | (Stale/timeout)   |
  +---------+---------+   |          +-------------------+
            |             |
            +------+------+
                   |
                   v
         +---------+---------+
         |     Completed     |
         | (Flight logged)   |
         +-------------------+
```

**Auto-Lifecycle Events:**
- **TAKEOFF**: If `auto_lifecycle.activate_on_takeoff` is enabled, filed plans are automatically activated
- **LAND@DEST**: If `auto_lifecycle.complete_on_landing` is enabled, landing at destination completes the plan

## Troubleshooting

### Flight plan markers not appearing
- Ensure the plan has valid departure and destination positions
- Check that the server is running and mission is loaded
- Verify the plan status is 'active' (not just 'filed')

### Waypoints not being parsed
- Check coordinate format matches one of the supported formats
- For user waypoints, ensure the `@` prefix is used
- Verify navigation fixes exist for the current theater (`/flightplan fix count`)

### OpenAIP sync failing
- Verify your API key is correct in the configuration
- Check that the theater name matches exactly (case-sensitive)
- Some regions may have limited navigation data coverage

### Discord updates not posting
- Ensure `status_channel` is configured with a valid channel ID
- Verify the bot has permission to post in that channel
- Check that `publish_on_file` and/or `publish_on_activate` are true

### Stale plans not being cleaned up
- Verify `auto_cancel_stale` is true in configuration
- Stale cleanup runs on mission start (simulation start event)
- Use `/flightplan stale` to manually trigger cleanup
