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
The additional configuration is handled in the [extension](../../extensions/tacview/README.md).

## Discord Commands
The following Discord commands are available through the LotAtc plugin:

| Command               | Parameter     | Channel | Role      | Description                                                                                                    |
|:----------------------|:--------------|:--------|:----------|:---------------------------------------------------------------------------------------------------------------|
| /tacview install      | server        | all     | DCS Admin | Install Tacview into this server (needs to be available on the node).                                          |
| /tacview uninstall    | server        | all     | DCS Admin | Uninstall Tacview from this server.                                                                            |
| /tacview update       | server        | all     | DCS Admin | Update the Tacview version in this instance (only possible if a newer Tacview version is installed on the PC). |
| /tacview repair       | server        | all     | DCS Admin | Reinstall Tacview on the respective instance. Same as /tacview uninstall + /tacview install.                   |
| /tacview configure    | server        | all     | DCS Admin | Change the Tacview configuration on this server.                                                               |
| /tacview download     | server file   | all     | DCS       | Download a server or player Tacview file.                                                                      |
| /tacview record_start | server [file] | all     | DCS Admin | Start a temporary Tacview recording.                                                                           |
| /tacview record_stop  | server        | all     | DCS Admin | Stop the temporary Tacview recording. If a target is specified, the file will be uploaded.                     |
