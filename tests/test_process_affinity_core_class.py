"""The core-class a CPU is grouped by must describe PERFORMANCE, not the OS scheduling value.

A symmetric CPU (all cores identical) must produce a single class, whatever values the OS
reports next to it: a split class on a symmetric machine starves auto-affinity (only the class
above the floor is ever handed to a server) and mislabels the topology pictures as P-cores and
E-cores that do not exist.

The fixtures below build the CPU-set shape both platform providers emit, ``Core Class`` being
the tier the caller groups by.
"""
from __future__ import annotations

import inspect
import sys
import threading
import types

from core.process import processmanager as pm


# --------------------------------------------------------------------------- fixtures

def _lp(lp: int, core: int, core_class: int, sched_class: int, rank: int | None = None) -> dict:
    return {
        "CPU Id": 256 + lp,
        "Logical Processor Index": lp,
        "Core Index": core,
        "Efficiency Class": core_class,
        "Core Class": core_class,
        "Core Rank": sched_class if rank is None else rank,
        "Scheduling Class": sched_class,
        "Numa Node Index": 0,
        "Last Level Cache Index": 0,
    }


def symmetric_cpu(preferred_lps: set[int] | None = None) -> list[dict]:
    """10 identical cores, 2 threads each, where the OS ranks some logical CPUs above the rest.

    The default split (0-3 ranked above 4-19) is the shape a real 10-core CPU reported.
    """
    preferred = {0, 1, 2, 3} if preferred_lps is None else set(preferred_lps)
    return [_lp(c * 2 + t, c * 2, 0, 1 if (c * 2 + t) in preferred else 0)
            for c in range(10) for t in range(2)]


def hybrid_cpu(e_core_rank: int = 0) -> list[dict]:
    """4 P-cores with 2 threads each (tier 1) followed by 8 single-threaded E-cores (tier 0)."""
    cpus, lp = [], 0
    for c in range(4):
        for _ in range(2):
            cpus.append(_lp(lp, 2 * c, 1, 1, rank=0))
            lp += 1
    for _ in range(8):
        cpus.append(_lp(lp, lp, 0, 0, rank=e_core_rank))
        lp += 1
    return cpus


def _manager(monkeypatch, cpu_sets: list[dict]):
    monkeypatch.setattr(pm, "get_cpu_set_information", lambda: cpu_sets)
    monkeypatch.setattr(pm, "get_cache_info", lambda: [])
    monkeypatch.setattr(pm, "get_die_info", lambda: [])

    topology = pm.ProcessManager._get_physical_topology()
    core_rank = pm.ProcessManager._get_core_ranks()
    classes = sorted({int(sched) for groups in topology.values() for sched, _ in groups})
    mgr = types.SimpleNamespace(
        topology=topology,
        core_rank=core_rank,
        scheduling_classes=classes,
        p_e_core_cpu=len(classes) > 1,
        excluded_cores=[],
        managed_processes={},
        reuse_cores=True,
        max_core_sharing=2,
        auto_affinity=True,
        growth_load_threshold=70.0,
        steal_load_threshold=85.0,
        idle_load_threshold=20.0,
        steal_streak=3,
        _lock=threading.RLock(),
        _load_cache={},
        _load_streak={},
    )
    mgr.performance_sched_class = max(classes) if classes else 0
    mgr.efficiency_sched_class = min(classes) if classes else 0
    # every shipped method, bound to this stand-in manager: no watcher thread, no singleton.
    # A staticmethod is attached unwrapped - it takes no instance argument.
    for name in dir(pm.ProcessManager):
        attr = getattr(pm.ProcessManager, name)
        if not callable(attr) or name.startswith("__"):
            continue
        if isinstance(inspect.getattr_static(pm.ProcessManager, name), staticmethod):
            setattr(mgr, name, attr)
        else:
            setattr(mgr, name, types.MethodType(attr, mgr))
    mgr._usage_labels = lambda: {}
    return mgr


def _draw(monkeypatch, mgr, method) -> dict[str, list[str]]:
    """Run one shipped render and return the core labels and the legend entries it drew."""
    labels: list[str] = []
    legend: list[str | None] = []

    class _Patch:
        def __init__(self, **kwargs):
            legend.append(kwargs.get("label"))

    class _Ax:
        def text(self, x, y, s, **kwargs):
            labels.append(s)

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    class _Fig:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    plt_stub = types.SimpleNamespace(
        switch_backend=lambda *a, **k: None,
        style=types.SimpleNamespace(use=lambda *a, **k: None),
        subplots=lambda *a, **k: (_Fig(), _Ax()),
        title=lambda *a, **k: None,
        tight_layout=lambda *a, **k: None,
        subplots_adjust=lambda *a, **k: None,
        savefig=lambda *a, **k: None,
        close=lambda *a, **k: None,
        Rectangle=lambda *a, **k: None,
        Patch=_Patch,
    )
    monkeypatch.setitem(sys.modules, "matplotlib",
                        types.SimpleNamespace(pyplot=plt_stub, patches=plt_stub))
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", plt_stub)

    method(mgr)
    core_labels = [label for label in labels
                   if label.isdigit() or (len(label) > 1 and label[0] in "PE" and label[1:].isdigit())]
    return {"cores": core_labels, "legend": [entry for entry in legend if entry]}


class _FakeProc:
    def __init__(self, pid: int = 4242):
        self.pid = pid
        self.name_tag = "DCS.exe"

    def name(self) -> str:
        return "DCS.exe"

    def cpu_percent(self, interval=None) -> float:
        return 0.0

    def cpu_affinity(self) -> list[int]:
        return []


# --------------------------------------------------------------------------- the class

def test_symmetric_cpu_is_one_class_whatever_the_os_scheduling_value_says(monkeypatch):
    mgr = _manager(monkeypatch, symmetric_cpu())

    assert mgr.scheduling_classes == [0]
    assert mgr.p_e_core_cpu is False
    assert mgr._core_class_label(0) == "Core"
    assert mgr._core_class_prefix(0) == ""
    assert mgr._is_performance_class(0) is True
    assert mgr._is_efficiency_class(0) is False


def test_symmetric_cpu_topology_keeps_every_logical_processor(monkeypatch):
    mgr = _manager(monkeypatch, symmetric_cpu())

    logicals = [l for groups in mgr.topology.values()
                for cores in groups.values() for ls in cores.values() for l in ls]
    assert sorted(logicals) == list(range(20))


def test_cache_picture_draws_every_core_of_a_symmetric_cpu(monkeypatch):
    mgr = _manager(monkeypatch, symmetric_cpu())

    drawn = _draw(monkeypatch, mgr, pm.ProcessManager.visualize_cache)

    assert drawn["cores"] == [str(core) for core in range(20)]


def test_usage_picture_draws_every_core_of_a_symmetric_cpu(monkeypatch):
    mgr = _manager(monkeypatch, symmetric_cpu())

    drawn = _draw(monkeypatch, mgr, pm.ProcessManager.visualize_usage)

    assert drawn["cores"] == [str(core) for core in range(20)]
    assert "Core (Idle)" in drawn["legend"]
    assert not [entry for entry in drawn["legend"] if entry.startswith(("P-Core", "E-Core"))]


def test_symmetric_cpu_gives_a_server_every_physical_core(monkeypatch):
    mgr = _manager(monkeypatch, symmetric_cpu())
    mgr.managed_processes = {4242: {"process": _FakeProc(), "min_cores": 8, "max_cores": 8,
                                    "quality": 1, "instance": ""}}

    assignments = mgr._compute_assignments(dry_run=True)

    assigned = sorted(assignments[4242])
    assert len(assigned) == 8
    assert all(core < 20 for core in assigned)
    # nothing is held back as an "E-core" tier
    assert set(assigned) <= set(range(20))


# --------------------------------------------------------------------------- the tiers

def test_hybrid_cpu_keeps_its_two_tiers(monkeypatch):
    mgr = _manager(monkeypatch, hybrid_cpu())

    assert mgr.scheduling_classes == [0, 1]
    assert mgr.p_e_core_cpu is True
    assert mgr._core_class_prefix(1) == "P"
    assert mgr._core_class_prefix(0) == "E"


def test_hybrid_cache_picture_splits_p_and_e_cores(monkeypatch):
    mgr = _manager(monkeypatch, hybrid_cpu())

    drawn = _draw(monkeypatch, mgr, pm.ProcessManager.visualize_cache)

    assert drawn["cores"] == [f"P{lp}" for lp in range(8)] + [f"E{lp}" for lp in range(8, 16)]


def test_hybrid_usage_picture_keeps_both_legend_entries(monkeypatch):
    mgr = _manager(monkeypatch, hybrid_cpu())

    drawn = _draw(monkeypatch, mgr, pm.ProcessManager.visualize_usage)

    assert "P-Core (Idle)" in drawn["legend"]
    assert "E-Core (Idle)" in drawn["legend"]


def test_hybrid_cpu_gives_a_quality_server_the_performance_tier_only(monkeypatch):
    mgr = _manager(monkeypatch, hybrid_cpu())
    mgr.managed_processes = {4242: {"process": _FakeProc(), "min_cores": 2, "max_cores": 2,
                                    "quality": 1, "instance": ""}}

    assignments = mgr._compute_assignments(dry_run=True)

    assert assignments[4242]
    assert set(assignments[4242]) <= set(range(8))


# --------------------------------------------------------------------------- the preference

def test_the_most_capable_core_goes_to_the_demanding_process(monkeypatch):
    # the ranked cores sit at the END of the index order here, so only a rank finds them
    mgr = _manager(monkeypatch, symmetric_cpu(preferred_lps={8, 9, 10, 11}))
    mgr.managed_processes = {4242: {"process": _FakeProc(), "min_cores": 2, "max_cores": 2,
                                    "quality": 1, "instance": ""}}

    assignments = mgr._compute_assignments(dry_run=True)

    assert sorted(assignments[4242]) == [8, 9]


def test_background_work_gets_the_least_capable_cores(monkeypatch):
    # the ranked cores come FIRST in the index order here, so an unranked allocator would hand
    # them to background work
    mgr = _manager(monkeypatch, symmetric_cpu())
    mgr.managed_processes = {4242: {"process": _FakeProc(), "min_cores": 2, "max_cores": 2,
                                    "quality": 0, "instance": ""}}

    assignments = mgr._compute_assignments(dry_run=True)

    assert sorted(assignments[4242]) == [4, 5]


def test_a_rank_never_lifts_a_core_out_of_its_class(monkeypatch):
    # E-cores rank higher than P-cores here; the demanding process must still get P-cores only
    mgr = _manager(monkeypatch, hybrid_cpu(e_core_rank=9))
    mgr.managed_processes = {4242: {"process": _FakeProc(), "min_cores": 2, "max_cores": 2,
                                    "quality": 1, "instance": ""}}

    assignments = mgr._compute_assignments(dry_run=True)

    assert sorted(assignments[4242]) == [0, 1]
