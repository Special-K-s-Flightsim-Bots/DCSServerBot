---
layout: default
title: README
nav_section: extensions/tacview
---

# Extension "Tacview"
Many servers run [Tacview](https://www.tacview.net/) to help people analyse their flight path, weapons employment and 
whatnot. It is an awesome tool for teaching and after action reports as well.<br/>
One of the downsides (besides a performance hit on servers) is, that you gather a lot of data and fill up your disk.
DCSServerBot takes care of both, it will a) warn you, if you configured Tacview in a way that is bad for your overall
server performance, and b) it can delete old Tacview files after a specific time. (see below)<br/>

## Configuration
To enable Tacview support, a change in nodes.yaml is needed:
```yaml
MyNode:
  # [...]
  extensions:
    Tacview:
      installation: '%ProgramFiles(x86)%\Tacview'
      tacviewExportPath: '%USERPROFILE%\Documents\Tacview'
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Tacview:
          autoupdate: true                      # if true, the bot will auto-update the tacview mod, if the version of Tacview was updated on your server (default: false)
          show_passwords: false                 # hide passwords in your server status embed (default: true)
          host: 127.0.0.1                       # Tacview host (default)
          log: "%USERPROFILE%\\Saved Games\\DCS.release_server\\Logs\tacview.log" # Only needed, if you export tacview logs to a different file.
          tacviewRealTimeTelemetryPort: 42674   # default
          tacviewRealTimeTelemetryPassword: ''  # default
          tacviewRemoteControlPort: 42675       # default
          tacviewRemoteControlPassword: ''      # default
          tacviewPlaybackDelay: 600             # default 0, should be 600 for performance reasons
          target: '<id:112233445566778899>'     # optional: channel id or directory
```
__Optional__ parameters (will change options.lua if necessary):</br>
* **tacviewExportPath** Sets this as the Tacview export path.
* **tacviewRealTimeTelemetryPort** Sets this as the Tacview realtime port.
* **tacviewRealTimeTelemetryPassword** Sets this as the Tacview realtime password.
* **tacviewRemoteControlPort** Sets this as the Tacview remote control port.
* **tacviewRemoteControlPassword** Sets this as the Tacview remote control password.
* **tacviewPlaybackDelay** Sets this as the Tacview playback delay.
* **delete_after** specifies the number of days after which old Tacview files will get deleted by the bot.
* **show_passwords** specifies whether to show the Tacview passwords in the server embed in your status channel or not.
* **target** a channel or directory where your tacview files should be uploaded to on mission end.

To delete old tacview files, checkout the [Cleanup](../../services/cleanup/README.md) service.

