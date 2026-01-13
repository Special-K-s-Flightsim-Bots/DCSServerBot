import logging
import psutil
import subprocess
import sys
import threading

if sys.platform == 'win32':
    from .win32.cpu import get_cpu_set_information, get_e_core_affinity
else:
    from .linux.cpu import get_cpu_set_information

logger = logging.getLogger(__name__)

_all_ = ['ProcessManager']


class ProcessManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ProcessManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, excluded_cores: list[int] | None = None, auto_affinity: bool = True):
        if getattr(self, '_initialized', False):
            return

        with self._lock:
            if getattr(self, '_initialized', False):
                return

            self.auto_affinity = auto_affinity
            self.p_e_core_cpu = get_e_core_affinity() > 0
            self.excluded_cores = excluded_cores or []
            self.topology = self._get_physical_topology()
            self.managed_processes: dict[int, dict] = {}
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

    def _get_physical_topology(self) -> dict[int, dict[int, list[int]]]:
        """Groups logical processors by physical CoreIndex and Scheduling Class."""
        cpu_sets = get_cpu_set_information()
        topo = {}

        for cpu in cpu_sets:
            l_idx   = cpu["Logical Processor Index"]
            sched   = cpu["Scheduling Class"]
            c_idx   = cpu["Core Index"]

            if sched not in topo:
                topo[sched] = {}
            if c_idx not in topo[sched]:
                topo[sched][c_idx] = []

            topo[sched][c_idx].append(l_idx)

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
                        if p.pid in self.managed_processes:
                            del self.managed_processes[p.pid]
                    self._redistribute_cores(cooperative=False)
            else:
                # Every 2 seconds, try a 'Cooperative' load-based pass
                with self._lock:
                    self._redistribute_cores(cooperative=True)

    def _update_load_metrics(self):
        """Refreshes the CPU load percentage for all managed processes."""
        for pid, info in self.managed_processes.items():
            try:
                # interval=None makes it non-blocking (uses time since last call)
                self._load_cache[pid] = info['process'].cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._load_cache[pid] = 0.0

    def _redistribute_cores(self, cooperative: bool = False):
        """Redistributes cores using fair minimums. Growth only occurs in cooperative mode under load."""
        if not self.managed_processes:
            return

        self._update_load_metrics()

        # 1. BUILD HARDWARE STATE MAP
        all_physical_units = []
        logical_to_unit = {}
        for sched in sorted(self.topology.keys(), reverse=True):
            for c_idx in sorted(self.topology[sched].keys()):
                logical = [l for l in self.topology[sched][c_idx] if l not in self.excluded_cores]
                if logical:
                    current_owners = {}
                    for l in logical:
                        for pid, p_info in self.managed_processes.items():
                            if l in p_info.get('_current_assignments', []):
                                current_owners[l] = {'pid': pid, 'quality': p_info['quality']}
                        logical_to_unit[l] = (sched, c_idx)

                    all_physical_units.append({
                        'sched': sched,
                        'c_idx': c_idx,
                        'logical': logical,
                        'is_p': (sched > 0) if self.p_e_core_cpu else True,
                        'owners': current_owners
                    })

        # 2. IDENTIFY AND PURGE DISPLACED PROCESSES
        pids_to_reset = set()
        sorted_pids = sorted(self.managed_processes.keys(),
                             key=lambda p: (self.managed_processes[p]['quality'], self._load_cache.get(p, 0.0)),
                             reverse=True)

        # Map logical cores to their current owners for an easier lookup
        current_owner_map = {}
        for pid, info in self.managed_processes.items():
            for l in info.get('_current_assignments', []):
                current_owner_map[l] = pid

        temp_units = [dict(u, logical=list(u['logical'])) for u in all_physical_units]

        for pid in sorted_pids:
            info = self.managed_processes[pid]
            # we do not need to reassign processes with the lowest quality requirements
            if info['quality'] == 0:
                continue

            # we want to try the max available scheduling classes
            max_available_sched = max({x[0] for x in logical_to_unit.values()})

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
                self.managed_processes[pid]['_current_assignments'] = []
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
            # try to fill the highest class first
            eligible.sort(key=lambda x: x['sched'], reverse=True)

            # Step A: Physical Consolidation
            # Try to complete units we already touch or take fresh clean units.
            for unit in eligible:
                if needed <= 0: break
                if any(l in current_cores for l in self.topology[unit['sched']][unit['c_idx']]) or not current_cores:
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

        # only re-arrange in cooperative mode
        if cooperative:

            # 4. PHASE 2: DEFRAGMENTATION
            for pid in sorted_pids:
                info = self.managed_processes[pid]
                current_cores = assignments[pid]
                if not current_cores: continue

                target_sched = max([logical_to_unit[x][0] for x in current_cores])
                min_allowed_sched = 1 if self.p_e_core_cpu and info['quality'] > 0 else 0

                occupied_units = {}
                for l in current_cores:
                    unit_key = logical_to_unit.get(l)
                    if unit_key:
                        occupied_units[unit_key] = occupied_units.get(unit_key, 0) + 1

                # Sort units by occupancy (most populated first)
                sorted_occupied = sorted(occupied_units.items(), key=lambda x: x[1], reverse=True)

                for (sched, c_idx), count in sorted_occupied:
                    # Defrag MUST stay within allowed boundaries
                    if not (min_allowed_sched <= sched <= target_sched):
                        continue

                    # If we already fully own this physical unit, LEAVE IT ALONE.
                    total_unit_logicals = len(self.topology[sched][c_idx])
                    if count >= total_unit_logicals:
                        continue

                    unit = next((u for u in all_physical_units if u['sched'] == sched and u['c_idx'] == c_idx), None)
                    if not unit: continue

                    foreigners = []
                    for other_pid, other_cores in assignments.items():
                        if other_pid == pid: continue
                        other_info = self.managed_processes[other_pid]
                        # Only swap with processes that are safe to move (1 core) and lower quality
                        if other_info['quality'] >= info['quality'] or len(other_cores) > 1:
                            continue

                        for l in other_cores:
                            if logical_to_unit.get(l) == (sched, c_idx):
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
                            if u_key == (sched, c_idx): continue

                            u_sched, u_cidx = u_key
                            # Same scheduling class check
                            if u_sched != sched: continue

                            u_total = len(self.topology[u_sched][u_cidx])
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
                                old_sched, old_cidx = logical_to_unit[old_core]
                                old_unit = next(
                                    u for u in all_physical_units if u['sched'] == old_sched and u['c_idx'] == old_cidx)
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
                                f"Defrag: Consolidated {to_move} cores for {getattr(info['process'], 'name_tag', pid)} into unit {c_idx} (Sched {sched})")
                            occupied_units[(sched, c_idx)] += to_move

            # 5. PHASE 3: COOPERATIVE GROWTH
            while True:
                added_any_this_pass = False
                for pid in sorted_pids:
                    current_cores = assignments[pid]
                    # we do not grow if we don't have a single core yet
                    if not current_cores: continue

                    info = self.managed_processes[pid]
                    load = self._load_cache.get(pid, 0.0)
                    if load <= 70.0 or len(current_cores) >= info['max_cores']: continue

                    target_sched = max([logical_to_unit[x][0] for x in current_cores])
                    min_allowed_sched = 1 if self.p_e_core_cpu and info['quality'] > 0 else 0

                    # Preference: Grow in our highest allowed class first (target_sched)
                    # Then look at lower classes if full.
                    available = [u for u in all_physical_units
                                 if min_allowed_sched <= int(u['sched']) <= target_sched
                                 and u['logical']]

                    if not available: continue

                    # Filter available units to prefer the highest scheduling class we are allowed
                    best_sched = max(u['sched'] for u in available)
                    preferred_available = [u for u in available if u['sched'] == best_sched]

                    # Atomic growth: finish the current physical unit or take a fresh one
                    occ = {logical_to_unit[l] for l in current_cores if l in logical_to_unit}
                    target_unit = next((u for u in preferred_available if (u['sched'], u['c_idx']) in occ),
                                       preferred_available[0])

                    while target_unit['logical'] and len(current_cores) < info['max_cores']:
                        current_cores.append(target_unit['logical'].pop(0))
                        added_any_this_pass = True

                if not added_any_this_pass: break

            # 6. PHASE 3: BALANCING (Steal from Idle)
            for pid in sorted_pids:
                current_cores = assignments[pid]
                # we do not steal if we do not have a single core yet
                if not current_cores: continue

                info = self.managed_processes[pid]
                load = self._load_cache.get(pid, 0.0)

                # Update the streak counter
                if load > 85.0:
                    self._load_streak[pid] = self._load_streak.get(pid, 0) + 1
                else:
                    self._load_streak[pid] = 0

                # Only proceed to steal if the streak requirement is met (e.g., 3 runs = 6 seconds)
                if self._load_streak[pid] < 3 or len(current_cores) >= info['max_cores']:
                    continue

                target_sched = max([logical_to_unit[x][0] for x in current_cores])
                for other_pid in reversed(sorted_pids):
                    if other_pid == pid or self._load_cache.get(other_pid, 0.0) >= 20.0: continue

                    # Non-Aggression Rule
                    # A process MUST NOT steal if the victim is at or below its minimum requirement.
                    if len(assignments[other_pid]) <= self.managed_processes[other_pid]['min_cores']:
                        continue

                    # Check if the idle process is holding a core we are actually allowed to use
                    stolen = assignments[other_pid][-1]
                    stolen_sched, _ = logical_to_unit.get(stolen, (0, 0))

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

        # 7. Apply Affinity and Update Internal State
        for pid, core_list in assignments.items():
            try:
                ps_proc = self.managed_processes[pid]['process']
                new_list = sorted(core_list)
                self.managed_processes[pid]['_current_assignments'] = new_list

                if new_list and new_list != sorted(ps_proc.cpu_affinity()):
                    ps_proc.cpu_affinity(new_list)
                    logger.debug(f"Affinity update: {getattr(ps_proc, 'name_tag', pid)} -> {new_list}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def launch_process(self, args, min_cores: int = 1, max_cores: int | None = None, quality: int = 1,
                       instance: str | None = None, affinity: list[int] | None = None, **kwargs):
        proc = subprocess.Popen(args, **kwargs)
        ps_proc = psutil.Process(proc.pid)

        # Attach the original Popen object so stdout/stderr can be accessed
        setattr(ps_proc, 'popen', proc)
        setattr(ps_proc, 'name_tag', ps_proc.name()[:-4] + (f"/{instance}" if instance else ""))
        setattr(ps_proc, 'stdout', proc.stdout)
        setattr(ps_proc, 'stderr', proc.stderr)

        if affinity:
            ps_proc.cpu_affinity(affinity)
        elif self.auto_affinity:
            with self._lock:
                self.managed_processes[proc.pid] = {
                    'process': ps_proc,
                    'min_cores': min_cores,
                    'max_cores': max_cores or 999,
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
        setattr(proc, 'name_tag', proc.name()[:-4] + (f"/{instance}" if instance else ""))

        if affinity:
            proc.cpu_affinity(affinity)
        elif self.auto_affinity:
            with self._lock:
                self.managed_processes[proc.pid] = {
                    'process': proc,
                    'min_cores': min_cores,
                    'max_cores': max_cores or 999,
                    'quality': quality,
                    'instance': instance or ""
                }
                self._redistribute_cores()

    def visualize_usage(self) -> bytes:
        """
        Generates a detailed CPU topology visualization with process overlays and prefixed IDs.
        """
        from io import BytesIO
        from matplotlib import pyplot as plt, patches
        from core.process.win32.cpu import get_cpu_name

        # 1. Gather current state
        with self._lock:
            usage_map = {}
            for info in self.managed_processes.values():
                try:
                    name = getattr(info['process'], 'name_tag', info['process'].name()).replace('/', '\n')
                    for cpu in info['process'].cpu_affinity():
                        usage_map[cpu] = name
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        plt.switch_backend('agg')
        plt.style.use('dark_background')

        # Colors
        p_color, e_color = '#2E6B9B', '#2B7A44'
        active_color, excl_color = '#D4A017', '#444444'
        text_color = '#E0E0E0'

        core_w, core_h = 0.8, 0.8
        phys_gap = 0.5
        y_spacing = 1.8

        # 2. Gather physical cores grouped by Performance vs. Efficiency tiers
        # Performance: Scheduling Class > 0, Efficiency: Scheduling Class 0
        p_phys = []
        e_phys = []
        for sched in sorted(self.topology.keys(), reverse=True):
            target_list = p_phys if not self.p_e_core_cpu or sched > 0 else e_phys
            target_list.extend(sorted(self.topology[sched].items()))

        fig, ax = plt.subplots(figsize=(20, 10))
        ax.set_aspect('equal')

        def draw_cluster(phys_cores, start_x, start_y, base_color, label_prefix):
            max_x = start_x
            last_row = 0
            for i, (c_idx, logicals) in enumerate(phys_cores):
                row, col = divmod(i, 8)  # Fixed 8 cores per row
                x_base = start_x + col * (core_w * 2 + phys_gap)
                y_base = start_y - row * y_spacing
                last_row = max(last_row, row)

                # Check for a spanning process
                names_in_core = {usage_map.get(l_id) for l_id in logicals if usage_map.get(l_id)}
                unique_name = list(names_in_core)[0] if len(names_in_core) == 1 else None

                for j, l_id in enumerate(logicals):
                    x = x_base + j * (core_w + 0.05)
                    proc_name = usage_map.get(l_id)
                    is_excl = l_id in self.excluded_cores
                    face = active_color if proc_name else (excl_color if is_excl else base_color)

                    rect = patches.Rectangle((x, y_base), core_w, core_h, facecolor=face,
                                             edgecolor='white', linewidth=0.5)
                    ax.add_patch(rect)

                    # Prefixed ID: P0, E12, etc.
                    ax.text(x + core_w / 2, y_base + core_h / 2, f"{label_prefix}{l_id}",
                            ha='center', va='center', color='white', fontsize=8, fontweight='bold')

                    if proc_name and not unique_name:
                        ax.text(x + core_w / 2, y_base - 0.2, proc_name, ha='center', va='top',
                                fontsize=7, color=active_color)

                if unique_name:
                    core_group_w = len(logicals) * (core_w + 0.05)
                    ax.text(x_base + core_group_w / 2, y_base - 0.2, unique_name, ha='center', va='top',
                            fontsize=8, color=active_color, fontweight='bold')

                max_x = max(max_x, x_base + (len(logicals) * (core_w + 0.05)))
            return max_x, last_row

        # 3. Draw Clusters with prefixes
        _, p_rows = draw_cluster(p_phys, 0, 0, p_color, "P")

        # Start E-cores below P-cores
        e_start_y = -(p_rows + 1) * y_spacing if p_phys else 0
        _, e_rows = draw_cluster(e_phys, 0, e_start_y, e_color, "E")

        # 4. Legend
        # Calculates total rows to determine a dynamic offset.
        # We add 1 to rows because they are 0-indexed.
        total_rows = (p_rows + 1 if p_phys else 0) + (e_rows + 1 if e_phys else 0)

        # The fewer the rows, the larger the relative offset needs to be
        # to maintain the same physical distance.
        dynamic_offset = -0.25 / (total_rows * 0.5) if total_rows > 0 else -0.20

        legend_elements = [
            patches.Patch(facecolor=p_color, label='P-Core (Idle)'),
            patches.Patch(facecolor=e_color, label='E-Core (Idle)'),
            patches.Patch(facecolor=active_color, label='Managed Process'),
            patches.Patch(facecolor=excl_color, label='System Reserved')
        ]

        ax.legend(handles=legend_elements, loc='upper center',
                  bbox_to_anchor=(0.5, dynamic_offset),
                  ncol=4, fancybox=True, shadow=True)

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
