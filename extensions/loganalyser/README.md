# Extension "LogAnalyser"
This is a default extension loaded in any case. 
It will scan your dcs.log for errors and react in several ways according to what happened.

## Configuration
Per default, the extension does not need any configuration. But you can disable the auto-restart on unlisting like so:
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        LogAnalyser:
          restart_on_unlist: true   # If true, a server will be auto-restarted if unlisted.
          message_unlist: 'Server is going to restart in {}!' # message to display to users on restarts
          disable_detections:       # if you do not want to use specific error detections, you can disable them here
            - script errors         # LUA script errors    
            - upnp                  # UPnP being unnecessarily activated
            - missing terrain       # Terrain needed but not installed
            - regmapstorage         # RegMapStorage full (internal DCS structure)
            - unlisted              # Server unlisting
            - moose version         # Outdated MOOSE version
            - mist version          # Outdated MIST version
          ignore_files:
            - moose.lua   # this can be any regular expression pattern like [Mm]oose.*\.lua
          warn_times: # times when to send the restart messages
            - 120
            - 60
            - 10 
```

> [!NOTE]
> "unlisted" and "regmapstorage" are disabled per default.
> To enable them, create a new "disabled_detections" structure and leave them out like so:
> ```yaml
> disable_detections: []  # enable all detections
> ```

### Type of Detections
a) script errors
General LUA errors in either embedded LUA code or an external script in your mission.

b) upnp
Detects if UPnP is enabled but should not. This speeds up the startup-time of your server a bit.

c) missing terrain
Detects if a terrain is missing but needed.

d) regmapstorage
Detects if the RegMapStorage (some storage inside DCS is full). If yes, it will restart your DCS server
automatically, as it would not work properly anymoer anyway.

e) unlisted
Your server has been unlisted from the ED website. People can not find your server anymore.
You will be warned, but you can decide to auto-restart the server also (`restart_on_unlist: true`).

f) moose version
Detects if an outdated version of MOOSE is being used.

g) mist version
Detects if an outdated version of MIST is being used.
