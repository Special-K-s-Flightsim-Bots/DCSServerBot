# Plugin "Tacview"
[Tacview](https://www.tacview.net/) is a famous flight analysis tool for DCS World.<br>
DCSServerBot supports it as an [extension](../../extensions/tacview/README.md), to manage and display the 
configuration in your servers and a plugin, to install or uninstall Tacview in/from your servers. 

## Configuration
As Tacview is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - tacview
```

Optionally, you can configure a directory to upload the Tacview files into on `/tacview download` in a file named
config\plugins\tacview.yaml:
```yaml
DEFAULT:
  upload:
    channel: 1122334455667788  # Discord channel ID to upload Tacview files to, use -1 for DMs
```

Any additional configuration is handled in the [extension](../../extensions/tacview/README.md).

## Discord Commands
The following Discord commands are available through the LotAtc plugin:

| Command               | Parameter     | Channel | Role      | Description                                                                                                    |
|:----------------------|:--------------|:--------|:----------|:---------------------------------------------------------------------------------------------------------------|
| /tacview download     | server file   | all     | DCS       | Download a server or player Tacview file.                                                                      |
| /tacview record_start | server [file] | all     | DCS Admin | Start a temporary Tacview recording.                                                                           |
| /tacview record_stop  | server        | all     | DCS Admin | Stop the temporary Tacview recording. If a target is specified, the file will be uploaded.                     |

> [!IMPORTANT]
> You need to install the Tacview extension with `/extension install <Tacview>` to use this plugin.
