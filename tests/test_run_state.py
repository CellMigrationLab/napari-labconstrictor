"""State handling around a run: choosers locked while running, no second launch, worker returned to the right app,
run records keep their inputs, a crash is not reported as a cancel."""

import json
import os
import subprocess
import sys
import time

import _paths  # noqa: F401  (must come first)
import napari
import numpy as np
from qtpy.QtWidgets import QApplication

subprocess.run(
    [
        sys.executable,
        "-m",
        "labconstrictor_tools",
        "register",
        "--name",
        "other",
        "--prefix",
        sys.prefix,
        "--module",
        "labconstrictor_tools.examples.synthetic",
        "--version",
        "0.0",
    ],
    check=True,
    capture_output=True,
)  # fmt: skip  (a second app, so that mixing them up is detectable)

v = napari.Viewer(show=True)
qa = QApplication.instance()
_, w = v.window.add_plugin_dock_widget("napari-labconstrictor", "LabConstrictor tools")
report, failures = {}, []


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        qa.processEvents()
        time.sleep(0.02)


def wait(timeout=60):
    end = time.time() + timeout
    while (w.last_task is None or w.task is not w.last_task) and time.time() < end:
        qa.processEvents()
        time.sleep(0.05)
    pump(0.8)
    return w.last_task


def pick(app, label):
    w.app_box.value = app
    pump(0.1)
    w.tool_box.value = label
    pump(0.3)


def expect(name, condition, detail=""):
    report[name] = bool(condition)
    if not condition:
        failures.append("%s %s" % (name, detail))


def workers():
    return subprocess.run(
        ["pgrep", "-f", "labconstrictor_tools serve"], capture_output=True, text=True
    ).stdout.split()


# 1. choosers are locked while a run is in progress, a second launch is ignored, the worker goes back under the right app
pick("synthetic", "Slow with progress")
w.gui.seconds.value = 4
w.last_task = None
w.gui()
pump(1.0)
first_task = w.task
expect("choosers_locked_while_running", not w.app_box.enabled and not w.tool_box.enabled)
w._launch({"seconds": 1})  # what a stray second click would do
expect("second_launch_ignored", w.task is first_task)
wait()
expect("run_completed", w.last_task.status == "COMPLETE", w.status.text())
expect("choosers_unlocked_after_run", w.app_box.enabled and w.tool_box.enabled)
expect(
    "worker_cached_under_launched_app", list(w._workers._workers) == ["synthetic"], list(w._workers._workers)
)

# 2. the run record keeps its inputs
pick("synthetic", "Scalar echo")
w.gui.a.value, w.gui.b.value = 1.5, 2.0
w.last_task = None
w.gui()
wait()
record = json.loads((w.last_record / "run.json").read_text())
expect(
    "record_has_inputs",
    record["inputs"].get("a") == 1.5 and "_job_dir" not in record["inputs"],
    record["inputs"],
)

# 3. a killed worker is a failure with an explanation, not a "cancel"
pick("other", "Slow with progress")
w.gui.seconds.value = 30
w.last_task = None
w.gui()
pump(2.0)
subprocess.run(["kill", "-9", *workers()[-1:]])
wait()
expect(
    "crash_is_not_called_cancel",
    w.last_task.status == "CRASHED" and w.status.text().startswith("✖"),
    w.status.text(),
)
expect("crash_details_explain", "killed" in w.last_details, w.last_details[:200])

# 4. a tool that ignores Cancel is stopped and that is reported as a cancel
pick("other", "Stubborn (ignores cancel)")
w.gui.seconds.value = 30
w.last_task = None
w.gui()
pump(2.0)
w.cancel()
wait(30)
expect(
    "forced_kill_after_cancel_is_a_cancel", w.status.text() == "cancelled (worker stopped)", w.status.text()
)
expect("forced_kill_not_blamed_on_oom", "out of memory" not in w.last_task.error, w.last_task.error)

# 6. the export cap is enforced with a readable message (a 4.9 GB virtual array: nothing is allocated)
from pathlib import Path as _P  # noqa: E402
from types import SimpleNamespace  # noqa: E402

pick("other", "Image stats")
huge = SimpleNamespace(data=np.broadcast_to(np.uint8(0), (70000, 70000)), name="huge", multiscale=False)
try:
    w._export_inputs({"image": huge}, _P(os.environ["LC_HOME"]))
    message = ""
except ValueError as error:
    message = str(error)
expect("export_cap_message", "huge" in message and "GB" in message, message)

# 7. closing the widget during a run leaves no worker and no temp folder
pick("other", "Slow with progress")
w.gui.seconds.value = 30
w.last_task = None
w.gui()
pump(1.5)
job_dir, pid = w._job_dir, w.worker.proc.pid
w.close()
pump(1.5)
expect("close_during_run_removes_temp", not job_dir.exists(), str(job_dir))
expect(
    "close_during_run_kills_worker",
    subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode != 0,
    pid,
)

# 5. an app whose Python disappeared after the scan is explained, not a bare KeyError
pick("other", "Scalar echo")
entry_file = os.path.join(os.environ["LC_HOME"], "apps", "other.json")
entry = json.load(open(entry_file))
entry["python"] = os.path.join(entry["prefix"], "bin", "python-gone")
os.chmod(entry_file, 0o600)
json.dump(entry, open(entry_file, "w"))
w._workers.close_all()
w.last_task = None
w.gui()
pump(1.0)
expect(
    "vanished_python_is_explained",
    "is missing" in w.status.text() or "is missing" in w.last_details,
    w.status.text(),
)
expect("ui_usable_after_start_failure", w.gui.call_button.enabled and w.app_box.enabled, "")

w._workers.close_all()
pump(1.0)
expect(
    "no_worker_left",
    not workers(),
    subprocess.run(
        ["ps", "-o", "pid,ppid,etime,args", "-p", ",".join(workers()) or "1"], capture_output=True, text=True
    ).stdout,
)
print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
