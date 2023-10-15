# Cleanup Service
The cleanup service is your cleaning lady, that deletes all the unnecessary stuff from your disk after some time.<br>

## Configuration
The configuration is held in config/services/cleanup.yaml and is straight forward. You can add as many directories
as you want to cleanup in here.

```yaml
DEFAULT:
  dcs.log:
    directory: "{instance.home}/Logs"
    pattern: "*.*"
    delete_after: 30
  trackfiles:
    directory: "{instance.home}/Tracks/Multiplayer"
    pattern: "*.trk"
    delete_after: 30
DCS.openbeta_server:
  greenieboard:
    directory: "{instance.home}/airboss"
    pattern:
    - "*.csv"
    - "*.png"
    delete_after: 30
  tacview:
    directory: "%USERPROFILE%/Documents/Tacview"
    pattern: "*.acmi"
    delete_after: 30
```
These are just examples, feel free to add your own directories.
