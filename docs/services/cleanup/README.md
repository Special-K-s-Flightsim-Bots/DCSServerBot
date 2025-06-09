---
layout: default
title: README
nav_section: services/cleanup
---

# Cleanup Service
The cleanup service is your cleaning lady, that deletes all the unnecessary stuff from your disk or discord channels 
after some time.

## Configuration
The configuration is held in config/services/cleanup.yaml and is straight forward. You can add as many directories
or channels as you want to clean up in here.

```yaml
DEFAULT:
  dcs.log:                                # Name (can be anything but needs to be unique)
    directory: "{instance.home}/Logs"     # The directory to clean up
    pattern: "*.*"                        # The pattern of the files to be cleaned up
    delete_after: 30                      # The min age of the files to be deleted (default: 30)
  trackfiles:
    directory: "{instance.home}/Tracks/Multiplayer"
    pattern: "*.trk"
    delete_after: 30
  channels:                 # channels have to be defined in the DEFAULT section
    channel:                # delete all messages from these channels ...
      - 112233445566778899
      - 998877665544332211
    ignore: 
      - 11992288337744      # ignore this user id AND message id (either the bot's or persistent messages in the channel); can be either an ID or a list of IDs
    delete_after: 7         # ... which are older than 7 days (default: 0)
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
These are just examples, feel free to add your own directories / channels.
> [!NOTE]
> Please keep in mind that deleting a lot of messages will take its time and can result in Discord rate limits.
