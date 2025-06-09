# Extension "DCS Real Weather Updater"
If you want to use real-time weather in your missions, you can do that by using [DCS-real-weather](https://github.com/evogelsa/DCS-real-weather).
Download the release zip and unzip it to a directory of your choice on your system running your DCS servers and the 
DCSServerBot. 

> [!IMPORTANT]
> DCSServerBot only supports DCS Real Weather Updater versions from 1.9.0 upwards.
> It is highly recommended to use version 2 or above because of issues in former versions.

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
      autoupdate: true  # enable autoupdate via GitHub
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
> [!NOTE]
> You can find a list of supported parameters in the config.json provided by DCS-real-weather.

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
            log:
              enable: true
              file: realweather.log
            weather:
              enable: true
              icao: PGUM
              icao-list:      # mutually exclusive with icao above!
                - xxxx
                - yyyy
              runway-elevation: 160
              wind:
                enable: true
                minimum: 0
                maximum: 50
                gust-minimum: 0
                gust-maximum: 50
                stability: 0.143
                fixed-reference: false
              clouds:
                enable: true
                fallback-to-legacy: true
                base:
                  minimum: 0
                  maximum: 15000
                presets:
                  default: Preset7
              fog:
                enable: true
                mode: auto
                thickness-minimum: 0
                thickness-maximum: 1000
                visibility-minimum: 0
                visibility-maximum: 6000
              dust:
                enable: true
                visibility-minimum: 300
                visibility-maximum: 3000
              temperature:
                enable: true
              pressure:
                enable: true
            time:
              enable: true
              system-time: true
              offset: '0h5m'
            date:
              enable: true
              system-date: true
              offset: "0"
```
> [!NOTE]
> You can find a list of supported parameters in the config.toml provided by DCS-real-weather.

> [!TIP]
> If you want to set a custom ICAO code (URMM in this case) per mission, you can name your mission like so:<br>
> `MyFancyMission_ICAO_URMM_whatsoever.miz`

> [!NOTE]
> You can use any parameter that Real Weather describes in their discord. I only write a json/toml from whatever
> you put in the extension configuration to pass that through to Real Weather. That said, it is ALWAYS a good
> idea to look at what they added or changed, as I can not keep up with every 3rd party app I support with the
> bot.
