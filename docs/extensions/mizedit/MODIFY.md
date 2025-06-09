---
layout: default
title: MODIFY
nav_section: extensions/mizedit
---

# Using the "modify"-Preset
To use the "modify"-Preset, you need to understand some of the basic concepts first. A DCS miz file, that carries your
mission is basically a ZIP file, consisting of several configuration files inside. MizEdit can amend these files and
with that the whole mission. Now, lets take a look at these files and talk about the purpose of them.

### mission
This file is the main file inside the miz file. It holds your units, their waypoints (routes), time and weather
information of the mission, any mission triggers and basic mission options.

### options
This file holds additional options, and can be seen as an overwrite for your Saved Games\DCS.server\Config\options.lua 
file.

### warehouses
This file holds information about airports, especially their warehouse information but also information about dynamic
spawns.

All these files are basically lua tables. To change a mission, you unpack it, amend these files and repack it again to
a new miz file. That's exactly what MizEdit is doing.

## Lua Table Navigation
As we need to "navigate" all around the basic lua files inside your miz file, we need some kind of path description 
first, that helps us find the respective elements that we want to change:

| Character | Description                                                                             |
|-----------|-----------------------------------------------------------------------------------------|
| /node     | Select this element from the datastructure at this point.                               |
| *         | Walk over all elements in a list or table.                                              |
| $         | Whatever comes after this is evaluated as Python code.                                  |
| \[x\]     | Select the n-th element from a list (starts with 1) or a specific element from a table. |
| \[x,y\]   | Selects these elements from a list (starts with 1) or from a table.                     |
| '{xx}'    | Replace with the variable value of xx ('...' needed, if xx is a string.                 |


### Finding Elements
To do so, we use the "for-each" keyword, which selects every element that fits a specific expression. We can use two
additional keywords "select" and "where" to navigate more precisely through the lua structure.

Let me show you two examples:
```yaml
MyFancyPreset:
  modify:
    file: mission  # default, can be any of "mission", "options" or "warehouses", see above
    for-each: coalition/blue/country/*/ship/group/*/units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
```
This selects some carriers from your blue coalition. 

Now let's write the same thing a bit different:
```yaml
MyFancyPreset:
  modify:
    file: mission
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
    file: mission
    for-each: coalition/blue/country/*/ship/group/*
    where: units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
    select: route/points/*/task/params/tasks/$'{id}' == 'WrappedAction'/params/action/$'{id}' == 'ActivateBeacon'/params
```
This does the following:
- Find any blue group ... 
- ... that contains a carrier of the listed type.
- Then select all task parameters of that group (!) where the task id == "WrappedAction", and the action id is "ActivateBeacon".

## Changing Elements
Now that we can find elements, we need to do something with them. We can either delete them, amend them or even insert
new elements.

```yaml
MyFancyPreset:
  modify:
    file: mission
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
The "modeChannel" parameter is replaced with "X". Next, the channel is updated using the built-in variable "reference," 
which, in this context, points to each "group" returned by the for-each statement.
Within each group, we locate the units and select the first one using `[0]`. From this unit, we retrieve its type, 
which corresponds to one of the carrier types (CVN_71 to CVN_75). We then extract the last two characters from the type 
name, representing the carrier number (e.g., 71 to 75). This number is then assigned as the TACAN channel.<br>

The following step involves setting the frequency. For this, we use a list of possible frequency options, ensuring 
that only one option is valid at a time. To determine the correct frequency, we recalculate the carrier number
(as described above) and match it with one from the predefined list of carrier types. In this example, only two carrier 
types (72 and 73) are included. The corresponding frequency is then selected based on the matched carrier type.

If we now look at CVN_73 for instance, this will be the result:
```lua
["params"] = 
{
    ["modeChannel"] = "X"
    ["channel"] = "73"
    ["frequency"] = 1160000000
}
```

> [!NOTE]
> Besides "replace", you can also use: 
> - delete: delete something from your mission, a unit type for instance, random failures, a whole coalition, etc.
> - merge: merge two parts of your mission file, like blue and neutral countries to create a new blue. 

## Variables
Sometimes it might be necessary to use variables inside your code. Some are preset already, like 
{reference} or one of the results of the selected element, some can be set on your own like so:
```yaml
MyFancyPreset:
  modify:
    variables:
      theatre: theatre                          # fills the missions theatre into the {theatre} variable
      temperature: weather/season/temperature   # fills the mission temperature in the {temperature} variable
      speed: 40                                 # sets a fixed value for speed
      rand: '$random.randint(1, 10)'            # fills some random number between 1 and 10 into ${rand}
      mylist: '$list(range(1, {rand}))'         # creates a list ${mylist} of numbers starting from 1 to the result of the random pick above
```
You can work with these variables then later on, to for instance create some randomness in your mission. To use a 
variable, just add `{variablename}` in your code.

## Conditions
If you only want to run the script under specific conditions, you can add an "if"-condition like so:
```yaml
MyFancyPreset:
  modify:
    variable:
      start_time: start_time
      theatre: theatre
    if: ${start_time} > 20000 and '{theatre}' == 'Caucasus'
    # ... continue ...
```


## Running Python Code for complex changes
You can run a specific python function on the result of a for-each call:
```yaml
relocate_carrier:
    modify:
        variables:
            wind: weather/wind/atGround
        for-each: coalition/*/country/*/ship/group/*
        where: units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75', "Stennis", "LHA_Tarawa"]
        run: core.utils.mizedit.relocate_carrier
```

This will call a function that takes specific parameters:

| Parameter | Definition             |
|-----------|------------------------|
| data      | The selected element.  |
| reference | The reference element. |
| kwargs    | Variables              |

```python
def my_function(data: dict, reference: dict, **kwargs) -> dict:
    ...
```

In the above example, it will call a function to relocate the carriers in a mission to allow a proper recovery.

## Examples
Here are some examples that you can copy and amend to your needs.

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
    file: mission
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
    file: mission  
    for-each: coalition/blue/country/*/ship/group/*/units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
    replace: 
      frequency: $int('3' + '{type}'[-2:] + '000000')
```

#### Example 3: Changing the TACAN Frequency
This is more complex, as we either need to search the carrier but to change the TACAN, we need to change some different
structure in the mission file.
```yaml
MyFancyPreset:
  modify:
    file: mission
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

#### Example 4: Set the 1st radio-preset of all blue F-14Bs to 243
```yaml
ChangeRadios:
  modify:
    file: mission
    for-each: coalition/blue/country/*/plane/group/*/units/$'{type}' in ['F-14B']
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
    file: mission
    for-each: coalition/[blue,red]/country/*/plane/group/*/units
    delete: $'{type}' == 'FA-18C_hornet'
```

#### Example 6: Change all blue warehouses to dynamic cargo
```yaml
EnableDynamicCargo:
  modify:
    file: warehouses
    debug: true
    for-each: airports/*/$'{coalition}' == 'BLUE'
    replace:
      dynamicCargo: true
```

