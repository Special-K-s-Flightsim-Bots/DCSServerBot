# Mission presets

A preset is a set of changes applied to a mission — start time, date, weather, clouds. Presets live
in `config/presets.yaml`:

```yaml
# config/presets.yaml
Spring:
  date: '2016-04-21'
  temperature: 8
Summer:
  date: '2016-07-21'
  temperature: 23
Nighttime:
  start_time: 03:00
```

Each top-level key is one preset, and a mission can carry several at once (start time *and* weather).

## Applying a preset

`/mission modify` applies a preset to the mission that is running, and `/mission load` can load a
mission with a preset applied. Both use the machinery described in
[Mission files](mission-files.md): the mission is copied, changed and loaded from `.dcssb/`.

!!! note
    A preset is applied when the mission is loaded **with extensions enabled** — `run_extensions` is
    on by default for the modify and load commands. The rewriting itself is done by the **MizEdit**
    extension; without it installed and configured, the mission loads unchanged. See
    [MizEdit](../extensions/mizedit.md) for installation and for the preset types it understands.

## Weather

For weather presets, DCS itself is the reference: the presets in `samples/presets.yaml` carry the
field names DCS expects, and `Config/Effects/clouds.lua` in the DCS installation lists the cloud
presets you can name.
