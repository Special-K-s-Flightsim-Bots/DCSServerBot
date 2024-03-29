---
parent: Extensions
nav_order: 0
---

# Tacview

Many servers run [Tacview](https://www.tacview.net/) to help people analyse their flight path, weapons employment and 
whatnot. It is an awesome tool for teaching and after action reports as well.<br/>
One of the downsides (besides a performance hit on servers) is, that you gather a lot of data and fill up your disk.
DCSServerBot takes care of both, it will a) warn you, if you configured Tacview in a way that is bad for your overall
server performance, and b) it can delete old Tacview files after a specific time.<br/>
To enable Tacview support, again, a change in scheduler.json is needed:

```json
{
  "configs": [
    {
      [...]
      "extensions": {
        "Tacview": {
          "tacviewExportPath": "%USERPROFILE%\\Documents\\Tacview", -- global setting (default, can be omitted)
          "delete_after": 30
        }
      }
      [...]
    },
    {
      "installation": "DCS.release_server",
      [...]
      "extensions": {
        "Tacview": {
          "tacviewExportPath": "%USERPROFILE%\\Documents\\Tacview-instance2",
          "tacviewRealTimeTelemetryPort": 42674,
          "tacviewRealTimeTelemetryPassword": "",
          "tacviewRemoteControlPort": 42675,
          "tacviewRemoteControlPassword": "",
          "tacviewPlaybackDelay": 600,
          "host": "myfancyhost.com",  -- Optional, default is your external IP
          "show_passwords": false,
          "channel": 837667390242291742
        }
      }
    }
  ]
}
```

__Optional__ parameters (will change options.lua if necessary):</br>
* **log** Defines a different log for tacview log messages, otherwise dcs.log is used (default)
* **tacviewExportPath** Sets this as the Tacview export path.
* **tacviewRealTimeTelemetryPort** Sets this as the Tacview realtime port.
* **tacviewRealTimeTelemetryPassword** Sets this as the Tacview realtime password.
* **tacviewRemoteControlPort** Sets this as the Tacview remote control port.
* **tacviewRemoteControlPassword** Sets this as the Tacview remote control password.
* **tacviewPlaybackDelay** Sets this as the Tacview playback delay.
* **delete_after** specifies the number of days after which old Tacview files will get deleted by the bot.
* **show_passwords** specifies whether to show the Tacview passwords in the server embed in your status channel or not.
* **channel** a channel where your tacview files should be uploaded into on mission end.
