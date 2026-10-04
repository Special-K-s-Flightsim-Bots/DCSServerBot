### Auto-Affinity

`auto_affinity` is an optional and experimental CPU affinity manager. It assigns managed DCSServerBot-related processes to CPU cores automatically, based on detected CPU topology, process priority, load, NUMA/cache layout, and configured minimum/maximum core requirements.

There are **two levels** of auto-affinity configuration:

1. **Node-level settings** configure the singleton `ProcessManager()` for the whole node.
2. **Process-level settings** configure individual managed processes, such as the bot process, a DCS instance, or an extension process.

This distinction is important: settings such as core reuse and load thresholds are global to the node, while `min_cores`, `max_cores`, and `quality` are per-process requests.

---

#### Node-level auto-affinity

The node-level `auto_affinity` block controls the singleton process manager for this node.

```yaml
# config/nodes.yaml
MyNode:
  auto_affinity:                # Optional / Experimental: node-wide auto-affinity settings
    enabled: true               # Enable auto-affinity for this node (default: false)
    excluded_cores: [0, 1]      # Optional: exclude logical CPUs from auto-affinity

    # Affinity for the bot process itself
    min_cores: 1                # Minimum number of logical CPUs for the bot process (default: 1)
    max_cores: 2                # Maximum number of logical CPUs for the bot process (default: min_cores)
    quality: 1                  # Core quality for the bot process (0 = low, 1 = normal, 2 = high, 3 = reserved/highest)

    # Node-wide oversubscription / core reuse
    reuse_cores: true           # Allow bounded core reuse if minimums cannot be satisfied exclusively (default: true)
    max_core_sharing: 2         # Maximum number of managed processes allowed to share one logical CPU (default: 2)

    # Node-wide load-based balancing thresholds
    growth_load_threshold: 70.0 # Grow a process only if its load per assigned core is above this value (default: 70.0)
    steal_load_threshold: 85.0  # Allow stealing from idle processes after this load per assigned core (default: 85.0)
    idle_load_threshold: 20.0   # A process below this load per assigned core may be considered idle (default: 20.0)
    steal_streak: 3             # Number of consecutive high-load checks before stealing is allowed (default: 3)
```

The following settings are **node-wide** and apply to the whole `ProcessManager()` instance:

| Setting                 | Description                                                                           |
|-------------------------|---------------------------------------------------------------------------------------|
| `enabled`               | Enables or disables auto-affinity on this node.                                       |
| `excluded_cores`        | Logical CPUs that must never be assigned by auto-affinity.                            |
| `reuse_cores`           | Allows bounded sharing of logical CPUs if not enough exclusive cores are available.   |
| `max_core_sharing`      | Maximum number of managed processes that may share one logical CPU.                   |
| `growth_load_threshold` | Load-per-assigned-core threshold above which a process may grow toward `max_cores`.   |
| `steal_load_threshold`  | Load-per-assigned-core threshold above which a process may steal from idle processes. |
| `idle_load_threshold`   | Load-per-assigned-core threshold below which another process may be considered idle.  |
| `steal_streak`          | Number of consecutive high-load checks required before stealing is allowed.           |

The following settings in the node-level block apply to the **bot process itself**:

| Setting     | Description                                       |
|-------------|---------------------------------------------------|
| `min_cores` | Minimum logical CPUs assigned to the bot process. |
| `max_cores` | Maximum logical CPUs the bot process may grow to. |
| `quality`   | CPU quality requested for the bot process.        |

---

#### Per-process auto-affinity

DCS instances and extensions may also define an `auto_affinity` block. These blocks describe the CPU requirements of that specific process only.

Only the following settings are valid there:

| Setting     | Description                                    |
|-------------|------------------------------------------------|
| `min_cores` | Minimum logical CPUs assigned to this process. |
| `max_cores` | Maximum logical CPUs this process may grow to. |
| `quality`   | CPU quality requested by this process.         |

Example for a DCS server instance:

```yaml
# config/nodes.yaml
MyNode:
  instances:
    DCS.dcs_serverrelease:
      auto_affinity:
        min_cores: 1            # Minimum logical CPUs for this DCS process
        max_cores: 4            # Maximum logical CPUs this DCS process may grow to
        quality: 3              # Prefer highest/reserved CPU quality
```

Example for an extension process:

```yaml
# config/nodes.yaml
MyNode:
  extensions:
    Olympus:
      auto_affinity:
        min_cores: 1            # Minimum logical CPUs for the extension process
        max_cores: 1            # Keep this helper process limited
        quality: 1              # Normal CPU quality
```

> [!IMPORTANT]
> Do not configure `reuse_cores`, `max_core_sharing`, `growth_load_threshold`, `steal_load_threshold`, `idle_load_threshold`, or `steal_streak` inside instance or extension `auto_affinity` blocks. These are node-wide `ProcessManager()` settings and only belong in the node-level `auto_affinity` block.

---

#### Core quality

The `quality` value describes what kind of CPU cores a process should prefer.

| Value | Meaning            | Typical use                                                            |
|------:|--------------------|------------------------------------------------------------------------|
|   `0` | Low / background   | Lightweight helpers, background services, less latency-sensitive tasks |
|   `1` | Normal             | Default for most auxiliary processes                                   |
|   `2` | High               | Performance-sensitive helpers                                          |
|   `3` | Reserved / highest | DCS server processes or other very latency-sensitive workloads         |

On hybrid CPUs, such as systems with performance and efficiency cores, the manager prefers performance-class cores for `quality > 0` and efficiency/background cores for `quality == 0`.

On non-hybrid CPUs, all cores are treated as suitable, but NUMA/cache locality is still considered.

---

#### Excluding cores

Use `excluded_cores` to reserve logical CPUs for the operating system, drivers, voice tools, monitoring, or other software that should not be managed by DCSServerBot.

```yaml
auto_affinity:
  enabled: true
  excluded_cores: [0, 1]
```

The values are **logical CPU indexes**, not physical core numbers. On SMT/Hyper-Threading systems, one physical core may have multiple logical CPU indexes.

You can also use a comma/range string where supported by the configuration:

```yaml
auto_affinity:
  excluded_cores: "0,1,8-11"
```

---

#### Minimum and maximum cores

`min_cores` is the number of logical CPUs a process should always try to receive.

`max_cores` is the upper limit the process may grow to when the cooperative load-balancer detects sustained load.

```yaml
auto_affinity:
  min_cores: 1
  max_cores: 4
```

The manager first tries to satisfy `min_cores` with exclusive, non-shared logical CPUs. If enough cores are available, processes do not share cores.

If `max_cores` is higher than `min_cores`, a process may receive additional cores while it is under sustained load and free eligible cores are available.

---

#### Core reuse / oversubscription

Core reuse is configured only at node level:

```yaml
auto_affinity:
  reuse_cores: true
  max_core_sharing: 2
```

When `reuse_cores` is enabled, the manager may reuse logical CPUs if there are more managed minimum-core requests than exclusively available cores.

Core reuse is used only as a **minimum backfill** mechanism:

1. The manager first tries to assign cores exclusively.
2. Higher-quality processes may displace lower-quality processes according to the normal affinity rules.
3. If some processes still do not have their `min_cores`, the manager may share already-used logical CPUs.
4. Cooperative growth does **not** use shared cores; it only uses free cores.

This prevents a newly managed process from ending up with no affinity assignment when all logical CPUs are already occupied.

`max_core_sharing` limits how many managed processes may share the same logical CPU.

```yaml
auto_affinity:
  enabled: true
  reuse_cores: true
  max_core_sharing: 2
```

With this configuration, no logical CPU will normally be assigned to more than two managed processes.

> [!NOTE]
> Core reuse does not create more CPU capacity. It only prevents starvation when the number of managed processes exceeds the number of available logical CPUs. If too many heavy processes share the same CPU, performance may still suffer.

---

#### Load-based growth

Load-based growth is configured only at node level:

```yaml
auto_affinity:
  growth_load_threshold: 70.0
```

A process can grow from `min_cores` toward `max_cores` if:

- it already has at least one assigned logical CPU,
- its load per assigned core is above `growth_load_threshold`,
- free eligible logical CPUs are available,
- it has not reached `max_cores`.

Load is evaluated per assigned core, not only as total process CPU usage. This helps avoid overreacting to multi-threaded processes that already have several cores assigned.

---

#### Load-based stealing

Load-based stealing is configured only at node level:

```yaml
auto_affinity:
  steal_load_threshold: 85.0
  idle_load_threshold: 20.0
  steal_streak: 3
```

If a process remains heavily loaded for several consecutive checks, it may steal an extra core from another managed process that appears idle.

The stealing rules are conservative:

- stealing only happens after `steal_streak` consecutive high-load checks,
- the victim must be below `idle_load_threshold`,
- the victim must have more than its configured `min_cores`,
- the stolen core must be in an allowed scheduling class for the demanding process,
- NUMA locality is preferred.

This avoids moving cores too aggressively during short load spikes.

---

#### CPU topology awareness

The manager uses the detected CPU topology to make better placement decisions. Depending on the operating system and CPU, this may include:

- logical CPUs,
- physical core grouping,
- SMT/Hyper-Threading siblings,
- NUMA nodes,
- scheduling/performance classes,
- last-level cache groups,
- physical die or CCD information where available.

On modern CPUs, this means the manager tries to keep related assignments close together, preferably inside the same NUMA/cache region, while still respecting process quality and available CPU classes.

---

#### Visualizing CPU assignments

The CPU topology command can be used to inspect current placement:

```text
/node cpuinfo
```

Shared logical CPUs are marked separately in the generated topology image. The exported JSON also includes shared-core information, which can help diagnose oversubscription.

---

#### Recommended starting values

For most installations, start conservatively at node level:

```yaml
auto_affinity:
  enabled: true
  excluded_cores: [0, 1]
  min_cores: 1
  max_cores: 2
  quality: 1
  reuse_cores: true
  max_core_sharing: 2
  growth_load_threshold: 70.0
  steal_load_threshold: 85.0
  idle_load_threshold: 20.0
  steal_streak: 3
```

For DCS server instances, configure only per-process requirements:

```yaml
instances:
  DCS.dcs_serverrelease:
    auto_affinity:
      min_cores: 1
      max_cores: 4
      quality: 3
```

For lightweight extensions such as web frontends or helper tools:

```yaml
extensions:
  Olympus:
    auto_affinity:
      min_cores: 1
      max_cores: 1
      quality: 1
```

---

#### When to disable core reuse

You may want to disable core reuse if you prefer strict isolation and would rather let the operating system schedule unmanaged processes normally than have DCSServerBot assign multiple managed processes to the same logical CPU.

```yaml
auto_affinity:
  reuse_cores: false
```

With core reuse disabled, the manager will only assign exclusively free cores. If the configured minimum requirements exceed the number of available logical CPUs, some processes may receive fewer cores than requested.

---

#### Important notes

> [!WARNING]
> Auto-affinity is experimental. Test it carefully on your hardware before relying on it for production events.

> [!IMPORTANT]
> CPU numbering uses logical CPU indexes as reported by the operating system. These do not always match physical core numbers shown in vendor tools.

> [!TIP]
> Reserve at least one or two logical CPUs for Windows/Linux and background services using `excluded_cores`, especially on busy DCS hosts.
