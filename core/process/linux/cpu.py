import os

__all__ = ['get_cpu_set_information']


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
