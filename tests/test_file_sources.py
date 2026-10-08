"""Every image parameter can come from an open layer OR from a file (an "or file" row). Needs a display (xvfb-run)."""

import json
import sys
import tempfile
import time
from pathlib import Path

import _paths  # noqa: F401  (must come first)
import napari
import numpy as np
import tifffile
from _paths import EVIDENCE
from qtpy.QtWidgets import QApplication

viewer = napari.Viewer(show=True)
qa = QApplication.instance()
_, w = viewer.window.add_plugin_dock_widget("napari-labconstrictor", "LabConstrictor tools")
TMP = Path(tempfile.mkdtemp(prefix="lcfile_"))
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


def run(timeout=120):
    w.last_task = None
    w.gui()
    end = time.time() + timeout
    while (
        (w.last_task is None or w.task is not w.last_task)
        and time.time() < end
        and "⚠" not in w.status.text()
    ):
        qa.processEvents()
        time.sleep(0.05)
    pump(0.8)
    return w.last_task


def expect(name, condition, detail=""):
    report[name] = bool(condition)
    if not condition:
        failures.append("%s %s" % (name, detail))


def values(task):
    return next(r["values"] for r in task.outputs["results"] if r["type"] == "values")


dark, bright = np.full((32, 32), 10, np.uint16), np.full((32, 32), 200, np.uint16)
tifffile.imwrite(TMP / "dark.tif", dark)
tifffile.imwrite(TMP / "bright.tif", bright)

# file only, with NO layer open (a required layer parameter must not block the run)
pick("synthetic", "Image stats")
expect("file_row_exists", "image" in w.file_sources)
w.file_sources["image"].value = TMP / "bright.tif"
pump(0.3)
expect("layer_disabled_when_file_set", not w.gui.image.enabled)
task = run()
expect(
    "file_only_no_layers",
    task is not None and task.status == "COMPLETE" and values(task)["mean"] == 200.0,
    w.status.text(),
)

# layer only
viewer.add_image(dark, name="dark_layer")
pump(0.3)
w.file_sources["image"].value = None
pump(0.3)
expect("layer_enabled_again", w.gui.image.enabled)
task = run()
expect(
    "layer_only",
    task is not None and task.status == "COMPLETE" and values(task)["mean"] == 10.0,
    w.status.text(),
)

# both: the file wins
w.file_sources["image"].value = TMP / "bright.tif"
pump(0.2)
task = run()
expect("file_wins_over_layer", task is not None and values(task)["mean"] == 200.0)

# missing file: clear message, no worker started
w.file_sources["image"].value = TMP / "missing.tif"
pump(0.2)
w.last_task = None
w.gui()
pump(0.5)
expect("missing_file_message", "file not found" in w.status.text() and w.last_task is None, w.status.text())

# other formats through imageio, and a corrupt file gives the tool's message
import imageio.v3 as iio  # noqa: E402

iio.imwrite(TMP / "plain.png", (bright // 2).astype(np.uint8))
w.file_sources["image"].value = TMP / "plain.png"
pump(0.2)
task = run()
expect(
    "png_file",
    task is not None and task.status == "COMPLETE" and values(task)["mean"] == 100.0,
    getattr(task, "error", ""),
)
(TMP / "broken.tif").write_bytes(b"not a tiff")
w.file_sources["image"].value = TMP / "broken.tif"
pump(0.2)
task = run()
expect(
    "broken_file_message",
    task is not None and task.status == "FAILED" and "cannot read broken.tif" in (task.error or ""),
    getattr(task, "error", ""),
)

# calibration stored in the file pre-fills the linked pixel-size field
tifffile.imwrite(
    TMP / "calibrated.tif", dark, imagej=True, resolution=(1 / 0.5, 1 / 0.5), metadata={"unit": "micron"}
)
pick("synthetic", "Kitchen sink")
w.file_sources["image"].value = TMP / "calibrated.tif"
pump(0.4)
expect("calibration_from_file", abs(w.gui.scale.value - 0.5) < 1e-9, w.gui.scale.value)

# a file whose header cannot be read is said so in the status line (it used to be swallowed silently)
w.status.setText("")
w.file_sources["image"].value = TMP / "broken.tif"
pump(0.4)
expect(
    "unreadable_file_is_reported",
    "could not read" in w.status.text() and "broken.tif" in w.status.text(),
    w.status.text(),
)

(EVIDENCE / "file_sources_report.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
