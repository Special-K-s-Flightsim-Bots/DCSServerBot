# Extension "DCS Real Weather Updater"
If you want to use real-time weather in your missions, you can do that by using [DCS-real-weather](https://github.com/evogelsa/DCS-real-weather).
Download the release zip and unzip it to a directory of your choice on your system running your DCS servers and the 
DCSServerBot. 

## Configuration
The configuration for RealWeather goes into your nodes.yaml. There are 2 versions available at the moment, v1.x.x and 
v2.x.x. Both differ in their configuration, which is why I added 2 examples in here:

### Version 1.x
```yaml
MyNode:
  # [...]
  extensions:
    RealWeather:
      installation: '%USERPROFILE%\Documents\realweather_v1.14.0'
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        RealWeather:
          enabled: true   # optional to disable the extension, default: true
          debug: true     # see outputs of RealWeather, default: false
          metar:
            icao: URMM
            runway-elevation: 50
            add-to-brief: true
          options:
            update-time: true
            update-weather: true
            wind:
              minimum: 0
              maximum: 5
              gust-minimum: 0
              gust-maximum: 10
              stability: 0.143
            clouds:
              disallowed-presets:
                - Preset10
                - RainyPreset1
                - RainyPreset2
                - RainyPreset3
            fog:
              enable: true
              thickness-minimum: 0
              thickness-maximum: 100
              visibility-minimum: 1000
              visibility-maximum: 4000
            dust:
              enable: true
              visibility-minimum: 300
              visibility-maximum: 2000
```

### Version 2.x
```yaml
MyNode:
  # [...]
  extensions:
    RealWeather:
      installation: '%USERPROFILE%\Documents\realweather_v2.0.0'
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        RealWeather:
          enabled: true   # optional to disable the extension, default: true
          debug: true     # see outputs of RealWeather, default: false
          options:
            weather:
              icao: PGUM
              wind:
                minimum: -1
                maximum: -1
                stability: 0.143
              fog:
                thickness-minimum: 0
              temperature:
                enabled: true
              pressure:
                enabled: true
            time:
              enabled: true
              system-time: true
              offset: '0h5m'
```
You can find a list of supported parameters in the config.json (v1.x) or config.toml (v2.x) provided by DCS-real-weather.


> ⚠️ **Attention!**<br>
> DCSServerBot only supports DCS Real Weather Updater versions from 1.9.0 upwards.
> 
> If you want to set a custom ICAO code (URMM in this case) per mission, you can name your mission like so:<br>
> `MyFancyMission_ICAO_URMM_whatsoever.miz`
