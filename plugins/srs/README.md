# Plugin "SRS"
[SimpleRadioStandalone](http://dcssimpleradio.com/) (DCS-SRS) is a radio solution for DCS World.<br>
If you want to use SRS in DCSServerBot, in most cases it is enough to activate the respective 
[extension](../../extensions/srs/README.md). Especially when it comes to [LotAtc](../../plugins/lotatc/README.md),
or, if you want to display an SRS activity status for your players, or if you even want to use slot blocking based
on SRS - then you want to install this plugin.

## Configuration
As SRS is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - srs
```

If you want to use the slot blocking feature, you need to create a config/plugins/srs.yaml file like so:
```yaml
DEFAULT:
  message_no_srs: You need to use SRS to play on this server!
  enforce_srs: true   # block slots until SRS is activated
  move_to_spec: true  # move people back to spectators, if they leave SRS (only if enforce is true) 
```
You can define, which server (instance) will use this blocking feature by specifying the instance name instead of 
DEFAULT.

## Discord Commands
The following Discord commands are available through the SRS plugin:

| Command     | Parameter | Channel | Role      | Description                                            |
|-------------|-----------|---------|-----------|--------------------------------------------------------|
| /srs list   |           | all     | DCS       | Shows active users on SRS with their connected radios. |
| /srs update | server    | all     | DCS Admin | Updates SRS on the respective node.                    |
| /srs repair | server    | all     | DCS Admin | Repairs (re-installs) SRS on the respective node.      |
