"""The dock widget end to end with the example app: form, calibration, results, worker reuse, progress, cancel, forced kill."""

import json
import os
import sys
import time

import _paths  # noqa: F401  (must come first)
import napari
import numpy as np
from _paths import EVIDENCE
from qtpy.QtWidgets import QApplication

v = napari.Viewer(show=True)
v.window.resize(1500, 950)
qa = QApplication.instance()
_, w = v.window.add_plugin_dock_widget(
    "napari-labconstrictor", "LabConstrictor tools"
)  # discovered through npe2
report, failures = {}, []


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        qa.processEvents()
        time.sleep(0.02)


def pick(app, label):
    w.app_box.value = app
    pump(0.1)
    w.tool_box.value = label
    pump(0.3)


def wait(timeout=120):
    end = time.time() + timeout
    while (w.last_task is None or w.task is not w.last_task) and time.time() < end:
        qa.processEvents()
        time.sleep(0.05)
    pump(0.8)
    return w.last_task


def run():
    w.last_task = None
    w.gui()
    return wait()


def expect(name, condition, detail=""):
    report[name] = bool(condition)
    if not condition:
        failures.append("%s %s" % (name, detail))


# a calibrated layer
image = (np.random.default_rng(1).random((64, 64)) * 1000).astype(np.uint16)
layer = v.add_image(image, name="query", scale=(0.325, 0.325), units="micrometer")
pump()

pick("synthetic", "Kitchen sink")
labels = {x.name: type(x).__name__ for x in w.gui if getattr(x, "name", None)}
expect(
    "controls_generated",
    labels.get("count") == "SpinBox"
    and labels.get("mode") == "ComboBox"
    and labels.get("flag") == "CheckBox",
    labels,
)
w.gui.required_string.value = "hello"
w.gui.image.value = layer
pump()
expect("calibration_prefilled_from_layer", abs(w.gui.scale.value - 0.325) < 1e-9, w.gui.scale.value)
task = run()
expect("kitchen_sink_runs", task.status == "COMPLETE", w.status.text())
expect(
    "results_become_layers_and_tables",
    "synthetic:doubled" in [x.name for x in v.layers] and "rows" in w.tables,
    [x.name for x in v.layers],
)
v.screenshot(str(EVIDENCE / "widget_kitchen_sink.png"), canvas_only=False)

# worker reuse and restart
first = w._workers._workers["synthetic"][0].proc.pid
run()
expect("worker_reused", w._workers._workers["synthetic"][0].proc.pid == first)
w.restart_button.click()
expect("restart_clears_cache", "synthetic" not in w._workers._workers)

w.gui["count"].value = 3
pick("synthetic", "Scalar echo")
w.gui.a.value, w.gui.b.value = 1.5, 2.0
task = run()
expect("scalar_tool", task.status == "COMPLETE" and "sum=3.5" in w.status.text(), w.status.text())

# a failing tool: readable message, Details button available, log file named
pick("synthetic", "Explode")
task = run()
expect(
    "failure_is_readable",
    task.status == "FAILED" and "always fails" in w.status.text() and "log:" in w.status.text(),
    w.status.text(),
)
expect("details_available", w.details_button.isEnabled() and "log file:" in w.last_details)

# progress in the UI, then cooperative cancel
pick("synthetic", "Slow with progress")
w.gui.seconds.value, w.gui.steps.value = 3.0, 6
w.last_task = None
seen = []
w.gui()
end = time.time() + 60
while w.last_task is None and time.time() < end:
    qa.processEvents()
    text = w.status.text()
    if not seen or seen[-1] != text:
        seen.append(text)
    time.sleep(0.02)
pump(0.5)
expect("progress_visible", sum("step" in t for t in seen) >= 3, seen)
w.gui.seconds.value, w.gui.steps.value = 30.0, 60
w.last_task = None
w.gui()
pump(1.5)
t0 = time.time()
w.cancel()
wait(15)
expect(
    "cooperative_cancel",
    w.last_task.status == "CANCELED" and time.time() - t0 < 6 and w.gui.call_button.enabled,
    w.last_task.status,
)

# a tool that ignores cancel: the worker is killed after the grace period
pick("synthetic", "Stubborn (ignores cancel)")
w.gui.seconds.value = 60.0
w.last_task = None
w.gui()
pump(1.5)
pid = w.worker.proc.pid
t0 = time.time()
w.cancel()
wait(20)
gone = not os.path.exists("/proc/%d" % pid) if os.path.exists("/proc/self") else True
expect(
    "forced_kill",
    w.last_task.status in ("CRASHED", "CANCELED") and gone and time.time() - t0 < 12,
    (w.last_task.status, time.time() - t0),
)

(EVIDENCE / "widget_report.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
