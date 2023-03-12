---
parent: Extensions
nav_order: 0
---

# DCS-real-weather

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
      "installation": "DCS.openbeta_server",
      [...]
      "extensions": {
        "RealWeather": {
          "enabled": true,
          "icao": "SFAL",
          "update-time": true,
          "update-weather": true
        }
      }
    }
  ]
}
```

You can find a list of supported parameters in the config.json provided by DCS-real-weather.
