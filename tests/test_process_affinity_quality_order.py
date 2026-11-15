"""Quality must decide who holds the best cores, not the order the processes were launched in.

A quality value is only meaningful if the better class of core ends up with the process that
asked for it: leaving the first process in possession of the top-ranked cores makes ``quality``
a launch-order coin flip and reduces it to a boolean.

The fixtures replay a recorded CPU-set dump through the shipped allocator (no process is
started), so the resolved assignment sets are what the manager would apply.
"""
from __future__ import annotations

import inspect
import threading
import types

from core.process import processmanager as pm


# --------------------------------------------------------------------------- fixtures

def _lp(lp: int, core: int, core_class: int, rank: int = 0) -> dict:
    return {
        "CPU Id": 256 + lp,
        "Logical Processor Index": lp,
        "Core Index": core,
        "Efficiency Class": core_class,
        "Core Class": core_class,
        "Core Rank": rank,
        "Numa Node Index": 0,
        "Last Level Cache Index": 0,
    }


def symmetric_cpu(ranked_lps: set[int] | None = None) -> list[dict]:
    """10 identical cores, 2 threads each; the OS ranks a few logical CPUs above the rest."""
    ranked = {0, 1, 2, 3} if ranked_lps is None else set(ranked_lps)
    return [_lp(c * 2 + t, c * 2, 0, 1 if (c * 2 + t) in ranked else 0)
            for c in range(10) for t in range(2)]


def unranked_cpu() -> list[dict]:
    """The same 20 logical CPUs with no ranking at all - what a non-Windows host reports."""
    return symmetric_cpu(ranked_lps=set())


def hybrid_cpu() -> list[dict]:
    """4 P-cores with 2 threads each (class 1) followed by 8 single-threaded E-cores (class 0)."""
    cpus, lp = [], 0
    for c in range(4):
        for _ in range(2):
            cpus.append(_lp(lp, 2 * c, 1, rank=1))
            lp += 1
    for _ in range(8):
        cpus.append(_lp(lp, lp, 0, rank=0))
        lp += 1
    return cpus


class _FakeProc:
    def __init__(self, pid: int, tag: str = "DCS.exe"):
        self.pid = pid
        self.name_tag = tag
        self.affinity: list[int] = []

    def name(self) -> str:
        return "DCS.exe"

    def cpu_percent(self, interval=None) -> float:
        return 0.0

    def cpu_affinity(self, new=None) -> list[int]:
        if new is not None:
            self.affinity = list(new)
        return list(self.affinity)


def _manager(monkeypatch, cpu_sets: list[dict], reuse_cores: bool = True):
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
        reuse_cores=reuse_cores,
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


def launch(mgr, pid: int, min_cores: int, quality: int):
    """Register a process exactly as ``launch_process`` does and let the manager place it."""
    proc = _FakeProc(pid)
    mgr.managed_processes[pid] = {
        "process": proc,
        "min_cores": min_cores,
        "max_cores": min_cores,
        "quality": quality,
        "instance": "",
    }
    mgr._redistribute_cores()
    return proc


def cores(mgr, pid: int) -> list[int]:
    return sorted(mgr.managed_processes[pid].get("_current_assignments", []))


# --------------------------------------------------------------------------- rank priority

def test_later_quality_3_takes_the_best_ranked_cores_from_quality_1(monkeypatch):
    mgr = _manager(monkeypatch, symmetric_cpu())
    launch(mgr, 1001, 4, 1)
    assert cores(mgr, 1001) == [0, 1, 2, 3]

    launch(mgr, 2002, 8, 3)

    # the ranked cores (0-3) belong to the higher quality, whatever the launch order
    assert sorted(set(cores(mgr, 2002)) & {0, 1, 2, 3}) == [0, 1, 2, 3]
    assert set(cores(mgr, 1001)).isdisjoint(cores(mgr, 2002))
    assert len(cores(mgr, 1001)) == 4


def test_launch_order_does_not_change_the_final_split(monkeypatch):
    mgr = _manager(monkeypatch, symmetric_cpu())
    launch(mgr, 1001, 4, 1)
    launch(mgr, 2002, 8, 3)
    forward = (cores(mgr, 1001), cores(mgr, 2002))

    mgr = _manager(monkeypatch, symmetric_cpu())
    launch(mgr, 2002, 8, 3)
    launch(mgr, 1001, 4, 1)
    reversed_order = (cores(mgr, 1001), cores(mgr, 2002))

    # whichever went first, the ranked cores end up with the higher quality and the
    # lower quality keeps its own count on cores the other one does not hold
    for lower, higher in (forward, reversed_order):
        assert {0, 1, 2, 3} <= set(higher)
        assert len(lower) == 4 and len(higher) == 8
        assert not set(lower) & set(higher)


def test_the_split_is_stable_across_repeated_passes(monkeypatch):
    mgr = _manager(monkeypatch, symmetric_cpu())
    launch(mgr, 1001, 4, 1)
    launch(mgr, 2002, 8, 3)
    settled = (cores(mgr, 1001), cores(mgr, 2002))

    for _ in range(3):
        mgr._redistribute_cores()
        mgr._redistribute_cores(cooperative=True)
        assert (cores(mgr, 1001), cores(mgr, 2002)) == settled


def test_equal_quality_processes_never_exchange_cores(monkeypatch):
    mgr = _manager(monkeypatch, symmetric_cpu())
    launch(mgr, 1001, 4, 3)
    first = cores(mgr, 1001)
    launch(mgr, 2002, 4, 3)

    assert cores(mgr, 1001) == first
    assert cores(mgr, 2002) == [4, 5, 6, 7]


def test_no_core_rank_keeps_first_come_order(monkeypatch):
    mgr = _manager(monkeypatch, unranked_cpu())
    launch(mgr, 1001, 4, 1)
    launch(mgr, 2002, 8, 3)

    # with no ranking there is nothing to spend; nobody is moved
    assert cores(mgr, 1001) == [0, 1, 2, 3]
    assert cores(mgr, 2002) == [4, 5, 6, 7, 8, 9, 10, 11]


def test_quality_1_takes_the_best_ranked_core_from_quality_0(monkeypatch):
    mgr = _manager(monkeypatch, symmetric_cpu())
    # a background process is placed on the least capable cores it can get
    launch(mgr, 1001, 4, 0)
    background = cores(mgr, 1001)
    assert not set(background) & {0, 1, 2, 3}

    launch(mgr, 2002, 4, 1)

    assert set(cores(mgr, 2002)) & {0, 1, 2, 3}
    assert len(cores(mgr, 1001)) == 4


# --------------------------------------------------------------------------- displacement

def test_quality_3_takes_whole_p_cores_from_a_lower_quality_process(monkeypatch):
    mgr = _manager(monkeypatch, hybrid_cpu())
    launch(mgr, 1001, 8, 1)          # takes all 8 P logicals
    assert cores(mgr, 1001) == list(range(8))

    launch(mgr, 2002, 4, 3)          # the machine has no free P core left

    # the higher quality is confined to the two most capable physical cores, taken away
    # from the lower quality rather than shared thinly across four of them
    assert cores(mgr, 2002) == [0, 1, 2, 3]


def test_displaced_process_keeps_its_core_count(monkeypatch):
    mgr = _manager(monkeypatch, hybrid_cpu())
    launch(mgr, 1001, 8, 1)
    before = len(cores(mgr, 1001))

    launch(mgr, 2002, 4, 3)

    assert len(cores(mgr, 2002)) == 4
    assert len(cores(mgr, 1001)) == before
