# Plugin "SlotBlocking"
This is a slot blocking plugin that can be used in several ways (more to come).
Slots can be either blocked by Discord roles (specific planes blocked for Discord Members, other ones blocked for 
Donators for instance), by credit points (see [CreditSystem](../creditsystem/README.md)) that people earn by kills or a specific VIP role
assigned in this plugins configuration.

## Configuration
The slot blocking is configured with a file named config/plugins/slotblocking.yaml. 
You'll find a sample file in the samples directory:
```yaml
DEFAULT:            # Default section - true for all your servers.
  VIP:              # define a VIP group
    audit: true     # you want to be informed, if someone of that group enters your server
    discord:        # you can define VIPs by a specific discord role
    - Admin
    - DCS Admin
    ucid:           # and a list of UCIDs (can be a single one also)
      - aabbccddeeffgghh
      - 11aa22bb33dd44ee
    slots: 2        # Optional: These number of slots are locked for VIPs only 
  restricted:       # These slots are restricted in any way. Here we restrict CA slots for Donators or members or the Discord.
  - unit_type: artillery_commander
    discord: Donators
    message: This slot is reserved for Donators!
  - unit_type: forward_observer
    discord: Donators
    message: This slot is reserved for Donators!
  - unit_type: instructor
    discord: Donators
    message: This slot is reserved for Donators!
  - unit_type: observer
    discord: '@everyone'  # Only the "everyone" role needs the @ at the beginning, all other roles don't.
    message: This slot is reserved for members of http://invite.link!
DCS.openbeta_server:
  restricted:             # in this example we restrict by credit points
  - group_name: Rookie    # this tag has to be in the group name of the respective units (best is to prepend it)
    points: 10            # you need at least 10 credit points to join this unit
    costs: 10             # the unit will cost you 10 points, depending on the reservations (see below)
  - group_name: Veteran
    points: 20
    costs: 10
  - group_name: Ace
    points: 50
    costs: 30
  use_reservations: true  # If true, you will only lose "costs" credits, if you don't return the plane safely home (landing).
```
Each unit can be either defined by its "group_name" or "unit_name", which are substrings/[pattern](https://riptutorial.com/lua/example/20315/lua-pattern-matching) of the names 
used in your mission or by its "unit_type". The restriction can either be credit "points" that you gain by kills or 
"discord", which is then a specific Discord role (in the example "Donators"). "costs" are the points you lose when you 
get killed in this specific aircraft and if provided.

## Sample Use Case
Here are some sample use cases that show how the plugin can be used.
### One Life per User 
You die, you can't hop in again.

slotblocking.yaml:
```yaml
restricted:
  - group_name: ".+"  # true for each unit / group 
    points:  1        # you need 1 credit point to enter
    costs: 1          # you'll immediately lose 1 credit point on entering
    message: You ran out of lifes.
```

creditsystem.yaml:
```yaml
initial_points: 1   # you get one initial credit point (your "lifes")
points_per_kill:
  - default: 0      # you don't get new lifes by kills
```

### One Life per User (get new lives per pvp kills)
slotblocking.yaml:
```yaml
restricted:
  - group_name: ".+"  # true for each unit / group 
    points:  1        # you need 1 credit point to enter
    costs: 1          # you'll immediately lose 1 credit point on entering
    message: You ran out of lifes.
```
creditsystem.yaml:
```yaml
initial_points: 1     # you get one initial credit point (your "lifes")
points_per_kill:
  - default: 0        # you don't get new lifes by kills
  - category: Planes  # you get one point (aka one additional life), if you kill another player
    type: Player
    points: 1
```

### One Life per User (hard version)
Life will be taken if you hop in your plane already. You get it back, if you land properly on any airport, only then
you can select another slot.<p>
slotblocking.yaml:
```yaml
use_reservations: true  # this enables the reservation system
restricted:
  - group_name: ".+"    # true for each unit / group 
    points:  1          # you need 1 credit point to enter
    costs: 1            # you'll immediately lose 1 credit point on entering
    message: You ran out of lifes.
```
creditsystem.yaml:
```yaml
initial_points: 1   # you get one initial credit point (your "lifes")
points_per_kill:
  - default: 0      # you don't get new lifes by kills
```
