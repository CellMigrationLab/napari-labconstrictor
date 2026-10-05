"""Keep one worker per app alive between runs (a repeat run of a heavy tool is typically seconds instead of tens of seconds), and never leak them."""

import atexit
import time

from labconstrictor_tools import client

IDLE_SECONDS = 600  # an unused worker (and the memory its imports hold) is closed after this long


class WorkerCache:
    def __init__(self, idle_seconds=IDLE_SECONDS):
        self.idle_seconds = idle_seconds
        self._workers = {}  # app name -> [WorkerProcess, last_used]
        atexit.register(self.close_all)

    def acquire(self, app, reuse=True):
        """A running worker for `app`: the cached one if allowed and still alive, otherwise a new one."""
        cached = self._workers.pop(app, None)
        if cached and cached[0].alive and reuse:
            return cached[0]
        if cached:
            cached[0].close(timeout=2)
        return client.WorkerProcess(app)

    def release(self, app, worker, keep):
        """Hand the worker back after a run. Only healthy workers are kept."""
        if keep and worker.alive:
            self._workers[app] = [worker, time.monotonic()]
        else:
            worker.close(timeout=2) if worker.alive else None

    def discard(self, app):
        cached = self._workers.pop(app, None)
        if cached:
            cached[0].close(timeout=2)

    def reap_idle(self):
        now = time.monotonic()
        for app in [a for a, (_, used) in self._workers.items() if now - used > self.idle_seconds]:
            self.discard(app)

    def close_all(self):
        for app in list(self._workers):
            self.discard(app)
