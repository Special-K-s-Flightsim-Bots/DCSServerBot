# Plugin Profiler
This plugin adds lua profiling functionality to DCSServerBot.

You can choose from two different profilers:
* Chrome
* Callgrind

Both profilers have pros and cons. Please refer to the respective documentation of these profilers.

> [!WARNING]
> Profiling can be extremely heavy and will most likely slow down your DCS World server.<br>
> Please do not use it in production or only for a short period of time!

## Configuration
As Profiler is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - profiler
```

The plugin itself is configured with a file named config/plugins/profiler.yaml. You'll find a sample in ./samples:
```yaml
DCS.dcsserver_release:
  profiler: chrome        # one of chrome, callgrind
  attach_on_launch: true  # attach the profiler on mission launch (default: true)
```
If the debugger is not attached on mission launch, you need to attach the debugger manually with the below-mentioned
Discord commands.

## Discord Commands
| Command        | Parameter | Channel | Role       | Description                                                   |
|:---------------|:----------|:-------:|:-----------|:--------------------------------------------------------------|
| /profile start | profiler  |   all   | DCS Admin  | Loads the respective profiler into the mission and starts it. |
| /profile stop  |           |   all   | DCS Admin  | Stops the profiler.                                           |

## Evaluating Results

### Chrome Profiler
The chrome profiler generates a file "profile.json" in your Saved Games\<instance>\Logs directory.
You can load this file either into the Chrome DevTools (CTRL+SHIFT+I in your Chrome browser) Performance Tab or you can
use other third-party tools to analyze the trace. One option is [speedscope](https://www.speedscope.app/).

### Callgrind
Callgrind generates a file named callgrind.out. You can load this file into Valgrind or (K)Cachegrind. Both tools are
Linux tools, which means that you either need a Linux (virtual) system at hand or you can use WSL to install the
respective tool.

> [!IMPORTANT]
> This plugin is experimental and may yield misleading or incorrect results. 
> I have made a lot of efforts to refine it, but further adjustments and possibly significant modifications are 
> required for accurate outcomes. It's also possible that this approach won't be compatible with DCS World due to 
> inherent limitations within the software itself.
