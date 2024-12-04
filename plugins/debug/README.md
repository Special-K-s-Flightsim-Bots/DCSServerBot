# Plugin "Debug"
Simple event debugger for DCS callbacks and DCS mission events. It dumps the whole event structure for debug purposes.

## Configuration
As Debug is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - debug
```
> Please keep in mind that additional logging can cost some performance, where I don't expect this plugin to take much.

To separate the debug events from your dcs.log, I would recommend to add this to your autoexec.cfg:
```lua
log.set_output('events', 'EVENT DEBUGGER', log.ALL, log.FULL)
```

## Credits
Thanks to MisterOutOfTime (Moots) for helping me building that.
