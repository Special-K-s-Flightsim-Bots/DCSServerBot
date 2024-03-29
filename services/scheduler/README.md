# Scheduler Service
This will be a possible future replacement / enhancement of the [Scheduler-plugin](../../plugins/scheduler/README.md) 
and some things that are implemented in scheduled functions in other plugins or even complete services like cleanup and 
backup and maybe monitoring.<br>

You can use it to run pre-defined actions at specific times. For now, this can be:
- report - generate reports with the [Reporting Framework](../../reports/README.md)
- restart - restart your DCS servers or rotate missions (no user warning implemented yet!)
 
More to come.

## Configuration
As per usual, the service is configured with a yaml file, in this case config/services/scheduler.yaml:
```yaml
DEFAULT:
  actions:
  - cron: '0 * * * *'                 # run every full hour
    action:
      type: report                    # generate a report
      params:
        file: mysample.json           # using this template in reports/scheduler
        channel: 1122334455667788     # channel to post the report in
        persistent: true              # is it a persistent report? (default = true)
DCS.release_server:
  actions:
    - cron: '0 0,4,8,12,16,20 * * *'  # run every 4 hrs
      action:
        type: restart                 # restart the respective server that is linked to this instance
        params:
          shutdown: false             # do not shutdown during the restart (default = false)
          rotate: false               # do not rotate the mission (default = false)
          run_extensions: true        # run the extensions (default = true)
```