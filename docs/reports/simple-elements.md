---
title: Simple Elements
parent: Report Framework
nav_order: 20
---

# Simple Report Elements for Embeds

The following elements can be used in your reports without any additional coding. Anybody familiar with Discord Embeds should be able to create a simple report with them.

## Ruler

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

## Image

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

## Field

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

## Table

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

## SQLField

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

## SQLTable

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
