# DCSServerBot Reporting Framework Guide

The DCSServerBot **Reporting Framework** provides a declarative, JSON-based Domain-Specific Language (DSL) for 
designing rich Discord Embeds, formatted tabular data, and high-resolution graphical charts (bar charts, pie charts, 
rendered tables).

---

## Table of Contents
1. [Architecture & Resolution Order](#architecture--resolution-order)
   - [Default vs Custom Overrides](#default-vs-custom-overrides)
   - [Modular Composition & Inclusions](#modular-composition--inclusions)
2. [JSON DSL Specification](#json-dsl-specification)
   - [Top-Level Report Schema](#top-level-report-schema)
   - [Dynamic Formatting & Variable Substitution](#dynamic-formatting--variable-substitution)
   - [Input Pipeline (`input`)](#input-pipeline-input)
   - [Pagination Configuration (`pagination`)](#pagination-configuration-pagination)
3. [Built-in Embed Elements](#built-in-embed-elements)
   - [Ruler](#ruler)
   - [Image](#image)
   - [Field](#field)
   - [Table](#table)
   - [SQLField](#sqlfield)
   - [SQLTable](#sqltable)
   - [Button](#button)
4. [Built-in Graph Elements (Matplotlib)](#built-in-graph-elements-matplotlib)
   - [Graph Container (`Graph`)](#graph-container-graph)
   - [BarChart & SQLBarChart](#barchart--sqlbarchart)
   - [PieChart & SQLPieChart](#piechart--sqlpiechart)
   - [SQLRenderedTable](#sqlrenderedtable)
5. [Using Reports in Plugins (Python API)](#using-reports-in-plugins-python-api)
   - [Standard Report (`Report`)](#standard-report-report)
   - [Paginated Report (`PaginationReport`)](#paginated-report-paginationreport)
   - [Persistent Auto-Updating Report (`PersistentReport`)](#persistent-auto-updating-report-persistentreport)
   - [Working with `ReportEnv`](#working-with-reportenv)
6. [Extending the Framework with Custom Python Code](#extending-the-framework-with-custom-python-code)
   - [Custom Embed Element (`EmbedElement`)](#custom-embed-element-embedelement)
   - [Custom Graph Element (`GraphElement`)](#custom-graph-element-graphelement)
   - [Custom Pagination Provider (`Pagination`)](#custom-pagination-provider-pagination)
7. [Best Practices & Developer Checklist](#best-practices--developer-checklist)

---

## Architecture & Resolution Order

### Default vs Custom Overrides
DCSServerBot ships with default report definitions packaged with plugins. 
Users can override any existing report template without modifying bot source files:

1. **Custom Override**: `./reports/<plugin>/<filename>` (Checked first)
2. **Plugin Default**: `./plugins/<plugin>/reports/<filename>` (Fallback)

If `reports/<plugin>/<filename>` exists in the bot root directory, it takes precedence over the plugin's default file.

### Modular Composition & Inclusions
Reports can be composed hierarchically using the `"include"` keyword at either the report root level or inside 
the `elements` array:

#### 1. Root-Level Report Inheritance / Merging
```json
{
  "include": {
    "plugin": "userstats",
    "filename": "base_stats.json"
  },
  "title": "Custom Overridden Title"
}
```

#### 2. Element-Level Inclusions
```json
{
  "title": "Combined Server Status",
  "elements": [
    {
      "include": {
        "plugin": "mission",
        "filename": "server_header.json"
      }
    },
    {
      "type": "Field",
      "params": {
        "name": "Custom Notes",
        "value": "Mission running smoothly."
      }
    }
  ]
}
```

---

## JSON DSL Specification

### Top-Level Report Schema

```json
{
  "color": "blue",
  "mention": [112233445566, 223344556677],
  "title": "Embed Title (max 256 chars)",
  "description": "Embed Description (max 4096 chars)",
  "url": "https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot",
  "img": "https://example.com/thumbnail.png",
  "author": "Author Name",
  "author_url": "https://example.com/author",
  "author_icon": "https://example.com/author_icon.png",
  "footer": "Custom footer text (max 2048 chars)",
  "input": [],
  "pagination": {},
  "elements": []
}
```

#### Top-Level Fields
| Field         | Type         | Description                                                                                                                        |
|---------------|--------------|------------------------------------------------------------------------------------------------------------------------------------|
| `color`       | `str`        | Embed stripe color name (`"blue"`, `"red"`, `"green"`, `"gold"`, etc. matching `discord.Color`). Supports `{variable}` formatting. |
| `mention`     | `list[int]`  | Discord role or user IDs to ping alongside the message.                                                                            |
| `title`       | `str`        | Main embed title.                                                                                                                  |
| `description` | `str`        | Embed body description text.                                                                                                       |
| `url`         | `str`        | Hyperlink attached to the embed title.                                                                                             |
| `img`         | `str`        | URL to display as the embed thumbnail image.                                                                                       |
| `author`      | `str`        | Author field text.                                                                                                                 |
| `author_url`  | `str`        | Link attached to the author name.                                                                                                  |
| `author_icon` | `str`        | Small icon URL next to author name.                                                                                                |
| `footer`      | `str`        | Embed footer note (appended to standard bot footers).                                                                              |
| `input`       | `list[dict]` | Variable declarations, validations, DB queries, or DCS variable fetch steps.                                                       |
| `pagination`  | `dict`       | Pagination parameter source (for `PaginationReport`).                                                                              |
| `elements`    | `list[dict]` | Array of Embed or Graph elements rendered inside the report.                                                                       |

---

### Dynamic Formatting & Variable Substitution

All string values in the JSON DSL support Python f-string syntax using parameters passed into `render(...)` or created via the `input` section:

```json
{
  "title": "Statistics for {server_name}",
  "description": "Player **{player[name]}** (UCID: `{player[ucid]}`)"
}
```

- **Dictionary Access**: Write `{player[name]}` (do **not** quote inside brackets like `{player['name']}`).
- **Arithmetic Expressions**: Used in dimension fields like `height`, `width`, `dpi`:
  ```json
  {
    "height": "${limit} / 2 + 2"
  }
  ```

---

### Input Pipeline (`input`)

The `input` array lets reports declare variables, apply default values, validate input ranges, query the database, execute Python helper functions, or fetch live DCS variables:

```json
{
  "input": [
    {
      "name": "ruler_length",
      "value": 27
    },
    {
      "name": "period",
      "range": ["", "day", "week", "month", "year"],
      "default": "day"
    },
    {
      "name": "campaign_data",
      "call": "plugins.gamemaster.helper.get_active_campaign",
      "params": {
        "server_name": "{server_name}"
      }
    },
    {
      "sql": "SELECT ucid, name FROM players WHERE discord_id = %(discord_id)s"
    },
    {
      "callback": "MissionFocusString"
    },
    {
      "event": "getMissionUpdate",
      "params": { "detailed": true }
    }
  ]
}
```

#### Input Pipeline Directives
1. **Set / Override Variable (`value`)**: Assigns a static or formatted string to `params[name]`.
2. **Range Validation (`range`) & Default (`default`)**: Validates parameter against allowed values; raises `ValueNotInRange` if invalid. If missing, assigns `default`.
3. **Function Call (`call`)**: Invokes a sync or async Python callable by dotted path (`module.func`). Passes available parameters matching the function signature.
4. **SQL Query (`sql`)**: Executes an async PostgreSQL query. If exactly one row is returned, columns are unpacked directly into `params`.
5. **DCS Variable Callback (`callback`)**: Queries a global Lua variable from the running DCS server via `{ "command": "getVariable", "name": callback }`.
6. **DCS Event Call (`event`)**: Sends a synchronous JSON command to DCS via `{ "command": event }` and stores the response object in `params[event]`.

---

### Pagination Configuration (`pagination`)

Used in conjunction with `PaginationReport` to generate interactive pagination dropdowns/buttons:

```json
{
  "pagination": {
    "param": {
      "name": "server_name",
      "sql": "SELECT DISTINCT server_name FROM missions ORDER BY 1"
    }
  }
}
```

Alternatively, pagination options can be populated by a custom Python `Pagination` class:
```json
{
  "pagination": {
    "param": {
      "name": "selected_user",
      "class": "plugins.userstats.pagination.UserPagination"
    }
  }
}
```

---

## Built-in Embed Elements

Embed elements render directly into Discord embed fields, text blocks, or interactive buttons.

### Ruler
Adds a visual line separator in an embed field (max 34 characters per Discord field limits).
```json
{
  "type": "Ruler",
  "params": {
    "header": "Active Servers",
    "ruler_length": 30
  }
}
```

### Image
Sets the thumbnail of the embed.
```json
{
  "type": "Image",
  "params": {
    "url": "https://example.com/logo.png"
  }
}
```

### Field
Displays a single key/value field.
```json
{
  "type": "Field",
  "params": {
    "name": "Server Status",
    "value": "{server.status.name}",
    "inline": true,
    "default": "Offline"
  }
}
```

### Table
Displays structured tabular data using multiple inline fields.
```json
{
  "type": "Table",
  "params": {
    "obj": "servers",
    "values": {
      "display_name": "Server Name",
      "status": "Status",
      "num_players": "Active Players"
    },
    "ansi_colors": false
  }
}
```
- `obj`: Parameter name containing a `list[dict]`.
- `values`: Mapping of dictionary keys to displayed column titles (maximum 3 columns per table).
- `ansi_colors`: When `true`, parses ANSI escape sequences (e.g. `\u001b[0;31m`) for colored Discord code blocks.

### SQLField
Queries a single scalar value from the database and displays it as an embed field.
```json
{
  "type": "SQLField",
  "params": {
    "name": "Total Points",
    "sql": "SELECT SUM(points) AS \"Points\" FROM pu_events WHERE init_id = %(ucid)s",
    "inline": false,
    "no_data": { "Points": 0 },
    "on_error": { "Points": "Error loading points" }
  }
}
```

### SQLTable
Executes an SQL query and displays up to 3 columns as aligned Discord fields.
```json
{
  "type": "SQLTable",
  "params": {
    "sql": "SELECT init_id as ucid, event, SUM(points) AS points FROM pu_events WHERE init_id = %(ucid)s GROUP BY 1,2",
    "no_data": "You have no points yet!",
    "on_error": "An error occurred: {ex}",
    "ansi_colors": false
  }
}
```

### Button
Adds an interactive Discord UI button directly to the view.
```json
{
  "type": "Button",
  "params": {
    "label": "View Server Live Map",
    "style": "link",
    "url": "https://myserver.com/map"
  }
}
```

---

## Built-in Graph Elements (Matplotlib)

Graph elements generate high-resolution PNG charts via Matplotlib and automatically attach them to the Discord embed 
as `attachment://report.png`.

### Graph Container (`Graph`)
The `Graph` element defines the figure canvas and subplot layout. **Only one `Graph` element is allowed per report.**

```json
{
  "type": "Graph",
  "params": {
    "width": 10,
    "height": 6,
    "cols": 2,
    "rows": 1,
    "wspace": 0.4,
    "hspace": 0.4,
    "dpi": 100,
    "facecolor": "#2C2F33",
    "elements": [
      {
        "type": "SQLBarChart",
        "params": {
          "col": 0,
          "row": 0,
          "title": "Kills by Type",
          "sql": "SELECT event, COUNT(*) AS count FROM pu_events WHERE init_id = %(ucid)s GROUP BY event"
        }
      },
      {
        "type": "SQLPieChart",
        "params": {
          "col": 1,
          "row": 0,
          "title": "Flight Hours by Airframe",
          "is_time": true,
          "sql": "SELECT slot, ROUND(SUM(EXTRACT(EPOCH FROM (hop_off - hop_on)))) AS duration FROM statistics WHERE player_ucid = %(ucid)s GROUP BY slot"
        }
      }
    ]
  }
}
```

#### Canvas Grid Parameters
| Parameter   | Type            | Default     | Description                                                               |
|-------------|-----------------|-------------|---------------------------------------------------------------------------|
| `width`     | `float` / `str` | `8`         | Canvas width in inches (supports dynamic math like `"${limit} / 2 + 2"`). |
| `height`    | `float` / `str` | `4`         | Canvas height in inches.                                                  |
| `cols`      | `int`           | `1`         | Number of subplot grid columns.                                           |
| `rows`      | `int`           | `1`         | Number of subplot grid rows.                                              |
| `wspace`    | `float`         | `0.2`       | Horizontal spacing between subplots.                                      |
| `hspace`    | `float`         | `0.2`       | Vertical spacing between subplots.                                        |
| `dpi`       | `int`           | `100`       | Output image DPI.                                                         |
| `facecolor` | `str`           | `"#2C2F33"` | Canvas background color hex.                                              |

#### Common Subplot Placement Options
- `col` (`int`): Subplot column index (0-indexed).
- `row` (`int`): Subplot row index (0-indexed).
- `colspan` (`int`, optional): Number of columns spanned (default `1`).
- `rowspan` (`int`, optional): Number of rows spanned (default `1`).

---

### BarChart & SQLBarChart
Renders vertical or horizontal bar charts.

```json
{
  "type": "BarChart",
  "params": {
    "col": 0,
    "row": 0,
    "title": "Mission Events",
    "color": "dodgerblue",
    "orientation": "vertical",
    "rotate_labels": 30,
    "bar_labels": true,
    "is_time": false,
    "show_no_data": true,
    "values": {
      "Takeoffs": 12,
      "Landings": 10,
      "Crashes": 2
    }
  }
}
```

For `SQLBarChart`, replace `"values"` with an `"sql"` query returning label-value pairs:
```json
{
  "type": "SQLBarChart",
  "params": {
    "col": 0,
    "row": 0,
    "title": "Air-to-Air vs Ground Kills",
    "sql": "SELECT 'Air Kills' AS label, SUM(kills_air) AS value FROM statistics WHERE player_ucid = %(ucid)s UNION ALL SELECT 'Ground Kills', SUM(kills_ground) FROM statistics WHERE player_ucid = %(ucid)s"
  }
}
```

### PieChart & SQLPieChart
Renders pie charts with automatic slice percentage calculation and color schemes.

```json
{
  "type": "SQLPieChart",
  "params": {
    "col": 0,
    "row": 0,
    "title": "Coalition Flight Distribution",
    "is_time": true,
    "sql": "SELECT coalition, SUM(flight_time) FROM player_stats WHERE player_ucid = %(ucid)s GROUP BY coalition"
  }
}
```
- `is_time` (`bool`): When `true`, formats values as HH:MM:SS time strings instead of raw integers.

### SQLRenderedTable
Renders a high-resolution, graphically styled data table directly onto the Matplotlib canvas.

```json
{
  "type": "SQLRenderedTable",
  "params": {
    "col": 0,
    "row": 0,
    "title": "Top 10 Highscores",
    "sql": "SELECT name AS \"Player\", kills AS \"Kills\", score AS \"Score\" FROM statistics ORDER BY score DESC LIMIT 10",
    "no_data": "No statistics available.",
    "fontsize": 10
  }
}
```

---

## Using Reports in Plugins (Python API)

Reports are instantiated and executed inside plugin command handlers (`commands.py`).

### Standard Report (`Report`)

```python
import discord
from core import Plugin, command, Report
from services.bot import DCSServerBot


class MyStatsPlugin(Plugin):

    @command(description="Display user statistics")
    async def stats(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer()
        target = user or interaction.user

        report = Report(self.bot, self.plugin_name, "stats.json")
        env = await report.render(
            discord_id=target.id,
            name=target.display_name
        )

        file = discord.File(env.buffer, filename=env.filename) if env.buffer else discord.utils.MISSING
        await interaction.followup.send(embed=env.embed, file=file)


async def setup(bot: DCSServerBot):
    await bot.add_cog(MyStatsPlugin(bot))
```

---

### Paginated Report (`PaginationReport`)

Interactive multi-page report with select menus and page flipping:

```python
import discord
from discord import app_commands
from typing import Optional
from core import Plugin, command, utils, Server, PaginationReport
from services.bot import DCSServerBot


class ServerStats(Plugin):

    @command(description="View paginated server activity")
    async def serveractivity(self, interaction: discord.Interaction,
                             server: Optional[app_commands.Transform[Server, utils.ServerTransformer]] = None):
        await interaction.response.defer()
        report = PaginationReport(interaction, plugin=self.plugin_name, filename="activity.json")
        await report.render(server_name=server.name if server else None)


async def setup(bot: DCSServerBot):
    await bot.add_cog(ServerStats(bot))
```

---

### Persistent Auto-Updating Report (`PersistentReport`)

Used for persistent dashboard embeds (e.g. pinned server status boards, auto-updating highscores). Every execution updates the existing Discord embed:

```python
from core import Plugin, PersistentReport, Server
from discord.ext import tasks


class StatusDashboard(Plugin):

    @tasks.loop(minutes=5.0)
    async def update_dashboard(self):
        server: Server = self.bot.servers.get("DCS.dcs_server1")
        if not server:
            return

        report = PersistentReport(
            self.bot,
            plugin=self.plugin_name,
            filename="dashboard.json",
            embed_name="server_dashboard"  # Unique key per server/channel
        )
        await report.render(server_name=server.name)
```

---

### Working with `ReportEnv`

`report.render()` returns an instance of `ReportEnv` (`core.report.ReportEnv`):

| Attribute      | Type                       | Description                                                     |
|----------------|----------------------------|-----------------------------------------------------------------|
| `env.bot`      | `DCSServerBot`             | The active bot service instance.                                |
| `env.embed`    | `discord.Embed`            | The generated Discord embed.                                    |
| `env.figure`   | `matplotlib.figure.Figure` | The Matplotlib figure object (if graphs were rendered).         |
| `env.buffer`   | `io.BytesIO`               | In-memory stream containing the generated PNG image bytes.      |
| `env.filename` | `str`                      | Attachment filename (e.g., `"report.png"`).                     |
| `env.params`   | `dict`                     | Merged parameters dictionary after `input` pipeline processing. |
| `env.view`     | `discord.ui.View`          | Interactive Discord View containing attached buttons or menus.  |

---

## Extending the Framework with Custom Python Code

When pre-built elements do not satisfy specific requirements, developers can write custom Python elements and reference them directly in JSON reports via the `"class"` property.

### Custom Embed Element (`EmbedElement`)

Create a custom subclass of `core.report.EmbedElement` to generate custom Discord embed fields:

```python
# plugins/myplugin/elements.py
from core.report import EmbedElement


class SystemLoadField(EmbedElement):

    async def render(self, cpu_threshold: int = 80):
        # Access parameters from self.env.params
        server = self.env.params.get("server")
        cpu_usage = server.node.stats.get("cpu", 0) if server else 0

        status_icon = "⚠️" if cpu_usage >= cpu_threshold else "✅"
        self.embed.add_field(
            name="CPU Load",
            value=f"{status_icon} **{cpu_usage}%** (Threshold: {cpu_threshold}%)",
            inline=True
        )
```

#### Reference in JSON Report:
```json
{
  "elements": [
    {
      "class": "plugins.myplugin.elements.SystemLoadField",
      "params": {
        "cpu_threshold": 75
      }
    }
  ]
}
```

---

### Custom Graph Element (`GraphElement`)

Subclass `core.report.GraphElement` to render custom visualizations using Matplotlib:

```python
# plugins/myplugin/graphs.py
import numpy as np
from core.report import GraphElement


class RadarThreatChart(GraphElement):

    async def render(self, threats: dict):
        # Create radar / polar visualization on self.axes
        labels = list(threats.keys())
        values = list(threats.values())

        angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
        values += values[:1]
        angles += angles[:1]

        self.axes.plot(angles, values, color='red', linewidth=2)
        self.axes.fill(angles, values, color='red', alpha=0.25)
        self.axes.set_thetagrids(np.degrees(angles[:-1]), labels)
        self.axes.set_title("Threat Proximity Radar", color="white", fontsize=12)
```

#### Reference in JSON Report:
```json
{
  "type": "Graph",
  "params": {
    "width": 6,
    "height": 6,
    "cols": 1,
    "rows": 1,
    "elements": [
      {
        "class": "plugins.myplugin.graphs.RadarThreatChart",
        "params": {
          "col": 0,
          "row": 0,
          "threats": {
            "SAM": 4,
            "CAP": 2,
            "AAA": 8,
            "EWR": 1
          }
        }
      }
    ]
  }
}
```

---

### Custom Pagination Provider (`Pagination`)

Subclass `core.report.Pagination` to supply dynamic pagination options from external APIs or complex algorithms:

```python
# plugins/myplugin/pagination.py
from core.report import Pagination


class ActiveSquadronsPagination(Pagination):

    async def values(self, **kwargs) -> list[str]:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("SELECT DISTINCT squadron_name FROM squadrons ORDER BY 1")
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
```

#### Reference in JSON Report:
```json
{
  "pagination": {
    "param": {
      "name": "squadron_name",
      "class": "plugins.myplugin.pagination.ActiveSquadronsPagination"
    }
  }
}
```

---

## Best Practices & Developer Checklist

When creating or customizing reports, follow this checklist:

- [ ] **Declarative First**: Prefer JSON DSL features (`input`, `sql`, `params`, built-in elements) before writing custom Python classes.
- [ ] **Directory Hierarchy**: Place user overrides under `reports/<plugin>/<filename>.json` and plugin defaults under `plugins/<plugin>/reports/<filename>.json`.
- [ ] **SQL Sanitization**: Always use parameterized SQL (`%(param)s`) in `input` and `sql` elements rather than string concatenation to prevent SQL injection.
- [ ] **Async Safety**: Use async database connections (`self.apool`) and `asyncio.to_thread` for heavy disk/math operations in custom elements.
- [ ] **Discord Limits**:
  - Title: max 256 characters.
  - Description: max 4096 characters.
  - Fields: max 25 fields per embed; field values max 1024 characters.
  - Tables: max 3 inline columns per row.
- [ ] **Graph Optimization**: Keep DPI reasonable (`100`–`150`) and canvas sizes balanced (`width: 8–12`, `height: 4–8`) to ensure fast generation and responsive Discord upload times.
- [ ] **Unique Embed Keys**: When using `PersistentReport`, ensure `embed_name` is strictly unique per server/channel to prevent reports from overwriting each other.
