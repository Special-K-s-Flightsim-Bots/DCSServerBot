# Plugin "SlotBlocking"
This plugin is designed for slot blocking and offers multiple usage options. 
Slots can be restricted based on Discord roles (for instance, restricting certain planes for regular Discord members 
while blocking others for donators), credit points (as detailed in the [CreditSystem](../creditsystem/README.md) document), or a specific 
VIP role set within this plugin's configuration.
It also includes a balance system for both teams (red and blue), ensuring fair gameplay by maintaining an equal 
distribution of players on each side.

## Configuration
As SlotBlocking is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - slotblocking
```

The plugin itself is configured with a file named config/plugins/slotblocking.yaml. 
You'll find a sample file in the ./samples directory:
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
    message_server_full: The server is full, please try again later!  # default message, if the server is considered full
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
    side: 2               # side 1 = red, 2 = blue, not given - both sides
    discord: '@everyone'  # Only the "everyone" role needs the @ at the beginning, all other roles don't.
    message: This slot is reserved for members of https://invite.link!
  - unit_type: dynamic    # restrict the usage of dynamic slots in general for a specific Discord role
    discord: Tester
    message: Dynamic Slots are restricted for Testers only.
  balancing:                  # Optional: Allows balancing for your server (blue vs red)
    blue_vs_red: 0.5          # 50% balance blue vs red
    threshold: 0.1            # 10% threshold until slots are blocked
    activation_threshold: 10  # do not balance, if the number of players is below this threshold
    message: You need to take a slot of the opposite coalition to keep the balance!
  messages:
    credits_taken: '{deposit} credits taken for using a reserved module.' # Possible variables: deposit, old_points, new_points
    payback: 'You have been given {deposit} credits back.'                # Possible variables: deposit, old_points, new_points
DCS.dcs_serverrelease:
  restricted:             # in this example we restrict by credit points
  - group_name: Rookie    # this tag has to be in the group name of the respective units (best is to prepend it)
    points: 10            # you need at least 10 credit points to join this unit
    costs: 10             # the unit will cost you 10 points, depending on the payback (see below)
  - group_name: Veteran
    points: 20
    costs: 10
  - group_name: Ace
    points: 50
    costs: 30
  payback: true         # payback the plane costs on proper landings, otherwise charge by usage
```
Each unit can be identified using either its "group_name" or "unit_name", which are substrings or [patterns](https://riptutorial.com/lua/example/20315/lua-pattern-matching) of the 
names used in your mission, or by its "unit_type". 
Restrictions can be enforced through credit "points" earned from kills or "discord" (specific Discord roles, 
such as "Donators" in the example provided). 
The "costs" refer to the points you lose when you are killed while flying this specific aircraft, if applicable.

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
payback: true           # you get the costs back on landing
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

### Balancing
If you have a PvP server and want to enable balancing, this is how you can set it up.
```yaml
DEFAULT:
  balancing:                  # Optional: Allows balancing for your server (blue vs red)
    blue_vs_red: 0.5          # 50% balance blue vs red
    threshold: 0.1            # 10% threshold until slots are blocked
    activation_threshold: 10  # do not balance if the number of players is below this threshold
    message: You need to take a slot of the opposite coalition to keep the balance!
``` 
> [!NOTE]
> Balancing will **not** be checked
> - if a user selects another slot on the same side (if you are on blue, you can stay on blue)
> - if a user jumps in a CA (Artillery Commander, etc.) or carrier slot (LSO, Airboss)
