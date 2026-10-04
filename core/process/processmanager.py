import logging
import psutil
import sys
import threading

from collections import defaultdict
from io import BytesIO
from typing import Any

if sys.platform == 'win32':
    from .win32.cpu import (get_cpu_set_information, get_e_core_affinity, get_cache_info,
                            get_cpu_name, get_p_core_affinity, get_cpus_from_affinity, get_die_info)
else:
    from .linux.cpu import (get_cpu_set_information, get_e_core_affinity, get_cache_info,
                            get_cpu_name, get_p_core_affinity, get_cpus_from_affinity, get_die_info)

logger = logging.getLogger(__name__)

__all__ = ['ProcessManager']


class ProcessManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ProcessManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, excluded_cores: list[int] | str | None = None, auto_affinity: bool = True,
                 reuse_cores: bool = True, max_core_sharing: int = 2,
                 growth_load_threshold: float = 70.0, steal_load_threshold: float = 85.0,
                 idle_load_threshold: float = 20.0, steal_streak: int = 3):
        if getattr(self, '_initialized', False):
            return

        with self._lock:
            if getattr(self, '_initialized', False):
                return

            self.auto_affinity = auto_affinity
            self.excluded_cores = self._parse_cores(excluded_cores)
            self.topology = self._get_physical_topology()
            self.scheduling_classes = self._get_scheduling_classes()
            self.p_e_core_cpu = len(self.scheduling_classes) > 1
            self.performance_sched_class = max(self.scheduling_classes) if self.scheduling_classes else 0
            self.efficiency_sched_class = min(self.scheduling_classes) if self.scheduling_classes else 0
            self.reuse_cores = bool(reuse_cores)
            self.max_core_sharing = max(1, int(max_core_sharing))
            self.growth_load_threshold = float(growth_load_threshold)
            self.steal_load_threshold = float(steal_load_threshold)
            self.idle_load_threshold = float(idle_load_threshold)
            self.steal_streak = max(1, int(steal_streak))
            self.managed_processes: dict[int, dict[str, Any]] = {}
            # Cache for CPU load: {pid: last_load_percentage}
            self._load_cache: dict[int, float] = {}
            # Track consecutive high-load runs: {pid: count}
            self._load_streak: dict[int, int] = {}

            self._stop_event = threading.Event()
            if self.auto_affinity:
                logger.warning("EXPERIMENTAL: Auto-Affinity is active!")
                self._watcher_thread = threading.Thread(
                    target=self._watch_processes,
                    name="ProcessManagerWatcher",
                    daemon=True
                )
                self._watcher_thread.start()
            self._initialized = True

    @staticmethod
    def _parse_cores(cores: Any) -> list[int]:
        if not cores:
            return []
        if isinstance(cores, list):
            return cores
        if isinstance(cores, str):
            res = []
            try:
                for part in cores.split(','):
                    part = part.strip()
                    if not part:
                        continue
                    if '-' in part:
                        start, end = map(int, part.split('-'))
                        res.extend(range(start, end + 1))
                    else:
                        res.append(int(part))
                return sorted(list(set(res)))
            except ValueError:
                logger.error(f"Error parsing excluded_cores: {cores}")
                return []
        return []

    @staticmethod
    def _normalise_affinity_params(min_cores: int = 1, max_cores: int | None = None,
                                   quality: int = 1) -> tuple[int, int, int]:
        """Normalise process affinity parameters before storing them."""
        try:
            min_cores = int(min_cores)
        except (TypeError, ValueError):
            min_cores = 1
        try:
            max_cores = int(max_cores) if max_cores is not None else min_cores
        except (TypeError, ValueError):
            max_cores = min_cores
        try:
            quality = int(quality)
        except (TypeError, ValueError):
            quality = 1

        min_cores = max(1, min_cores)
        max_cores = max(min_cores, max_cores)
        quality = max(0, quality)
        return min_cores, max_cores, quality

    def _cleanup_pid(self, pid: int) -> None:
        """Remove all manager-side state for a process id."""
        self.managed_processes.pop(pid, None)
        self._load_cache.pop(pid, None)
        self._load_streak.pop(pid, None)

    def _eligible_logical_cpus(self) -> list[int]:
        """Return all logical CPUs usable by auto-affinity after exclusions."""
        cpus: list[int] = []
        for groups in self.topology.values():
            for cores_map in groups.values():
                for logicals in cores_map.values():
                    cpus.extend(l for l in logicals if l not in self.excluded_cores)
        return sorted(set(cpus))

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the watcher thread cleanly."""
        self._stop_event.set()
        watcher = getattr(self, "_watcher_thread", None)
        if watcher and watcher.is_alive():
            watcher.join(timeout=timeout)

    def plan_assignments(self, cooperative: bool = False) -> dict[int, list[int]]:
        """Compute assignments without applying them.

        Useful for tests, diagnostics, and dry-run validation.
        """
        with self._lock:
            return self._compute_assignments(cooperative=cooperative, dry_run=True)

    def shared_cores(self) -> dict[int, list[str]]:
        """Return shared logical CPUs as {cpu: [process labels...]}."""
        usage = self._usage_labels()
        return {
            cpu: labels
            for cpu, labels in usage.items()
            if len(labels) > 1
        }

    def _get_scheduling_classes(self) -> list[int]:
        """Returns the scheduling classes found in the discovered topology.

        This treats heterogeneous CPUs as a generic tiered system instead of assuming
        a strict Intel-style P-core/E-core split. On symmetric CPUs this is usually
        a single class.
        """
        classes = {
            int(sched)
            for groups in self.topology.values()
            for sched, _ in groups.keys()
        }
        return sorted(classes)

    def _is_performance_class(self, sched: int) -> bool:
        """Whether *sched* belongs to the performance side of this CPU.

        On a two-tier hybrid CPU this maps to the higher scheduling class. On CPUs
        with more than two classes, all non-lowest classes are treated as usable for
        normal/performance-sensitive processes.
        """
        if not self.p_e_core_cpu:
            return True
        return int(sched) > self.efficiency_sched_class

    def _is_efficiency_class(self, sched: int) -> bool:
        """Whether *sched* is the lowest-efficiency/background scheduling tier."""
        return self.p_e_core_cpu and int(sched) == self.efficiency_sched_class

    def _core_class_label(self, sched: int) -> str:
        """Human-readable scheduling class label for visualizations."""
        if not self.p_e_core_cpu:
            return "Core"
        if len(self.scheduling_classes) == 2:
            return "P-Core" if self._is_performance_class(sched) else "E-Core"
        if int(sched) == self.performance_sched_class:
            return f"Perf Class {sched}"
        if int(sched) == self.efficiency_sched_class:
            return f"Eff Class {sched}"
        return f"Class {sched}"

    def _core_class_prefix(self, sched: int) -> str:
        """Short scheduling class prefix for logical CPU labels."""
        if not self.p_e_core_cpu:
            return ""
        if len(self.scheduling_classes) == 2:
            return "P" if self._is_performance_class(sched) else "E"
        if int(sched) == self.performance_sched_class:
            return f"P{sched}-"
        if int(sched) == self.efficiency_sched_class:
            return f"E{sched}-"
        return f"C{sched}-"

    @staticmethod
    def _get_physical_topology() -> dict[int, dict[tuple[int, int], dict[int, list[int]]]]:
        """Groups logical processors by Numa Node, (Scheduling Class, LLC Index), and Physical Core Index."""
        cpu_sets = get_cpu_set_information()
        try:
            cache_info = get_cache_info()
        except Exception:
            cache_info = []
        try:
            die_info = get_die_info()
        except Exception:
            die_info = []

        # 1. Try to build a mapping based on physical dies (CCD)
        llc_map = {}
        if die_info:
            for die_id, logicals in enumerate(die_info):
                for lp_idx in logicals:
                    llc_map[lp_idx] = die_id

        # 2. If die info is missing, fall back to L3 cache boundaries
        if not llc_map:
            l3_caches = sorted(
                [c for c in cache_info if c.get('level') == 3 and c.get('cores')],
                key=lambda x: x['cores'][0]
            )
            for l3_id, cache in enumerate(l3_caches):
                for lp_idx in cache['cores']:
                    llc_map[lp_idx] = l3_id

            # Last-resort AMD fallback for systems where L3 is reported per core
            # or otherwise cannot be mapped into useful shared-cache groups.
            #
            # This deliberately avoids the previous hard-coded "8 cores per CCD"
            # assumption. Modern AMD layouts can vary by SKU, disabled cores, EPYC /
            # Threadripper topology, or X3D asymmetry. If the OS/cache helpers expose
            # usable cache or die data, that data wins. If they do not, the OS-provided
            # Last Level Cache Index remains the safer fallback.
            if not llc_map and "AMD" in get_cpu_name():
                logger.debug(
                    "AMD CPU detected without usable die/L3 topology; using OS-provided Last Level Cache Index."
                )

        topo = {}
        for cpu in cpu_sets:
            l_idx = cpu["Logical Processor Index"]
            sched = cpu["Scheduling Class"]
            c_idx = cpu["Core Index"]
            n_idx = cpu.get("Numa Node Index", 0)

            # Use our discovered mapping if available, otherwise fallback to OS-provided index
            llc_idx = llc_map.get(l_idx, cpu.get("Last Level Cache Index", 0))

            group_key = (sched, llc_idx)
            if n_idx not in topo:
                topo[n_idx] = {}
            if group_key not in topo[n_idx]:
                topo[n_idx][group_key] = {}
            if c_idx not in topo[n_idx][group_key]:
                topo[n_idx][group_key][c_idx] = []

            topo[n_idx][group_key][c_idx].append(l_idx)
        return topo

    def _watch_processes(self):
        """Background worker that waits for processes to exit and triggers redistribution."""
        while not self._stop_event.is_set():
            # Create a local list of processes to watch while holding the lock briefly
            procs: list[psutil.Process] = []
            with self._lock:
                for info in self.managed_processes.values():
                    if isinstance(info, dict) and 'process' in info:
                        procs.append(info['process'])

            if not procs:
                self._stop_event.wait(timeout=2.0)
                continue

            # Wait for any process to terminate
            gone, _ = psutil.wait_procs(procs, timeout=2.0)
            if gone:
                with self._lock:
                    # Cleanup and do a 'Natural' redistribution
                    for p in gone:
                        self._cleanup_pid(p.pid)
                    self._redistribute_cores(cooperative=False)
            else:
                # Every 2 seconds, try a 'Cooperative' load-based pass
                with self._lock:
                    self._redistribute_cores(cooperative=True)

    def _update_load_metrics(self):
        """Refreshes the CPU load percentage for all managed processes using EWMA."""
        alpha = 0.3  # Smoothing factor: 0.3 = 30% new, 70% old
        stale_pids: list[int] = []

        for pid, info in self.managed_processes.items():
            try:
                # interval=None makes it non-blocking
                current_load = info['process'].cpu_percent(interval=None)
                if pid in self._load_cache:
                    self._load_cache[pid] = alpha * current_load + (1.0 - alpha) * self._load_cache[pid]
                else:
                    self._load_cache[pid] = current_load
            except psutil.NoSuchProcess:
                stale_pids.append(pid)
            except psutil.AccessDenied:
                self._load_cache[pid] = 0.0

        for pid in stale_pids:
            self._cleanup_pid(pid)

    @staticmethod
    def _build_core_owners(assignments: dict[int, list[int]]) -> dict[int, list[int]]:
        """Builds {logical_cpu: [pid, ...]} from current assignments.

        Unlike the exclusive owner map used by the main allocator, this supports
        shared cores for oversubscription.
        """
        owners: dict[int, list[int]] = defaultdict(list)
        for pid, cores in assignments.items():
            for core in cores:
                owners[core].append(pid)
        return owners

    def _load_per_assigned_core(self, pid: int, assignments: dict[int, list[int]]) -> float:
        """Returns a rough per-assigned-core load for scoring shared cores."""
        return self._load_cache.get(pid, 0.0) / max(1, len(assignments.get(pid, [])))

    def _shared_core_score(self, pid: int, logical: int, logical_to_unit: dict,
                           assignments: dict[int, list[int]],
                           core_owners: dict[int, list[int]]) -> tuple:
        """Scores how bad it would be to share *logical* with *pid*.

        Lower is better. This is deliberately conservative: sharing is only used
        after exclusive minimum allocation has failed.
        """
        info = self.managed_processes[pid]
        quality = int(info.get('quality', 0))
        n_idx, sched, c_idx, llc_idx = logical_to_unit[logical]
        owners = core_owners.get(logical, [])

        owner_qualities = [
            int(self.managed_processes[owner].get('quality', 0))
            for owner in owners
            if owner in self.managed_processes
        ]
        owner_load = sum(self._load_per_assigned_core(owner, assignments) for owner in owners)
        owner_quality_max = max(owner_qualities, default=-1)
        higher_quality_owner = any(owner_quality > quality for owner_quality in owner_qualities)

        current = assignments.get(pid, [])
        if current:
            current_units = [logical_to_unit[core] for core in current if core in logical_to_unit]
            current_numas = {unit[0] for unit in current_units}
            current_llcs = {unit[3] for unit in current_units}
            current_scheds = {unit[1] for unit in current_units}
        else:
            current_numas = set()
            current_llcs = set()
            current_scheds = set()

        if self.p_e_core_cpu and quality > 0:
            class_penalty = 0 if self._is_performance_class(sched) else 100
        elif self.p_e_core_cpu:
            class_penalty = 0 if self._is_efficiency_class(sched) else 20
        else:
            class_penalty = 0

        numa_penalty = 0 if not current_numas or n_idx in current_numas else 10
        llc_penalty = 0 if not current_llcs or llc_idx in current_llcs else 5
        sched_penalty = 0 if not current_scheds or sched in current_scheds else 3

        # Prefer spreading sharing pressure over physical cores, not only logical CPUs.
        physical_unit_pressure = sum(
            len(core_owners.get(core, []))
            for core, unit in logical_to_unit.items()
            if unit == (n_idx, sched, c_idx, llc_idx)
        )

        return (
            class_penalty,
            50 if higher_quality_owner else 0,
            len(owners),
            physical_unit_pressure,
            owner_load,
            owner_quality_max,
            numa_penalty,
            llc_penalty,
            sched_penalty,
            -int(sched),
            logical
        )

    def _shared_core_candidates(self, pid: int, all_physical_units: list[dict],
                                logical_to_unit: dict, assignments: dict[int, list[int]],
                                core_owners: dict[int, list[int]]) -> list[int]:
        """Returns logical CPUs eligible for shared minimum backfill."""
        info = self.managed_processes[pid]
        quality = int(info.get('quality', 0))
        current = set(assignments.get(pid, []))
        candidates: list[int] = []

        for unit in all_physical_units:
            sched = int(unit['sched'])
            if self.p_e_core_cpu and quality > 0 and not self._is_performance_class(sched):
                continue
            if self.p_e_core_cpu and quality == 0 and not self._is_efficiency_class(sched):
                # Background processes prefer efficiency cores, but may fall back below.
                continue

            for logical in self.topology[unit['n_idx']][(unit['sched'], unit['llc_idx'])][unit['c_idx']]:
                if logical in self.excluded_cores or logical in current:
                    continue
                if len(core_owners.get(logical, [])) >= self.max_core_sharing:
                    continue
                if logical in logical_to_unit:
                    candidates.append(logical)

        # If quality 0 found no E/background cores, allow it to share normal cores
        # rather than leaving the process without its minimum.
        if not candidates and self.p_e_core_cpu and quality == 0:
            for unit in all_physical_units:
                for logical in self.topology[unit['n_idx']][(unit['sched'], unit['llc_idx'])][unit['c_idx']]:
                    if logical in self.excluded_cores or logical in current:
                        continue
                    if len(core_owners.get(logical, [])) >= self.max_core_sharing:
                        continue
                    if logical in logical_to_unit:
                        candidates.append(logical)

        return candidates

    def _backfill_shared_minimums(self, sorted_pids: list[int], all_physical_units: list[dict],
                                  logical_to_unit: dict, assignments: dict[int, list[int]]) -> None:
        """Satisfies remaining minimum-core requirements by reusing cores.

        This phase intentionally runs after exclusive fair minimums and displacement.
        It does not feed cooperative growth; it only prevents a managed process from
        ending up with no/too few affinity CPUs when demand exceeds exclusive supply.
        """
        if not self.reuse_cores:
            return

        core_owners = self._build_core_owners(assignments)

        for pid in sorted_pids:
            info = self.managed_processes[pid]
            current_cores = assignments[pid]
            missing = int(info['min_cores']) - len(current_cores)
            if missing <= 0:
                continue

            process_name = getattr(info['process'], 'name_tag', pid)
            added: list[int] = []
            logger.debug("Shared minimum backfill: %s needs %d additional core(s)", process_name, missing)

            while missing > 0:
                candidates = self._shared_core_candidates(
                    pid, all_physical_units, logical_to_unit, assignments, core_owners
                )
                if not candidates:
                    logger.warning(
                        "Could not satisfy minimum affinity for %s: need %d more core(s), no reusable core available",
                        process_name, missing
                    )
                    break

                best_core = min(
                    candidates,
                    key=lambda core: self._shared_core_score(pid, core, logical_to_unit, assignments, core_owners)
                )

                current_cores.append(best_core)
                core_owners[best_core].append(pid)
                added.append(best_core)
                missing -= 1
                logger.debug(
                    "Shared affinity core: %s -> %s (owners=%s)",
                    process_name,
                    best_core,
                    core_owners[best_core]
                )

            if added:
                logger.info("Shared affinity backfill: %s reused core(s) %s", process_name, sorted(added))

    def _usage_labels(self) -> dict[int, list[str]]:
        """Returns {logical_cpu: [process labels...]} for visualization."""
        usage_map: dict[int, list[str]] = defaultdict(list)
        for info in self.managed_processes.values():
            try:
                name = getattr(info['process'], 'name_tag', info['process'].name()).replace('/', '\n')
                for cpu in info['process'].cpu_affinity():
                    usage_map[cpu].append(name)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return usage_map

    def _redistribute_cores(self, cooperative: bool = False):
        """Redistributes cores using fair minimums. Growth only occurs in cooperative mode under load."""
        assignments = self._compute_assignments(cooperative=cooperative)
        if assignments:
            self._apply_assignments(assignments)

    def _compute_assignments(self, cooperative: bool = False, *, dry_run: bool = False) -> dict[int, list[int]]:
        """Compute process-to-core assignments without applying process affinity."""
        if not self.managed_processes:
            return {}

        if not self._eligible_logical_cpus():
            logger.warning("Auto-affinity has no eligible logical CPUs after excluded_cores filtering.")
            return {}

        if not dry_run:
            self._update_load_metrics()

        if not self.managed_processes:
            return {}

        # 1. BUILD HARDWARE STATE MAP
        all_physical_units: list[dict] = []
        logical_to_unit = {}
        for n_idx in sorted(self.topology.keys()):
            for group_key in sorted(self.topology[n_idx].keys(), reverse=True):
                sched = group_key[0]
                for c_idx in sorted(self.topology[n_idx][group_key].keys()):
                    logical = [l for l in self.topology[n_idx][group_key][c_idx] if l not in self.excluded_cores]
                    if logical:
                        current_owners = {}
                        for l in logical:
                            for pid, p_info in self.managed_processes.items():
                                if l in p_info.get('_current_assignments', []):
                                    current_owners[l] = {'pid': pid, 'quality': p_info['quality']}
                            logical_to_unit[l] = (n_idx, sched, c_idx, group_key[1])

                        all_physical_units.append({
                            'n_idx': n_idx,
                            'sched': sched,
                            'llc_idx': group_key[1],
                            'c_idx': c_idx,
                            'logical': logical,
                            'is_p': self._is_performance_class(sched),
                            'owners': current_owners
                        })

        if not logical_to_unit:
            logger.warning("Auto-affinity has no usable logical CPU topology after exclusions.")
            return {}

        # 2. IDENTIFY AND PURGE DISPLACED PROCESSES
        pids_to_reset = set()
        sorted_pids = sorted(self.managed_processes.keys(),
                             key=lambda p: (self.managed_processes[p]['quality'], self._load_cache.get(p, 0.0)),
                             reverse=True)

        # Map logical cores to their current owners for an easier lookup
        current_owner_map: dict[int, int] = {}
        for pid, info in self.managed_processes.items():
            for l in info.get('_current_assignments', []):
                current_owner_map[l] = pid

        temp_units: list[dict] = [dict(u, logical=list(u['logical'])) for u in all_physical_units]

        # we want to try the max available scheduling classes
        max_available_sched = max({x[1] for x in logical_to_unit.values()})

        for pid in sorted_pids:
            info = self.managed_processes[pid]
            # we do not need to reassign processes with the lowest quality requirements
            if info['quality'] == 0:
                continue

            # how many logical cores do we need?
            needed = info['min_cores']

            # We try to find cores in the best scheduling class
            tier_units = [u for u in temp_units if int(u['sched']) == max_available_sched]
            tier_units.sort(key=lambda x: x['sched'], reverse=True)

            # 1. Try to fulfill from truly free cores in our target class first
            # We must not count ourselves in the current_owner_map
            other_owner_map = {x: y for x, y in current_owner_map.items() if y != pid}
            for unit in tier_units:
                if needed == 0: break
                free_cores = [l for l in unit['logical'] if l not in other_owner_map]
                take = min(len(free_cores), needed)
                for l in free_cores[:take]:
                    unit['logical'].remove(l)
                    needed -= 1

            # 2. If still needed, displace lower-quality processes
            if needed > 0:
                min_allowed_sched = 1 if self.p_e_core_cpu and info['quality'] > 0 else 0
                # Strictly greater is intentional here: this displacement step may only
                # steal from classes above the protected floor. Sharing/reuse of the
                # protected floor belongs to the future core-reuse oversubscription phase.
                tier_units = [u for u in temp_units if int(u['sched']) > min_allowed_sched]
                tier_units.sort(key=lambda x: x['sched'], reverse=True)

                for unit in tier_units:
                    if needed <= 0: break

                    # Find cores in this unit owned by someone with lower quality
                    displaceable = []
                    for l in unit['logical']:
                        owner_pid = current_owner_map.get(l)
                        if owner_pid and self.managed_processes[owner_pid]['quality'] < info['quality']:
                            displaceable.append(l)

                    if displaceable:
                        take = min(len(displaceable), needed)
                        for l in displaceable[:take]:
                            owner_pid = current_owner_map[l]
                            pids_to_reset.add(owner_pid)
                            del current_owner_map[l]
                            unit['logical'].remove(l)
                            needed -= 1

        # Reset state for displaced processes
        assignments = {pid: [] for pid in self.managed_processes}
        for pid in sorted_pids:
            if pid in pids_to_reset:
                assignments[pid] = []
            else:
                assignments[pid] = list(self.managed_processes[pid].get('_current_assignments', []))

        # Refresh the logical pool
        for unit in all_physical_units:
            unit['logical'] = [l for l in unit['logical'] if not any(l in a for a in assignments.values())]

        # 3. PHASE 1: FAIR MINIMUMS (Physical Unit First)
        for pid in sorted_pids:
            info = self.managed_processes[pid]
            current_cores = assignments[pid]
            needed = info['min_cores'] - len(current_cores)
            if needed <= 0: continue

            if not self.p_e_core_cpu:
                eligible = all_physical_units
            else:
                eligible = [x for x in all_physical_units if x['is_p'] is (info['quality'] > 0)]
            # NUMA awareness: Prefer cores on the same NUMA node as existing assignments
            if current_cores:
                u_info = logical_to_unit[current_cores[0]]
                current_numa = u_info[0]
                current_llc = u_info[3]
                eligible.sort(key=lambda x: (x['n_idx'] != current_numa, x['llc_idx'] != current_llc, -x['sched']))
            else:
                eligible.sort(key=lambda x: -x['sched'])

            # Step A: Physical Consolidation
            # Try to complete units we already touch or take fresh clean units.
            for unit in eligible:
                if needed <= 0: break
                unit_logicals = self.topology[unit['n_idx']][(unit['sched'], unit['llc_idx'])][unit['c_idx']]
                if any(l in current_cores for l in unit_logicals) or not current_cores:
                    while unit['logical'] and needed > 0:
                        current_cores.append(unit['logical'].pop(0))
                        needed -= 1

            # Step B: Emergency Backfill
            # If we STILL don't have enough cores, take anything left in the tier.
            if needed > 0:
                for unit in eligible:
                    if needed <= 0: break
                    while unit['logical'] and needed > 0:
                        current_cores.append(unit['logical'].pop(0))
                        needed -= 1

        # 4. PHASE 2: SHARED MINIMUM BACKFILL
        # If exclusive allocation cannot satisfy every process minimum, allow bounded
        # core reuse. This is intentionally min-only: cooperative growth still uses
        # exclusive free cores to avoid turning oversubscription into normal operation.
        self._backfill_shared_minimums(sorted_pids, all_physical_units, logical_to_unit, assignments)

        # only re-arrange in cooperative mode
        if cooperative:

            # 5. PHASE 3: DEFRAGMENTATION
            for pid in sorted_pids:
                info = self.managed_processes[pid]
                current_cores = assignments[pid]
                if not current_cores: continue

                target_sched = max([logical_to_unit[x][1] for x in current_cores])
                min_allowed_sched = 1 if self.p_e_core_cpu and info['quality'] > 0 else 0
                shared_owners = self._build_core_owners(assignments)

                occupied_units = {}
                for l in current_cores:
                    unit_key = logical_to_unit.get(l)
                    if unit_key:
                        occupied_units[unit_key] = occupied_units.get(unit_key, 0) + 1

                # Sort units by occupancy (most populated first)
                sorted_occupied = sorted(occupied_units.items(), key=lambda x: x[1], reverse=True)

                for (n_idx, sched, c_idx, llc_idx), count in sorted_occupied:
                    # Defrag MUST stay within allowed boundaries
                    if not (min_allowed_sched <= sched <= target_sched):
                        continue

                    # If we already fully own this physical unit, LEAVE IT ALONE.
                    total_unit_logicals = len(self.topology[n_idx][(sched, llc_idx)][c_idx])
                    if count >= total_unit_logicals:
                        continue

                    unit = next((u for u in all_physical_units 
                                 if u['n_idx'] == n_idx and u['sched'] == sched and u['c_idx'] == c_idx and u['llc_idx'] == llc_idx), None)
                    if not unit: continue

                    foreigners = []
                    for other_pid, other_cores in assignments.items():
                        if other_pid == pid: continue
                        other_info = self.managed_processes[other_pid]
                        # Displace lower quality processes. Allow moving larger blocks (up to 4 cores) for realignment.
                        if other_info['quality'] >= info['quality'] or len(other_cores) > 4:
                            continue

                        for l in other_cores:
                            # Do not defrag by tearing apart a core that is shared for minimum backfill.
                            if len(shared_owners.get(l, [])) > 1:
                                continue
                            u_key = logical_to_unit.get(l)
                            if u_key and u_key == (n_idx, sched, c_idx, llc_idx):
                                foreigners.append((other_pid, l))

                    free_slots = list(unit['logical'])
                    swap_slots = [f[1] for f in foreigners]
                    total_available = len(free_slots) + len(swap_slots)

                    if total_available > 0:
                        # Only move cores from units that are:
                        # 1. NOT fully owned
                        # 2. In the SAME scheduling class (to prevent oscillation between Sched 1 and Sched 2)
                        # 3. Less or equally populated
                        other_cores = []
                        for l in current_cores:
                            u_key = logical_to_unit.get(l)
                            if u_key and u_key == (n_idx, sched, c_idx, llc_idx): continue

                            u_nidx, u_sched, u_cidx, u_llc = u_key
                            # Same scheduling class check
                            if u_sched != sched: continue

                            u_total = len(self.topology[u_nidx][(u_sched, u_llc)][u_cidx])
                            u_occupied = occupied_units.get(u_key, 0)

                            if u_occupied < u_total and u_occupied <= count:
                                other_cores.append(l)

                        if not other_cores: continue

                        to_move = min(total_available, len(other_cores))
                        for _ in range(to_move):
                            old_core = other_cores.pop()
                            assignments[pid].remove(old_core)
                            new_core = None

                            if free_slots:
                                new_core = free_slots.pop(0)
                                unit['logical'].remove(new_core)
                                # Global pool update
                                old_nidx, old_sched, old_cidx, old_llc = logical_to_unit[old_core]
                                old_unit = next(
                                    u for u in all_physical_units 
                                    if u['n_idx'] == old_nidx and u['sched'] == old_sched and u['c_idx'] == old_cidx and u['llc_idx'] == old_llc)
                                old_unit['logical'].append(old_core)
                            elif swap_slots:
                                swap_core = swap_slots.pop(0)
                                f_pid, _ = next(f for f in foreigners if f[1] == swap_core)
                                assignments[f_pid].remove(swap_core)
                                assignments[f_pid].append(old_core)
                                new_core = swap_core

                            if new_core is not None:
                                assignments[pid].append(new_core)

                        if to_move > 0:
                            logger.debug(
                                f"Defrag: Consolidated {to_move} cores for {getattr(info['process'], 'name_tag', pid)} into unit {c_idx} (Sched {sched}, NUMA {n_idx})")
                            occupied_units[(n_idx, sched, c_idx, llc_idx)] += to_move

            # 6. PHASE 4: COOPERATIVE GROWTH
            while True:
                added_any_this_pass = False
                for pid in sorted_pids:
                    current_cores = assignments[pid]
                    # we do not grow if we don't have a single core yet
                    if not current_cores: continue

                    info = self.managed_processes[pid]
                    load = self._load_per_assigned_core(pid, assignments)
                    if load <= self.growth_load_threshold or len(current_cores) >= info['max_cores']: continue

                    target_nidx, target_sched, _, _ = logical_to_unit[current_cores[0]]
                    min_allowed_sched = 1 if self.p_e_core_cpu and info['quality'] > 0 else 0

                    # Preference: Grow in our highest allowed class first, same NUMA node if possible
                    available = [u for u in all_physical_units
                                 if min_allowed_sched <= int(u['sched']) <= target_sched
                                 and u['logical']]

                    if not available: continue

                    # Filter available units to prefer the same NUMA node AND highest scheduling class
                    available.sort(key=lambda x: (x['n_idx'] != target_nidx, -x['sched']))
                    preferred_available = [u for u in available if u['sched'] == available[0]['sched']]

                    # Atomic growth: finish the current physical unit or take a fresh one
                    occ = {logical_to_unit[l] for l in current_cores if l in logical_to_unit}
                    target_unit = next((u for u in preferred_available if (u['n_idx'], u['sched'], u['c_idx'], u['llc_idx']) in occ),
                                       preferred_available[0])

                    while target_unit['logical'] and len(current_cores) < info['max_cores']:
                        current_cores.append(target_unit['logical'].pop(0))
                        added_any_this_pass = True

                if not added_any_this_pass: break

            # 7. PHASE 5: BALANCING (Steal from Idle)
            for pid in sorted_pids:
                current_cores = assignments[pid]
                # we do not steal if we do not have a single core yet
                if not current_cores: continue

                info = self.managed_processes[pid]
                load = self._load_per_assigned_core(pid, assignments)

                # Update the streak counter
                if load > self.steal_load_threshold:
                    streak = self._load_streak.get(pid, 0) + 1
                else:
                    streak = 0

                if not dry_run:
                    self._load_streak[pid] = streak

                # Only proceed to steal if the streak requirement is met
                if streak < self.steal_streak or len(current_cores) >= info['max_cores']:
                    continue

                target_nidx, target_sched, _, _ = logical_to_unit[current_cores[0]]
                for other_pid in reversed(sorted_pids):
                    if other_pid == pid or self._load_per_assigned_core(other_pid, assignments) >= self.idle_load_threshold:
                        continue

                    # Non-Aggression Rule
                    # A process MUST NOT steal if the victim is at or below its minimum requirement.
                    if len(assignments[other_pid]) <= self.managed_processes[other_pid]['min_cores']:
                        continue

                    # Check if the idle process is holding a core we are actually allowed to use
                    stolen = assignments[other_pid][-1]
                    stolen_nidx, stolen_sched, _, _ = logical_to_unit.get(stolen, (0, 0, 0, 0))

                    # NUMA affinity: Only steal from same NUMA node first
                    if stolen_nidx != target_nidx:
                        continue

                    # Quality Ceiling Rule
                    # Quality 2 can only steal from Sched 2 or Sched 1 (if allowed).
                    # We also ensure Quality 2 doesn't "downwardly" steal an E-core (Sched 0)
                    # if its own minimum requirement is P-cores.
                    min_allowed_sched = 1 if self.p_e_core_cpu and info['quality'] > 0 else 0
                    if min_allowed_sched <= stolen_sched <= target_sched:
                        assignments[other_pid].pop()
                        current_cores.append(stolen)
                        # TODO: break earlier to grow slower
                        if len(current_cores) >= info['max_cores']:
                            break

        return assignments

    def _apply_assignments(self, assignments: dict[int, list[int]]) -> None:
        """Apply calculated process affinity and update internal assignment state."""
        for pid, core_list in assignments.items():
            try:
                if pid not in self.managed_processes:
                    continue

                ps_proc = self.managed_processes[pid]['process']
                new_list = sorted(set(core_list))
                self.managed_processes[pid]['_current_assignments'] = new_list

                if not new_list:
                    logger.warning("No affinity assignment available for %s", getattr(ps_proc, 'name_tag', pid))
                    continue

                if new_list != sorted(ps_proc.cpu_affinity()):
                    ps_proc.cpu_affinity(new_list)
                    logger.debug(f"Affinity update: {getattr(ps_proc, 'name_tag', pid)} -> {new_list}")
            except psutil.NoSuchProcess:
                self._cleanup_pid(pid)
            except psutil.AccessDenied:
                continue

    def launch_process(self, args, min_cores: int = 1, max_cores: int | None = None, quality: int = 1,
                       instance: str | None = None, affinity: list[int] | None = None, **kwargs) -> psutil.Popen:
        min_cores, max_cores, quality = self._normalise_affinity_params(min_cores, max_cores, quality)
        ps_proc = psutil.Popen(args, **kwargs)

        # Attach the original Popen object so stdout/stderr can be accessed
        setattr(ps_proc, 'popen', ps_proc)
        setattr(ps_proc, 'name_tag', ps_proc.name()[:-4] + (f"/{instance}" if instance else ""))

        if affinity:
            ps_proc.cpu_affinity(affinity)
        elif self.auto_affinity:
            with self._lock:
                self.managed_processes[ps_proc.pid] = {
                    'process': ps_proc,
                    'min_cores': min_cores,
                    'max_cores': max_cores,
                    'quality': quality,
                    'instance': instance or ""
                }
                self._redistribute_cores()

        return ps_proc

    def assign_process(self,
                       proc: psutil.Process,
                       min_cores: int = 1,
                       max_cores: int | None = None,
                       quality: int = 1,
                       instance: str | None = None,
                       affinity: list[int] | None = None):
        min_cores, max_cores, quality = self._normalise_affinity_params(min_cores, max_cores, quality)
        setattr(proc, 'name_tag', proc.name()[:-4] + (f"/{instance}" if instance else ""))

        if affinity:
            proc.cpu_affinity(affinity)
        elif self.auto_affinity:
            with self._lock:
                self.managed_processes[proc.pid] = {
                    'process': proc,
                    'min_cores': min_cores,
                    'max_cores': max_cores,
                    'quality': quality,
                    'instance': instance or ""
                }
                self._redistribute_cores()

    @property
    def topology_json(self) -> dict:
        # Convert tuple keys to strings for JSON serialization
        res = {}
        for n_idx, groups in self.topology.items():
            res[str(n_idx)] = {}
            for group_key, cores in groups.items():
                # group_key is (sched, llc_idx)
                res[str(n_idx)][str(group_key)] = cores
        return res

    def export_topology(self) -> dict:
        return {
            'cpu_name': get_cpu_name(),
            'topology': self.topology_json,
            'cpu_sets': get_cpu_set_information(),
            'cache': get_cache_info(),
            'die': get_die_info(),
            'shared_cores': self.shared_cores(),
            'reuse_cores': self.reuse_cores,
            'max_core_sharing': self.max_core_sharing,
            'thresholds': {
                'growth_load_threshold': self.growth_load_threshold,
                'steal_load_threshold': self.steal_load_threshold,
                'idle_load_threshold': self.idle_load_threshold,
                'steal_streak': self.steal_streak
            }
        }

    def visualize_usage(self) -> bytes:
        """
        Generates a detailed CPU topology visualization with process overlays and prefixed IDs.
        """
        from io import BytesIO
        from matplotlib import pyplot as plt, patches

        # 1. Gather current state
        with self._lock:
            usage_map = self._usage_labels()

        plt.switch_backend('agg')
        plt.style.use('dark_background')

        # Colors
        p_color, e_color = '#2E6B9B', '#2B7A44'
        active_color, shared_color, excl_color = '#D4A017', '#C4512D', '#444444'
        text_color = '#E0E0E0'

        core_w, core_h = 0.8, 0.8
        phys_gap = 0.5
        y_spacing = 1.8

        # 2. Gather physical cores grouped by Numa -> Scheduling Class
        numa_groups = {}
        # Colors: green, blue, purple, gold, dark red, dark green
        class_colors = [e_color, p_color, '#6B4488', '#8C8544', '#9B2E4F', '#4E886B']

        for n_idx in sorted(self.topology.keys()):
            numa_groups[n_idx] = []

            # Group by (class label, llc_idx) for display so multi-tier CPUs
            # are not reduced to a misleading binary P/E model.
            display_groups = {}
            for (sched, llc_idx), cores_map in self.topology[n_idx].items():
                class_label = self._core_class_label(sched)
                d_key = (class_label, llc_idx, sched)
                if d_key not in display_groups:
                    display_groups[d_key] = []
                for core_idx, logicals in cores_map.items():
                    display_groups[d_key].append((core_idx, logicals, sched))

            sorted_keys = sorted(display_groups.keys(), key=lambda x: (-x[2], x[1], x[0]))
            for i, (class_label, llc_idx, sched) in enumerate(sorted_keys):
                # Order by scheduling class (descending) then core index (ascending)
                phys = sorted(display_groups[(class_label, llc_idx, sched)], key=lambda x: (-x[2], x[0]))

                if self.p_e_core_cpu:
                    title = class_label
                    color = p_color if self._is_performance_class(sched) else e_color
                    prefix = self._core_class_prefix(sched)
                    # If multiple clusters of the same class exist, add LLC info for clarity
                    if any(k != (class_label, llc_idx, sched) and k[0] == class_label
                           for k in display_groups.keys()):
                        title += f" (Cluster {llc_idx})"
                else:
                    # Non-hybrid (AMD, older Intel)
                    if len(display_groups) > 1:
                        title = f"CCD {llc_idx}"
                        prefix = f"C{llc_idx}-"
                        color = class_colors[i % len(class_colors)]
                    else:
                        title = "Cores"
                        color = p_color
                        prefix = ""

                numa_groups[n_idx].append((title, color, phys, prefix))

        fig, ax = plt.subplots(figsize=(20, 10))
        ax.set_aspect('equal')

        def draw_cluster(phys_cores, start_x, start_y, base_color, label_prefix, cluster_title):
            if cluster_title != "Cores":
                ax.text(start_x, start_y + 1.2, cluster_title, color=text_color, fontsize=9, fontweight='bold')
            max_x = start_x
            last_row = 0
            unique_sched_count = len({c[2] for c in phys_cores})
            for i, (c_idx, logicals, sched) in enumerate(phys_cores):
                row, col = divmod(i, 8)  # Fixed 8 cores per row
                x_base = start_x + col * (core_w * 2 + phys_gap)
                y_base = start_y - row * y_spacing
                last_row = max(last_row, row)

                # Check for a spanning process
                names_in_core = {
                    names[0]
                    for l_id in logicals
                    for names in [usage_map.get(l_id, [])]
                    if len(names) == 1
                }
                unique_name = list(names_in_core)[0] if len(names_in_core) == 1 else None

                for j, l_id in enumerate(logicals):
                    x = x_base + j * (core_w + 0.05)
                    proc_names = usage_map.get(l_id, [])
                    proc_name = proc_names[0] if len(proc_names) == 1 else None
                    is_shared = len(proc_names) > 1
                    is_excl = l_id in self.excluded_cores
                    face = shared_color if is_shared else (
                        active_color if proc_name else (excl_color if is_excl else base_color))

                    rect = patches.Rectangle((x, y_base), core_w, core_h, facecolor=face,
                                             edgecolor='white', linewidth=0.5)
                    ax.add_patch(rect)

                    # ID: P0, E12, etc.
                    ax.text(x + core_w / 2, y_base + core_h / 2, f"{label_prefix}{l_id}",
                            ha='center', va='center', color='white', fontsize=7, fontweight='bold')

                    # Show scheduling class if multiple classes exist in this cluster
                    if j == 0 and unique_sched_count > 1:
                        ax.text(x + core_w - 0.05, y_base + core_h - 0.05, f"{sched}",
                                ha='right', va='top', color='#CCCCCC', fontsize=5)

                    if is_shared:
                        label = f"{len(proc_names)} procs"
                        ax.text(x + core_w / 2, y_base - 0.2, label, ha='center', va='top',
                                fontsize=7, color=shared_color, fontweight='bold')
                    elif proc_name and not unique_name:
                        ax.text(x + core_w / 2, y_base - 0.2, proc_name, ha='center', va='top',
                                fontsize=7, color=active_color)

                if unique_name:
                    core_group_w = len(logicals) * (core_w + 0.05)
                    ax.text(x_base + core_group_w / 2, y_base - 0.2, unique_name, ha='center', va='top',
                            fontsize=8, color=active_color, fontweight='bold')

                max_x = max(max_x, x_base + (len(logicals) * (core_w + 0.05)))
            return max_x, last_row

        # 3. Draw Clusters
        curr_y = 0
        total_rows = 0
        numa_max_x = 0
        for n_idx, clusters in numa_groups.items():
            numa_start_y = curr_y
            numa_current_max_x = 0
            for i, (title, color, phys, prefix) in enumerate(clusters):
                # Stable color selection for non-hybrid systems
                if not self.p_e_core_cpu and len(clusters) > 1:
                    color = class_colors[i % len(class_colors)]

                max_x, rows = draw_cluster(phys, 0.5, curr_y, color, prefix, title)
                numa_current_max_x = max(numa_current_max_x, max_x)
                curr_y -= (rows + 2.2) * y_spacing
                total_rows += (rows + 2.2)

            numa_max_x = max(numa_max_x, numa_current_max_x)

            # Draw NUMA box
            if len(numa_groups) >= 1:
                numa_box_y = curr_y + y_spacing
                numa_box_h = numa_start_y - numa_box_y + 2.2
                rect = patches.Rectangle((-0.5, numa_box_y), numa_current_max_x + 1, numa_box_h,
                                         facecolor='none', edgecolor='#666666', linestyle='--', linewidth=1)
                ax.add_patch(rect)
                ax.text(-0.4, numa_start_y + 1.8, f"NUMA NODE {n_idx}", color='#AAAAAA',
                        fontsize=12, fontweight='bold', ha='left')
                curr_y -= 2.0  # Extra space between NUMA nodes
                total_rows += 1

        # 4. Legend
        # Calculates total rows to determine a dynamic offset.
        # total_rows was calculated during drawing

        # The fewer the rows, the larger the relative offset needs to be
        # to maintain the same physical distance.
        dynamic_offset = -0.25 / (total_rows * 0.5) if total_rows > 0 else -0.20

        legend_elements = [
            patches.Patch(facecolor=p_color, label='P-Core (Idle)'),
            patches.Patch(facecolor=e_color, label='E-Core (Idle)'),
            patches.Patch(facecolor=active_color, label='Managed Process'),
            patches.Patch(facecolor=shared_color, label='Shared Core'),
            patches.Patch(facecolor=excl_color, label='System Reserved')
        ]

        ax.legend(handles=legend_elements, loc='upper center',
                  bbox_to_anchor=(0.5, dynamic_offset),
                  ncol=5, fancybox=True, shadow=True)

        ax.autoscale_view()
        ax.axis('off')
        plt.title(f"CPU Resource Allocation: {get_cpu_name()}", color=text_color, fontsize=16, pad=20)

        # This ensures the legend and process names aren't cut off or overlapping
        plt.tight_layout()
        # Add extra bottom margin specifically for the legend and labels
        plt.subplots_adjust(bottom=0.15)

        buf = BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', facecolor='#1C1C1C')
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    def visualize_cache(self) -> bytes:
        """
        Generates a detailed CPU cache hierarchy visualization.
        """
        from matplotlib import pyplot as plt, patches

        def format_size(size):
            if size >= 1024 * 1024: return f"{size / (1024 * 1024):.0f}M"
            if size >= 1024: return f"{size / 1024:.0f}K"
            return f"{size}B"

        p_mask = get_p_core_affinity()
        e_mask = get_e_core_affinity()
        p_cores_raw = get_cpus_from_affinity(p_mask)
        e_cores_raw = get_cpus_from_affinity(e_mask)

        # Order cores by topology to ensure SMT threads are adjacent and follow physical CCDs
        p_cores, e_cores = [], []
        for n_idx in sorted(self.topology.keys()):
            for (sched, llc_idx) in sorted(
                    self.topology[n_idx].keys(),
                    key=lambda x: (not self._is_performance_class(x[0]), -x[0], x[1])
            ):
                is_p = self._is_performance_class(sched)
                target = p_cores if is_p else e_cores
                for c_idx in sorted(self.topology[n_idx][(sched, llc_idx)].keys()):
                    for l_idx in self.topology[n_idx][(sched, llc_idx)][c_idx]:
                        if is_p and l_idx in p_cores_raw:
                            target.append(l_idx)
                        elif not is_p and l_idx in e_cores_raw:
                            target.append(l_idx)

        cache_info = get_cache_info()

        plt.switch_backend('agg')
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(20, 12))
        ax.set_aspect('equal')

        # Colors
        p_core_color, e_core_color = '#2E6B9B', '#2B7A44'
        l1_color, l2_color, l3_color = '#9B2E4F', '#6B4488', '#8C8544'
        text_color = '#E0E0E0'

        core_width, core_height = 0.8, 0.8
        core_gap = 0.4
        x_spacing = core_width + core_gap
        y_spacing = 1.2
        l3_height, l3_spacing = 0.8, 0

        # Layout dimensions
        p_cores_per_row = max(1, len(p_cores) // 2)
        e_cores_per_row = max(1, len(e_cores) // 2)
        p_rows = (len(p_cores) + p_cores_per_row - 1) // p_cores_per_row
        e_rows = (len(e_cores) + e_cores_per_row - 1) // e_cores_per_row if e_cores else 0

        p_cores_width = p_cores_per_row * core_width + (p_cores_per_row - 1) * core_gap
        e_cores_width = e_cores_per_row * core_width + (e_cores_per_row - 1) * core_gap
        e_section_start = p_cores_width + x_spacing
        total_width = p_cores_width + ((e_cores_width + x_spacing) if e_cores else 0)

        l2_groups = {tuple(sorted(c['cores'])): c for c in cache_info if c['level'] == 2}

        core_to_numa, core_to_llc, core_to_sched = {}, {}, {}
        for n_idx, groups in self.topology.items():
            for group_key, cores_map in groups.items():
                sched, llc_idx = group_key
                for logicals in cores_map.values():
                    for l_idx in logicals:
                        core_to_numa[l_idx] = n_idx
                        core_to_llc[l_idx] = llc_idx
                        core_to_sched[l_idx] = sched

        # Consolidate L3 caches by CCD/Cluster for visualization if they appear per-core
        l3_by_llc = {}
        for l3 in cache_info:
            if l3['level'] != 3 or not l3['cores']: continue
            llc_key = (core_to_numa.get(l3['cores'][0], 0), core_to_llc.get(l3['cores'][0], 0))
            if llc_key not in l3_by_llc:
                l3_by_llc[llc_key] = {'level': 3, 'type': l3['type'], 'size': 0, 'cores': set(), 'llc_key': llc_key}
            l3_by_llc[llc_key]['cores'].update(l3['cores'])
            l3_by_llc[llc_key]['size'] = max(l3_by_llc[llc_key]['size'], l3['size'])
        
        l3_caches = list(l3_by_llc.values())
        rows_with_l3 = set()
        for l3_cache in l3_caches:
            shared = sorted(list(l3_cache['cores']))
            # Find which row(s) these cores belong to
            for c in shared:
                if c in p_cores:
                    rows_with_l3.add(p_cores.index(c) // p_cores_per_row)
                elif c in e_cores:
                    rows_with_l3.add(e_cores.index(c) // e_cores_per_row)

        p_unique_sched = len({core_to_sched.get(c, 0) for c in p_cores}) > 1
        e_unique_sched = len({core_to_sched.get(c, 0) for c in e_cores}) > 1

        numa_extents, llc_extents = {}, {}
        def update_extent(ext_dict, key, x, y, w, h):
            if key is None: return
            if key not in ext_dict: ext_dict[key] = [x, x + w, y, y + h]
            else:
                ext = ext_dict[key]
                ext[0], ext[1] = min(ext[0], x), max(ext[1], x + w)
                ext[2], ext[3] = min(ext[2], y), max(ext[3], y + h)

        # Draw P-Cores
        for i, core in enumerate(p_cores):
            row = i // p_cores_per_row
            x = (i % p_cores_per_row) * x_spacing
            y = sum(y_spacing * 3 + (l3_spacing if r in rows_with_l3 else 0) for r in range(row))
            
            ax.add_patch(patches.Rectangle((x, y), core_width, core_height, facecolor=p_core_color, edgecolor='white', linewidth=0.5))
            ax.text(x + core_width / 2, y + core_height / 2, f"P{core}", ha='center', va='center', color=text_color, fontsize=8)
            if p_unique_sched:
                ax.text(x + core_width - 0.05, y + core_height - 0.05, f"{core_to_sched.get(core, 0)}",
                        ha='right', va='top', color='#CCCCCC', fontsize=5)
            llc_key = (core_to_numa.get(core, 0), core_to_llc.get(core, 0))
            update_extent(numa_extents, core_to_numa.get(core), x, y - 2.4, x_spacing, 2.4 + core_height)
            update_extent(llc_extents, llc_key, x, y - 2.4, x_spacing, 2.4 + core_height)

            # Draw L1/L2 caches for P-cores
            for cache in cache_info:
                if cache['level'] == 1 and core in cache['cores']:
                    cores_in_cache = [c for c in cache['cores'] if c in p_cores]
                    cores_in_row = [c for c in cores_in_cache if (p_cores.index(c) // p_cores_per_row) == row]
                    if cores_in_row and core == min(cores_in_row):
                        y_off = -0.6 if cache['type'] == 2 else -1.0
                        label = f"L1-{'I' if cache['type'] == 2 else 'D'} {format_size(cache['size'])}"
                        w = x_spacing * len(cores_in_row) - 0.4
                        ax.add_patch(patches.Rectangle((x, y + y_off), w, 0.4, facecolor=l1_color, edgecolor='white', linewidth=0.5))
                        ax.text(x + w / 2, y + y_off + 0.2, label, ha='center', va='center', fontsize=7, color=text_color)
            for group in l2_groups.values():
                if core in group['cores']:
                    cores_in_cache = [c for c in group['cores'] if c in p_cores]
                    cores_in_row = [c for c in cores_in_cache if (p_cores.index(c) // p_cores_per_row) == row]
                    if cores_in_row and core == min(cores_in_row):
                        w = x_spacing * len(cores_in_row) - 0.4
                        ax.add_patch(patches.Rectangle((x, y - 1.4), w, 0.4, facecolor=l2_color, edgecolor='white', linewidth=0.5))
                        ax.text(x + w / 2, y - 1.2, f"L2 {format_size(group['size'])}", ha='center', va='center', fontsize=7, color=text_color)
                        break

        # Draw E-Cores
        for i, core in enumerate(e_cores):
            row, x = i // e_cores_per_row, (i % e_cores_per_row) * x_spacing + e_section_start
            y = sum(y_spacing * 3 + (l3_spacing if r in rows_with_l3 else 0) for r in range(row))
            ax.add_patch(patches.Rectangle((x, y), core_width, core_height, facecolor=e_core_color, edgecolor='white', linewidth=0.5))
            ax.text(x + core_width / 2, y + core_height / 2, f"E{core}", ha='center', va='center', color=text_color, fontsize=8)
            if e_unique_sched:
                ax.text(x + core_width - 0.05, y + core_height - 0.05, f"{core_to_sched.get(core, 0)}",
                        ha='right', va='top', color='#CCCCCC', fontsize=5)
            llc_key = (core_to_numa.get(core, 0), core_to_llc.get(core, 0))
            update_extent(numa_extents, core_to_numa.get(core), x, y - 2.4, x_spacing, 2.4 + core_height)
            update_extent(llc_extents, llc_key, x, y - 2.4, x_spacing, 2.4 + core_height)

            # Draw L1/L2 caches for E-cores
            for cache in cache_info:
                if cache['level'] == 1 and core in cache['cores']:
                    cores_in_cache = [c for c in cache['cores'] if c in e_cores]
                    cores_in_row = [c for c in cores_in_cache if (e_cores.index(c) // e_cores_per_row) == row]
                    if cores_in_row and core == min(cores_in_row):
                        y_off = -0.6 if cache['type'] == 2 else -1.0
                        label = f"L1-{'I' if cache['type'] == 2 else 'D'} {format_size(cache['size'])}"
                        w = x_spacing * len(cores_in_row) - 0.4
                        ax.add_patch(patches.Rectangle((x, y + y_off), w, 0.4, facecolor=l1_color, edgecolor='white', linewidth=0.5))
                        ax.text(x + w / 2, y + y_off + 0.2, label, ha='center', va='center', fontsize=7, color=text_color)
            for group in l2_groups.values():
                if core in group['cores']:
                    cores_in_cache = [c for c in group['cores'] if c in e_cores]
                    cores_in_row = [c for c in cores_in_cache if (e_cores.index(c) // e_cores_per_row) == row]
                    if cores_in_row and core == min(cores_in_row):
                        w = x_spacing * len(cores_in_row) - 0.4
                        ax.add_patch(patches.Rectangle((x, y - 1.4), w, 0.4, facecolor=l2_color, edgecolor='white', linewidth=0.5))
                        ax.text(x + w / 2, y - 1.2, f"L2 {format_size(group['size'])}", ha='center', va='center', fontsize=7, color=text_color)
                        break

        # Draw L3
        for l3_cache in l3_caches:
            llc_key = l3_cache.get('llc_key')
            if llc_key in llc_extents:
                mx1, mx2, my1, my2 = llc_extents[llc_key]
                y_l3 = my1  # Position at the bottom of the extent
                w_l3 = mx2 - mx1 - 0.4
                ax.add_patch(patches.Rectangle((mx1, y_l3), w_l3, l3_height, facecolor=l3_color, edgecolor='white', linewidth=0.5))
                ax.text(mx1 + w_l3 / 2, y_l3 + l3_height / 2, f"L3 {format_size(l3_cache['size'])}", ha='center', va='center', color=text_color, fontsize=8)

        # Draw Boxes
        if len(llc_extents) > 1:
            for llc_key, (mx1, mx2, my1, my2) in llc_extents.items():
                llc_idx = llc_key[1]
                ax.add_patch(patches.Rectangle((mx1 - 0.1, my1 - 0.1), mx2 - mx1 + 0.2, my2 - my1 + 0.2, facecolor='none', edgecolor='#444444', linestyle=':', linewidth=0.5))
                scheds = {
                    core_to_sched.get(core, 0)
                    for core, core_llc in core_to_llc.items()
                    if (core_to_numa.get(core, 0), core_llc) == llc_key
                }
                if self.p_e_core_cpu and len(scheds) == 1:
                    sched = next(iter(scheds))
                    label = f"{self._core_class_label(sched)} Cluster {llc_idx}"
                else:
                    label = f"Cluster {llc_idx}" if self.p_e_core_cpu else f"CCD {llc_idx}"
                ax.text(mx1 + 0.1, my2 - 0.1, label, color='#888888', fontsize=8, ha='left', va='top')
        for n_idx, (mx1, mx2, my1, my2) in numa_extents.items():
            ax.add_patch(patches.Rectangle((mx1 - 0.2, my1 - 0.5), mx2 - mx1 + 0.4, my2 - my1 + 1.6, facecolor='none', edgecolor='#666666', linestyle='--', linewidth=1))
            ax.text(mx1 + 0.1, my2 + 0.9, f"NUMA NODE {n_idx}", color='#AAAAAA', fontsize=10, fontweight='bold', ha='left')

        ax.set_xlim(-1, total_width + 1)
        ax.set_ylim(-4, max(p_rows, e_rows) * y_spacing * 3 + 1)
        ax.axis('off')
        plt.title(f"CPU Topology & Cache: {get_cpu_name()}", color=text_color, y=0.98)
        plt.tight_layout()
        buf = BytesIO()
        plt.savefig(buf, format='png', facecolor='#1C1C1C')
        plt.close(fig)
        buf.seek(0)
        return buf.read()
