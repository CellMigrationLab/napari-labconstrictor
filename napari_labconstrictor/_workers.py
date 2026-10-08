"""Keep one worker per app alive between runs (a repeat run of a heavy tool is typically seconds instead of tens of seconds), and never leak them."""

from __future__ import annotations

import atexit
import time

from labconstrictor_tools import client

IDLE_SECONDS = 600  # an unused worker (and the memory its imports hold) is closed after this long


class WorkerCache:
    """Workers kept per app between runs; `acquire`/`release` hand one out and take it back, `close_all` runs at exit."""

    def __init__(self, idle_seconds: float = IDLE_SECONDS) -> None:
        self.idle_seconds = idle_seconds
        self._workers: dict[str, list] = (
            {}
        )  # app name -> [WorkerProcess, last_used]  (idle, ready for the next run)
        self._active: set = set()  # workers handed out by acquire() and not released yet
        atexit.register(self.close_all)

    def acquire(self, app: str, reuse: bool = True) -> client.WorkerProcess:
        """A running worker for `app`: the cached one if allowed and still alive, otherwise a new one."""
        cached = self._workers.pop(app, None)
        if cached and cached[0].alive and reuse:
            worker = cached[0]
        else:
            if cached:
                cached[0].close(timeout=2)
            worker = client.WorkerProcess(app)
        self._active.add(worker)
        return worker

    def release(self, app: str, worker: client.WorkerProcess, keep: bool) -> None:
        """Hand the worker back after a run. Only healthy workers are kept."""
        self._active.discard(worker)
        if keep and worker.alive:
            displaced = self._workers.get(
                app
            )  # a second worker for the same app was kept meanwhile: do not leak it
            if displaced and displaced[0] is not worker:
                displaced[0].close(timeout=2)
            self._workers[app] = [worker, time.monotonic()]
        else:
            worker.close(timeout=2) if worker.alive else None

    def discard(self, app: str) -> None:
        """Close and forget the idle worker of `app`, if any."""
        cached = self._workers.pop(app, None)
        if cached:
            cached[0].close(timeout=2)

    def reap_idle(self) -> None:
        """Close the workers that have been idle longer than `idle_seconds`."""
        now = time.monotonic()
        for app in [a for a, (_, used) in self._workers.items() if now - used > self.idle_seconds]:
            self.discard(app)

    def close_all(self) -> None:
        for app in list(self._workers):
            self.discard(app)
        for worker in list(self._active):  # also the ones that are running a task right now
            self._active.discard(worker)
            worker.close(timeout=2)
