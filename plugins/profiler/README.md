# Plugin Profiler
This plugin adds lua profiling functionality to DCSServerBot.

You can choose from three different profilers:
* Chrome
* Callgrind
* Sample

The Chrome and Callgrind profilers *trace* every Lua call/return (heavy, exact). The Sample profiler
*statistically samples* the stack at a fixed instruction interval (much lighter, approximate) — use it
first when you just want to know "which part of my mission is hot?". Please refer to the respective
documentation of these profilers.

> [!WARNING]
> Profiling can be extremely heavy and will most likely slow down your DCS World server.<br>
> Please do not use it in production or only for a short period of time!

## Configuration
As Profiler is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
# config/main.yaml
opt_plugins:
  - profiler
```

The plugin itself is configured with a file named config/plugins/profiler.yaml. You'll find a sample in ./samples:
```yaml
# config/plugins/profiler.yaml
DCS.dcsserver_release:
  profiler: chrome        # one of chrome, callgrind, sample
  attach_on_launch: true  # attach the profiler on mission launch (default: true)
  verbose: true           # Enable verbose profiling (default: false). This will slow down even more! 
  memory: false           # (Chrome only) emit throttled Lua heap counter events (default: false)
  interval: 10000         # (Sample only) VM instructions between samples (default: 10000)
```
If the profiler is not attached on mission launch, you can attach the profiler manually with the below-mentioned
Discord commands.

> [!NOTE]
> If the profiler is attached on mission start, it only profiles lua calls and does not call a GC before capturing
> the heap size. 
> If the profiler is attached manually, lua and C calls will be captured and a GC will be called before the heap size 
> is captured.
> Attaching the profiler manually is much more expensive than doing it for the whole mission, but you get much better
> information out of it.
> Also, the file sizes will vary dramatically.

## Discord Commands
| Command         | Parameter                                       | Channel | Role       | Description                                                                     |
|:----------------|:------------------------------------------------|:-------:|:-----------|:--------------------------------------------------------------------------------|
| /profiler start | \[profiler\] \[verbose\] \[memory\] \[interval\] |   all   | DCS Admin  | Loads the respective profiler into the mission (default: Chrome) and starts it. |
| /profiler stop  |                                                 |   all   | DCS Admin  | Stops the profiler.                                                             |

## Evaluating Results

### Chrome Profiler
The chrome profiler generates a file "profile.json" in your Saved Games\<instance>\Logs directory.
You can load this file either into the Chrome DevTools (CTRL+SHIFT+I in your Chrome browser) Performance tab, 
or you can use other third-party tools to analyze the trace. 
Two options are [Perfetto UI](https://ui.perfetto.dev/) or [speedscope](https://www.speedscope.app/).

Every event is tagged with a category: `lua` for your mission's Lua functions and `c` for base-game
C functions called across the Lua boundary. Filter or colour by category in the viewer to isolate which
engine (C) calls invoked from Lua are expensive. Note that only the Lua→C *boundary* is visible — time
spent purely inside the base-game simulation (never crossing into Lua) cannot be captured by a Lua hook.
The optional `memory` flag adds throttled `lua_heap` counter tracks; leave it off when you only care
about CPU, as it adds events and (in verbose mode) forces a GC per Lua call.

### Callgrind
Callgrind generates a file named callgrind.out. You can load this file into Valgrind or (K)Cachegrind. Both tools are
Linux tools, which means that you either need a Linux (virtual) system at hand or you can use WSL to install the
respective tool.

### Sample Profiler
The sample profiler generates a file "profile.folded" in your Saved Games\<instance>\Logs directory (fixed name —
each run overwrites the previous one). This is the [folded stacks](https://github.com/brendangregg/FlameGraph)
format: each line is `root;...;leaf <count>`. Load it directly in [speedscope](https://www.speedscope.app/) (drag &
drop) or render a flame graph with Brendan Gregg's `flamegraph.pl`.

Instead of tracing every call, it takes a stack sample every `interval` VM instructions (default 10000, settable via
the `interval` parameter or config key). Overhead scales with the sample rate, not the number of calls, so it is by
far the lightest of the three — start here for "which part of my mission is hot?". Because it is statistical, it
answers "where did time go across the window" rather than "how long did one specific call take", and very short,
rarely-hit functions may be missed. Lower the interval for more resolution (more overhead); raise it for less.

Each frame is prefixed with `lua:` or `c:` so you can tell your Lua functions from base-game C called across the Lua
boundary. As with the Chrome profiler, only the Lua→C boundary is visible; time spent purely inside the base-game
simulation (never entering Lua) cannot be captured by a Lua hook. Note also that because sampling is driven by the
*instruction* counter, long-running C calls advance few instructions and are therefore under-sampled relative to
their real wall-clock cost — treat C weights as directional, not absolute.

> [!IMPORTANT]
> This plugin is experimental and may yield misleading or incorrect results. 
> I have made a lot of efforts to refine it, but further adjustments and possibly significant modifications are 
> required for accurate outcomes. It's also possible that this approach won't be compatible with DCS World due to 
> inherent limitations within the software itself.
