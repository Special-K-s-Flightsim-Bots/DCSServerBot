---
parent: Extensions
nav_order: 0
---

# DCS Real Weather Updater

If you want to use real-time weather in your missions, you can do that by using [DCS-real-weather](https://github.com/evogelsa/DCS-real-weather).
Download the release zip and unzip it to a directory of your choice on your system running your DCS servers and the 
DCSServerBot. You can then add another extension into your scheduler.json:
```json
{
  "configs": [
    {
      [...]
      "extensions": {
        "RealWeather": {
          "installation": "%USERPROFILE%\\Documents\\realweather_v1.5.0"
        }
      }
      [...]
    },
    {
      "installation": "DCS.release_server",
      [...]
      "extensions": {
        "RealWeather": {
          "enabled": true,
          "metar": {
            "icao": "KLSV",
            "remarks": "Test 1.9.0"
            },
          "options": {
            "update-time": false,
            "update-weather": true,
            "time-offset": "0",
            "wind": {
                "minimum": 2,
                "maximum": 16,
                "stability": 0.143
            },
            "clouds": {
                "disallowed-presets": []
            },
            "fog-allowed": true,
            "dust-allowed": true,
            "logfile": "rwlogfile.log"
          }
        }
      }
    }
  ]
}
```
You can find a list of supported parameters in the config.json provided by DCS-real-weather.<br>
**DCSServerBot only supports DCS Real Weather Updater versions from 1.9.0 upwards.**
