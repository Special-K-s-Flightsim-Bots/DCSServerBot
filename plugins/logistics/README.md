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
  delivery:
    proximity_threshold: 3000  # Detection radius in meters
    require_landing: true      # Require landing event for auto-complete
  markers:
    enabled: true
    show_deadline: true
  tasks:
    auto_approve: false        # Auto-approve player requests
    timeout_hours: 24          # Task expiration
    max_per_player: 1          # Max concurrent tasks per player
```

## Discord Commands

### Task Management (`/logistics`)

| Command                                                                                       | Description             | Role      |
|-----------------------------------------------------------------------------------------------|-------------------------|-----------|
| `/logistics create <server> <cargo> <source> <destination> [coalition] [priority] [deadline]` | Create a logistics task | DCS Admin |
| `/logistics list [server] [status] [coalition]`                                               | List tasks with filters | DCS       |
| `/logistics view <task>`                                                                      | View task details       | DCS       |
| `/logistics approve <task>`                                                                   | Approve pending task    | DCS Admin |
| `/logistics deny <task> [reason]`                                                             | Deny pending task       | DCS Admin |
| `/logistics cancel <task>`                                                                    | Cancel any task         | DCS Admin |

### Warehouse Commands (`/warehouse`)

| Command                                              | Description                 | Role  |
|------------------------------------------------------|-----------------------------|-------|
| `/warehouse status <server> <airbase>`               | Query inventory at location | DCS   |
| `/warehouse compare <server> <source> <destination>` | Compare two locations       | DCS   |

## In-Game Chat Commands

| Command                   | Description                                       |
|---------------------------|---------------------------------------------------|
| `-tasks`                  | List available logistics tasks for your coalition |
| `-accept <id>`            | Accept/claim a logistics task                     |
| `-mytask`                 | Show your current assigned task                   |
| `-taskinfo <id>`          | View details of any visible task                  |
| `-deliver`                | Mark current task as delivered (manual)           |
| `-abandon`                | Release task back to available pool               |
| `-request <dest> <cargo>` | Player-initiated logistics request                |

## F10 Menu Structure

```
Logistics/
├── View Available Tasks     (Show popup with available tasks)
├── My Current Task          (Show your assigned task details)
├── Accept Task/             (Submenu with available tasks)
│   ├── #1: Mk-82 -> Akrotiri
│   ├── #2!: Fuel -> Illustrious  (! = urgent)
│   └── ...
├── Mark Delivered           (Manual completion - when assigned)
└── Abandon Task             (Release task - when assigned)
```

## F10 Map Markers

When a task is approved, coalition-specific markers appear on the F10 map:

- **Source Marker** (Green): `[PICKUP] Airbase Name`
- **Destination Marker** (Yellow):
  ```
  [DELIVERY] Airbase Name
  Cargo: 10x Mk-82
  Pilot: Maverick (or UNASSIGNED)
  Deadline: 14:30Z
  ```
- **Waypoint Markers** (Yellow): `[VIA 1] Waypoint Name`
- **Route Lines** (Yellow): Connecting source -> waypoints -> destination

## Delivery Detection

Tasks are automatically completed when:

1. **Primary**: Player lands at the destination airbase/FARP/carrier (detected via `S_EVENT_LAND`)
2. **Secondary**: Player's aircraft is within proximity threshold of destination position
3. **Fallback**: Manual `-deliver` command or F10 menu "Mark Delivered"

## Task Workflow

```
┌─────────┐     ┌──────────┐     ┌──────────┐     ┌───────────┐
│ pending │────>│ approved │────>│ assigned │────>│ completed │
└─────────┘     └──────────┘     └──────────┘     └───────────┘
     │               │                │
     │               │                │
     v               v                v
 ┌────────┐     ┌─────────┐     ┌───────────┐
 │ denied │     │cancelled│     │  failed   │
 └────────┘     └─────────┘     └───────────┘
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
