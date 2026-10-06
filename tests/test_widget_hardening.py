"""Widget failure paths: a start that fails half way, results that cannot be shown, a disk that fills while exporting,
Rescan during a run, duplicate tool labels, nullable yes/no parameters, an image given as a file driving enabled_when."""

import glob
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _paths  # noqa: F401  (must come first)
import napari
import numpy as np
from qtpy.QtWidgets import QApplication

_app = Path(tempfile.mkdtemp(prefix="lchard_"))
(_app / "hard_lc_tools.py").write_text(
    "from typing import Annotated, Optional\n"
    "from labconstrictor_tools import EnabledWhen, Image, Scalars, tool\n"
    "@tool('Segment')\n"
    "def seg_a() -> Scalars:\n    return {'which': 1}\n"
    "@tool('Segment')\n"
    "def seg_b() -> Scalars:\n    return {'which': 2}\n"
    "@tool('Flag tool')\n"
    "def flag_tool(flag: Optional[bool] = None) -> Scalars:\n    return {'flag': str(flag)}\n"
    "@tool('Gated')\n"
    "def gated(image: Optional[Image] = None, x: Annotated[float, EnabledWhen('image')] = 1.0) -> Scalars:\n"
    "    return {'x': x}\n"
)
subprocess.run(
    [sys.executable, "-m", "labconstrictor_tools", "register", "--name", "hard", "--prefix", sys.prefix,
     "--module", "hard_lc_tools", "--pythonpath", str(_app)],
    check=True, capture_output=True,
)  # fmt: skip

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


def input_dirs():
    return set(glob.glob(str(Path(tempfile.gettempdir()) / "lcin_*")))


# 1. two tools with the same label are both selectable and the right one is run
names = list(w.tool_box.choices)
expect("duplicate_labels_disambiguated", "Segment (seg_a)" in names and "Segment (seg_b)" in names, names)
expect("unique_labels_unchanged", "Flag tool" in names, names)
pick("hard", "Segment (seg_b)")
expect("second_tool_is_the_one_selected", w.tool["id"] == "seg_b", w.tool and w.tool["id"])
pick("hard", "Segment (seg_a)")
expect("first_tool_is_the_one_selected", w.tool["id"] == "seg_a", w.tool and w.tool["id"])

# 2. a failure after the worker was acquired undoes the start
class FakeWorker:
    alive = True
    closed = False
    stderr = []

    def task(self, *args, **kwargs):
        raise BrokenPipeError("the worker died before the request was sent")

    def close(self, timeout=0):
        self.closed = True
        self.alive = False


fake, before = FakeWorker(), input_dirs()
real_acquire = w._workers.acquire
w._workers.acquire = lambda app, reuse=True: fake
pick("hard", "Flag tool")
w.last_task = None
w.gui()
pump(0.5)
w._workers.acquire = real_acquire
expect("failed_start_reports_error", w.status.text().startswith("✖") and "died" in w.status.text(), w.status.text())
expect("failed_start_unlocks_form", w.gui.call_button.enabled and w.app_box.enabled and w.rescan_button.isEnabled())
expect("failed_start_releases_worker", fake.closed)
expect("failed_start_leaves_no_task", w.task is None)
expect("failed_start_removes_temp", input_dirs() == before, input_dirs() - before)
expect("failed_start_details_available", w.details_button.isEnabled())

# 3. results that cannot be shown are not reported as a success, and the temp folder still goes
from napari_labconstrictor import _widget  # noqa: E402

real_show = _widget.ResultPresenter.show


def boom(self, result):
    raise RuntimeError("cannot draw this result")


_widget.ResultPresenter.show = boom
pick("synthetic", "Scalar echo")
w.last_task = None
w.gui()
wait()
_widget.ResultPresenter.show = real_show
expect("unshowable_result_not_success", w.status.text().startswith("✖") and "done" not in w.status.text(), w.status.text())
expect("unshowable_result_removes_temp", not w._job_dir.exists(), str(w._job_dir))
expect("unshowable_result_unlocks", w.gui.call_button.enabled and w.app_box.enabled)
expect("unshowable_result_explained", "cannot draw this result" in w.last_details, w.last_details[:300])

# 3b. a result the presenter itself fails on (it reports instead of raising) is a warning, not a plain success
real_values = _widget.ResultPresenter._values


def bad_values(self, result):
    raise ValueError("bad table")


_widget.ResultPresenter._values = bad_values
pick("synthetic", "Scalar echo")
w.last_task = None
w.gui()
wait()
_widget.ResultPresenter._values = real_values
expect("display_failure_is_a_warning", w.status.text().startswith("⚠") and "could not be shown" in w.status.text(), w.status.text())
expect("display_failure_in_details", "bad table" in w.last_details, w.last_details[-300:])

# 4. Rescan and Restart are off while a task runs, and a rescan afterwards still works
pick("synthetic", "Slow with progress")
w.gui.seconds.value = 3
w.last_task = None
w.gui()
pump(1.0)
expect("rescan_disabled_while_running", not w.rescan_button.isEnabled() and not w.restart_button.isEnabled())
wait()
expect("rescan_enabled_after_run", w.rescan_button.isEnabled() and w.restart_button.isEnabled())

# 5. a disk that fills while saving the layer: explained, cleaned up, form usable
import tifffile  # noqa: E402

v.add_image(np.zeros((8, 8), np.uint8), name="img")
pick("synthetic", "Image stats")
before = input_dirs()
real_imwrite = tifffile.imwrite


def full_disk(*args, **kwargs):
    Path(args[0]).write_bytes(b"partial")
    raise OSError(28, "No space left on device")


tifffile.imwrite = full_disk
w.last_task = None
w.gui()
pump(0.5)
tifffile.imwrite = real_imwrite
expect("export_oserror_explained", "could not prepare the inputs" in w.status.text(), w.status.text())
expect("export_oserror_removes_temp", input_dirs() == before, input_dirs() - before)
expect("export_oserror_form_usable", w.gui.call_button.enabled and w.app_box.enabled)

# 6. a nullable yes/no can be left unset (and is then not sent as False)
pick("hard", "Flag tool")
expect("nullable_bool_has_set_toggle", "flag" in w.unset_toggles, list(w.unset_toggles))
sent = w._export_inputs({"flag": False}, Path(tempfile.mkdtemp(prefix="lcin_t_")))
expect("unset_bool_is_omitted", "flag" not in sent, sent)
w.unset_toggles["flag"].value = True
w.gui.flag.value = False
sent = w._export_inputs({"flag": False}, Path(tempfile.mkdtemp(prefix="lcin_t_")))
expect("set_false_is_sent_as_false", sent.get("flag") is False, sent)

# 7. an image chosen as a file counts as set for enabled_when
pick("hard", "Gated")
expect("gated_disabled_without_image", not w.gui.x.enabled)
tif = Path(tempfile.mkdtemp(prefix="lcimg_")) / "a.tif"
tifffile.imwrite(tif, np.zeros((8, 8), np.uint8))
w.file_sources["image"].value = tif
pump(0.3)
expect("gated_enabled_by_file_image", w.gui.x.enabled)

# 8. closing the viewer stops the workers it keeps between runs (not only the end of the Python process)
pick("synthetic", "Scalar echo")
w.last_task = None
w.gui()
wait()
pid = w.worker.proc.pid if w.worker is not None else None
expect("worker_kept_between_runs", pid is not None and w._workers._workers, w._workers._workers)


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()[:1] != "Z"


v.close()
end = time.time() + 15
while alive(pid) and time.time() < end:
    qa.processEvents()
    time.sleep(0.1)
expect("closing_the_viewer_stops_the_workers", not alive(pid), pid)

print(__import__("json").dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
