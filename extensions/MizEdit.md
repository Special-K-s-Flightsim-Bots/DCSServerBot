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

> If you want to lookup the presets used in DCS, you can take a look at 
> `C:\Program Files\Eagle Dynamics\DCS World\Config\Effects\clouds.lua`.

### config/presets.yaml
As you usually want to re-use your presets, they are bundled together in a larger configuration file. Each preset has
a name. Presets can be chained to create a combination of presets as a separate preset.

> You can create any other file named presets*.yaml to better structure your presets.
> If you want to use presets from another yaml file, you can specify that in your MizEdit-Extension.
> You can mix several presets files by specifying them as a list (see example below).

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
nested or even dependent on another parts of your mission file. This is for instance true, if you want to change
frequencies, TACAN codes or similar items.
Therefore, I developed some SQL-like query language, where you can search and change values in your mission.

To use the "modify"-Preset, you need to understand some of the concepts first. 
As we need to "navigate" all around the mission file inside your miz file, we need some kind of path description first,
that helps us to find the respective elements that we want to change:

| Character | Description                                                                             |
|-----------|-----------------------------------------------------------------------------------------|
| /node     | Select this element from the datastructure at this point.                               |
| *         | Walk over all elements in a list or table.                                              |
| $         | Whatever comes after this is evaluated as Python code.                                  |
| \[x\]     | Select the n-th element from a list (starts with 1) or a specific element from a table. |
| \[x,y\]   | Selects these elements from a list (starts with 1) or from a table.                     |
| '{xx}'    | Replace with the variable value of xx ('...' needed, if xx is a string.                 |


Let me show you an example:
```yaml
MyFancyPreset:
  modify:
    for-each: coalition/blue/country/*/ship/group/*/units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
```
This selects some carriers from your blue coalition. Now lets write the same thing a bit different:
```yaml
MyFancyPreset:
  modify:
    for-each: coalition/blue/country/*/ship/group/*
    where: units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
```
In theory, this does the very same. It processes over some carriers on the blue coalition. The difference is that
the "reference" element, meaning the element on which we will work in a bit, is a carrier unit in the first example
and all groups **containing** any of the carriers in the second example.

Now lets see, why we might need that difference:
```yaml
MyFancyPreset:
  modify:
    for-each: coalition/blue/country/*/ship/group/*
    where: units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
    select: route/points/*/task/params/tasks/$'{id}' == 'WrappedAction'/params/action/$'{id}' == 'ActivateBeacon'/params
```
So this does the following:
- Find any blue group ... 
- ... that contains a carrier of the listed type.
- Then select all task parameters of that group (!) where the task id == "WrappedAction", and the action id is "ActivateBeacon".

And now, we can work on these task parameters like so:
```yaml
MyFancyPreset:
  modify:
    for-each: coalition/blue/country/*/ship/group/*
    where: units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
    select: route/points/*/task/params/tasks/$'{id}' == 'WrappedAction'/params/action/$'{id}' == 'ActivateBeacon'/params
    replace:
      modeChannel: X
      channel: $'{reference[units][0][type]}'[-2:]
      frequency:
        $'{reference[units][0][type]}'[-2:] == '72': 1158000000
        $'{reference[units][0][type]}'[-2:] == '73': 1160000000
```
So this replaces the "modeChannel" parameter with "X". Then, we replace the channel using a built-in variable 
"reference", which in our case points to one of each "group" returned by the for-each statement.
Inside of this group, we select the units, the first one of them with [0], its type, which is one of CVN_71 .. 75.
Then we cut out the last 2 characters from that type name, which is the carrier number (71 .. 75). Then we set this as
our TACAN channel.
Next we set the frequency. We are using one of the possible ways of doing it - a list of options, where only one is true
at a time. In our case, we calculate the carrier number again (like above) and then match it with one of the possible 
numbers (I was lazy and only added 2 carrier types 72 and 73 in here). Then we select the respective frequency for that
specific carrier.

If we now look at CVN_73 for instance, this will be the result:
```lua
["params"] = 
{
    ["modeChannel"] = "X"
    ["channel"] = "73"
    ["frequency"] = 1160000000
}
```

Besides "replace", you can also use: 
- delete: delete something from your mission, a unit type for instance, random failures, a whole coalition, etc.
- merge: merge two parts of your mission file, like blue and neutral countries to create a new blue. 

Sometimes it might be necessary to use some variables ({xxx}) inside your code. Some are preset already, like 
{reference} or one of the results of the selected element, some can be set on your own like so:
```yaml
MyFancyPreset:
  modify:
    variables:
      theatre: theatre                          # fills the missions theatre into the {theatre} variable
      temperature: weather/season/temperature   # fills the mission temperature in the {temperature} variable
      rand: '$random.randint(1, 10)'            # fills some random number between 1 and 10 into ${rand}
      mylist: '$list(range(1, {rand}))'         # creates a list ${mylist} of numbers starting from 1 to the result of the random pick above
```
You can work with these variables then later on, to for instance create some randomness in your mission.




#### Example 1: Search all CVN carriers in your mission:
> coalition/[blue,red]/country/*/ship/group/*/units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']

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
   |_ red
      |_ country
         |_ ... all countries ...
            |_ ship
               |_ group
                   |_ ... all groups ...
                       |_ units
                             |_ elements where ["type"] is one of ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
```

#### Example 2a: Change the carrier's frequency for the blue coalition to 3 + carrier type + 000000 (w. g. CVN-71 => 371000000)
```yaml
MyFancyPreset:
  modify:
    for-each: coalition/blue/country/*/ship/group/*/units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
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
      channel: $'{reference[units][0][type]}'[-2:]
      frequency:
        $'{reference[units][0][type]}'[-2:] == '72': 1158000000
        $'{reference[units][0][type]}'[-2:] == '73': 1160000000
```

#### Example 4: Set the 1st radio-preset of all blue F-14Bs to 243
```yaml
ChangeRadios:
  modify:
  - for-each: coalition/blue/country/*/plane/group/*/units/$'{type}' in ['F-14B']
    select: Radio/[1]/channels
    replace:
      1: 243
    insert:
      Radio:
        - channels:
            - 243
```

#### Example 5: Delete all Hornets from your mission
```yaml
DeleteAllHornets:
  modify:
    for-each: coalition/[blue,red]/country/*/plane/group/*/units
    delete: $'{type}' == 'FA-18C_hornet'
```

## Usage
MizEdit is used like any other extension. It is added to your nodes.yaml and configured through it.
Again, you have multiple options on how you want your missions to be changed:

a) Changes, based on the local server time
```yaml
        MizEdit:
          presets: 
            - config/presets.yaml         # default
            - config/presets_weather.yaml # own preset, will be merged with the default one
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

d) Map-specific Settings
```yaml
        MizEdit:
          theatre:
            Caucasus:
              settings:
              - Winter, Morning, Slight Breeze, Halo
            Syria:
              settings:
              - Spring, Morning, Rainy, Halo
```
