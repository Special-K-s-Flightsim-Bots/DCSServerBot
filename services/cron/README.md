# Cron Service
You can use this service to run pre-defined actions at specific times. See [here](#actions) for examples.
More to come.

## Configuration
As per usual, the service is configured with a yaml file, in this case config/services/cron.yaml:
```yaml
DEFAULT:
  actions:
    - cron: '0 * * * *'                 # run every full hour
      action:
        type: report                    # generate a report
        params:
          file: mysample.json           # using this template in reports/cron
          channel: 1122334455667788     # channel to post the report in
          persistent: true              # is it a persistent report? (default = true)
    - cron: '0 3 * * 0,2-6'             # reboot the server each night but Monday at 03:00
      action:
        type: restart                   
        params:
          reboot: true                  # reboot the PC (shutdown /r)
    - cron: '0 4 * * 1'                 # shut the server down once a week on Monday
      action:
        type: halt                      # reboot the server each monday night at 03:00
    - cron: '0 12 * * *'                # run every 12 hrs
      action:
        type: purge_channel             # purge Discord channels
        params:
          channel:                      # list of channels to purge
            - 112233445566778899
            - 998877665544332211
          older_than: 7                 # delete all messages that are older than 7 days
          ignore: 119922883377446655    # ignore this user id AND message id (either the bot's or persistent messages in the channel); can be either an ID or a list of IDs
DCS.dcs_serverrelease:
  actions:
    - cron: '0 0,4,8,12,16,20 * * *'  # run every 4 hrs
      action:
        type: restart                 # restart the respective server that is linked to this instance
        params:
          shutdown: false             # do not shutdown during the restart (default = false)
          rotate: false               # do not rotate the mission (default = false)
          run_extensions: true        # run the extensions (default = true)
    - cron: '55 3 * * 1'                # Send a message to everyone, 5 mins prior to the shutdown
      action:
        type: popup                     
        params:
          message: Server will shut down in 5 mins!
          timeout: 20
```

## Actions
The following actions are available:

a) report
Send a report at a specific time.
```yaml
DEFAULT:
  actions:
    - cron: '0 * * * *'                 # run every full hour
      action:
        type: report                    # generate a report
        params:
          file: mysample.json           # using this template in reports/cron
          channel: 1122334455667788     # channel to post the report in
          persistent: true              # is it a persistent report? (default = true)
```

b) restart
Restarts the running mission / server / PC.
```yaml
DEFAULT:
  actions:
    - cron: '0 3 * * 0,2-6'             # reboot the server each night but Monday at 03:00
      action:
        type: restart                   
        params:
          rotate: true                  # Optional: rotate to the next mission
          shutdown: true                # Optional: shutdown the DCS server
          reboot: true                  # Optional: reboot the PC (shutdown /r)
```

c) halt
Shuts the PC down.
```yaml
DEFAULT:
  actions:
    - cron: '0 4 * * 1'                 # shut the server down once a week on Monday
      action:
        type: halt                      # reboot the server each monday night at 03:00
```

d) cmd
Run a shell command.
```yaml
DEFAULT:
  actions:
    - cron: '*/5 * * * *' # run a specific command every 5 minutes
      action:
        type: cmd
        params:
          cmd: 'copy a.txt b.txt'
```

e) popup
Send a popup to a running server.
```yaml
DCS.server_release:
  actions:
    - cron: '55 3 * * 1'                # Send a message to everyone at Mo, 03:55h
      action:
        type: popup                     
        params:
          message: Server will shut down in 5 mins!
          timeout: 20
```

f) broadcast
Send a popup to all running servers.
```yaml
DEFAULT:
  actions:
    - cron: '55 3 * * 1'                # Send a message to everyone at Mo, 03:55h
      action:
        type: broadcast                     
        params:
          message: Server will shut down in 5 mins!
          timeout: 20
```

g) purge_channel
Delete messages from a Discord channel.
```yaml
DEFAULT:
  actions:
    - cron: '0 12 * * *'                # run every 12 hrs
      action:
        type: purge_channel             # purge Discord channels
        params:
          channel:                      # list of channels to purge
            - 112233445566778899
            - 998877665544332211
          older_than: 7                 # delete all messages that are older than 7 days
          ignore: 119922883377446655    # ignore this user id AND message id (either the bot's or persistent messages in the channel); can be either an ID or a list of IDs
```

h) dcs_update
Run a DCS update at a specific time.
```yaml
DEFAULT:
  actions:
    - cron: '0 3 * * *'   # run every night at 03:00
      action:
        type: dcs_update  # Update DCS (if there is an update available)
        params:
          warn_times:     # Optional: warn users before the update
            - 120
            - 60
```

i) dcs_repair
Run a DCS repair at a specific time.
```yaml
DEFAULT:
  actions:
    - cron: '0 1 1 * *'   # run every month on the 1st at 01:00
      action:
        type: dcs_repair  # Repair DCS
        params:
          slow: true                # optional: do a slow repair (default: false)
          check_extra_files: true   # optional: check extra files (default: false)
          warn_times:               # Optional: warn users before the update
            - 120
            - 60
```

j) node_shutdown
Shutdown / restart the bot.
```yaml
DEFAULT:
  actions:
    - cron: '0 3 * * *'     # run every night at 03:00
      action:
        type: node_shutdown  # restart the bot
        params:
          restart: true
```
