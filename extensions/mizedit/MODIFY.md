# Using the "modify"-Preset

MizEdit's `modify` DSL (Domain Specific Language) lets you search and modify Lua tables inside DCS `.miz` files.

A `.miz` file is a ZIP archive containing three Lua table files:

| File | Contents                                                             |
|------|----------------------------------------------------------------------|
| `mission` | Units, routes, weather, triggers, coalition data — the main mission  |
| `options` | Mission options (overrides the player's `options.lua`)               |
| `warehouses` | Airport warehouse data and dynamic spawn configuration               |

MizEdit unpacks the `.miz`, amends these Lua tables, and repacks into a new `.miz` file.

---

## DSL Overview

Every `modify` block follows this structure:

```yaml
PresetName:
  modify:
    file: mission          # which Lua file to modify: mission | options | warehouses
    variables: ...         # optional: extract or define variables
    if: ...                # optional: condition that must be true
    for-each: <path>       # required: path to elements to iterate
    where: ...             # optional: filter on the reference element
    select: <path>         # optional: navigate deeper to the element to change
    replace: ...           # modify existing values
    delete: ...            # remove elements
    insert: ...            # add new keys/values
    merge: ...             # merge two Lua tables
    run: <python.func>     # call a Python function for complex logic
    debug: true            # optional: verbose logging
```

---

## Path Navigation

Paths navigate the Lua table tree from root to leaf:

| Syntax | Meaning                                                                                                       |
|--------|---------------------------------------------------------------------------------------------------------------|
| `/node` | Descend into a named key                                                                                      |
| `*` | Iterate all elements in a list or table                                                                       |
| `[x]` | Select the n-th element from a **Lua list** (1-based) or a specific key from a table                          |
| `[x,y]` | Select multiple elements                                                                                      |
| `$'...'` | Evaluate the content as a **Python expression** (returns a boolean for filtering, or a value for replacement) |
| `'{var}'` | Substitute a previously defined variable's value                                                              |

**Important on indexing:** Lua lists are 1-based, so `[1]` is the first element. However, when accessing data via Python 
expressions (inside `$'...'` or `reference`), use Python 0-based indexing: `reference[units][0]`.

---

## Finding Elements

### `for-each` — sets the iteration and the reference element

`for-each` defines which elements to iterate over. The matched element becomes the **reference element** — the context you work with.

```yaml
for-each: coalition/blue/country/*/ship/group/*/units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
```

This walks the mission tree and selects every individual carrier **unit** matching the type list. The reference element is each unit.

### `where` — filters without changing the reference

`where` filters which reference elements to process, but does NOT change what the reference points to.

```yaml
for-each: coalition/blue/country/*/ship/group/*
where: units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
```

This iterates all groups, but only processes groups **containing** a matching carrier. The reference element is each **group** (not the unit). This matters when you need to modify group-level data like routes.

**Rule:** Use `for-each` alone when you want to modify the iterated elements directly. Add `where` when you need to filter by a child property but work on the parent.

### `select` — navigates deeper to the target

`select` navigates from the reference element to the specific sub-element to modify. The reference stays the same.

```yaml
for-each: coalition/blue/country/*/ship/group/*
where: units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CN_74','CVN_75']
select: route/points/*/task/params/tasks/$'{id}' == 'WrappedAction'/params/action/$'{id}' == 'ActivateBeacon'/params
```

This finds blue carrier groups, then drills into their route tasks to find TACAN beacon parameters.

---

## Modification Operations

### `replace` — overwrite values

Simple replacement:

```yaml
replace:
  frequency: 371000000
  modeChannel: X
```

**Conditional replacement** (switch/case): When a `replace` value is a mapping, each key is a Python expression. The first expression that evaluates to `True` determines the value:

```yaml
replace:
  frequency:
    $'{reference[units][0][type]}'[-2:] == '72': 1158000000
    $'{reference[units][0][type]}'[-2:] == '73': 1160000000
```

### `delete` — remove elements

```yaml
delete: $'{type}' == 'FA-18C_hornet'
```

### `insert` — add new keys/values

Use `insert` when the target key doesn't exist yet:

```yaml
insert:
  Radio:
    - channels:
        - 243
```

### `merge` — combine Lua tables

Merges two parts of a mission file together, e.g., merging neutral country data into blue:

```yaml
merge:
  source: coalition/neutral
  target: coalition/blue
```

---

## Built-in Variables

The following variables are available in all `modify` blocks:

| Variable | Description                                                                                                                            |
|----------|----------------------------------------------------------------------------------------------------------------------------------------|
| `{reference}` | The current reference element (set by `for-each`). Access properties like `{reference[units][0][type]}`. Uses Python 0-based indexing. |
| `{type}` | The type name of the current element (when iterating units)                                                                            |

---

## Custom Variables

Extract values from the mission or define your own:

```yaml
variables:
  theatre: theatre                          # extract from mission
  temperature: weather/season/temperature   # extract nested value
  speed: 40                                 # fixed value
  rand: '$random.randint(1, 10)'            # Python random
  mylist: '$list(range(1, {rand}))'         # use other variables
```

Reference variables with `{variablename}` in paths and expressions.

---

## Conditions

Run the modification only when a condition is met:

```yaml
variables:
  start_time: start_time
  theatre: theatre
if: ${start_time} > 20000 and '{theatre}' == 'Caucasus'
```

---

## Python Functions

For complex logic, call a Python function:

```yaml
variables:
  wind: weather/wind/atGround
for-each: coalition/*/country/*/ship/group/*
where: units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75', "Stennis", "LHA_Tarawa"]
run: core.utils.mizedit.relocate_carrier
```

The function receives three arguments:

| Parameter | Description            |
|-----------|------------------------|
| `data` | The selected element   |
| `reference` | The reference element  |
| `kwargs` | All defined variables  |

```python
def my_function(data: dict, reference: dict, **kwargs) -> dict:
    # modify data in place or return a replacement dict
    return data
```

See the DCSServerBot source for available built-in functions.

---

## Debugging

Set `debug: true` to enable verbose logging. The log shows which elements were matched and what changes were made. Check the DCSServerBot log file for output.

---

## Examples

### Example 1: Change carrier frequencies (simple replacement)

Select every blue carrier unit and set its frequency based on type:

```yaml
# config/presets.yaml
SetCarrierFreqs:
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

Shorter version using a Python calculation:

```yaml
# config/presets.yaml
SetCarrierFreqs:
  modify:
    file: mission
    for-each: coalition/blue/country/*/ship/group/*/units/$'{type}' in ['CVN_71','CVN_72','CVN_73','CVN_74','CVN_75']
    replace:
      frequency: $int('3' + '{type}'[-2:] + '000000')
```

### Example 2: Change TACAN frequencies (deep navigation with `where` + `select`)

TACAN data is nested inside the group's route, not on the unit itself. We use `for-each` to iterate groups, `where` to filter for carrier groups, and `select` to reach the beacon parameters:

```yaml
# config/presets.yaml
SetTACAN:
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

Here `{reference}` points to the group (from `for-each`), so `{reference[units][0][type]}` gets the first unit's type name. The `[-2:]` slice extracts the carrier number (e.g. "72" from "CVN_72").

### Example 3: Set radio presets (with `insert`)

Set the first radio channel of all blue F-14Bs. Uses `insert` to add the `Radio` key if it doesn't exist:

```yaml
# config/presets.yaml
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

### Example 4: Delete all Hornets (with `delete`)

```yaml
# config/presets.yaml
DeleteAllHornets:
  modify:
    file: mission
    for-each: coalition/[blue,red]/country/*/plane/group/*/units
    delete: $'{type}' == 'FA-18C_hornet'
```

### Example 5: Enable dynamic cargo on blue warehouses (different target file)

```yaml
# config/presets.yaml
EnableDynamicCargo:
  modify:
    file: warehouses
    debug: true
    for-each: airports/*/$'{coalition}' == 'BLUE'
    replace:
      dynamicCargo: true
```

### Example 6: Search path walkthrough

The path `coalition/[blue,red]/country/*/ship/group/*/units/$'{type}' in ['CVN_71'..'CVN_75']` walks the tree:

```
|_ coalition
   |_ blue
      |_ country
         |_ ... all countries ...
            |_ ship
               |_ group
                  |_ ... all groups ...
                      |_ units
                            |_ elements where ["type"] is one of ['CVN_71'..'CVN_75']
   |_ red
      |_ ... same structure ...
```
