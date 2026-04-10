# Plugin "RealWeather"
With this plugin, you can apply real weather supplied by [DCS-real-weather](https://github.com/evogelsa/DCS-real-weather)
to your mission.

## Configuration
As RealWeather is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - realweather
```

You need to point to your DCS-real-weather installation in your nodes.yaml like so:
```yaml
MyNode:
  # [...]
  extensions:
    RealWeather:
      installation: '%USERPROFILE%\Documents\realweather_v2.5.0'
```
This can be used to enable the RealWeather [extension](../../extensions/realweather/README.md) also.

## Discord Commands
| Command         | Parameter                            | Channel | Role       | Description                                                                                    |
|-----------------|--------------------------------------|---------|------------|------------------------------------------------------------------------------------------------|
| /realweather    | server airport [optional parameters] | any     | DCS Admin  | Change the weather and time in the mission to the one that is active at that specific airport. |

## In-Game Chat Commands
| Command      | Parameter      | Role      | Description                                                                                     |
|--------------|----------------|-----------|-------------------------------------------------------------------------------------------------|
| -realweather | icao / airport | DCS Admin | Change the weather and time in the mission to the one that is active at that specific airport.  |
