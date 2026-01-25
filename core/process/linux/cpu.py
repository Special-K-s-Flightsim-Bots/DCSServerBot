import os

__all__ = [
    'get_cpu_set_information',
    'get_e_core_affinity'
]


def get_cpu_set_information() -> list[dict]:
    """
    Returns a list of dictionaries that mimics the Windows structure.
    Keys:
        - CPU Id
        - Logical Processor Index
        - Core Index
        - Efficiency Class  (set to 0 – not available on Linux)
        - Scheduling Class  (physical package / NUMA node id)
    """
    cpu_info_list: list[dict] = []

    # Helper: try to read a file and return its integer value
    def _read_int(path: str) -> int | None:
        try:
            with open(path, "r") as f:
                return int(f.read().strip())
        except Exception:
            return None

    # ---------- 1️⃣  Preferred path: /sys/devices/system/cpu/cpuX/topology ----------
    sysfs_root = "/sys/devices/system/cpu"
    if os.path.isdir(sysfs_root):
        for entry in sorted(os.listdir(sysfs_root)):
            if not entry.startswith("cpu") or not entry[3:].isdigit():
                continue

            cpu_num = int(entry[3:])  # logical processor number

            core_path = os.path.join(sysfs_root, entry, "topology/core_id")
            pkg_path  = os.path.join(sysfs_root, entry, "topology/physical_package_id")

            core_id = _read_int(core_path)
            pkg_id  = _read_int(pkg_path)

            if core_id is None or pkg_id is None:
                # Fall back to /proc/cpuinfo later
                continue

            cpu_info_list.append(
                {
                    "CPU Id": cpu_num,
                    "Logical Processor Index": cpu_num,
                    "Core Index": core_id,
                    "Efficiency Class": 0,          # not exposed on Linux
                    "Scheduling Class": pkg_id,     # use physical package as a stand‑in
                }
            )

    # ---------- 2️⃣  Fallback: parse /proc/cpuinfo ----------
    if not cpu_info_list:
        proc_path = "/proc/cpuinfo"
        if not os.path.isfile(proc_path):
            raise RuntimeError("Unable to discover CPU topology: /proc/cpuinfo missing")

        with open(proc_path, "r") as f:
            cpu_block: dict[str, str] = {}
            for line in f:
                line = line.strip()
                if not line:          # blank line → end of one CPU block
                    if cpu_block:
                        cpu_num = int(cpu_block["processor"])
                        core_id = int(cpu_block["core id"])
                        pkg_id  = int(cpu_block["physical id"])
                        cpu_info_list.append(
                            {
                                "CPU Id": cpu_num,
                                "Logical Processor Index": cpu_num,
                                "Core Index": core_id,
                                "Efficiency Class": 0,
                                "Scheduling Class": pkg_id,
                            }
                        )
                    cpu_block.clear()
                    continue

                if ":" not in line:
                    continue
                key, val = (p.strip() for p in line.split(":", 1))
                cpu_block[key] = val

            # Last block (no trailing blank line)
            if cpu_block:
                cpu_num = int(cpu_block["processor"])
                core_id = int(cpu_block["core id"])
                pkg_id  = int(cpu_block["physical id"])
                cpu_info_list.append(
                    {
                        "CPU Id": cpu_num,
                        "Logical Processor Index": cpu_num,
                        "Core Index": core_id,
                        "Efficiency Class": 0,
                        "Scheduling Class": pkg_id,
                    }
                )

    return cpu_info_list


def get_e_core_affinity() -> int:
    """
    Best-effort detection of Linux "E-cores" affinity mask.

    Linux doesn't expose a Windows-like EfficiencyClass uniformly, but on some kernels / CPUs
    (Intel hybrid, ARM big.LITTLE) you can infer it from sysfs:

      - /sys/devices/system/cpu/cpuX/topology/core_type (if present)
      - plus an optional "capacity" signal to decide which type is "smaller":
          * cpu_capacity
          * acpi_cppc/highest_perf
          * cpufreq/cpuinfo_max_freq

    If we cannot reliably detect heterogeneous cores, returns 0 (meaning: no E-cores detected).

    Returns:
        int: bitmask where bit N corresponds to logical CPU N.
    """
    sysfs_root = "/sys/devices/system/cpu"
    if not os.path.isdir(sysfs_root):
        return 0

    def _read_int(path: str) -> int | None:
        try:
            with open(path, "r") as f:
                return int(f.read().strip())
        except Exception:
            return None

    def _iter_cpu_nums() -> list[int]:
        nums: list[int] = []
        for entry in os.listdir(sysfs_root):
            if entry.startswith("cpu") and entry[3:].isdigit():
                nums.append(int(entry[3:]))
        return sorted(nums)

    def _read_capacity(cpu_num: int) -> int | None:
        """
        Try multiple sysfs signals that correlate with "bigger/faster" cores.
        """
        base = os.path.join(sysfs_root, f"cpu{cpu_num}")
        candidates = [
            os.path.join(base, "cpu_capacity"),
            os.path.join(base, "acpi_cppc", "highest_perf"),
            os.path.join(base, "cpufreq", "cpuinfo_max_freq"),
        ]
        for p in candidates:
            v = _read_int(p)
            if v is not None:
                return v
        return None

    # Gather per-cpu core_type and capacity
    per_cpu: dict[int, dict[str, int | None]] = {}
    core_types: set[int] = set()

    for cpu_num in _iter_cpu_nums():
        core_type_path = os.path.join(sysfs_root, f"cpu{cpu_num}", "topology", "core_type")
        core_type = _read_int(core_type_path)
        cap = _read_capacity(cpu_num)

        per_cpu[cpu_num] = {"core_type": core_type, "capacity": cap}
        if core_type is not None:
            core_types.add(core_type)

    # Need at least 2 distinct core_types to talk about E vs P
    if len(core_types) < 2:
        return 0

    # Decide which core_type corresponds to "E"
    # Prefer using capacity (lower avg capacity => E). Otherwise use a conservative heuristic.
    type_to_caps: dict[int, list[int]] = {}
    for cpu_num, info in per_cpu.items():
        ct = info["core_type"]
        cap = info["capacity"]
        if ct is None or cap is None:
            continue
        type_to_caps.setdefault(ct, []).append(cap)

    e_core_type: int | None = None

    if type_to_caps and len(type_to_caps) >= 2:
        # Pick the type with lowest average "capacity-like" metric as E
        avg_by_type = {ct: (sum(caps) / len(caps)) for ct, caps in type_to_caps.items() if caps}
        if len(avg_by_type) >= 2:
            e_core_type = min(avg_by_type, key=avg_by_type.get)

    if e_core_type is None:
        # Heuristic fallback:
        # On many Intel hybrid systems core_type includes 1 and 2; Atom (E) is commonly 2.
        if 2 in core_types and 1 in core_types:
            e_core_type = 2
        else:
            # Last resort: choose the smallest numeric core_type as "E"
            e_core_type = min(core_types)

    # Build affinity mask
    mask = 0
    for cpu_num, info in per_cpu.items():
        if info["core_type"] == e_core_type:
            mask |= 1 << cpu_num

    return mask
