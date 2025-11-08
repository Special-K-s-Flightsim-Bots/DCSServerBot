# Extension "MizEdit"

One of the primary features of DCSServerBot is its ability to allow users to use any standard missions, 
either self-created or obtained from the community, while having complete control over them without modifying the 
mission itself. 
This flexibility applies to SlotBlocking and CreditSystem configurations, but users can also make more extensive 
modifications to the missions itself as desired without having to open the MissionEditor at all.

Sounds like magic? Well, it kinda is.

## Concept
DCS World organizes a mission as multiple files within a zipped archive with the ".miz" extension, one of which is the 
**mission** file - a large Lua table containing essential information about the mission such as the theater, date and time, 
weather, units, triggers, settings, and more. 
The **options** file stores parts of the mission configuration, while the **warehouses** file contains details about 
the dedicated airports and their warehouses. 
By using the Mission Editor to write and modify these files, any changes made to the mission are accurately represented. 
However, the question is: why not accomplish this directly, without relying on the editor?

## Presets
Each modification made to a mission is stored in a compact YAML data structure that I've named "presets". 
These presets typically function as consistent settings that can be applied to any mission, similar to weather presets 
familiar from recent DCS World versions.

> [!NOTE]
> If you want to look up the weather-presets used in DCS World, you can take a look at 
> `C:\Program Files\Eagle Dynamics\DCS World\Config\Effects\clouds.lua`.

### config/presets.yaml
Since presets are meant for frequent use, they are organized within a larger configuration file. 
Each preset can be given a unique name, and multiple presets can be combined to create new presets by linking them 
together.

> [!TIP]
> You can create any other file named "presets*.yaml" to better structure your presets.
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
* accidental_failures (set this to `false` to remove any failures from your mission)
* forcedOptions (force any mission option)
* miscellaneous (set any miscellaneous option)
* difficulty (set any difficulty option)

> [!NOTE]
> I highly recommend looking at a mission or options file inside your miz-file to see the structure of these settings.

date has different options:
* date: '2022-05-31' # normal date
* date: 'today'      # one of today, yesterday, tomorrow

start_time has different options:
* start_time: 28800            # seconds since midnight (here: 08:00h)
* start_time: '08:00'          # fixed time
* start_time: 'noon'           # one of now, dawn, sunrise, morning, noon, evening, sunset, dusk, night as a moment in time based on the current map / date
* start_time: 'morning +02:00' # relative time to one of the above-mentioned moments

> [!NOTE]
> The moments are calculated based on the current theater and date. 
> If you change the date through MizEdit, you need to set that prior to the start_time!
> 
> Thanks, @davidp57 for contributing the moments-part!

#### b) Attaching Files
If you want to attach files to your mission (e.g. sounds or others like scripts, etc.), you can do it like this:
```yaml
Sounds:
  files:
  - sounds/alarm.ogg
  - sounds/beep.ogg
```
This will create a new preset "Sounds", that - if applied - copies the two ogg files into your l10n/DEFAULT directory 
of your miz-file. The path of these files is relative to your DCSServerBot installation directory.

If you want to add files to a specific directory, you can do it like so:
```yaml
AddFiles:
  files:
    - source: sounds/alarm.ogg    # upload a single file to the target dir inside of the mission
      target: l10n/EN 
    - source: kneeboards          # upload the whole file structure from this directory into the mission
      target: KNEEBOARDS          # at this place
```

#### c) Fog
Starting with DCS 2.9.10, Eagle Dynamics added a new fog system, which allows fog animations, based on time. 
You can use this new feature with the bot like so:
```yaml
auto_fog:   # let DCS to the fog on its own
    fog:
        mode: auto
manual_fog: # set a manual fog animation
    fog:
        mode: manual
        0: {"thickness": 100, "visibility": 1000}
        300: {"thickness": 200, "visibility": 2000}
        600: {"thickness": 250, "visibility": 2500}
        900: {"thickness": 100, "visibility": 500}
        1200: {"thickness": 0, "visibility": 0}
```
The key is the time in seconds after which the specific thickness and visibility should appear. DCS will then animate
the fog changes in-between for you.

#### d) DCS RealWeather
You can run DCS RealWeather from MizEdit like so:
```yaml
realweather:
    RealWeather:
        options:
            weather:
                icao: UGKO
```

#### e) Complex Modifications
In certain instances, modifying only the weather may not suffice, as there may be parts of the mission that are deeply 
nested or dependent on other elements within the mission file. 
For example, adjusting frequencies, TACAN codes, or similar items can require direct access to specific areas of the 
mission data. 
To address this issue, I developed a SQL-like query language capable of searching and modifying values in the mission 
file.

> [!NOTE]
> As this is complex and very (!) powerful, I decided to move the documentation in a separate file [here](MODIFY.md).

## Usage
MizEdit is used like any other extension. It is added to your nodes.yaml and configured through it.
Again, you have multiple options on how you want your missions to be changed:

a) Changes, based on the local server time
```yaml
        MizEdit:
          debug: true                     # Optional: enable debug logging for "modify"
          presets: 
            - config/presets.yaml         # default
            - config/presets_weather.yaml # own preset - will be merged with the default one
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
          timezone: UTC # optional - provide a timezone for the time values
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
          terrains:
            Caucasus:
              settings:
              - Winter, Morning, Slight Breeze, Halo
            Syria:
              settings:
              - Spring, Morning, Rainy, Halo
```

e) Mission filter
```yaml
        MizEdit:
          filter: MyFancy*  # apply to all missions that start with "MyFancy" in their name
          settings:
          - Spring, Morning, Rainy, Halo
          - Summer, Morning, Slight Breeze, Halo
          - Autumn, Morning, Heavy Storm, Halo
          - Winter, Morning, Slight Breeze, Halo
```