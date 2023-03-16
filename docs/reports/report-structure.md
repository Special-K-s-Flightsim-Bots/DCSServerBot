---
parent: Report Framework
nav_order: 0
---

# General Report Structure
Every report results in an Embed in Discord.

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

## Input Section

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

## Pagination Section

Only needed for PaginationReports (see Report Types).

## Elements Section

The "elements" section contains the real data that you want to present with your report.<br/>
You can either use pre-defined elements or write your own element by inheritance of one of the base classes provided by the framework.
