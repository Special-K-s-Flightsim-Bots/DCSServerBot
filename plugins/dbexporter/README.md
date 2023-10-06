# Plugin DBExporter
This plugin will dump the whole DCSServerBot database every hour to ./export/_tablename_.json files for further processing, if needed.

## Configuration
As usual, you can configure this plugin with a simple yaml file.
```yaml
DEFAULT:
  autoexport: true
  tablefilter:
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
