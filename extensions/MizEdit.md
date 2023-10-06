# MizEdit

One of the main concepts of DCSServerBot is to have you use any standard mission that you built or got from the
community and do whatever you like with it, without the need of you changing anything in the mission itself.
This works well for SlotBlocking already or the CreditSystem, but you can even go further and really amend the mission
without touching it.

Sounds like magic? Well, it kinda is.

## Concept
The whole concept behind MizEdit is, that your mission consists out of several files that are bundled together in a 
zipped file that ED decided to give the ".miz" extension. One of these files, the `mission`-file is a large lua table
that holds the main information about your mission, like the theatre, the date and time, the weather, all units, 
triggers, the majority of settings and whatnot.<br>
The Mission Editor writes and changes this file to represent any change that you made to your mission.
So - why not do that on our own, without the need of the editor?

## Presets
Each mission change is represented in a small data-structure in yaml. I've called these "presets", as they usually will
work as a fixed setting for any mission you have, like any weather preset you know already from the recent DCS versions.

### config/presets.yaml
As you usually want to re-use your presets, they are bundled together in a larger configuration file. Each preset has
a name. Presets can be chained to create a combination of presets as a separate preset.

#### a) Simple Presets
```yaml
Spring:
  date: '2016-04-21'
  temperature: 8
Summer:
  date: '2016-07-21'
  temperature: 23
Autumn:
  date: '2016-10-21'
  temperature: 10
Winter:
  date: '2016-12-21'
  temperature: -10
Nighttime:
  start_time: 03:00
Morning:
  start_time: 08:00
Noon:
  start_time: 12:00
Evening:
  start_time: 18:00
Late Evening:
  start_time: 22:00
Casual Weather:
- Summer
- Morning
```
In this case the preset "Casual Weather" will be the same as if you apply both, "Summer" and "Morning" to the mission.

With this method, you can change the following values in your mission (to be extended):
* start_time
* date
* temperature
* atmosphere_type
* wind (including sub-structures like listed in your mission file)
* groundTurbulence
* enable_dust
* dust_density
* qnh
* clouds (including sub-structures like listed in your mission file)
* enable_fog
* fog
* halo (including sub-structures like listed in your mission file)
* requiredModules (set this to [] to remove any module requirements from your mission)
* accidental_failures (set this to false, to remove any failures from your mission)
* forcedOptions (force any mission option)
* miscellaneous (set any miscellaneous option)
* difficulty (set any difficulty option)

I highly recommend looking at a mission or options file inside your miz-file to see the structure of these settings.

#### b) Attaching Files
If you want to attach files to your mission (e. g. sounds but others like scripts, etc.), you can do it like this:
```yaml
Sounds:
  files:
  - sounds/alarm.ogg
  - sounds/beep.ogg
```
This will create a new preset "Sounds", that - if applied - copies the two ogg files into your i10n directory of your
miz-file. The path of these files is relative to your DCSServerBot installation directory.

#### c) Complex Modifications
Sometimes, only changing the weather is not enough and you want to change some parts in the mission that are deeply 
nested or even dependent on another part of your mission file. This is for instance true, if you want to change
frequencies, TACAN codes or similar items.
Therefore, I developed some SQL-like query language, where you can search and change values in your mission.

You can use these special characters:

| Character | Description                                                             |
|-----------|-------------------------------------------------------------------------|
| /node     | Select this element from the datastructure at this point.               |
| *         | Walk over all elements in a list.                                       |
| $         | Whatever comes after this is evaluated as Python code.                  |
| '{xx}'    | Replace with the variable value of xx ('...' needed, if xx is a string. |

#### Example 1: Search all CVN carriers in your mission:
> coalition/blue/country/*/ship/group/*/units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']

will walk the mission tree like so:
```
|_ coalition
   |_ blue
      |_ country
         |_ ... all countries ...
            |_ ship
               |_ group
                   |_ ... all groups ...
                       |_ units
                             |_ elements where ["type"] is one of ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
```

#### Example 2a: Change the carrier's frequency to 3 + carrier type + 000000 (w. g. CVN-71 => 371000000)
```yaml
MyFancyPreset:
  modify:
  - for-each: coalition/blue/country/*/ship/group/*/units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
    replace:
      frequency: 
        "$'{type}' == 'CVN_71'": 371000000
        "$'{type}' == 'CVN_72'": 372000000
        "$'{type}' == 'CVN_73'": 373000000
        "$'{type}' == 'CVN_74'": 374000000
        "$'{type}' == 'CVN_75'": 375000000
```

#### Example 2b: Shorter version of the above, using a Python calculation
```yaml
MyFancyPreset:
  modify:
  - for-each: coalition/blue/country/*/ship/group/*/units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
    replace: 
      frequency: $int('3' + '{type}'[-2:] + '000000')
```

#### Example 3: Changing the TACAN Frequency
This is more complex, as we either need to search the carrier but to change the TACAN, we need to change some different
structure in the mission file.
```yaml
MyFancyPreset:
  modify:
  - for-each: coalition/blue/country/*/ship/group/*
    where: units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
    select: route/points/*/task/params/tasks/$'{id}' == 'WrappedAction'/params/action/$'{id}' == 'ActivateBeacon'/params
    replace:
      modeChannel: X
      channel: $'{where[type]}'[-2:]
      frequency:
        $'{where[type]}'[-2:] == '72': 1158000000
        $'{where[type]}'[-2:] == '73': 1160000000
```


## Usage
MizEdit is used like any other extension. It is added to your nodes.yaml and configured through it.
Again, you have multiple options on how you want your missions to be changed:

a) Changes, based on the local server time
```yaml
        MizEdit:
          settings:
            00:01-06:00: Spring, Morning, Rainy, Halo
            06:01-12:00: Summer, Morning, Slight Breeze, Halo
            12:01-18:00: Autumn, Morning, Heavy Storm, Halo
            18:01-00:00: Winter, Morning, Slight Breeze, Halo
```

b) Random choice of fixed settings
```yaml
        MizEdit:
          settings:
          - Spring, Morning, Rainy, Halo
          - Summer, Morning, Slight Breeze, Halo
          - Autumn, Morning, Heavy Storm, Halo
          - Winter, Morning, Slight Breeze, Halo
```

c) Permutations
```yaml
        MizEdit:
          settings:
            00:00-12:00:  # Any permutation out of [Spring, Summer] + [Morning, Noon] + [Slight Breeze, Rainy, Heavy Storm]
            - - Spring
              - Summer
              - Autumn
              - Winter
            - - Morning
              - Noon
            - - Slight Breeze
              - Rainy
              - Heavy Storm
            12:00-24:00:
            - - Autumn
              - Winter
            - - Evening
              - Nighttime
            - - Slight Breeze
              - Rainy
              - Heavy Storm
```
