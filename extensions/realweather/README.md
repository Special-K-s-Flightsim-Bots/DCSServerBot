# Extension "DCS Real Weather Updater"
If you want to use real-time weather in your missions, you can do that by using [DCS-real-weather](https://github.com/evogelsa/DCS-real-weather).
Download the release zip and unzip it to a directory of your choice on your system running your DCS servers and the 
DCSServerBot. 

## Configuration
You can then add another extension into your nodes.yaml:
```yaml
MyNode:
  # [...]
  extensions:
    RealWeather:
      installation: '%USERPROFILE%\Documents\realweather_v1.9.0-rc2'
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
              enabled: true
              thickness-minimum: 0
              thickness-maximum: 100
              visibility-minimum: 1000
              visibility-maximum: 4000
            dust:
              enabled: true
              visibility-minimum: 300
              visibility-maximum: 2000
```
You can find a list of supported parameters in the config.json provided by DCS-real-weather.<br>
> ⚠️ **Attention!**<br>
> DCSServerBot only supports DCS Real Weather Updater versions from 1.9.0 upwards.
> 
> If you want to set a custom ICAO code (URMM in this case) per mission, you can name your mission like so:<br>
> `MyFancyMission_ICAO_URMM_whatsoever.miz`
