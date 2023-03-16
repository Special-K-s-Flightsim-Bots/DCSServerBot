---
title: Report Framework
has_children: true
nav_order: 70
---

# Report Framework

One of the main goals of DCSServerBot is gathering data of your DCS World servers and display them in a useful format.

To achieve this, DCSServerBot already comes with some built-in reports. Many plugins display simple to complex data, which I thought, might be of interest.

To allow you to change the look and feel of existing reports and to make it easier to build your own, I've developed a JSON-based reporting framework.
Here you'll find the main features and elements of this framework.

## Variables

You usually work with variables that you pass to the corresponding `render()` call or that you define in the `"input"` section.
These can be dictionaries like server- or player-data or just single values like server_name.
To use them in your reports, expect all strings to be f-string capable:

```json
{
  "title": "Report for Server {server_name}",
  "description": "Player {player[name]} is causing trouble."
}
```

Be aware that `player['name']` is written as `player[name]` in the reports!
