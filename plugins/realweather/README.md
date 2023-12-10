# Plugin "RealWeather"
With this plugin, you can apply real weather supplied by [DCS-real-weather](https://github.com/evogelsa/DCS-real-weather)
to your mission.

## Configuration
You need to point to your DCS-real-weather installation in your nodes.yaml like so:
```yaml
MyNode:
  # [...]
  extensions:
    RealWeather:
      installation: '%USERPROFILE%\Documents\realweather_v1.9.0-rc2'
```
This can be used to enable the RealWeather [extension](../../extensions/README.md) also.

## Discord Commands

| Command              | Parameter           | Channel       | Role                  | Description                                                                                    |
|----------------------|---------------------|---------------|-----------------------|------------------------------------------------------------------------------------------------|
| /realweather         | server airport      | any           | DCS Admin             | Change the weather and time in the mission to the one that is active at that specific airport. |
