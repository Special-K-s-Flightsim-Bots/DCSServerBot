"""In-flight registry for an extension's ON/OFF switch — one job per (node, scope, extension).

WHY A REGISTRY AND NOT A SPINNER. Switching an extension on runs its ``install()``/``prepare()``, which
for an extension that ships a package (Olympus and its dependencies) means a pip install: minutes, not
milliseconds. Awaiting that inside the POST held the request open for the whole install — the browser
sat on a blank tab with nothing to read, could drop the connection entirely, and nothing stopped a
second click, a second tab or a second operator from starting a second install of the same extension
into the same venv.

So the POST HANDS THE WORK OVER: it claims the job here, returns at once, and the action runs as a
background task. This module is the claim itself — the single source of truth a page reads to draw the
"Enabling…" state, and the lock that answers a second attempt with "already switching".

THE JOB OUTLIVES ITS REQUEST, THE RESULT OUTLIVES ITS JOB. ``finish`` stores the action's own message
against the job, and the next page render that asks for it (:func:`take_results`) takes it ONCE and
renders it as the console's ordinary notice — the same sentence the blocking switch used to show, now
arriving after the redirect instead of inside it.

A SWITCH ALWAYS ENDS. Three things can go wrong with work nobody awaits, and each has its own guard:
a task that dies without reporting (``track``'s done-callback), a task that outlives any sane wait
(:func:`reconcile` marks the job overdue, and the page stops drawing it as in progress), and a job
claimed but never started (:data:`_CLAIM_GRACE_SECONDS`). In every case the operator gets a sentence
instead of a switch that pulses for as long as the tab stays open.

Nothing here is per-extension or per-node-name: the key is ``(node key, scope value, extension name)``,
the same tuple the switch posts, so every extension gets the behaviour with no code of its own.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: A job older than this is dropped — one nobody looked at, or one whose task died long ago.
_MAX_AGE_SECONDS = 3600.0

#: How long the PAGE draws a switch as in progress, per action. An enable may install a package, so it
#: gets the long wait; a disable only runs shutdown hooks and writes a flag, so a disable still running
#: after three minutes is stuck and the operator must be told rather than left watching a pulse. The
#: job itself is NOT cancelled: it may still finish, and its own completion still lands in the log and
#: in the registry — only the page stops claiming it is working.
_UI_WAIT_SECONDS = {"enable": 1800.0, "disable": 180.0}
_UI_WAIT_DEFAULT = 600.0

#: How long a claimed job may exist with no task attached before it is treated as never started.
_CLAIM_GRACE_SECONDS = 30.0

STOPPED_SHORT = ("The switch stopped before it reported: the task running it is gone (the bot was most "
                 "likely restarted). Check the extension's state on this page.")

#: Strong references to the live tasks. ``asyncio.create_task`` alone keeps only a WEAK reference: an
#: unreferenced task can be garbage-collected at an await point, which cancels it without a trace. This
#: set is what makes that impossible; each task removes itself when it finishes.
_TASKS: set = set()

RUNNING = "running"
DONE = "done"


@dataclass
class SwitchJob:
    """One switch in flight — or one whose result has not been read by a page yet."""

    node: str                      #: the nodes.yaml key the switch writes
    node_name: str                 #: the node name the page asked for
    instance: str | None           #: the scope: ``None`` for the node-wide tab, else the instance
    extension: str
    action: str                    #: the posted action word: ``enable`` / ``disable``
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    ok: bool = False
    message: str = ""
    shown: bool = False
    task: asyncio.Task | None = None   #: the task running it — the registry holds it alive
    overdue: bool = False              #: past the page's wait: no longer drawn as in progress

    @property
    def running(self) -> bool:
        """Whether the job is still going — what the LOCK answers to."""
        return self.finished == 0.0

    @property
    def busy(self) -> bool:
        """Whether the PAGE still draws the switch as in progress (a stuck job stops being busy)."""
        return self.running and not self.overdue

    @property
    def reported(self) -> bool:
        """Whether there is a verdict to show: finished, or given up on by the page."""
        return not self.running or self.overdue

    @property
    def ui_wait(self) -> float:
        return _UI_WAIT_SECONDS.get((self.action or "").lower(), _UI_WAIT_DEFAULT)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.node, self.instance or "", self.extension.lower())

    @property
    def state_key(self) -> str:
        """The key as the page and the poll endpoint exchange it (JSON has no tuple keys)."""
        return "|".join(self.key)


_registry_lock = threading.Lock()


def _jobs(state) -> dict[tuple[str, str, str], SwitchJob]:
    """The registry, kept on the ASGI app's own state so two apps never share one."""
    jobs = getattr(state, "extension_switches", None)
    if jobs is None:
        jobs = {}
        try:
            setattr(state, "extension_switches", jobs)
        except AttributeError:                      # a frozen state double: keep it local to the call
            return jobs
    return jobs


def _purge(jobs: dict, now: float) -> None:
    stale = [key for key, job in jobs.items() if now - job.started > _MAX_AGE_SECONDS]
    for key in stale:
        jobs.pop(key, None)


def begin(state, node: str, node_name: str, instance: str | None, extension: str,
          action: str) -> SwitchJob | None:
    """Claim the switch for ONE extension. ``None`` means a job for it is already in flight."""
    with _registry_lock:
        jobs = _jobs(state)
        now = time.time()
        _purge(jobs, now)
        key = (node, instance or "", extension.lower())
        current = jobs.get(key)
        if current is not None:
            if current.running:
                return None
            # AN OVERDUE JOB STILL HOLDS THE LOCK while its task is alive: the page stopped waiting,
            # but starting a SECOND shutdown/install beside the first is exactly what this registry
            # exists to prevent. Refused, never queued.
            if current.task is not None and not current.task.done():
                return None
        job = SwitchJob(node=node, node_name=node_name, instance=instance, extension=extension,
                        action=action)
        jobs[key] = job
        return job


def track(state, job: SwitchJob, coro) -> SwitchJob:
    """Start *coro* as the job's task, HOLD IT, and close the job when it ends without a verdict.

    Both references matter: the loop keeps only a WEAK one, so a task nobody else holds can be
    collected at an await point and cancelled silently. The done-callback is the other half — a task
    cancelled BEFORE its first step never runs the coroutine body at all, so nothing inside it can
    record anything; the job is closed here instead, which is what stops the card pulsing when the bot
    goes down mid-switch.
    """
    task = asyncio.ensure_future(coro)
    job.task = task
    _TASKS.add(task)

    def _settle(ended: asyncio.Task) -> None:
        _TASKS.discard(ended)
        if job.running:                       # the body recorded nothing
            finish(state, job, False, STOPPED_SHORT)

    task.add_done_callback(_settle)
    return job


def reconcile(state, node_name: str | None = None) -> None:
    """Close or age-out any job that cannot end on its own, so no page can pulse forever.

    Called by the poll endpoint and by the page render. Three cases, each with its own sentence:
    a task that ENDED without a verdict, a job claimed but never given a task, and a task still alive
    past the page's wait for its action (marked overdue: the page stops drawing it, the lock stays).
    """
    late = []
    with _registry_lock:
        jobs = _jobs(state)
        now = time.time()
        for job in list(jobs.values()):
            if not job.running or (node_name is not None and job.node_name != node_name):
                continue
            task = job.task
            if task is None and now - job.started > _CLAIM_GRACE_SECONDS:
                job.finished, job.ok, job.message = now, False, STOPPED_SHORT
            elif task is not None and task.done():
                job.finished, job.ok = now, False
                job.message = job.message or STOPPED_SHORT
            elif now - job.started > job.ui_wait:
                job.overdue, job.ok = True, False
                job.message = _overdue_sentence(job)
                late.append(job)
    for job in late:
        log.error("extension switch is not finishing: %s on %s/%s after %.0fs — the page stopped "
                  "waiting for it; its task is %s", job.action, job.node, job.extension,
                  time.time() - job.started,
                  "still alive" if job.task is not None and not job.task.done() else "gone")


def _overdue_sentence(job: SwitchJob) -> str:
    minutes = int(job.ui_wait // 60)
    word = "on" if (job.action or "").lower() == "enable" else "off"
    return (f"Switching {job.extension} {word} has not finished after {minutes} minute"
            f"{'' if minutes == 1 else 's'}, so this page stopped waiting for it. It is still running "
            f"in the bot — check its log for what it is stuck on. The switch stays locked until it "
            f"finishes; restarting the bot clears it.")


def finish(state, job: SwitchJob, ok: bool, message: str) -> None:
    """Record the action's own verdict; the next render that asks for it shows it as the notice."""
    with _registry_lock:
        job.finished = time.time()
        job.ok = bool(ok)
        job.message = message or ""


def job_for(state, node: str, instance: str | None, extension: str) -> SwitchJob | None:
    """The job this card should draw as BUSY, or ``None`` — an overdue job is not drawn as busy."""
    with _registry_lock:
        job = _jobs(state).get((node, instance or "", extension.lower()))
        return job if job is not None and job.busy else None


def running(state, node_name: str | None = None) -> list[SwitchJob]:
    """Every job the page should still draw as IN PROGRESS — the poll endpoint's answer.

    An overdue job is deliberately absent: that is what ends the page's wait and triggers the reload
    whose render shows the sentence.
    """
    with _registry_lock:
        return [job for job in _jobs(state).values()
                if job.busy and (node_name is None or job.node_name == node_name)]


def take_results(state, node_name: str | None = None) -> list[SwitchJob]:
    """The reported jobs nobody has shown yet — each returned ONCE, so a reload cannot repeat it."""
    with _registry_lock:
        jobs = _jobs(state)
        _purge(jobs, time.time())
        taken = [job for job in jobs.values()
                 if job.reported and not job.shown
                 and (node_name is None or job.node_name == node_name)]
        for job in taken:
            job.shown = True
        return taken


def reset(state) -> None:
    """Forget every job — a test helper and a shutdown guard, never a page's doing."""
    with _registry_lock:
        jobs = _jobs(state)
        jobs.clear()
