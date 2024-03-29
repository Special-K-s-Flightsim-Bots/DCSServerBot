---
title: SlotBlocking
parent: Plugin System
nav_order: 0
---

# Plugin "SlotBlocking"

This is a simple slot blocking plugin that can be used in two different ways (for now, more to come).
Slots can be either blocked by Discord groups (specific planes blocked for Discord Members, other ones blocked for 
Donators for instance) or by credit points (see [CreditSystem]) that people earn by kills. 
So you can hop in another plane, as soon as you have killed a specific number of enemies. Friendly fire or self kills 
are not counted._

## Configuration

The slot blocking is configured with a file named config\slotblocking.json. You'll find a sample file in that directory:

```json
{
  "configs": [
    { -- this is the default section (no server name or instance name provided)
      "VIP": {                  -- VIP slots
        "slots": 2,             -- 2 slots will be blocked for VIP users
        "discord": ["Admin", "DCS Admin"],
        "ucid": ["1234567890abcdefghijklmn"],
        "audit": true           -- if true, a message will be provided in the audit channel, if a VIP joins
      },
      "restricted": [           -- restrictions for CA slots, they can only be used by Discord group "Donators"
        { "unit_type": "artillery_commander", "discord": "Donators" },
        { "unit_type": "forward_observer", "discord": "Donators" },
        { "unit_type": "instructor", "discord": "Donators" },
        { "unit_type": "observer", "discord": "Donators" }
      ]
    },
    { -- this is a server specific section for the instance "DCS.release_server" in this case
      "installation": "DCS.release_server",
      "use_reservations": true, -- if true, a "deposit" will be taken on hop-on and payed out on RTB, otherwise points will be credited on death 
      "restricted": [           -- restriction for specific groups of planes, based on a points system
        { "group_name": "^Rookie", "points": 10, "costs": 10 },
        { "group_name": "^Veteran", "points": 20, "crew": 5, "costs": 10 }, -- a multicrew seat (aka RIO) costs 5 points here
        { "group_name": "^Ace", "points": 50, "costs": 30 },
        { "unit_name": "My Plane", "ucid": "11223344556677889900..." },     -- restriced slot for a specific ucid
        { "unit_name": "^Party Boats", "ucids": ["11223344", "44556677"]}   -- restricted slots for a group of ucids
      ]
    }
  ]
}
```

Each unit can be either defined by its "group_name" or "unit_name", which are substrings/[pattern] of the used names in your mission or by its "unit_type".
The restriction can either be "points" that you gain by kills or "discord", which is then a specific Discord role (in the example "Donators").
"costs" are the points you lose when you get killed in this specific aircraft and if provided.

## Sample Use Case

Here are some sample use cases that show how the plugin can be used.

### One Life per User 

You die, you can't hop in again, `slotblocking.json`:

```json
{
  "configs": [
    {
      "restricted": [
        { "group_name": ".+", "points":  1, "costs": 1, "message": "You ran out of lifes."}
      ]
    }
  ]
}
```

`creditsystem.json`:

```json
{
  "configs": [
    {
      "initial_points": 1,
      "points_per_kill": [
        { "default": 0 }
      ]
    }
  ]
}
```

### One Life per User (get new lives per pvp kills)

`slotblocking.json`:

```json
{
  "configs": [
    {
      "restricted": [
        { "group_name": ".+", "points":  1, "costs": 1, "message": "You ran out of lifes."}
      ]
    }
  ]
}
```

`creditsystem.json`:

```json
{
  "configs": [
    {
      "initial_points": 1,
      "points_per_kill": [
        { "default": 0 },
        { "category": "Planes", "type": "Player", "points": 1 }
      ]
    }
  ]
}
```

### One Life per User (hard version)

Life will be taken if you hop in your plane already. You get it back, if you land properly on another airport, only then
you can select another slot.

`slotblocking.json`:

```json
{
  "configs": [
    {
      "use_reservations": true, 
      "restricted": [
        { "group_name": ".+", "points":  1, "costs": 1, "message": "You ran out of lifes."}
      ]
    }
  ]
}
```

`creditsystem.json`:

```json
{
  "configs": [
    {
      "initial_points": 1,
      "points_per_kill": [
        { "default": 0 }
      ]
    }
  ]
}
```

[CreditSystem]: creditsystem.md
[pattern]: https://riptutorial.com/lua/example/20315/lua-pattern-matching
