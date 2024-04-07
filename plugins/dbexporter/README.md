# Plugin DBExporter
This plugin will dump the whole DCSServerBot database every hour to ./export/_tablename_.json files for further processing, if needed.

## Configuration
As DBExporter is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - dbexporter
```

As usual, you can configure this plugin with a simple yaml file in config/plugins/dbexporter.yaml:
```yaml
DEFAULT:
  autoexport: true  # if true, the configured tables are exported every hour
  tablefilter:      # define which tables should be exported
  - missions
  - statistics
```

| Parameter   | Description                                               |
|-------------|-----------------------------------------------------------|
| autoexport  | If true, the DB export will run automatically every hour. |
| tablefilter | Don't dump these tables on autoexport.                    |

If no configuration is provided, the autoexport will not run and the .export command (see below) will still work.

## Discord Commands

| Command | Parameter | Channel | Role    | Description                                            |
|---------|-----------|---------|---------|--------------------------------------------------------|
| /export |           | all     | Admin   | Exports the whole database. Table filters don't apply! |
