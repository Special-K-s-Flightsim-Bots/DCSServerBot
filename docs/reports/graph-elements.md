---
parent: Report Framework
nav_order: 30
---

# Graph Elements

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

{: .note }
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
