# Cleanup Service
The cleanup service is your cleaning lady, that deletes all the unnecessary stuff from your disk after some time.<br>

## Configuration
The configuration is held in config/services/cleanup.yaml and is straight forward. You can add as many directories
as you want to clean up in here.

```yaml
DEFAULT:
  dcs.log:                                # Name (can be anything but needs to be unique)
    directory: "{instance.home}/Logs"     # The directory to clean up
    pattern: "*.*"                        # The pattern of the files to be cleaned up
    delete_after: 30                      # The min age of the files to be deleted
  trackfiles:
    directory: "{instance.home}/Tracks/Multiplayer"
    pattern: "*.trk"
    delete_after: 30
DCS.release_server:
  greenieboard:
    directory: "{instance.home}/airboss"
    pattern:
    - "*.csv"
    - "*.png"
    delete_after: 30
  tacview:
    directory: "%USERPROFILE%/Documents/Tacview"
    pattern: "*.acmi"
    recursive: true                       # If true, subdirectories will be included
    delete_after: 30
```
These are just examples, feel free to add your own directories.
