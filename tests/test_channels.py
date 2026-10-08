"""PickChannel: the Channel chooser lists the colours of an RGB layer or the channels of a TIFF file; the tool receives only the chosen channel."""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _paths  # noqa: F401  (must come first)
import napari
import numpy as np
import tifffile
from qtpy.QtWidgets import QApplication

subprocess.run(
    [sys.executable, "-m", "labconstrictor_tools", "register", "--name", "interactions", "--prefix", sys.prefix,
     "--module", "labconstrictor_tools.examples.interactions", "--version", "0.0"],
    check=True, capture_output=True,
)  # fmt: skip

work = Path(tempfile.mkdtemp(prefix="lcchannels_"))
three = np.zeros((3, 32, 32), np.uint8)
for c in range(3):
    three[c] = 10 * (c + 1)
tifffile.imwrite(work / "three.tif", three, imagej=True, metadata={"axes": "CYX"})
flat = np.full((32, 32), 7, np.uint8)
tifffile.imwrite(work / "flat.tif", flat)

v = napari.Viewer(show=True)
v.window._qt_window.resize(1400, 800)
qa = QApplication.instance()
_, w = v.window.add_plugin_dock_widget("napari-labconstrictor", "LabConstrictor tools")
report, failures = {}, []


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        qa.processEvents()
        time.sleep(0.02)


def until(cond, timeout=60):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        qa.processEvents()
        time.sleep(0.05)
    pump(0.3)


def expect(name, ok, detail=""):
    report[name] = bool(ok)
    if not ok:
        failures.append("%s %s" % (name, detail))


def run():
    w.last_task = None
    w.gui()
    until(lambda: w.last_task is not None and w.task is w.last_task)
    pump(0.5)


shown = lambda x: not x.native.isHidden()
rgb = np.zeros((32, 32, 3), np.uint8)
rgb[..., 0], rgb[..., 1], rgb[..., 2] = 10, 20, 30
layer_rgb = v.add_image(rgb, rgb=True, name="rgb")
layer_flat = v.add_image(flat, name="flat")
w.app_box.value = "interactions"
pump(0.3)
w.tool_box.value = "Mean of a channel"
pump(1)
box = w.channel_boxes["image"]
w.gui.image.value = layer_flat
pump(0.4)
expect("single_channel_layer_has_no_chooser", not shown(box))
run()
expect("single_channel_layer_is_sent_as_it_is", "mean=7" in w.status.text(), w.status.text())
w.gui.image.value = layer_rgb
pump(0.4)
expect(
    "rgb_layer_lists_the_colours",
    shown(box) and list(box.choices) == ["Red", "Green", "Blue"],
    (shown(box), list(box.choices)),
)
for index, colour in enumerate(["Red", "Green", "Blue"]):
    box.value = colour
    run()
    expect(
        "rgb_%s_is_what_the_tool_receives" % colour,
        "mean=%d" % (10 * (index + 1)) in w.status.text() and "32 x 32" in w.status.text(),
        w.status.text(),
    )
w.file_sources["image"].value = work / "three.tif"
pump(0.6)
expect(
    "file_with_channels_lists_them",
    shown(box) and list(box.choices) == ["Channel 1", "Channel 2", "Channel 3"],
    list(box.choices),
)
for index in range(3):
    box.value = "Channel %d" % (index + 1)
    run()
    expect(
        "file_channel_%d_is_what_the_tool_receives" % (index + 1),
        "mean=%d" % (10 * (index + 1)) in w.status.text() and "32 x 32" in w.status.text(),
        w.status.text(),
    )
w.file_sources["image"].value = work / "flat.tif"
pump(0.6)
expect("single_channel_file_has_no_chooser", not shown(box))
run()
expect("single_channel_file_is_sent_as_it_is", "mean=7" in w.status.text(), w.status.text())
w.file_sources["image"].value = work / "three.tif"
pump(0.6)
box.value = "Channel 2"
v.screenshot(
    str(Path(__file__).resolve().parent.parent / "evidence" / "widget_channels.png"), canvas_only=False
)
print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
