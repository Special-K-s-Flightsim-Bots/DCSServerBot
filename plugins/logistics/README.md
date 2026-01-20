# Logistics Plugin

A comprehensive logistics mission system for DCSServerBot with full DCS in-game integration. Create, manage, and track cargo delivery missions with F10 map markers, in-game chat commands, and automatic delivery detection.

## Features

- **Task Management**: Create and manage logistics delivery tasks via Discord
- **In-Game Integration**: View and accept tasks via F10 menu or chat commands
- **F10 Map Markers**: Visual route markers showing pickup, delivery, waypoints with cargo/pilot/deadline info
- **Auto-Completion**: Automatic task completion when pilot lands at destination
- **Logbook Integration**: Completed tasks are credited to pilot's logbook record
- **Warehouse Queries**: Query airbase/carrier inventory via Discord commands
- **Coalition-Specific**: All markers and tasks are coalition-restricted
- **Discord Status Board**: Real-time task status updates posted to a Discord channel

## Requirements

- DCSServerBot v3.6+
- **missionstats plugin** must be enabled (provides `onMissionEvent` for landing detection)
- **logbook plugin** (optional) - enables pilot credit for completed deliveries

## Installation

1. Add `logistics` to `opt_plugins` in your `config/main.yaml`:
   ```yaml
   opt_plugins:
     - logistics
   ```

2. Restart DCSServerBot - the database tables will be created automatically

3. (Optional) Configure the plugin in `config/plugins/logistics.yaml`

## Configuration

```yaml
# config/plugins/logistics.yaml
DEFAULT:
  enabled: true
  status_channel: 123456789012345678  # Discord channel ID for status updates
  publish_on_create: true             # Post when task is created
  publish_on_assign: true             # Post when task is assigned to a pilot
  publish_on_complete: true           # Post when task is completed
  publish_on_abandon: true            # Post when task is abandoned
  publish_on_cancel: true             # Post when task is cancelled
  marker_timeout: 30                  # Seconds before temporary markers disappear
  delivery:
    proximity_threshold: 3000         # Detection radius in meters
    require_landing: true             # Require landing event for auto-complete
  markers:
    enabled: true
    show_deadline: true
  tasks:
    auto_approve: false        # Auto-approve Discord-created tasks
    timeout_hours: 24          # Task expiration
    stale_days: 7              # Auto-cancel pending/approved tasks after N days (0 to disable)
    max_per_player: 1          # Max concurrent tasks per player
```

## Discord Commands

### Task Management (`/logistics`)

| Command                                                                                       | Description                      | Role      |
|-----------------------------------------------------------------------------------------------|----------------------------------|-----------|
| `/logistics create <server> <cargo> <source> <destination> [coalition] [priority] [deadline]` | Create a logistics task          | DCS Admin |
| `/logistics list [server] [status]`                                                           | List tasks with filters          | DCS       |
| `/logistics view <task>`                                                                      | View task details                | DCS       |
| `/logistics assign <task> <player>`                                                           | Assign task to a specific pilot  | DCS Admin |
| `/logistics approve <task>`                                                                   | Approve pending task             | DCS Admin |
| `/logistics deny <task> <reason>`                                                             | Deny pending task                | DCS Admin |
| `/logistics cancel <task>`                                                                    | Cancel any task                  | DCS Admin |

### Warehouse Commands (`/warehouse`)

| Command                                              | Description                 | Role  |
|------------------------------------------------------|-----------------------------|-------|
| `/warehouse status <server> <airbase>`               | Query inventory at location | DCS   |
| `/warehouse compare <server> <source> <destination>` | Compare two locations       | DCS   |

## Discord Status Board

The plugin can post task status updates to a designated Discord channel, providing real-time visibility into logistics operations.

### Setup

1. Create a dedicated channel for logistics status updates (e.g., `#logistics-board`)
2. Copy the channel ID and add it to your configuration:
   ```yaml
   DEFAULT:
     status_channel: 123456789012345678
   ```

### What Gets Published

The status board posts are updated in-place (same Discord message) as task status changes:

| Event | Published When | Default |
|-------|----------------|---------|
| `publish_on_create` | Task is created by admin | true |
| `publish_on_assign` | Pilot accepts/is assigned the task | true |
| `publish_on_complete` | Task is successfully completed | true |
| `publish_on_abandon` | Pilot abandons the task | true |
| `publish_on_cancel` | Admin cancels the task | true |

Each post shows the task ID, cargo, route, assigned pilot, and current status with color-coded embeds.

## In-Game Chat Commands

| Command                   | Description                                          |
|---------------------------|------------------------------------------------------|
| `-tasks`                  | List available logistics tasks for your coalition    |
| `-accept <id>`            | Accept/claim a logistics task (creates map markers)  |
| `-plot all`               | Plot all available tasks on F10 map (30s timeout)    |
| `-plot <id>`              | Plot specific task on F10 map (30s timeout)          |
| `-mytask`                 | Show your current assigned task                      |
| `-taskinfo <id>`          | View details of any visible task                     |
| `-deliver`                | Mark current task as delivered (manual)              |
| `-abandon`                | Release task back to available pool                  |

## F10 Menu Structure

```
Logistics/
+-- View Available Tasks     (Show popup with available tasks)
+-- My Current Task          (Show your assigned task details)
+-- Accept Task/             (Submenu with available tasks)
|   +-- #1: Mk-82 -> Akrotiri
|   +-- #2!: Fuel -> Illustrious  (! = urgent)
|   +-- ...
+-- Plot All Tasks (30s)     (Show all task markers temporarily)
+-- Plot Task/               (Submenu to plot specific tasks)
|   +-- #1: Mk-82 -> Akrotiri
|   +-- #2!: Fuel -> Illustrious
|   +-- ...
+-- Mark Delivered           (Manual completion - when assigned)
+-- Abandon Task             (Release task - when assigned)
```

## F10 Map Markers

When a task is accepted (`-accept`) or plotted (`-plot`), coalition-specific markers appear on the F10 map:

- **Source Marker**: `[PICKUP #1] Airbase Name`
- **Destination Marker**:
  ```
  [DELIVERY #1] Airbase Name
  Cargo: 10x Mk-82
  Pilot: Maverick (or UNASSIGNED)
  Deadline: 14:30Z
  ```
- **Waypoint Markers**: `[VIA 1] Waypoint Name`
- **Route Lines**: Yellow lines connecting source -> waypoints -> destination
- **Info Text Box**: Displayed at the midpoint of the first route segment with full task details:
  ```
  TASK #1
  From: Batumi
  To: Kutaisi
  Cargo: 10x Mk-82
  Pilot: UNASSIGNED
  Deadline: 14:30Z
  ```

**Marker Behavior:**
- `-accept <id>`: Creates permanent markers until task is completed/cancelled
- `-plot all`: Creates temporary markers for all available tasks (auto-remove after 30 seconds)
- `-plot <id>`: Creates temporary marker for specific task (auto-remove after 30 seconds)

Markers created via "Plot All Tasks" or "Plot Task" menu options automatically disappear after 30 seconds (configurable via `marker_timeout`).

## Delivery Detection

Tasks are automatically completed when:

1. **Primary**: Player lands at the destination airbase/FARP/carrier (detected via `S_EVENT_LAND`)
2. **Secondary**: Player's aircraft is within proximity threshold of destination position
3. **Fallback**: Manual `-deliver` command or F10 menu "Mark Delivered"

## Task Workflow

```
+---------+     +----------+     +----------+     +-----------+
| pending |---->| approved |---->| assigned |---->| completed |
+---------+     +----------+     +----------+     +-----------+
     |               |                |
     |               |                |
     v               v                v
 +--------+     +---------+     +-----------+
 | denied |     |cancelled|     |  failed   |
 +--------+     +---------+     +-----------+
```

- **pending**: Created by Discord command or player request, awaiting approval
- **approved**: Approved by admin, visible on map, available for acceptance
- **assigned**: Claimed by a pilot, shown with pilot name on markers
- **in_progress**: (Optional) Pilot has picked up cargo
- **completed**: Delivered successfully, credited to pilot's logbook
- **failed**: Task failed (timeout, other issues)
- **cancelled**: Cancelled by admin
- **denied**: Request denied by admin

## Logbook Integration

When a logistics task is completed, the pilot receives credit in the `logbook_logistics_completions` table:

| Field            | Description              |
|------------------|--------------------------|
| player_ucid      | Pilot's unique ID        |
| task_id          | Completed task reference |
| cargo_type       | What was delivered       |
| source_name      | Pickup location          |
| destination_name | Delivery location        |
| completed_at     | Completion timestamp     |

This data can be used for:
- Displaying logistics stats in `/logbook stats`
- Auto-granting qualifications (e.g., "Logistics Specialist" after 10 deliveries)
- Squadron statistics

## Database Schema

The plugin creates the following tables:

- `logistics_tasks` - Task definitions with status, positions, assignments
- `logistics_tasks_history` - Audit trail of task events
- `logistics_markers` - F10 marker ID tracking for cleanup
- `logbook_logistics_completions` - Pilot delivery records

## Migration from /stores

If you were using the `/stores` commands from the logbook plugin, note that logistics replaces that functionality entirely with a more comprehensive system. The old `logbook_stores_requests` table is preserved for reference but no longer actively used.

## Troubleshooting

### Markers not appearing
- Ensure the task has `source_position` and `destination_position` set
- Verify player is on the correct coalition
- Check DCS.log for Lua errors

### Auto-completion not working
- Ensure missionstats plugin is enabled (provides landing events)
- Check that destination name matches exactly or position is within threshold
- Try manual `-deliver` command as fallback

### F10 menu not showing
- Player must be in a valid slot (not spectator)
- Check that player's group_id is valid
- Verify the mission plugin's menu system is working
