# Extension "LogAnalyser"
This is a default extension that is loaded in any case. It will scan your dcs.log for errors and react in several ways
according to what happened.
Currently these actions are implemented:
- Restart the mission on server unlisting.
- Print script errors to the audit channel (if configured).

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
          message_unlist: 'Server is going to restart in {}!' # message to display to users on restarts
          ignore_files:
            - moose.lua   # this can be any regular expression pattern like [Mm]oose.*\.lua
          warn_times: # times when to send the restart messages
            - 120
            - 60
            - 10 
```
