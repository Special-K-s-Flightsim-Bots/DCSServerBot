# Report Framework
One of the main goals of DCSServerBot is gathering data of your DCS World servers and display them in a useful format.<br/>
To achieve this, DCSServerBot already comes with some built-in reports. Many plugins display simple to complex data, which I thought, might be of interest.
<br/><br/>
To allow you to change the look and feel of existing reports and to make it easier to build your own, I've developed a JSON-based reporting framework.
Here you'll find the main features and elements of this framework.

## Using Reports in your Plugins
It is very simple to generate a report in your plugins. You just need to instantiate one of the available Report classes with a json file that is stored in the ./reports subdirectory of your plugin.

```python
from core import DCSServerBot, Plugin, Report
from discord.ext import commands


class Test(Plugin):
    @commands.command(description='Test')
    async def test(self, ctx):
        report = Report(self.bot, self.plugin_name, 'test.json')
        env = await report.render(params={"name": "Special K"})
        await ctx.send(embed=env.embed)


def setup(bot: DCSServerBot):
    bot.add_cog(Test(bot))
```
> ⚠️ **Attention!**<br>
> If your reports contain graphs, the created image will be returned in env.filename. You need to take care of wrapping the in a discord.File and deleting the file after it has been displayed. 

## General Report Structure
Every report results in an Embed in Discord.<br/> 
An Embed has several attributes and many of them can be set inside the report description:
```json
{
  "color": "blue",
  "title": "This is the title of the Embed.",
  "description": "This is a brief description.",
  "url": "https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot",
  "img": "https://raw.githubusercontent.com/Special-K-s-Flightsim-Bots/DCSServerBot/master/images/play_256.png",
  "input": [],
  "pagination": {},
  "elements": [],
  "footer": "This is the footer (will be added to any other footers)"
}
```
### Input Section
Within the "input" section you can define variables that will be used inside the report or validate such, that came from your render(...) call.
```json
  "input":
  [
    {
      "name": "ruler_length",                          -- set a variable (here a reserved one, the length of the ruler)
      "value": 27                                      -- to a new value (default is 30)
    },
    {
      "name": "period",
      "range": ["", "day", "week", "month", "year"],   -- validate these passed parameters against a list of possible values
      "default": "day"                                 -- if no value for this variable is provided, set a default
    },
    {
      "sql": "SELECT ucid, name FROM players WHERE discord_id = %(discord_id)s"  -- read these parameters from the database
    },
    {
      "callback": "MissionFocusString"                 -- read a mission variable from DCS with this name
    }
  ],
```
### Pagination Section
Only needed for PaginationReports (see below).

### Elements Section
The "elements" section contains the real data that you want to present with your report.<br/>
You can either use pre-defined elements or write your own element by inheritance of one of the base classes provided by the framework.

### Variables
You usually work with variables that you pass to the corresponding render() call or that you define in the "input" section. These can be dictionaries like server- or player-data or just single values like server_name.
To use them in your reports, expect all strings to be f-string capable:
```json
{
  "title": "Report for Server {server_name}",
  "description": "Player {player[name]} is causing trouble."
}
```
Be aware that player['name'] is written as player[name] in the reports!

## Simple Report Elements for Embeds
The following elements can be used in your reports without any additional coding. Anybody familiar with Discord Embeds should be able to create a simple report with them.

### Ruler
A ruler, default size is 25 characters.
```json
"elements": [
  {
    "Ruler"
  }
]
```
Or if you want to change the size:
```json
"elements": [
    {
      "type": "Ruler",
      "params": {
        "ruler_length": 10
      }
    }
]
```
You can add a header, too:
```json
"elements": [
    {
      "type": "Ruler",
      "params": {
        "header": "Active Servers"
      }
    }
]
```

### Image
An image used as a thumbnail.
```json
"elements": [
    {
      "type": "Image",
      "params": {
        "url": "https://static.wikia.nocookie.net/simpsons/images/a/a1/Flying_Hellfish_Logo.png"
      }
    }
]
```

### Field
A field with a single key/value pair.
```json
"elements": [
    {
      "type": "Field",
      "params": {
        "name": "Name",
        "value": "Special K",
        "inline": false
      }
    }
]
```

### Table
Multiple fields displayed as a table with a single header line and a maximum of 3 columns.
```json
"elements": [
    {
      "type": "Table",
      "params": {
        "values": [
          {
            "name": "Special K",
            "skill": "limited"
          },
          {
            "name": "Special A",
            "skill": "expert"
          }
        ]
      }
    }
]
```
A table can be built up from a passed object that needs to be a list of dict. The values section then contains
the key of the fields you want to use from these dictionaries and the name you want to display:
```json
"elements": [
    {
      "type": "Table",
      "params": {
        "obj": "servers",
        "values": {
          "display_name": "Server Name",
          "status": "Status",
          "num_players": "Active Players"
        }
      }
    }
]
```

### SQLField
If you want to display a single value from a database table, use the SQLField for it.
```json
"elements": [
    {
      "type": "SQLField",
      "params": {
        "sql": "SELECT points AS \"Points\" FROM sb_points WHERE player_ucid = %(ucid)s",
        "inline": false
      }
    }
]
```
If your query needs values, you can provide them as a dictionary to your report() call.

### SQLTable
Similar to the Table element but with values from a SQL query:
```json
"elements": [
    {
      "type": "SQLTable",
      "params": {
        "sql": "SELECT init_id as ucid, event, SUM(points) AS points FROM pu_events WHERE init_id = %(ucid)s GROUP BY 1,2"
      }
    }
]
```

## Graph Elements
To display nice graphics like bar-charts or pie-charts, you need to wrap them in a Graph element:
```json
"elements": [
  {
    "type": "Graph",
    "params": {
      "width": 10, -- width of the resulting image
      "height": 10, -- height of the resulting image
      "cols": 2, -- number of columns in the grid
      "rows": 1, -- number of rows in the grid
      "elements": [
        ... describe your GraphElements in here ...
      ]
    }
  }      
]
```
> ⚠️ **Attention!**<br>
> Only one Graph element per report is allowed.

Each sub-element has at least the following parameters:
```json
    "elements": [
      {
        "type": "xxx",    -- the type of the element (BarChart, PieChart, etc.)
        "params": {
        "col": 0,         -- the x position of the chart in the grid
        "row": 0,         -- the y position of the chart in the grid
        "colspan": 1,     -- optional: the number of columns that this chart uses
        "rowspan": 1      -- optional: the number of rows that this chart uses
      }
    ]
```

### BarChart
Simple bar chart that will display all elements of a given dictionary.
```json
    "elements": [
      {
        "type": "BarChart",
        "params": {
          "col": 0,
          "row": 0,
          "title": "Test BarChart",
          "color": "blue",
          "rotate_labels": 30,         -- rotate the labels by 30°        
          "bar_labels": true,          -- put the value of each bar at the top
          "is_time": true,             -- select the time formatter
          "orientation": "horizontal", -- set the orientation (vertical is default)
          "show_no_data": false,       -- if no data is available, don't display "No data available." but nothing
          "values": { "Takeoffs": 2, "Landings": 1, "Crashes": 1 }
        }
      }
    ]
```

### SQLBarChart
Same as bar chart, but with an SQL to grab the data from the database.
```json
    "elements": [
      {
        "type": "SQLBarChart",
        "params": {
          "col": 0,
          "row": 0,
          "title": "Kills & Deaths",
          "sql": "SELECT SUM(kills) AS Kills, SUM(deaths) AS Deaths FROM statistics WHERE player_ucid = %(ucid)s"
        }
      }
    ]
```

### PieChart
Simple pie chart that will display all elements of a given dictionary.
```json
    "elements": [
      {
        "type": "PieChart",
        "params": {
          "col": 0,
          "row": 0,
          "title": "Test PieChart",
          "is_time": true,             -- select the time formatter
          "show_no_data": false,       -- if no data is available, don't display "No data available." but nothing
          "values": { "Takeoffs": 2, "Landings": 1, "Crashes": 1 }
        }
      }
    ]
```

### SQLPieChart
Same as pie chart, but with an SQL to grab the data from the database.
```json
    "elements": [
      {
        "type": "SQLPieChart",
        "params": {
          "col": 0,
          "row": 0,
          "title": "Test PieChart",
          "sql": "SELECT SUM(kills) AS Kills, SUM(deaths) AS Deaths FROM statistics WHERE player_ucid = %(ucid)s"
        }
      }
    ]
```

## Report Types
There are three report types that you can use:

1) Report
<br/>Standard implementation. Will output a single report as an embed.
2) PaginationReport
<br/>Will enable pagination based on a provided parameter list.
3) PersistentReport
<br/>For auto-updates. Everytime a persistent report is generated, it will update the former embed.

Let's look at the more complex ones.

### PaginationReport
To use a PaginationReport, your code could look like the following:

```python
from core import PaginationReport
from discord.ext import commands
from typing import Optional


@commands.command(description='Pagination Test', usage='[period] [server name]')
async def test(self, ctx, period: Optional[str] = None, server_name: Optional[str] = None):
    report = PaginationReport(self.bot, ctx, self.plugin_name, 'mytest.json')
    await report.render(period=period, server_name=server_name)
```
Providing None to the pagination value (here server_name) will result in None being the first element to allow aggregated
displays. If you provide a strict value, this will be the first to be displayed out of the pagination list.


In your report though, you have to specify a pagination section:
```json
{
  "color": "blue",
  "title": "My Pagination Test",
  "input": [
    {
      "name": "period",
      "range": ["", "day", "week", "month", "year"],
      "default": "day"
    }
  ],
  "pagination":
  {
    "param":
    {
      "name": "server_name",
      "sql": "SELECT DISTINCT server_name FROM missions"
    }
  },
  "elements": []
}
```
Now you can use {server_name} in your report elements:
```json
    "elements": [
      {
        "type": "SQLPieChart",
        "params": {
          "col": 0,
          "row": 0,
          "title": "Server Time",
          "sql": "select mission_name, ROUND(SUM(EXTRACT(EPOCH FROM (mission_end - mission_start))) / 3600) FROM missions GROUP BY 1 WHERE server_name LIKE '{server_name}'"
        }
      }
    ]
```

### Persistent Report
To use a PersistentReport, in general you produce a normal report but provide a unique key with it, that will be used to access and update it later on.

```python
from core import utils, PersistentReport
from discord.ext import commands
from typing import Optional


@commands.command(description='Pagination Test', usage='[period] [server name]')
async def test(self, ctx, period: Optional[str] = None, server_name: Optional[str] = None):
    server = await utils.get_server(self, ctx)
    report = PersistentReport(self.bot, self.plugin_name, 'mytest.json', server, 'test_embed')
    return await report.render(period=period, server_name=server_name)
```

Whenever you call ```.test```, you will not generate a new report but update the existing one.<br/>
> ⚠️ **Attention!**<br>
> The key is unique in that server. You must not use the same key for two different reports, they will replace each other otherwise.
