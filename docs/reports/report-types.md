---
parent: Report Framework
nav_order: 10
---

# Report Types

There are three report types that you can use:

| Type       | Description                                                               |
|--------------|---------------------------------------------------------------------|
| Report | Standard implementation. Will output a single report as an embed. |
| PaginationReport | Will enable pagination based on a provided parameter list. |
| PersistentReport | For auto-updates. Everytime a persistent report is generated, it will update the former embed. |

Let's look at the more complex ones.

## PaginationReport

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

## Persistent Report

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

Whenever you call ```.test```, you will not generate a new report but update the existing one.

{: .warning }
> The key is unique in that server. You must not use the same key for two different reports, they will replace each other otherwise.
