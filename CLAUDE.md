# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DCSServerBot is a comprehensive Python Discord bot for administrating DCS World servers. It features a modular plugin architecture, multi-node clustering support, and bidirectional communication with DCS via Lua scripts over UDP.

## Running the Bot

```bash
# Install dependencies (uses pip-tools)
pip install pip-tools
pip-compile requirements.in
pip-sync requirements.txt

# Run the bot
python run.py
```

The bot requires:
- Python 3.11+
- PostgreSQL database
- DCS World (client or dedicated server)

## Architecture

### Core Hierarchy

```
Node (PC installation)
├── Services (singletons: Bot, ServiceBus, Dashboard, Monitoring, etc.)
├── Extensions (optional: SRS, Tacview, LotAtc, etc.)
└── Plugins (Discord commands + event handlers)
    └── Each plugin has: commands.py, listener.py, lua/, db/
```

### Key Concepts

- **Node**: Single DCSServerBot installation on one PC. One node in a cluster is the "master" running the Discord bot
- **Instance**: A DCS.exe process controlled by a node (e.g., `DCS.dcs_serverrelease`)
- **Server**: A configuration that can be loaded into an instance (name, password, slots)
- **Plugin**: Modular component with Discord commands (`commands.py`) and DCS event handlers (`listener.py`)
- **Service**: Cluster-wide singleton (Bot, ServiceBus) or node-local background process

### Communication Flow

```
Discord ←→ Bot Service ←→ Plugin ←→ Lua Scripts ←→ DCS World
                              ↓
                         PostgreSQL
```

Plugins communicate with DCS via:
1. `server.send_to_dcs({"command": "name", ...})` - async, no response
2. `server.send_to_dcs_sync({"command": "name", ...})` - sync, waits for response

### Plugin Structure

```
plugins/myplugin/
├── __init__.py           # from .version import __version__
├── version.py            # __version__ = "1.0"
├── commands.py           # Discord slash commands (extends Plugin class)
├── listener.py           # Event handlers and chat commands (extends EventListener)
├── db/
│   ├── tables.sql        # Initial schema
│   └── update_v1.x.sql   # Migrations (version tracked per plugin)
├── lua/
│   ├── commands.lua      # Hook env: handles Python→DCS commands
│   ├── callbacks.lua     # Hook env: DCS callbacks (onPlayerConnect, etc.)
│   └── mission.lua       # Mission env: loaded via trigger.action.doFile()
└── README.md
```

### Database Patterns

- Use psycopg3 async: `async with self.apool.connection() as conn:`
- Row factory: `cursor(row_factory=dict_row)` for dict access
- Migrations are versioned per-plugin and run automatically on load

### Configuration

Plugin configs live in `config/plugins/<plugin>.yaml`:
```yaml
DEFAULT:
  setting: value
DCS.dcs_serverrelease:  # Instance-specific overrides
  setting: different_value
```

Access via `self.get_config(server)` (merges DEFAULT + instance-specific).

### Lua Script Patterns

**commands.lua** (Hook environment - receives from Python):
```lua
function dcsbot.myCommand(json)
    local script = 'dcsbot.myFunction(' .. utils.basicSerialize(json.param) .. ')'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end
```

**mission.lua** (Mission environment - runs in-game):
```lua
dcsbot.myFunction = function(param)
    -- Do stuff in mission environment
    dcsbot.sendBotTable({command = "onMyEvent", data = result})
end
```

Always use `utils.basicSerialize()` for strings and `tostring()` for numbers to prevent Lua injection.

### Event Handling

```python
from core import EventListener, event, chat_command

class MyListener(EventListener):
    @event(name="onPlayerConnect")
    async def on_connect(self, server: Server, data: dict):
        player = server.get_player(id=data['id'])

    @chat_command(name="mycommand", help="Does something")
    async def cmd_my(self, server: Server, player: Player, params: list[str]):
        await player.sendChatMessage("Response")
```

### F10 Menu Integration

F10 menus are created in mission.lua via `missionCommands.addCommand()`. Callbacks trigger events that the Python listener handles via `@event(name="pluginname")`.

---

## Development Environment

### Git Remotes (Local Machine)

- `origin` - Special-K-s-Flightsim-Bots/DCSServerBot (upstream, read-only)
- `fork` - engines-wafu/DCSServerBot (push here)

### GitHub Issues

Create issues on the **fork** only: `gh issue create -R engines-wafu/DCSServerBot`

**Issue Management:**
- Create an issue for each bug discovered or feature requested
- Update issues with progress comments as work proceeds
- Close issues with a comment referencing the fix commit when resolved
- Always apply appropriate labels when creating/updating issues

**Labels (use `--label` flag):**
- **Type:** `bug`, `enhancement`, `documentation`, `question`
- **Priority:** `priority: high`, `priority: medium`, `priority: low`
- **Plugin:** `plugin: flightplan`, `plugin: logbook`, `plugin: logistics`
- **Other:** `ux`, `help wanted`, `good first issue`

Example: `gh issue create -R engines-wafu/DCSServerBot --label "bug" --label "plugin: logbook" --label "priority: high"`

### Pull Requests

Do NOT include AI attribution in PR descriptions or commit messages.

---

## Server Access (Production)

```
Host: hoverstop
IP: 192.154.225.181
User: Administrator
Platform: Windows Server 2025
```

**Directory structure:**
```
C:\Users\Administrator\github\DCSServerBot\           # Main bot installation
C:\Users\Administrator\github\DCSServerBot\config\plugins\  # Plugin configs
C:\Users\Administrator\.dcssb\                        # Python venv
C:\Users\Administrator\Saved Games\                   # DCS saved games
```

**Common commands:**
```bash
ssh hoverstop "cd C:\Users\Administrator\github\DCSServerBot && git pull origin development"
ssh hoverstop "type C:\Users\Administrator\github\DCSServerBot\config\plugins\flightplan.yaml"
```

**Log file access:**

Log files rotate with `.1`, `.2`, etc. suffixes (most recent is `.1`). Use PowerShell for searching:

```bash
# List log files
ssh hoverstop 'dir C:\Users\Administrator\github\DCSServerBot\logs'

# Get last N lines of most recent log
ssh hoverstop 'powershell "Get-Content C:/Users/Administrator/github/DCSServerBot/logs/dcssb-server.log.1 -Tail 100"'

# Search for errors in most recent log
ssh hoverstop 'powershell "Get-Content C:/Users/Administrator/github/DCSServerBot/logs/dcssb-server.log.1 | Select-String -Pattern \"ERROR\" -Context 0,20"'

# Search for a pattern across log files
ssh hoverstop 'powershell "Get-Content C:/Users/Administrator/github/DCSServerBot/logs/dcssb-server.log.1 | Select-String -Pattern \"assign|logistics\" -CaseSensitive:0"'

# Get lines around a timestamp (e.g., 19:23)
ssh hoverstop 'powershell "Get-Content C:/Users/Administrator/github/DCSServerBot/logs/dcssb-server.log.1 | Select-String -Pattern \"19:23\" -Context 5,20"'

# Read specific line range (skip N, take M)
ssh hoverstop 'powershell "Get-Content C:/Users/Administrator/github/DCSServerBot/logs/dcssb-server.log.1 | Select-Object -Skip 300 -First 80"'
```

**Important:** Use single quotes around the SSH command and forward slashes in PowerShell paths to avoid shell escaping issues.

**Note:** On server, `origin` = engines-wafu fork, `upstream` = SpecialK. On local machine, it's reversed.

### Deployment Workflow

**IMPORTANT:** After pushing fixes to the fork, always pull to the DCS server:
```bash
# 1. Push to fork (from local machine)
git push fork development

# 2. Pull to DCS server
ssh hoverstop "cd C:\Users\Administrator\github\DCSServerBot && git pull origin development"

# 3. Clear pycache and restart bot via supervisor signal
ssh hoverstop 'powershell "Remove-Item -Recurse -Force C:/Users/Administrator/github/DCSServerBot/plugins/<plugin>/__pycache__ 2>$null; New-Item -Path C:/Users/Administrator/github/DCSServerBot/.restart_requested -ItemType File -Force"'

# 4. Wait for restart (15 seconds) then verify
sleep 15 && curl -s http://192.154.225.181:9876/mcp/bot/health
```

**Restart timing:** The supervisor checks for the restart signal every 1 second, then waits 5 seconds before starting the new process. Total restart time is approximately 10-15 seconds. Always clear `__pycache__` for modified plugins to ensure new code is loaded.

---

## MCP Tools and REST API

The project has two MCP integrations for AI-assisted development:

### 1. PostgreSQL MCP (Direct Database Access)

Configured in `.mcp.json` at project root. Provides direct read-only access to the production database.

**Available tools:**
- `mcp__postgres__execute_sql` - Run SELECT queries
- `mcp__postgres__list_schemas` - List database schemas
- `mcp__postgres__list_objects` - List tables/views in a schema
- `mcp__postgres__get_object_details` - Get table structure
- `mcp__postgres__explain_query` - Analyze query plans
- `mcp__postgres__analyze_db_health` - Check database health

**Example queries:**
```sql
-- List logistics tasks
SELECT id, source, destination, cargo, status, assigned_ucid
FROM logistics_tasks ORDER BY id DESC LIMIT 10;

-- Check player stats
SELECT p.name, ps.deaths, ps.kills
FROM players p JOIN playerstats ps ON p.ucid = ps.player_ucid;

-- View recent flights
SELECT * FROM logbook_flights ORDER BY created_at DESC LIMIT 5;
```

### 2. MCP REST API (Bot Control & Testing)

REST API running on the DCS server at `http://192.154.225.181:9876/mcp/`

#### Bot Control Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/mcp/bot/status` | GET | Bot version, uptime, loaded plugins |
| `/mcp/bot/health` | GET | Health check (DB connection, servers online) |
| `/mcp/bot/logs` | GET | Recent log entries with level/search filtering |
| `/mcp/bot/plugins` | GET | List all loaded plugins with versions |
| `/mcp/bot/restart` | POST | Signal supervisor to restart bot |

#### DCS Server Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/mcp/servers/{name}/status` | GET | Server status with mission and player info |
| `/mcp/servers/{name}/players` | GET | Connected players list |
| `/mcp/servers/{name}/mission` | GET | Current mission details |
| `/mcp/servers/{name}/chat` | POST | Send in-game chat message |
| `/mcp/servers/{name}/logistics/task` | POST | Create a logistics task |

#### Slash Command Execution

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/mcp/commands` | GET | List all available Discord slash commands |
| `/mcp/commands/execute` | POST | Execute any slash command programmatically |

**Execute command example:**
```bash
curl -s -X POST http://192.154.225.181:9876/mcp/commands/execute \
  -H "Content-Type: application/json" \
  -d '{"command":"logistics list","parameters":{"server":"DCS Server"}}'
```

Response contains `success`, `content`, `embeds[]`, and `error` fields.

---

## Testing and Verification Workflow

### After Code Changes

1. **Deploy the change:**
   ```bash
   git push fork <branch>
   ssh hoverstop "cd C:\Users\Administrator\github\DCSServerBot && git pull origin <branch>"
   ```

2. **Clear pycache and restart:**
   ```bash
   ssh hoverstop 'powershell "Remove-Item -Recurse -Force C:/Users/Administrator/github/DCSServerBot/plugins/<plugin>/__pycache__ 2>$null; New-Item -Path C:/Users/Administrator/github/DCSServerBot/.restart_requested -ItemType File -Force"'
   ```

3. **Wait and verify bot is healthy (15 seconds):**
   ```bash
   sleep 15 && curl -s http://192.154.225.181:9876/mcp/bot/health
   ```
   Expected: `{"status":"healthy","database":"connected","servers_online":1,"servers_total":1}`

4. **Test the specific functionality:**
   - Use `/mcp/commands/execute` to run the relevant slash command
   - Check logs for errors: `ssh hoverstop 'powershell "Get-Content ... | Select-String -Pattern \"ERROR\""'`
   - Query database directly to verify data changes

### Verifying Slash Command Behavior

Instead of asking users to test in Discord, execute commands via the REST API:

```bash
# Test logistics commands
curl -s -X POST http://192.154.225.181:9876/mcp/commands/execute \
  -H "Content-Type: application/json" \
  -d '{"command":"logistics list","parameters":{"server":"DCS Server","status":"approved"}}'

# Test logbook commands
curl -s -X POST http://192.154.225.181:9876/mcp/commands/execute \
  -H "Content-Type: application/json" \
  -d '{"command":"logbook stats","parameters":{"server":"DCS Server"}}'

# Test with different parameters
curl -s -X POST http://192.154.225.181:9876/mcp/commands/execute \
  -H "Content-Type: application/json" \
  -d '{"command":"mission restart","parameters":{"server":"DCS Server","delay":"30"}}'
```

### Checking for Errors After Testing

```bash
# Get recent errors from logs
ssh hoverstop 'powershell "Get-Content C:/Users/Administrator/github/DCSServerBot/logs/dcssb-server.log.1 -Tail 200 | Select-String -Pattern \"ERROR|Exception|Traceback\" -Context 0,10"'

# Check for specific command errors
ssh hoverstop 'powershell "Get-Content C:/Users/Administrator/github/DCSServerBot/logs/dcssb-server.log.1 | Select-String -Pattern \"logistics|logbook\" -CaseSensitive:0 -Context 2,5"'
```

### Database Verification

Use the PostgreSQL MCP to verify data changes:

```sql
-- After creating a logistics task
SELECT * FROM logistics_tasks ORDER BY id DESC LIMIT 1;

-- After a player action
SELECT * FROM logistics_task_history WHERE task_id = <id> ORDER BY changed_at DESC;

-- Check for constraint violations or orphaned records
SELECT lt.* FROM logistics_tasks lt
LEFT JOIN players p ON lt.assigned_ucid = p.ucid
WHERE lt.assigned_ucid IS NOT NULL AND p.ucid IS NULL;
```

### Feedback Loop

When testing reveals issues:

1. **Check logs immediately** for stack traces
2. **Query database** to understand current state
3. **Create GitHub issue** with:
   - Command/action that failed
   - Error message from logs
   - Database state if relevant
   - Steps to reproduce
4. **Fix, deploy, and re-test** using the same verification steps

---

## Current Work

### Feature Branch: `feat/flight_plan`

**FlightPlan Plugin** - IFR-style flight planning with F10 map visualization, waypoint autocomplete, OpenAIP integration.

**Logistics Plugin** - Cargo delivery missions with task acceptance, F10 markers, auto-completion.

**Logbook Plugin** - Pilot records, squadrons, qualifications, awards with ribbon rack images.

### Discord Test Server

- Server: Hover Stop Staging
- Guild ID: 1461528993516748980
- Default Channel ID: 1461528994708062363

### OpenAIP Configuration

FlightPlan plugin uses OpenAIP for navigation fix data. API key configured in `config/plugins/flightplan.yaml`:
```yaml
DEFAULT:
  openaip:
    api_key: 00dd1cf7f2956902a9c4f021c89fe335
```

Sync fixes with `/flightplan fix sync <theater>`.
