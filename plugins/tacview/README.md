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

| Command            | Parameter   | Channel | Role      | Description                                                           |
|--------------------|-------------|---------|-----------|-----------------------------------------------------------------------|
| /tacview install   | server      | all     | DCS Admin | Install Tacview into this server (needs to be available on the node). |
| /tacview uninstall | server      | all     | DCS Admin | Uninstall Tacview from this server.                                   |
| /tacview configure | server      | all     | DCS Admin | Change the Tacview configuration on this server.                      |
| /tacview download  | server file | all     | DCS       | Download a server or player Tacview file.                             |
