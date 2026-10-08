"""Host parity K03 (NP-9): a worker gets 10 s to exit when closed (what PROTOCOL.md says the worker needs), then it is killed."""

import json
import sys

import _paths  # noqa: F401  (must come first)

from napari_labconstrictor import _workers

failures = []
if _workers.CLOSE_TIMEOUT_S != 10:
    failures.append("CLOSE_TIMEOUT_S is %r" % _workers.CLOSE_TIMEOUT_S)
timeouts = []


class FakeWorker:
    alive = True

    def close(self, timeout=None):
        timeouts.append(timeout)


worker = FakeWorker()
cache = _workers.WorkerCache()
cache.release("a", worker, keep=False)
cache.release("b", worker, keep=True)
cache.discard("b")
if timeouts != [10, 10]:
    failures.append("close() was called with %r" % timeouts)
print(json.dumps({"close_gets_10_seconds": not failures}))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
